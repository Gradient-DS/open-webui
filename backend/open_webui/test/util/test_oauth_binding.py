"""OAuth account selection uses persisted provider subjects, never email."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from fastapi import HTTPException
from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal import db as database
from open_webui.models.auths import Auth
from open_webui.models.users import User, Users
from open_webui.routers import auths
from open_webui.utils import oauth
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.responses import Response


@pytest_asyncio.fixture
async def login(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
        await connection.run_sync(Auth.__table__.create)
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    monkeypatch.setattr(oauth, 'OAUTH_PROVIDERS', {'google': {}})
    manager = oauth.OAuthManager.__new__(oauth.OAuthManager)
    claims = {'sub': 'subject-fern', 'email': 'fern@example.invalid', 'name': 'Fern'}
    token = {'access_token': 'fictional-access-token', 'expires_in': 3600, 'userinfo': claims}
    client = SimpleNamespace(
        authorize_access_token=AsyncMock(return_value=token),
        userinfo=AsyncMock(return_value=claims),
    )
    manager._clients = {'google': client}
    runtime = SimpleNamespace(**{name: default for name, (_, default) in oauth.OAUTH_RUNTIME_CONFIG.items()})
    runtime.ENABLE_OAUTH = True
    runtime.ENABLE_OAUTH_SIGNUP = True
    runtime.OAUTH_INVITE_REQUIRED = False
    runtime.OAUTH_ALLOWED_DOMAINS = ['*']
    runtime.OAUTH_SUB_CLAIM = 'sub'
    runtime.OAUTH_EMAIL_CLAIM = 'email'
    runtime.OAUTH_USERNAME_CLAIM = 'name'
    runtime.OAUTH_PICTURE_CLAIM = ''
    runtime.OAUTH_UPDATE_NAME_ON_LOGIN = False
    runtime.OAUTH_UPDATE_EMAIL_ON_LOGIN = False
    runtime.OAUTH_UPDATE_PICTURE_ON_LOGIN = False
    runtime.ENABLE_OAUTH_GROUP_MANAGEMENT = False
    runtime.JWT_EXPIRES_IN = '1h'
    monkeypatch.setattr(oauth, 'get_oauth_runtime_config', AsyncMock(return_value=runtime))
    monkeypatch.setattr(oauth.Invites, 'get_pending_invite_by_email', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth.Invites, 'consume_invite_by_email', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth, 'get_password_hash', AsyncMock(return_value='fictional-password-hash'))
    monkeypatch.setattr(manager, 'get_user_role', AsyncMock(return_value='user'))
    monkeypatch.setattr(manager, 'update_user_role_from_oauth', AsyncMock(side_effect=lambda **kwargs: kwargs['user']))
    monkeypatch.setattr(oauth.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth, 'apply_default_group_assignment', AsyncMock())
    monkeypatch.setattr(oauth, 'publish_event', AsyncMock())
    monkeypatch.setattr(oauth, 'ENABLE_OAUTH_ID_TOKEN_COOKIE', False)
    monkeypatch.setattr(oauth.OAuthSessions, 'get_sessions_by_user_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(
        oauth.OAuthSessions, 'create_session', AsyncMock(return_value=SimpleNamespace(id='session-fern'))
    )
    monkeypatch.setattr(oauth.silent_connect, 'after_login', AsyncMock(side_effect=lambda user, provider, url: url))
    mint = Mock(wraps=oauth.create_token)
    monkeypatch.setattr(oauth, 'create_token', mint)
    request = SimpleNamespace(base_url='https://chat.example.invalid/', app=SimpleNamespace(state=SimpleNamespace()))
    request.app.state.oauth_manager = manager

    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        await Users.insert_new_user('admin-cedar', 'Cedar', 'cedar@example.invalid', role='admin', db=db)

        async def callback():
            return await manager.handle_callback(request, 'google', Response(), db=db)

        yield SimpleNamespace(
            db=db, callback=callback, claims=claims, runtime=runtime, mint=mint, request=request, manager=manager
        )
    await engine.dispose()


def assert_logged_in(response, login, user_id):
    assert response.status_code == 307
    assert response.headers['location'] == 'https://chat.example.invalid/auth'
    assert any(cookie.startswith('token=') for cookie in response.headers.getlist('set-cookie'))
    assert login.mint.call_args.kwargs['data'] == {'id': user_id}


@pytest.mark.asyncio
async def test_new_user_signup_stores_provider_subject(login):
    response = await login.callback()
    user = await Users.get_user_by_email('fern@example.invalid', db=login.db)
    assert user is not None
    assert user.oauth == {'google': {'sub': 'subject-fern'}}
    assert user.name == 'Fern'
    assert user.role == 'user'
    assert_logged_in(response, login, user.id)


@pytest.mark.asyncio
async def test_returning_subject_logs_in_when_email_changes_and_signup_is_disabled(login):
    binding = {'google': {'sub': 'subject-fern'}}
    await Users.insert_new_user('fern', 'Fern', 'old-fern@example.invalid', role='user', oauth=binding, db=login.db)
    login.runtime.ENABLE_OAUTH_SIGNUP = False
    login.runtime.OAUTH_INVITE_REQUIRED = True
    assert_logged_in(await login.callback(), login, 'fern')
    user = await Users.get_user_by_id('fern', db=login.db)
    assert user.email == 'old-fern@example.invalid'
    assert user.oauth == binding


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'binding', [None, {'google': {'sub': 'other-subject'}}, {'microsoft': {'sub': 'subject-fern'}}]
)
@pytest.mark.parametrize('invite', ['none', 'pending', 'consumed-concurrently'])
async def test_same_email_never_attaches_an_unknown_subject(login, binding, invite, monkeypatch, caplog):
    original = copy.deepcopy(binding)

    async def create_existing_user():
        await Users.insert_new_user('fern', 'Fern', 'fern@example.invalid', role='user', oauth=binding, db=login.db)

    if invite != 'consumed-concurrently':
        await create_existing_user()
    if invite != 'none':
        pending = SimpleNamespace(role='admin', name='Fern')
        oauth.Invites.get_pending_invite_by_email.return_value = pending
        if invite == 'pending':
            oauth.Invites.consume_invite_by_email.return_value = pending
        else:
            # The password flow wins after the OAuth subject lookup, before signup.
            async def accept_on_password_page(email):
                await create_existing_user()
                return None

            oauth.Invites.consume_invite_by_email.side_effect = accept_on_password_page
    update = AsyncMock(wraps=Users.update_user_oauth_by_id)
    monkeypatch.setattr(Users, 'update_user_oauth_by_id', update)

    with caplog.at_level('WARNING'):
        response = await login.callback()

    assert parse_qs(urlsplit(response.headers['location']).query) == {'error': [ERROR_MESSAGES.ACCESS_PROHIBITED]}
    assert response.headers.getlist('set-cookie') == []
    login.mint.assert_not_called()
    oauth.OAuthSessions.create_session.assert_not_awaited()
    update.assert_not_awaited()
    user = await Users.get_user_by_id('fern', db=login.db)
    assert user.oauth == original
    assert user.role == 'user'
    assert await Users.get_num_users(db=login.db) == 2
    record = next(record for record in caplog.records if getattr(record, 'code', None) == 'oauth_email_conflict')
    assert record.provider == 'google'
    assert 'fern@example.invalid' not in caplog.text + repr([vars(record) for record in caplog.records])


@pytest.mark.asyncio
async def test_new_user_signup_remains_disabled_without_an_invite(login):
    login.runtime.ENABLE_OAUTH_SIGNUP = False
    response = await login.callback()
    assert parse_qs(urlsplit(response.headers['location']).query) == {'error': [ERROR_MESSAGES.ACCESS_PROHIBITED]}
    assert await Users.get_user_by_email('fern@example.invalid', db=login.db) is None
    login.mint.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('known_subject', [False, True])
async def test_token_exchange_requires_the_stored_provider_subject(login, monkeypatch, known_subject):
    binding = {'google': {'sub': 'subject-fern' if known_subject else 'other-subject'}}
    await Users.insert_new_user('fern', 'Fern', 'fern@example.invalid', role='user', oauth=binding, db=login.db)
    monkeypatch.setattr(auths, 'ENABLE_OAUTH_TOKEN_EXCHANGE', True)
    monkeypatch.setattr(auths, 'OAUTH_PROVIDERS', {'google': {}})
    monkeypatch.setattr(auths, 'OAUTH_TOKEN_EXCHANGE_TRUSTED_CLIENT_IDS', [])
    monkeypatch.setattr(auths, 'token_exchange_rate_limiter', None)

    async def get_config(key, default=None):
        if key == 'oauth.allowed_domains':
            return ['*']
        # A stale persisted merge key must never restore the removed behavior.
        return True if key.endswith('_by_email') else default

    monkeypatch.setattr(auths.Config, 'get', AsyncMock(side_effect=get_config))
    session = AsyncMock(return_value={'id': 'fern'})
    monkeypatch.setattr(auths, 'create_session_response', session)
    form = auths.TokenExchangeForm(token='fictional-access-token')
    if known_subject:
        result = await auths.token_exchange(login.request, Response(), 'google', form, db=login.db)
        assert result == {'id': 'fern'}
        session.assert_awaited_once()
        assert session.call_args.args[1].id == 'fern'
    else:
        with pytest.raises(HTTPException) as error:
            await auths.token_exchange(login.request, Response(), 'google', form, db=login.db)
        assert error.value.status_code == 403
        session.assert_not_awaited()
    assert (await Users.get_user_by_id('fern', db=login.db)).oauth == binding


@pytest.mark.asyncio
async def test_email_merge_setting_is_absent(monkeypatch):
    legacy_name = '_'.join(('OAUTH', 'MERGE', 'ACCOUNTS', 'BY', 'EMAIL'))
    legacy_key = 'oauth.' + '_'.join(('merge', 'accounts', 'by', 'email'))
    assert not hasattr(config, legacy_name)
    assert legacy_key not in config.DEFAULT_CONFIG
    assert legacy_name not in oauth.OAUTH_RUNTIME_CONFIG
    assert legacy_name not in auths.OAuthConfigForm.model_fields
    assert legacy_name not in auths.OAUTH_CONFIG_KEYS
    monkeypatch.setattr(oauth.Config, 'get_many', AsyncMock(return_value={legacy_key: True}))
    runtime = await oauth.get_oauth_runtime_config()
    assert not hasattr(runtime, legacy_name)
