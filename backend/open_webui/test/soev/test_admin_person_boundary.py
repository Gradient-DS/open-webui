"""Admin requests never borrow the target's platform identity or service authority."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from open_webui.test.soev.test_keycloak_token import token_app as token_app
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def admin_app(token_app, monkeypatch):
    from open_webui import config, env
    from open_webui.internal import db as database
    from open_webui.main import soev_api_error_handler
    from open_webui.models import user_archives
    from open_webui.models.config import Config
    from open_webui.models.users import User, UserModel, Users
    from open_webui.routers import archives, scim, users
    from open_webui.services.archival import resources
    from open_webui.soev import acting, client

    ctx = token_app
    ctx.user.role = 'admin'
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid')
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=True))
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        await Users.insert_new_user('elm', 'Elm', 'elm@example.invalid', role='user', db=db)

        async def lookup(user_id, *, db=db):
            if user_id == ctx.user.id:
                return ctx.user
            row = await db.get(User, user_id)
            return UserModel.model_validate(row) if row else None

        @asynccontextmanager
        async def context(db=None):
            yield session

        session = db
        monkeypatch.setattr(Users, 'get_user_by_id', lookup)
        monkeypatch.setattr(Users, 'get_scim_user_by_id', lookup)
        monkeypatch.setattr(Users, 'get_first_user', AsyncMock(return_value=SimpleNamespace(id='primary-rowan')))
        monkeypatch.setattr(user_archives, 'get_async_db_context', context)
        monkeypatch.setattr(resources, 'get_async_db_context', context)

        async def dependency():
            yield db

        ctx.app.dependency_overrides[database.get_async_session] = dependency
        ctx.app.dependency_overrides[scim.get_scim_auth] = lambda: True
        ctx.app.add_exception_handler(client.SoevApiError, soev_api_error_handler)
        ctx.app.include_router(users.router, prefix='/api/v1/users')
        ctx.app.include_router(archives.router, prefix='/api/v1/archives')
        ctx.app.include_router(scim.router, prefix='/scim/v2')
        # Observe the real resolver, including cross-user rejection, at every admission attempt.
        resolve = AsyncMock(wraps=acting.user_token)
        monkeypatch.setattr(client, 'user_token', resolve)
        yield SimpleNamespace(ctx=ctx, db=db, resolve=resolve, user_table=User)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize('enabled', [False, True])
async def test_admin_suspension_refuses_before_any_write(admin_app, monkeypatch, role, enabled):
    from open_webui import env
    from open_webui.models.auths import Auths

    store = admin_app
    target = await store.db.get(store.user_table, 'elm')
    target.role = role
    await store.db.commit()
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', enabled)
    password = AsyncMock()
    email = AsyncMock()
    monkeypatch.setattr(Auths, 'update_user_password_by_id', password)
    monkeypatch.setattr(Auths, 'update_email_by_id', email)
    response = await store.ctx.browser.post(
        '/api/v1/users/elm/update',
        json={'role': 'pending', 'name': 'Changed', 'email': 'changed@example.invalid', 'password': 'fictional'},
        headers={'Accept-Language': 'nl'},
    )
    assert response.status_code == 403, response.text
    assert response.json()['detail'].get('constraint'), response.text
    assert response.json()['detail']['constraint'] == 'suspension:console'
    assert response.json()['detail']['detail'] == 'Het opschorten van een gebruiker gebeurt in de soev-console'
    await store.db.refresh(target)
    assert (target.role, target.name, target.email) == (role, 'Elm', 'elm@example.invalid')
    password.assert_not_awaited()
    email.assert_not_awaited()
    assert store.ctx.requests == []
    store.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['offboard', 'preview'])
async def test_admin_offboarding_refuses_with_the_real_token_resolver(admin_app, operation):
    store = admin_app
    if operation == 'offboard':
        response = await store.ctx.browser.delete('/api/v1/users/elm')
    else:
        response = await store.ctx.browser.get('/api/v1/users/elm/shared-data')
    assert response.status_code == 403, response.text
    assert response.json()['detail'].get('constraint'), response.text
    assert response.json()['detail']['constraint'] == 'offboarding:console'
    assert await store.db.get(store.user_table, 'elm') is not None
    assert store.ctx.requests == []
    store.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['PATCH', 'PUT'])
async def test_scim_suspension_refuses_without_partial_updates(admin_app, method):
    store = admin_app
    body = (
        {
            'Operations': [
                {'op': 'replace', 'path': 'externalId', 'value': 'changed'},
                {'op': 'replace', 'path': 'active', 'value': False},
            ]
        }
        if method == 'PATCH'
        else {'userName': 'changed@example.invalid', 'active': False, 'externalId': 'changed'}
    )
    response = await store.ctx.browser.request(method, '/scim/v2/Users/elm', json=body)
    assert response.status_code == 403, response.text
    assert response.json()['detail'].get('constraint'), response.text
    assert response.json()['detail']['constraint'] == 'suspension:console'
    target = await store.db.get(store.user_table, 'elm')
    await store.db.refresh(target)
    assert (target.role, target.email, target.scim) == ('user', 'elm@example.invalid', None)
    assert store.ctx.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['DELETE', 'PATCH'])
@pytest.mark.parametrize('token', [False, True])
@pytest.mark.parametrize('collections', [[], ['archive-fern']])
async def test_archive_mutations_require_the_admins_own_token(admin_app, method, token, collections):
    from open_webui.models.user_archives import UserArchives

    store = admin_app
    archive = await UserArchives.insert_archive(
        'elm', 'elm@example.invalid', 'Elm', 'Departure', 'fern', {'collections': collections}, retention_days=7
    )
    if not token:
        store.ctx.browser.cookies.clear()
    response = await store.ctx.browser.request(
        method, f'/api/v1/archives/{archive.id}', **({'json': {'never_delete': True}} if method == 'PATCH' else {})
    )
    if not token:
        assert response.status_code == 401
        assert response.json()['detail']['code'] == 'sign_in'
        current = await UserArchives.get_archive_by_id(archive.id)
        assert current is not None and current.never_delete == archive.never_delete
        assert store.ctx.requests == []
        return
    assert response.status_code == 200
    for request in store.ctx.requests:
        assert request.headers['X-Soev-User-Token'] == 'fictional-access-token'
        assert 'X-Soev-Subject' not in request.headers
    assert all(call.args == ('owui:user:fern',) for call in store.resolve.await_args_list)


@pytest.mark.asyncio
async def test_explicit_cross_user_client_call_still_refuses(admin_app):
    from fastapi import Depends
    from open_webui.soev import identity

    store = admin_app

    @store.ctx.app.get('/cross-user')
    async def cross_user(user=Depends(store.ctx.auth.get_verified_user)):
        return await identity.build_client().get('/v1/collections', as_user='owui:user:elm')

    response = await store.ctx.browser.get('/cross-user')
    assert response.status_code == 401
    assert response.json()['detail']['code'] == 'sign_in'
    assert store.ctx.requests == []


@pytest.mark.asyncio
async def test_oauth_role_management_surfaces_suspension_as_403(admin_app, monkeypatch):
    from fastapi import Depends, Request
    from open_webui.models.users import Users
    from open_webui.utils.oauth import OAuthManager

    store = admin_app
    manager = OAuthManager.__new__(OAuthManager)
    monkeypatch.setattr(manager, 'get_user_role', AsyncMock(return_value='pending'))

    @store.ctx.app.get('/oauth-role-update')
    async def update_role(request: Request, user=Depends(store.ctx.auth.get_verified_user)):
        target = await Users.get_user_by_id('elm', db=store.db)
        return await manager.update_user_role_from_oauth(request, target, {}, 'oidc', db=store.db)

    response = await store.ctx.browser.get('/oauth-role-update')
    assert response.status_code == 403
    assert response.json()['detail']['constraint'] == 'suspension:console'
    target = await store.db.get(store.user_table, 'elm')
    await store.db.refresh(target)
    assert target.role == 'user'
    assert store.ctx.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('before,after', [('admin', 'user'), ('pending', 'user')])
async def test_role_changes_without_platform_custody_still_work(admin_app, monkeypatch, before, after):
    from open_webui.soev import group_sync

    store = admin_app
    target = await store.db.get(store.user_table, 'elm')
    target.role = before
    await store.db.commit()
    narrow = AsyncMock()
    monkeypatch.setattr(group_sync, 'narrow_admin', narrow)
    response = await store.ctx.browser.post('/api/v1/users/elm/update', json={'role': after})
    assert response.status_code == 200
    await store.db.refresh(target)
    assert target.role == after
    assert store.ctx.requests == []
    if before == 'admin':
        narrow.assert_awaited_once_with('elm', db=store.db)
    else:
        narrow.assert_not_awaited()
