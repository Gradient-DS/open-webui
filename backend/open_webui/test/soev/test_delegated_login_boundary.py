"""Platform identity cannot enter through LDAP, trusted headers or local signup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from open_webui import config, env
from open_webui.constants import ERROR_MESSAGES
from open_webui.routers import auths


@pytest_asyncio.fixture
async def delegated_app(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(auths, 'ENABLE_PASSWORD_AUTH', True)
    monkeypatch.setattr(auths, 'WEBUI_AUTH_TRUSTED_EMAIL_HEADER', 'X-Fictional-Email')
    calls = []
    for owner, name in (
        (auths.Config, 'get'),
        (auths.Users, 'get_user_by_email'),
        (auths.Users, 'has_users'),
        (auths.Auths, 'authenticate_user_by_email'),
        (auths.Auths, 'authenticate_user'),
        (auths.Auths, 'insert_new_auth'),
        (auths, 'get_password_hash'),
        (auths, 'create_session_response'),
    ):
        call = AsyncMock()
        monkeypatch.setattr(owner, name, call)
        calls.append(call)
    server, connection = Mock(), Mock()
    monkeypatch.setattr(auths, 'Server', server)
    monkeypatch.setattr(auths, 'Connection', connection)
    app = FastAPI()
    app.include_router(auths.router, prefix='/api/v1/auths')
    app.dependency_overrides[auths.get_async_session] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://chat.example.invalid'
    ) as http:
        yield SimpleNamespace(http=http, calls=calls, server=server, connection=connection)


@pytest.mark.asyncio
@pytest.mark.parametrize('agent_enabled', [False, True])
@pytest.mark.parametrize(
    'path,body,header,code',
    [
        ('/ldap', {'user': 'fern', 'password': 'fictional-password'}, False, 'ldap'),
        ('/signin', {'email': 'ignored@example.invalid', 'password': ''}, True, 'trusted_header'),
        ('/signin', {'email': 'ignored@example.invalid', 'password': ''}, False, 'trusted_header'),
        (
            '/signup',
            {'email': 'fern@example.invalid', 'password': 'fictional-password', 'name': 'Fern'},
            False,
            'local_signup',
        ),
    ],
)
async def test_platform_delegated_paths_refuse_before_lookup_or_ldap_bind(
    delegated_app, monkeypatch, caplog, agent_enabled, path, body, header, code
):
    ctx = delegated_app
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', agent_enabled)
    response = await ctx.http.post(
        '/api/v1/auths' + path, json=body, headers={'X-Fictional-Email': 'fern@example.invalid'} if header else {}
    )
    assert response.status_code == 403
    assert response.json() == {'detail': ERROR_MESSAGES.ACCESS_PROHIBITED}
    assert response.headers.get_list('set-cookie') == []
    for call in ctx.calls:
        call.assert_not_awaited()
    ctx.server.assert_not_called()
    ctx.connection.assert_not_called()
    assert any(getattr(r, 'code', None) == f'oauth_platform_{code}_refused' for r in caplog.records)


@pytest.mark.asyncio
async def test_platform_no_auth_mode_cannot_issue_a_session(delegated_app, monkeypatch):
    ctx = delegated_app
    monkeypatch.setattr(auths, 'WEBUI_AUTH_TRUSTED_EMAIL_HEADER', None)
    monkeypatch.setattr(auths, 'WEBUI_AUTH', False)
    response = await ctx.http.post('/api/v1/auths/signin', json={'email': '', 'password': ''})
    assert response.status_code == 403
    for call in ctx.calls:
        call.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_signup_handler_is_guarded_independently(delegated_app):
    with pytest.raises(HTTPException) as error:
        await auths.signup_handler(None, 'fern@example.invalid', 'fictional-password', 'Fern', db=None)
    assert error.value.status_code == 403
    for call in delegated_app.calls:
        call.assert_not_awaited()
