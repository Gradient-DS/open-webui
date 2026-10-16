"""Silent Microsoft reconnect after an Entra login: once per connection, as top-level redirects."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import cloud_sync
from open_webui.soev import silent_connect
from open_webui.soev.client import SoevClient, close_client

EMAIL = 'alice@example.invalid'
START = 'https://soev.invalid/oauth/start/invalid-attempt-capability-{}'
CONNECTIONS = [
    {'id': 'c-enabled', 'source_kind': 'onedrive', 'lifecycle': 'enabled'},
    {'id': 'c-revoked', 'source_kind': 'onedrive', 'lifecycle': 'revoked'},
    {'id': 'c-google', 'source_kind': 'google_drive', 'lifecycle': 'pending'},
    {'id': 'c-onedrive', 'source_kind': 'onedrive', 'lifecycle': 'pending'},
    {'id': 'c-mail', 'source_kind': 'outlook_mail', 'lifecycle': 'suspended:reauth'},
]


@pytest.fixture
def api(monkeypatch, platform_capabilities):
    """The real client against queued soev-api responses, with user settings held in memory."""
    monkeypatch.setattr('open_webui.config.SOEV_API_URL', 'https://soev.invalid')
    requests, responses = [], []

    def handle(request):
        requests.append(request)
        return responses.pop(0)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = SoevClient(
        'https://soev.invalid', 'invalid-test-runtime-key', subject_minter=lambda ref: 'invalid-test-assertion'
    )
    monkeypatch.setattr(silent_connect.identity, 'build_client', Mock(return_value=client))
    monkeypatch.setattr(silent_connect.identity, 'ensure_link', AsyncMock())
    settings: dict = {}

    async def get_user_by_id(user_id):
        return SimpleNamespace(id=user_id, settings=dict(settings))

    async def update_user_settings_by_id(user_id, updated):
        settings.update(updated)

    monkeypatch.setattr(
        silent_connect,
        'Users',
        SimpleNamespace(get_user_by_id=get_user_by_id, update_user_settings_by_id=update_user_settings_by_id),
    )
    monkeypatch.setattr(silent_connect, 'Config', SimpleNamespace(get=AsyncMock(return_value='https://owui.invalid/')))
    user = SimpleNamespace(id='alice', role='user', email=EMAIL)
    app = FastAPI()
    app.include_router(cloud_sync.router, prefix='/api/v1/cloud-sync')
    app.dependency_overrides[cloud_sync.get_verified_user] = lambda: user
    with TestClient(app) as browser:
        browser.portal.call(close_client)
        yield SimpleNamespace(
            browser=browser, requests=requests, responses=responses, settings=settings, user=user, portal=browser.portal
        )
        browser.portal.call(close_client)
    assert not responses


def queue_hop(api, connection_id):
    api.responses.extend(
        [
            httpx.Response(200, json={'data': CONNECTIONS}),
            httpx.Response(
                200, json={'authorize_url': START.format(connection_id), 'expires_at': '2026-10-16T12:05:00Z'}
            ),
        ]
    )


def login(api, provider='microsoft'):
    return api.portal.call(silent_connect.after_login, api.user, provider, 'https://owui.invalid/auth')


def done(api, connection, result):
    return api.browser.get(
        '/api/v1/cloud-sync/connect/done',
        params={'connection': connection, 'result': result},
        follow_redirects=False,
    )


def test_a_login_starts_one_silent_attempt_for_the_first_microsoft_connection_needing_consent(api):
    """Enabled, revoked and Google connections are left alone; the attempt asks for silent mode."""
    queue_hop(api, 'c-onedrive')
    assert login(api) == START.format('c-onedrive')
    assert [(request.method, request.url.path) for request in api.requests] == [
        ('GET', '/v1/connections'),
        ('POST', '/v1/connections/c-onedrive/authorize'),
    ]
    assert json.loads(api.requests[1].content) == {'owner_email': EMAIL, 'silent': True}
    assert api.settings[silent_connect.SETTINGS_KEY]['tried'] == ['c-onedrive']
    assert api.settings[silent_connect.SETTINGS_KEY]['inflight']['connection'] == 'c-onedrive'


def test_the_chain_tries_each_connection_once_and_then_lands_in_the_app(api):
    """Done continues with the next connection, then the sign-in landing; nothing is tried twice."""
    queue_hop(api, 'c-onedrive')
    login(api)
    queue_hop(api, 'c-mail')
    page = done(api, 'c-onedrive', 'interaction_required')
    assert page.status_code == 302
    assert page.headers['location'] == START.format('c-mail')
    assert page.headers['cache-control'] == 'no-store'
    api.responses.append(httpx.Response(200, json={'data': CONNECTIONS}))
    page = done(api, 'c-mail', 'pending')
    assert page.status_code == 302
    assert page.headers['location'] == 'https://owui.invalid/auth'
    authorized = [request.url.path for request in api.requests if request.method == 'POST']
    assert authorized == ['/v1/connections/c-onedrive/authorize', '/v1/connections/c-mail/authorize']
    # A later login lists the connections and finds nothing left to try.
    api.responses.append(httpx.Response(200, json={'data': CONNECTIONS}))
    assert login(api) == 'https://owui.invalid/auth'
    assert [request.url.path for request in api.requests if request.method == 'POST'] == authorized


def test_a_failed_attempt_is_never_retried(api):
    """The connection is marked before the attempt, so a refusal cannot loop either."""
    api.responses.extend(
        [
            httpx.Response(200, json={'data': CONNECTIONS[3:4]}),
            httpx.Response(422, json={'code': 'invalid_field', 'detail': 'x', 'constraint': 'lifecycle'}),
            httpx.Response(200, json={'data': CONNECTIONS[3:4]}),
        ]
    )
    assert login(api) == 'https://owui.invalid/auth'
    assert login(api) == 'https://owui.invalid/auth'
    assert len([request for request in api.requests if request.method == 'POST']) == 1
    assert api.settings[silent_connect.SETTINGS_KEY]['inflight'] is None


def test_a_platform_outage_never_breaks_the_login(api, caplog):
    """Any failure keeps the normal destination and logs only a code."""
    api.responses.append(httpx.Response(503, json={'code': 'service_unavailable', 'detail': 'down'}))
    with caplog.at_level(logging.INFO):
        assert login(api) == 'https://owui.invalid/auth'
    assert 'invalid-test-runtime-key' not in caplog.text
    assert 'invalid-test-assertion' not in caplog.text


def test_a_local_failure_logs_its_type_and_line_never_its_message(api, caplog, monkeypatch):
    monkeypatch.setattr(silent_connect.CloudSync, 'connections', AsyncMock(side_effect=KeyError('private-detail')))
    with caplog.at_level(logging.INFO):
        assert login(api) == 'https://owui.invalid/auth'
    [record] = [record for record in caplog.records if record.getMessage().startswith('Silent reconnect skipped')]
    assert record.getMessage().startswith('Silent reconnect skipped: KeyError at ')
    assert 'private-detail' not in caplog.text


def test_other_login_providers_are_untouched(api):
    """Only an Entra login can finish Microsoft consent silently."""
    assert login(api, 'google') == 'https://owui.invalid/auth'
    assert not api.requests


def test_a_popup_after_the_chain_still_posts_and_closes(api):
    """The chain ends once; the manual reconnect that follows keeps its popup page."""
    queue_hop(api, 'c-onedrive')
    login(api)
    api.responses.append(httpx.Response(200, json={'data': []}))
    assert done(api, 'c-onedrive', 'interaction_required').status_code == 302
    page = done(api, 'c-onedrive', 'pending')
    assert page.status_code == 200
    assert 'window.close();' in page.text


@pytest.mark.parametrize('connection', ['c-other', 'c-onedrive'])
def test_only_a_fresh_return_of_the_attempt_continues_the_chain(api, monkeypatch, connection):
    """Another connection's popup, or a return after the attempt expired, is an ordinary popup."""
    queue_hop(api, 'c-onedrive')
    login(api)
    if connection == 'c-onedrive':
        now = silent_connect.time.time() + silent_connect.CHAIN_SECONDS
        monkeypatch.setattr(silent_connect.time, 'time', lambda: now)
    page = done(api, connection, 'pending')
    assert page.status_code == 200
    assert 'window.close();' in page.text


def test_no_capability_or_token_reaches_the_logs(api, caplog):
    """The start URL is a single-use capability; logs carry ids and results only."""
    with caplog.at_level(logging.DEBUG):
        queue_hop(api, 'c-onedrive')
        login(api)
        api.responses.append(httpx.Response(200, json={'data': []}))
        done(api, 'c-onedrive', 'pending')
    assert 'invalid-attempt-capability' not in caplog.text
    assert 'invalid-test-assertion' not in caplog.text
    assert 'invalid-test-runtime-key' not in caplog.text
    started = [record for record in caplog.records if record.getMessage() == 'Silent reconnect started']
    assert [record.connection_id for record in started] == ['c-onedrive']
