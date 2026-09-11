"""Knowledge store behavior against recorded HTTP and isolated OWUI file rows."""

import importlib
import inspect
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from open_webui.test.soev.fake_api import FakeSoevApi
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SERVICE = 'owui:service:webui'
SOEV = {
    'insert_new_knowledge',
    'get_knowledge_bases',
    'search_knowledge_bases',
    'search_knowledge_files',
    'check_access_by_user_id',
    'get_knowledge_bases_by_type',
    'get_knowledge_bases_by_user_id',
    'get_knowledge_items_by_user_id',
    'get_knowledge_by_id',
    'get_knowledge_by_id_and_user_id',
    'get_knowledge_by_file_id',
    'get_knowledges_by_file_id',
    'get_knowledge_files_by_file_id',
    'get_referenced_file_ids',
    'get_file_counts_by_knowledge_ids',
    'search_files_by_id',
    'get_files_by_id',
    'get_file_metadatas_by_id',
    'has_file',
    'remove_file_from_knowledge_by_id',
    'reset_knowledge_by_id',
    'update_knowledge_by_id',
    'delete_knowledge_by_id',
    'delete_all_knowledge',
    'soft_delete_by_id',
    'soft_delete_by_user_id',
    'get_knowledge_by_id_unfiltered',
    'create_directory',
    'get_directories',
    'get_all_directories',
    'get_files_with_directory_ids',
    'get_directory_rollups',
    'get_file_ids_in_directory_subtree',
    'get_directory_by_id',
    'get_directory_breadcrumbs',
    'rename_directory',
    'move_directory',
    'update_directory',
    'delete_directory',
    'move_file_to_directory',
}
VACUOUS = {
    'get_pending_deletions',
    'get_stale_knowledge',
    'get_suspended_expired_knowledge',
    'is_suspended',
    'get_suspension_info',
}
REFUSED = {
    'add_file_to_knowledge_by_id',
    'set_path_fields_by_file_id',
    'update_knowledge_meta_by_id',
    'update_knowledge_data_by_id',
    'update_knowledge_user_id_by_id',
}


