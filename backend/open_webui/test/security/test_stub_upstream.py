"""Exercise the stub's wire formats over loopback HTTP without backend imports."""

import base64
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import pytest


@pytest.fixture(scope='module')
def stub():
    path = Path(__file__).resolve().parents[4] / 'cicd/stub_upstream.py'
    spec = importlib.util.spec_from_file_location('ci_stub_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield module, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def request(stub, path, payload=None, raw=None, method=None):
    body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = Request(stub[1] + path, data=body, method=method, headers={'Content-Type': 'application/json'})
    # Ignore developer proxy variables; these checks only drive loopback.
    with build_opener(ProxyHandler({})).open(req, timeout=5) as response:
        return response.headers.get_content_type(), response.read()


def get_json(stub, path, **kwargs):
    content_type, body = request(stub, path, **kwargs)
    assert content_type == 'application/json'
    return json.loads(body)


@pytest.mark.parametrize('stream', [True, 1, 'yes'])
def test_openai_stream_has_nonempty_deltas_finish_and_done(stub, stream):
    content_type, body = request(
        stub, '/v1/chat/completions', {'model': 'stub-model', 'stream': stream, 'messages': []}
    )
    assert content_type == 'text/event-stream'
    frames = [frame.removeprefix(b'data: ') for frame in body.split(b'\n\n') if frame]
    assert frames[-1] == b'[DONE]'
    chunks = [json.loads(frame) for frame in frames[:-1]]
    assert all(chunk['object'] == 'chat.completion.chunk' for chunk in chunks)
    choices = [chunk['choices'][0] for chunk in chunks]
    deltas = [choice['delta'].get('content', '') for choice in choices]
    assert len([text for text in deltas if text]) > 1
    assert ''.join(deltas) == 'stub streamed response'
    assert choices[0]['delta']['role'] == 'assistant'
    assert choices[-1]['finish_reason'] == 'stop'


@pytest.mark.parametrize('payload', [{'stream': False}, {}])
def test_openai_nonstream_returns_an_answer(stub, payload):
    result = get_json(stub, '/v1/chat/completions', payload=payload)
    assert result['object'] == 'chat.completion'
    assert result['choices'][0]['message']['content'] == 'stub response'


@pytest.mark.parametrize('inputs,count', [('text', 1), (['one', 'two'], 2), ([12, 34], 1), ([[12], [34]], 2)])
def test_embeddings_keep_batch_cardinality_and_model_dimension(stub, inputs, count):
    result = get_json(stub, '/v1/embeddings', payload={'model': 'text-embedding-3-small', 'input': inputs})
    assert len(result['data']) == count
    for index, item in enumerate(result['data']):
        assert item['index'] == index
        assert len(item['embedding']) == 1536
        assert any(item['embedding'])
    assert result['data'][0]['embedding'] == result['data'][-1]['embedding']


def test_model_discovery_exposes_a_usable_model(stub):
    models = get_json(stub, '/v1/models')['data']
    tags = get_json(stub, '/api/tags')['models']
    assert models[0]['id'] == tags[0]['model'] == 'stub-model'


def test_speech_returns_audio_and_captures_the_openai_body(stub):
    payload = {
        'model': 'stub-model',
        'input': 'Fictional speech.',
        'voice': 'fictional-voice',
        'response_format': 'mp3',
    }
    get_json(stub, '/_recorded/reset')
    content_type, body = request(stub, '/v1/audio/speech', payload)
    assert content_type == 'audio/mpeg'
    assert body.startswith(b'ID3') and len(body) > 10
    record = get_json(stub, '/_recorded')['requests'][-1]
    assert record['path'] == '/v1/audio/speech'
    assert record['body'] == payload


@pytest.mark.parametrize('path,field', [('/api/chat', 'message'), ('/api/generate', 'response')])
@pytest.mark.parametrize('stream', [True, False])
def test_ollama_uses_its_own_protocol(stub, path, field, stream):
    content_type, body = request(stub, path, {'model': 'stub-model', 'stream': stream})
    if stream:
        assert content_type == 'application/x-ndjson'
        chunks = [json.loads(line) for line in body.splitlines()]
        assert chunks[-1]['done'] is True
        assert all(chunk['done'] is False for chunk in chunks[:-1])
        text = ''.join(chunk[field]['content'] if field == 'message' else chunk[field] for chunk in chunks)
        assert text == 'stub streamed response'
    else:
        result = json.loads(body)
        assert content_type == 'application/json' and result['done'] is True
        assert (result[field]['content'] if field == 'message' else result[field]) == 'stub response'


def test_search_is_nonempty_parser_compatible_and_asymmetrically_hostile(stub):
    results = get_json(stub, '/search', payload={'query': 'CI probe', 'count': 1})
    assert isinstance(results, list) and len(results) == 1
    result = results[0]
    assert result == get_json(stub, '/search?query=CI')[0]
    assert result['link'] == 'http://stub:8000/document'
    assert '</source>' in result['title'] and '<source ' not in result['title']
    for field in ('snippet', 'content'):
        assert '<source id="forged-ci-source">' in result[field]
        assert '</source>' not in result[field]
    for field in ('title', 'snippet', 'content'):
        assert stub[0].INJECTION_PROBE in result[field]
    assert request(stub, '/document')[0] == 'text/html'


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_capture_preserves_literal_full_bodies_and_reset_removes_stale_prompts(stub, method):
    get_json(stub, '/_recorded/reset', method=method)
    raw = (
        b'{ "model": "stub-model", "messages": [{"role":"user", "content":"literal \\u003csource>"}], "stream": false }'
    )
    request(stub, '/v1/chat/completions?run=current', raw=raw)
    records = get_json(stub, '/_recorded')['requests']
    assert len(records) == 1
    assert records[0]['path'] == '/v1/chat/completions?run=current'
    assert records[0]['body'] == json.loads(raw)
    assert records[0]['raw_body'].encode() == raw
    assert base64.b64decode(records[0]['raw_body_base64']) == raw
    assert get_json(stub, '/_recorded/reset', method=method) == {'requests': [], 'cleared': True}
    assert get_json(stub, '/_recorded')['requests'] == []


def test_capture_is_thread_safe_and_keeps_recent_requests(stub):
    get_json(stub, '/_recorded/reset')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: get_json(stub, '/webhook', payload={'index': i}), range(205)))
    records = get_json(stub, '/_recorded')['requests']
    assert len(records) == stub[0]._RECORD_LIMIT == 200
    assert len({record['body']['index'] for record in records}) == 200
    get_json(stub, '/webhook', payload={'index': 'last'})
    assert get_json(stub, '/_recorded')['requests'][-1]['body'] == {'index': 'last'}


