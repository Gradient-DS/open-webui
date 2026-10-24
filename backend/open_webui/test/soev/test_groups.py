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
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PERSONS = {
    'alice': 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace',
    'bob': 'platform:user:f92f2148-7789-4d52-8f78-fb77601e5090',
    'carol': 'platform:user:dd573aff-c5d5-4923-a967-006331e24baa',
}

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
    state = SimpleNamespace(requests=[], members={}, operations={}, failures=[], original_client=httpx.AsyncClient)
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
        assert request.headers['Authorization'] == 'Bearer soev_test_cred-runtime_test-secret'
        assert 'X-Soev-Subject' not in request.headers
        operation = request.headers['Idempotency-Key']
        assert 8 <= len(operation) <= 255
        body = json.loads(request.content)
        assert set(body) == {'members'}
        members = body['members']
        assert members == sorted(set(members))
        assert all(ref.startswith('platform:user:') for ref in members)
        if state.failures:
            failure = state.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(
                failure,
                json={'code': 'test_failure', 'detail': 'soev_test_cred-runtime_test-secret reflected-secret'},
                headers={'Content-Type': 'application/problem+json'},
            )
        if operation in state.operations:
            assert state.operations[operation] == (group_ref, members)
            return httpx.Response(204)
        state.operations[operation] = (group_ref, members)
        state.members[group_ref] = members
        return httpx.Response(204)

    class DirectoryClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr('open_webui.soev.client.httpx.AsyncClient', DirectoryClient)
    return state


@pytest_asyncio.fixture
async def group_store(identity_config, directory_http, monkeypatch, platform_capabilities):
    """Exercise the real upstream group methods against isolated SQLite tables."""
    from open_webui.soev import group_sync

    monkeypatch.setattr(group_sync, '_pending', {})
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
            await session.execute(update(users.User).where(users.User.id == user_id).values(person_id=PERSONS[user_id]))
        await session.commit()
        yield SimpleNamespace(
            models=models, table=models.Groups, raw=models.GroupTable(), db=session, http=directory_http
        )
    await engine.dispose()


async def seed(store, name='staff', members=('bob', 'alice'), owner='alice'):
    group = await store.raw.insert_new_group(owner, store.models.GroupForm(name=name, description=''), db=store.db)
    assert group is not None
    await store.raw.set_group_user_ids_by_id(group.id, list(members), db=store.db)
    return group


@pytest.mark.asyncio
async def test_push_skips_users_without_person_ids_and_keeps_local_members(group_store):
    from open_webui.models.users import User
    from open_webui.soev import acting, group_sync

    store = group_store
    group = await seed(store)
    await store.db.execute(update(User).where(User.id == 'bob').values(person_id=None))
    await store.db.commit()
    resolver = AsyncMock(return_value='fictional-user-token')
    token = acting._token_resolver.set(resolver)
    requester = acting._requester_ref.set('owui:user:alice')
    try:
        with acting.acting_as('owui:user:alice'):
            await group_sync.publish(store.table, [group.id], db=store.db)
    finally:
        acting._requester_ref.reset(requester)
        acting._token_resolver.reset(token)
    assert store.http.members[f'owui:group:{group.id}'] == [PERSONS['alice']]
    assert sorted(await store.raw.get_group_user_ids_by_id(group.id, db=store.db)) == ['alice', 'bob']
    assert 'X-Soev-User-Token' not in store.http.requests[-1].headers
    resolver.assert_not_awaited()


async def assert_pushed(store, expected):
    assert set(store.http.members) == {f'owui:group:{group_id}' for group_id in expected}
    for group_id, members in expected.items():
        assert store.http.members[f'owui:group:{group_id}'] == sorted(PERSONS[user_id] for user_id in members)
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
@pytest.mark.parametrize('failure', [403, 503, httpx.ConnectError('secret'), httpx.ReadTimeout('secret')])
@pytest.mark.parametrize('action', ['replace', 'remove', 'remove_all', 'delete', 'delete_all', 'delete_owned', 'oauth'])
async def test_narrowing_refuses_before_any_local_write(group_store, failure, action):
    from fastapi import HTTPException

    store = group_store
    group = await seed(store)
    store.http.failures.append(failure)
    calls = {
        'replace': lambda: store.table.set_group_user_ids_by_id(group.id, ['carol'], db=store.db),
        'remove': lambda: store.table.remove_users_from_group(group.id, ['alice'], db=store.db),
        'remove_all': lambda: store.table.remove_user_from_all_groups('alice', db=store.db),
        'delete': lambda: store.table.delete_group_by_id(group.id, db=store.db),
        'delete_all': lambda: store.table.delete_all_groups(db=store.db),
        'delete_owned': lambda: store.table.delete_groups_by_user_id('alice', db=store.db),
        'oauth': lambda: store.table.sync_groups_by_group_names('alice', [], db=store.db),
    }
    with pytest.raises(HTTPException) as raised:
        await calls[action]()
    assert raised.value.status_code == 503
    assert await store.raw.get_group_by_id(group.id, db=store.db) is not None
    assert sorted(await store.raw.get_group_user_ids_by_id(group.id, db=store.db)) == ['alice', 'bob']


