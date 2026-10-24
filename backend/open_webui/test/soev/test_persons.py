"""Person discovery uses the live login's proof and persists only the returned id."""

import importlib
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.users import User
from open_webui.soev import persons
from open_webui.soev.client import SoevApiError
from open_webui.test.soev.test_keycloak_token import token_app as token_app

PERSON = 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'


@pytest_asyncio.fixture
async def person_store(token_app, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    token_app.user.person_id = None
    async with sessions() as db:
        db.add(User(**token_app.user.model_dump(exclude={'variables'})))
        await db.commit()

    @asynccontextmanager
    async def context():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(persons, 'get_async_db_context', context)
    yield token_app, sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_oidc_request_stores_person_without_a_subject_assertion(person_store):
    ctx, sessions = person_store
    ctx.session.expires_at = 1
    assert (await ctx.browser.get('/call/product')).status_code == 200
    me = ctx.requests[0]
    assert me.url.path == '/v1/me'
    assert me.headers['Authorization'] == 'Bearer soev_test_cred-runtime_test-secret'
    assert me.headers['X-Soev-User-Token'] == 'fictional-refreshed-token'
    assert 'X-Soev-Subject' not in me.headers
    async with sessions() as db:
        assert (await db.get(User, ctx.user.id)).person_id == PERSON
    assert ctx.user.person_id == PERSON
    assert 'person_id' not in ctx.user.model_dump()
    assert (await ctx.browser.get('/call/product')).status_code == 200
    assert sum(request.url.path == '/v1/me' for request in ctx.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('login', ['password', 'microsoft', 'api_key', 'foreign_session'])
async def test_no_person_is_inferred_without_the_users_oidc_proof(person_store, login):
    ctx, sessions = person_store
    if login == 'password':
        ctx.browser.cookies.clear()
    elif login == 'microsoft':
        ctx.session.provider = 'microsoft'
    elif login == 'api_key':
        ctx.browser.headers['Authorization'] = 'Bearer sk-fictional'
    else:
        ctx.session.user_id = 'lark'
    assert (await ctx.browser.get('/call/product')).status_code == 200
    assert all(request.url.path != '/v1/me' for request in ctx.requests)
    async with sessions() as db:
        assert (await db.get(User, ctx.user.id)).person_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{}, {'person_id': 'owui:user:fern'}, {'person_id': 'platform:user:invalid'}])
async def test_invalid_person_response_never_writes_a_mapping(person_store, monkeypatch, payload):
    ctx, sessions = person_store
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: http)
        await persons.remember_person(ctx.user, 'fictional-access-token')
    async with sessions() as db:
        assert (await db.get(User, ctx.user.id)).person_id is None


@pytest.mark.asyncio
async def test_identity_failure_keeps_the_mapping_empty(person_store, monkeypatch):
    ctx, sessions = person_store
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401))) as http:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: http)
        await persons.remember_person(ctx.user, 'fictional-invalid-token')
    async with sessions() as db:
        assert (await db.get(User, ctx.user.id)).person_id is None


@pytest.mark.asyncio
async def test_unavailable_person_discovery_does_not_fail_the_request(person_store, monkeypatch):
    ctx, sessions = person_store

    async def unavailable(self, access_token):
        raise SoevApiError(503, 'service_unavailable', 'unavailable')

    monkeypatch.setattr(persons.SoevClient, 'me', unavailable)
    assert (await ctx.browser.get('/call/product')).status_code == 200
    async with sessions() as db:
        assert (await db.get(User, ctx.user.id)).person_id is None


@pytest.mark.asyncio
async def test_an_existing_person_mapping_cannot_be_overwritten(person_store):
    ctx, sessions = person_store
    other_person = 'platform:user:63643f13-46eb-4ecf-baa9-9b91bdab907c'
    async with sessions() as db:
        await db.execute(sa.update(User).where(User.id == ctx.user.id).values(person_id=other_person))
        await db.commit()
    with pytest.raises(persons.PersonMismatchError):
        await persons.remember_person(ctx.user, 'fictional-access-token')
    async with sessions() as db:
        assert (await db.get(User, ctx.user.id)).person_id == other_person


@pytest.mark.asyncio
async def test_a_different_stored_person_refuses_the_request(person_store):
    ctx, sessions = person_store
    other_person = 'platform:user:63643f13-46eb-4ecf-baa9-9b91bdab907c'
    async with sessions() as db:
        await db.execute(sa.update(User).where(User.id == ctx.user.id).values(person_id=other_person))
        await db.commit()
    assert (await ctx.browser.get('/call/product')).status_code == 401


def test_person_column_migration_preserves_existing_users_and_downgrades():
    migration = importlib.import_module('open_webui.migrations.versions.b7e943a2f610_add_user_person_id')
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE user (id TEXT PRIMARY KEY, name TEXT)'))
        connection.execute(sa.text("INSERT INTO user VALUES ('fern', 'Fern')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT person_id FROM user')).scalar() is None
            connection.execute(sa.text('UPDATE user SET person_id=:person'), {'person': PERSON})
            migration.downgrade()
        assert connection.execute(sa.text('SELECT id, name FROM user')).one() == ('fern', 'Fern')
        assert 'person_id' not in {column['name'] for column in sa.inspect(connection).get_columns('user')}
    engine.dispose()
