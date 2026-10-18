"""Cover the DELETE methods forwarded by the terminal and Ollama proxies."""

import pytest

from . import test_stub_upstream as upstream

stub = upstream.stub


@pytest.mark.parametrize(
    ('path', 'raw'),
    [('/openapi.json', b''), ('/api/delete', b'{ "model": "stub-model" }')],
)
def test_forwarded_delete_returns_json_and_retains_request_evidence(stub, path, raw):
    upstream.get_json(stub, '/_recorded/reset')
    assert upstream.get_json(stub, path, raw=raw, method='DELETE') == {'status': 'ok'}
    recorded = upstream.get_json(stub, '/_recorded')['requests']
    assert len(recorded) == 1
    assert recorded[0]['path'] == path
    assert recorded[0]['raw_body'].encode() == raw
    # Deletion is an acknowledgement in this disposable, stateless stub.
    assert upstream.get_json(stub, '/v1/models')['data'][0]['id'] == 'stub-model'
