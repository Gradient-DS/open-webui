"""HTTP contracts for the subject-scoped cloud-sync router and popup page."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from open_webui.routers import cloud_sync
from open_webui.soev.client import SoevClient, close_client


@pytest.fixture
def api(monkeypatch, platform_capabilities):
    """Exercise the real client against queued HTTP responses without network access."""
    monkeypatch.setattr('open_webui.config.SOEV_API_URL', 'https://soev.invalid')
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
    user = SimpleNamespace(id='alice', role='user', email='alice@example.com')
    app.dependency_overrides[cloud_sync.get_verified_user] = lambda: user
    with TestClient(app) as browser:
        browser.portal.call(close_client)
        yield SimpleNamespace(browser=browser, app=app, requests=requests, responses=responses, refs=refs, link=link)
        browser.portal.call(close_client)
    assert not responses


def response(body, status=200):
    return httpx.Response(status, json=body)


@pytest.mark.parametrize('provider', ['onedrive', 'google_drive'])
def test_create_connection_returns_the_authorize_url(api, provider):
    """Creating a subject connection starts consent through a separate asserted call."""
    api.responses.extend(
        [
            response({'id': 'connection-1', 'lifecycle': 'pending'}, 201),
            response({'authorize_url': 'https://provider.invalid/consent', 'expires_at': '2026-09-17T12:00:00Z'}),
        ]
    )
    result = api.browser.post('/api/v1/cloud-sync/connections', json={'provider': provider})
    assert result.status_code == 200
    assert result.json() == {'connection_id': 'connection-1', 'authorize_url': 'https://provider.invalid/consent'}
    assert json.loads(api.requests[0].content) == {'source_kind': provider, 'credential_kind': 'user_oauth'}
    if provider == 'google_drive':
        assert json.loads(api.requests[1].content) == {'owner_email': 'alice@example.com'}
    else:
        assert not api.requests[1].content
    assert [(request.method, request.url.path) for request in api.requests] == [
        ('POST', '/v1/connections'),
        ('POST', '/v1/connections/connection-1/authorize'),
    ]
    assert api.refs == ['owui:user:alice', 'owui:user:alice']
    assert_assertions(api.requests)


def test_create_connection_reuses_an_existing_healthy_connection_without_consent(api):
    """soev-api answers with the user's enabled connection: no authorize call, no authorize URL."""
    api.responses.append(response({'id': 'conn-live', 'source_kind': 'onedrive', 'lifecycle': 'enabled'}))
    result = api.browser.post('/api/v1/cloud-sync/connections', json={'provider': 'onedrive'})
    assert result.status_code == 200
    assert result.json() == {'connection_id': 'conn-live', 'authorize_url': None}
    assert [(request.method, request.url.path) for request in api.requests] == [('POST', '/v1/connections')]
    assert_assertions(api.requests)


@pytest.mark.parametrize(
    'connection',
    [
        {'lifecycle': 'pending'},
        {'lifecycle': 'suspended:reauth'},
        {'lifecycle': 'enabled', 'last_error': 'reauth_required'},
    ],
)
def test_create_connection_authorizes_an_existing_connection_that_needs_consent(api, connection):
    """A returned pending, suspended or failing connection gets consent instead of a duplicate."""
    api.responses.extend(
        [
            response({'id': 'conn-old', 'source_kind': 'onedrive', **connection}),
            response({'authorize_url': 'https://provider.invalid/consent'}),
        ]
    )
    result = api.browser.post('/api/v1/cloud-sync/connections', json={'provider': 'onedrive'})
    assert result.status_code == 200
    assert result.json() == {'connection_id': 'conn-old', 'authorize_url': 'https://provider.invalid/consent'}
    assert [(request.method, request.url.path) for request in api.requests] == [
        ('POST', '/v1/connections'),
        ('POST', '/v1/connections/conn-old/authorize'),
    ]


