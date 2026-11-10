"""Proven Entra link requests at sign-in and failure isolation from login."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import jwt
import httpx
import pytest
from fastapi import HTTPException
from open_webui.constants import ERROR_MESSAGES
from open_webui.soev import login_attach
from starlette.responses import Response

PERSON = 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'


@pytest.fixture
def login(identity_config, monkeypatch):
    """Run the real OAuth callback with isolated provider and persistence boundaries."""
    from open_webui.utils import oauth

    identity, _ = identity_config
    user = identity.UserModel(
        id='alice',
        email='alice@example.invalid',
        name='Alice',
        role='user',
        oauth={'microsoft': {'sub': 'pairwise-sub-not-the-oid'}},
        person_id=PERSON,
        last_active_at=0,
        updated_at=0,
        created_at=0,
    )
    token = {
        'id_token': jwt.encode({'oid': 'test-entra-oid', 'sub': 'pairwise-sub-not-the-oid'}, key='', algorithm='none'),
        'access_token': 'invalid-test-access-token',
        'expires_in': 3600,
        'userinfo': {'sub': 'pairwise-sub-not-the-oid', 'email': user.email, 'name': user.name},
    }
    monkeypatch.setattr(oauth, 'OAUTH_PROVIDERS', {})
    manager = oauth.OAuthManager(app=Mock())
    for provider in ('microsoft', 'google'):
        oauth.OAUTH_PROVIDERS[provider] = {}
        manager._clients[provider] = SimpleNamespace(authorize_access_token=AsyncMock(return_value=token))
    auth_config = SimpleNamespace(
        ENABLE_OAUTH=True,
        OAUTH_ALLOWED_TENANTS=[],
        OAUTH_SUB_CLAIM='sub',
        OAUTH_EMAIL_CLAIM='email',
        OAUTH_USERNAME_CLAIM='name',
        OAUTH_PICTURE_CLAIM='',
        OAUTH_ALLOWED_DOMAINS=['*'],
        OAUTH_UPDATE_NAME_ON_LOGIN=False,
        OAUTH_UPDATE_EMAIL_ON_LOGIN=False,
        OAUTH_UPDATE_PICTURE_ON_LOGIN=False,
        OAUTH_INVITE_REQUIRED=False,
        ENABLE_OAUTH_SIGNUP=True,
        ENABLE_OAUTH_GROUP_MANAGEMENT=False,
        JWT_EXPIRES_IN='1h',
    )
    monkeypatch.setattr(oauth, 'get_oauth_runtime_config', AsyncMock(return_value=auth_config))
    monkeypatch.setattr(oauth, 'ENABLE_OAUTH_ID_TOKEN_COOKIE', False)
    monkeypatch.setattr(oauth.Invites, 'get_pending_invite_by_email', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth.Users, 'get_user_by_oauth_sub', AsyncMock(return_value=user))
    monkeypatch.setattr(oauth.Users, 'get_user_by_email', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth.Users, 'update_user_oauth_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(oauth.Users, 'get_num_users', AsyncMock(return_value=2))
    monkeypatch.setattr(oauth.Auths, 'insert_new_auth', AsyncMock(return_value=user))
    monkeypatch.setattr(oauth, 'insert_oauth_account', AsyncMock(return_value=user))
    monkeypatch.setattr(login_attach, '_attach_login', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth, 'get_password_hash', AsyncMock(return_value='invalid-test-password-hash'))
    monkeypatch.setattr(oauth, 'apply_default_group_assignment', AsyncMock())
    monkeypatch.setattr(oauth.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth, 'publish_event', AsyncMock())
    monkeypatch.setattr(oauth.OAuthSessions, 'get_sessions_by_user_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(oauth.OAuthSessions, 'create_session', AsyncMock(return_value=SimpleNamespace(id='session')))
    mint = Mock(wraps=oauth.create_token)
    monkeypatch.setattr(oauth, 'create_token', mint)
    # The silent reconnect hop has its own tests; here it keeps the login's own destination.
    silent = AsyncMock(side_effect=lambda user, provider, url: url)
    monkeypatch.setattr(oauth.silent_connect, 'after_login', silent)

    async def callback(provider='microsoft'):
        return await manager.handle_callback(SimpleNamespace(base_url='https://owui.invalid/'), provider, Response())

    return SimpleNamespace(user=user, token=token, oauth=oauth, callback=callback, mint=mint, silent=silent)


def assert_logged_in(response, login):
    assert response.status_code == 307
    assert response.headers['location'] == 'https://owui.invalid/auth'
    assert any(cookie.startswith('token=') for cookie in response.headers.getlist('set-cookie'))
    assert login.mint.call_args.kwargs['data'] == {'id': login.user.id}


def assert_only_person_discovery(requests, count):
    assert [(request.method, request.url.path) for request in requests] == [('GET', '/v1/me')] * count
    for request in requests:
        assert request.headers['Authorization'] == 'Bearer soev_test_cred-runtime_test-secret'
        assert request.headers['X-Soev-User-Token'] == 'invalid-test-access-token'
        assert 'X-Soev-' + 'Subject' not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('account', ['existing', 'new'])
async def test_microsoft_login_never_publishes_a_legacy_link(login, identity_http, monkeypatch, account):

    if account != 'existing':
        login.oauth.Users.get_user_by_oauth_sub.return_value = None
    identity_http[1].extend(httpx.Response(200, json={'person_id': PERSON}) for _ in range(2))
    for _ in range(2):
        assert_logged_in(await login.callback(), login)
    assert_only_person_discovery(identity_http[0], 2)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['password', 'other-provider', 'api-disabled'])
async def test_a_local_login_links_nothing(identity_config, login, identity_http, monkeypatch, mode):
    """Local login refuses; verified OAuth discovers a person without sending a legacy link."""
    identity, _ = identity_config
    discoveries = 0
    if mode == 'password':
        from open_webui.routers import auths

        login.user.oauth = None
        monkeypatch.setattr(auths, 'ENABLE_PASSWORD_AUTH', True)
        monkeypatch.setattr(auths, 'WEBUI_AUTH', True)
        monkeypatch.setattr(auths, 'WEBUI_AUTH_TRUSTED_EMAIL_HEADER', None)
        monkeypatch.setattr(auths.signin_rate_limiter, 'is_limited', Mock(return_value=False))
        monkeypatch.setattr(auths.Auths, 'authenticate_user', AsyncMock(return_value=login.user))
        monkeypatch.setattr(
            auths.Config, 'get', AsyncMock(side_effect=lambda key: '1h' if key == 'auth.jwt_expiry' else None)
        )
        monkeypatch.setattr(auths, 'get_permissions', AsyncMock(return_value={}))
        monkeypatch.setattr(auths, 'publish_event', AsyncMock())
        response = Response()
        with pytest.raises(HTTPException) as error:
            await auths.signin(
                SimpleNamespace(),
                response,
                auths.SigninForm(email=login.user.email, password='invalid-test-password'),
                db=None,
            )
        assert error.value.status_code == 403
        assert error.value.detail == ERROR_MESSAGES.ACCESS_PROHIBITED
        auths.Auths.authenticate_user.assert_not_awaited()
        login.mint.assert_not_called()
        assert response.headers.getlist('set-cookie') == []
    else:
        if mode == 'api-disabled':
            monkeypatch.setattr(identity.config, 'SOEV_API_URL', '')
        else:
            identity_http[1].append(httpx.Response(200, json={'person_id': PERSON}))
            discoveries = 1
        assert_logged_in(await login.callback('google' if mode == 'other-provider' else 'microsoft'), login)
    assert_only_person_discovery(identity_http[0], discoveries)


@pytest.mark.asyncio
async def test_a_login_can_go_through_a_silent_reconnect_first(login, identity_http):
    """The callback hands its destination to the silent reconnect and still sets the session cookie."""
    start = 'https://soev.invalid/oauth/start/invalid-attempt-capability'
    login.silent.side_effect = lambda user, provider, url: start
    identity_http[1].append(httpx.Response(200, json={'person_id': PERSON}))
    response = await login.callback()
    assert response.headers['location'] == start
    assert any(cookie.startswith('token=') for cookie in response.headers.getlist('set-cookie'))
    login.silent.assert_awaited_once_with(login.user, 'microsoft', 'https://owui.invalid/auth')
    assert_only_person_discovery(identity_http[0], 1)
