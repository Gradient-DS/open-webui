"""Access decisions and grant mutations through recorded soev-api requests."""

import base64
import importlib
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from open_webui.test.soev.fake_api import FakeSoevApi
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SERVICE = 'owui:service:webui'


@pytest_asyncio.fixture
async def env(identity_config, monkeypatch):
    """Use real signed HTTP requests and forbid knowledge access to the SQL context."""
    identity, _ = identity_config
    models = importlib.import_module('open_webui.models.access_grants')
    module = importlib.import_module('open_webui.soev.access_grants')
    stores = importlib.import_module('open_webui.soev.knowledge_store')
    api = FakeSoevApi()
    original = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(api.handle), **kwargs),
    )
    client = identity.build_client()
    store = stores.SoevKnowledgeTable(client=client, service_principal=SERVICE)
    table = module.SoevAccessGrantsTable(store=store)
    monkeypatch.setattr(
        models, 'get_async_db_context', AsyncMock(side_effect=AssertionError('Knowledge must not use SQL grants'))
    )
    await identity.ensure_link('owui:user:owner', client)
    await client.send(
        'POST',
        '/v1/collections',
        {
            'key': 'kb',
            'name': 'Research',
            'description': 'Notes',
            'visibility': 'restricted',
            'principals': [SERVICE, 'owui:group:readers', 'owui:group:writers'],
            'writers': ['owui:group:writers'],
        },
        as_user='owui:user:owner',
        idempotency_key='seed:collection',
    )
    api.groups.update({'owui:group:readers': ['owui:user:reader'], 'owui:group:writers': ['owui:user:writer']})
    api.requests.clear()
    return SimpleNamespace(api=api, table=table, store=store, models=models, module=module)


def subject(request):
    encoded = request.headers['X-Soev-Subject'].split('.')[1]
    return json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['sub']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'user,read,write', [('reader', True, False), ('writer', True, True), ('outsider', False, False)]
)
async def test_read_and_write_are_soev_apis_decision(env, user, read, write):
    """Remote group closure decides access even when the caller supplies misleading local groups."""
    assert await env.table.has_access(user, 'knowledge', 'kb', 'read', {'unrelated'}) is read
    assert await env.table.has_access(user, 'knowledge', 'kb', 'write', {'writers'}) is write
    assert not await env.table.has_access(user, 'knowledge', 'missing')
    assert subject(env.api.requests[-1]) == f'owui:user:{user}'
    assert env.api.requests[0].url.path == '/v1/identity/links'


@pytest.mark.asyncio
@pytest.mark.parametrize('permission,expected', [('read', True), ('write', False)])
async def test_one_request_answers_both_permissions(env, permission, expected):
    """Each permission check needs only one collection GET after ensuring the identity link."""
    assert await env.table.has_access('reader', 'knowledge', 'kb', permission) is expected
    gets = [request for request in env.api.requests if request.method == 'GET']
    assert len(gets) == 1
    assert gets[0].url.path == '/v1/collections/kb'
    assert subject(gets[0]) == 'owui:user:reader'


@pytest.mark.asyncio
@pytest.mark.parametrize('permission,expected', [('read', {'kb', 'public'}), ('write', {'kb'})])
async def test_batch_access_lists_once_and_intersects_requested_ids(env, permission, expected):
    """Batch decisions walk the user's collection listing once and omit pending deletions."""
    env.api.collections['public'] = {
        **env.api.collections['kb'],
        'key': 'public',
        'visibility': 'public',
        'writers': [],
    }
    env.api.collections['deleted'] = {**env.api.collections['kb'], 'key': 'deleted'}
    await env.store.delete_knowledge_by_id('deleted')
    env.api.requests.clear()
    result = await env.table.get_accessible_resource_ids(
        'writer', 'knowledge', ['kb', 'public', 'deleted', 'missing'], permission, {'unrelated'}
    )
    assert result == expected
    gets = [request for request in env.api.requests if request.url.path == '/v1/collections']
    assert len(gets) == 1
    assert subject(gets[0]) == 'owui:user:writer'
    env.api.requests.clear()
    assert await env.table.get_accessible_resource_ids('writer', 'knowledge', []) == set()
    assert not env.api.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('permission,expected', [('read', {'kb', 'read-only'}), ('write', {'kb', 'write-only'})])