@pytest.mark.asyncio
async def test_widening_queues_retry_reads_current_members_and_logs_only_group_id(group_store, caplog):
    from open_webui.soev import group_sync

    store = group_store
    group = await seed(store)
    store.http.failures.append(503)
    with caplog.at_level(logging.WARNING):
        await store.table.add_users_to_group(group.id, ['carol'], db=store.db)
    assert sorted(await store.raw.get_group_user_ids_by_id(group.id, db=store.db)) == ['alice', 'bob', 'carol']
    records = [record for record in caplog.records if record.name == 'open_webui.soev.group_sync']
    assert len(records) == 1 and records[0].group_id == group.id
    assert 'status' not in vars(records[0])
    assert records[0].exc_info is None
    assert group.id in group_sync.pending_groups()
    await store.raw.remove_users_from_group(group.id, ['alice'], db=store.db)
    await group_sync.retry_pending(store.table, now=float('inf'), db=store.db)
    assert store.http.members[f'owui:group:{group.id}'] == sorted([PERSONS['bob'], PERSONS['carol']])
    assert group.id not in group_sync.pending_groups()


@pytest.mark.asyncio
async def test_mixed_replacement_never_publishes_new_members_before_commit(group_store, monkeypatch):
    store = group_store
    group = await seed(store)
    original = store.models.GroupTable.set_group_user_ids_by_id

    async def commit(table, group_id, user_ids, db=None):
        assert store.http.members[f'owui:group:{group_id}'] == [PERSONS['alice']]
        return await original(table, group_id, user_ids, db=db)

    monkeypatch.setattr(store.models.GroupTable, 'set_group_user_ids_by_id', commit)
    await store.table.set_group_user_ids_by_id(group.id, ['alice', 'carol'], db=store.db)
    await assert_pushed(store, {group.id: ['alice', 'carol']})


@pytest.mark.asyncio
async def test_repeated_pushes_replace_members_again_after_an_intervening_change(group_store):
    """The same member set replays 204 under a stable key regardless of input ordering."""
    store = group_store
    group = await seed(store)
    for members in (['bob', 'alice'], ['carol'], ['alice', 'bob']):
        await store.table.set_group_user_ids_by_id(group.id, members, db=store.db)
    await assert_pushed(store, {group.id: ['alice', 'bob']})


@pytest.mark.asyncio
async def test_an_empty_api_url_skips_pushes(group_store, identity_config, monkeypatch, caplog):
    """An unconfigured deployment keeps local writes without platform calls."""
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
    assert records == []


@pytest.mark.asyncio
async def test_local_group_write_succeeds_without_push_capability(group_store, platform_capabilities):
    platform_capabilities.discard('groups_push')
    group = await seed(group_store)
    await group_store.table.set_group_user_ids_by_id(group.id, ['carol'], db=group_store.db)
    assert await group_store.raw.get_group_user_ids_by_id(group.id, db=group_store.db) == ['carol']
    assert group_store.http.requests == []


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
    """A failed local deletion leaves the platform narrower until reconciliation."""
    store = group_store
    group = await seed(store)
    monkeypatch.setattr(store.models.GroupTable, 'delete_group_by_id', AsyncMock(return_value=False))
    assert await store.table.delete_group_by_id(group.id, db=store.db) is False
    assert store.http.members[f'owui:group:{group.id}'] == []
    assert await store.raw.get_group_by_id(group.id, db=store.db) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('narrowing', [False, True])
async def test_unknown_capabilities_refuse_narrowing_and_queue_widening(group_store, monkeypatch, narrowing):
    from fastapi import HTTPException
    from open_webui.soev import capabilities, group_sync

    group = await seed(group_store)
    monkeypatch.setattr(capabilities, 'has', AsyncMock(return_value=False))
    monkeypatch.setattr(capabilities, 'known', lambda: False)
    if narrowing:
        with pytest.raises(HTTPException):
            await group_store.table.remove_users_from_group(group.id, ['alice'], db=group_store.db)
        assert sorted(await group_store.raw.get_group_user_ids_by_id(group.id, db=group_store.db)) == ['alice', 'bob']
    else:
        await group_store.table.add_users_to_group(group.id, ['carol'], db=group_store.db)
        assert group.id in group_sync.pending_groups()
        assert sorted(await group_store.raw.get_group_user_ids_by_id(group.id, db=group_store.db)) == [
            'alice',
            'bob',
            'carol',
        ]