@pytest.mark.parametrize('result', ['pending', 'error', 'invalid'])
def test_the_done_page_posts_the_result_and_closes(api, result, monkeypatch):
    """The popup posts its escaped result to its own origin and each configured product origin, then closes."""
    monkeypatch.setattr(cloud_sync, 'CORS_ALLOW_ORIGIN', ['*', 'http://localhost:18273', '</script>'])
    connection = '</script><script>alert(1)</script>'
    page = api.browser.get('/api/v1/cloud-sync/connect/done', params={'connection': connection, 'result': result})
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'no-store'
    assert 'window.close();' in page.text
    assert page.text.count('</script>') == 1
    origins = page.text.split('new Set([window.location.origin, ...', 1)[1].split('])', 1)[0]
    assert json.loads(origins) == ['</script>', 'http://localhost:18273']
    payload = page.text.split('postMessage(', 1)[1].split(', origin)', 1)[0]
    assert json.loads(payload) == {'type': 'soev_connect', 'connection': connection, 'result': result}
    assert not api.requests


@pytest.mark.parametrize('cadence', [None, 60])
def test_a_schedule_is_registered_on_the_kbs_collection_key(api, cadence):
    """Registration derives the destination from the KB route and rejects a body override."""
    body = {
        'connection_id': 'connection-1',
        'kind': 'content',
        'scope': {'drive_id': 'drive', 'item_id': 'folder'},
    }
    if cadence is not None:
        body['cadence_minutes'] = cadence
    api.responses.append(
        response({'id': 'schedule-1', **body, 'collection_key': 'corpus:drive', 'subscribers': ['kb-1']}, 201)
    )
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
        'collection_key': 'corpus:drive',
        'subscribers': ['kb-1', 'kb-2'],
        'subscriber_count': 2,
        'document_count': 12,
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
            response({'data': [second], 'next_cursor': None}),
            response(connection),
        ]
    )
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb-1/sync')
    assert result.status_code == 200
    assert result.json() == {'schedules': [{**detail, 'connection': connection}, {**second, 'connection': connection}]}
    assert dict(api.requests[1].url.params) == {'collection_key': 'kb-1'}
    assert dict(api.requests[2].url.params) == {'collection_key': 'kb-1', 'cursor': 'page-2'}
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
        'retry_failed',
        'cancel',
        'suspend',
        'resume',
        'foreign',
        'unauthenticated',
    ],
)
def test_every_call_carries_the_users_assertion(api, operation, monkeypatch):
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
        decline = AsyncMock()
        monkeypatch.setattr(cloud_sync.silent_connect, 'decline', decline)
        api.responses.append(response({'id': 'c', 'source_kind': 'outlook_mail'}))
        api.responses.append(httpx.Response(204))
        result = api.browser.delete(prefix + '/connections/c')
        assert result.status_code == 204
        # A disconnected provider is not connected again by the next Microsoft login.
        decline.assert_awaited_once_with('alice', 'outlook_mail')
    elif operation == 'authorize':
        api.responses.append(response({'id': 'c', 'source_kind': 'onedrive'}))
        api.responses.append(response({'authorize_url': 'https://provider.invalid/fresh'}))
        result = api.browser.post(prefix + '/connections/c/authorize')
        assert result.json()['authorize_url'].endswith('/fresh')
    else:
        api.responses.append(
            response(
                {
                    'id': 's',
                    'collection_key': 'corpus:drive',
                    'subscribers': ['foreign' if operation == 'foreign' else 'kb'],
                }
            )
        )
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
            runs = operation in ('run', 'retry_failed')
            api.responses.append(response({'job_id': 'job-1'}, 201) if runs else httpx.Response(204))
            result = api.browser.post(path + '/' + operation)
            assert result.status_code == (201 if runs else 204)
            assert result.content == (b'{"job_id":"job-1"}' if runs else b'')
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


