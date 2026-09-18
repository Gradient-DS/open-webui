"""Migrate legacy cloud sources through owner-scoped, replayable HTTP requests."""

import copy
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from open_webui.soev import migrate
from open_webui.test.soev.fake_api import FakeSoevApi, Problem
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class CloudApi(FakeSoevApi):
    def __init__(self):
        super().__init__(page_size=1)
        self.connections = {}
        self.connection_owners = {}
        self.min_cadence_minutes = 37

    def _route(self, request, body, credential, subject):
        path = request.url.path
        if path == '/v1/sync-policy' and request.method == 'GET':
            return httpx.Response(200, json={'min_cadence_minutes': self.min_cadence_minutes})
        if path not in {'/v1/connections', '/v1/schedules'} or request.method != 'POST':
            return super()._route(request, body, credential, subject)
        self._require(credential, 'connect')
        if subject is None:
            raise Problem(403, 'subject_required')
        if path == '/v1/connections':
            assert set(body) == {'source_kind', 'credential_kind'}
            assert body['credential_kind'] == 'user_oauth'
            connection_id = f'connection-{len(self.connections)}'
            row = {'id': connection_id, **body, 'lifecycle': 'pending'}
            self.connections[connection_id] = row
            self.connection_owners[connection_id] = subject
        else:
            assert set(body) == {'connection_id', 'kind', 'scope', 'cadence_minutes', 'collection_key'}
            connection_id = body['connection_id']
            assert self.connection_owners[connection_id] == subject
            self._collection(body['collection_key'], credential, subject, write=True)
            assert body['cadence_minutes'] >= self.min_cadence_minutes
            schedule_id = f'schedule-{len(self.schedules)}'
            row = {'id': schedule_id, **body, 'source_kind': self.connections[connection_id]['source_kind']}
            self.schedules[schedule_id] = row
            self.schedule_owners[schedule_id] = subject
        return httpx.Response(201, json=row)


@pytest_asyncio.fixture
async def env(identity_config, fake_api, monkeypatch):
    """Use real SQL reads and signed HTTP with no deployment credentials or SQL writes."""
    identity, _ = identity_config
    api = CloudApi()
    api.signing_keys = fake_api.signing_keys
    api.audience = fake_api.audience
    api.capabilities['test-runtime-key'] = {'connect', 'directory', 'read', 'write'}
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(api.handle), **kwargs),
    )
    database = importlib.import_module('open_webui.internal.db')
    knowledge = importlib.import_module('open_webui.models.knowledge')
    users = importlib.import_module('open_webui.models.users')
    groups = importlib.import_module('open_webui.models.groups')
    access = importlib.import_module('open_webui.models.access_grants')
    files = importlib.import_module('open_webui.models.files')
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    tables = [
        users.User,
        groups.Group,
        groups.GroupMember,
        knowledge.Knowledge,
        knowledge.KnowledgeFile,
        knowledge.KnowledgeDirectory,
        access.AccessGrant,
        files.File,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: database.Base.metadata.create_all(sync, tables=[table.__table__ for table in tables])
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        for user_id in ('alice', 'bob'):
            await users.Users.insert_new_user(user_id, user_id, f'{user_id}@example.test', db=db)

        async def seed(provider='onedrive', *, source_type='folder', owner='alice', sources=None):
            source = {'type': source_type, 'item_id': 'item-1', 'name': 'Source', 'item_path': '/Source'}
            if provider == 'onedrive':
                source['drive_id'] = 'drive-1'
            kb = knowledge.Knowledge(
                id=f'{provider}-{owner}',
                user_id=owner,
                type=provider,
                name='Cloud',
                description='Cloud notes',
                meta={
                    f'{provider}_sync': {
                        'sources': [source] if sources is None else sources,
                        'delta_link': 'old-cursor',
                    }
                },
                created_at=1,
                updated_at=1,
            )
            db.add(kb)
            await db.commit()
            return kb

        def read_only(_connection, _cursor, statement, _parameters, _context, _many):
            assert statement.lstrip().upper().startswith('SELECT'), 'Migration attempted a SQL write'

        async def run(*, dry_run=False):
            event.listen(engine.sync_engine, 'before_cursor_execute', read_only)
            try:
                return await migrate.migrate(db=db, dry_run=dry_run)
            finally:
                event.remove(engine.sync_engine, 'before_cursor_execute', read_only)

        yield SimpleNamespace(api=api, identity=identity, seed=seed, run=run)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_onedrive_kb_becomes_a_pending_connection_and_two_schedules(env, capsys):
    """A folder retains its owner, source and collection with both schedule kinds at the policy floor."""
    kb = await env.seed()
    assert await env.run() == 0
    assert list(env.api.connections.values()) == [
        {'id': 'connection-0', 'source_kind': 'onedrive', 'credential_kind': 'user_oauth', 'lifecycle': 'pending'}
    ]
    assert env.api.connection_owners == {'connection-0': 'owui:user:alice'}
    schedules = list(env.api.schedules.values())
    assert [row['kind'] for row in schedules] == ['content', 'acl_refresh']
    for row in schedules:
        assert row['connection_id'] == 'connection-0'
        assert row['collection_key'] == kb.id
        assert row['cadence_minutes'] == 37
        assert row['scope'] == {
            'drive_id': 'drive-1',
            'item_id': 'item-1',
            'include_descendants': True,
            'single_file': False,
        }
    writes = [r for r in env.api.requests if r.url.path in {'/v1/connections', '/v1/schedules'} and r.method == 'POST']
    assert [r.headers['Idempotency-Key'] for r in writes] == [
        f'migrate:connection:{kb.id}',
        f'migrate:schedule:{kb.id}:0',
        f'migrate:schedule:{kb.id}:1',
    ]
    paths = [r.url.path for r in env.api.requests]
    assert paths.index('/v1/collections') < paths.index('/v1/connections')
    assert 'KBs with a schedule: 1 | KBs of a cloud type: 1' in capsys.readouterr().out
    env.api.schedules.clear()
    await migrate.reconcile(
        {kb.id: 0},
        env.identity.build_client(),
        conflicts=set(),
        cloud_owners={kb.id: 'owui:user:alice'},
    )
    assert 'KBs with a schedule: 0 | KBs of a cloud type: 1' in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'provider,source_type', [('onedrive', 'file'), ('google_drive', 'file'), ('google_drive', 'folder')]
)
async def test_a_single_file_source_maps_to_single_file_scope(env, provider, source_type):
    """Legacy item ids map to provider scopes with recursion disabled for individual files."""
    await env.seed(provider, source_type=source_type)
    assert await env.run() == 0
    expected = (
        {'drive_id': 'drive-1', 'item_id': 'item-1', 'include_descendants': False, 'single_file': True}
        if provider == 'onedrive'
        else {'file_id': 'item-1', 'drive_id': None, 'include_descendants': source_type == 'folder'}
    )
    assert len(env.api.schedules) == 2
    assert all(row['scope'] == expected for row in env.api.schedules.values())


