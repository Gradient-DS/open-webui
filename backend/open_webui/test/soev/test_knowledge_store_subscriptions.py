"""Subscribed corpus documents are projected as members of the requesting KB."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev.client import SoevClient
from open_webui.soev.knowledge_store import SoevKnowledgeTable


@pytest.fixture
def subscribed_store(monkeypatch, identity_config, identity_http):
    """Serve an owned page and a reached corpus page through the real paging client."""
    from open_webui.models.files import Files

    identity, _ = identity_config
    monkeypatch.setattr(identity, 'ensure_link', AsyncMock())
    monkeypatch.setattr(Files, 'get_unlanded_files_for_collection', AsyncMock(return_value=[]))
    requests, responses = identity_http
    documents = [
        {'collection_key': 'kb', 'source_id': 'uploaded', 'schedule_ids': [], 'path': 'notes.txt'},
        {'collection_key': 'kb', 'source_id': 'onedrive:item-1', 'schedule_ids': ['one'], 'path': 'shared/report.pdf'},
        {
            'collection_key': 'kb',
            'source_id': 'google_drive:item-2',
            'schedule_ids': ['google'],
            'path': 'shared/budget.csv',
        },
    ]
    client = SoevClient('https://soev.invalid', 'test-key', subject_minter=lambda ref: 'test-assertion')
    return SimpleNamespace(
        store=SoevKnowledgeTable(client=client, service_principal='owui:service:webui'),
        requests=requests,
        responses=responses,
        documents=documents,
    )


@pytest.mark.asyncio
async def test_members_list_reached_documents_under_the_kb(subscribed_store):
    """Members retain reached documents and pass the composite cursor through opaquely."""
    env = subscribed_store
    cursor = 'corpus:drive|after/item+1='
    env.responses.extend(
        httpx.Response(200, json=body)
        for body in [
            {'data': env.documents[:1], 'next_cursor': cursor},
            {'data': env.documents[1:], 'next_cursor': None},
            {'data': [], 'next_cursor': None},
            {'data': [], 'next_cursor': None},
        ]
    )
    members = await env.store._members('kb', user_id='alice')
    assert members == {doc['source_id']: (doc, doc['path']) for doc in env.documents}
    assert [request.url.path for request in env.requests[:2]] == ['/v1/collections/kb/documents'] * 2
    assert env.requests[1].url.params['cursor'] == cursor
    assert all(request.headers['X-Soev-Subject'] == 'test-assertion' for request in env.requests)
    assert not env.responses


@pytest.mark.asyncio
async def test_counts_include_reached_documents(subscribed_store):
    """The visible collection count includes both owned and subscribed corpus documents."""
    env = subscribed_store
    env.responses.append(
        httpx.Response(200, json={'data': [{'key': 'kb', 'document_count': len(env.documents)}], 'next_cursor': None})
    )
    assert await env.store.get_file_counts_by_knowledge_ids(['kb'], user_id='alice') == {'kb': 3}
    assert len(env.requests) == 1
    assert env.requests[0].url.path == '/v1/collections'
    assert env.requests[0].headers['X-Soev-Subject'] == 'test-assertion'
    assert not env.responses


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['onedrive', 'google_drive'])
async def test_every_subscriber_keeps_its_synced_type(subscribed_store, provider):
    """A corpus schedule projects the provider to every subscribing KB, preserving its guards."""
    from open_webui.models.knowledge import is_synced_kb

    env = subscribed_store
    env.responses.append(
        httpx.Response(
            200,
            json={
                'data': [
                    {
                        'id': 'shared-schedule',
                        'kind': 'content',
                        'lifecycle': 'enabled',
                        'collection_key': 'corpus:drive',
                        'subscribers': ['kb', 'other-kb'],
                        'subscriber_count': 2,
                        'source_kind': provider,
                    }
                ],
                'next_cursor': None,
            },
        )
    )
    types = await env.store._types(user_id='alice')
    assert types == {'kb': provider, 'other-kb': provider}
    for key in ('kb', 'other-kb', 'unsubscribed'):
        row = {
            'key': key,
            'name': key,
            'description': '',
            'created_by': 'owui:user:alice',
            'created_at': '2026-09-18T12:00:00Z',
            'updated_at': '2026-09-18T12:00:00Z',
            'visibility': 'restricted',
            'principals': [],
            'writers': [],
        }
        assert is_synced_kb(env.store._knowledge(row, types)) == (key != 'unsubscribed')
    assert env.requests[0].url.path == '/v1/schedules'
    assert not env.responses


@pytest.fixture
def folder_store(monkeypatch, identity_config):
    """Serve subscribed folders, a single file and owned content through a counted HTTP stub."""
    from open_webui.models.files import Files
    from open_webui.soev.knowledge_store import _catalog_file_row

    identity, _ = identity_config
    monkeypatch.setattr(identity, 'ensure_link', AsyncMock())
    monkeypatch.setattr(Files, 'get_unlanded_files_for_collection', AsyncMock(return_value=[]))
    collection = {
        'key': 'kb',
        'name': 'KB',
        'description': '',
        'created_by': 'owui:user:alice',
        'visibility': 'restricted',
        'principals': [],
        'writers': [],
        'subscriptions': ['onedrive'],
        'document_count': 4,
        'created_at': '2026-01-01T00:00:00Z',
        'updated_at': '2026-01-01T00:00:00Z',
    }
    schedules = [
        {'id': 'a', 'kind': 'content', 'label': 'Reports', 'scope': {'single_file': False}},
        {'id': 'b', 'kind': 'content', 'label': 'Reports', 'scope': {'single_file': False}},
        {'id': 'empty', 'kind': 'content', 'label': None, 'scope': {'single_file': False}},
        {'id': 'file', 'kind': 'content', 'label': 'Single', 'scope': {'single_file': True}},
        {'id': 'acl', 'kind': 'acl_refresh', 'scope': {}},
    ]
    for schedule in schedules:
        schedule.update(connection_id='c', lifecycle='enabled')
    documents = [
        {'source_id': 'shared', 'path': None, 'schedule_ids': ['a', 'b']},
        {'source_id': 'nested', 'path': 'Quarter/January', 'schedule_ids': ['a']},
        {'source_id': 'single', 'path': 'ignored', 'schedule_ids': ['file']},
        {'source_id': 'owned', 'path': 'Uploads', 'schedule_ids': []},
    ]
    for document in documents:
        document.update(collection_key='kb', ingested_at=collection['created_at'], filename=document['source_id'])
    requests = []

    async def handle(request):
        requests.append(request)
        assert request.method == 'GET'
        path = request.url.path
        bodies = {
            '/v1/collections/kb': collection,
            '/v1/collections': {'data': [collection], 'next_cursor': None},
            '/v1/collections/kb/documents': {'data': documents, 'next_cursor': None},
            '/v1/schedules': {'data': schedules, 'next_cursor': None},
            '/v1/jobs': {'data': [], 'next_cursor': None},
            '/v1/connections/c': {'id': 'c', 'source_kind': 'onedrive', 'lifecycle': 'enabled'},
        }
        if path == '/v1/collections/kb/folders':
            folders = (
                [] if request.url.params.get('under') else [{'path': 'Uploads', 'created_at': collection['created_at']}]
            )
            body = {'folders': folders, 'documents': [], 'next_cursor': None}
        else:
            body = bodies[path]
        return httpx.Response(200, json=body)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = SoevClient('https://soev.invalid', 'test-key', subject_minter=lambda ref: 'test-assertion')
    store = SoevKnowledgeTable(client=client, service_principal='owui:service:webui')

    async def file_rows(ids, *, catalog=None, **kwargs):
        return [_catalog_file_row(catalog[source][0], 'alice') for source in ids if catalog[source][0]]

    monkeypatch.setattr(store, '_file_rows', file_rows)
    return SimpleNamespace(store=store, requests=requests, schedules=schedules, documents=documents, client=client)


@pytest.mark.asyncio
async def test_folder_sources_appear_as_top_level_directories(folder_store):
    """Duplicate labels have distinct stable ids, empty sources appear, and real folders remain."""
    env = folder_store
    page = await env.store.search_files_by_id('kb', 'alice', {'directory_id': None})
    assert sorted(row.name for row in page.directories) == ['Folder', 'Reports', 'Reports', 'Uploads']
    assert len({row.id for row in page.directories}) == 4
    assert all(row.parent_id is None for row in page.directories)
    assert sum(request.url.path == '/v1/schedules' for request in env.requests) == 1
    again = await env.store.get_all_directories('kb')
    assert {row.id for row in page.directories} <= {row.id for row in again}


@pytest.mark.asyncio
async def test_documents_list_under_their_source_folder(folder_store):
    """A shared document appears under both sources, with nested rollups and breadcrumbs."""
    store = folder_store.store
    root = await store.search_files_by_id('kb', 'alice', {'directory_id': None})
    for row in [row for row in root.directories if row.name == 'Reports']:
        page = await store.search_files_by_id('kb', 'alice', {'directory_id': row.id})
        assert [file.id for file in page.items] == ['shared']
        assert [crumb.name for crumb in page.breadcrumbs] == ['Reports']
    nested_id = store._projection.directory_id('kb', ('\0sync:a', 'Quarter', 'January'))
    nested = await store.search_files_by_id('kb', 'alice', {'directory_id': nested_id})
    assert [file.id for file in nested.items] == ['nested']
    assert [crumb.name for crumb in nested.breadcrumbs] == ['Reports', 'Quarter', 'January']
    rollups = await store.get_directory_rollups('kb', [store._projection.directory_id('kb', ('\0sync:a',))])
    assert next(iter(rollups.values()))['child_count'] == 2
    owned = await store.search_files_by_id(
        'kb', 'alice', {'directory_id': store._projection.directory_id('kb', ('Uploads',))}
    )
    assert [file.id for file in owned.items] == ['owned']


@pytest.mark.asyncio
async def test_single_file_sources_stay_at_the_root(folder_store):
    """Single-file sources remain at root even when their catalog path is populated."""
    page = await folder_store.store.search_files_by_id('kb', 'alice', {'directory_id': None})
    assert [file.id for file in page.items] == ['single']
    assert page.total == 1
    all_files = await folder_store.store.search_files_by_id('kb', 'alice', {})
    assert all_files.total == 4


@pytest.mark.asyncio
async def test_virtual_directories_are_read_only(folder_store):
    """Virtual folder mutations and moves into them fail before any upstream mutation."""
    from fastapi import HTTPException

    store = folder_store.store
    virtual = store._projection.directory_id('kb', ('\0sync:a',))
    real = store._projection.directory_id('kb', ('Uploads',))
    operations = [
        lambda: store.create_directory('kb', 'New', 'alice', parent_id=virtual),
        lambda: store.rename_directory(virtual, 'New'),
        lambda: store.delete_directory(virtual),
        lambda: store.move_directory(real, virtual),
        lambda: store.move_file_to_directory('kb', 'owned', virtual),
        lambda: store.add_file_to_knowledge_by_id('kb', 'owned', 'alice', virtual),
    ]
    for operation in operations:
        with pytest.raises(HTTPException) as error:
            await operation()
        assert error.value.status_code == 403
        assert error.value.detail == {'code': 'synced_folder_read_only'}
    assert not folder_store.requests


@pytest.mark.asyncio
async def test_opening_a_kb_fetches_each_resource_once(folder_store):
    """A metadata, items and sync request each fetch a resource at most once in its own scope."""
    from collections import Counter

    from open_webui.soev.cloud_sync import CloudSync
    from open_webui.soev.request_cache import request_cache

    env = folder_store
    counts = []
    operations = [
        lambda: env.store.get_knowledge_by_id('kb'),
        lambda: env.store.search_files_by_id('kb', 'alice', {'directory_id': None}),
        lambda: CloudSync(env.client, 'owui:user:alice').sync_status('kb'),
    ]
    for operation in operations:
        env.requests.clear()
        with request_cache():
            await operation()
        resources = Counter(str(request.url) for request in env.requests)
        assert all(count == 1 for count in resources.values()), resources
        counts.append(len(env.requests))
    assert counts == [1, 6, 3]


@pytest.mark.asyncio
async def test_counts_use_one_collection_listing(folder_store):
    """Counts read the listing once even when multiple KB ids are requested."""
    env = folder_store
    assert await env.store.get_file_counts_by_knowledge_ids(['kb', 'absent'], user_id='alice') == {'kb': 4}
    assert [request.url.path for request in env.requests] == ['/v1/collections']


@pytest.mark.asyncio
async def test_schedule_listing_is_shared_with_sync_status_in_one_request(folder_store):
    """Folder projection and status share schedules if they run in the same request."""
    import asyncio

    from open_webui.soev.cloud_sync import CloudSync
    from open_webui.soev.request_cache import request_cache

    env = folder_store
    with request_cache():
        await asyncio.gather(
            env.store.search_files_by_id('kb', 'alice', {'directory_id': None}),
            CloudSync(env.client, 'owui:user:alice').sync_status('kb'),
        )
    assert sum(request.url.path == '/v1/schedules' for request in env.requests) == 1
    assert sum(request.url.path == '/v1/collections/kb' for request in env.requests) == 1
