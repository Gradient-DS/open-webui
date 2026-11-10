"""Platform login uses the broker even with stale direct-provider credentials."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from open_webui import config
from open_webui.utils import oauth
from starlette.responses import RedirectResponse


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(config, 'OAUTH_PROVIDERS', {})
    monkeypatch.setattr(oauth, 'OAUTH_PROVIDERS', config.OAUTH_PROVIDERS)
    for provider in ('GOOGLE', 'GITHUB', 'FEISHU', 'MICROSOFT'):
        for suffix in ('CLIENT_ID', 'CLIENT_SECRET'):
            name = f'{provider}_{suffix}'
            monkeypatch.setenv(name, 'fictional-credential')
            monkeypatch.setattr(config, name, 'fictional-credential', raising=False)
    monkeypatch.setenv('MICROSOFT_CLIENT_TENANT_ID', 'aaaaaaaa-0000-4000-8000-000000000001')
    monkeypatch.setattr(config, 'MICROSOFT_CLIENT_TENANT_ID', 'aaaaaaaa-0000-4000-8000-000000000001', raising=False)
    monkeypatch.setattr(config, 'OAUTH_CLIENT_ID', 'owui-fictional')
    monkeypatch.setattr(config, 'OAUTH_CLIENT_SECRET', 'fictional-broker-secret')
    monkeypatch.setattr(
        config, 'OPENID_PROVIDER_URL', 'https://auth.example.invalid/realms/fictional/.well-known/openid-configuration'
    )
    monkeypatch.setattr(config, 'OAUTH_PROVIDER_NAME', 'soev')
    monkeypatch.setattr(
        oauth, 'get_oauth_runtime_config', AsyncMock(return_value=SimpleNamespace(ENABLE_OAUTH=True, OAUTH_AUDIENCE=''))
    )
    return config.OAUTH_PROVIDERS


@pytest.mark.parametrize('platform', [False, True])
def test_microsoft_is_never_registered(providers, monkeypatch, platform):
    monkeypatch.setenv('SOEV_API_URL', 'https://api.example.invalid' if platform else '')
    config.load_oauth_providers()
    manager = oauth.OAuthManager(SimpleNamespace())
    assert 'microsoft' not in providers
    assert manager.get_client('microsoft') is None
    if platform:
        assert set(providers) == {'oidc'}
        assert providers['oidc']['name'] == 'soev'
        assert manager.get_client('oidc') is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', [False, True])
async def test_direct_microsoft_login_and_callbacks_return_404(providers, monkeypatch, platform):
    main = importlib.import_module('open_webui.main')
    from open_webui.internal.db import get_async_session

    monkeypatch.setenv('SOEV_API_URL', 'https://api.example.invalid' if platform else '')
    config.load_oauth_providers()
    manager = oauth.OAuthManager(SimpleNamespace())
    monkeypatch.setattr(main, 'oauth_manager', manager)
    app = FastAPI()
    paths = {'/oauth/{provider}/login', '/oauth/{provider}/callback', '/oauth/{provider}/login/callback'}
    app.router.routes.extend(route for route in main.app.routes if getattr(route, 'path', None) in paths)
    assert {
        getattr(route, 'path', None) for route in app.router.routes if getattr(route, 'path', None) in paths
    } == paths
    app.dependency_overrides[get_async_session] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://owui.example.invalid') as client:
        blocked = ('microsoft', 'google', 'github', 'feishu') if platform else ('microsoft',)
        for provider in blocked:
            for suffix in ('login', 'callback', 'login/callback'):
                response = await client.get(f'/oauth/{provider}/{suffix}')
                assert response.status_code == 404
                assert 'location' not in response.headers
                assert 'set-cookie' not in response.headers
        if platform:
            broker_url = 'https://auth.example.invalid/realms/fictional/protocol/openid-connect/auth'
            redirect = AsyncMock(return_value=RedirectResponse(broker_url))
            monkeypatch.setattr(manager.get_client('oidc'), 'authorize_redirect', redirect)
            response = await client.get('/oauth/oidc/login')
            assert response.status_code == 307
            assert response.headers['location'] == broker_url
            redirect.assert_awaited_once()
    assert set(manager._clients).isdisjoint({'microsoft'})