@pytest.mark.parametrize('provider', ['google_drive', 'onedrive'])
def test_reauthorize_sends_owner_email_only_for_google(api, provider):
    api.responses.extend(
        [
            response({'id': 'c', 'source_kind': provider}),
            response({'authorize_url': 'https://soev.invalid/oauth/start/attempt'}),
        ]
    )
    result = api.browser.post('/api/v1/cloud-sync/connections/c/authorize')
    assert result.status_code == 200
    if provider == 'google_drive':
        assert json.loads(api.requests[-1].content) == {'owner_email': 'alice@example.com'}
    else:
        assert not api.requests[-1].content
    assert api.refs == ['owui:user:alice', 'owui:user:alice']
    assert_assertions(api.requests)


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


@pytest.mark.parametrize('key', ['kb-1', 'kb: space&plus+?=#é'])
def test_delete_forwards_the_kb_as_collection_key(api, key):
    """Unsubscribing encodes the KB query value without changing the corpus destination."""
    from urllib.parse import quote

    api.responses.extend(
        [
            response({'id': 's', 'collection_key': 'corpus:drive', 'subscribers': [key, 'other-kb']}),
            httpx.Response(204),
        ]
    )
    result = api.browser.delete(f'/api/v1/cloud-sync/knowledge/{quote(key, safe="")}/schedules/s')
    assert result.status_code == 204
    assert api.requests[-1].method == 'DELETE'
    assert api.requests[-1].url.path == '/v1/schedules/s'
    assert dict(api.requests[-1].url.params) == {'collection_key': key}
    assert_assertions(api.requests)


@pytest.mark.parametrize('action', ['delete', 'run', 'retry_failed', 'cancel', 'suspend', 'resume'])
@pytest.mark.parametrize('subscribed', [False, True])
def test_actions_require_the_kb_to_subscribe(api, action, subscribed):
    """Every mutation checks subscribers even when the schedule destination equals the KB."""
    api.responses.append(
        response(
            {
                'id': 's',
                'collection_key': 'corpus:drive' if subscribed else 'kb',
                'subscribers': ['other-kb', 'kb'] if subscribed else ['other-kb'],
            }
        )
    )
    runs = action in ('run', 'retry_failed')
    if subscribed:
        api.responses.append(response({'job_id': 'j'}, 201) if runs else httpx.Response(204))
    path = '/api/v1/cloud-sync/knowledge/kb/schedules/s'
    result = api.browser.delete(path) if action == 'delete' else api.browser.post(path + '/' + action)
    assert result.status_code == ((201 if runs else 204) if subscribed else 404)
    assert len(api.requests) == (2 if subscribed else 1)
    if not subscribed:
        assert result.json()['detail']['code'] == 'connection_not_found'
    assert_assertions(api.requests)


@pytest.mark.parametrize('field', ['label', 'path'])
@pytest.mark.parametrize('length', [512, 513])
def test_schedule_display_fields_are_forwarded_with_limits(api, field, length):
    """Display fields pass through at the limit and are rejected before I/O above it."""
    body = {'connection_id': 'c', 'kind': 'content', 'scope': {}, field: 'a' * length}
    if length == 512:
        api.responses.append(response({'id': 's'}, 201))
    result = api.browser.post('/api/v1/cloud-sync/knowledge/kb/schedules', json=body)
    assert result.status_code == (200 if length == 512 else 422)
    if length == 512:
        assert json.loads(api.requests[0].content) == {**body, 'collection_key': 'kb'}
    else:
        assert not api.requests


def test_schedule_create_forwards_label_and_path(api):
    """Picker display fields remain outside the deduplicated scope."""
    body = {
        'connection_id': 'c',
        'kind': 'content',
        'scope': {'drive_id': 'd', 'item_id': 'folder'},
        'label': 'Reports',
        'path': '/Team/Reports',
    }
    api.responses.append(response({'id': 's', **body}, 201))
    result = api.browser.post('/api/v1/cloud-sync/knowledge/kb/schedules', json=body)
    assert result.status_code == 200
    assert json.loads(api.requests[0].content) == {**body, 'collection_key': 'kb'}
    assert result.json()['label'] == 'Reports'
    assert result.json()['path'] == '/Team/Reports'
    assert_assertions(api.requests)


