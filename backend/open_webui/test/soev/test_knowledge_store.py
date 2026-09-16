"""Knowledge store behavior against recorded HTTP and isolated OWUI file rows."""

import base64
import datetime as dt
import importlib
import inspect
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SERVICE = 'owui:service:webui'
SOEV = {
    'add_file_to_knowledge_by_id',
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
    'set_path_fields_by_file_id',
    'update_knowledge_data_by_id',
    'get_pending_deletions',
    'get_stale_knowledge',
    'get_suspended_expired_knowledge',
    'is_suspended',
    'get_suspension_info',
}
REFUSED = {
    'update_knowledge_meta_by_id',
    'update_knowledge_user_id_by_id',
}


@pytest_asyncio.fixture
async def env(identity_config, fake_api, monkeypatch):
    """Use the real client and Files methods without creating any local knowledge or grant tables."""
    identity, _ = identity_config
    module = importlib.import_module('open_webui.soev.knowledge_store')
    models = importlib.import_module('open_webui.models.knowledge')
    files = importlib.import_module('open_webui.models.files')
    database = importlib.import_module('open_webui.internal.db')
    projection = importlib.import_module('open_webui.soev.projection')
    api = fake_api
    api.page_size = 1
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


async def in_flight(env, source='upload', key='kb', **meta):
    ingest = importlib.import_module('open_webui.soev.ingest')
    row = await env.files.Files.insert_new_file(
        'alice', env.files.FileForm(id=source, filename=source + '.txt', path='', meta=meta)
    )
    await ingest.submit(row, collection_key=key, user_id='alice', text='inline content', client=env.client)
    return await env.files.Files.get_file_by_id(source)


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
async def test_get_knowledge_by_id_inside_a_request_runs_as_the_acting_user(env):
    """Collection reads assert the acting user while grant reads remain identity-free in the same context."""
    acting = importlib.import_module('open_webui.soev.acting')
    token = acting._acting_ref.set('owui:user:alice')
    try:
        assert (await env.store.get_knowledge_by_id('kb')).id == 'kb'
        request = env.api.requests[-1]
        assert request.method == 'GET' and request.url.path == '/v1/collections/kb'
        encoded = request.headers['X-Soev-Subject'].split('.')[1]
        assert json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['sub'] == 'owui:user:alice'
        env.api.requests.clear()
        assert await env.store.get_collection_grants('kb') == []
        assert len(env.api.requests) == 1
        request = env.api.requests[0]
        assert request.method == 'GET' and request.url.path == '/v1/collections/kb'
        assert 'X-Soev-Subject' not in request.headers
    finally:
        acting._acting_ref.reset(token)


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
async def test_delete_commissions_a_job_and_the_list_hides_it(env, method, monkeypatch):
    """The deleting user's listing hides queued and running deletions immediately and reveals failed ones."""
    monkeypatch.setattr(env.module, 'acting_ref', lambda: 'owui:user:alice')
    assert await getattr(env.store, method)('kb') is True
    job = next(iter(env.api.jobs.values()))
    assert job['kind'] == 'delete_collection'
    store = env.module.SoevKnowledgeTable(client=env.client, service_principal=SERVICE)
    assert job['status'] == 'QUEUED'
    assert await store.get_knowledge_bases() == []
    env.api.advance(job['job_id'], 'RUNNING')
    assert await store.get_knowledge_bases() == []
    assert (await store.get_knowledge_by_id_unfiltered('kb')).id == 'kb'
    env.api.advance(job['job_id'], 'FAILED')
    assert [row.id for row in await store.get_knowledge_bases()] == ['kb']
    queries = [r.url.params['status'] for r in env.api.requests if r.url.path == '/v1/jobs']
    assert queries == ['QUEUED', 'RUNNING'] * 3
    for request in env.api.requests:
        if request.method == 'DELETE' or (request.method == 'GET' and request.url.path == '/v1/jobs'):
            encoded = request.headers['X-Soev-Subject'].split('.')[1]
            assert json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['sub'] == 'owui:user:alice'


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
    """Directory filters differ, and search hydration stays slim apart from unlanded membership reads."""
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
    assert all(
        'file.data' not in statement for statement in statements if 'WHERE JSON_EXTRACT(file.meta,' not in statement
    )
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
async def test_search_knowledge_files_spans_every_readable_collection(env, monkeypatch):
    """Cross-collection search reads only accessible documents as the user and keeps the first duplicate."""
    await seed(env, 'second', owner='bob')
    env.api.collections['second']['principals'].append('owui:user:alice')
    await seed(env, 'private', owner='bob')
    await file(env, 'first')
    await file(env, 'shared', key='second')
    await file(env, 'secret', key='private')
    env.api.add_document('second', 'first')
    env.api.documents['second', 'first']['ingested_at'] = '2026-09-12T12:00:00Z'
    lookup = AsyncMock(wraps=env.store._file_rows)
    monkeypatch.setattr(env.store, '_file_rows', lookup)
    listing = AsyncMock(wraps=env.store._collections)
    monkeypatch.setattr(env.store, '_collections', listing)
    env.api.requests.clear()
    filters = {'user_id': 'alice', 'group_ids': ['irrelevant']}
    result = await env.store.search_knowledge_files(filters)
    assert [row.id for row in result.items] == ['first', 'shared']
    assert result.total == 2 and result.directories == [] and result.breadcrumbs == []
    assert result.items[0].added_at == int(
        dt.datetime.fromisoformat(env.api.documents['kb', 'first']['ingested_at']).timestamp()
    )
    listing.assert_awaited_once_with(user_id='alice')
    lookup.assert_awaited_once_with(['first', 'shared'], filters=filters, user_id='alice')
    requests = [request for request in env.api.requests if request.method == 'GET']
    assert requests
    for request in requests:
        encoded = request.headers['X-Soev-Subject'].split('.')[1]
        assert json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['sub'] == 'owui:user:alice'
    assert not any('/private' in request.url.path for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('order_by', 'direction', 'expected'),
    [
        (None, None, ['z', 'a', 'b']),
        ('invalid', 'asc', ['z', 'a', 'b']),
        ('name', 'asc', ['a', 'b', 'z']),
        ('name', 'desc', ['z', 'b', 'a']),
        ('created_at', 'asc', ['b', 'a', 'z']),
        ('created_at', 'desc', ['z', 'a', 'b']),
        ('updated_at', 'asc', ['a', 'b', 'z']),
        ('updated_at', 'desc', ['z', 'a', 'b']),
    ],
)
async def test_search_knowledge_files_filters_by_query_and_pages(env, order_by, direction, expected):
    """Filename and optional content filters precede ordering and pagination with an unsliced total."""
    await seed(env, 'second')
    await file(env, 'b')
    await file(env, 'z', key='second')
    await file(env, 'a')
    async with env.sessions() as session:
        for source, created_at, updated_at in [('a', 3, 2), ('b', 1, 2), ('z', 4, 3)]:
            row = await session.get(env.files.File, source)
            row.created_at, row.updated_at = created_at, updated_at
        row.user_id = 'bob'
        await session.commit()
    filters = {'user_id': 'alice', 'order_by': order_by, 'direction': direction}
    match = await env.store.search_knowledge_files({**filters, 'query': 'B.TXT'})
    assert [row.id for row in match.items] == ['b'] and match.total == 1
    assert isinstance(match.items[0], env.models.FileUserMetadataResponse)
    page = await env.store.search_knowledge_files({**filters, 'query': '.txt'}, skip=1, limit=1)
    assert [row.id for row in page.items] == expected[1:2] and page.total == 3
    remaining = await env.store.search_knowledge_files(filters, skip=1, limit=0)
    assert [row.id for row in remaining.items] == expected[1:] and remaining.total == 3
    assert (await env.store.search_knowledge_files({**filters, 'query': 'needle'})).total == 0
    content = await env.store.search_knowledge_files({**filters, 'query': 'needle', 'include_content': True})
    assert [row.id for row in content.items] == expected and content.total == 3
    assert all(isinstance(row, env.models.FileUserResponse) for row in content.items)
    shared = await env.store.search_knowledge_files({**filters, 'view_option': 'shared'})
    assert [row.id for row in shared.items] == ['z'] and shared.total == 1
    assert (await env.store.search_knowledge_files({**filters, 'view_option': 'created'})).total == 2


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
    await file(env, soev_collection_key='kb')
    assert await env.store.remove_file_from_knowledge_by_id('kb', 'f1') is True
    assert (await env.files.Files.get_file_by_id('f1')).meta['soev_collection_key'] is None
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
        ('update_knowledge_meta_by_id', ('kb', {}), 'cloud sync'),
        ('update_knowledge_user_id_by_id', ('kb', 'bob'), 'owner transfer'),
    ],
)
async def test_a_refused_method_names_the_plan_it_moves_with(env, method, args, reason):
    """Unsupported writes identify their method and the missing plan or owner-transfer route."""
    with pytest.raises(env.module.NotOnSoev) as caught:
        await getattr(env.store, method)(*args)
    assert method in str(caught.value) and reason in str(caught.value)
    assert env.api.requests == []