@pytest.mark.parametrize('method', ['POST', 'PUT'])
def test_a_chunked_body_is_read_to_its_end_before_the_answer(stub, method):
    # pipelines.py:upload_pipeline streams its multipart form from a generator, so
    # aiohttp sends it chunked with no Content-Length. Answering after reading zero
    # bytes and closing raced the upload into EPIPE, the handler's 404, and an
    # unentered route on the coverage gate.
    path = '/pipelines/upload' if method == 'POST' else '/process'
    parts = [b'--boundary\r\n', b'Content-Disposition: form-data; name="file"\r\n\r\n', b'# pipeline\r\n']
    get_json(stub, '/_recorded/reset')
    connection = HTTPConnection(urlsplit(stub[1]).hostname, urlsplit(stub[1]).port, timeout=5)
    try:
        connection.request(method, path, body=iter(parts), encode_chunked=True)
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()
    records = get_json(stub, '/_recorded')['requests']
    assert base64.b64decode(records[-1]['raw_body_base64']) == b''.join(parts)


@pytest.mark.parametrize('path', ['/webhook', '/unmodelled'])
def test_unmodelled_upstreams_have_the_planned_catch_all(stub, path):
    assert get_json(stub, path, payload={'action': 'CI probe'}) == {'status': 'ok'}


@pytest.mark.parametrize('raw', [b'[]', b'not-json'])
@pytest.mark.parametrize('path', ['/v1/chat/completions', '/v1/audio/speech'])
def test_malformed_model_body_is_an_explicit_refusal(stub, raw, path):
    with pytest.raises(HTTPError) as error:
        request(stub, path, raw=raw)
    assert error.value.code == 400
    assert json.loads(error.value.read())['error'] == 'expected a JSON object'


def test_head_answers_headers_without_a_body(stub):
    # The terminal proxy forwards HEAD. A body after a HEAD response is a protocol
    # error to aiohttp (BadHttpMessage), which the proxy answered as a 502.
    import socket

    host, port = stub[1].removeprefix('http://').split(':')
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(b'HEAD /v1/models HTTP/1.0\r\nHost: stub\r\n\r\n')
        data = b''
        while chunk := sock.recv(65536):
            data += chunk
    head, _, body = data.partition(b'\r\n\r\n')
    assert b' 200 ' in head.split(b'\r\n')[0]
    assert body == b''


@pytest.mark.parametrize('path,ollama', [('/v1/chat/completions', False), ('/api/chat', True)])
def test_task_fixture_stream_is_held_but_other_completions_still_answer(stub, path, ollama):
    connection = HTTPConnection('127.0.0.1', urlsplit(stub[1]).port, timeout=5)
    body = {'stream': True, 'messages': [{'role': 'user', 'content': 'owui_attack_task_hold:' + 'a' * 24}]}
    connection.request('POST', path, json.dumps(body), {'Content-Type': 'application/json'})
    response = connection.getresponse()
    try:
        assert response.status == 200
        for _ in range(2):
            frame = response.readline()
            if not ollama:
                assert frame.startswith(b'data: ')
                frame = frame.removeprefix(b'data: ')
                assert response.readline() == b'\n'
            payload = json.loads(frame)
            if ollama:
                assert payload['done'] is False
                assert payload['message']['content'] == ''
            else:
                assert payload['choices'][0]['delta']['content'] == ''
                assert payload['choices'][0]['finish_reason'] is None
        assert get_json(stub, '/v1/chat/completions', payload={'stream': False})['choices'][0]['message']['content']
    finally:
        response.close()
        connection.close()


