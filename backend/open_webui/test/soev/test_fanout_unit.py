"""Call budgets and owner isolation without an application or database."""

import importlib
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev import acting, client, threads
from open_webui.soev.chat_paths import ChatPaths
from open_webui.soev.request_cache import request_cache


def stub_module(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, name, module)
    parent, attribute = name.rsplit('.', 1)
    if parent in sys.modules:
        monkeypatch.setattr(sys.modules[parent], attribute, module, raising=False)


def isolated_module(monkeypatch, name):
    spec = importlib.util.spec_from_file_location(
        'open_webui.soev._fanout_test_' + name,
        Path(__file__).parents[2] / 'soev' / (name + '.py'),
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def knowledge_module(monkeypatch, owner):
    stub_module(monkeypatch, 'open_webui.soev.availability', SimpleNamespace(SoevNotConfigured=Exception))
    return isolated_module(monkeypatch, 'knowledge_store')


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:alice')
    monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value='fictional-owner-token'))
    monkeypatch.setattr(client, 'requester_ref', lambda: 'owui:user:alice')
    monkeypatch.setattr(client, 'user_token', AsyncMock(return_value='fictional-owner-token'))
    access_jobs = SimpleNamespace(references=AsyncMock(return_value={}), remember=AsyncMock())
    stub_module(monkeypatch, 'open_webui.soev.knowledge_access_jobs', access_jobs)
    return access_jobs