def test_connection_usage_lists_subscribing_kbs(api):
    """Usage unions subscribers across every page and both schedule kinds as the owner."""
    api.responses.extend(
        [
            response({'id': 'c'}),
            response(
                {
                    'data': [
                        {'id': 's1', 'kind': 'content', 'subscribers': ['kb-1', 'kb-2']},
                        {'id': 's2', 'kind': 'acl_refresh', 'subscribers': ['kb-1', 'kb-2']},
                    ],
                    'next_cursor': 'next',
                }
            ),
            response({'data': [{'id': 's3', 'subscribers': ['kb-2', 'kb-3']}], 'next_cursor': None}),
        ]
    )
    result = api.browser.get('/api/v1/cloud-sync/connections/c/usage')
    assert result.status_code == 200
    assert result.json() == {'knowledge_ids': ['kb-1', 'kb-2', 'kb-3']}
    assert api.requests[0].url.path == '/v1/connections/c'
    assert api.requests[1].url.path == api.requests[2].url.path == '/v1/schedules'
    assert dict(api.requests[1].url.params) == {'connection_id': 'c'}
    assert dict(api.requests[2].url.params) == {'connection_id': 'c', 'cursor': 'next'}
    assert api.refs == ['owui:user:alice'] * 3
    assert_assertions(api.requests)


def test_connection_usage_without_schedules_is_empty(api):
    """An unused account has no subscribing knowledge bases."""
    api.responses.extend([response({'id': 'c'}), response({'data': [], 'next_cursor': None})])
    result = api.browser.get('/api/v1/cloud-sync/connections/c/usage')
    assert result.status_code == 200
    assert result.json() == {'knowledge_ids': []}
    assert_assertions(api.requests)


def test_connection_usage_refuses_an_inaccessible_connection(api):
    """A missing or foreign account is refused before schedules are listed."""
    api.responses.append(response({'code': 'connection_not_found', 'detail': 'No such connection'}, 404))
    result = api.browser.get('/api/v1/cloud-sync/connections/foreign/usage')
    assert result.status_code == 404
    assert len(api.requests) == 1
    assert_assertions(api.requests)


def test_connection_usage_requires_a_verified_user(api):
    """Usage cannot expose subscribers without an authenticated subject."""

    def reject():
        raise HTTPException(401)

    api.app.dependency_overrides[cloud_sync.get_verified_user] = reject
    assert api.browser.get('/api/v1/cloud-sync/connections/c/usage').status_code == 401
    assert not api.requests


