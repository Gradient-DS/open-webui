"""owui-perf: five-run local medians and minimum upstream calls.

Run with pytest -s. HTTP transports add 10 ms per platform call;
these are comparative local measurements, not live-stack latency claims.
"""

import asyncio
import statistics
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev.request_cache import request_cache
from open_webui.test.soev import test_chat_paths, test_knowledge_store

dark_api = test_chat_paths.dark_api
env = test_knowledge_store.env
HTTP_CLIENT = httpx.AsyncClient


async def measure(label, calls, operation, expected):
    elapsed, counts = [], []
    for _ in range(5):
        calls.clear()
        start = time.perf_counter()
        with request_cache():
            result = await operation()
        if isinstance(result, httpx.Response):
            assert result.status_code == 200, result.text
        elapsed.append((time.perf_counter() - start) * 1000)
        counts.append(len(calls))
    assert counts == [expected] * 5
    print(f'owui-perf working {label}: {statistics.median(elapsed):.1f} ms, {expected} calls')


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['/?page=1', '/pinned', '/shared', '/fictional-chat?current=true'])
async def test_chat_page_load_minimum_calls(dark_api, identity_config, monkeypatch, endpoint):
    from open_webui.soev import client as transport

    original = dark_api.client.projection

    async def projection(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.page.messages[-1].content = {'role': 'assistant', 'text': 'Plain answer'}
        return result

    monkeypatch.setattr(dark_api.client, 'projection', projection)

    async def handle(request):
        await asyncio.sleep(0.01)
        if request.url.path.startswith('/v1/chat/threads/'):
            dark_api.calls.append(request)
            return httpx.Response(200, json={'events': []})
        return test_chat_paths.fake_api(request, dark_api.calls, dark_api.state, dark_api.visible, dark_api.feedback)

    async with HTTP_CLIENT(transport=httpx.MockTransport(handle)) as upstream:
        monkeypatch.setattr(transport, '_shared_client', lambda: upstream)
        monkeypatch.setattr('open_webui.soev.threads._shared_client', lambda: upstream)
        expected = 1
        if endpoint == '/pinned':
            expected = 0
        await measure(
            '/chats' + endpoint, dark_api.calls, lambda: dark_api.browser.get('/api/v1/chats' + endpoint), expected
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [0, 3])
async def test_folders_page_load_minimum_calls(dark_api, monkeypatch, size):
    from fastapi import Request
    from open_webui.routers import folders
    from open_webui.soev import client as transport

    monkeypatch.setattr(folders, 'check_folders_permission', AsyncMock())
    records = [SimpleNamespace(id=f'folder-{i}', parent_id=None, data=None, model_dump=lambda: {}) for i in range(size)]
    monkeypatch.setattr(folders.Folders, 'get_folders_by_user_id', AsyncMock(return_value=records))
    monkeypatch.setattr(folders, 'FolderNameIdResponse', lambda **kwargs: kwargs)
    user = SimpleNamespace(id='fictional-fern', role='user')
    scope = {'type': 'http', 'app': dark_api.app, 'headers': []}
    request = Request(scope)

    async def handle(req):
        await asyncio.sleep(0.01)
        return test_chat_paths.fake_api(req, dark_api.calls, dark_api.state, dark_api.visible, dark_api.feedback)

    async with HTTP_CLIENT(transport=httpx.MockTransport(handle)) as upstream:
        monkeypatch.setattr(transport, '_shared_client', lambda: upstream)
        monkeypatch.setattr('open_webui.soev.threads._shared_client', lambda: upstream)
        await measure(
            '/folders/ (' + str(size) + ')',
            dark_api.calls,
            lambda: folders.get_folders(request, user, None),
            int(size > 0),
        )
    assert folders.Folders.get_folders_by_user_id.await_count == 5


@pytest.mark.asyncio
async def test_knowledge_search_minimum_calls(env, monkeypatch):
    from open_webui.routers import knowledge
    from open_webui.soev import acting
    from open_webui.soev import client as transport
    from open_webui.soev.knowledge_store import SoevKnowledgeTable

    store = SoevKnowledgeTable(client=env.client, service_principal=test_knowledge_store.SERVICE)
    env.api.page_size = 200
    for i in range(3):
        await test_knowledge_store.seed(env, key=f'pending-{i}')
    keys = ['pending-0', 'pending-1', 'pending-2']
    # Pending local uploads require catalog reads to avoid double counting.
    file = SimpleNamespace(id='pending-file')
    monkeypatch.setattr(
        store,
        '_unlanded_files_by_collection',
        AsyncMock(return_value={key: [file] if key in keys else [] for key in ['kb', *keys]}),
    )
    monkeypatch.setattr(knowledge, 'Knowledges', store)
    monkeypatch.setattr(knowledge.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))

    async def writable(**kw):
        return await store.accessible_collection_ids(kw['user_id'], kw['resource_ids'], kw['permission'])

    monkeypatch.setattr(knowledge.AccessGrants, 'get_accessible_resource_ids', AsyncMock(side_effect=writable))

    async def handle(request):
        await asyncio.sleep(0.01)
        return env.api.handle(request)

    requester = acting._requester_ref.set('owui:user:alice')
    try:
        async with HTTP_CLIENT(transport=httpx.MockTransport(handle)) as upstream:
            monkeypatch.setattr(transport, '_shared_client', lambda: upstream)
            user = SimpleNamespace(id='alice', role='user')
            await measure(
                '/knowledge/search (3 pending)',
                env.api.requests,
                lambda: knowledge.search_knowledge_bases(user=user, db=None),
                6,
            )
    finally:
        acting._requester_ref.reset(requester)