@pytest_asyncio.fixture
async def env(identity_config, monkeypatch):
    """Use the real client and Files methods without creating any local knowledge or grant tables."""
    identity, _ = identity_config
    module = importlib.import_module('open_webui.soev.knowledge_store')
    models = importlib.import_module('open_webui.models.knowledge')
    files = importlib.import_module('open_webui.models.files')
    database = importlib.import_module('open_webui.internal.db')
    projection = importlib.import_module('open_webui.soev.projection')
    api = FakeSoevApi(page_size=1)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(api.handle), **kwargs),
    )
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(files.File.__table__.create)
        await connection.run_sync(models.User.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    monkeypatch.setattr(database, 'get_async_db_context', context)
    monkeypatch.setattr(files, 'get_async_db_context', context)
    users = importlib.import_module('open_webui.models.users')
    monkeypatch.setattr(users, 'get_async_db_context', context)
    forbidden = AsyncMock(side_effect=AssertionError('Knowledge access must not read local grants or groups'))
    monkeypatch.setattr(models.AccessGrants, 'has_access', forbidden)
    monkeypatch.setattr(models.AccessGrants, 'get_grants_by_resource', forbidden)
    monkeypatch.setattr(models.Groups, 'get_groups_by_member_id', forbidden)
    client = identity.build_client()
    store = module.SoevKnowledgeTable(client=client, service_principal=SERVICE)
    state = SimpleNamespace(
        store=store,
        api=api,
        client=client,
        identity=identity,
        models=models,
        files=files,
        projection=projection,
        module=module,
        sessions=sessions,
        engine=engine,
    )
    await seed(state)
    api.requests.clear()
    yield state
    await engine.dispose()


async def seed(env, key='kb', owner='alice', name='Research'):
    ref = f'owui:user:{owner}'
    await env.identity.ensure_link(ref, env.client)
    await env.client.send(
        'POST',
        '/v1/collections',
        {
            'key': key,
            'name': name,
            'description': 'Notes',
            'visibility': 'restricted',
            'principals': [SERVICE],
            'writers': [],
        },
        as_user=ref,
        idempotency_key=f'collection:{key}',
    )


async def file(env, source='f1', path=None, key='kb', **meta):
    env.api.add_document(key, source, path=path)
    async with env.sessions() as session:
        session.add(
            env.files.File(
                id=source,
                user_id='alice',
                filename=source + '.txt',
                hash='hash',
                data={'content': 'needle ' + 'large text ' * 100},
                meta=meta,
                created_at=1,
                updated_at=2,
            )
        )
        await session.commit()


def test_every_knowledge_table_method_is_classified(env):
    """Every upstream public coroutine belongs to exactly one explicit group and none can be inherited."""
    methods = {
        name
        for name, value in inspect.getmembers(env.models.KnowledgeTable, inspect.iscoroutinefunction)
        if not name.startswith('_')
    }
    assert not SOEV & VACUOUS and not SOEV & REFUSED and not VACUOUS & REFUSED
    assert methods == SOEV | VACUOUS | REFUSED
    assert not issubclass(type(env.store), env.models.KnowledgeTable)
    assert methods <= set(type(env.store).__dict__)


def test_signatures_accept_every_argument_callers_pass(env):
    """Store methods preserve all upstream positional and keyword arguments and their defaults."""
    for name in SOEV | VACUOUS | REFUSED:
        original = inspect.signature(getattr(env.models.KnowledgeTable, name))
        replacement = inspect.signature(getattr(type(env.store), name))
        names = list(original.parameters)
        assert list(replacement.parameters)[: len(names)] == names, name
        for key, parameter in original.parameters.items():
            assert replacement.parameters[key].kind == parameter.kind, (name, key)
            assert replacement.parameters[key].default == parameter.default, (name, key)


@pytest.mark.asyncio
async def test_get_knowledge_by_id_reads_as_the_service_principal(env):
    """An identity-free lookup uses the credential, returns the projection, and preserves 404 as None."""
    model = await env.store.get_knowledge_by_id('kb', db=object())
    assert model.id == 'kb' and model.user_id == 'alice'
    assert model == env.projection.knowledge_of(
        env.api._view(env.api.collections['kb'], None), service_principal=SERVICE
    )
    assert all('X-Soev-Subject' not in request.headers for request in env.api.requests)
    assert await env.store.get_knowledge_by_id('absent') is None


@pytest.mark.asyncio
async def test_search_knowledge_bases_reads_under_the_users_assertion(env):
    """Search uses explicit identity over request context, filters, sorts, and counts before pagination."""
    acting = importlib.import_module('open_webui.soev.acting')
    await seed(env, 'other', owner='bob', name='Private')
    await seed(env, 'second', name='Alpha')
    token = acting._acting_ref.set('owui:user:bob')
    try:
        result = await env.store.search_knowledge_bases('alice', {'order_by': 'name', 'direction': 'asc'}, limit=1)
    finally:
        acting._acting_ref.reset(token)
    assert result.total == 2 and [row.id for row in result.items] == ['second']
    assert (await env.store.search_knowledge_bases('alice', {'query': 'research'})).total == 1
    assert (await env.store.search_knowledge_bases('alice', {'view_option': 'shared'})).total == 0
    assert (await env.store.search_knowledge_bases('alice', {'type': 'remote'})).total == 0


@pytest.mark.asyncio
async def test_insert_sends_readers_writers_and_the_subject(env):
    """An explicit creator is linked before collection creation and reaches both the owner and writer projection."""
    form = env.models.KnowledgeForm(
        name='New',
        description='',
        access_grants=[{'principal_type': 'group', 'principal_id': 'staff', 'permission': 'write'}],
    )
    result = await env.store.insert_new_knowledge('carol', form)
    assert result.user_id == 'carol'
    row = env.api.collections[result.id]
    assert row['principals'] == [SERVICE, 'owui:user:carol']
    assert row['writers'] == ['owui:group:staff', 'owui:user:carol']
    relevant = [request for request in env.api.requests if request.method == 'POST']
    assert relevant[-2].url.path == '/v1/identity/links'
    assert relevant[-1].url.path == '/v1/collections'


@pytest.mark.asyncio
async def test_create_replays_with_the_collection_key(env, monkeypatch):
    """Retrying creation with the same generated collection identity replays the original response."""
    monkeypatch.setattr(env.module, 'uuid4', lambda: 'retry-collection')
    form = env.models.KnowledgeForm(name='Retry', description='')
    first = await env.store.insert_new_knowledge('alice', form)
    second = await env.store.insert_new_knowledge('alice', form)
    requests = [r for r in env.api.requests if r.url.path == '/v1/collections']
    assert first == second
    assert [r.headers['Idempotency-Key'] for r in requests] == ['kb:retry-collection'] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['delete_knowledge_by_id', 'soft_delete_by_id', 'soft_delete_by_user_id'])
