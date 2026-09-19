"""Exercise thread transport with fresh admission on every stream connection."""

import json
from contextlib import aclosing

import httpx
import pytest
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError
from open_webui.test.soev.fake_api import FakeSoevApi


@pytest.mark.asyncio
async def test_stream_reconnects_without_reposting_input(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    await identity.ensure_link('owui:user:alice', client)
    chat_http.chat.close_after = 2
    stream = client.chat_stream(
        '/v1/chat/threads', {'agent': 'test', 'input': 'hello', 'collections': []}, as_user='owui:user:alice'
    )
    async with aclosing(stream):
        frames = [frame async for frame in stream]
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
    assert len({r.headers['X-Soev-Subject'] for r in requests}) == 3
    assert all(r.headers['Authorization'] == 'Bearer test-runtime-key' for r in requests)
    assert all('Idempotency-Key' not in r.headers for r in requests)
    assert all(tail.closed for tail in chat_http.chat.tails)


@pytest.mark.asyncio
async def test_fork_delivers_thread_id_and_preserves_owner(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    await identity.ensure_link('owui:user:alice', client)
    async for _ in client.chat_stream(
        '/v1/chat/threads', {'agent': 'test', 'input': 'hello', 'collections': []}, as_user='owui:user:alice'
    ):
        pass
    branch = await client.chat_post('/v1/chat/threads/thr-1/fork', {'at': 1}, as_user='owui:user:alice')
    assert branch['thread_id'] == 'thr-2'
    assert branch['status']['position'] == 1
    await identity.ensure_link('owui:user:bob', client)
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
            status, json={'code': code, 'detail': 'orphaned'}, headers={'Content-Type': 'application/problem+json'}
        )
    )
    with pytest.raises(SoevApiError) as caught:
        async for _ in identity.build_client().chat_stream('/v1/chat/threads', {}, as_user='owui:user:alice'):
            pass
    assert (caught.value.status, caught.value.code) == (status, code)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_terminal_error_is_not_retried(chat_http: FakeSoevApi) -> None:
    client = identity.build_client()
    await identity.ensure_link('owui:user:alice', client)
    chat_http.chat.turns = [[('error', {'code': 'service_unavailable', 'detail': 'failed'})]]
    frames = [
        event
        async for event in client.chat_stream(
            '/v1/chat/threads', {'agent': 'test', 'input': 'hello', 'collections': []}, as_user='owui:user:alice'
        )
    ]
    assert frames[-1].event == 'error'
    assert len(chat_http.chat.requests) == 1
    assert json.loads(chat_http.chat.requests[0].content)['input'] == 'hello'
