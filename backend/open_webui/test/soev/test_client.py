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
async def test_transport_failures_are_safe_and_are_not_retried(recorded_http, caplog, error_type, status):
    """Transport exceptions cannot expose credentials or replay a subject assertion."""
    requests, responses = recorded_http
    responses.append(error_type('private-transport-detail test-api-key test-assertion'))
    with caplog.at_level(logging.DEBUG), pytest.raises(SoevApiError) as caught:
        await SoevClient('https://soev.invalid', 'test-api-key', subject_minter=lambda _: 'test-assertion').get(
            '/v1/collections', as_user='owui:user:alice'
        )
    assert caught.value.status == status
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
