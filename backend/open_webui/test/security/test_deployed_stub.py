"""Wire contracts for the production-enabled upstreams, without app imports."""

import base64
import struct
import zlib

from .test_stub_upstream import get_json
from .test_stub_upstream import stub as stub


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
