"""Cloud-sync input cannot select OWUI's outbound origin or redirect its credential."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui import config
from open_webui.routers import cloud_sync
from open_webui.soev.client import SoevClient, close_client

HOSTILE = (
    'http://169.254.169.254/latest/meta-data/',
    'http://stub:8000/record',
    '//stub:8000/record',
    'https://trusted.invalid@127.0.0.1/',
    'http://[::1]/',
    '../../connections/foreign?tenant_id=other#fragment',
)


@pytest.fixture
def boundary(monkeypatch, platform_capabilities):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.invalid')
    sent, replies = [], []

    def handle(request):
        sent.append(request)
        assert request.url.scheme == 'https'
        assert request.url.host == 'platform.invalid'
        assert request.headers['authorization'] == 'Bearer ci-key'
        assert request.headers['x-soev-subject'] == 'assertion:owui:user:owner'
        return replies.pop(0)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = SoevClient('https://platform.invalid', 'ci-key', subject_minter=lambda ref: f'assertion:{ref}')
    monkeypatch.setattr(cloud_sync.identity, 'build_client', lambda: client)
    monkeypatch.setattr(cloud_sync.identity, 'ensure_link', AsyncMock())
    app = FastAPI()
    app.include_router(cloud_sync.router, prefix='/api/v1/cloud-sync')
    app.dependency_overrides[cloud_sync.get_verified_user] = lambda: SimpleNamespace(
        id='owner', role='user', email='owner@example.com'
    )
    with TestClient(app) as browser:
        browser.portal.call(close_client)
        yield SimpleNamespace(browser=browser, sent=sent, replies=replies)
        browser.portal.call(close_client)
    assert not replies


@pytest.mark.parametrize('value', HOSTILE)
@pytest.mark.parametrize('field', ['url', 'host', 'tenant_id', 'authorize_url'])
def test_connection_creation_rejects_caller_selected_destinations(boundary, field, value):
    answer = boundary.browser.post('/api/v1/cloud-sync/connections', json={'provider': 'onedrive', field: value})
    assert answer.status_code == 422
    assert not boundary.sent


@pytest.mark.parametrize('value', HOSTILE)
def test_provider_cannot_be_a_url_or_host(boundary, value):
    answer = boundary.browser.post('/api/v1/cloud-sync/connections', json={'provider': value})
    assert answer.status_code == 422
    assert not boundary.sent


@pytest.mark.parametrize('value', HOSTILE)
def test_schedule_scope_is_data_sent_only_to_the_configured_platform(boundary, value):
    body = {'connection_id': value, 'kind': 'content', 'scope': {'url': value, 'host': value, 'tenant_id': value}}
    boundary.replies.append(httpx.Response(422, json={'detail': 'invalid scope'}))
    answer = boundary.browser.post('/api/v1/cloud-sync/knowledge/owned/schedules', json=body)
    assert answer.status_code == 422
    assert len(boundary.sent) == 1
    request = boundary.sent[0]
    assert request.url.path == '/v1/schedules'
    assert json.loads(request.content) == {**body, 'collection_key': 'owned'}


@pytest.mark.parametrize('value', HOSTILE)
def test_connection_ids_cannot_replace_the_platform_origin(boundary, value):
    # Exercise the service directly so encoded slashes cannot be rejected by
    # Starlette before reaching the outbound URL construction under test.
    boundary.replies.append(httpx.Response(200, json={'id': value}))
    sync = cloud_sync.CloudSync(cloud_sync.identity.build_client(), 'owui:user:owner')
    answer = boundary.browser.portal.call(sync.connection, value)
    assert answer == {'id': value}
    assert boundary.sent[0].url.raw_path == f'/v1/connections/{quote(value, safe="")}'.encode()


@pytest.mark.parametrize('destination', HOSTILE[:5])
def test_platform_redirect_is_not_followed_with_the_api_credential(boundary, destination):
    boundary.replies.append(httpx.Response(302, headers={'Location': destination}))
    answer = boundary.browser.get('/api/v1/cloud-sync/connections/owned')
    assert answer.status_code == 302
    assert len(boundary.sent) == 1


def test_pagination_cursor_is_a_query_value_not_a_fetch_url(boundary):
    cursor = 'http://169.254.169.254/latest/meta-data/?tenant_id=foreign'
    boundary.replies.extend(
        [httpx.Response(200, json={'data': [], 'next_cursor': cursor}), httpx.Response(200, json={'data': []})]
    )
    assert boundary.browser.get('/api/v1/cloud-sync/connections').status_code == 200
    assert [request.url.path for request in boundary.sent] == ['/v1/connections', '/v1/connections']
    assert boundary.sent[1].url.params['cursor'] == cursor


@pytest.mark.parametrize('value', HOSTILE)
def test_popup_completion_does_not_fetch_the_connection_value(boundary, value):
    answer = boundary.browser.get('/api/v1/cloud-sync/connect/done', params={'connection': value, 'result': 'error'})
    assert answer.status_code == 200
    assert not boundary.sent
