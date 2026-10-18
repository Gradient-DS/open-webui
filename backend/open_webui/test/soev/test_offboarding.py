"""Offboarding sends the platform contract before committing any local erasure."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest_asyncio.fixture
async def offboard_store(identity_config, chat_http, monkeypatch, platform_capabilities, tmp_path):
    from open_webui.internal import db as database
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.chats import Chat
    from open_webui.models.config import Config
    from open_webui.models.knowledge import Knowledge
    from open_webui.models.memories import Memory
    from open_webui.models.models import Model
    from open_webui.models.notes import Note
    from open_webui.models.prompts import Prompt
    from open_webui.models.tools import Tool
    from open_webui.models.user_archives import UserArchive
    from open_webui.models.users import User, Users
    from open_webui.services.deletion import offboarding

    monkeypatch.setattr(
        Config,
        'get',
        AsyncMock(
            side_effect=lambda key: {'admin.enable_user_archival': True, 'admin.default_archive_retention_days': 1095}[
                key
            ]
        ),
    )

    identity, _ = identity_config
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    # Upstream's group hierarchy writes open their own session, so every session must reach the same file.
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/offboarding.db', poolclass=NullPool)
    monkeypatch.setattr(database, 'AsyncSessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        for uid in ['fern', 'lark']:
            await Users.insert_new_user(uid, uid.title(), f'{uid}@example.invalid', role='user', db=db)
        for cls, rid in [
            (Model, 'assistant'),
            (Prompt, 'prompt'),
            (Tool, 'tool'),
            (Note, 'note'),
            (Knowledge, 'native'),
        ]:
            values = {'id': rid, 'user_id': 'fern', 'created_at': 1, 'updated_at': 1}
            if cls is Tool:
                values.update(name='Tool', content='', specs=[], meta={})
            db.add(cls(**values))
        db.add(Chat(id='chat', user_id='fern', title='A conversation', chat={}, created_at=1, updated_at=1))
        db.add(Memory(id='memory', user_id='fern', content='A preference', created_at=1, updated_at=1))
        await db.commit()
        client = identity.build_client()
        await identity.ensure_link('owui:user:fern', client)
        for key, kind in [('native', 'local'), ('cloud', 'onedrive')]:
            await client.send(
                'POST',
                '/v1/collections',
                {
                    'key': key,
                    'name': key,
                    'source_kind': kind,
                    'visibility': 'restricted',
                    'principals': ['owui:user:fern'],
                    'writers': ['owui:user:fern'],
                },
                as_user='owui:user:fern',
                idempotency_key='seed:' + key,
            )
        chat_http.requests.clear()
        monkeypatch.setattr(offboarding, 'delete_external_user_data', AsyncMock())
        yield SimpleNamespace(
            db=db,
            api=chat_http,
            identity=identity,
            module=offboarding,
            classes=(User, Model, Prompt, Tool, Note, Chat, Memory, Knowledge, UserArchive, AccessGrant),
        )
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_modes_move_or_delete_resources_then_unlink(offboard_store, mode):
    store = offboard_store
    options = store.module.OffboardingOptions(
        mode=mode,
        transfer_to='lark' if mode == 'transfer' else None,
        archive_reason='Planned departure',
        archive_retention_days=7,
    )
    await store.module.offboard_user('fern', actor_id='operator', options=options, db=store.db)
    User, Model, Prompt, Tool, Note, Chat, Memory, Knowledge, Archive, Grant = store.classes
    assert await store.db.get(User, 'fern') is None
    assert await store.db.get(Chat, 'chat') is None
    assert await store.db.get(Memory, 'memory') is None
    expected_owner = 'lark' if mode == 'transfer' else 'platform:svc:archive-owui'
    for cls, rid in [(Model, 'assistant'), (Prompt, 'prompt'), (Tool, 'tool'), (Note, 'note'), (Knowledge, 'native')]:
        row = await store.db.get(cls, rid)
        assert row is None if mode == 'delete' else row.user_id == expected_owner
    assert any(r.method == 'DELETE' and r.url.path == '/v1/collections/cloud' for r in store.api.requests)
    owner_calls = [r for r in store.api.requests if r.url.path.endswith('/owner')]
    if mode != 'delete':
        assert len(owner_calls) == 1
        assert 'X-Soev-Subject' not in owner_calls[0].headers
        body = json.loads(owner_calls[0].content)
        assert body['owner'] == ('owui:user:lark' if mode == 'transfer' else expected_owner)
        if mode == 'archive':
            archive = (await store.db.execute(select(Archive))).scalar_one()
            assert body['readers'] == ['owui:group:admins-archive']
            assert body['expires_at'].startswith(
                __import__('datetime')
                .datetime.fromtimestamp(archive.expires_at, __import__('datetime').UTC)
                .strftime('%Y-%m-%d')
            )
            assert archive.data['chats'][0]['id'] == 'chat'
    unlink = [r for r in store.api.requests if '/identity/links/' in r.url.path]
    assert len(unlink) == 1
    assert store.api.requests.index(unlink[0]) > max(
        i for i, r in enumerate(store.api.requests) if r.url.path.startswith('/v1/collections/') and r.method != 'GET'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(('mode', 'method'), [('transfer', 'DELETE'), ('archive', 'POST')])
async def test_chat_attachments_are_never_transferred_to_another_person(offboard_store, mode, method):
    """Transfer deletes the personal attachments collection; archive keeps it beside the chat snapshot."""
    from open_webui.soev import ingest

    store = offboard_store
    key = ingest.attachments_collection_key('fern')
    await store.identity.build_client().send(
        'POST',
        '/v1/collections',
        {
            'key': key,
            'name': key,
            'source_kind': 'local',
            'visibility': 'restricted',
            'principals': ['owui:user:fern'],
            'writers': ['owui:user:fern'],
        },
        as_user='owui:user:fern',
        idempotency_key='seed:' + key,
    )
    store.api.requests.clear()
    options = store.module.OffboardingOptions(mode=mode, transfer_to='lark' if mode == 'transfer' else None)
    await store.module.offboard_user('fern', actor_id='operator', options=options, db=store.db)
    calls = [r for r in store.api.requests if key in r.url.path and r.method != 'GET']
    assert [r.method for r in calls] == [method]


@pytest.mark.asyncio
async def test_inventory_links_an_approved_user_before_listing_as_them(offboard_store):
    """The shared-data listing works for a user whose link is not cached (e.g. a queued retry)."""
    store = offboard_store
    store.identity._linked_refs.clear()
    owned = await store.module.inventory('fern', db=store.db)
    assert {row['key'] for row in owned.collections} == {'native', 'cloud'}
    paths = [(r.method, r.url.path) for r in store.api.requests]
    assert paths.index(('POST', '/v1/identity/links')) < paths.index(('GET', '/v1/collections'))


@pytest.mark.asyncio
async def test_a_shared_assistants_files_collection_is_listed_as_its_assistant_only(offboard_store):
    from open_webui.soev import ingest

    store = offboard_store
    key = ingest.assistant_collection_key('assistant')
    await store.identity.build_client().send(
        'POST',
        '/v1/collections',
        {
            'key': key,
            'name': 'Assistant files',
            'visibility': 'restricted',
            'principals': ['owui:user:fern', 'owui:user:lark'],
            'writers': ['owui:user:fern'],
        },
        as_user='owui:user:fern',
        idempotency_key='seed:' + key,
    )
    owned = await store.module.inventory('fern', db=store.db)
    assert key in {row['key'] for row in owned.collections}
    assert key not in {item['id'] for item in owned.shared}


@pytest.mark.asyncio
async def test_shared_deletion_requires_exact_inventory(offboard_store):
    from fastapi import HTTPException

    store = offboard_store
    store.api.collections['native']['principals'].append('platform:pseudo:owui')
    Grant = store.classes[-1]
    store.db.add(
        Grant(
            id='share',
            resource_type='model',
            resource_id='assistant',
            principal_type='user',
            principal_id='lark',
            permission='write',
            created_at=1,
        )
    )
    await store.db.commit()
    for confirmation in [[], ['native'], ['native', 'assistant', 'unexpected']]:
        with pytest.raises(HTTPException) as error:
            await store.module.offboard_user(
                'fern',
                actor_id='operator',
                options=store.module.OffboardingOptions(confirm_shared_deletion=confirmation),
                db=store.db,
            )
        assert error.value.status_code == 409
        assert sorted(item['id'] for item in error.value.detail['items']) == ['assistant', 'native']
    assert all(r.method == 'GET' for r in store.api.requests)
    await store.module.offboard_user(
        'fern',
        actor_id='operator',
        options=store.module.OffboardingOptions(confirm_shared_deletion=['assistant', 'native']),
        db=store.db,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('step', ['inventory', 'cloud', 'groups', 'resources', 'unlink', 'local'])
async def test_failure_never_partially_deletes_local_data(offboard_store, monkeypatch, step):
    store = offboard_store
    from open_webui.models.groups import Groups

    targets = {
        'inventory': (store.module, 'inventory'),
        'cloud': (store.module, 'revoke_cloud'),
        'groups': (Groups, 'remove_user_from_all_groups'),
        'resources': (store.module, 'apply_collections'),
        'unlink': (store.identity, 'unlink'),
        'local': (store.module, 'finalize_local'),
    }
    obj, name = targets[step]
    monkeypatch.setattr(obj, name, AsyncMock(side_effect=RuntimeError(step)))
    with pytest.raises(RuntimeError, match=step):
        await store.module.offboard_user(
            'fern', actor_id='operator', options=store.module.OffboardingOptions(), db=store.db
        )
    for cls, rid in zip(store.classes[:8], ['fern', 'assistant', 'prompt', 'tool', 'note', 'chat', 'memory', 'native']):
        assert await store.db.get(cls, rid) is not None


@pytest.mark.asyncio
async def test_archive_never_delete_omits_expiry(offboard_store):
    store = offboard_store
    await store.module.offboard_user(
        'fern',
        actor_id='operator',
        options=store.module.OffboardingOptions(mode='archive', archive_reason='Keep', never_delete=True),
        db=store.db,
    )
    request = next(r for r in store.api.requests if r.url.path.endswith('/owner'))
    assert 'expires_at' not in json.loads(request.content)


@pytest.mark.asyncio
async def test_archive_assistant_is_readable_only_by_current_admins(offboard_store):
    from open_webui.models.access_grants import AccessGrantsTable
    from open_webui.models.users import Users

    store = offboard_store
    await Users.insert_new_user('rook', 'Rook', 'rook@example.invalid', role='admin', db=store.db)
    await store.module.offboard_user(
        'fern', actor_id='rook', options=store.module.OffboardingOptions(mode='archive'), db=store.db
    )
    grants = AccessGrantsTable()
    assert await grants.has_access('rook', 'model', 'assistant', 'read', db=store.db)
    assert not await grants.has_access('rook', 'model', 'assistant', 'write', db=store.db)
    assert not await grants.has_access('lark', 'model', 'assistant', 'read', db=store.db)
    await Users.update_user_role_by_id('rook', 'user', db=store.db)
    assert not await grants.has_access('rook', 'model', 'assistant', 'read', db=store.db)


@pytest.mark.asyncio
async def test_final_transaction_rolls_back_already_staged_deletions(offboard_store, monkeypatch):
    store = offboard_store
    finalize = store.module.finalize_local

    async def fail_after_staging(*args, **kwargs):
        await finalize(*args, **kwargs)
        raise RuntimeError('commit failed')

    monkeypatch.setattr(store.module, 'finalize_local', fail_after_staging)
    with pytest.raises(RuntimeError, match='commit failed'):
        await store.module.offboard_user(
            'fern', actor_id='operator', options=store.module.OffboardingOptions(), db=store.db
        )
    for cls, rid in zip(store.classes[:8], ['fern', 'assistant', 'prompt', 'tool', 'note', 'chat', 'memory', 'native']):
        assert await store.db.get(cls, rid) is not None


@pytest.mark.asyncio
async def test_cloud_revocations_act_as_departing_user(offboard_store):
    from open_webui.test.soev.test_identity import claims

    store = offboard_store
    client = store.identity.build_client()
    connection = await client.send(
        'POST',
        '/v1/connections',
        {'source_kind': 'onedrive', 'credential_kind': 'user_oauth'},
        as_user='owui:user:fern',
        idempotency_key='connection:fern',
    )
    schedule = await client.send(
        'POST',
        '/v1/schedules',
        {'connection_id': connection['id'], 'collection_key': 'cloud', 'kind': 'content'},
        as_user='owui:user:fern',
        idempotency_key='schedule:fern',
    )
    store.api.requests.clear()
    await store.module.offboard_user(
        'fern', actor_id='operator', options=store.module.OffboardingOptions(), db=store.db
    )
    mutations = [
        r
        for r in store.api.requests
        if r.method == 'DELETE' and r.url.path.startswith(('/v1/schedules/', '/v1/connections/'))
    ]
    assert len(mutations) == 2
    assert all(claims(r.headers['X-Soev-Subject'])['sub'] == 'owui:user:fern' for r in mutations)
    assert store.api.schedules[schedule['id']]['subscribers'] == []
    assert connection['id'] not in store.api.connections


@pytest.mark.asyncio
async def test_transfer_can_snapshot_chats_without_transferring_them(offboard_store):
    store = offboard_store
    await store.module.offboard_user(
        'fern',
        actor_id='operator',
        options=store.module.OffboardingOptions(mode='transfer', transfer_to='lark', archive_before_delete=True),
        db=store.db,
    )
    Archive = store.classes[-2]
    archive = (await store.db.execute(select(Archive))).scalar_one()
    assert archive.expires_at - archive.created_at == 1095 * 86400
    assert archive.data['chats'][0]['id'] == 'chat'
    assert archive.data['collections'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['fern', 'missing'])
async def test_transfer_refuses_invalid_target_before_remote_changes(offboard_store, target):
    store = offboard_store
    with pytest.raises(store.identity.SoevApiError) as error:
        await store.module.offboard_user(
            'fern',
            actor_id='operator',
            options=store.module.OffboardingOptions(mode='transfer', transfer_to=target),
            db=store.db,
        )
    assert error.value.status == 422
    assert store.api.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path', ['/v1/connections/', '/v1/directory/groups/', '/v1/collections/native', '/v1/identity/links/']
)
async def test_actual_http_failure_keeps_local_source_rows(offboard_store, monkeypatch, path):
    from open_webui.models.groups import GroupForm, GroupTable

    store = offboard_store
    group = await GroupTable().insert_new_group('lark', GroupForm(name='team', description=''), db=store.db)
    await GroupTable().set_group_user_ids_by_id(group.id, ['fern', 'lark'], db=store.db)
    await store.identity.build_client().send(
        'POST',
        '/v1/connections',
        {'source_kind': 'onedrive', 'credential_kind': 'user_oauth'},
        as_user='owui:user:fern',
        idempotency_key='connection:fern',
    )
    original = store.api.handle

    def handle(request):
        if request.method != 'GET' and request.url.path.startswith(path):
            return httpx.Response(
                503, json={'code': 'test_failure'}, headers={'Content-Type': 'application/problem+json'}
            )
        return original(request)

    monkeypatch.setattr(store.api, 'handle', handle)
    from fastapi import HTTPException

    with pytest.raises((store.identity.SoevApiError, HTTPException)):
        await store.module.offboard_user(
            'fern', actor_id='operator', options=store.module.OffboardingOptions(), db=store.db
        )
    for cls, rid in zip(store.classes[:8], ['fern', 'assistant', 'prompt', 'tool', 'note', 'chat', 'memory', 'native']):
        assert await store.db.get(cls, rid) is not None


@pytest.mark.asyncio
async def test_archive_retry_reuses_snapshot_and_original_collection_manifest(offboard_store, monkeypatch):
    store = offboard_store
    unlink = store.identity.unlink
    monkeypatch.setattr(store.identity, 'unlink', AsyncMock(side_effect=RuntimeError('unlink failed')))
    options = store.module.OffboardingOptions(mode='archive', archive_reason='Departure')
    with pytest.raises(RuntimeError):
        await store.module.offboard_user('fern', actor_id='operator', options=options, db=store.db)
    Archive = store.classes[-2]
    first = (await store.db.execute(select(Archive))).scalar_one()
    monkeypatch.setattr(store.identity, 'unlink', unlink)
    await store.module.offboard_user('fern', actor_id='operator', options=options, db=store.db)
    archive = (await store.db.execute(select(Archive))).scalar_one()
    assert archive.id == first.id
    assert archive.data['collections'] == ['native']
    assert archive.data['chats'][0]['id'] == 'chat'


@pytest.mark.asyncio
async def test_archive_cleanup_removes_owned_resources_and_snapshot(offboard_store, monkeypatch):
    from open_webui.services.archival.resources import delete_archive

    store = offboard_store
    await store.module.offboard_user(
        'fern', actor_id='operator', options=store.module.OffboardingOptions(mode='archive'), db=store.db
    )
    Archive = store.classes[-2]
    archive = (await store.db.execute(select(Archive))).scalar_one()
    original = store.api.handle

    def handle(request):
        if request.method == 'DELETE' and request.url.path == '/v1/collections/native':
            store.api.requests.append(request)
            return httpx.Response(204)
        return original(request)

    monkeypatch.setattr(store.api, 'handle', handle)
    assert await delete_archive(archive.id, db=store.db)
    for cls, rid in zip(store.classes[1:5], ['assistant', 'prompt', 'tool', 'note']):
        store.db.expire_all()
        assert await store.db.get(cls, rid) is None
    assert await store.db.get(Archive, archive.id) is None


@pytest.mark.asyncio
async def test_retention_edits_send_the_same_expiry_and_never_delete(offboard_store, monkeypatch):
    from open_webui.models.user_archives import UserArchives
    from open_webui.services.archival.resources import set_expiry

    store = offboard_store
    await store.module.offboard_user(
        'fern', actor_id='operator', options=store.module.OffboardingOptions(mode='archive'), db=store.db
    )
    Archive = store.classes[-2]
    row = (await store.db.execute(select(Archive))).scalar_one()
    archive = await UserArchives.get_archive_by_id(row.id, db=store.db)
    archive.expires_at = archive.created_at + 2 * 86400
    await set_expiry(archive)
    request = store.api.requests[-1]
    assert 'X-Soev-Subject' not in request.headers
    assert json.loads(request.content)['expires_at']
    archive.never_delete = True
    await set_expiry(archive)
    assert 'expires_at' not in json.loads(store.api.requests[-1].content)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_cloud_rows_never_move_and_memories_never_survive(offboard_store, mode):
    from open_webui.models.files import File
    from open_webui.models.knowledge import Knowledge

    store = offboard_store
    store.db.add(Knowledge(id='cloud', user_id='fern', type='onedrive', created_at=1, updated_at=1))
    store.db.add(File(id='local-file', user_id='fern', filename='notes.txt', meta={}, created_at=1, updated_at=1))
    store.db.add(
        File(
            id='cloud-file',
            user_id='fern',
            filename='cloud.txt',
            meta={'source': {'provider': 'onedrive'}},
            created_at=1,
            updated_at=1,
        )
    )
    await store.db.commit()
    await store.module.offboard_user(
        'fern',
        actor_id='operator',
        options=store.module.OffboardingOptions(mode=mode, transfer_to='lark' if mode == 'transfer' else None),
        db=store.db,
    )
    assert await store.db.get(Knowledge, 'cloud') is None
    assert await store.db.get(File, 'cloud-file') is None
    local = await store.db.get(File, 'local-file')
    if mode == 'delete':
        assert local is None
    else:
        assert local.user_id == ('lark' if mode == 'transfer' else 'platform:svc:archive-owui')


@pytest.mark.asyncio
async def test_memory_vector_failure_blocks_all_local_deletion(offboard_store, monkeypatch):
    from open_webui.retrieval.vector import async_client
    from open_webui.services.deletion import offboarding_store as boundary

    store = offboard_store
    monkeypatch.setattr(store.module, 'delete_external_user_data', boundary.delete_external_user_data)
    monkeypatch.setattr(
        async_client,
        'ASYNC_VECTOR_DB_CLIENT',
        SimpleNamespace(has_collection=AsyncMock(side_effect=RuntimeError('vectors offline'))),
    )
    with pytest.raises(RuntimeError, match='vectors offline'):
        await store.module.offboard_user(
            'fern', actor_id='operator', options=store.module.OffboardingOptions(), db=store.db
        )
    for cls, rid in zip(store.classes[:8], ['fern', 'assistant', 'prompt', 'tool', 'note', 'chat', 'memory', 'native']):
        assert await store.db.get(cls, rid) is not None


@pytest.mark.asyncio
async def test_storage_partial_failure_keeps_file_and_user_rows(offboard_store, monkeypatch):
    from open_webui.models.files import File
    from open_webui.retrieval.vector import async_client
    from open_webui.services.deletion import offboarding_store as boundary
    from open_webui.storage import provider

    store = offboard_store
    store.db.add(
        File(
            id='paper',
            user_id='fern',
            filename='paper.txt',
            path='/fictional/paper.txt',
            meta={},
            created_at=1,
            updated_at=1,
        )
    )
    await store.db.commit()
    monkeypatch.setattr(store.module, 'delete_external_user_data', boundary.delete_external_user_data)
    monkeypatch.setattr(
        async_client, 'ASYNC_VECTOR_DB_CLIENT', SimpleNamespace(has_collection=AsyncMock(return_value=False))
    )
    monkeypatch.setattr(provider.Storage, 'delete_files', lambda paths: 0)
    with pytest.raises(store.identity.SoevApiError):
        await store.module.offboard_user(
            'fern', actor_id='operator', options=store.module.OffboardingOptions(), db=store.db
        )
    assert await store.db.get(File, 'paper') is not None
    assert await store.db.get(store.classes[0], 'fern') is not None


@pytest.mark.asyncio
async def test_scim_deletion_does_not_erase_threads_before_shared_confirmation(monkeypatch):
    from fastapi import HTTPException
    from open_webui.routers import scim

    user = SimpleNamespace(id='fern', email='fern@example.invalid', role='user')
    monkeypatch.setattr(scim.Users, 'get_scim_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(
        scim.Users, 'delete_user_by_id', AsyncMock(side_effect=HTTPException(409, 'confirmation required'))
    )
    threads = AsyncMock()
    monkeypatch.setattr(scim, 'delete_threads', threads, raising=False)
    monkeypatch.setattr(scim, 'user_thread_ids', AsyncMock(return_value={'thread'}), raising=False)
    with pytest.raises(HTTPException):
        await scim.delete_user('fern', request=SimpleNamespace(), db=SimpleNamespace())
    threads.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_pending_offboarding_uses_service_without_recreating_a_link(offboard_store, monkeypatch, mode):
    from open_webui.models.users import Users

    store = offboard_store
    await Users.update_user_role_by_id('fern', 'pending', db=store.db)
    monkeypatch.setattr(
        store.identity, 'ensure_link', AsyncMock(side_effect=AssertionError('Pending users stay unlinked'))
    )
    responses = [
        httpx.Response(200, json={'data': list(store.api.collections.values()), 'next_cursor': None}),
        httpx.Response(204),
        httpx.Response(204 if mode == 'delete' else 200, json={}),
        httpx.Response(204),
    ]
    store.api.requests.clear()

    def handle(request):
        store.api.requests.append(request)
        return responses.pop(0)

    monkeypatch.setattr(store.api, 'handle', handle)
    await store.module.offboard_user(
        'fern',
        actor_id='operator',
        options=store.module.OffboardingOptions(mode=mode, transfer_to='lark' if mode == 'transfer' else None),
        db=store.db,
    )
    assert not responses
    assert all('X-Soev-Subject' not in request.headers for request in store.api.requests)
    assert store.api.requests[0].url.path == '/v1/collections'
    assert not any(request.url.path.startswith(('/v1/schedules', '/v1/connections')) for request in store.api.requests)
    assert store.api.requests[-1].url.path.startswith('/v1/identity/links/')
    assert await store.db.get(store.classes[0], 'fern') is None
    model = await store.db.get(store.classes[1], 'assistant')
    if mode == 'delete':
        assert model is None
    else:
        assert model.user_id == ('lark' if mode == 'transfer' else 'platform:svc:archive-owui')


async def seed_cloud_custody(store):
    client = store.identity.build_client()
    connection = await client.send(
        'POST',
        '/v1/connections',
        {'source_kind': 'onedrive', 'credential_kind': 'user_oauth'},
        as_user='owui:user:fern',
        idempotency_key='connection:fern',
    )
    schedule = await client.send(
        'POST',
        '/v1/schedules',
        {'connection_id': connection['id'], 'collection_key': 'cloud', 'kind': 'content'},
        as_user='owui:user:fern',
        idempotency_key='schedule:fern',
    )
    return connection['id'], schedule['id']


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize('method', ['role', 'update'])
async def test_demotion_revokes_cloud_before_unlink_and_role_commit(offboard_store, monkeypatch, role, method):
    from open_webui.models.users import Users
    from open_webui.test.soev.test_identity import claims

    store = offboard_store
    await Users.update_user_role_by_id('fern', role, db=store.db)
    connection_id, schedule_id = await seed_cloud_custody(store)
    store.api.requests.clear()
    original = store.api.handle
    observed_roles = []

    async def handle(request):
        observed_roles.append((await Users.get_user_by_id('fern', db=store.db)).role)
        return original(request)

    monkeypatch.setattr(store.api, 'handle', handle)
    if method == 'role':
        await Users.update_user_role_by_id('fern', 'pending', db=store.db)
    else:
        await Users.update_user_by_id('fern', {'role': 'pending', 'name': 'New name'}, db=store.db)
    assert observed_roles and set(observed_roles) == {role}
    assert store.api.requests[0].url.path == '/v1/schedules'
    deletes = [request for request in store.api.requests if request.method == 'DELETE']
    assert [request.url.path for request in deletes] == [
        f'/v1/schedules/{schedule_id}',
        f'/v1/connections/{connection_id}',
        '/v1/identity/links/' + store.identity.platform_user_id('owui:user:fern'),
    ]
    assert all(claims(request.headers['X-Soev-Subject'])['sub'] == 'owui:user:fern' for request in deletes[:2])
    assert 'X-Soev-Subject' not in deletes[-1].headers
    assert connection_id not in store.api.connections
    assert store.api.schedules[schedule_id]['subscribers'] == []
    assert 'owui:user:fern' not in store.api.links
    assert (await Users.get_user_by_id('fern', db=store.db)).role == 'pending'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['schedule', 'connection', 'unlink'])
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_demotion_revocation_failures_preserve_local_role_and_link(offboard_store, monkeypatch, failure, role):
    from open_webui.models.users import Users

    store = offboard_store
    await Users.update_user_role_by_id('fern', role, db=store.db)
    connection_id, schedule_id = await seed_cloud_custody(store)
    original = store.api.handle
    path = {
        'schedule': f'/v1/schedules/{schedule_id}',
        'connection': f'/v1/connections/{connection_id}',
        'unlink': '/v1/identity/links/' + store.identity.platform_user_id('owui:user:fern'),
    }[failure]
    failed = []

    def handle(request):
        if request.method == 'DELETE' and request.url.path == path:
            failed.append(request)
            return httpx.Response(503, json={'code': 'service_unavailable'})
        return original(request)

    monkeypatch.setattr(store.api, 'handle', handle)
    with pytest.raises(store.identity.SoevApiError):
        await Users.update_user_by_id('fern', {'role': 'pending', 'name': 'New name'}, db=store.db)
    assert failed
    user = await Users.get_user_by_id('fern', db=store.db)
    assert user.role == role and user.name == 'Fern'
    assert 'owui:user:fern' in store.api.links
    assert not any(request.url.path.startswith('/v1/identity/links/') for request in store.api.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize('step', ['inventory', 'groups', 'resources', 'unlink', 'local'])
async def test_pending_offboarding_keeps_fail_closed_boundaries(offboard_store, monkeypatch, step):
    from open_webui.models.groups import Groups
    from open_webui.models.users import Users

    store = offboard_store
    await Users.update_user_role_by_id('fern', 'pending', db=store.db)
    monkeypatch.setattr(
        store.identity, 'ensure_link', AsyncMock(side_effect=AssertionError('Pending users stay unlinked'))
    )
    store.api.requests.clear()
    failure = store.identity.SoevApiError(503, 'service_unavailable', 'Unavailable')
    if step == 'groups':
        monkeypatch.setattr(Groups, 'remove_user_from_all_groups', AsyncMock(side_effect=failure))
    if step == 'local':
        finalize = store.module.finalize_local

        async def fail_after_staging(*args, **kwargs):
            await finalize(*args, **kwargs)
            raise failure

        monkeypatch.setattr(store.module, 'finalize_local', fail_after_staging)

    def handle(request):
        store.api.requests.append(request)
        path = request.url.path
        if (
            step == 'inventory'
            or (step == 'resources' and request.method == 'DELETE' and path.startswith('/v1/collections/'))
            or (step == 'unlink' and path.startswith('/v1/identity/links/'))
        ):
            return httpx.Response(503, json={'code': 'service_unavailable'})
        if request.method == 'GET':
            return httpx.Response(200, json={'data': list(store.api.collections.values()), 'next_cursor': None})
        return httpx.Response(204)

    monkeypatch.setattr(store.api, 'handle', handle)
    with pytest.raises(store.identity.SoevApiError):
        await store.module.offboard_user(
            'fern', actor_id='operator', options=store.module.OffboardingOptions(), db=store.db
        )
    assert all('X-Soev-Subject' not in request.headers for request in store.api.requests)
    assert not any(request.url.path.startswith(('/v1/schedules', '/v1/connections')) for request in store.api.requests)
    for cls, rid in zip(store.classes[:8], ['fern', 'assistant', 'prompt', 'tool', 'note', 'chat', 'memory', 'native']):
        assert await store.db.get(cls, rid) is not None
