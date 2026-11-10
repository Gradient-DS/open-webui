"""Platform password routes refuse before reading accounts or recovery tokens."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from open_webui import config, env
from open_webui.constants import ERROR_MESSAGES
from open_webui.routers import auths
from open_webui.soev.login_attach import platform_mode
from open_webui.soev.policy_jobs import v2_enabled


@pytest_asyncio.fixture
async def password_app(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(auths, 'ENABLE_PASSWORD_AUTH', True)
    monkeypatch.setattr(auths, 'WEBUI_AUTH_TRUSTED_EMAIL_HEADER', None)
    calls = []
    for owner, name in (
        (auths.Users, 'get_user_by_email'),
        (auths.Auths, 'authenticate_user'),
        (auths.Auths, 'get_auth_by_user_id'),
        (auths.Auths, 'update_user_password_by_id'),
        (auths.PasswordResetTokens, 'get_by_token_hash'),
        (auths.PasswordResetTokens, 'create'),
        (auths.PasswordResetTokens, 'mark_used'),
        (auths.Config, 'get'),
        (auths, 'create_session_response'),
    ):
        mock = AsyncMock()
        monkeypatch.setattr(owner, name, mock)
        calls.append(mock)
    mint = Mock()
    monkeypatch.setattr(auths, 'create_token', mint)
    app = FastAPI()
    app.include_router(auths.router, prefix='/api/v1/auths')
    app.dependency_overrides[auths.get_async_session] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://chat.example.invalid'
    ) as http:
        yield SimpleNamespace(http=http, calls=calls, mint=mint)


@pytest.mark.asyncio
@pytest.mark.parametrize('agent_enabled', [False, True])
@pytest.mark.parametrize(
    'method,path,body,code',
    [
        ('POST', '/signin', {'email': 'fern@example.invalid', 'password': 'fictional-password'}, 'password_signin'),
        ('POST', '/password/forgot', {'email': 'fern@example.invalid'}, 'password_recovery'),
        (
            'POST',
            '/password/reset',
            {'token': 'fictional-reset', 'new_password': 'fictional-password'},
            'password_reset',
        ),
        ('GET', '/password/reset/fictional-reset/validate', None, 'password_reset'),
    ],
)
async def test_platform_password_paths_refuse_before_account_work(
    password_app, monkeypatch, caplog, agent_enabled, method, path, body, code
):
    ctx = password_app
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', agent_enabled)
    response = await ctx.http.request(method, f'/api/v1/auths{path}', **({'json': body} if body else {}))
    assert response.status_code == 403
    assert response.json() == {'detail': ERROR_MESSAGES.ACCESS_PROHIBITED}
    assert response.headers.get_list('set-cookie') == []
    for call in ctx.calls:
        call.assert_not_awaited()
    ctx.mint.assert_not_called()
    record = next(r for r in caplog.records if getattr(r, 'code', None) == f'oauth_platform_{code}_refused')
    assert 'fern@example.invalid' not in repr(vars(record))


@pytest.mark.parametrize(
    'url,agent,platform,v2',
    [
        ('', False, False, False),
        ('', True, False, False),
        ('https://platform.example.invalid', False, True, False),
        ('https://platform.example.invalid', True, True, True),
    ],
)
def test_login_predicate_is_stricter_than_agent_routing(monkeypatch, url, agent, platform, v2):
    monkeypatch.setattr(config, 'SOEV_API_URL', url)
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', agent)
    assert platform_mode() is platform
    assert v2_enabled() is v2
