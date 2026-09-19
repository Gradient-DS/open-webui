"""Recorded HTTP contracts for the soev-api client and its deployment settings."""

import json
import logging
import os
import subprocess
import sys
from unittest.mock import Mock

import httpx
import pytest
from open_webui.soev.client import SoevApiError, SoevClient


@pytest.fixture
def recorded_http(monkeypatch):
    """Record requests without opening a network connection."""
    requests = []
    responses = []
    clients = []
    async_client = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def make_client(**kwargs):
        client = async_client(transport=httpx.MockTransport(handle), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr('open_webui.soev.client.httpx.AsyncClient', make_client)
    yield requests, responses
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_the_bearer_key_and_subject_assertion_are_both_sent(recorded_http):
    """The client mints an assertion for the external ref only when acting as a user."""
    requests, responses = recorded_http
    responses.extend([httpx.Response(200, json={'key': 'kb'}) for _ in range(2)])
    minter = Mock(return_value='test-assertion')
    client = SoevClient('https://soev.invalid/', 'test-api-key', subject_minter=minter, timeout=7.0)

    assert await client.get('/v1/collections/kb', as_user='owui:user:alice', params={'limit': 2}) == {'key': 'kb'}
    await client.get('/v1/collections/kb')

    minter.assert_called_once_with('owui:user:alice')
    assert str(requests[0].url) == 'https://soev.invalid/v1/collections/kb?limit=2'
    assert requests[0].headers['Authorization'] == 'Bearer test-api-key'
    assert requests[0].headers['X-Soev-Subject'] == 'test-assertion'
    assert 'X-Soev-Subject' not in requests[1].headers
    assert 'Idempotency-Key' not in requests[0].headers
    assert requests[0].extensions['timeout']['read'] == 7.0


@pytest.mark.asyncio
async def test_put_bytes_sends_the_presigned_headers_and_no_bearer(recorded_http):
    """Presigned uploads send raw bytes and supplied headers without minting an assertion."""
    requests, responses = recorded_http
    responses.append(httpx.Response(200))
    minter = Mock(return_value='test-assertion')
    client = SoevClient('https://soev.invalid', 'test-api-key', subject_minter=minter, timeout=7.0)
    url = 'https://s3.invalid/bucket/file?X-Amz-Signature=private-signature'
    body = b'\x00uploaded bytes\xff'
    headers = {'x-amz-checksum-sha256': 'test-checksum', 'Content-Length': str(len(body))}

    assert await client.put_bytes(url, headers=headers, body=body) is None

    assert len(requests) == 1
    request = requests[0]
    assert request.method == 'PUT'
    assert str(request.url) == url
    assert request.content == body
    for name, value in headers.items():
        assert request.headers[name] == value
    for name in ('Authorization', 'X-Soev-Subject', 'Idempotency-Key'):
        assert name not in request.headers
    assert request.extensions['timeout']['read'] == 7.0
    minter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [403, 302])
async def test_put_bytes_maps_a_rejected_upload_to_a_soev_api_error(recorded_http, status):
    """Rejected uploads expose only their status and never follow redirects."""
    requests, responses = recorded_http
    url = 'https://s3.invalid/bucket/file?X-Amz-Signature=private-signature'
    responses.append(
        httpx.Response(
            status,
            text=f'<Error><Code>AccessDenied</Code><Resource>{url}</Resource></Error>',
            headers={'Content-Type': 'application/xml', 'Location': 'https://other.invalid/'},
        )
    )

    with pytest.raises(SoevApiError) as caught:
        await SoevClient('https://soev.invalid', 'test-api-key').put_bytes(url, headers={}, body=b'upload')

    assert caught.value.status == 502
    assert caught.value.code == 'upload_failed'
    assert str(status) in caught.value.detail
    assert url not in str(caught.value)
    assert 'private-signature' not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_get_text_returns_markdown_without_json_parsing(recorded_http):
    """Markdown responses retain their exact text without JSON decoding."""
    requests, responses = recorded_http
    markdown = '# Title\n\nSome **markdown**, café.\n'
    responses.append(httpx.Response(200, text=markdown, headers={'Content-Type': 'text/markdown'}))

    assert await SoevClient('https://soev.invalid', 'test-api-key').get_text('/v1/files/file/text') == markdown

    assert requests[0].method == 'GET'
    assert str(requests[0].url) == 'https://soev.invalid/v1/files/file/text'
    assert requests[0].headers['Authorization'] == 'Bearer test-api-key'
    assert 'X-Soev-Subject' not in requests[0].headers


