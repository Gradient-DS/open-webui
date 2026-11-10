"""Platform mode cannot issue an OWUI session through OAuth token exchange."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.routers import auths


@pytest_asyncio.fixture
async def exchange_app(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(auths, 'ENABLE_OAUTH_TOKEN_EXCHANGE', True)
    monkeypatch.setattr(auths, 'OAUTH_PROVIDERS', {'oidc': {}})
    client = SimpleNamespace(userinfo=AsyncMock())
    manager = SimpleNamespace(get_client=Mock(return_value=client))
    limiter = Mock()
    limiter.is_limited.return_value = False
    monkeypatch.setattr(auths, 'token_exchange_rate_limiter', limiter)
    session = AsyncMock()
    lookup = AsyncMock()
    read_config = AsyncMock(return_value='email')
    introspect = AsyncMock()
    monkeypatch.setattr(auths, 'create_session_response', session)
    monkeypatch.setattr(auths.Users, 'get_user_by_oauth_sub', lookup)
    monkeypatch.setattr(auths.Config, 'get', read_config)
    monkeypatch.setattr(auths, 'get_token_client_id', introspect)
    app = FastAPI()
    app.include_router(auths.router, prefix='/api/v1/auths')
    app.state.oauth_manager = manager
    app.dependency_overrides[auths.get_async_session] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://chat.example.invalid'
    ) as http:
        yield SimpleNamespace(
            http=http,
            manager=manager,
            client=client,
            limiter=limiter,
            session=session,
            lookup=lookup,
            read_config=read_config,
            introspect=introspect,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('provider', ['oidc', 'unconfigured-provider'])
@pytest.mark.parametrize('authorization', [None, 'Bearer fictional-owui-session'])
async def test_platform_refuses_exchange_before_any_identity_or_session_work(
    exchange_app, monkeypatch, caplog, enabled, provider, authorization
):
    ctx = exchange_app
    monkeypatch.setattr(auths, 'ENABLE_OAUTH_TOKEN_EXCHANGE', enabled)
    ctx.limiter.is_limited.return_value = True
    headers = {'Authorization': authorization} if authorization else {}

    response = await ctx.http.post(
        f'/api/v1/auths/oauth/{provider}/token/exchange',
        json={'token': 'fictional-external-access-token'},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json() == {'detail': ERROR_MESSAGES.ACCESS_PROHIBITED}
    assert response.headers.get_list('set-cookie') == []
    ctx.session.assert_not_awaited()
    ctx.lookup.assert_not_awaited()
    ctx.read_config.assert_not_awaited()
    ctx.introspect.assert_not_awaited()
    ctx.manager.get_client.assert_not_called()
    ctx.client.userinfo.assert_not_awaited()
    ctx.limiter.is_limited.assert_not_called()
    records = [r for r in caplog.records if getattr(r, 'code', None) == 'oauth_platform_token_exchange_refused']
    assert len(records) == 1
    assert 'fictional-external-access-token' not in caplog.text + repr(vars(records[0]))


@pytest.mark.asyncio
async def test_standalone_exchange_keeps_existing_disabled_guard(exchange_app, monkeypatch):
    ctx = exchange_app
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    monkeypatch.setattr(auths, 'ENABLE_OAUTH_TOKEN_EXCHANGE', False)
    response = await ctx.http.post('/api/v1/auths/oauth/oidc/token/exchange', json={'token': 'fictional-access-token'})
    assert response.status_code == 403
    assert response.json() == {'detail': 'Token exchange is disabled'}
    ctx.session.assert_not_awaited()
    ctx.manager.get_client.assert_not_called()
