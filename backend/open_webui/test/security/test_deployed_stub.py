"""Wire contracts for the production-enabled upstreams, without app imports."""

import base64
import json
import struct
import zlib

import pytest

from .test_stub_upstream import get_json, request, stub


def test_agent_discovery_metadata_and_spec(stub):
    models = get_json(stub, '/v1/models')
    assert models['object'] == 'list'
    assert models['data'][0]['id']
    meta = get_json(stub, '/v1/gradient_agent_meta')
    assert meta['id'] == 'soev_chat_manual'
    assert isinstance(meta['config']['welcome_message'], str)
    assert meta['config']['welcome_message'].strip()
    spec = get_json(stub, '/openapi.json')
    assert spec['openapi'] == '3.1.0'
    assert spec['info']['title'] and spec['info']['version']
    for path, method in (
        ('/v1/models', 'get'),
        ('/v1/chat/completions', 'post'),
        ('/v1/gradient_agent_meta', 'get'),
        ('/openapi.json', 'get'),
    ):
        assert '200' in spec['paths'][path][method]['responses']


@pytest.mark.parametrize('agent', ['soev_chat_manual', 'assistant_onboarding'])
@pytest.mark.parametrize('stream', [False, True, 'yes'])
def test_deployed_agents_return_readable_completions(stub, agent, stream):
    model = get_json(stub, '/v1/models')['data'][0]['id']
    content_type, body = request(
        stub,
        '/v1/chat/completions',
        {
            'model': model,
            'agent': agent,
            'stream': stream,
            'messages': [{'role': 'user', 'content': 'CI agent probe'}],
            'user_id': 'ci-user',
        },
    )
    if stream:
        assert content_type == 'text/event-stream'
        frames = [frame.removeprefix(b'data: ') for frame in body.split(b'\n\n') if frame]
        assert frames[-1] == b'[DONE]'
        chunks = [json.loads(frame) for frame in frames[:-1]]
        assert all(chunk['model'] == model for chunk in chunks)
        assert ''.join(chunk['choices'][0]['delta'].get('content', '') for chunk in chunks)
        assert chunks[-1]['choices'][0]['finish_reason'] == 'stop'
    else:
        assert content_type == 'application/json'
        assert json.loads(body)['choices'][0]['message']['content']


def test_searxng_envelope_can_feed_external_web_loader(stub):
    payload = get_json(stub, '/search?q=CI&format=json&pageno=1')
    results = sorted(payload['results'], key=lambda row: row.get('score', 0), reverse=True)
    assert results
    assert '</source>' in results[0]['title']
    assert '<source ' in results[0]['content'] and '</source>' not in results[0]['content']
    urls = [row['url'] for row in results]
    documents = get_json(stub, '/extract', payload={'urls': urls})
    assert len(documents) == len(urls)
    for document, url in zip(documents, urls):
        assert document['metadata']['source'] == url
        assert stub[0].INJECTION_PROBE in document['page_content']


def test_external_web_loader_preserves_batch_cardinality(stub):
    urls = ['http://stub:8000/document', 'http://stub:8000/document?second']
    documents = get_json(stub, '/extract', payload={'urls': urls})
    assert [document['metadata']['source'] for document in documents] == urls
    assert all(isinstance(document['page_content'], str) and document['page_content'] for document in documents)


def test_document_processor_accepts_binary_put_and_returns_documents(stub):
    raw = b'%PDF-1.4\n\xff\x00binary CI document'
    get_json(stub, '/_recorded/reset')
    documents = get_json(stub, '/process', method='PUT', raw=raw)
    assert isinstance(documents, list) and documents
    for document in documents:
        assert isinstance(document['page_content'], str) and document['page_content']
        assert isinstance(document['metadata'], dict)
    record = get_json(stub, '/_recorded')['requests'][-1]
    assert record['path'] == '/process'
    assert base64.b64decode(record['raw_body_base64']) == raw


def test_reranker_returns_one_indexed_score_per_document(stub):
    documents = ['first document', 'second document', 'third document']
    response = get_json(
        stub,
        '/v1/rerank',
        payload={
            'model': 'ci-reranker',
            'query': 'CI',
            'documents': documents,
            'top_n': len(documents),
        },
    )
    results = sorted(response['results'], key=lambda row: row['index'])
    assert [row['index'] for row in results] == list(range(len(documents)))
    assert all(isinstance(row['relevance_score'], (float, int)) for row in results)
    assert results[0]['relevance_score'] > results[-1]['relevance_score'] > 0


def test_document_pipeline_submission_has_pollable_terminal_status(stub):
    job = get_json(stub, '/jobs', payload={'metadata': {'items': []}, 'parameters': {}})
    assert isinstance(job['job_id'], str) and job['job_id']
    assert get_json(stub, '/jobs/' + job['job_id'])['status'] == 'completed'


def test_generated_image_is_inline_and_decodable(stub):
    result = get_json(stub, '/v1/images/generations', payload={'prompt': 'CI', 'n': 2})
    assert len(result['data']) == 2
    for item in result['data']:
        png = base64.b64decode(item['b64_json'], validate=True)
        assert png[:8] == b'\x89PNG\r\n\x1a\n'
        offset = 8
        pixels = b''
        while offset < len(png):
            size = struct.unpack('!I', png[offset : offset + 4])[0]
            chunk = png[offset + 4 : offset + 8 + size]
            crc = struct.unpack('!I', png[offset + 8 + size : offset + 12 + size])[0]
            assert zlib.crc32(chunk) == crc
            if chunk[:4] == b'IDAT':
                pixels += chunk[4:]
            offset += size + 12
        assert zlib.decompress(pixels)