@pytest.mark.asyncio
async def test_add_file_is_a_no_op_for_a_landed_document(env):
    """Landed membership returns the existing link without sending a mutation."""
    await file(env, path='a')
    env.api.requests.clear()
    result = await env.store.add_file_to_knowledge_by_id('kb', 'f1', 'alice')
    assert result == env.projection.knowledge_link_of(
        env.api.collections['kb'], env.api.documents['kb', 'f1'], service_principal=SERVICE
    )
    assert all(request.method == 'GET' for request in env.api.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize('job_meta', [{}, {'soev_job': None}])
async def test_add_file_before_submit_records_the_collection_key(env, job_meta):
    row = await env.files.Files.insert_new_file(
        'alice',
        env.files.FileForm(
            id='unsubmitted',
            filename='new.txt',
            path='',
            data={'status': 'pending'},
            meta={'status': 'pending', 'preserved': 'metadata', **job_meta},
        ),
    )
    result = await env.store.add_file_to_knowledge_by_id('kb', row.id, 'alice')
    updated = await env.files.Files.get_file_by_id(row.id)
    assert updated.meta == {**row.meta, 'soev_collection_key': 'kb'}
    assert result.file_id == row.id and result.knowledge_id == 'kb'
    assert result.relative_path is None and result.directory_id is None
    assert result.created_at == result.updated_at == row.created_at
    assert [item.id for item in await env.files.Files.get_pending_files_for_knowledge('kb')] == [row.id]
    listed = await env.store.search_files_by_id('kb', 'alice', {}, metadata_only=True)
    assert [(item.id, item.status) for item in listed.items] == [(row.id, 'pending')]
    assert all(request.method == 'GET' for request in env.api.requests)


@pytest.mark.asyncio
async def test_add_file_for_a_missing_file_is_none(env):
    assert await env.store.add_file_to_knowledge_by_id('kb', 'missing', 'alice') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('directory_path', [None, ('target',)])
async def test_add_file_in_another_collection_preserves_the_in_flight_job(env, directory_path):
    await seed(env, 'other')
    row = await in_flight(env, key='other', relative_path='original/upload.txt')
    directory_id = env.projection.directory_id('kb', directory_path) if directory_path else None
    env.api.requests.clear()
    result = await env.store.add_file_to_knowledge_by_id('kb', row.id, 'alice', directory_id)
    updated = await env.files.Files.get_file_by_id(row.id)
    assert updated == row
    assert result.file_id == row.id and result.knowledge_id == 'kb'
    assert result.relative_path is None and result.directory_id is None
    assert result.created_at == result.updated_at == row.created_at
    assert all(request.method == 'GET' for request in env.api.requests)


@pytest.mark.asyncio
async def test_add_file_with_a_directory_moves_a_landed_document(env):
    """Adding landed membership to a directory moves the document and returns its new link."""
    await file(env)
    directory = await env.store.create_directory('kb', 'a', 'alice')
    env.api.requests.clear()
    result = await env.store.add_file_to_knowledge_by_id('kb', 'f1', 'alice', directory.id)
    assert result.directory_id == directory.id and result.relative_path == 'a'
    assert env.api.documents['kb', 'f1']['path'] == 'a'
    writes = [request for request in env.api.requests if request.method != 'GET']
    assert [(request.method, request.url.path) for request in writes] == [
        ('POST', '/v1/collections/kb/documents/f1/move')
    ]
    assert json.loads(writes[0].content) == {'to': 'a'}
    encoded = writes[0].headers['X-Soev-Subject'].split('.')[1]
    assert json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['sub'] == 'owui:user:alice'


@pytest.mark.asyncio
async def test_add_file_with_a_directory_records_the_path_for_an_in_flight_job(env, monkeypatch):
    """An intended folder preserves job fields and is applied by the poller after landing."""
    row = await in_flight(env, relative_path='old/upload.txt', preserved='metadata')
    job = row.meta['soev_job']
    directory = await env.store.create_directory('kb', 'a', 'alice')
    original = await env.store.add_file_to_knowledge_by_id('kb', row.id, 'alice')
    assert original.relative_path == 'old' and original.created_at == row.created_at
    env.api.requests.clear()
    result = await env.store.add_file_to_knowledge_by_id('kb', row.id, 'alice', directory.id)
    updated = await env.files.Files.get_file_by_id(row.id)
    assert updated.meta == {**row.meta, 'soev_job': {**job, 'path': 'a'}}
    assert result.file_id == row.id and result.directory_id == directory.id
    assert result.created_at == result.updated_at == row.created_at
    assert all(request.method == 'GET' for request in env.api.requests)
    jobs = importlib.import_module('open_webui.soev.jobs')
    monkeypatch.setattr(jobs, 'emit_file_status', AsyncMock())
    env.api.advance(job['job_id'], 'SUCCEEDED')
    assert await jobs.poll_once(env.client, now=job['submitted_at']) == 1
    assert env.api.documents['kb', row.id]['path'] == 'a'


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata_only', [True, False])
async def test_in_flight_files_are_listed_with_their_processing_status(env, metadata_only):
    """Unlanded membership participates in search, directory filters, rollups, and collection counts."""
    directory = await env.store.create_directory('kb', 'a', 'alice')
    row = await in_flight(env, relative_path='a/upload.txt')
    await in_flight(env, 'sibling', relative_path='ab/sibling.txt')
    assert {item.id for item in await env.store.get_files_by_id('kb')} == {row.id, 'sibling'}
    assert {item.id for item in await env.store.get_file_metadatas_by_id('kb')} == {row.id, 'sibling'}
    assert await env.store.has_file('kb', row.id)
    result = await env.store.search_files_by_id('kb', 'alice', {}, metadata_only=metadata_only)
    assert result.total == 2
    item = next(item for item in result.items if item.id == row.id)
    assert item.added_at == row.created_at
    assert (item.status if metadata_only else item.meta.status) == 'processing'
    assert result.directories[0].child_count == 1
    assert result.directories[0].status_counts == {'pending': 1, 'failed': 0, 'completed': 0, 'unknown': 0}
    assert (await env.store.search_files_by_id('kb', 'alice', {'directory_id': None})).total == 0
    nested = await env.store.search_files_by_id('kb', 'alice', {'directory_id': directory.id})
    assert [item.id for item in nested.items] == [row.id]
    assert await env.store.get_file_ids_in_directory_subtree('kb', directory.id) == [row.id]
    pairs = await env.store.get_files_with_directory_ids('kb')
    assert dict((item.id, folder) for item, folder in pairs)[row.id] == directory.id
    assert await env.store.get_file_counts_by_knowledge_ids(['kb']) == {'kb': 2}
    cross = await env.store.search_knowledge_files({'user_id': 'alice', 'query': 'upload'})
    assert [item.id for item in cross.items] == [row.id] and cross.items[0].status == 'processing'
    await seed(env, 'private', owner='bob')
    await env.files.Files.update_file_metadata_by_id(row.id, {'soev_collection_key': 'private'})
    assert (await env.store.search_files_by_id('private', 'alice', {})).total == 0


@pytest.mark.asyncio
async def test_a_failed_file_stays_listed_as_error_until_removed(env, monkeypatch):
    """Poller failures keep root membership and error details until removal clears the retained key."""
    row = await in_flight(env, relative_path='a/upload.txt')
    job = row.meta['soev_job']
    jobs = importlib.import_module('open_webui.soev.jobs')
    monkeypatch.setattr(jobs, 'emit_file_status', AsyncMock())
    env.api.advance(
        job['job_id'], 'COMPLETED_WITH_ERRORS', item_code='unsupported_content_type', item_detail='Cannot parse'
    )
    assert await jobs.poll_once(env.client, now=job['submitted_at']) == 1
    result = await env.store.search_files_by_id('kb', 'alice', {'directory_id': None}, metadata_only=True)
    assert result.total == 1 and result.items[0].status == 'failed'
    assert result.items[0].error == 'unsupported_content_type: Cannot parse'
    root = env.projection.directory_id('kb', ())
    rollups = await env.store.get_directory_rollups('kb', [root])
    assert rollups[root]['status_counts']['failed'] == 1
    assert (await env.store.get_files_with_directory_ids('kb'))[0][1] is None
    assert await env.store.get_file_counts_by_knowledge_ids(['kb']) == {'kb': 1}
    assert await env.store.has_file('kb', row.id)
    env.api.requests.clear()
    assert await env.store.remove_file_from_knowledge_by_id('kb', row.id)
    assert all(request.method == 'GET' for request in env.api.requests)
    assert (await env.files.Files.get_file_by_id(row.id)).meta['soev_collection_key'] is None
    assert await env.store.get_files_by_id('kb') == []
    assert await env.store.get_file_counts_by_knowledge_ids(['kb']) == {}


@pytest.mark.asyncio
async def test_a_landed_file_is_listed_once(env):
    """A document visible before the poller clears its job wins over the retained unlanded row."""
    directory = await env.store.create_directory('kb', 'a', 'alice')
    row = await in_flight(env, relative_path='a/upload.txt')
    env.api.advance(row.meta['soev_job']['job_id'], 'SUCCEEDED')
    assert await env.store._unlanded('kb') == []
    assert [item.id for item in await env.store.get_files_by_id('kb')] == [row.id]
    assert [item.id for item in await env.store.get_file_metadatas_by_id('kb')] == [row.id]
    assert (await env.store.search_files_by_id('kb', 'alice', {})).total == 1
    assert (await env.store.search_knowledge_files({'user_id': 'alice'})).total == 1
    assert await env.store.get_file_counts_by_knowledge_ids(['kb']) == {'kb': 1}
    assert len(await env.store.get_files_with_directory_ids('kb')) == 1
    assert await env.store.get_file_ids_in_directory_subtree('kb', directory.id) == [row.id]
    rollups = await env.store.get_directory_rollups('kb', [directory.id])
    assert rollups[directory.id]['child_count'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('acting_user', [None, 'alice'])
async def test_references_use_the_source_lookup_route(env, acting_user):
    """One lookup per source resolves each collection once under the same acting identity."""
    await file(env, 'first')
    await file(env, 'second')
    await seed(env, 'other')
    env.api.add_document('other', 'first')
    await seed(env, 'private', owner='bob')
    env.api.add_document('private', 'first')
    acting = importlib.import_module('open_webui.soev.acting')
    token = acting._acting_ref.set(f'owui:user:{acting_user}' if acting_user else None)
    env.api.requests.clear()
    try:
        pairs = await env.store._references({'first', 'second', 'missing'})
    finally:
        acting._acting_ref.reset(token)
    expected = {('kb', 'first'), ('kb', 'second'), ('other', 'first')}
    if acting_user is None:
        expected.add(('private', 'first'))
    assert {(collection['key'], document['source_id']) for collection, document in pairs} == expected
    lookups = [request for request in env.api.requests if request.url.path == '/v1/documents']
    assert [str(request.url).split('/v1/')[1] for request in lookups] == [
        'documents?source_id=first',
        'documents?source_id=missing',
        'documents?source_id=second',
    ]
    assert all(request.method == 'GET' for request in env.api.requests)
    assert not any(request.url.path.endswith('/documents') and request not in lookups for request in env.api.requests)
    assert sum(request.url.path == '/v1/collections/kb' for request in env.api.requests) == 1
    assert not any(request.url.path == '/v1/collections' for request in env.api.requests)
    for request in env.api.requests:
        if acting_user:
            encoded = request.headers['X-Soev-Subject'].split('.')[1]
            assert json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['sub'] == 'owui:user:alice'
        else:
            assert 'X-Soev-Subject' not in request.headers


@pytest.mark.asyncio
async def test_attachments_collections_are_hidden_from_every_listing(env):
    """Knowledge listings and cross-collection file search exclude reserved attachment collections."""
    attachment = 'owui-attachments-alice'
    await seed(env, attachment)
    await file(env, 'attachment', key=attachment)
    assert [item.id for item in await env.store.get_knowledge_bases()] == ['kb']
    assert [item.id for item in (await env.store.search_knowledge_bases('alice', {})).items] == ['kb']
    assert [item.id for item in await env.store.get_knowledge_bases_by_type('local')] == ['kb']
    for permission in ('read', 'write'):
        assert [item.id for item in await env.store.get_knowledge_bases_by_user_id('alice', permission)] == ['kb']
    assert [item.id for item in await env.store.get_knowledge_items_by_user_id('alice')] == ['kb']
    assert await env.store.accessible_collection_ids('alice', ['kb', attachment]) == {'kb'}
    assert [item['key'] for item in await env.store._collections(as_service=True)] == ['kb']
    assert (await env.store.search_knowledge_files({'user_id': 'alice'})).total == 0


@pytest.mark.asyncio
async def test_removing_an_in_flight_file_cancels_its_job(env):
    """Removing pending membership cancels the ingest job and clears both durable keys."""
    row = await in_flight(env)
    job_id = row.meta['soev_job']['job_id']
    env.api.requests.clear()
    assert await env.store.remove_file_from_knowledge_by_id('kb', row.id)
    assert [(request.method, request.url.path) for request in env.api.requests] == [
        ('POST', f'/v1/jobs/{job_id}/cancel')
    ]
    updated = await env.files.Files.get_file_by_id(row.id)
    assert updated.meta['soev_job'] is None and updated.meta['soev_collection_key'] is None
    assert env.api.jobs[job_id]['status'] == 'CANCELLED'
    assert not await env.store.has_file('kb', row.id)


@pytest.mark.asyncio
async def test_reset_cancels_in_flight_jobs_before_deleting_documents(env):
    """Reset clears pending and failed membership before commissioning any landed document deletion."""
    await file(env, 'landed')
    row = await in_flight(env)
    landed = await in_flight(env, 'just-landed')
    env.api.advance(landed.meta['soev_job']['job_id'], 'SUCCEEDED')
    failed = await in_flight(env, 'failed')
    env.api.advance(failed.meta['soev_job']['job_id'], 'FAILED')
    await env.files.Files.update_file_metadata_by_id(failed.id, {'soev_job': None, 'status': 'failed'})
    env.api.requests.clear()
    assert (await env.store.reset_knowledge_by_id('kb', include_directories=False)).id == 'kb'
    writes = [request for request in env.api.requests if request.method != 'GET']
    assert [request.method for request in writes] == ['POST', 'POST', 'DELETE', 'DELETE']
    assert {request.url.path for request in writes[:2]} == {
        f'/v1/jobs/{file.meta["soev_job"]["job_id"]}/cancel' for file in (row, landed)
    }
    assert env.api.jobs[row.meta['soev_job']['job_id']]['status'] == 'CANCELLED'
    assert await env.files.Files.get_unlanded_files_for_collection('kb') == []
    for member in (row, landed, failed):
        updated = await env.files.Files.get_file_by_id(member.id)
        assert updated.meta['soev_job'] is None and updated.meta['soev_collection_key'] is None


@pytest.mark.asyncio
async def test_vacuous_path_fields_and_data_updates_make_no_request(env):
    """Obsolete path and knowledge data writes return their vacuous results without network requests."""
    assert await env.store.set_path_fields_by_file_id('file', {'relative_path': 'a'}) is True
    assert await env.store.update_knowledge_data_by_id('kb', {'file_ids': ['file']}) is None
    assert env.api.requests == []