@pytest.mark.asyncio
async def test_get_text_carries_the_subject_assertion(recorded_http):
    """Text reads carry user authority and preserve API problem errors."""
    requests, responses = recorded_http
    problem = {'code': 'file_not_found', 'detail': 'The file does not exist.'}
    responses.extend(
        [
            httpx.Response(200, text='# Title\n', headers={'Content-Type': 'text/markdown'}),
            httpx.Response(404, json=problem, headers={'Content-Type': 'application/problem+json'}),
        ]
    )
    minter = Mock(return_value='test-assertion')
    client = SoevClient('https://soev.invalid', 'test-api-key', subject_minter=minter)

    assert await client.get_text('/v1/files/file/text', as_user='owui:user:alice') == '# Title\n'
    with pytest.raises(SoevApiError) as caught:
        await client.get_text('/v1/files/missing/text', as_user='owui:user:alice')

    assert minter.call_count == 2
    minter.assert_called_with('owui:user:alice')
    assert all(request.headers['X-Soev-Subject'] == 'test-assertion' for request in requests)
    assert all(request.headers['Authorization'] == 'Bearer test-api-key' for request in requests)
    assert caught.value.status == 404
    assert caught.value.code == problem['code']
    assert caught.value.detail == problem['detail']


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['POST', 'PATCH', 'PUT', 'DELETE'])
async def test_a_mutation_requires_an_idempotency_key(recorded_http, method):
    """Every mutation requires a nonempty caller-owned key before any I/O."""
    requests, responses = recorded_http
    minter = Mock(return_value='test-assertion')
    client = SoevClient('https://soev.invalid', 'test-api-key', subject_minter=minter)
    with pytest.raises(TypeError):
        await client.send(method, '/v1/collections/kb', {})  # pylint: disable=missing-kwoa
    for invalid in ('', '   ', None):
        with pytest.raises(ValueError, match='idempotency'):
            await client.send(method, '/v1/collections/kb', {}, idempotency_key=invalid)
    assert requests == []
    minter.assert_not_called()

    responses.append(httpx.Response(202, json={'id': 'job-1'}))
    result = await client.send(
        method, '/v1/collections/kb', {'name': 'Renamed'}, as_user='owui:user:alice', idempotency_key='operation-1'
    )
    minter.assert_called_once_with('owui:user:alice')
    assert result == {'id': 'job-1'}
    assert requests[0].method == method
    assert requests[0].headers['Idempotency-Key'] == 'operation-1'
    assert requests[0].headers['Authorization'] == 'Bearer test-api-key'
    assert requests[0].headers['X-Soev-Subject'] == 'test-assertion'
    assert json.loads(requests[0].content) == {'name': 'Renamed'}


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint', ['collection:writers', None])
async def test_a_problem_document_becomes_a_soev_api_error_carrying_its_code(recorded_http, constraint):
    """Problem fields survive translation while the HTTP status remains authoritative."""
    _, responses = recorded_http
    problem = {
        'type': 'https://soev.invalid/problems/scope_insufficient',
        'title': 'Insufficient scope',
        'status': 400,
        'code': 'scope_insufficient',
        'detail': 'The subject is not a collection writer.',
    }
    if constraint is not None:
        problem['constraint'] = constraint
    responses.append(httpx.Response(403, json=problem, headers={'Content-Type': 'application/problem+json'}))

    with pytest.raises(SoevApiError) as caught:
        await SoevClient('https://soev.invalid', 'test-api-key').send(
            'PATCH', '/v1/collections/kb', {'name': 'Renamed'}, idempotency_key='operation-1'
        )

    assert caught.value.status == 403
    assert caught.value.code == 'scope_insufficient'
    assert caught.value.detail == problem['detail']
    assert caught.value.constraint == constraint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status, content_type',
    [(502, 'text/html'), (502, 'application/problem+json'), (503, 'application/problem+json; charset=utf-8')],
)
async def test_an_upstream_502_never_leaks_its_body(recorded_http, caplog, status, content_type):
    """Server errors retain problem codes and constraints while withholding detail and secrets."""
    _, responses = recorded_http
    responses.append(
        httpx.Response(
            status,
            json={
                'type': 'https://soev.invalid/problems/service_unavailable',
                'title': 'Service unavailable',
                'status': status,
                'code': 'service_unavailable',
                'detail': 'private-upstream-body',
                'constraint': 'upstream:catalog',
            },
            headers={'Content-Type': content_type, 'X-Request-ID': 'request-5xx'},
        )
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(SoevApiError) as caught:
        await SoevClient('https://soev.invalid', 'test-api-key', subject_minter=lambda _: 'test-assertion').get(
            '/v1/collections', as_user='owui:user:alice'
        )

    assert caught.value.status == status
    is_problem = content_type.startswith('application/problem+json')
    assert caught.value.code == ('service_unavailable' if is_problem else 'upstream_error')
    assert caught.value.constraint == ('upstream:catalog' if is_problem else None)
    assert caught.value.detail == f'soev-api returned HTTP {status}'
    exposed = f'{caught.value!s} {caught.value!r} {vars(caught.value)} {caplog.text}'
    for secret in ('private-upstream-body', 'test-api-key', 'test-assertion'):
        assert secret not in exposed


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 404, 502])
async def test_the_request_id_reaches_the_log_record(recorded_http, caplog, status):
    """Successful and failed responses carry the upstream request id in structured logs."""
    _, responses = recorded_http
    responses.append(httpx.Response(status, json={}, headers={'X-Request-ID': 'request-123'}))
    with caplog.at_level(logging.INFO, logger='open_webui.soev.client'):
        try:
            await SoevClient('https://soev.invalid', 'test-api-key').get('/v1/collections')
        except SoevApiError:
            assert status >= 400
    records = [record for record in caplog.records if record.name == 'open_webui.soev.client']
    assert len(records) == 1
    assert records[0].request_id == 'request-123'
    assert records[0].status == status