def test_skipped_items_list_failed_job_items(api):
    """Skipped items use the run id, keep the specific reason and resolve names across document pages."""
    api.responses.extend(
        [
            response({'key': 'kb'}),
            response({'id': 's', 'kind': 'content', 'subscribers': ['kb'], 'last_run': {'id': 'job-1'}}),
            response(
                {
                    'items': [
                        {'source_id': 'ok', 'title': 'Good.docx', 'status': 'succeeded', 'code': None},
                        {'source_id': 'large', 'title': None, 'status': 'failed', 'code': 'item_too_large'},
                        {
                            'source_id': 'unknown',
                            'title': None,
                            'status': 'skipped',
                            'code': 'unsupported_content_type',
                        },
                        {'source_id': 'waiting', 'title': None, 'status': 'pending', 'code': None},
                        {
                            'source_id': 'empty',
                            'title': 'Empty.pdf',
                            'status': 'skipped',
                            'code': 'empty_content',
                            'detail': 'empty_parsed_content',
                        },
                        {
                            'source_id': 'empty',
                            'title': 'Empty.pdf',
                            'status': 'skipped',
                            'code': 'empty_content',
                        },
                        {
                            'source_id': 'broken',
                            'title': 'Current.pdf',
                            'status': 'failed',
                            'code': 'processing_failed',
                            'reason': 'bundle_missing',
                        },
                        {
                            'source_id': 'odd',
                            'title': 'Odd.pdf',
                            'status': 'failed',
                            'code': 'internal_error',
                            'detail': 'Traceback: something broke',
                            'reason': 'Graph said no',
                        },
                        {
                            'source_id': 'slow',
                            'title': 'Slow.pdf',
                            'status': 'failed',
                            'code': 'internal_error',
                            'detail': 'timed_out',
                        },
                    ]
                }
            ),
            response({'data': [{'source_id': 'ok', 'filename': 'Good.docx'}], 'next_cursor': 'next'}),
            response(
                {
                    'data': [
                        {'source_id': 'large', 'title': 'Large.pdf'},
                        {'source_id': 'broken', 'title': 'Old.pdf'},
                    ],
                    'next_cursor': None,
                }
            ),
        ]
    )
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb/schedules/s/skipped')
    assert result.status_code == 200
    assert result.json() == [
        {'source_id': 'large', 'name': 'Large.pdf', 'code': 'item_too_large', 'reason': None},
        {'source_id': 'unknown', 'name': 'unknown', 'code': 'unsupported_content_type', 'reason': None},
        {'source_id': 'waiting', 'name': 'waiting', 'code': 'pending', 'reason': None},
        {'source_id': 'empty', 'name': 'Empty.pdf', 'code': 'empty_content', 'reason': None},
        {'source_id': 'broken', 'name': 'Current.pdf', 'code': 'processing_failed', 'reason': 'bundle_missing'},
        # Free text never reaches the browser: neither a detail nor a reason.
        {'source_id': 'odd', 'name': 'Odd.pdf', 'code': 'internal_error', 'reason': None},
        {'source_id': 'slow', 'name': 'Slow.pdf', 'code': 'timed_out', 'reason': None},
    ]
    assert api.requests[2].url.path == '/v1/jobs/job-1'
    assert dict(api.requests[2].url.params) == {'include_items': 'true'}
    assert api.requests[3].url.path == '/v1/collections/kb/documents'
    assert dict(api.requests[4].url.params) == {'cursor': 'next'}
    assert api.refs == ['owui:user:alice'] * 5
    assert_assertions(api.requests)


def test_skipped_items_ignore_acl_refresh_echoes(api):
    """ACL refresh skips never trigger a job lookup or duplicate content failures."""
    api.responses.extend(
        [
            response({'key': 'kb'}),
            response({'id': 'acl', 'kind': 'acl_refresh', 'subscribers': ['kb'], 'last_run': {'id': 'acl-job'}}),
        ]
    )
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb/schedules/acl/skipped')
    assert result.status_code == 200
    assert result.json() == []
    assert len(api.requests) == 2
    assert_assertions(api.requests)


@pytest.mark.parametrize('subscribed', [False, True])
def test_skipped_items_require_subscription_and_allow_no_run(api, subscribed):
    """A foreign schedule is refused and an unstarted schedule needs no job request."""
    api.responses.extend(
        [
            response({'key': 'kb'}),
            response(
                {'id': 's', 'kind': 'content', 'subscribers': ['kb'] if subscribed else ['other'], 'last_run': None}
            ),
        ]
    )
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb/schedules/s/skipped')
    assert result.status_code == (200 if subscribed else 404)
    if subscribed:
        assert result.json() == []
    assert len(api.requests) == 2
    assert_assertions(api.requests)


def test_skipped_items_require_readable_knowledge(api):
    """An unreadable KB is refused before fetching its schedule or job."""
    api.responses.append(response({'code': 'collection_not_found', 'detail': 'No such collection'}, 404))
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb/schedules/s/skipped')
    assert result.status_code == 404
    assert len(api.requests) == 1


