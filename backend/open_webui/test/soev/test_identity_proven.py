"""Proven Entra link requests at sign-in and failure isolation from login."""

import base64
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import jwt
import pytest
from starlette.responses import Response


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
        OAUTH_MERGE_ACCOUNTS_BY_EMAIL=True,
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
    monkeypatch.setattr(oauth, 'get_password_hash', AsyncMock(return_value='invalid-test-password-hash'))
    monkeypatch.setattr(oauth, 'apply_default_group_assignment', AsyncMock())
    monkeypatch.setattr(manager, 'get_user_role', AsyncMock(return_value='user'))
    monkeypatch.setattr(manager, 'update_user_role_from_oauth', AsyncMock(return_value=user))
    monkeypatch.setattr(oauth.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth, 'publish_event', AsyncMock())
    monkeypatch.setattr(oauth.OAuthSessions, 'get_sessions_by_user_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(oauth.OAuthSessions, 'create_session', AsyncMock(return_value=SimpleNamespace(id='session')))
    mint = Mock(wraps=oauth.create_token)
    monkeypatch.setattr(oauth, 'create_token', mint)

    async def callback(provider='microsoft'):
        return await manager.handle_callback(SimpleNamespace(base_url='https://owui.invalid/'), provider, Response())

    return SimpleNamespace(user=user, token=token, oauth=oauth, callback=callback, mint=mint)


def assert_logged_in(response, login):
    assert response.status_code == 307
    assert response.headers['location'] == 'https://owui.invalid/auth'
    assert any(cookie.startswith('token=') for cookie in response.headers.getlist('set-cookie'))
    assert login.mint.call_args.kwargs['data'] == {'id': login.user.id}


@pytest.mark.asyncio
@pytest.mark.parametrize('account', ['existing', 'merged', 'new'])
async def test_a_microsoft_login_links_the_entra_oid_as_proven(identity_config, login, identity_http, account):
    """Every Microsoft sign-in sends its oid proof before minting the OWUI session."""
    identity, key = identity_config
    requests, responses = identity_http
    if account != 'existing':
        login.oauth.Users.get_user_by_oauth_sub.return_value = None
    if account == 'merged':
        login.oauth.Users.get_user_by_email.return_value = login.user

    async def accept(request):
        assert login.mint.call_count == len(requests) - 1
        body = json.loads(request.content)
        assert set(body) == {'platform_user_id', 'assertion', 'id_token'}
        assert body['platform_user_id'] == identity.platform_user_id('owui:user:alice')
        assert body['id_token'] == login.token['id_token']
        claims = jwt.decode(body['assertion'], options={'verify_signature': False})
        assert claims['sub'] == 'entra:user:test-entra-oid'
        assert claims['iss'] == 'runtime-credential'
        assert claims['aud'] == 'test-tenant'
        header, payload, signature = body['assertion'].split('.')
        key.public_key().verify(base64.urlsafe_b64decode(signature + '=='), f'{header}.{payload}'.encode())
        assert request.method == 'POST'
        assert request.url.path == '/v1/identity/links'
        assert request.headers['Authorization'] == 'Bearer test-runtime-key'
        assert 'X-Soev-Subject' not in request.headers
        return httpx.Response(204)

    responses.extend([accept, accept])
    for _ in range(2):
        assert_logged_in(await login.callback(), login)
    assert len(requests) == 2
    assert requests[0].headers['Idempotency-Key'] != requests[1].headers['Idempotency-Key']
    assert json.loads(requests[0].content)['assertion'] != json.loads(requests[1].content)['assertion']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['password', 'other-provider', 'api-disabled'])
async def test_a_local_login_links_nothing(identity_config, login, identity_http, monkeypatch, mode):
    """Password sign-in, other providers and disabled soev-api never send a proven link."""
    identity, _ = identity_config
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
        result = await auths.signin(
            SimpleNamespace(),
            response,
            auths.SigninForm(email=login.user.email, password='invalid-test-password'),
            db=None,
        )
        assert result['id'] == login.user.id
        assert result['token']
        assert 'token=' in response.headers['set-cookie']
    else:
        if mode == 'api-disabled':
            monkeypatch.setattr(identity.config, 'SOEV_API_URL', '')
        assert_logged_in(await login.callback('google' if mode == 'other-provider' else 'microsoft'), login)
    assert identity_http[0] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['api', 'transport', 'signer', 'missing-token', 'malformed-token', 'missing-oid'])
async def test_a_link_failure_never_blocks_login(identity_config, login, identity_http, monkeypatch, caplog, failure):
    """API, transport, configuration and token failures still produce a login cookie."""
    identity, _ = identity_config
    _, responses = identity_http
    code = 'invalid_id_token'
    if failure == 'api':
        code = 'proof_rejected'
        responses.append(
            httpx.Response(
                403, json={'code': code, 'detail': 'Rejected'}, headers={'Content-Type': 'application/problem+json'}
            )
        )
    elif failure == 'transport':
        code = 'upstream_error'

        async def unavailable(request):
            raise httpx.ConnectError('Unavailable', request=request)

        responses.append(unavailable)
    elif failure == 'signer':
        code = 'link_failed'
        monkeypatch.setattr(identity, 'mint_assertion', Mock(side_effect=ValueError('Invalid signing configuration')))
    elif failure == 'missing-token':
        login.token.pop('id_token')
    elif failure == 'malformed-token':
        login.token['id_token'] = 'invalid-test-id-token'
    else:
        login.token['id_token'] = jwt.encode({'sub': 'not-an-oid'}, key='', algorithm='none')
    with caplog.at_level(logging.WARNING):
        assert_logged_in(await login.callback(), login)
    record = next(record for record in caplog.records if record.name == identity.__name__)
    assert record.user_id == login.user.id
    assert record.code == code
    assert record.exc_info is None


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'api-error', 'unexpected-error'])
async def test_the_id_token_never_reaches_a_log_record(identity_config, login, identity_http, caplog, outcome):
    """Neither successful links nor errors echoing credentials expose tokens or assertions."""
    identity, _ = identity_config
    requests, responses = identity_http

    async def reply(request):
        body = json.loads(request.content)
        detail = f'{body["id_token"]} {body["assertion"]} {login.token["access_token"]}'
        if outcome == 'unexpected-error':
            raise RuntimeError(detail)
        if outcome == 'api-error':
            return httpx.Response(
                403,
                json={'code': 'proof_rejected', 'detail': detail},
                headers={'Content-Type': 'application/problem+json'},
            )
        return httpx.Response(204)

    responses.append(reply)
    with caplog.at_level(logging.DEBUG):
        assert_logged_in(await login.callback(), login)
    exposed = caplog.text + repr([vars(record) for record in caplog.records])
    for secret in (
        login.token['id_token'],
        login.token['access_token'],
        json.loads(requests[0].content)['assertion'],
        identity.config.SOEV_API_KEY,
        identity.config.SOEV_API_SIGNING_KEY,
    ):
        assert secret not in exposed
