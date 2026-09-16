"""OWUI group writes projected through the recorded directory replacement contract."""

import importlib
import inspect
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

OVERRIDDEN = {
    'insert_new_group',
    'set_group_user_ids_by_id',
    'update_group_by_id',
    'delete_group_by_id',
    'delete_all_groups',
    'remove_user_from_all_groups',
    'create_groups_by_group_names',
    'sync_groups_by_group_names',
    'add_users_to_group',
    'remove_users_from_group',
    'delete_groups_by_user_id',
}
READ_ONLY = {
    'get_all_groups',
    'get_group_by_name',
    'get_groups',
    'search_groups',
    'get_groups_by_member_id',
    'get_groups_by_member_ids',
    'get_group_by_id',
    'get_group_user_ids_by_id',
    'get_group_user_ids_by_ids',
    'get_group_member_count_by_id',
    'get_group_member_counts_by_ids',
}


def assert_import_binds_soev_table(tmp_path, *, config_first):
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
    code += 'import open_webui.models.groups as g; print(type(g.Groups).__name__)'
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=backend, env=environment, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == 'SoevGroupTable'


def test_importing_the_groups_model_first_binds_the_soev_table(tmp_path):
    """A fresh model-first process binds Groups without recursively importing it through migrations."""
    assert_import_binds_soev_table(tmp_path, config_first=False)


def test_importing_config_first_binds_the_soev_table(tmp_path):
    """A fresh config-first process also binds Groups to the soev table."""
    assert_import_binds_soev_table(tmp_path, config_first=True)


@pytest.fixture
def directory_http(monkeypatch):
    """Record replacements and replay 204 without applying a committed operation twice."""
    state = SimpleNamespace(requests=[], members={}, operations={}, failures=[])
    original_client = httpx.AsyncClient

    def handle(request):
        state.requests.append(request)
        assert request.method == 'PUT'
        prefix = '/v1/directory/groups/'
        assert request.url.path.startswith(prefix)
        assert request.url.path.endswith('/members')
        group_ref = request.url.path[len(prefix) : -len('/members')]
        assert group_ref.startswith('owui:group:')
        assert request.url.raw_path == f'{prefix}{quote(group_ref, safe="")}/members'.encode()
        assert request.headers['Authorization'] == 'Bearer test-runtime-key'
        assert 'X-Soev-Subject' not in request.headers
        operation = request.headers['Idempotency-Key']
        assert 8 <= len(operation) <= 255
        body = json.loads(request.content)
        assert set(body) == {'members'}
        members = body['members']
        assert members == sorted(set(members))
        assert all(ref.startswith('owui:user:') for ref in members)
        if state.failures:
            failure = state.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(
                failure,
                json={'code': 'test_failure', 'detail': 'test-runtime-key reflected-secret'},
                headers={'Content-Type': 'application/problem+json'},
            )
        if operation in state.operations:
            assert state.operations[operation] == (group_ref, members)
            return httpx.Response(204)
        state.operations[operation] = (group_ref, members)
        state.members[group_ref] = members
        return httpx.Response(204)

    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    return state