async def test_collection_deletion_retry_commissions_one_job(env, method, monkeypatch):
    """All collection deletion entry points reuse the collection's deletion identity."""
    monkeypatch.setattr(env.module, 'acting_ref', lambda: 'owui:user:alice')
    argument = 'alice' if method == 'soft_delete_by_user_id' else 'kb'
    await getattr(env.store, method)(argument)
    await env.store.delete_knowledge_by_id('kb')
    requests = [r for r in env.api.requests if r.method == 'DELETE']
    assert requests[0].headers['Idempotency-Key'] == 'kb-delete:kb'
    assert requests[-1].headers['Idempotency-Key'] == 'kb-delete:kb'
    assert len(env.api.jobs) == 1


@pytest.mark.asyncio
async def test_document_deletion_retry_commissions_one_job(env):
    """A repeated document deletion replays its job without affecting another source."""
    env.api.add_document('kb', 'first')
    env.api.add_document('kb', 'second')
    for source in ('first', 'first', 'second'):
        await env.store.remove_file_from_knowledge_by_id('kb', source)
    requests = [r for r in env.api.requests if r.method == 'DELETE']
    assert [r.headers['Idempotency-Key'] for r in requests] == [
        'doc-delete:kb:first',
        'doc-delete:kb:first',
        'doc-delete:kb:second',
    ]
    assert len(env.api.jobs) == 2


@pytest.mark.asyncio
async def test_access_and_patch_replay_by_sorted_body(env):
    """Equivalent bodies replay, while changed bodies and different routes retain distinct identities."""
    form = env.models.KnowledgeForm(name='Renamed', description='New', access_grants=[])
    for _ in range(2):
        await env.store.update_knowledge_by_id('kb', form)
    writes = [r for r in env.api.requests if r.method in ('PATCH', 'PUT')]
    assert writes[0].headers['Idempotency-Key'] == writes[2].headers['Idempotency-Key']
    assert writes[1].headers['Idempotency-Key'] == writes[3].headers['Idempotency-Key']
    assert len(env.api.jobs) == 1
    reversed_body = dict(reversed(list(json.loads(writes[0].content).items())))
    await env.store._send('PATCH', '/v1/collections/kb', reversed_body)
    assert env.api.requests[-1].headers['Idempotency-Key'] == writes[0].headers['Idempotency-Key']
    await env.store.update_knowledge_by_id('kb', form.model_copy(update={'name': 'Different'}))
    patches = [r for r in env.api.requests if r.method == 'PATCH']
    assert patches[-1].headers['Idempotency-Key'] != patches[0].headers['Idempotency-Key']
    await seed(env, key='other')
    await env.store.update_knowledge_by_id('other', form)
    assert len({r.headers['Idempotency-Key'] for r in env.api.requests if r.method == 'PUT'}) == 2


@pytest.mark.asyncio
async def test_folder_and_document_moves_replay_by_paths(env):
    """Folder creation, movement, deletion and document movement retain their path identities on retry."""
    for _ in range(2):
        folder = await env.store.create_directory('kb', 'Folder', 'alice')
    env.api.add_document('kb', 'source')
    for _ in range(2):
        assert await env.store.move_file_to_directory('kb', 'source', folder.id)
    for _ in range(2):
        renamed = await env.store.rename_directory(folder.id, 'Renamed')
        assert renamed.name == 'Renamed'
    for _ in range(2):
        assert await env.store.move_file_to_directory('kb', 'source')
    for _ in range(2):
        assert await env.store.delete_directory(renamed.id)
    writes = [r for r in env.api.requests if '/folders' in r.url.path or '/move' in r.url.path]
    writes = [r for r in writes if r.method != 'GET']
    assert len(writes) == 10
    keys = [r.headers['Idempotency-Key'] for r in writes]
    assert keys[::2] == keys[1::2]
    assert len(set(keys)) == 5


