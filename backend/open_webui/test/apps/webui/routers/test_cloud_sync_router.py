"""HTTP contracts for the subject-scoped cloud-sync router and popup page."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from open_webui.routers import cloud_sync
from open_webui.soev.client import SoevClient


@pytest.fixture
def api(monkeypatch):
    """Exercise the real client against queued HTTP responses without network access."""
    requests, responses = [], []
    refs = []

    def mint(ref):
        refs.append(ref)
        return f'invalid-test-assertion-{len(refs)}'

    def handle(request):
        requests.append(request)
        return responses.pop(0)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = SoevClient('https://soev.invalid', 'invalid-test-runtime-key', subject_minter=mint)
    monkeypatch.setattr(cloud_sync.identity, 'build_client', Mock(return_value=client))
    link = AsyncMock()
    monkeypatch.setattr(cloud_sync.identity, 'ensure_link', link)
    app = FastAPI()
    app.include_router(cloud_sync.router, prefix='/api/v1/cloud-sync')
    user = SimpleNamespace(id='alice', role='user')
    app.dependency_overrides[cloud_sync.get_verified_user] = lambda: user
    with TestClient(app) as browser:
        yield SimpleNamespace(browser=browser, app=app, requests=requests, responses=responses, refs=refs, link=link)
    assert not responses


def response(body, status=200):
    return httpx.Response(status, json=body)


def test_create_connection_returns_the_authorize_url(api):
    """Creating a subject connection starts consent through a separate asserted call."""
    api.responses.extend(
        [
            response({'id': 'connection-1', 'lifecycle': 'pending'}, 201),
            response({'authorize_url': 'https://provider.invalid/consent', 'expires_at': '2026-09-17T12:00:00Z'}),
        ]
    )
    result = api.browser.post('/api/v1/cloud-sync/connections', json={'provider': 'onedrive'})
    assert result.status_code == 200
    assert result.json() == {'connection_id': 'connection-1', 'authorize_url': 'https://provider.invalid/consent'}
    assert json.loads(api.requests[0].content) == {'source_kind': 'onedrive', 'credential_kind': 'user_oauth'}
    assert [(request.method, request.url.path) for request in api.requests] == [
        ('POST', '/v1/connections'),
        ('POST', '/v1/connections/connection-1/authorize'),
    ]
    assert api.refs == ['owui:user:alice', 'owui:user:alice']
    assert_assertions(api.requests)


@pytest.mark.parametrize('result', ['pending', 'error', 'invalid'])
def test_the_done_page_posts_the_result_and_closes(api, result):
    """The popup posts only its escaped result to the same-origin opener and closes."""
    connection = '</script><script>alert(1)</script>'
    page = api.browser.get('/api/v1/cloud-sync/connect/done', params={'connection': connection, 'result': result})
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'no-store'
    assert 'window.location.origin' in page.text
    assert 'window.close();' in page.text
    assert page.text.count('</script>') == 1
    payload = page.text.split('postMessage(', 1)[1].split(', window.location.origin', 1)[0]
    assert json.loads(payload) == {'type': 'soev_connect', 'connection': connection, 'result': result}
    assert not api.requests


def test_a_schedule_is_registered_on_the_kbs_collection_key(api):
    """Registration derives the destination from the KB route and rejects a body override."""
    body = {
        'connection_id': 'connection-1',
        'kind': 'content',
        'scope': {'drive_id': 'drive', 'item_id': 'folder'},
        'cadence_minutes': 60,
    }
    api.responses.append(response({'id': 'schedule-1', **body, 'collection_key': 'kb-1'}, 201))
    result = api.browser.post('/api/v1/cloud-sync/knowledge/kb-1/schedules', json=body)
    assert result.status_code == 200
    assert json.loads(api.requests[0].content) == {**body, 'collection_key': 'kb-1'}
    assert api.requests[0].url.path == '/v1/schedules'
    assert_assertions(api.requests)
    assert (
        api.browser.post(
            '/api/v1/cloud-sync/knowledge/kb-1/schedules', json={**body, 'collection_key': 'foreign'}
        ).status_code
        == 422
    )


def test_sync_status_shapes_the_schedule_and_the_connection(api):
    """Status includes run progress, due time, expiry and connection lifecycle across pages.

    The listing now carries each schedule's own detail, so a page costs one
    request instead of one per schedule: four calls here, not six. The
    `last_run` below is a verbatim soev-api StoredRun — there is no `status`
    field; the fork derives one with `runStatus()`.
    """
    detail = {
        'id': 'schedule-1',
        'connection_id': 'connection-1',
        'collection_key': 'kb-1',
        'source_kind': 'onedrive',
        'last_run': {
            'id': 'run-1',
            'schedule_id': 'schedule-1',
            'job_id': 'job-1',
            'started_at': '2026-09-17T11:00:00Z',
            'heartbeat_at': '2026-09-17T11:00:05Z',
            'finished_at': None,
            'outcome': None,
            'counts': {'fetched': 3},
        },
        'next_due_at': '2026-09-17T12:00:00Z',
        'provider_secret_days_to_expiry': 12,
    }
    connection = {'id': 'connection-1', 'lifecycle': 'suspended:reauth', 'last_error': 'invalid_grant'}
    second = {**detail, 'id': 'schedule-2'}
    api.responses.extend(
        [
            response({'key': 'kb-1'}),
            response({'data': [detail], 'next_cursor': 'page-2'}),
            response(connection),
            response({'data': [second], 'next_cursor': None}),
        ]
    )
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb-1/sync')
    assert result.status_code == 200
    assert result.json() == {'schedules': [{**detail, 'connection': connection}, {**second, 'connection': connection}]}
    assert dict(api.requests[1].url.params) == {'collection_key': 'kb-1'}
    assert dict(api.requests[3].url.params) == {'collection_key': 'kb-1', 'cursor': 'page-2'}
    # One connection fetch for two schedules sharing it, and no per-schedule call.
    assert len(api.refs) == 4
    assert_assertions(api.requests)


@pytest.mark.parametrize(
    'operation',
    [
        'list',
        'get',
        'revoke',
        'authorize',
        'delete',
        'run',
        'cancel',
        'suspend',
        'resume',
        'foreign',
        'unauthenticated',
    ],
)
def test_every_call_carries_the_users_assertion(api, operation):
    """All calls carry fresh user assertions and mutations cannot cross a KB boundary."""
    prefix = '/api/v1/cloud-sync'
    if operation == 'unauthenticated':

        def reject():
            raise HTTPException(401)

        api.app.dependency_overrides[cloud_sync.get_verified_user] = reject
        assert api.browser.get(prefix + '/connections').status_code == 401
        assert api.browser.get(prefix + '/connect/done?connection=c&result=pending').status_code == 401
        assert not api.requests
        return
    if operation == 'list':
        api.responses.extend(
            [
                response({'data': [{'id': 'c'}], 'next_cursor': 'next'}),
                response({'data': [{'id': 'd'}], 'next_cursor': None}),
            ]
        )
        result = api.browser.get(prefix + '/connections')
        assert result.json() == [{'id': 'c'}, {'id': 'd'}]
    elif operation == 'get':
        api.responses.append(response({'id': 'c', 'lifecycle': 'pending', 'last_error': None}))
        result = api.browser.get(prefix + '/connections/c')
        assert result.json()['lifecycle'] == 'pending'
    elif operation == 'revoke':
        api.responses.append(httpx.Response(204))
        result = api.browser.delete(prefix + '/connections/c')
        assert result.status_code == 204
    elif operation == 'authorize':
        api.responses.append(response({'authorize_url': 'https://provider.invalid/fresh'}))
        result = api.browser.post(prefix + '/connections/c/authorize')
        assert result.json()['authorize_url'].endswith('/fresh')
    else:
        api.responses.append(response({'id': 's', 'collection_key': 'foreign' if operation == 'foreign' else 'kb'}))
        path = prefix + '/knowledge/kb/schedules/s'
        if operation == 'foreign':
            result = api.browser.post(path + '/run')
            assert result.status_code == 404
            assert len(api.requests) == 1
        elif operation == 'delete':
            api.responses.append(httpx.Response(204))
            result = api.browser.delete(path)
            assert result.status_code == 204
        else:
            api.responses.append(response({'job_id': 'job-1'}, 201) if operation == 'run' else httpx.Response(204))
            result = api.browser.post(path + '/' + operation)
            assert result.status_code == (201 if operation == 'run' else 204)
            assert result.content == (b'{"job_id":"job-1"}' if operation == 'run' else b'')
            assert api.requests[-1].url.path == '/v1/schedules/s/' + operation
    assert result.status_code < 300 or operation == 'foreign'
    api.link.assert_awaited_once()
    assert api.refs == ['owui:user:alice'] * len(api.requests)
    assert_assertions(api.requests)


def assert_assertions(requests):
    for index, request in enumerate(requests, 1):
        assert request.headers['Authorization'] == 'Bearer invalid-test-runtime-key'
        assert request.headers['X-Soev-Subject'] == f'invalid-test-assertion-{index}'
        if request.method != 'GET':
            assert request.headers['Idempotency-Key']


@pytest.mark.parametrize(
    ('status', 'code'),
    [(403, 'policy_forbids'), (404, 'connection_not_found'), (409, 'connection_pending'), (422, 'invalid_field')],
)
def test_a_soev_api_policy_refusal_maps_to_403_with_its_code(api, status, code):
    """The client problem mapping preserves the upstream HTTP status and stable code."""
    api.responses.append(
        httpx.Response(
            status,
            json={'code': code, 'detail': 'Refused', 'constraint': 'provider'},
            headers={'Content-Type': 'application/problem+json'},
        )
    )
    result = api.browser.post('/api/v1/cloud-sync/connections', json={'provider': 'onedrive'})
    assert result.status_code == status
    assert result.json() == {'detail': {'code': code, 'detail': 'Refused', 'constraint': 'provider'}}