@pytest.mark.asyncio
async def test_pages_walks_every_cursor(recorded_http):
    """Pagination yields data items in order, preserves filters, and follows opaque cursors."""
    requests, responses = recorded_http
    pages = [
        {'data': [{'key': 'first'}, {'key': 'second'}], 'next_cursor': 'opaque/+= cursor'},
        {'data': [], 'next_cursor': 'last'},
        {'data': [{'key': 'last'}], 'next_cursor': None},
    ]
    responses.extend(httpx.Response(200, json=page) for page in pages)
    params = {'limit': 1, 'under': 'a/b', 'cursor': 'start'}
    client = SoevClient('https://soev.invalid', 'test-api-key')

    assert [item async for item in client.pages('/v1/collections/kb/folders', params=params)] == [
        {'key': 'first'},
        {'key': 'second'},
        {'key': 'last'},
    ]
    assert [request.url.params['cursor'] for request in requests] == ['start', 'opaque/+= cursor', 'last']
    assert all(request.url.params['under'] == 'a/b' and request.url.params['limit'] == '1' for request in requests)
    assert params == {'limit': 1, 'under': 'a/b', 'cursor': 'start'}


@pytest.mark.asyncio
async def test_every_page_carries_a_fresh_assertion(recorded_http, caplog):
    """Each cursor request mints a distinct assertion for the same external ref."""
    requests, responses = recorded_http
    responses.extend(
        httpx.Response(200, json={'data': [{'key': index}], 'next_cursor': cursor})
        for index, cursor in enumerate(['cursor-1', 'cursor-2', None])
    )
    minted_for = []

    def mint(user_ref):
        minted_for.append(user_ref)
        return f'test-assertion-{len(minted_for)}'

    client = SoevClient('https://soev.invalid', 'test-api-key', subject_minter=mint)
    with caplog.at_level(logging.DEBUG):
        assert [item async for item in client.pages('/v1/collections', as_user='owui:user:alice')] == [
            {'key': 0},
            {'key': 1},
            {'key': 2},
        ]
    assert minted_for == ['owui:user:alice'] * 3
    assert [request.headers['X-Soev-Subject'] for request in requests] == [
        'test-assertion-1',
        'test-assertion-2',
        'test-assertion-3',
    ]
    assert 'test-assertion-' not in caplog.text
    assert 'test-api-key' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['get', 'pages', 'send'])
async def test_as_user_without_a_minter_is_refused(recorded_http, method):
    """Acting as a user requires a configured minter before any HTTP request."""
    requests, _ = recorded_http
    client = SoevClient('https://soev.invalid', 'test-api-key')
    with pytest.raises(ValueError, match='minter'):
        if method == 'pages':
            _ = [item async for item in client.pages('/v1/collections', as_user='owui:user:alice')]
        elif method == 'send':
            await client.send('POST', '/v1/collections', {}, as_user='owui:user:alice', idempotency_key='operation-1')
        else:
            await client.get('/v1/collections', as_user='owui:user:alice')
    assert requests == []