@pytest.mark.asyncio
async def test_update_patches_and_regrants_through_the_access_job(env):
    """Request identity reaches writes, preserving owner authority and read ACLs until job completion."""
    acting = importlib.import_module('open_webui.soev.acting')
    token = acting._acting_ref.set('owui:user:alice')
    form = env.models.KnowledgeForm(
        name='Renamed',
        description='New',
        access_grants=[
            {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'},
            {'principal_type': 'user', 'principal_id': 'bob', 'permission': 'write'},
        ],
    )
    try:
        result = await env.store.update_knowledge_by_id('kb', form)
    finally:
        acting._acting_ref.reset(token)
    assert result.name == 'Renamed'
    assert env.api.collections['kb']['writers'] == ['owui:user:alice', 'owui:user:bob']
    assert env.api.collections['kb']['visibility'] == 'restricted'
    job = next(iter(env.api.jobs.values()))
    assert job['kind'] == 'update_collection_access' and job['status'] == 'QUEUED'
    assert all('X-Soev-Subject' in r.headers for r in env.api.requests if r.method in ('PATCH', 'PUT'))
    env.api.advance(job['job_id'], 'SUCCEEDED')
    assert env.api.collections['kb']['visibility'] == 'public'


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['delete_knowledge_by_id', 'soft_delete_by_id'])
async def test_delete_commissions_a_job_and_the_list_hides_it(env, method):
    """All active deletion states hide a collection across new store instances, while failed jobs reveal it again."""
    assert await getattr(env.store, method)('kb') is True
    job = next(iter(env.api.jobs.values()))
    assert job['kind'] == 'delete_collection'
    store = env.module.SoevKnowledgeTable(client=env.client, service_principal=SERVICE)
    for status in ('QUEUED', 'RUNNING', 'AWAITING_UPLOAD'):
        env.api.advance(job['job_id'], status)
        assert await store.get_knowledge_bases() == []
    assert (await store.get_knowledge_by_id_unfiltered('kb')).id == 'kb'
    env.api.advance(job['job_id'], 'FAILED')
    assert [row.id for row in await store.get_knowledge_bases()] == ['kb']
    queries = [r.url.params['status'] for r in env.api.requests if r.url.path == '/v1/jobs']
    assert queries[:3] == ['QUEUED', 'RUNNING', 'AWAITING_UPLOAD']


@pytest.mark.asyncio
async def test_collection_variants_use_api_authority_and_owner_filters(env):
    """Collection lookup variants preserve write checks, local type, owned-only lists, and explicit-user precedence."""
    await seed(env, 'other', owner='bob')
    env.api.collections['kb']['principals'].append('owui:user:bob')
    assert await env.store.check_access_by_user_id('kb', 'bob', 'read') is True
    assert await env.store.check_access_by_user_id('kb', 'bob', 'write') is False
    assert await env.store.get_knowledge_by_id_and_user_id('kb', 'bob') is None
    assert (await env.store.get_knowledge_by_id_and_user_id('kb', 'alice')).id == 'kb'
    assert [r.id for r in await env.store.get_knowledge_items_by_user_id('bob')] == ['other']
    assert {r.id for r in await env.store.get_knowledge_bases_by_user_id('bob', 'read')} == {'kb', 'other'}
    assert [r.id for r in await env.store.get_knowledge_bases_by_user_id('bob')] == ['other']
    assert len(await env.store.get_knowledge_bases_by_type('local')) == 2
    assert await env.store.get_knowledge_bases_by_type('confluence') == []


@pytest.mark.asyncio
async def test_bulk_deletes_commission_each_owned_or_visible_collection(env):
    """Owner deletion counts its jobs, and deletion of all visible collections uses the remaining API catalog."""
    await seed(env, 'other', owner='bob')
    assert await env.store.soft_delete_by_user_id('alice') == 1
    assert await env.store.delete_all_knowledge() is True
    assert {j['collection_key'] for j in env.api.jobs.values()} == {'kb', 'other'}


