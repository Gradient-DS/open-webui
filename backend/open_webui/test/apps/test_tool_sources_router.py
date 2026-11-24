"""[Gradient] External connections are discovered as the verified caller, never as a service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.models.config import Config
from open_webui.routers import meetings, tool_sources
from open_webui.soev.client import SoevApiError
from open_webui.utils.auth import get_verified_user

pytestmark = pytest.mark.usefixtures('platform_capabilities')
SOURCE = {
    'id': '00000000-0000-4000-8000-000000000001',
    'kind': 'openapi',
    'name': 'Fable weather',
    'group_name': 'Fable',
    'countries': ['NL'],
}


def client(monkeypatch, *, payload=None, error=None, enabled=True, url='http://soev-api', default='soev_react'):
    from open_webui import config
    from open_webui.main import soev_not_configured_handler
    from open_webui.soev import identity
    from open_webui.soev.availability import SoevNotConfigured

    fake = SimpleNamespace(get=AsyncMock(return_value=payload or {'data': []}, side_effect=error))
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=default))
    monkeypatch.setattr(meetings, 'feature_enabled', AsyncMock(return_value=enabled))
    monkeypatch.setattr(config, 'SOEV_API_URL', url)
    monkeypatch.setattr(identity, 'build_client', lambda **kwargs: fake)
    monkeypatch.setattr(identity, 'external_ref', lambda user: f'owui:user:{user.id}')
    app = FastAPI(exception_handlers={SoevNotConfigured: soev_not_configured_handler})
    app.include_router(tool_sources.router, prefix='/api/v1/tool-sources')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-reader', role='user')
    return TestClient(app, raise_server_exceptions=False), fake


@pytest.mark.parametrize('agent', [None, 'alternate'])
def test_forwards_as_caller_and_preserves_shape(monkeypatch, agent):
    payload = {'data': [SOURCE, {**SOURCE, 'id': '00000000-0000-4000-8000-000000000002', 'kind': 'mcp'}]}
    http, fake = client(monkeypatch, payload=payload)
    response = http.get('/api/v1/tool-sources', params={'agent': agent} if agent else {})
    assert response.status_code == 200
    assert response.json() == payload
    fake.get.assert_awaited_once_with(
        f'/v1/agents/{agent or "soev_react"}/tool-sources', as_user='owui:user:fictional-reader'
    )


def test_empty_and_no_configured_picker(monkeypatch):
    http, fake = client(monkeypatch)
    assert http.get('/api/v1/tool-sources').json() == {'data': []}
    http, fake = client(monkeypatch, default='')
    assert http.get('/api/v1/tool-sources').json() == {'data': []}
    fake.get.assert_not_awaited()


@pytest.mark.parametrize('status', [401, 403, 404, 422, 503])
def test_maps_platform_refusals(monkeypatch, status):
    http, _ = client(monkeypatch, error=SoevApiError(status, 'refused', 'Unavailable', retry_after='3'))
    response = http.get('/api/v1/tool-sources')
    assert response.status_code == status
    assert response.json()['detail'] == {'code': 'refused', 'detail': 'Unavailable'}
    assert response.headers['retry-after'] == '3'


@pytest.mark.parametrize(
    'payload',
    [
        {'data': {}},
        {'data': [{'id': 'bad'}]},
        {'data': [{**SOURCE, 'countries': 'NL'}]},
        {'data': [{**SOURCE, 'id': 'bad'}]},
        {'data': [{**SOURCE, 'secret': 'never expose'}]},
    ],
)
def test_rejects_invalid_platform_shapes(monkeypatch, payload):
    http, _ = client(monkeypatch, payload=payload)
    response = http.get('/api/v1/tool-sources')
    assert response.status_code == 502
    assert 'never expose' not in response.text


@pytest.mark.parametrize('params', [{'agent': '../other'}, {'agent': ''}, {'agent': 'x/y'}])
def test_agent_path_is_bounded(monkeypatch, params):
    http, fake = client(monkeypatch)
    assert http.get('/api/v1/tool-sources', params=params).status_code == 422
    fake.get.assert_not_awaited()


def test_anonymous_is_refused_before_discovery(monkeypatch):
    http, fake = client(monkeypatch)
    http.app.dependency_overrides.clear()
    assert http.get('/api/v1/tool-sources').status_code in (401, 403)
    fake.get.assert_not_awaited()


def test_same_capability_and_configuration_gates(monkeypatch, platform_capabilities):
    http, fake = client(monkeypatch, enabled=False)
    assert http.get('/api/v1/tool-sources').status_code == 403
    http, fake = client(monkeypatch, url='')
    assert http.get('/api/v1/tool-sources').status_code == 503
    platform_capabilities.discard('meetings')
    http, fake = client(monkeypatch)
    assert http.get('/api/v1/tool-sources').status_code == 503
    fake.get.assert_not_awaited()