@pytest.mark.asyncio
async def test_a_no_content_mutation_returns_none(recorded_http):
    """Directory membership replacement accepts the contract's empty 204 response."""
    requests, responses = recorded_http
    responses.append(httpx.Response(204))
    result = await SoevClient('https://soev.invalid', 'test-api-key').send(
        'PUT', '/v1/directory/groups/owui:group:group-1/members', {'members': []}, idempotency_key='operation-1'
    )
    assert result is None
    assert json.loads(requests[0].content) == {'members': []}


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [b'<html>private-body</html>', b'[]', b'{"code": 1, "detail": []}'])
@pytest.mark.parametrize('status', [400, 503])
async def test_an_invalid_problem_uses_a_safe_fallback(recorded_http, body, status):
    """Malformed error documents produce a stable error without exposing response bytes."""
    _, responses = recorded_http
    responses.append(httpx.Response(status, content=body, headers={'Content-Type': 'application/problem+json'}))
    with pytest.raises(SoevApiError) as caught:
        await SoevClient('https://soev.invalid', 'test-api-key').get('/v1/collections')
    assert caught.value.status == status
    assert caught.value.code == 'upstream_error'
    assert 'private-body' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('error_type, status', [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
@pytest.mark.parametrize('method', ['get', 'get_text', 'put_bytes'])
async def test_transport_failures_are_safe_and_are_not_retried(recorded_http, caplog, error_type, status, method):
    """Transport exceptions cannot expose credentials or replay a subject assertion."""
    requests, responses = recorded_http
    responses.append(error_type('private-transport-detail test-api-key test-assertion'))
    with caplog.at_level(logging.DEBUG), pytest.raises(SoevApiError) as caught:
        client = SoevClient('https://soev.invalid', 'test-api-key', subject_minter=lambda _: 'test-assertion')
        if method == 'put_bytes':
            await client.put_bytes('https://s3.invalid/file?signature=private-signature', headers={}, body=b'upload')
        else:
            await getattr(client, method)('/v1/collections', as_user='owui:user:alice')
    assert caught.value.status == status
    assert caught.value.code == ('upload_failed' if method == 'put_bytes' else 'upstream_error')
    assert len(requests) == 1
    assert caught.value.__suppress_context__
    assert 'private-transport-detail' not in str(caught.value) + caplog.text


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed(recorded_http):
    """The HTTP client never forwards an assertion to a redirect target."""
    requests, responses = recorded_http
    responses.append(httpx.Response(302, headers={'Location': 'https://other.invalid/'}))
    with pytest.raises(SoevApiError):
        await SoevClient('https://soev.invalid', 'test-api-key', subject_minter=lambda _: 'test-assertion').get(
            '/v1/collections', as_user='owui:user:alice'
        )
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['https://other.invalid/v1/collections', '//other.invalid/v1/collections'])
async def test_only_api_paths_are_accepted(recorded_http, path):
    """Caller-supplied paths cannot send the credential to another origin."""
    requests, _ = recorded_http
    with pytest.raises(ValueError, match='path'):
        await SoevClient('https://soev.invalid', 'test-api-key').get(path)
    assert requests == []


@pytest.mark.parametrize('configured', [False, True])
def test_settings_are_environment_only_and_secrets_are_not_registered_or_logged(tmp_path, configured):
    """Deployment settings preserve PEM bytes and never enter database defaults or logs."""
    settings = {
        'SOEV_API_URL': 'https://soev.invalid',
        'SOEV_API_KEY': 'test-api-key-never-persist',
        'SOEV_API_SIGNING_KEY': '-----BEGIN PRIVATE KEY-----\ntest-signing-key\n-----END PRIVATE KEY-----\n',
        'SOEV_API_SIGNING_KID': 'key-1',
        'SOEV_API_AUDIENCE': 'tenant-1',
        'SOEV_API_SERVICE_PRINCIPAL': 'owui:service:webui',
    }
    environment = {key: value for key, value in os.environ.items() if key not in settings}
    environment.update(
        DATA_DIR=str(tmp_path),
        DATABASE_URL=f'sqlite:///{tmp_path / "config.db"}',
        ENABLE_DB_MIGRATIONS='False',
        STATIC_DIR=str(tmp_path / 'static'),
    )
    if configured:
        environment.update(settings)
    code = """
import json
import logging
import sys
from io import StringIO

output = StringIO()
logging.basicConfig(stream=output, level=logging.DEBUG, force=True)
from open_webui import config

expected = json.loads(sys.argv[1])
for name, value in expected.items():
    assert getattr(config, name) == value, name
assert not any(key.startswith('soev_api.') for key in config.DEFAULT_CONFIG)
for name in ('SOEV_API_KEY', 'SOEV_API_SIGNING_KEY'):
    value = expected[name]
    if value:
        assert value not in repr(config.DEFAULT_CONFIG)
        assert value not in output.getvalue()
"""
    expected = settings if configured else dict.fromkeys(settings, '')
    result = subprocess.run(
        [sys.executable, '-c', code, json.dumps(expected)], env=environment, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_original_stream_is_incremental_and_closes_on_disconnect(recorded_http):
    requests, responses = recorded_http

    class Body(httpx.AsyncByteStream):
        closed = False
        delivered = 0

        async def __aiter__(self):
            for chunk in (b'first', b'second'):
                self.delivered += 1
                yield chunk

        async def aclose(self):
            self.closed = True

    body = Body()
    responses.extend(
        [
            httpx.Response(303, headers={'Location': 'https://storage.invalid/original'}),
            httpx.Response(200, stream=body),
        ]
    )
    client = SoevClient('https://soev.invalid', 'test-api-key', subject_minter=lambda ref: 'assertion')
    stream = client.stream('/v1/collections/kb/documents/file/original', as_user='owui:user:alice')
    assert await anext(stream) == b'first'
    assert body.delivered == 1 and not body.closed
    await stream.aclose()
    assert body.closed and body.delivered == 1
    assert requests[0].headers['X-Soev-Subject'] == 'assertion'
    assert 'X-Soev-Subject' not in requests[1].headers
    assert 'Authorization' not in requests[1].headers


@pytest.mark.asyncio
async def test_original_stream_follows_only_one_redirect(recorded_http):
    requests, responses = recorded_http
    responses.extend(
        [
            httpx.Response(303, headers={'Location': 'https://storage.invalid/original'}),
            httpx.Response(303, headers={'Location': 'https://elsewhere.invalid/original'}),
        ]
    )
    client = SoevClient('https://soev.invalid', 'test-api-key', subject_minter=lambda ref: 'assertion')
    with pytest.raises(SoevApiError) as error:
        await anext(client.stream('/original', as_user='owui:user:alice'))
    assert error.value.status == 502
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_get_memo_is_scoped_to_request_subject_and_parameters(recorded_http):
    """Concurrent identical reads share one call while subjects, parameters and requests stay isolated."""
    import asyncio

    from open_webui.soev.request_cache import request_cache

    requests, responses = recorded_http
    responses.extend(httpx.Response(200, json={'value': index}) for index in range(5))
    client = SoevClient('https://soev.invalid', 'test-key', subject_minter=lambda ref: 'assertion')
    with request_cache():
        first, second = await asyncio.gather(
            client.get('/v1/collections', as_user='alice'), client.get('/v1/collections', as_user='alice')
        )
        assert first == second == {'value': 0}
        assert await client.get('/v1/collections', as_user='bob') == {'value': 1}
        assert await client.get('/v1/collections', as_user='alice', params={'cursor': 'next'}) == {'value': 2}
    with request_cache():
        assert await client.get('/v1/collections', as_user='alice') == {'value': 3}
    assert await client.get('/v1/collections', as_user='alice') == {'value': 4}
    assert len(requests) == 5


@pytest.mark.asyncio
async def test_mutation_invalidates_request_memo(recorded_http):
    """A read after a mutation observes the new upstream state within the same request."""
    from open_webui.soev.request_cache import request_cache

    _, responses = recorded_http
    responses.extend(
        [httpx.Response(200, json={'name': 'old'}), httpx.Response(204), httpx.Response(200, json={'name': 'new'})]
    )
    client = SoevClient('https://soev.invalid', 'test-key')
    with request_cache():
        assert await client.get('/v1/collections/kb') == {'name': 'old'}
        await client.send('PATCH', '/v1/collections/kb', {'name': 'new'}, idempotency_key='rename')
        assert await client.get('/v1/collections/kb') == {'name': 'new'}


@pytest.mark.asyncio
async def test_request_debug_log_has_timing_without_credentials_or_query(recorded_http, caplog):
    """Debug timing identifies the path and status without logging request credentials or query values."""
    _, responses = recorded_http
    responses.append(httpx.Response(200, json={}))
    client = SoevClient('https://soev.invalid', 'private-api-key')
    with caplog.at_level(logging.DEBUG, logger='open_webui.soev.client'):
        await client.get('/v1/collections/kb?cursor=private-cursor')
    record = next(record for record in caplog.records if record.getMessage().startswith('soev-api request'))
    message = record.getMessage()
    assert message.startswith('soev-api request GET /v1/collections/kb 200 ')
    assert message.endswith('ms')
    assert 'private-api-key' not in str(record.__dict__)
    assert 'private-cursor' not in str(record.__dict__)