@pytest.mark.asyncio
async def test_reconcile_paginates_and_empties_remote_orphans(group_store, fake_api, monkeypatch):
    from open_webui.soev import group_sync

    group = await seed(group_store)
    chat_http = fake_api
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: group_store.http.original_client(transport=httpx.MockTransport(chat_http.handle), **kwargs),
    )
    chat_http.page_size = 1
    chat_http.groups = {
        'owui:group:missing': ['owui:user:carol'],
        'owui:group:other': [],
        'foreign:group:kept': ['foreign:user:x'],
    }
    await group_sync.reconcile(group_store.table, db=group_store.db)
    assert chat_http.groups[f'owui:group:{group.id}'] == sorted([PERSONS['alice'], PERSONS['bob']])
    assert chat_http.groups['owui:group:missing'] == []
    assert chat_http.groups['foreign:group:kept'] == ['foreign:user:x']
    gets = [r for r in chat_http.requests if r.method == 'GET']
    assert len(gets) >= 3
    assert all(r.url.path == '/v1/directory/groups' and int(r.url.params['limit']) <= 500 for r in gets)


@pytest.mark.asyncio
@pytest.mark.parametrize('trigger', ['admin', 'scim', 'oauth'])
async def test_narrowing_trigger_propagates_refusal_without_local_removal(group_store, monkeypatch, trigger):
    from fastapi import HTTPException, Request
    from open_webui.routers import groups, scim
    from open_webui.utils import oauth

    store = group_store
    group = await seed(store)
    store.http.failures.append(503)
    request = Request({'type': 'http', 'path': '/', 'headers': []})
    user = SimpleNamespace(id='alice', role='admin')
    for module in (groups, scim, oauth):
        monkeypatch.setattr(module, 'Groups', store.table)
        monkeypatch.setattr(module, 'publish_event', AsyncMock())
    monkeypatch.setattr(
        oauth,
        'get_oauth_runtime_config',
        AsyncMock(
            return_value=SimpleNamespace(
                OAUTH_GROUPS_CLAIM='groups', OAUTH_BLOCKED_GROUPS='[]', ENABLE_OAUTH_GROUP_CREATION=False
            )
        ),
    )
    with pytest.raises(HTTPException) as raised:
        if trigger == 'admin':
            await groups.remove_users_from_group(
                request=request,
                id=group.id,
                form_data=store.models.UserIdsForm(user_ids=['alice']),
                user=user,
                db=store.db,
            )
        elif trigger == 'scim':
            await scim.update_group(
                group_id=group.id,
                request=request,
                group_data=scim.SCIMGroupUpdateRequest(members=[scim.SCIMGroupMember(value='bob')]),
                db=store.db,
            )
        else:
            await oauth.OAuthManager.update_user_groups(None, request, user, {'groups': ['other']}, {}, db=store.db)
    assert raised.value.status_code == 503
    assert sorted(await store.raw.get_group_user_ids_by_id(group.id, db=store.db)) == ['alice', 'bob']


@pytest.mark.asyncio
async def test_signup_default_group_keeps_local_addition_and_queues_failure(group_store):
    from open_webui.soev import group_sync
    from open_webui.utils.groups import apply_default_group_assignment

    group = await seed(group_store)
    group_store.http.failures.append(503)
    await apply_default_group_assignment(group.id, 'carol', db=group_store.db)
    assert group.id in group_sync.pending_groups()
    assert 'carol' in await group_store.raw.get_group_user_ids_by_id(group.id, db=group_store.db)


@pytest.mark.asyncio
async def test_retries_wait_for_backoff(group_store):
    from open_webui.soev import group_sync

    group = await seed(group_store)
    group_store.http.failures.extend([503, 503])
    await group_store.table.add_users_to_group(group.id, ['carol'], db=group_store.db)
    due, attempts = group_sync._pending[group.id]
    assert attempts == 1
    requests = len(group_store.http.requests)
    await group_sync.retry_pending(group_store.table, now=due - 1, db=group_store.db)
    assert len(group_store.http.requests) == requests
    await group_sync.retry_pending(group_store.table, now=due, db=group_store.db)
    assert group_sync._pending[group.id][1] == 2
    assert group_sync._pending[group.id][0] > due


@pytest.mark.asyncio
async def test_reconciler_runs_at_startup_and_configured_interval(monkeypatch):
    import asyncio

    from open_webui import config
    from open_webui.soev import group_sync

    now = [0.0]
    reconciles, retries = AsyncMock(), AsyncMock()
    monkeypatch.setattr(group_sync, 'monotonic', lambda: now[0])
    monkeypatch.setattr(group_sync, 'reconcile', reconciles)
    monkeypatch.setattr(group_sync, 'retry_pending', retries)
    monkeypatch.setattr(config, 'SOEV_GROUP_RECONCILE_MINUTES', 1)

    async def pause(seconds):
        now[0] += 30
        if now[0] > 60:
            raise asyncio.CancelledError

    monkeypatch.setattr(group_sync.asyncio, 'sleep', pause)
    await group_sync._run(asyncio.Event())
    assert reconciles.await_count == 2
    assert retries.await_count == 3