@pytest.mark.asyncio
async def test_read_batch_uses_one_owner_request_and_keeps_selector_lists(owner, monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        assert request.headers['X-Soev-User-Token'] == 'fictional-owner-token'
        assert json.loads(request.content)['reads'][0]['query']['job_ids'] == ['one', 'two']
        return httpx.Response(200, json={'results': [{'status': 200, 'body': {'data': []}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(client, '_shared_client', lambda: http)
        api = client.SoevClient('https://soev.invalid', 'fictional-product-key')
        assert await api.read_many([{'resource': 'jobs', 'query': {'job_ids': ['one', 'two']}}]) == [{'data': []}]
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_batch_refusal_never_becomes_an_empty_success(owner, monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={'results': [{'status': 503, 'body': {'code': 'service_unavailable', 'detail': 'Unavailable'}}]},
            )
        )
    ) as http:
        monkeypatch.setattr(client, '_shared_client', lambda: http)
        api = client.SoevClient('https://soev.invalid', 'fictional-product-key')
        with pytest.raises(client.SoevApiError) as refused:
            await api.read_many([{'resource': 'jobs'}])
        assert refused.value.status == 503


@pytest.mark.asyncio
async def test_cloud_status_batches_connection_details_without_an_owner_list(owner):
    from open_webui.soev.cloud_sync import CloudSync

    api = SimpleNamespace(
        read_pages=AsyncMock(
            return_value=[
                {'key': 'kb'},
                {'data': [{'id': str(i), 'connection_id': 'connection-' + str(i)} for i in range(12)]},
            ]
        ),
        read_many=AsyncMock(side_effect=lambda selected, **kwargs: [{'id': row['connection_id']} for row in selected]),
    )
    result = await CloudSync(api, 'owui:user:alice').sync_status('kb')
    assert len(result['schedules']) == 12
    api.read_pages.assert_awaited_once()
    api.read_many.assert_awaited_once()
    assert all(row['resource'] == 'connection' for row in api.read_many.call_args.args[0])
    assert api.read_many.call_args.kwargs == {'as_user': 'owui:user:alice'}


@pytest.mark.asyncio
async def test_knowledge_snapshot_has_one_call_for_many_deletions(owner, monkeypatch, knowledge_module):
    api = SimpleNamespace(
        read_pages=AsyncMock(
            return_value=[
                {'key': 'kb'},
                {'data': [{'source_id': 'readable', 'path': None}]},
                {'data': [{'items': [{'source_id': str(index)}]} for index in range(100)]},
                {'data': []},
            ]
        )
    )
    stub_module(monkeypatch, 'open_webui.soev.availability', SimpleNamespace(SoevNotConfigured=Exception))
    SoevKnowledgeTable = knowledge_module.SoevKnowledgeTable

    store = SoevKnowledgeTable(client=api)
    monkeypatch.setattr(store, '_with_owners', AsyncMock(side_effect=lambda rows: rows))
    with request_cache():
        collection, documents, deleted, _ = await store._snapshot('kb', user_id='alice')
        assert await store._collection('kb', user_id='alice') == collection
        assert len(deleted) == 100 and documents[0]['source_id'] == 'readable'
    api.read_pages.assert_awaited_once()
    assert len(api.read_pages.call_args.args[0]) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['QUEUED', 'RUNNING', 'FAILED', 'SUCCEEDED'])
async def test_snapshot_keeps_applied_access_and_current_job_in_one_bundle(
    owner, monkeypatch, knowledge_module, status
):
    owner.references.return_value = {
        'kb': {'job_id': 'access-job', 'terminal': 'previous-job'},
        'other': {'job_id': 'other-job'},
    }
    applied = {'key': 'kb', 'principals': ['alice', 'bob'], 'writers': ['alice']}
    job = {'job_id': 'access-job', 'kind': 'update_collection_access', 'collection_key': 'kb', 'status': status}
    api = SimpleNamespace(
        read_pages=AsyncMock(
            return_value=[
                applied,
                {'data': []},
                {'data': []},
                {'data': []},
                {'data': [job]},
            ]
        )
    )
    stub_module(monkeypatch, 'open_webui.soev.availability', SimpleNamespace(SoevNotConfigured=Exception))
    SoevKnowledgeTable = knowledge_module.SoevKnowledgeTable

    store = SoevKnowledgeTable(client=api)
    monkeypatch.setattr(store, '_with_owners', AsyncMock(side_effect=lambda rows: rows))
    with request_cache():
        row, *_ = await store._snapshot('kb', user_id='alice')
        assert await store._collection('kb', user_id='alice') == row
    assert row['principals'] == applied['principals'] and row['writers'] == applied['writers']
    assert row['_access_update'] == job
    assert row['_access_terminal_job'] == ('previous-job' if status in ('QUEUED', 'RUNNING') else 'access-job')
    api.read_pages.assert_awaited_once()
    reads = api.read_pages.call_args.args[0]
    assert len(reads) == 5
    assert reads[-1] == {'resource': 'jobs', 'query': {'job_ids': ['access-job'], 'limit': 200}}
    assert api.read_pages.call_args.kwargs == {'as_user': 'owui:user:alice'}
    owner.references.assert_awaited_once_with('owui:user:alice')


@pytest.mark.asyncio
async def test_collection_list_batches_all_pending_access_jobs_with_its_catalog(owner, monkeypatch, knowledge_module):
    rows = [{'key': str(i), 'principals': ['alice'], 'writers': ['alice']} for i in range(100)]
    owner.references.return_value = {row['key']: {'job_id': 'job-' + row['key']} for row in rows}
    jobs = [
        {
            'job_id': 'job-' + row['key'],
            'kind': 'update_collection_access',
            'collection_key': row['key'],
            'status': 'RUNNING',
        }
        for row in rows
    ]
    api = SimpleNamespace(read_pages=AsyncMock(return_value=[{'data': rows}, {'data': []}, {'data': jobs}]))
    stub_module(monkeypatch, 'open_webui.soev.availability', SimpleNamespace(SoevNotConfigured=Exception))
    stub_module(
        monkeypatch,
        'open_webui.soev.ingest',
        SimpleNamespace(
            is_attachments_collection=lambda key: False,
            is_assistant_collection=lambda key: False,
            is_folder_collection=lambda key: False,
        ),
    )
    SoevKnowledgeTable = knowledge_module.SoevKnowledgeTable

    store = SoevKnowledgeTable(client=api)
    monkeypatch.setattr(store, '_with_owners', AsyncMock(side_effect=lambda rows: rows))
    with request_cache():
        result = await store._collections(user_id='alice')
    assert len(result) == 100
    assert all(row['_access_update']['status'] == 'RUNNING' and row['writers'] == ['alice'] for row in result)
    api.read_pages.assert_awaited_once()
    reads = api.read_pages.call_args.args[0]
    assert [read['resource'] for read in reads] == ['collections', 'jobs', 'jobs']
    assert len(reads[-1]['query']['job_ids']) == 100
    assert api.read_pages.call_args.kwargs == {'as_user': 'owui:user:alice'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'returned',
    [
        [],
        [{'job_id': 'access-job', 'kind': 'ingest', 'collection_key': 'kb'}],
        [{'job_id': 'access-job', 'kind': 'update_collection_access', 'collection_key': 'other'}],
    ],
)
async def test_access_status_never_uses_hidden_or_mismatched_jobs(owner, monkeypatch, knowledge_module, returned):
    owner.references.side_effect = lambda ref: (
        {'kb': {'job_id': 'access-job', 'terminal': 'previous'}} if ref == 'owui:user:alice' else {}
    )
    api = SimpleNamespace(read_pages=AsyncMock(return_value=[{'data': returned}]))
    stub_module(monkeypatch, 'open_webui.soev.availability', SimpleNamespace(SoevNotConfigured=Exception))
    SoevKnowledgeTable = knowledge_module.SoevKnowledgeTable

    store = SoevKnowledgeTable(client=api)
    applied = {'key': 'kb', 'writers': ['alice']}
    with request_cache():
        [owner_row] = await store._with_access_jobs([applied], user_id='alice')
        assert owner_row['_access_update'] is None
        assert owner_row['_access_terminal_job'] == 'previous'
        assert await store._with_access_jobs([applied], user_id='bob') == [applied]
        assert await store._with_access_jobs([applied], as_service=True, job_pages=[]) == [applied]
    api.read_pages.assert_awaited_once()
    assert api.read_pages.call_args.kwargs == {'as_user': 'owui:user:alice'}


@pytest.mark.asyncio
async def test_knowledge_initial_page_reuses_catalog_for_cloud_status(owner, monkeypatch, knowledge_module):
    stub_module(
        monkeypatch,
        'open_webui.soev.capabilities',
        SimpleNamespace(CLOUD_SYNC='cloud_sync', has=AsyncMock(return_value=True), known=lambda: True),
    )
    stub_module(monkeypatch, 'open_webui.soev.availability', SimpleNamespace(SoevNotConfigured=Exception))
    SoevKnowledgeTable = knowledge_module.SoevKnowledgeTable

    api = SimpleNamespace(
        read_pages=AsyncMock(
            return_value=[
                {'key': 'kb'},
                {'data': []},
                {'data': []},
                {'data': [{'id': str(i), 'connection_id': 'c-' + str(i)} for i in range(12)]},
            ]
        ),
        read_many=AsyncMock(side_effect=lambda selected, **kwargs: [{'id': row['connection_id']} for row in selected]),
    )
    store = SoevKnowledgeTable(client=api)
    monkeypatch.setattr(store, '_with_owners', AsyncMock(side_effect=lambda rows: rows))
    with request_cache():
        await store._snapshot('kb')
        assert len((await store.page_sync_status('kb'))['schedules']) == 12
    api.read_pages.assert_awaited_once()
    api.read_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_consumers_in_one_request_share_one_authorized_page(owner, monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'items': [], 'next_cursor': None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(threads, '_shared_client', lambda: http)
        with request_cache():
            first = threads.ThreadClient('https://soev.invalid', 'fictional-key')
            second = threads.ThreadClient('https://soev.invalid', 'fictional-key')
            assert (await first.index(limit=200)).items == []
            assert (await second.index(limit=200)).items == []
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_sidebar_attachment_access_is_primed_in_two_owner_batches(owner, monkeypatch, knowledge_module):
    files = [SimpleNamespace(id=str(i), user_id='bob', meta={}) for i in range(100)]
    stub_module(
        monkeypatch,
        'open_webui.models.files',
        SimpleNamespace(Files=SimpleNamespace(get_files_by_ids=AsyncMock(return_value=files))),
    )
    stub_module(monkeypatch, 'open_webui.soev.availability', SimpleNamespace(SoevNotConfigured=Exception))
    SoevKnowledgeTable = knowledge_module.SoevKnowledgeTable

    documents = [{'source_id': file.id, 'collection_key': 'shared'} for file in files[:-1]]
    api = SimpleNamespace(
        is_service=False,
        read_pages=AsyncMock(return_value=[{'data': documents}]),
        read_many=AsyncMock(return_value=[{'data': [{'key': 'shared'}]}, {'items': [], 'next_cursor': None}]),
    )
    store = SoevKnowledgeTable(client=api)
    monkeypatch.setattr(store, '_with_owners', AsyncMock(side_effect=lambda rows: rows))
    with request_cache():
        index = await store.prime_sidebar_access(
            'alice',
            {file.id for file in files},
            {'shared', 'hidden'},
            index_read={'resource': 'thread_index', 'query': {'limit': 200}},
        )
        assert index == {'items': [], 'next_cursor': None}
        assert await store._collection('shared', user_id='alice') == {'key': 'shared'}
        assert await store._collection('hidden', user_id='alice') is None
        for file in files:
            refs = await store._source_references(file.id, user_id='alice')
            assert len(refs) == (0 if file.id == '99' else 1)
    api.read_pages.assert_awaited_once()
    api.read_many.assert_awaited_once()
    assert api.read_pages.call_args.kwargs == api.read_many.call_args.kwargs == {'as_user': 'owui:user:alice'}


@pytest.mark.asyncio
async def test_owner_poll_claims_many_jobs_but_sends_one_bundle(owner, monkeypatch):
    files = [
        SimpleNamespace(
            id=str(index),
            user_id='alice',
            meta={
                'soev_job': {'job_id': f'job-{index}', 'collection_key': f'kb-{index % 3}', 'submitted_at': 1},
            },
        )
        for index in range(150)
    ]
    table = SimpleNamespace(
        set_status=AsyncMock(),
        get_files_with_soev_jobs=AsyncMock(return_value=files),
        claim_soev_job_poll=AsyncMock(
            side_effect=lambda identifier, *args, **kwargs: next(file for file in files if file.id == identifier)
        ),
    )
    stubs = {
        'open_webui.config': SimpleNamespace(SOEV_API_JOB_POLL_SECONDS=5, SOEV_API_JOB_MAX_WALL_CLOCK_SECONDS=600),
        'open_webui.models.files': SimpleNamespace(FileModel=SimpleNamespace, Files=table),
        'open_webui.services.files.events': SimpleNamespace(emit_file_status=AsyncMock()),
        'open_webui.soev.identity': ModuleType('identity'),
        'open_webui.soev.ingest': ModuleType('ingest'),
    }
    for name, module in stubs.items():
        stub_module(monkeypatch, name, module)
    jobs = isolated_module(monkeypatch, 'jobs')
    api = SimpleNamespace(
        is_service=False,
        read_many=AsyncMock(
            return_value=[
                {'data': [{'job_id': f'job-{index}', 'status': 'RUNNING'} for index in range(150)]},
                {'data': []},
                {'data': []},
                {'data': []},
            ]
        ),
    )
    assert await jobs.poll_once(api, now=2) == 0
    api.read_many.assert_awaited_once()
    reads = api.read_many.call_args.args[0]
    assert len(reads) == 4
    assert len(reads[0]['query']['job_ids']) == 150
    assert api.read_many.call_args.kwargs == {'as_user': 'owui:user:alice'}
    table.get_files_with_soev_jobs.assert_awaited_once_with(user_id='alice')
    api.read_many.return_value = [{}, {'data': []}, {'data': []}, {'data': []}]
    assert await jobs.poll_once(api, now=8) == 0
    table.set_status.assert_not_called()

    monkeypatch.setattr(jobs.time, 'time', lambda: 2)
    coalesced = client.SoevClient('https://soev.invalid', 'fictional-product-key')
    broken = False

    async def respond(method, path, *, body, **kwargs):
        results = []
        for read in body['reads']:
            payload = {'key': 'kb'} if read['resource'] == 'collection' else {'data': []}
            status = 200
            if read['resource'] == 'jobs':
                payload = {'data': [{'job_id': f'job-{index}', 'status': 'RUNNING'} for index in range(150)]}
                if broken:
                    status, payload = 503, {'code': 'service_unavailable', 'detail': 'Unavailable'}
            results.append({'status': status, 'body': payload})
        return httpx.Response(200, json={'results': results})

    monkeypatch.setattr(coalesced, '_request', AsyncMock(side_effect=respond))
    monkeypatch.setattr(jobs.identity, 'build_client', lambda: coalesced, raising=False)
    selected = [{'resource': 'collection', 'collection_key': 'kb'}]
    with jobs.defer_owner_jobs('alice'):
        assert await coalesced.read_many(selected, as_user='owui:user:alice') == [{'key': 'kb'}]
        assert await coalesced.read_many(selected, as_user='owui:user:alice') == [{'key': 'kb'}]
    sent = coalesced._request.call_args_list
    assert len(sent) == 2
    assert len(sent[0].kwargs['body']['reads']) == 5
    assert sent[1].kwargs['body']['reads'] == selected
    broken = True
    with jobs.defer_owner_jobs('alice'):
        assert await coalesced.read_many(selected, as_user='owui:user:alice') == [{'key': 'kb'}]
    table.set_status.assert_not_called()
    broken = False
    coalesced._request.reset_mock()
    monkeypatch.setattr(coalesced, '_require_thread_request', AsyncMock())
    with request_cache(), jobs.defer_owner_jobs('alice'):
        assert await coalesced.get('/v1/collections/kb', as_user='owui:user:alice') == {'key': 'kb'}
        assert await coalesced.get('/v1/collections/kb', as_user='owui:user:alice') == {'key': 'kb'}
    coalesced._request.assert_awaited_once()
    assert len(coalesced._request.call_args.kwargs['body']['reads']) == 5
    table.get_files_with_soev_jobs.return_value = []
    resolver = AsyncMock(return_value='unused-token')
    monkeypatch.setattr(acting, 'user_token', resolver)
    assert await jobs.poll_once(api, now=40) == 0
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_sidebar_derives_rows_pins_and_tags_from_one_authorized_scan(monkeypatch):
    from open_webui.soev import chat_routes
    from open_webui.soev.chat_routes import chat_read

    monkeypatch.setattr(chat_routes, 'sidebar_folders', AsyncMock(return_value={'owned': [], 'shared': []}))
    monkeypatch.setattr(chat_routes, 'prime_sidebar_access', AsyncMock(return_value=True))
    from starlette.requests import Request

    def item(identifier, **organization):
        return {
            'thread_id': identifier,
            'owned': True,
            'title': identifier,
            'tags': [identifier],
            'updated_at': '2026-11-08T00:00:00Z',
            'organization': organization,
        }

    service = SimpleNamespace(
        authorized=AsyncMock(
            return_value=[item('ordinary'), item('pinned', pinned=True), item('folder', folder_id='f')]
        ),
        ui_rows=ChatPaths.ui_rows,
        _included=ChatPaths._included,
    )
    result = await chat_read(
        service,
        Request(
            {
                'type': 'http',
                'headers': [],
                'query_string': b'include_sidebar=true',
            }
        ),
        SimpleNamespace(id='alice'),
        '',
        '/',
    )
    assert [row['id'] for row in result['items']] == ['ordinary']
    assert [row['id'] for row in result['pinned']] == ['pinned']
    assert result['tags'] == ['folder', 'ordinary', 'pinned']
    service.authorized.assert_awaited_once_with('alice')


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', [True, False])
async def test_opening_bundles_projection_and_versions_but_never_releases_reader_versions(monkeypatch, owner):
    api = SimpleNamespace(
        read_many=AsyncMock(
            return_value=[
                {
                    'thread_id': 'thread',
                    'generation': 'generation',
                    'title': 'Title',
                    'preferences': {} if owner else None,
                    'messages': [],
                    'next_cursor': None,
                },
                {'items': []},
            ]
        )
    )
    stub_module(monkeypatch, 'open_webui.soev.identity', SimpleNamespace(build_client=lambda: api))
    service = ChatPaths(None, SimpleNamespace(list=AsyncMock(return_value=[])))
    page, versions = await service.opening('alice', 'thread', current=True)
    assert page.thread_id == 'thread'
    assert (versions is not None) is owner
    api.read_many.assert_awaited_once()
    reads = api.read_many.call_args.args[0]
    assert [read['resource'] for read in reads] == ['thread', 'thread_versions']
    assert api.read_many.call_args.kwargs == {'as_user': 'owui:user:alice'}


@pytest.mark.asyncio
async def test_expanded_folder_pages_share_the_sidebar_index(owner, monkeypatch):
    from open_webui.soev.chat_routes import sidebar_folders

    folder_pages = AsyncMock()
    thread = threads.ThreadClient('https://soev.invalid', 'fictional-key')

    async def owned(request, *, user, db):
        await thread.index(limit=200)
        return [{'id': 'owned', 'is_expanded': True}]

    async def shared(request, *, user, db):
        return [{'id': 'shared', 'is_expanded': True}]

    async def contents(request, identifier, **kwargs):
        await thread.index(limit=200)
        await folder_pages(identifier, **kwargs)
        return {'chats': [{'id': identifier}], 'has_more': False}

    @asynccontextmanager
    async def database():
        yield None

    stub_module(monkeypatch, 'open_webui.internal.db', SimpleNamespace(get_async_db_context=database))
    stub_module(
        monkeypatch,
        'open_webui.routers.folders',
        SimpleNamespace(
            get_folders=owned,
            get_shared_folders=shared,
            get_shared_folder_chats=contents,
        ),
    )
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'items': [], 'next_cursor': None})

    actor = SimpleNamespace(id='alice')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(threads, '_shared_client', lambda: http)
        with request_cache():
            await thread.index(limit=200)
            result = await sidebar_folders(None, actor)
    assert len(seen) == 1
    assert result['owned'][0]['initial_page']['chats'] == [{'id': 'owned'}]
    assert result['shared'][0]['initial_page']['chats'] == [{'id': 'shared'}]
    assert folder_pages.await_count == 2
    assert all(call.kwargs['user'] is actor and call.kwargs['page'] == 1 for call in folder_pages.call_args_list)