@pytest.mark.asyncio
async def test_collection_lists_preserve_owner_display_and_timestamp_order(env):
    """Lists retain available owner details and sort RFC 3339 values by time rather than offset text."""
    async with env.sessions() as session:
        session.add(
            env.models.User(
                id='alice',
                name='Alice',
                email='alice@example.invalid',
                role='user',
                last_active_at=0,
                created_at=0,
                updated_at=0,
            )
        )
        await session.commit()
    await seed(env, 'second', name='Alpha')
    env.api.collections['kb']['updated_at'] = '2026-09-11T10:00:00-03:00'
    env.api.collections['second']['updated_at'] = '2026-09-11T12:00:00Z'
    rows = await env.store.get_knowledge_bases()
    assert [row.id for row in rows] == ['kb', 'second']
    assert rows[0].user.name == 'Alice'
    search = await env.store.search_knowledge_bases('alice', {'query': 'alice@example.invalid'})
    assert search.total == 2


@pytest.mark.asyncio
async def test_request_identity_cannot_fall_back_to_capability_only_writes(env):
    """An authenticated non-writer is refused, while a background write deliberately carries no subject."""
    acting = importlib.import_module('open_webui.soev.acting')
    env.api.collections['kb']['principals'].append('owui:user:bob')
    token = acting._acting_ref.set('owui:user:bob')
    try:
        with pytest.raises(Exception) as caught:
            await env.store.update_knowledge_by_id('kb', env.models.KnowledgeForm(name='Refused', description=''))
        assert caught.value.status == 403
    finally:
        acting._acting_ref.reset(token)
    assert 'owui:user:bob' in env.api.links
    assert env.api.requests[0].url.path == '/v1/identity/links'
    env.api.requests.clear()
    result = await env.store.update_knowledge_by_id('kb', env.models.KnowledgeForm(name='Worker', description=''))
    assert result.name == 'Worker'
    assert all('X-Soev-Subject' not in r.headers for r in env.api.requests)


@pytest.mark.asyncio
async def test_get_files_by_id_returns_owui_file_rows_by_source_id(env, monkeypatch):
    """Document identities select OWUI file rows even when the wire filename differs."""
    await file(env)
    env.api.documents['kb', 'f1']['filename'] = 'remote-name.pdf'
    original = env.files.Files.get_files_by_ids
    lookup = AsyncMock(wraps=original)
    monkeypatch.setattr(env.files.Files, 'get_files_by_ids', lookup)
    result = await env.store.get_files_by_id('kb')
    assert [row.filename for row in result] == ['f1.txt']
    lookup.assert_awaited_once_with(['f1'])
    assert [row.id for row in await env.store.get_file_metadatas_by_id('kb')] == ['f1']
    assert await env.store.get_file_counts_by_knowledge_ids(['kb', 'absent']) == {'kb': 1}


