"""Admin requests never borrow the target's platform identity or service authority."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from open_webui.test.soev.test_keycloak_token import token_app as token_app
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _non_directory_requests(requests):
    remaining = []
    for request in requests:
        if request.method == 'GET' and request.url.path == '/v1/groups':
            assert 'X-Soev-User-Token' not in request.headers
            assert 'X-Soev-' + 'Subject' not in request.headers
        else:
            remaining.append(request)
    return remaining


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
    monkeypatch.setattr(scim, 'SCIM_AUTH_PROVIDER', 'fictional-provider')
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
@pytest.mark.parametrize('method', ['PATCH', 'PUT'])
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize('other_fields', [False, True])
@pytest.mark.parametrize('active_attribute', ['active', 'Active', 'ACTIVE', 'aCtIvE'])
async def test_scim_suspension_refuses_without_partial_updates(
    admin_app, monkeypatch, method, role, other_fields, active_attribute
):
    from open_webui.routers import scim

    store = admin_app
    target = await store.db.get(store.user_table, 'elm')
    target.role = role
    target.scim = {'fictional-provider': {'external_id': 'original'}}
    await store.db.commit()
    original = (target.role, target.name, target.email, target.profile_image_url, target.scim.copy())
    update = AsyncMock(wraps=scim.Users.update_user_by_id)
    stamp = AsyncMock(wraps=scim.Users.update_user_scim_by_id)
    publish = AsyncMock()
    monkeypatch.setattr(scim.Users, 'update_user_by_id', update)
    monkeypatch.setattr(scim.Users, 'update_user_scim_by_id', stamp)
    monkeypatch.setattr(scim, 'publish_event', publish)
    if method == 'PATCH':
        operations = []
        if other_fields:
            operations.extend(
                [
                    {'op': 'replace', 'path': 'externalId', 'value': 'changed'},
                    {'op': 'replace', 'path': 'displayName', 'value': 'Changed'},
                    {'op': 'replace', 'path': 'userName', 'value': 'changed@example.invalid'},
                ]
            )
        operations.append({'op': 'replace', 'path': active_attribute, 'value': False})
        body = {'Operations': operations}
    else:
        body = {active_attribute: False}
        if other_fields:
            body.update(
                {
                    'userName': 'changed@example.invalid',
                    'displayName': 'Changed',
                    'photos': [{'value': '/changed.png'}],
                    'externalId': 'changed',
                }
            )
    response = await store.ctx.browser.request(method, '/scim/v2/Users/elm', json=body)
    assert response.status_code == 403, response.text
    assert response.json()['detail'].get('constraint'), response.text
    assert response.json()['detail']['constraint'] == 'suspension:console'
    await store.db.refresh(target)
    assert (target.role, target.name, target.email, target.profile_image_url, target.scim) == original
    update.assert_not_awaited()
    stamp.assert_not_awaited()
    publish.assert_not_awaited()
    assert _non_directory_requests(store.ctx.requests) == []
    store.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize('op', ['replace', 'add', 'RePlAcE', 'ADD'])
@pytest.mark.parametrize(
    'path,value', [('active', False), ('Active', False), (None, {'active': False}), (None, {'Active': False})]
)
async def test_scim_cannot_hide_deactivation_with_a_later_reactivation(admin_app, monkeypatch, role, op, path, value):
    from open_webui.routers import scim

    store = admin_app
    target = await store.db.get(store.user_table, 'elm')
    target.role = role
    await store.db.commit()
    update = AsyncMock(wraps=scim.Users.update_user_by_id)
    stamp = AsyncMock(wraps=scim.Users.update_user_scim_by_id)
    publish = AsyncMock()
    monkeypatch.setattr(scim.Users, 'update_user_by_id', update)
    monkeypatch.setattr(scim.Users, 'update_user_scim_by_id', stamp)
    monkeypatch.setattr(scim, 'publish_event', publish)
    deactivation = {'op': op, 'path': path, 'value': value} if path else {'op': op, 'value': value}
    response = await store.ctx.browser.patch(
        '/scim/v2/Users/elm',
        json={
            'Operations': [
                {'op': 'replace', 'path': 'externalId', 'value': 'changed'},
                {'op': 'replace', 'path': 'displayName', 'value': 'Changed'},
                deactivation,
                {'op': 'replace', 'path': 'active', 'value': True},
            ]
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()['detail']['constraint'] == 'suspension:console'
    await store.db.refresh(target)
    assert (target.role, target.name, target.scim) == (role, 'Elm', None)
    update.assert_not_awaited()
    stamp.assert_not_awaited()
    publish.assert_not_awaited()
    assert _non_directory_requests(store.ctx.requests) == []
    store.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize(
    'operation',
    [
        {'op': 'remove', 'path': 'active'},
        {'op': 'unknown', 'path': 'displayName', 'value': 'Changed'},
        {'op': 'replace', 'path': 'unknown', 'value': 'Changed'},
        {'op': 'replace', 'value': {'unknown': 'Changed'}},
        {'op': 'replace', 'value': {'displayName': 'Changed', 'unknown': 'Changed'}},
        {'op': 'replace', 'value': False},
        {'op': 'add', 'value': []},
        {'op': 'replace', 'value': {}},
        {'op': 'replace', 'path': '', 'value': False},
        {'op': 'replace', 'path': 'active', 'value': 'false'},
        {'op': 'add', 'path': 'Active', 'value': 0},
        {'op': 'replace', 'value': {'active': None}},
        {'op': 'replace', 'path': 'displayName', 'value': {'formatted': 'Changed'}},
    ],
)
async def test_scim_invalid_patch_refuses_before_companion_writes(admin_app, monkeypatch, role, operation):
    from open_webui.routers import scim

    store = admin_app
    target = await store.db.get(store.user_table, 'elm')
    target.role = role
    await store.db.commit()
    update = AsyncMock(wraps=scim.Users.update_user_by_id)
    stamp = AsyncMock(wraps=scim.Users.update_user_scim_by_id)
    publish = AsyncMock()
    monkeypatch.setattr(scim.Users, 'update_user_by_id', update)
    monkeypatch.setattr(scim.Users, 'update_user_scim_by_id', stamp)
    monkeypatch.setattr(scim, 'publish_event', publish)
    response = await store.ctx.browser.patch(
        '/scim/v2/Users/elm',
        json={
            'Operations': [
                {'op': 'replace', 'path': 'externalId', 'value': 'changed'},
                {'op': 'replace', 'path': 'displayName', 'value': 'Changed'},
                operation,
            ]
        },
    )
    assert response.status_code == 400, response.text
    await store.db.refresh(target)
    assert (target.role, target.name, target.scim) == (role, 'Elm', None)
    update.assert_not_awaited()
    stamp.assert_not_awaited()
    publish.assert_not_awaited()
    assert _non_directory_requests(store.ctx.requests) == []
    store.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin', 'pending'])
@pytest.mark.parametrize('op', ['replace', 'add'])
@pytest.mark.parametrize('pathless', [False, True])
async def test_scim_normalized_patch_applies_supported_attributes(admin_app, role, op, pathless):
    store = admin_app
    target = await store.db.get(store.user_table, 'elm')
    target.role = role
    await store.db.commit()
    attributes = {
        'Active': True,
        'UserName': 'changed@example.invalid',
        'DisplayName': 'Changed',
        'ExternalId': 'changed',
    }
    operations = (
        [{'op': op, 'value': attributes}]
        if pathless
        else [{'op': op, 'path': path, 'value': value} for path, value in attributes.items()]
    )
    response = await store.ctx.browser.patch('/scim/v2/Users/elm', json={'Operations': operations})
    assert response.status_code == 200, response.text
    await store.db.refresh(target)
    assert target.role == role
    assert (target.name, target.email) == ('Changed', 'changed@example.invalid')
    assert target.scim == {'fictional-provider': {'external_id': 'changed'}}
    assert _non_directory_requests(store.ctx.requests) == []
    store.resolve.assert_not_awaited()


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
    assert response.json()['detail']['state'] == 'sign_in'
    assert _non_directory_requests(store.ctx.requests) == []
