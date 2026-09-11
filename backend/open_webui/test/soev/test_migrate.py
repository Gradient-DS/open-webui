"""Migrate original SQL rows through the recorded soev-api contract."""

import base64
import copy
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import pytest_asyncio
from open_webui.soev.client import SoevApiError
from open_webui.test.soev.fake_api import FakeSoevApi
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def env(identity_config, monkeypatch):
    """Seed original SQL classes, then reject every SQL write and rebound singleton call."""
    identity, _ = identity_config
    knowledge = importlib.import_module('open_webui.models.knowledge')
    access = importlib.import_module('open_webui.models.access_grants')
    groups = importlib.import_module('open_webui.models.groups')
    files = importlib.import_module('open_webui.models.files')
    database = importlib.import_module('open_webui.internal.db')
    raw, grants, group_table = knowledge.KnowledgeTable(), access.AccessGrantsTable(), groups.GroupTable()
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    users = importlib.import_module('open_webui.models.users')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: database.Base.metadata.create_all(
                sync,
                tables=[
                    users.User.__table__,
                    groups.Group.__table__,
                    groups.GroupMember.__table__,
                    knowledge.Knowledge.__table__,
                    knowledge.KnowledgeFile.__table__,
                    knowledge.KnowledgeDirectory.__table__,
                    access.AccessGrant.__table__,
                    files.File.__table__,
                ],
            )
        )
    api = FakeSoevApi(page_size=1)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(api.handle), **kwargs),
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        for user_id in ('alice', 'bob', 'oauth'):
            user = await users.Users.insert_new_user(user_id, user_id, f'{user_id}@example.test', db=db)
            assert user is not None
        oauth = await db.get(users.User, 'oauth')
        oauth.oauth = {'entra': {'sub': 'provider-sub'}}
        await db.commit()
        group = await group_table.insert_new_group('alice', groups.GroupForm(name='Staff', description=''), db=db)
        await group_table.set_group_user_ids_by_id(group.id, ['bob', 'alice'], db=db)
        empty = await group_table.insert_new_group('alice', groups.GroupForm(name='Empty', description=''), db=db)
        kbs = []
        with monkeypatch.context() as seed_patch:
            seed_patch.setattr(knowledge, 'AccessGrants', grants)
            for name, owner, kind, acl in (
                (
                    'Research',
                    'alice',
                    'local',
                    [
                        {'principal_type': 'group', 'principal_id': group.id, 'permission': 'read'},
                        {'principal_type': 'group', 'principal_id': group.id, 'permission': 'write'},
                        {'principal_type': 'user', 'principal_id': 'bob', 'permission': 'read'},
                    ],
                ),
                ('Public', 'bob', 'custom', [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]),
                ('Orphan', 'gone', 'local', []),
                ('External', 'alice', 'local', []),
                ('Deleted', 'alice', 'local', []),
            ):
                kb = await raw.insert_new_knowledge(
                    owner,
                    knowledge.KnowledgeForm(name=name, description=f'{name} notes', type=kind, access_grants=acl),
                    db=db,
                )
                assert kb is not None
                kbs.append(kb)
            await raw.update_knowledge_meta_by_id(kbs[3].id, {'source': 'external'}, db=db)
            deleted = await db.get(knowledge.Knowledge, kbs[4].id)
            deleted.deleted_at = 1
            await db.commit()
            for index, path in enumerate(('reports/2026/a.pdf', 'reports/2026/b.pdf', 'notes/c.txt', 'root.txt', None)):
                file = await files.Files.insert_new_file(
                    'alice',
                    files.FileForm(
                        id=f'file-{index}', filename=f'{index}.txt', path='/unused', meta={'relative_path': path}
                    ),
                    db=db,
                )
                assert file is not None
                assert await raw.add_file_to_knowledge_by_id(kbs[0].id, file.id, 'alice', db=db)
            file = await db.get(files.File, 'file-0')
            file.meta = {'relative_path': 'stale/file-metadata.pdf'}
            await db.commit()
        for module, name in (
            (knowledge, 'Knowledges'),
            (knowledge, 'AccessGrants'),
            (access, 'AccessGrants'),
            (groups, 'Groups'),
        ):
            monkeypatch.setattr(module, name, Mock(side_effect=AssertionError('Rebound singleton used')))

        def read_only(_connection, _cursor, statement, _parameters, _context, _many):
            assert statement.lstrip().upper().startswith('SELECT'), 'Migration attempted a SQL write'

        event.listen(engine.sync_engine, 'before_cursor_execute', read_only)
        yield SimpleNamespace(db=db, api=api, kbs=kbs, group=group, empty=empty, identity=identity)
        event.remove(engine.sync_engine, 'before_cursor_execute', read_only)
    await engine.dispose()