@pytest.mark.asyncio
async def test_file_reference_methods_use_document_membership(env):
    """Every reference helper derives membership from the API rather than a local knowledge_file row."""
    await file(env, path='a')
    assert await env.store.has_file('kb', 'f1') is True
    assert await env.store.has_file('kb', 'absent') is False
    assert (await env.store.get_knowledge_by_file_id('f1')).id == 'kb'
    assert [row.id for row in await env.store.get_knowledges_by_file_id('f1')] == ['kb']
    links = await env.store.get_knowledge_files_by_file_id('f1')
    assert [(row.knowledge_id, row.file_id) for row in links] == [('kb', 'f1')]
    assert links[0].directory_id == env.projection.directory_id('kb', ('a',))
    assert await env.store.get_referenced_file_ids(['f1', 'absent']) == {'f1'}
    pairs = await env.store.get_files_with_directory_ids('kb')
    assert [(row.id, directory) for row, directory in pairs] == [('f1', links[0].directory_id)]


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata_only', [True, False])
async def test_search_files_preserves_directory_filter_and_slim_rows(env, metadata_only):
    """Absent, null, and populated directory filters differ, and both search paths avoid loading File.data."""
    await file(env, 'root', status='completed')
    await file(env, 'nested', path='a', status='processing')
    await file(env, 'deep', path='a/b', status='error')
    statements = []
    event.listen(
        env.engine.sync_engine,
        'before_cursor_execute',
        lambda conn, cursor, statement, params, ctx, many: statements.append(statement),
    )
    result = await env.store.search_files_by_id('kb', 'alice', {}, limit=1, metadata_only=metadata_only)
    assert result.total == 3 and len(result.items) == 1
    assert [row.name for row in result.directories] == ['a']
    assert result.directories[0].child_count == 2
    assert result.directories[0].status_counts == {'pending': 1, 'failed': 1, 'completed': 0, 'unknown': 0}
    assert all('file.data' not in statement for statement in statements)
    assert all(row.model_dump().get('data') is None for row in result.items)
    root = await env.store.search_files_by_id('kb', 'alice', {'directory_id': None}, metadata_only=metadata_only)
    assert [row.id for row in root.items] == ['root']
    folder = env.projection.directory_id('kb', ('a',))
    nested = await env.store.search_files_by_id('kb', 'alice', {'directory_id': folder}, metadata_only=metadata_only)
    assert [row.id for row in nested.items] == ['nested']
    assert [row.name for row in nested.breadcrumbs] == ['a']
    assert [row.name for row in nested.directories] == ['b']
    if metadata_only:
        assert nested.items[0].status == 'processing' and nested.items[0].error is None
        assert 'data' not in nested.items[0].model_dump()
    assert (await env.store.search_files_by_id('kb', 'alice', {'query': 'needle'})).total == 0
    assert (await env.store.search_files_by_id('kb', 'alice', {'query': 'needle', 'include_content': True})).total == 3
    empty = await env.store.search_knowledge_files({'query': 'needle'})
    assert empty.model_dump() == {'items': [], 'directories': [], 'breadcrumbs': [], 'total': 0}


@pytest.mark.asyncio
async def test_create_directory_is_a_folder_mkdir(env):
    """Folder creation and the private materialization helper use mkdir and reversible parent ids."""
    first = await env.store.create_directory('kb', 'a', 'alice')
    second = await env.store.create_directory('kb', 'b', 'alice', first.id)
    assert second.id == env.projection.directory_id('kb', ('a', 'b'))
    assert [d.name for d in await env.store.get_directories('kb')] == ['a']
    assert [d.name for d in await env.store.get_directories('kb', first.id)] == ['b']
    assert {d.name for d in await env.store.get_all_directories('kb')} == {'a', 'b'}
    assert (await env.store.get_directory_by_id(second.id)).parent_id == first.id
    assert [d.id for d in await env.store.get_directory_breadcrumbs(second.id)] == [first.id, second.id]
    assert await env.store._find_or_create_directory(None, 'kb', first.id, 'b', 'alice') == second.id


@pytest.mark.asyncio
async def test_move_file_to_directory_is_a_document_move(env):
    """File moves send the folder path and subtree lookup excludes similarly prefixed siblings."""
    await file(env, path='ab')
    directory = await env.store.create_directory('kb', 'a', 'alice')
    assert await env.store.get_file_ids_in_directory_subtree('kb', directory.id) == []
    assert await env.store.move_file_to_directory('kb', 'f1', directory.id) is True
    assert env.api.documents['kb', 'f1']['path'] == 'a'
    assert await env.store.get_file_ids_in_directory_subtree('kb', directory.id) == ['f1']
    assert await env.store.move_file_to_directory('kb', 'f1') is True
    assert env.api.documents['kb', 'f1']['path'] is None