def test_policy_uses_runtime_credential_without_linking_a_subject(api, monkeypatch) -> None:
    monkeypatch.setattr('open_webui.config.SOEV_API_URL', 'https://soev.invalid')
    policy = {
        'providers_enabled': ['onedrive', 'confluence'],
        'scope_shapes_allowed': {'onedrive': ['folder', 'file']},
        'min_cadence_minutes': 15,
        'default_cadence_minutes': 60,
    }
    api.responses.append(response({**policy, 'updated_by': 'admin', 'limits': {}}))
    result = api.browser.get('/api/v1/cloud-sync/policy')
    assert result.status_code == 200
    assert result.json() == policy
    assert len(api.requests) == 1
    request = api.requests[0]
    assert (request.method, request.url.path) == ('GET', '/v1/sync-policy')
    assert request.headers['Authorization'] == 'Bearer invalid-test-runtime-key'
    assert 'X-Soev-Subject' not in request.headers
    assert api.refs == []
    api.link.assert_not_awaited()


def test_policy_is_empty_without_soev_api(api, monkeypatch) -> None:
    monkeypatch.setattr('open_webui.config.SOEV_API_URL', '')
    result = api.browser.get('/api/v1/cloud-sync/policy')
    assert result.status_code == 200
    assert result.json() == {
        'providers_enabled': [],
        'scope_shapes_allowed': {},
        'min_cadence_minutes': None,
        'default_cadence_minutes': None,
    }
    cloud_sync.identity.build_client.assert_not_called()
    api.link.assert_not_awaited()


def test_policy_is_empty_without_cloud_sync_capability(api, platform_capabilities):
    platform_capabilities.discard('cloud_sync')
    result = api.browser.get('/api/v1/cloud-sync/policy')
    assert result.status_code == 200
    assert result.json()['providers_enabled'] == []
    assert not api.requests


@pytest.mark.parametrize('feature', ['cloud_sync', 'live_documents', 'live_mail'])
def test_connections_remain_available_for_each_connection_family(api, platform_capabilities, feature):
    platform_capabilities.intersection_update({feature})
    api.responses.append(response({'data': [], 'next_cursor': None}))
    assert api.browser.get('/api/v1/cloud-sync/connections').status_code == 200
    assert api.requests[0].url.path == '/v1/connections'


@pytest.mark.parametrize(
    'path,capability',
    [
        ('knowledge/kb/schedules/s/skipped', 'cloud_sync'),
        ('connections/c/live/live_documents', 'live_documents'),
        ('connections/c/live/mail', 'live_mail'),
        ('connections/c/usage', 'cloud_sync'),
    ],
)
def test_missing_capability_refuses_before_platform_calls(api, platform_capabilities, path, capability):
    platform_capabilities.discard(capability)
    result = api.browser.get(f'/api/v1/cloud-sync/{path}')
    assert result.status_code == 503
    assert result.json()['detail']['code'] == 'feature_unavailable'
    assert not api.requests
    api.link.assert_not_awaited()


def test_sync_status_without_cloud_sync_is_empty(api, platform_capabilities):
    """A tenant without cloud sync answers status with no schedules and no platform call."""
    platform_capabilities.discard('cloud_sync')
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb-local/sync')
    assert result.status_code == 200
    assert result.json() == {'schedules': []}
    assert not api.requests
    api.link.assert_not_awaited()


def test_sync_status_with_unknown_capabilities_still_refuses(api, platform_capabilities, monkeypatch):
    """An unread capability set is not taken as no cloud sync."""
    platform_capabilities.clear()
    monkeypatch.setattr(cloud_sync.capabilities, 'known', lambda: False)
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb-cloud/sync')
    assert result.status_code == 503
    assert result.json()['detail']['code'] == 'feature_unavailable'
    assert not api.requests


def test_sync_status_maps_a_platform_failure(api):
    """A platform error on a status read still reaches the browser as an error."""
    api.responses.extend([response({'code': 'internal_error', 'detail': 'boom'}, 500)] * 2)
    result = api.browser.get('/api/v1/cloud-sync/knowledge/kb-cloud/sync')
    assert result.status_code == 500
    assert result.json()['detail']['code'] == 'upstream_error'


def test_no_connection_capabilities_refuses_connection_routes(api, platform_capabilities):
    platform_capabilities.clear()
    result = api.browser.get('/api/v1/cloud-sync/connections')
    assert result.status_code == 503
    assert result.json()['detail']['code'] == 'feature_unavailable'
    assert not api.requests