async def run(env, *, dry_run=False):
    module = importlib.import_module('open_webui.soev.migrate')
    return await module.migrate(dry_run=dry_run, db=env.db)


@pytest.mark.asyncio
async def test_every_knowledge_base_becomes_a_collection_keyed_by_its_id(env):
    """All eligible KB types retain ids, names, descriptions, ACLs and owner attribution."""
    assert await run(env) == 0
    assert set(env.api.collections) == {kb.id for kb in env.kbs[:3]}
    for kb in env.kbs[:3]:
        row = env.api.collections[kb.id]
        assert (row['name'], row['description']) == (kb.name, kb.description)
    row = env.api.collections[env.kbs[0].id]
    assert row['created_by'] == 'owui:user:alice'
    assert row['principals'] == sorted(
        ['owui:service:webui', 'owui:user:alice', 'owui:user:bob', f'owui:group:{env.group.id}']
    )
    assert row['writers'] == sorted(['owui:user:alice', f'owui:group:{env.group.id}'])
    assert env.api.collections[env.kbs[1].id]['visibility'] == 'public'
    creates = [r for r in env.api.requests if r.url.path == '/v1/collections' and r.method == 'POST']
    assert {r.headers['Idempotency-Key'] for r in creates} == {f'kb:{kb.id}' for kb in env.kbs[:3]}


@pytest.mark.asyncio
async def test_a_rerun_creates_nothing(env):
    """Same-process and fresh-process reruns preserve remote rows without expiring replay records."""
    assert await run(env) == 0
    before = copy.deepcopy((env.api.collections, env.api.folders, env.api.links, env.api.groups))
    assert await run(env) == 0
    assert (env.api.collections, env.api.folders, env.api.links, env.api.groups) == before
    env.identity._linked_refs.clear()
    try:
        result = await run(env)
    except SoevApiError as error:
        pytest.fail(f'Fresh-process rerun failed: HTTP {error.status} {error.code}', pytrace=False)
    assert result == 0
    assert (env.api.collections, env.api.folders, env.api.links, env.api.groups) == before


@pytest.mark.asyncio
async def test_every_user_gets_a_link_of_the_right_assurance(env):
    """Local and OAuth users both use self-vouched OWUI refs without an IdP token."""
    assert await run(env) == 0
    assert env.api.links == {
        f'owui:user:{uid}': env.identity.platform_user_id(f'owui:user:{uid}') for uid in ('alice', 'bob', 'oauth')
    }
    for request in env.api.requests:
        if request.url.path != '/v1/identity/links':
            continue
        body = json.loads(request.content)
        assert set(body) == {'platform_user_id', 'assertion'}
        encoded = body['assertion'].split('.')[1]
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        assert payload['sub'] in env.api.links


@pytest.mark.asyncio
async def test_groups_are_pushed_before_collections_name_them(env):
    """Links precede complete sorted group replacements, including empty groups, then collections."""
    assert await run(env) == 0
    requests = env.api.requests
    links = [i for i, r in enumerate(requests) if r.url.path == '/v1/identity/links']
    groups = [i for i, r in enumerate(requests) if r.method == 'PUT']
    creates = [i for i, r in enumerate(requests) if r.method == 'POST' and r.url.path == '/v1/collections']
    assert max(links) < min(groups) <= max(groups) < min(creates)
    assert env.api.groups == {
        f'owui:group:{env.group.id}': ['owui:user:alice', 'owui:user:bob'],
        f'owui:group:{env.empty.id}': [],
    }
    for i in groups:
        request = requests[i]
        members = json.loads(request.content)['members']
        digest = hashlib.sha256(json.dumps(members, separators=(',', ':')).encode()).hexdigest()
        assert request.headers['Idempotency-Key'].endswith(':' + digest)


@pytest.mark.asyncio
async def test_folders_are_created_from_relative_paths(env):
    """Distinct join-row parent paths become folders with implied ancestors and no root mkdir."""
    assert await run(env) == 0
    assert set(env.api.folders[env.kbs[0].id]) == {'reports', 'reports/2026', 'notes'}
    folders = [r for r in env.api.requests if r.url.path.endswith('/folders')]
    assert [json.loads(r.content) for r in folders] == [{'path': 'notes'}, {'path': 'reports/2026'}]
    creates = [i for i, r in enumerate(env.api.requests) if r.method == 'POST' and r.url.path == '/v1/collections']
    assert max(creates) < env.api.requests.index(folders[0])


