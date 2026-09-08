"""Exercise the stub's wire formats over loopback HTTP without backend imports."""

import base64
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
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


@pytest.mark.parametrize('path', ['/webhook', '/jobs', '/sync/cancel'])
def test_unmodelled_upstreams_have_the_planned_catch_all(stub, path):
    assert get_json(stub, path, payload={'action': 'CI probe'}) == {'status': 'ok'}


@pytest.mark.parametrize('raw', [b'[]', b'not-json'])
def test_malformed_model_body_is_an_explicit_refusal(stub, raw):
    with pytest.raises(HTTPError) as error:
        request(stub, '/v1/chat/completions', raw=raw)
    assert error.value.code == 400
    assert json.loads(error.value.read())['error'] == 'expected a JSON object'