def test_policy_preserves_upstream_forbidden_code(api, monkeypatch) -> None:
    monkeypatch.setattr('open_webui.config.SOEV_API_URL', 'https://soev.invalid')
    api.responses.append(
        httpx.Response(
            403,
            json={'code': 'forbidden', 'detail': 'Policy denied'},
            headers={'Content-Type': 'application/problem+json'},
        )
    )
    result = api.browser.get('/api/v1/cloud-sync/policy')
    assert result.status_code == 403
    assert result.json() == {'detail': {'code': 'forbidden', 'detail': 'Policy denied'}}
    api.link.assert_not_awaited()
    assert api.refs == []


def test_policy_requires_a_verified_user(api) -> None:
    def deny() -> None:
        raise HTTPException(status_code=401, detail='Not authenticated')

    api.app.dependency_overrides[cloud_sync.get_verified_user] = deny
    assert api.browser.get('/api/v1/cloud-sync/policy').status_code == 401
    cloud_sync.identity.build_client.assert_not_called()


@pytest.mark.parametrize('provider,family', [('onedrive', 'live_documents'), ('outlook_mail', 'mail')])
@pytest.mark.parametrize('lifecycle', ['enabled', 'suspended', 'missing'])
def test_live_document_grant_enable_uses_owned_connection(api, lifecycle, provider, family):
    """Only a user's OneDrive grant is reused, resumed or created with the document family."""
    grant = {'id': 'g', 'families': [family], 'lifecycle': lifecycle}
    api.responses.extend(
        [
            response({'id': 'c', 'source_kind': provider}),
            response({'data': [] if lifecycle == 'missing' else [grant]}),
        ]
    )
    if lifecycle != 'enabled':
        api.responses.append(response({**grant, 'lifecycle': 'enabled'}))
    result = api.browser.post(f'/api/v1/cloud-sync/connections/c/live/{family}')
    assert result.status_code == 200 and result.json()['lifecycle'] == 'enabled'
    assert all(r.headers.get('X-Soev-Subject') for r in api.requests)
    if lifecycle == 'missing':
        assert json.loads(api.requests[-1].content) == {'families': [family]}
        assert api.requests[-1].headers['Idempotency-Key'] == f'live:{family}:c'
    elif lifecycle == 'suspended':
        assert api.requests[-1].method == 'PATCH'
        assert json.loads(api.requests[-1].content) == {'enabled': True}


def test_live_document_grant_is_provider_neutral(api):
    """The platform decides which providers can grant the document family."""
    grant = {'id': 'g', 'families': ['live_documents'], 'lifecycle': 'enabled'}
    api.responses.extend([response({'id': 'c', 'source_kind': 'google_drive'}), response({'data': [grant]})])
    result = api.browser.post('/api/v1/cloud-sync/connections/c/live/live_documents')
    assert result.status_code == 200 and result.json() == grant


@pytest.mark.parametrize('key', ['kb-1', 'kb: space&plus+?=#é'])
def test_sync_all_runs_the_kbs_schedules_in_one_call(api, key):
    """Sync all forwards the KB as collection key and returns per-schedule outcomes."""
    from urllib.parse import quote

    runs = {
        'data': [
            {'schedule_id': 'a', 'job_id': 'j', 'code': None},
            {'schedule_id': 'b', 'job_id': None, 'code': 'run_too_soon'},
        ]
    }
    api.responses.append(response(runs))
    result = api.browser.post(f'/api/v1/cloud-sync/knowledge/{quote(key, safe="")}/sync')
    assert result.status_code == 200
    assert result.json() == runs
    assert api.requests[0].method == 'POST'
    assert api.requests[0].url.raw_path.decode() == f'/v1/collections/{quote(key, safe="")}/sync'
    assert api.requests[0].headers['Idempotency-Key']
    assert_assertions(api.requests)