@pytest.mark.asyncio
async def test_a_confluence_kb_is_reported_and_skipped(env, capsys):
    """Confluence keeps collection migration but creates no connection or schedules."""
    kb = await env.seed('confluence')
    assert await env.run() == 0
    output = capsys.readouterr().out
    assert f'{kb.id}: Confluence cloud sync skipped (D10)' in output
    assert 'KBs with a schedule: 0 | KBs of a cloud type: 1' in output
    assert kb.id in env.api.collections
    assert not env.api.connections and not env.api.schedules
    assert not any(r.url.path == '/v1/sync-policy' for r in env.api.requests)


@pytest.mark.asyncio
async def test_dry_run_sends_nothing(env, capsys):
    """Preview includes both providers and skipped Confluence without HTTP or minted assertions."""
    for provider in ('onedrive', 'google_drive', 'confluence'):
        await env.seed(provider)
    assert await env.run(dry_run=True) == 0
    output = capsys.readouterr().out
    assert not env.api.requests and not env.identity._linked_refs
    planned = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
    connections = [row for row in planned if row['path'] == '/v1/connections']
    schedules = [row for row in planned if row['path'] == '/v1/schedules']
    assert len(connections) == 2 and len(schedules) == 4
    assert all(row['as_user'] == 'owui:user:alice' for row in connections + schedules)
    assert all(row['body']['cadence_minutes'] == '<sync-policy.min_cadence_minutes>' for row in schedules)
    assert all(row['body']['connection_id'].startswith('<connection:') for row in schedules)
    assert 'KBs with a schedule: not read (dry-run) | KBs of a cloud type: 3' in output
    assert 'test-runtime-key' not in output and 'PRIVATE KEY' not in output and 'eyJ' not in output


@pytest.mark.asyncio
async def test_rerun_creates_nothing_new(env, capsys):
    """Multiple owners and sources survive both cached-link and fresh-process idempotent reruns."""
    await env.seed()
    await env.seed(
        'google_drive',
        owner='bob',
        sources=[
            {'type': 'folder', 'item_id': 'folder', 'drive_id': 'shared-drive'},
            {'type': 'file', 'item_id': 'file'},
        ],
    )
    assert await env.run() == 0
    before = copy.deepcopy((env.api.connections, env.api.schedules, env.api.collections))
    assert len(env.api.connections) == 2 and len(env.api.schedules) == 6
    assert env.api.connection_owners == {'connection-0': 'owui:user:bob', 'connection-1': 'owui:user:alice'}
    assert [row['scope']['drive_id'] for row in list(env.api.schedules.values())[:4]] == [
        'shared-drive',
        'shared-drive',
        None,
        None,
    ]
    for clear_links in (False, True):
        if clear_links:
            env.identity._linked_refs.clear()
        assert await env.run() == 0
        assert (env.api.connections, env.api.schedules, env.api.collections) == before
    assert capsys.readouterr().out.count('KBs with a schedule: 2 | KBs of a cloud type: 2') == 3
    schedule_reads = [r for r in env.api.requests if r.url.path == '/v1/schedules' and r.method == 'GET']
    assert all('X-Soev-Subject' in r.headers for r in schedule_reads)
    assert any('cursor' in r.url.params for r in schedule_reads)