@pytest.mark.asyncio
async def test_an_owner_who_no_longer_exists_is_reported_by_name(env, capsys):
    """Missing owners are identified by KB id and created without a subject or created_by."""
    assert await run(env) == 0
    kb = env.kbs[2]
    assert f'{kb.id}: owner missing, created without created_by' in capsys.readouterr().out
    assert env.api.collections[kb.id]['created_by'] is None
    request = next(
        r
        for r in env.api.requests
        if r.method == 'POST' and r.url.path == '/v1/collections' and json.loads(r.content)['key'] == kb.id
    )
    assert 'X-Soev-Subject' not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch', ['documents', 'missing', 'conflict', 'extra'])
async def test_reconcile_exits_nonzero_only_on_a_collection_mismatch(env, capsys, monkeypatch, mismatch):
    """Document gaps and extra collections are informational while missing KBs and create conflicts fail."""
    assert await run(env) == 0
    capsys.readouterr()
    kb = env.kbs[0]
    if mismatch == 'missing':
        del env.api.collections[kb.id]
    elif mismatch == 'conflict':
        env.api.replays.clear()
        env.api.creation_bodies[kb.id][0]['name'] = 'Different decisive fields'
        env.api.collections[kb.id]['name'] = 'Different decisive fields'
    elif mismatch == 'extra':
        env.api.collections['extra'] = {**env.api.collections[kb.id], 'key': 'extra'}
    if mismatch == 'missing':
        module = importlib.import_module('open_webui.soev.migrate')
        result = await module.reconcile({kb.id: 5}, env.identity.build_client(), conflicts=set())
    else:
        result = await run(env)
    output = capsys.readouterr().out
    assert result == int(mismatch in ('missing', 'conflict'))
    assert 'OWUI files' in output and 'document_count' in output and kb.id in output
    if mismatch == 'conflict':
        assert f'{kb.id}: collection_exists (409)' in output
        assert env.api.collections[kb.id]['name'] == 'Different decisive fields'
    if mismatch == 'documents':
        assert '5' in output and 'gap' in output


@pytest.mark.asyncio
async def test_external_and_deleted_knowledge_bases_are_skipped(env):
    """External metadata and soft deletion each exclude a KB from creation and reconciliation."""
    assert await run(env) == 0
    bodies = [json.loads(r.content) for r in env.api.requests if r.content and r.url.path == '/v1/collections']
    assert all(body['key'] not in {kb.id for kb in env.kbs[3:]} for body in bodies)


@pytest.mark.asyncio
async def test_dry_run_sends_nothing(env, capsys):
    """Dry-run reads SQL and prints planned writes and reconciliation without minting or sending secrets."""
    assert await run(env, dry_run=True) == 0
    output = capsys.readouterr().out
    assert not env.api.requests and not env.identity._linked_refs
    assert '/v1/identity/links' in output and '/v1/directory/groups/' in output
    assert '/v1/collections' in output and 'reports/2026' in output
    assert 'OWUI files' in output and 'document_count' in output and 'not read' in output
    assert 'test-runtime-key' not in output and 'PRIVATE KEY' not in output and 'eyJ' not in output
    assert env.kbs[3].id not in output and env.kbs[4].id not in output
    assert (
        (await env.db.execute(select(importlib.import_module('open_webui.models.knowledge').Knowledge))).scalars().all()
    )


@pytest.mark.asyncio
async def test_module_dry_run_preserves_sql_tables(env, tmp_path):
    """A fresh CLI process previews seeded SQL without running schema migrations or changing any rows."""
    database_path = tmp_path / 'migration.db'
    connection = await env.db.connection()
    raw = await connection.get_raw_connection()
    with sqlite3.connect(database_path) as snapshot:
        await raw.driver_connection.backup(snapshot)
        before = list(snapshot.iterdump())
    backend = Path(__file__).resolve().parents[3]
    environment = {
        'PATH': os.defpath,
        'PYTHONPATH': str(backend),
        'WEBUI_SECRET_KEY': 't',
        'STATIC_DIR': str(tmp_path / 'static'),
        'DATA_DIR': str(tmp_path / 'data'),
        'DATABASE_URL': f'sqlite:///{database_path}',
        'DATABASE_ENABLE_SQLITE_WAL': 'false',
        'VECTOR_DB': 'weaviate',
        'SOEV_API_URL': 'https://soev.invalid',
        'SOEV_API_SERVICE_PRINCIPAL': 'owui:service:webui',
    }
    for name in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        environment[f'DATABASE_{name}'] = ''
    result = subprocess.run(
        [sys.executable, '-m', 'open_webui.soev.migrate', '--dry-run'],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'KB count: 3' in result.stdout
    assert 'reports/2026' in result.stdout
    assert all(kb.id in result.stdout for kb in env.kbs[:3])
    with sqlite3.connect(database_path) as snapshot:
        assert list(snapshot.iterdump()) == before