@pytest.mark.parametrize('operation', ['list', 'add', 'duplicate', 'remove'])
def test_shared_mailbox_proxies_use_the_users_identity(api, operation):
    mailbox = {'address': 'gedeeld@example.test', 'added_at': '2026-10-31T12:00:00Z'}
    path = '/api/v1/cloud-sync/connections/c/mailboxes'
    upstream = '/v1/connections/c/mailboxes'
    if operation == 'list':
        body = {'enabled': True, 'consented': False, 'mailboxes': [mailbox]}
        api.responses.append(response(body))
        result = api.browser.get(path)
        assert result.json() == body
    elif operation == 'remove':
        api.responses.append(httpx.Response(204))
        result = api.browser.delete(path + '/gedeeld%40example.test')
        assert result.status_code == 204
        assert not result.content
        upstream += '/gedeeld@example.test'
    else:
        api.responses.append(response(mailbox, 201 if operation == 'add' else 200))
        result = api.browser.post(path, json={'address': mailbox['address']})
        assert result.json() == mailbox
        assert json.loads(api.requests[0].content) == {'address': mailbox['address']}
    assert result.is_success
    assert api.requests[0].url.path == upstream
    assert api.refs == ['owui:user:alice']
    assert_assertions(api.requests)


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
@pytest.mark.parametrize(
    'status,code,constraint',
    [
        (403, 'policy_forbids', None),
        (403, 'mailbox_access_denied', None),
        (409, 'reconsent_required', None),
        (409, 'mailbox_limit', None),
        (409, 'connection_required', None),
        (422, 'invalid_field', 'address:email'),
        (422, 'invalid_field', 'address:own_mailbox'),
    ],
)
def test_shared_mailbox_refusals_keep_their_code(api, method, status, code, constraint):
    problem = {'code': code, 'detail': 'Refused', **({'constraint': constraint} if constraint else {})}
    api.responses.append(httpx.Response(status, json=problem, headers={'Content-Type': 'application/problem+json'}))
    path = '/api/v1/cloud-sync/connections/c/mailboxes'
    if method == 'DELETE':
        path += '/gedeeld%40example.test'
    result = api.browser.request(
        method, path, **({'json': {'address': 'gedeeld@example.test'}} if method == 'POST' else {})
    )
    assert result.status_code == status
    assert result.json()['detail'] == problem
    assert_assertions(api.requests)


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
def test_shared_mailboxes_require_live_mail_and_a_verified_user(api, platform_capabilities, method):
    path = '/api/v1/cloud-sync/connections/c/mailboxes'
    if method == 'DELETE':
        path += '/gedeeld%40example.test'
    kwargs = {'json': {'address': 'gedeeld@example.test'}} if method == 'POST' else {}
    platform_capabilities.discard('live_mail')
    assert api.browser.request(method, path, **kwargs).status_code == 503

    def reject():
        raise HTTPException(401)

    api.app.dependency_overrides[cloud_sync.get_verified_user] = reject
    assert api.browser.request(method, path, **kwargs).status_code == 401
    assert not api.requests
    api.link.assert_not_awaited()


def test_outlook_silent_reauthorization_uses_the_signed_in_email(api):
    api.app.dependency_overrides[cloud_sync.get_verified_user] = lambda: SimpleNamespace(
        id='alice', role='user', email='gebruiker@example.test'
    )
    api.responses.extend(
        [
            response({'id': 'c', 'source_kind': 'outlook_mail', 'lifecycle': 'enabled'}),
            response({'authorize_url': 'https://provider.invalid/silent'}),
        ]
    )
    result = api.browser.post('/api/v1/cloud-sync/connections/c/authorize', json={'silent': True})
    assert result.status_code == 200
    assert json.loads(api.requests[1].content) == {'silent': True, 'owner_email': 'gebruiker@example.test'}
    assert_assertions(api.requests)


def test_silent_interaction_keeps_the_popup_for_interactive_consent(api):
    page = api.browser.get('/api/v1/cloud-sync/connect/done?connection=c&result=interaction_required')
    assert page.status_code == 200
    assert 'window.close();' not in page.text
    assert '"result": "interaction_required"' in page.text
