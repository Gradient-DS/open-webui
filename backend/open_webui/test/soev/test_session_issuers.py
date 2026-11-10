"""Alternative login issuers refuse platform mode before account or session writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from open_webui import config, env
from open_webui.internal.db import get_async_session
from open_webui.routers import auths, invites, totp
from open_webui.utils import auth


@pytest_asyncio.fixture
async def issuers(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    monkeypatch.setattr(auth, 'SESSION_SECRET', 'fictional-session-issuers-secret-key')
    user = SimpleNamespace(id='fern', role='admin', oauth={})
    read = AsyncMock(return_value=user)
    write = AsyncMock()
    mint = Mock()
    session = AsyncMock(return_value={'token': 'fictional-session'})
    monkeypatch.setattr(auths.Users, 'get_user_by_email', read)
    monkeypatch.setattr(auths.Users, 'get_user_by_id', read)
    monkeypatch.setattr(invites.Invites, 'get_invite_by_token', read)
    monkeypatch.setattr(auths.Auths, 'insert_new_auth', write)
    monkeypatch.setattr(totp.Auths, 'get_auth_by_user_id', read)
    monkeypatch.setattr(totp.Auths, 'update_totp', write)
    monkeypatch.setattr(totp.Auths, 'update_totp_last_used', write)
    monkeypatch.setattr(totp.RecoveryCodes, 'generate_codes', write)
    monkeypatch.setattr(totp.RecoveryCodes, 'verify_code', write)
    monkeypatch.setattr(invites.Invites, 'accept_invite', write)
    monkeypatch.setattr(auths, 'create_token', mint)
    monkeypatch.setattr(invites, 'create_token', mint)
    monkeypatch.setattr(totp, 'create_session_response', session)
    monkeypatch.setattr(totp, 'totp_verify_rate_limiter', SimpleNamespace(is_limited=Mock(return_value=False)))
    app = FastAPI()
    app.state.redis = None
    app.dependency_overrides[auth.get_admin_user] = lambda: user
    app.dependency_overrides[get_async_session] = lambda: None
    app.include_router(auths.router, prefix='/api/v1/auths')
    app.include_router(totp.router, prefix='/api/v1/auths/2fa')
    app.include_router(invites.router, prefix='/api/v1/invites')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://owui.invalid') as client:
        yield SimpleNamespace(app=app, client=client, user=user, read=read, write=write, mint=mint, session=session)


@pytest.mark.asyncio
@pytest.mark.parametrize('issuer', ['invite', 'add', 'verify', 'recovery', 'enable'])
async def test_platform_refuses_issuers_before_lookup_or_write(issuers, caplog, issuer):
    if issuer == 'invite':
        response = await issuers.client.post('/api/v1/invites/fictional-invite/accept', json={'password': 'Fable-123!'})
        code = 'invite_acceptance'
    elif issuer == 'add':
        response = await issuers.client.post(
            '/api/v1/auths/add', json={'email': 'fern@example.invalid', 'password': 'Fable-123!', 'name': 'Fern'}
        )
        code = 'add_user'
    else:
        purpose = '2fa_setup_pending' if issuer == 'enable' else '2fa_pending'
        token = auth.create_token({'id': 'fern', 'purpose': purpose})
        path = '/api/v1/auths/2fa/totp/enable' if issuer == 'enable' else '/api/v1/auths/2fa/verify'
        body = {'password': 'Fable-123!', 'secret': 'FABLESECRET', 'code': '123456'}
        if issuer == 'recovery':
            body['code'] = 'ABCDE-FGHIJ'
        response = await issuers.client.post(path, json=body, headers={'Authorization': f'Bearer {token}'})
        code = '2fa_setup' if issuer == 'enable' else '2fa_verify'
    assert response.status_code == 403, response.text
    assert 'set-cookie' not in response.headers
    assert any(getattr(record, 'code', None) == f'oauth_platform_{code}_refused' for record in caplog.records)
    issuers.read.assert_not_awaited()
    issuers.write.assert_not_awaited()
    issuers.mint.assert_not_called()
    issuers.session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['setup', 'enable', 'verify'])
@pytest.mark.parametrize('revocation', ['token', 'user'])
async def test_standalone_partial_tokens_obey_revocation(issuers, monkeypatch, endpoint, revocation):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    purpose = '2fa_pending' if endpoint == 'verify' else '2fa_setup_pending'
    token = auth.create_token({'id': 'fern', 'purpose': purpose})
    claims = auth.decode_token(token)
    revoked_key = (
        f'{auth.REDIS_KEY_PREFIX}:auth:token:{claims["jti"]}:revoked'
        if revocation == 'token'
        else f'{auth.REDIS_KEY_PREFIX}:auth:user:fern:revoked_at'
    )
    issuers.app.state.redis = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: str(claims['iat']) if key == revoked_key else None)
    )
    path = '/api/v1/auths/2fa/verify' if endpoint == 'verify' else f'/api/v1/auths/2fa/totp/{endpoint}'
    body = {'password': 'Fable-123!', 'secret': 'FABLESECRET', 'code': '123456'}
    response = await issuers.client.post(path, json=body, headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401, response.text
    issuers.read.assert_not_awaited()
    issuers.write.assert_not_awaited()
    issuers.session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['123456', 'ABCDE-FGHIJ'])
async def test_standalone_2fa_still_completes_login(issuers, monkeypatch, code):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    token = auth.create_token({'id': 'fern', 'purpose': '2fa_pending'})
    monkeypatch.setattr(
        totp.Auths,
        'get_auth_by_user_id',
        AsyncMock(return_value=SimpleNamespace(totp_enabled=True, totp_secret='encrypted', totp_last_used_at=None)),
    )
    monkeypatch.setattr(totp, 'decrypt_secret', lambda _: 'FABLESECRET')
    monkeypatch.setattr(totp, 'verify_totp', lambda *args: (True, 42))
    issuers.write.return_value = True
    response = await issuers.client.post(
        '/api/v1/auths/2fa/verify', json={'code': code}, headers={'Authorization': f'Bearer {token}'}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {'token': 'fictional-session'}
    issuers.session.assert_awaited_once()


@pytest.mark.asyncio
async def test_standalone_setup_token_still_enables_and_issues_session(issuers, monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    token = auth.create_token({'id': 'fern', 'purpose': '2fa_setup_pending'})
    monkeypatch.setattr(totp.Config, 'get', AsyncMock(return_value=True))
    monkeypatch.setattr(
        totp.Auths,
        'get_auth_by_user_id',
        AsyncMock(return_value=SimpleNamespace(totp_enabled=False, password='fictional-hash')),
    )
    monkeypatch.setattr(totp, 'verify_password', lambda *args: True)
    monkeypatch.setattr(totp, 'verify_totp', lambda *args: (True, 42))
    monkeypatch.setattr(totp, 'encrypt_secret', lambda _: 'encrypted')
    issuers.write.return_value = ['ABCDE-FGHIJ']
    response = await issuers.client.post(
        '/api/v1/auths/2fa/totp/enable',
        json={'password': 'Fable-123!', 'secret': 'FABLESECRET', 'code': '123456'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert response.status_code == 200, response.text
    assert response.json()['token'] == 'fictional-session'
    issuers.session.assert_awaited_once()


@pytest.mark.asyncio
async def test_platform_full_session_cannot_enable_local_totp(issuers, caplog):
    issuers.user.person_id = 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'
    token = auth.create_token({'id': 'fern'})
    response = await issuers.client.post(
        '/api/v1/auths/2fa/totp/enable',
        json={'password': 'Fable-123!', 'secret': 'FABLESECRET', 'code': '123456'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert response.status_code == 403, response.text
    assert any(getattr(r, 'code', None) == 'oauth_platform_2fa_setup_refused' for r in caplog.records)
    issuers.read.assert_awaited_once_with('fern')
    issuers.write.assert_not_awaited()
    issuers.session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('issuer', ['invite', 'add'])
async def test_standalone_local_account_issuers_keep_working(issuers, monkeypatch, issuer):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    user = SimpleNamespace(
        id='lark', name='Lark', email='lark@example.invalid', role='user', profile_image_url='/fictional.png'
    )
    issuers.mint.return_value = 'fictional-local-session'
    issuers.write.return_value = user
    monkeypatch.setattr(
        auths.Config, 'get', AsyncMock(side_effect=lambda key: '1h' if key == 'auth.jwt_expiry' else {})
    )
    monkeypatch.setattr(auths, 'get_password_hash', AsyncMock(return_value='fictional-hash'))
    monkeypatch.setattr(invites, 'get_password_hash', AsyncMock(return_value='fictional-hash'))
    monkeypatch.setattr(auths, 'validate_password', lambda _: None)
    monkeypatch.setattr(invites, 'validate_password', lambda _: None)
    monkeypatch.setattr(auths, 'apply_default_group_assignment', AsyncMock())
    monkeypatch.setattr(invites, 'apply_default_group_assignment', AsyncMock())
    monkeypatch.setattr(auths, 'publish_event', AsyncMock())
    monkeypatch.setattr(invites, 'get_permissions', AsyncMock(return_value={}))
    if issuer == 'invite':
        monkeypatch.setattr(
            invites.Invites,
            'get_invite_by_token',
            AsyncMock(
                return_value=SimpleNamespace(
                    name=user.name,
                    email=user.email,
                    role=user.role,
                    revoked_at=None,
                    accepted_at=None,
                    expires_at=4102444800,
                )
            ),
        )
        path = '/api/v1/invites/fictional-invite/accept'
    else:
        issuers.read.return_value = None
        path = '/api/v1/auths/add'
    response = await issuers.client.post(path, json={'email': user.email, 'password': 'Fable-123!', 'name': user.name})
    assert response.status_code == 200, response.text
    assert response.json()['token'] == 'fictional-local-session'
    issuers.mint.assert_called_once()
    assert issuers.write.await_count == (2 if issuer == 'invite' else 1)
    assert ('set-cookie' in response.headers) is (issuer == 'invite')