@pytest_asyncio.fixture
async def group_store(identity_config, directory_http, monkeypatch):
    """Exercise the real upstream group methods against isolated SQLite tables."""
    models = importlib.import_module('open_webui.models.groups')
    users = importlib.import_module('open_webui.models.users')
    database = importlib.import_module('open_webui.internal.db')
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: database.Base.metadata.create_all(
                sync, tables=[models.Group.__table__, models.GroupMember.__table__, users.User.__table__]
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for user_id in ('alice', 'bob', 'carol'):
            await users.Users.insert_new_user(user_id, user_id, f'{user_id}@example.test', db=session)
        yield SimpleNamespace(
            models=models, table=models.Groups, raw=models.GroupTable(), db=session, http=directory_http
        )
    await engine.dispose()


async def seed(store, name='staff', members=('bob', 'alice'), owner='alice'):
    group = await store.raw.insert_new_group(owner, store.models.GroupForm(name=name, description=''), db=store.db)
    assert group is not None
    await store.raw.set_group_user_ids_by_id(group.id, list(members), db=store.db)
    return group


async def assert_pushed(store, expected):
    assert set(store.http.members) == {f'owui:group:{group_id}' for group_id in expected}
    for group_id, members in expected.items():
        assert store.http.members[f'owui:group:{group_id}'] == [f'owui:user:{user_id}' for user_id in sorted(members)]
        group = await store.raw.get_group_by_id(group_id, db=store.db)
        actual = await store.raw.get_group_user_ids_by_id(group_id, db=store.db) if group else []
        assert sorted(actual) == sorted(members)


@pytest.mark.asyncio
async def test_insert_new_group_pushes_its_members(group_store):
    """Creating a group pushes its empty member set, excluding its owner."""
    store = group_store
    group = await store.table.insert_new_group(
        'alice', store.models.GroupForm(name='staff', description=''), db=store.db
    )
    assert group is not None
    await assert_pushed(store, {group.id: []})


@pytest.mark.asyncio
async def test_set_group_user_ids_by_id_pushes_its_members(group_store):
    """Replacing members pushes the complete persisted set in external-ref order."""
    store = group_store
    group = await seed(store)
    result = await store.table.set_group_user_ids_by_id(group.id, ['carol', 'alice'], db=store.db)
    assert result is None
    await assert_pushed(store, {group.id: ['alice', 'carol']})


@pytest.mark.asyncio
async def test_update_group_by_id_pushes_its_members(group_store):
    """A metadata update republishes unchanged memberships and preserves its return value."""
    store = group_store
    group = await seed(store)
    result = await store.table.update_group_by_id(
        group.id, store.models.GroupUpdateForm(name='renamed', description='new'), overwrite=True, db=store.db
    )
    assert result.name == 'renamed'
    await assert_pushed(store, {group.id: ['alice', 'bob']})


@pytest.mark.asyncio
async def test_delete_group_by_id_pushes_its_members(group_store):
    """Deleting a group pushes an empty set even when SQLite leaves orphan membership rows."""
    store = group_store
    group = await seed(store)
    assert await store.table.delete_group_by_id(group.id, db=store.db) is True
    await assert_pushed(store, {group.id: []})


@pytest.mark.asyncio
async def test_delete_all_groups_pushes_their_members(group_store):
    """Bulk deletion remembers every group's id before removing its rows."""
    store = group_store
    first, second = await seed(store), await seed(store, 'other', ('carol',))
    assert await store.table.delete_all_groups(db=store.db) is True
    await assert_pushed(store, {first.id: [], second.id: []})


@pytest.mark.asyncio
async def test_remove_user_from_all_groups_pushes_their_members(group_store):
    """Removing a user republishes each prior group and leaves unrelated groups alone."""
    store = group_store
    first, second = await seed(store), await seed(store, 'other', ('alice', 'carol'))
    await seed(store, 'untouched', ('carol',))
    assert await store.table.remove_user_from_all_groups('alice', db=store.db) is True
    await assert_pushed(store, {first.id: ['bob'], second.id: ['carol']})


@pytest.mark.asyncio
async def test_create_groups_by_group_names_pushes_their_members(group_store):
    """Creating groups by name pushes each newly created group and skips existing names."""
    store = group_store
    await seed(store, 'existing')
    groups = await store.table.create_groups_by_group_names('alice', ['existing', 'new', 'other'], db=store.db)
    assert {group.name for group in groups} == {'new', 'other'}
    await assert_pushed(store, {group.id: [] for group in groups})


@pytest.mark.asyncio
async def test_sync_groups_by_group_names_pushes_their_members(group_store):
    """Name synchronization republishes both removed and added memberships."""
    store = group_store
    removed = await seed(store, 'removed')
    added = await seed(store, 'added', ('carol',))
    kept = await seed(store, 'kept', ('alice',))
    await seed(store, 'untouched', ('bob',))
    assert await store.table.sync_groups_by_group_names('alice', ['added', 'kept', 'absent'], db=store.db) is True
    await assert_pushed(store, {removed.id: ['bob'], added.id: ['alice', 'carol'], kept.id: ['alice']})


@pytest.mark.asyncio
async def test_add_users_to_group_pushes_its_members(group_store):
    """Adding users sends the old members as well as the new users."""
    store = group_store
    group = await seed(store)
    result = await store.table.add_users_to_group(group.id, ['carol'], db=store.db)
    assert result.id == group.id
    await assert_pushed(store, {group.id: ['alice', 'bob', 'carol']})


@pytest.mark.asyncio
async def test_remove_users_from_group_pushes_its_members(group_store):
    """Removing members republishes the surviving users from the persisted group."""
    store = group_store
    group = await seed(store)
    result = await store.table.remove_users_from_group(group.id, ['alice'], db=store.db)
    assert result.id == group.id
    await assert_pushed(store, {group.id: ['bob']})


@pytest.mark.asyncio
async def test_delete_groups_by_user_id_pushes_their_members(group_store):
    """Owner deletion clears all owned groups without touching groups the user only belongs to."""
    store = group_store
    first, second = await seed(store), await seed(store, 'other', ('carol',))
    await seed(store, 'untouched', owner='bob')
    assert await store.table.delete_groups_by_user_id('alice', db=store.db) is True
    await assert_pushed(store, {first.id: [], second.id: []})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure', [403, 503, httpx.ConnectError('reflected-secret'), httpx.ReadTimeout('reflected-secret')]
)
async def test_a_failed_push_is_logged_and_never_fails_the_owui_write(group_store, failure, caplog):
    """HTTP and transport failures keep the OWUI write and disclose only group id and status."""
    store = group_store
    first, second = await seed(store), await seed(store, 'other')
    store.http.failures.append(failure)
    with caplog.at_level(logging.WARNING):
        assert await store.table.remove_user_from_all_groups('alice', db=store.db) is True
    assert len(store.http.requests) == 2
    for group in (first, second):
        assert await store.raw.get_group_user_ids_by_id(group.id, db=store.db) == ['bob']
    records = [record for record in caplog.records if record.name == 'open_webui.soev.groups']
    assert len(records) == 1
    assert records[0].group_id in {first.id, second.id}
    expected_status = failure if isinstance(failure, int) else (504 if isinstance(failure, httpx.ReadTimeout) else 502)
    assert records[0].status == expected_status
    assert records[0].exc_info is None
    exposed = caplog.text + repr([vars(record) for record in records])
    assert 'reflected-secret' not in exposed
    assert 'test-runtime-key' not in exposed
    assert 'owui:user:' not in exposed


