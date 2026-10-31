"""Exercise thread transport with fresh admission on every stream connection."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing

import httpx
import pytest
from open_webui.soev import identity
from open_webui.soev.client import ChatEvent, SoevApiError
from open_webui.test.soev.fake_api import FakeSoevApi


async def _collect(stream: AsyncIterator[ChatEvent], *, timeout: float = 8) -> list[ChatEvent]:
    async with asyncio.timeout(timeout), aclosing(stream):
        return [event async for event in stream]


@pytest.mark.asyncio
@pytest.mark.parametrize('close_after', [2, 3])
async def test_reconnected_tail_close_reads_terminality_without_a_status_frame(
    chat_http: FakeSoevApi, close_after: int
) -> None:
    client = identity.build_client()
    chat_http.chat.close_after = close_after
    chat_http.chat.tail_closes = True

    frames = await _collect(
        client.chat_stream(
            '/v1/chat/threads', {'agent': 'test', 'input': {'text': 'hello'}}, as_user='owui:user:alice'
        ),
        timeout=2,
    )
    assert frames[-1].event == 'status'
    assert frames[-1].data['state'] == 'finished'
    assert all(b'event: status' not in tail.content for tail in chat_http.chat.tails)
    assert sum(request.url.path == '/v1/chat/threads/thr-1' for request in chat_http.chat.requests) == 1


@pytest.mark.asyncio
async def test_reconnected_tail_checks_root_outputs_without_reading_every_frame(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    chat_http.chat.close_after = 2
    chat_http.chat.turns = [
        [
            ('model_output', {'content': '', 'tool_calls': [{'id': 'c1', 'name': 'search', 'arguments': {}}]}),
            ('tool_output', {'call_id': 'c1', 'data': 'found'}),
            ('source', {'id': 's1', 'ref': 'doc', 'text': 'passage'}),
            ('model_output', {'content': 'Answer'}),
        ]
    ]
    frames = await _collect(
        client.chat_stream('/v1/chat/threads', {'agent': 'test', 'input': {'text': 'hello'}}, as_user='owui:user:alice')
    )
    assert [event.event for event in frames][-5:] == ['model_output', 'tool_output', 'source', 'model_output', 'status']
    assert sum(request.url.path == '/v1/chat/threads/thr-1' for request in chat_http.chat.requests) == 2


@pytest.mark.asyncio
async def test_reconnected_tail_waits_for_lease_release_after_last_output(
    chat_http: FakeSoevApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = identity.build_client()
    chat_http.chat.close_after = 2
    original = chat_http.chat._view
    reads = []

    def view(thread: dict) -> dict:
        result = original(thread)
        reads.append(thread['thread_id'])
        if len(reads) == 1:
            result['status']['state'] = 'running'
        return result

    monkeypatch.setattr(chat_http.chat, '_view', view)

    frames = await _collect(
        client.chat_stream(
            '/v1/chat/threads', {'agent': 'test', 'input': {'text': 'hello'}}, as_user='owui:user:alice'
        ),
        timeout=7,
    )
    assert frames[-1].event == 'status'
    assert frames[-1].data['state'] == 'finished'
    assert len(reads) == 2


@pytest.mark.asyncio
async def test_stream_reconnects_without_reposting_input(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    chat_http.chat.close_after = 2
    stream = client.chat_stream(
        '/v1/chat/threads', {'agent': 'test', 'input': {'text': 'hello'}}, as_user='owui:user:alice'
    )
    frames = await _collect(stream)
    assert [frame.event for frame in frames] == [
        'connection',
        'opened',
        'input',
        'connection',
        'model_output',
        'status',
    ]
    requests = chat_http.chat.requests
    assert [(r.method, r.url.path) for r in requests] == [
        ('POST', '/v1/chat/threads'),
        ('GET', '/v1/chat/threads/thr-1/events'),
        ('GET', '/v1/chat/threads/thr-1'),
    ]
    assert requests[1].headers['Last-Event-ID'] == '2'
    assert {r.headers['X-Soev-User-Token'] for r in requests} == {'fictional-login:owui:user:alice'}
    assert all(r.headers['Authorization'] == 'Bearer soev_test_cred-runtime_test-secret' for r in requests)
    assert all('Idempotency-Key' not in r.headers for r in requests)
    assert all(tail.closed for tail in chat_http.chat.tails)


@pytest.mark.asyncio
async def test_open_returns_sse_with_thread_header_even_when_json_requested(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    response = await client._request(
        'POST',
        '/v1/chat/threads',
        body={'agent': 'test', 'input': {'text': 'hello'}},
        as_user='owui:user:alice',
        admission_headers={**await client._headers('owui:user:alice'), 'Accept': 'application/json'},
    )
    assert chat_http.chat.requests[-1].headers['Accept'] == 'application/json'
    assert response.status_code == 201
    assert response.headers['Content-Type'] == 'text/event-stream'
    assert response.headers['X-Soev-Thread-Id'] == 'thr-1'
    assert b'event: input\n' in response.content and b'event: model_output\n' in response.content


@pytest.mark.asyncio
async def test_reserved_open_is_idle_until_streamed_and_preserves_owner(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    reserved = await client.chat_post('/v1/chat/threads:reserve', {'agent': 'test'}, as_user='owui:user:alice')
    thread_id = reserved['thread_id']
    thread = chat_http.chat.threads[thread_id]
    assert thread['state'] == 'reserved' and thread['events'] == []
    body = {'agent': 'test', 'thread_id': thread_id, 'input': {'text': 'hello'}, 'model': 'fictional-model'}
    with pytest.raises(SoevApiError) as caught:
        await _collect(client.chat_stream('/v1/chat/threads', body, as_user='owui:user:bob', thread_id=thread_id))
    assert (caught.value.status, caught.value.code) == (404, 'not_found')
    assert thread['state'] == 'reserved' and thread['events'] == []
    frames = await _collect(
        client.chat_stream('/v1/chat/threads', body, as_user='owui:user:alice', thread_id=thread_id)
    )
    assert [event.event for event in frames] == ['connection', 'opened', 'input', 'model_output', 'status']
    assert frames[0].data['thread_id'] == thread_id
    assert frames[-1].data['state'] == 'finished'
    assert len(chat_http.chat.threads) == 1
    with pytest.raises(SoevApiError) as caught:
        await _collect(client.chat_stream('/v1/chat/threads', body, as_user='owui:user:alice', thread_id=thread_id))
    assert (caught.value.status, caught.value.code) == (409, 'thread_active')
    assert sum(event['type'] == 'input' for event in thread['events']) == 1
    requests = chat_http.chat.requests
    assert [request.url.path for request in requests] == ['/v1/chat/threads:reserve'] + ['/v1/chat/threads'] * 3
    assert [request.headers['X-Soev-User-Token'] for request in requests] == [
        'fictional-login:owui:user:alice',
        'fictional-login:owui:user:bob',
        'fictional-login:owui:user:alice',
        'fictional-login:owui:user:alice',
    ]
    assert all(request.headers['Authorization'] == 'Bearer soev_test_cred-runtime_test-secret' for request in requests)


@pytest.mark.asyncio
async def test_fork_delivers_thread_id_and_preserves_owner(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    await _collect(
        client.chat_stream('/v1/chat/threads', {'agent': 'test', 'input': {'text': 'hello'}}, as_user='owui:user:alice')
    )
    branch = await client.chat_post('/v1/chat/threads/thr-1/fork', {'at': 1}, as_user='owui:user:alice')
    assert branch['thread_id'] == 'thr-2'
    assert branch['status']['position'] == 1
    with pytest.raises(SoevApiError) as caught:
        await client.get('/v1/chat/threads/thr-2', as_user='owui:user:bob')
    assert caught.value.code == 'not_found'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,code', [(404, 'not_found'), (409, 'thread_active'), (422, 'invalid_field'), (503, 'service_unavailable')]
)
async def test_stream_keeps_relay_error_codes(
    identity_config: tuple, identity_http: tuple, status: int, code: str
) -> None:
    requests, responses = identity_http
    responses.append(
        httpx.Response(
            status, json={'code': code, 'detail': 'interrupted'}, headers={'Content-Type': 'application/problem+json'}
        )
    )
    with pytest.raises(SoevApiError) as caught:
        await _collect(identity.build_client().chat_stream('/v1/chat/threads', {}, as_user='owui:user:alice'))
    assert (caught.value.status, caught.value.code) == (status, code)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_terminal_error_is_not_retried(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    chat_http.chat.turns = [[('error', {'code': 'service_unavailable', 'detail': 'failed'})]]
    frames = await _collect(
        client.chat_stream('/v1/chat/threads', {'agent': 'test', 'input': {'text': 'hello'}}, as_user='owui:user:alice')
    )
    assert frames[-1].event == 'error'
    assert len(chat_http.chat.requests) == 1
    assert json.loads(chat_http.chat.requests[0].content)['input'] == {'text': 'hello'}