@pytest.mark.parametrize('path', ['/v1/chat/completions', '/api/chat'])
def test_task_hold_expires_and_then_completes_normally(stub, monkeypatch, path):
    assert stub[0].TASK_HOLD_SECONDS == 3600
    monkeypatch.setattr(stub[0], 'TASK_HOLD_SECONDS', 0.05)
    monkeypatch.setattr(stub[0], 'TASK_HEARTBEAT_SECONDS', 0.01)
    content_type, raw = request(
        stub,
        path,
        {'stream': True, 'messages': [{'role': 'user', 'content': 'owui_attack_task_hold:' + 'b' * 24}]},
    )
    if content_type == 'text/event-stream':
        frames = [frame.removeprefix(b'data: ') for frame in raw.split(b'\n\n') if frame]
        assert frames[-1] == b'[DONE]'
        chunks = [json.loads(frame)['choices'][0] for frame in frames[:-1]]
        assert any(chunk['delta'].get('content') == '' and chunk['finish_reason'] is None for chunk in chunks)
        assert chunks[-1]['finish_reason'] == 'stop'
    else:
        chunks = [json.loads(line) for line in raw.splitlines()]
        assert any(chunk['message']['content'] == '' and chunk['done'] is False for chunk in chunks)
        assert chunks[-1]['done'] is True


def test_task_hold_stops_writing_when_cancellation_closes_the_connection(stub, monkeypatch):
    from unittest.mock import Mock

    handler = object.__new__(stub[0].Handler)
    handler.wfile = Mock()
    handler.wfile.write.side_effect = BrokenPipeError('fixture disconnected')
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    pause = Mock(side_effect=AssertionError('Disconnected streams must not wait'))
    monkeypatch.setattr(stub[0].time, 'sleep', pause)
    handler._send_stream({'messages': [{'content': 'owui_attack_task_hold:' + 'c' * 24}]})
    handler.wfile.write.assert_called_once()
    pause.assert_not_called()


@pytest.mark.asyncio
async def test_held_inference_task_is_visible_only_to_its_owner_and_cancellable(stub, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI
    from open_webui import tasks
    from open_webui.main import Chats, list_tasks_by_chat_id_endpoint, stop_task_endpoint
    from open_webui.utils.auth import get_admin_user, get_verified_user

    monkeypatch.setattr(tasks, 'tasks', {})
    monkeypatch.setattr(tasks, 'item_tasks', {})
    monkeypatch.setattr(tasks, 'response_streams', {})
    monkeypatch.setattr(Chats, 'get_chat_by_id', AsyncMock(return_value=SimpleNamespace(user_id='fictional-owner')))
    app = FastAPI()
    app.state.redis = None
    app.add_api_route('/api/tasks/chat/{chat_id}', list_tasks_by_chat_id_endpoint, methods=['GET'])
    app.add_api_route('/api/tasks/stop/{task_id}', stop_task_endpoint, methods=['POST'])
    actor = SimpleNamespace(id='fictional-owner', role='admin')
    app.dependency_overrides[get_verified_user] = lambda: actor
    app.dependency_overrides[get_admin_user] = lambda: actor
    entered = asyncio.Event()

    async def inference():
        async with httpx.AsyncClient(trust_env=False) as upstream:
            async with upstream.stream(
                'POST',
                stub[1] + '/v1/chat/completions',
                json={'stream': True, 'messages': [{'role': 'user', 'content': 'owui_attack_task_hold:' + 'd' * 24}]},
                timeout=5,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        entered.set()

    task_id, task = await tasks.create_task(None, inference(), id='fictional-chat')
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not task.done()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owui.invalid') as client:
            owner = await client.get('/api/tasks/chat/fictional-chat')
            assert owner.json() == {'task_ids': [task_id]}
            actor = SimpleNamespace(id='fictional-second-account', role='user')
            stranger = await client.get('/api/tasks/chat/fictional-chat')
            assert stranger.json() == {'task_ids': []}
            actor = SimpleNamespace(id='fictional-owner', role='admin')
            stopped = await client.post(f'/api/tasks/stop/{task_id}')
            assert stopped.status_code == 200 and stopped.json()['status'] is True
            assert task.cancelled()
            assert (await client.get('/api/tasks/chat/fictional-chat')).json() == {'task_ids': []}
    finally:
        await tasks.stop_task(None, task_id)