@pytest.mark.asyncio
async def test_repeated_pushes_use_the_same_operation_key(group_store):
    """The same member set replays 204 under a stable key regardless of input ordering."""
    store = group_store
    group = await seed(store)
    for members in (['bob', 'alice'], ['alice', 'bob'], ['carol']):
        await store.table.set_group_user_ids_by_id(group.id, members, db=store.db)
    keys = [request.headers['Idempotency-Key'] for request in store.http.requests]
    assert len(keys) == 3
    assert keys[0] == keys[1]
    assert keys[2] != keys[0]
    assert all(key.startswith(f'group:{group.id}:') and len(key.rsplit(':', 1)[1]) == 64 for key in keys)
    await assert_pushed(store, {group.id: ['carol']})


@pytest.mark.asyncio
async def test_an_empty_api_url_skips_pushes_and_warns_once(group_store, identity_config, monkeypatch, caplog):
    """An unconfigured deployment keeps local writes and warns once across table instances."""
    store = group_store
    identity, _ = identity_config
    monkeypatch.setattr(identity.config, 'SOEV_API_URL', '')
    group = await seed(store)
    with caplog.at_level(logging.WARNING):
        await store.table.set_group_user_ids_by_id(group.id, ['carol'], db=store.db)
        await type(store.table)().set_group_user_ids_by_id(group.id, ['bob'], db=store.db)
    assert store.http.requests == []
    assert await store.raw.get_group_user_ids_by_id(group.id, db=store.db) == ['bob']
    records = [record for record in caplog.records if record.name == 'open_webui.soev.groups']
    assert len(records) == 1
    assert 'SOEV_API_URL' in records[0].getMessage()


def test_every_mutating_group_method_is_overridden(group_store):
    """Every public upstream coroutine is literally classified and mutators have matching signatures."""
    parent = group_store.models.GroupTable
    child = type(group_store.table)
    methods = {
        name for name, method in inspect.getmembers(parent, inspect.iscoroutinefunction) if not name.startswith('_')
    }
    assert OVERRIDDEN.isdisjoint(READ_ONLY)
    assert methods == OVERRIDDEN | READ_ONLY
    assert child.__name__ == 'SoevGroupTable'
    for name in OVERRIDDEN:
        assert name in child.__dict__
        original, replacement = inspect.signature(getattr(parent, name)), inspect.signature(getattr(child, name))
        assert list(original.parameters) == list(replacement.parameters)
        for key, parameter in original.parameters.items():
            assert parameter.default == replacement.parameters[key].default
            assert parameter.kind == replacement.parameters[key].kind
    for name in READ_ONLY:
        assert getattr(child, name) is getattr(parent, name)


@pytest.mark.asyncio
async def test_a_failed_local_deletion_republishes_the_actual_members(group_store, monkeypatch):
    """A failed local deletion keeps its return value and never empties a surviving group remotely."""
    store = group_store
    group = await seed(store)
    monkeypatch.setattr(store.models.GroupTable, 'delete_group_by_id', AsyncMock(return_value=False))
    assert await store.table.delete_group_by_id(group.id, db=store.db) is False
    await assert_pushed(store, {group.id: ['alice', 'bob']})