@pytest.mark.asyncio
async def test_rename_move_and_update_directory_return_the_new_identity(env):
    """Renaming and moving use one folder move, return new ids, and reject cross-collection parents and cycles."""
    await file(env, path='a/child')
    first = env.projection.directory_id('kb', ('a',))
    renamed = await env.store.rename_directory(first, 'renamed')
    assert renamed.name == 'renamed'
    parent = await env.store.create_directory('kb', 'parent', 'alice')
    moved = await env.store.move_directory(renamed.id, parent.id)
    assert env.projection.directory_of(moved.id) == ('kb', ('parent', 'renamed'))
    updated = await env.store.update_directory(moved.id, name='final', parent_id=None)
    assert updated.parent_id is None and updated.name == 'final'
    assert env.api.documents['kb', 'f1']['path'] == 'final/child'
    child = env.projection.directory_id('kb', ('final', 'child'))
    assert await env.store.move_directory(updated.id, child) is None
    assert await env.store.move_directory(updated.id, env.projection.directory_id('other', ('folder',))) is None


@pytest.mark.asyncio
async def test_delete_directory_moves_direct_files_and_handles_queued_deletion(env):
    """Deletion returns False until queued jobs land and never moves nested files implicitly."""
    await file(env, path='a')
    folder = env.projection.directory_id('kb', ('a',))
    assert await env.store.delete_directory(folder) is True
    assert env.api.documents['kb', 'f1']['path'] is None
    await file(env, 'nested', path='b/child')
    folder = env.projection.directory_id('kb', ('b',))
    assert await env.store.delete_directory(folder) is False
    assert env.api.documents['kb', 'nested']['path'] == 'b/child'
    assert await env.store.delete_directory(folder, move_files_to_parent=False) is False
    jobs = [j for j in env.api.jobs.values() if j['kind'] == 'delete_document']
    assert len(jobs) == 1
    env.api.advance(jobs[0]['job_id'], 'SUCCEEDED')
    assert await env.store.delete_directory(env.projection.directory_id('kb', ('b', 'child'))) is True
    assert await env.store.delete_directory(folder, move_files_to_parent=False) is True


@pytest.mark.asyncio
async def test_removing_and_resetting_files_commissions_document_jobs(env):
    """Removing a file and resetting a collection use document deletions and keep the collection itself."""
    await file(env)
    assert await env.store.remove_file_from_knowledge_by_id('kb', 'f1') is True
    assert next(iter(env.api.jobs.values()))['kind'] == 'delete_document'
    env.api.advance(next(iter(env.api.jobs)), 'SUCCEEDED')
    await file(env, 'second')
    result = await env.store.reset_knowledge_by_id('kb', include_directories=False)
    assert result.id == 'kb'
    assert all(job['kind'] == 'delete_document' for job in env.api.jobs.values())


@pytest.mark.asyncio
async def test_reset_preserves_the_original_failure_result_until_folders_are_empty(env):
    """A reset returns None for a still-occupied folder and can finish after document jobs land."""
    await file(env, path='a/b')
    assert await env.store.reset_knowledge_by_id('kb') is None
    for job_id in list(env.api.jobs):
        env.api.advance(job_id, 'SUCCEEDED')
    assert (await env.store.reset_knowledge_by_id('kb')).id == 'kb'
    assert env.api.folders['kb'] == {}


@pytest.mark.asyncio
async def test_suspension_is_false_and_the_sweeps_are_empty(env):
    """Cloud suspension and local retention sweeps have no corresponding soev state and perform no requests."""
    assert await env.store.is_suspended('kb') is False
    assert await env.store.get_suspension_info('kb') is None
    assert await env.store.get_pending_deletions() == []
    assert await env.store.get_stale_knowledge(0) == []
    assert await env.store.get_suspended_expired_knowledge() == []
    assert env.api.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'args', 'reason'),
    [
        ('add_file_to_knowledge_by_id', ('kb', 'file', 'alice'), 'ingest'),
        ('set_path_fields_by_file_id', ('file', {}), 'ingest'),
        ('update_knowledge_meta_by_id', ('kb', {}), 'cloud sync'),
        ('update_knowledge_data_by_id', ('kb', {}), 'ingest'),
        ('update_knowledge_user_id_by_id', ('kb', 'bob'), 'owner transfer'),
    ],
)
async def test_a_refused_method_names_the_plan_it_moves_with(env, method, args, reason):
    """Unsupported writes identify their method and the missing plan or owner-transfer route."""
    with pytest.raises(env.module.NotOnSoev) as caught:
        await getattr(env.store, method)(*args)
    assert method in str(caught.value) and reason in str(caught.value)
    assert env.api.requests == []