@pytest.mark.parametrize('user_id', ['', None])
async def test_group_scoped_access_matches_collection_principals(env, permission, expected, user_id):
    """Group previews match explicit principals without inheriting the request user's audience."""
    base = env.api.collections['kb']
    for key, readers, writers in (
        ('read-only', ['owui:group:readers'], []),
        ('write-only', [], ['owui:group:writers']),
        ('unrelated', ['owui:group:other'], ['owui:group:other']),
        ('unrequested', ['owui:group:readers'], ['owui:group:writers']),
        ('public', [], []),
        ('deleted', ['owui:group:readers'], ['owui:group:writers']),
    ):
        env.api.collections[key] = {
            **base,
            'key': key,
            'principals': [SERVICE, *readers],
            'writers': writers,
            'visibility': 'public' if key == 'public' else 'restricted',
        }
    await env.store.delete_knowledge_by_id('deleted')
    env.api.requests.clear()
    acting = importlib.import_module('open_webui.soev.acting')
    token = acting._acting_ref.set('owui:user:outsider')
    try:
        result = await env.table.get_accessible_resource_ids(
            user_id,
            'knowledge',
            ['kb', 'read-only', 'write-only', 'unrelated', 'public', 'deleted', 'missing'],
            permission,
            {'readers', 'writers'},
        )
        assert result == expected
        assert await env.table.get_accessible_resource_ids(user_id, 'knowledge', ['kb'], permission, set()) == set()
    finally:
        acting._acting_ref.reset(token)
    assert env.api.requests
    assert all(request.method == 'GET' and 'X-Soev-Subject' not in request.headers for request in env.api.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize('permission,expected', [('read', {'kb'}), ('write', set())])
async def test_a_user_query_still_uses_the_assertion(env, permission, expected):
    """A supplied user takes precedence over local group IDs for both permissions."""
    result = await env.table.get_accessible_resource_ids('reader', 'knowledge', ['kb'], permission, {'writers'})
    assert result == expected
    gets = [request for request in env.api.requests if request.method == 'GET']
    assert gets
    assert all(subject(request) == 'owui:user:reader' for request in gets)


@pytest.mark.asyncio
async def test_grant_reads_are_identity_free_projections(env):
    """Grant reads use the service credential and exclude the owner and service from the projection."""
    acting = importlib.import_module('open_webui.soev.acting')
    token = acting._acting_ref.set('owui:user:outsider')
    try:
        rows = await env.table.get_grants_by_resources('knowledge', ['kb', 'missing'])
        single = await env.table.get_grants_by_resource('knowledge', 'kb')
    finally:
        acting._acting_ref.reset(token)
    assert rows == {'kb': single, 'missing': []}
    assert {(row.principal_id, row.permission) for row in single} == {
        ('readers', 'read'),
        ('writers', 'read'),
        ('writers', 'write'),
    }
    assert all(request.method == 'GET' and 'X-Soev-Subject' not in request.headers for request in env.api.requests)


@pytest.mark.asyncio
async def test_setting_grants_patches_writers_and_commissions_the_access_job(env):
    """Writers change immediately while readers change only when the commissioned access job succeeds."""
    acting = importlib.import_module('open_webui.soev.acting')
    token = acting._acting_ref.set('owui:user:owner')
    grants = [
        {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'},
        {'principal_type': 'group', 'principal_id': 'editors', 'permission': 'write'},
    ]
    try:
        result = await env.table.set_access_grants('knowledge', 'kb', grants)
        await env.table.set_access_grants('knowledge', 'kb', grants)
    finally:
        acting._acting_ref.reset(token)
    writes = [request for request in env.api.requests if request.method in ('PATCH', 'PUT')]
    assert [request.method for request in writes] == ['PATCH', 'PUT', 'PATCH', 'PUT']
    assert json.loads(writes[0].content) == {'writers': ['owui:group:editors', 'owui:user:owner']}
    assert json.loads(writes[1].content) == {'visibility': 'public', 'principals': [SERVICE, 'owui:user:owner']}
    assert all(subject(request) == 'owui:user:owner' for request in writes)
    assert any(row.principal_id == 'editors' and row.permission == 'write' for row in result)
    assert env.api.collections['kb']['visibility'] == 'restricted'
    assert len(env.api.jobs) == 1
    env.api.advance(next(iter(env.api.jobs)), 'SUCCEEDED')
    assert env.api.collections['kb']['visibility'] == 'public'


@pytest.mark.asyncio
async def test_revoke_all_access_returns_to_owner_only(env):
    """Revocation keeps the owner as writer and restores restricted readers through the access job."""
    removed = await env.table.revoke_all_access('knowledge', 'kb')
    assert removed == 3
    assert env.api.collections['kb']['writers'] == ['owui:user:owner']
    job_id = next(iter(env.api.jobs))
    assert env.api.job_effects[job_id] == {'visibility': 'restricted', 'principals': [SERVICE, 'owui:user:owner']}
    env.api.advance(job_id, 'SUCCEEDED')
    assert await env.table.get_grants_by_resource('knowledge', 'kb') == []
    assert not await env.table.has_access('reader', 'knowledge', 'kb')
    assert await env.table.has_access('owner', 'knowledge', 'kb', 'write')


@pytest.mark.asyncio
@pytest.mark.parametrize('resource_type', ['model', 'prompt', 'tool', 'note', 'channel', 'file'])
async def test_every_other_resource_type_is_untouched(identity_config, resource_type, monkeypatch):
    """Every non-knowledge override executes the original SQL against an isolated access-grant table."""
    models = importlib.import_module('open_webui.models.access_grants')
    module = importlib.import_module('open_webui.soev.access_grants')
    table = module.SoevAccessGrantsTable()

    @asynccontextmanager
    async def context(db=None):
        yield db

    monkeypatch.setattr(models, 'get_async_db_context', context)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    statements = []
    event.listen(engine.sync_engine, 'before_cursor_execute', lambda *args: statements.append(args[2]))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(models.AccessGrant.__table__.create)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            grants = [{'principal_type': 'group', 'principal_id': 'team', 'permission': 'write'}]
            saved = await table.set_access_grants(resource_type, 'resource', grants, db=db)
            assert len(saved) == 1
            assert await table.has_access('user', resource_type, 'resource', 'write', {'team'}, db)
            assert not await table.has_access('user', resource_type, 'resource', 'read', {'team'}, db)
            assert await table.get_accessible_resource_ids(
                'user', resource_type, ['resource', 'other'], 'write', {'team'}, db
            ) == {'resource'}
            assert await table.get_grants_by_resource(resource_type, 'resource', db) == saved
            assert await table.get_grants_by_resources(resource_type, ['resource', 'other'], db) == {
                'resource': saved,
                'other': [],
            }
            knowledge = importlib.import_module('open_webui.models.knowledge').Knowledge
            args = (db, select(knowledge), knowledge, {'group_ids': ['team']}, resource_type, 'write')
            assert str(table.has_permission_filter(*args)) == str(
                models.AccessGrantsTable().has_permission_filter(*args)
            )
            assert await table.revoke_all_access(resource_type, 'resource', db) == 1
            assert await table.get_grants_by_resource(resource_type, 'resource', db) == []
        assert any('INSERT INTO access_grant' in statement for statement in statements)
        assert any('SELECT' in statement and 'access_grant' in statement for statement in statements)
        assert any('DELETE FROM access_grant' in statement for statement in statements)
    finally:
        await engine.dispose()


def test_legacy_knowledge_sql_filter_is_refused(env):
    """The retired SQL knowledge caller cannot accidentally consult the access-grant table."""
    with pytest.raises(NotImplementedError, match='has_permission_filter'):
        env.table.has_permission_filter(None, select(env.models.AccessGrant), None, {}, 'knowledge')


def assert_import_binding(tmp_path, *, config_first):
    backend = Path(__file__).resolve().parents[3]
    environment = {
        'PATH': os.defpath,
        'PYTHONPATH': str(backend),
        'WEBUI_SECRET_KEY': 't',
        'DATABASE_URL': f'sqlite:///{tmp_path}/imports.db',
        'STATIC_DIR': str(tmp_path / 'static'),
        'DATA_DIR': str(tmp_path / 'data'),
        'VECTOR_DB': 'weaviate',
    }
    for name in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        environment[f'DATABASE_{name}'] = ''
    code = 'import open_webui.config; ' if config_first else ''
    code += 'import open_webui.models.access_grants as m; print(type(m.AccessGrants).__name__)'
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=backend, env=environment, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == 'SoevAccessGrantsTable'


def test_importing_the_access_grants_model_first_binds_the_soev_table(tmp_path):
    """A fresh model-first process binds the access seam without importing configuration recursively."""
    assert_import_binding(tmp_path, config_first=False)


def test_importing_config_first_binds_the_soev_table(tmp_path):
    """A fresh config-first process binds the same access seam."""
    assert_import_binding(tmp_path, config_first=True)
