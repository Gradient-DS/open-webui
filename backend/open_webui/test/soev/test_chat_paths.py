"""The dark handlers exercised through the HTTP client and a fictional 7a API."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from open_webui.soev import acting, threads
from open_webui.soev.chat_paths import ChatPaths
from open_webui.soev.threads import Organization, ThreadError
from open_webui.test.soev.test_threads import index_item, page
from open_webui.utils.auth import get_verified_user


class FictionalOrganizations:
    def __init__(self):
        self.records = [
            Organization(
                id='fictional-chat',
                thread_id='fictional-thread',
                folder_id='fictional-folder',
            )
        ]
        self.writes = []

    async def list(self, user_id):
        return list(self.records)

    async def legacy_share(self, share_id):
        return getattr(self, 'legacy', {}).get(share_id)

    async def folder_records(self, folder_id):
        return getattr(self, 'folders', {}).get(folder_id, {})

    async def put(self, user_id, record):
        self.records = [r for r in self.records if r.id != record.id] + [record]
        self.writes.append(record)

    async def remove(self, user_id, chat_id):
        self.records = [r for r in self.records if r.id != chat_id]


def fake_api(request, calls, state, visible, feedback):
    import json

    calls.append(request)
    assert request.headers['X-Soev-User-Token'] == 'fictional-login'
    if state['status'] != 200:
        return httpx.Response(state['status'], json={'detail': 'Fictional secret'})
    path = request.url.path
    if path in {'/v1/threads/usage', '/v1/feedback', '/v1/feedback/summary'}:
        return fake_metrics(path, state, feedback)
    if path.endswith('/shares') or not state['allowed'] or state.get('reader'):
        return fake_reader(request, state) if state.get('reader') else fake_shares(request, state)
    if path.startswith('/v1/chat/threads'):
        return fake_runtime(request)
    if path == '/v1/threads':
        return httpx.Response(200, json={'items': visible, 'next_cursor': None})
    if path.endswith('/fork'):
        assert json.loads(request.content) == {'at': 2}
        state['forks'] = state.get('forks', 0) + 1
        return httpx.Response(200, json={'thread_id': 'fictional-child' if state['forks'] == 1 else 'fictional-clone'})
    if path.endswith('/feedback'):
        body = json.loads(request.content)
        assert set(body) == {'rating', 'position', 'comment', 'comment_authored'}
        feedback.append(
            {
                'thread_id': 'fictional-thread',
                'rating': body['rating'],
                'model': 'fictional-model',
                'agent': 'fictional-agent',
                'time': '2026-10-25T10:00:00Z',
                'comment': body['comment'],
            }
        )
        return httpx.Response(204)
    if path in {'/v1/threads/fictional-thread', '/v1/threads/fictional-child'}:
        if request.headers.get('If-None-Match'):
            return httpx.Response(304)
        return httpx.Response(
            200,
            json={**page(1, 2), 'thread_id': path.rsplit('/', 1)[1], 'preferences': {'tags': ['Fictional tag']}},
            headers={'ETag': 'fictional-etag'},
        )
    return httpx.Response(501)


def fake_reader(request, state):
    import json

    shares = state.get('shares', [])
    if request.url.path.endswith('/shares'):
        return httpx.Response(404, json={'code': 'not_found'})
    if (
        not shares
        or not state.get('label', True)
        or not state.get('same_tenant', True)
        or 'fictional-missing' in request.url.path
    ):
        return httpx.Response(404, json={'code': 'not_found'})
    at = max(share['at'] for share in shares)
    if request.url.path.endswith('/fork'):
        assert json.loads(request.content)['at'] <= at
        state['clone'] = {'owner': 'fictional-reader', 'label': 'fictional-source-label'}
        return httpx.Response(200, json={'thread_id': 'fictional-reader-fork'})
    return httpx.Response(200, json={**page(*range(1, at + 1)), 'version': 'fictional-projection-version'})


def fake_shares(request, state):
    import json

    shares = state.setdefault('shares', [])
    if not state['allowed'] or not state.get('owner', True) or state.get('fallback', False):
        return httpx.Response(404, json={'code': 'not_found'})
    if request.method == 'GET':
        return httpx.Response(200, json={'items': shares, 'next_cursor': None})
    body = json.loads(request.content)
    shares[:] = [s for s in shares if s['audience'] != body['audience']]
    if request.method == 'POST':
        shares.append(body)
    return httpx.Response(204)


def fake_runtime(request):
    import json

    path = request.url.path
    if path == '/v1/chat/threads' and request.method == 'POST':
        payload = json.loads(request.content)
        assert set(payload) == {'agent', 'input', 'model'}
        assert 'thread_id' not in payload
        return httpx.Response(201, json={'thread_id': 'fictional-created', 'status': {}, 'events': []})
    if path.endswith('/inputs') and request.method == 'POST':
        return httpx.Response(200, json={'thread_id': 'fictional-thread', 'status': {}, 'events': []})
    if path.startswith('/v1/chat/threads/') and request.method == 'DELETE':
        return httpx.Response(204)
    return httpx.Response(501)


def fake_metrics(path, state, feedback):
    if path == '/v1/threads/usage':
        return httpx.Response(
            200,
            json={
                'model_outputs': 3,
                'unknown_outputs': 1,
                'known_tokens': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10},
                'tokens': None,
            },
        )
    if path == '/v1/feedback':
        own = feedback if state['admin'] else feedback[:1]
        return httpx.Response(200, json={'items': own, 'next_cursor': None})
    if path == '/v1/feedback/summary':
        return httpx.Response(
            200,
            json={
                'items': [
                    {
                        'model': 'fictional-model',
                        'agent': 'fictional-agent',
                        'rating': 1,
                        'count': len(feedback) if state['admin'] else min(1, len(feedback)),
                    }
                ],
                'next_cursor': None,
            },
        )
    raise AssertionError(path)


@pytest_asyncio.fixture
async def dark_api(monkeypatch):
    from open_webui.routers import chats, evaluations, export, notes, tasks
    from open_webui.soev import client as transport

    calls, visible, feedback = [], [index_item()], []
    state = {'allowed': True, 'admin': False, 'status': 200}
    records = FictionalOrganizations()
    api = FastAPI()
    api.state.thread_organizations = records
    for router, prefix in [
        (chats.router, 'chats'),
        (evaluations.router, 'evaluations'),
        (export.router, 'export'),
        (notes.router, 'notes'),
        (tasks.router, 'tasks'),
    ]:
        api.include_router(router, prefix='/api/v1/' + prefix)
    api.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-fern', role='user')
    legacy = AsyncMock(return_value=[])
    monkeypatch.setattr(chats.Chats, 'get_chat_title_id_list_by_user_id', legacy)
    monkeypatch.setattr(chats, 'can_share_chats', AsyncMock(return_value=True))
    monkeypatch.setattr(chats, 'require_chat_import_permission', AsyncMock())
    monkeypatch.setattr(
        chats.Folders,
        'get_folder_by_id',
        AsyncMock(return_value=SimpleNamespace(id='fictional-folder', user_id='fictional-fern')),
    )
    monkeypatch.setattr(chats, 'add_active_state_to_chat_list', AsyncMock(side_effect=lambda request, result: result))
    monkeypatch.setattr(threads, 'enabled', lambda: True)

    requester = acting._requester_ref.set('owui:user:fictional-fern')
    resolver = acting._token_resolver.set(AsyncMock(return_value='fictional-login'))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: fake_api(request, calls, state, visible, feedback))
    ) as upstream:
        monkeypatch.setattr(threads, '_shared_client', lambda: upstream)
        monkeypatch.setattr(transport, '_shared_client', lambda: upstream)
        client = threads.ThreadClient('https://soev.invalid', 'fictional-key')
        monkeypatch.setattr(threads, 'build_client', lambda: client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api),
            base_url='https://owui.invalid',
            headers={'Authorization': 'Bearer fictional-session', 'Content-Type': 'application/json'},
        ) as browser:
            yield SimpleNamespace(
                browser=browser,
                client=client,
                calls=calls,
                state=state,
                visible=visible,
                feedback=feedback,
                records=records,
                legacy=legacy,
                app=api,
            )
    acting._token_resolver.reset(resolver)
    acting._requester_ref.reset(requester)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,path',
    [
        ('POST', '/chats/import'),
        ('POST', '/chats/fictional-chat'),
        ('POST', '/chats/fictional-chat/messages/fictional-message'),
        ('DELETE', '/chats/fictional-chat/messages/fictional-message'),
        ('POST', '/chats/fictional-chat/messages/fictional-message/event'),
        ('POST', '/chats/fictional-chat/compact'),
        ('GET', '/chats/all'),
        ('GET', '/chats/all/archived'),
        ('POST', '/chats/fictional-chat/tags'),
        ('DELETE', '/chats/fictional-chat/tags'),
        ('POST', '/export/data'),
        ('GET', '/export/data/status'),
        ('DELETE', '/export/data'),
        ('GET', '/notes/fictional-note/chat'),
        ('GET', '/notes/fictional-note/chats'),
        ('POST', '/notes/fictional-note/chat'),
        ('POST', '/tasks/title/completions'),
        ('POST', '/tasks/tags/completions'),
        ('GET', '/evaluations/feedbacks/all/export'),
        ('DELETE', '/evaluations/feedbacks'),
    ],
)
async def test_refused_paths_never_read_or_write_local_content(dark_api, method, path):
    response = await dark_api.browser.request(method, '/api/v1' + path, json={'snapshot': 'Fictional content'})
    assert response.status_code == 409, response.text
    assert response.json() == {'state': 'unavailable'}
    assert response.headers['cache-control'] == 'private, no-store'
    assert not dark_api.calls and not dark_api.records.writes


@pytest.mark.asyncio
async def test_projection_forks_clone_tags_and_usage(dark_api):
    response = await dark_api.browser.get('/api/v1/chats/fictional-chat')
    assert response.json()['messages'][1]['position'] == 2
    assert response.headers['etag'] == 'fictional-etag'
    conditional = await dark_api.browser.get(
        '/api/v1/chats/fictional-chat', headers={'If-None-Match': 'fictional-etag'}
    )
    assert conditional.status_code == 304
    for suffix, expected in [('fork', 'fictional-child'), ('clone', 'fictional-clone')]:
        result = await dark_api.browser.post('/api/v1/chats/fictional-chat/' + suffix, json={'at': 2})
        assert result.status_code == 200, result.text
        assert result.json()['thread_id'] == expected
    tags = await dark_api.browser.get('/api/v1/chats/fictional-chat/tags')
    assert tags.json() == ['Fictional tag']
    for path in ['stats/usage', 'stats/export', 'stats/export/fictional-chat']:
        metrics = await dark_api.browser.get('/api/v1/chats/' + path)
        assert metrics.json()['tokens'] is None
        assert metrics.json()['unknown_outputs'] == 1
    dark_api.state['allowed'] = False
    refused = await dark_api.browser.get('/api/v1/chats/fictional-chat', headers={'If-None-Match': 'fictional-etag'})
    assert refused.status_code == 404
    assert 'Fictional' not in refused.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path',
    ['search?text=Fictional%20message&page=1', 'list', '', 'folder/fictional-folder', 'folder/fictional-folder/list'],
)
async def test_listing_rows_have_ui_ids_dates_and_caller_state(dark_api, path):
    folder_id = 'fictional-folder' if path.startswith('folder/') else None
    dark_api.records.records[0] = dark_api.records.records[0].model_copy(
        update={'folder_id': folder_id, 'last_read_at': 1792922399}
    )
    dark_api.records.folders = {'fictional-folder': {'fictional-shared': 'fictional-lark'}}
    dark_api.visible.append(index_item('fictional-shared', owned=False))
    response = await dark_api.browser.get('/api/v1/chats/' + path)
    assert response.status_code == 200, response.text
    rows = {row['id']: row for row in response.json()}
    assert set(rows) == {'fictional-chat', 'fictional-shared'}
    for chat_id, row in rows.items():
        assert row['title'] == 'Fictional title'
        assert type(row['updated_at']) is int
        assert row['updated_at'] == 1792922400
        if 'created_at' in row:
            assert type(row['created_at']) is int
        assert row['readonly'] is (chat_id == 'fictional-shared')
        assert row['last_read_at'] == (1792922399 if chat_id == 'fictional-chat' else None)
        assert row['pinned'] is False
        assert row['archived'] is False
        assert row['folder_id'] == folder_id
        assert row['active'] is False
    assert all(call.url.path == '/v1/threads' for call in dark_api.calls)
    assert dict(dark_api.calls[-1].url.params) == {
        'q': 'Fictional message' if path.startswith('search') else '',
        'limit': '200',
        'cursor': '',
    }
    dark_api.legacy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path', ['search?text=Fictional', '?include_pinned=true', 'list?include_pinned=true', 'pinned']
)
async def test_pinned_shared_chat_keeps_thread_id_and_readonly(dark_api, path):
    dark_api.visible[0]['owned'] = False
    dark_api.records.records[0].folder_id = None
    response = await dark_api.browser.post('/api/v1/chats/fictional-chat/pin')
    assert response.status_code == 200, response.text
    response = await dark_api.browser.get('/api/v1/chats/' + path)
    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row['id'] == 'fictional-thread'
    assert row['organization']['id'] == 'fictional-chat'
    assert row['pinned'] is True
    assert row['readonly'] is True


@pytest.mark.asyncio
async def test_owned_chat_without_record_is_writable(dark_api):
    dark_api.records.records.clear()
    response = await dark_api.browser.get('/api/v1/chats/list')
    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row['id'] == 'fictional-thread'
    assert row['organization'] is None
    assert row['readonly'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('owned', [False, True])
@pytest.mark.parametrize('member', ['fictional-fern', 'fictional-reader'])
async def test_folder_rows_keep_member_readonly_override(dark_api, owned, member):
    dark_api.visible[0]['owned'] = owned
    dark_api.records.folders = {'fictional-folder': {'fictional-thread': member}}
    response = await dark_api.browser.get('/api/v1/chats/folder/fictional-folder/list')
    assert response.status_code == 200, response.text
    row = response.json()[0]
    readonly = member != 'fictional-fern'
    assert row['readonly'] is readonly
    assert row['id'] == ('fictional-thread' if readonly else 'fictional-chat')
    service = ChatPaths(dark_api.client, dark_api.records)
    assert await service.folder_counts('fictional-fern', {'fictional-folder': None}) == {
        'fictional-folder': 0 if readonly else 1
    }


def test_ui_rows_convert_available_creation_dates_without_inventing_them():
    rows = ChatPaths.ui_rows(
        [
            {**index_item(), 'organization': None, 'created_at': '2026-10-25T11:59:59.900+02:00'},
            {**index_item('fictional-shared', owned=False), 'organization': None},
        ]
    )
    assert rows[0]['created_at'] == 1792922399
    assert type(rows[0]['created_at']) is int
    assert 'created_at' not in rows[1]


@pytest.mark.asyncio
async def test_tag_results_have_ui_ids_and_dates(dark_api):
    response = await dark_api.browser.post('/api/v1/chats/tags', json={'name': 'Fictional tag'})
    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row['id'] == 'fictional-chat'
    assert row['updated_at'] == 1792922400
    assert row['readonly'] is False
    assert row['folder_id'] == 'fictional-folder'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path,expected',
    [
        ('list', {'fictional-plain', 'fictional-shared'}),
        ('?include_pinned=true&include_folders=true', {'fictional-plain', 'fictional-chat', 'fictional-shared'}),
        ('search?text=Fictional', {'fictional-plain', 'fictional-chat', 'fictional-shared'}),
        ('search?text=Fictional&include_pinned=false', {'fictional-plain', 'fictional-shared'}),
        ('pinned', {'fictional-chat'}),
        ('archived', {'fictional-archive'}),
        ('archived/count', 1),
    ],
)
async def test_listing_filters_preserve_caller_state(dark_api, path, expected):
    dark_api.records.records[0].pinned = True
    dark_api.records.records.extend(
        [
            Organization(id='fictional-plain', thread_id='fictional-plain-thread'),
            Organization(id='fictional-archive', thread_id='fictional-archive-thread', archived=True, pinned=True),
        ]
    )
    dark_api.visible.extend(
        index_item(thread_id, owned=thread_id != 'fictional-shared')
        for thread_id in ['fictional-plain-thread', 'fictional-archive-thread', 'fictional-shared']
    )
    response = await dark_api.browser.get('/api/v1/chats/' + path)
    assert response.status_code == 200, response.text
    if path == 'archived/count':
        assert response.json() == expected
        return
    rows = response.json()
    assert {row['id'] for row in rows} == expected
    for row in rows:
        org = row['organization'] or {}
        assert row['pinned'] == org.get('pinned', False)
        assert row['archived'] == org.get('archived', False)
        assert row['folder_id'] == org.get('folder_id')
        assert row['readonly'] is (not row['owned'])
        assert type(row['updated_at']) is int


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['search?text=Fictional&page=', 'list?page=', '?page='])
async def test_listing_pages_keep_distinct_chat_ids(dark_api, path):
    dark_api.records.records = [
        Organization(id=f'fictional-chat-{i:02}', thread_id=f'fictional-thread-{i:02}') for i in range(61)
    ]
    dark_api.visible[:] = [index_item(record.thread_id) for record in dark_api.records.records]
    pages = []
    for number in [1, 2, 3]:
        response = await dark_api.browser.get('/api/v1/chats/' + path + str(number))
        assert response.status_code == 200, response.text
        pages.append([row['id'] for row in response.json()])
    assert pages == [
        [f'fictional-chat-{i:02}' for i in range(60, 0, -1)],
        ['fictional-chat-00'],
        [],
    ]
    assert all(call.url.params['limit'] == '200' and call.url.params['cursor'] == '' for call in dark_api.calls)


@pytest.mark.asyncio
async def test_invisible_thread_changes_no_order_count_page_or_folder_unread(dark_api):
    service = ChatPaths(dark_api.client, dark_api.records)
    before = await service.listing('fictional-fern', folder_id='fictional-folder', page=1)
    counts = await service.folder_counts('fictional-fern', {'fictional-folder': None})
    dark_api.records.records.insert(
        0,
        Organization(
            id='fictional-hidden',
            thread_id='fictional-invisible',
            folder_id='fictional-folder',
            pinned=True,
        ),
    )
    assert await service.listing('fictional-fern', folder_id='fictional-folder', page=1) == before
    assert await service.folder_counts('fictional-fern', {'fictional-folder': None}) == counts
    assert counts == {'fictional-folder': 1}
    for path in [
        'list?include_folders=true',
        'search?text=Fictional',
        'folder/fictional-folder',
        'folder/fictional-folder/list',
        'archived',
        'pinned',
        'archived/count',
    ]:
        response = await dark_api.browser.get('/api/v1/chats/' + path)
        assert response.status_code == 200, response.text
        assert 'fictional-hidden' not in response.text


@pytest.mark.asyncio
async def test_organization_changes_reauthorize_and_store_no_content(dark_api):
    for suffix in ['pin', 'archive', 'unread']:
        response = await dark_api.browser.post('/api/v1/chats/fictional-chat/' + suffix)
        assert response.status_code == 200, response.text
        assert 'title' not in response.json() and 'messages' not in response.json()
    writes = len(dark_api.records.writes)
    dark_api.state['allowed'] = False
    response = await dark_api.browser.post('/api/v1/chats/fictional-chat/pin')
    assert response.status_code == 404
    assert len(dark_api.records.writes) == writes


@pytest.mark.asyncio
async def test_platform_delete_completes_before_removing_record(dark_api):
    dark_api.state['status'] = 503
    response = await dark_api.browser.delete('/api/v1/chats/fictional-chat')
    assert response.status_code == 503
    assert len(dark_api.records.records) == 1
    dark_api.state['status'] = 200
    response = await dark_api.browser.delete('/api/v1/chats/fictional-chat')
    assert response.status_code == 200
    assert not dark_api.records.records
    assert dark_api.calls[-1].url.path == '/v1/chat/threads/fictional-thread'


@pytest.mark.asyncio
async def test_feedback_allowlist_authored_comment_and_admin_exception(dark_api):
    rating = {'thread_id': 'fictional-thread', 'position': 2, 'rating': 1}
    for extra in [{'snapshot': {}}, {'model': 'fictional-forged'}, {'tags': []}, {'comment': 'Fictional unasserted'}]:
        response = await dark_api.browser.post('/api/v1/evaluations/feedback', json={**rating, **extra})
        assert response.status_code == 422
    for comment in [None, 'Fictional written comment']:
        response = await dark_api.browser.post(
            '/api/v1/evaluations/feedback',
            json={
                **rating,
                **({'comment': comment, 'comment_authored': True} if comment else {}),
            },
        )
        assert response.status_code == 204, response.text
    listed = await dark_api.browser.get('/api/v1/evaluations/feedbacks/user')
    assert len(listed.json()['items']) == 1
    dark_api.state['admin'] = True
    # The fake's admin claim simulates the platform token, independent of OWUI role.
    listed = await dark_api.browser.get('/api/v1/evaluations/feedbacks/list')
    assert len(listed.json()['items']) == 2
    assert set(listed.json()['items'][1]) == {'thread_id', 'rating', 'model', 'agent', 'time', 'comment'}
    summary = await dark_api.browser.get('/api/v1/evaluations/leaderboard')
    assert summary.json()['items'][0]['count'] == 2
    dark_api.state['status'] = 403
    denied = await dark_api.browser.get('/api/v1/evaluations/feedbacks/list')
    assert denied.status_code == 404 and 'secret' not in denied.text


@pytest.mark.asyncio
async def test_flag_off_uses_original_handler(dark_api, monkeypatch):
    monkeypatch.setattr(threads, 'enabled', lambda: False)
    response = await dark_api.browser.get('/api/v1/chats/list')
    assert response.status_code == 200 and response.json() == []
    dark_api.legacy.assert_awaited_once()
    assert not dark_api.calls


@pytest.mark.asyncio
async def test_model_boundaries_work_with_content_tables_absent(monkeypatch):
    from open_webui.models.chat_messages import ChatMessages
    from open_webui.models.chats import Chats
    from open_webui.models.feedbacks import Feedbacks
    from open_webui.models.shared_chats import SharedChats

    monkeypatch.setattr(threads, 'enabled', lambda: True)
    for facade, method in [
        (Chats, 'get_chat_by_id'),
        (ChatMessages, 'get_messages_by_chat_id'),
        (SharedChats, 'get_by_id'),
        (Feedbacks, 'get_feedback_by_id'),
    ]:
        with pytest.raises(ThreadError):
            await getattr(facade, method)('fictional-chat')


@pytest.mark.asyncio
async def test_user_room_and_redis_never_retain_content_under_v2(monkeypatch):
    from open_webui import tasks
    from open_webui.socket import main as socket

    monkeypatch.setattr(threads, 'enabled', lambda: True)
    redis = AsyncMock()
    emit = AsyncMock()
    monkeypatch.setattr(socket.sio, 'emit', emit)
    emitter = await socket.get_event_emitter(
        {
            'user_id': 'fictional-fern',
            'chat_id': 'fictional-chat',
            'message_id': 'fictional-message',
        }
    )
    await emitter({'type': 'message', 'data': {'content': 'Fictional secret'}})
    await tasks.save_response_stream(redis, 'fictional-task', 'fictional-chat', 'fictional-message', 'Fictional', [])
    assert await tasks.get_response_streams_by_chat_id(redis, 'fictional-chat') == []
    emit.assert_not_awaited()
    assert not redis.mock_calls


@pytest.mark.asyncio
async def test_non_router_entrypoints_refuse_before_touching_content(monkeypatch):
    import importlib

    from open_webui.services.export.service import ExportService

    main = importlib.import_module('open_webui.main')

    monkeypatch.setattr(threads, 'enabled', lambda: True)
    # Empty arguments would fail immediately if any legacy code ran before
    # the refusal. No content tables, models or response stores are supplied.
    for call in [
        lambda: main.serve_cache_file('exports/fictional-user/fictional.zip', SimpleNamespace()),
        lambda: ExportService.build_export_zip('fictional-user', {}),
    ]:
        with pytest.raises(ThreadError):
            await call()
    with pytest.raises(ThreadError):
        ExportService.get_active_export('fictional-user')


@pytest.mark.asyncio
async def test_per_thread_feedback_filters_all_authorized_records(dark_api):
    dark_api.state['admin'] = True
    for thread_id in ['fictional-thread', 'fictional-other']:
        dark_api.feedback.append(
            {
                'thread_id': thread_id,
                'rating': 1,
                'model': 'fictional-model',
                'agent': 'fictional-agent',
                'time': '2026-10-25T10:00:00Z',
                'comment': None,
            }
        )
    response = await dark_api.browser.get('/api/v1/evaluations/feedback/conversation/fictional-thread')
    assert response.status_code == 200
    assert [item['thread_id'] for item in response.json()['items']] == ['fictional-thread']


@pytest.mark.asyncio
async def test_edit_forks_keep_chat_identity_and_clone_has_an_independent_lifecycle(dark_api):
    service = ChatPaths(dark_api.client, dark_api.records)
    edited = await service.fork('fictional-fern', 'fictional-chat', 2)
    assert edited.id == 'fictional-chat'
    assert edited.thread_id == 'fictional-child'
    assert edited.branch_thread_ids == ['fictional-child', 'fictional-thread']
    assert 'branch_thread_ids' not in edited.model_dump()
    dark_api.visible.append(index_item('fictional-child', 'fictional-thread', 2))
    items = await service.authorized('fictional-fern')
    assert [item['thread_id'] for item in items] == ['fictional-child']
    cloned = await service.fork('fictional-fern', 'fictional-chat', 2, clone=True)
    assert cloned.id == 'fictional-clone'
    assert cloned.branch_thread_ids == []
    assert len(dark_api.records.records) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,path,payload',
    [
        ('GET', '/chats/?include_folders=true', None),
        ('GET', '/chats/all/tags', None),
        ('POST', '/chats/tags', {'name': 'Fictional tag'}),
        ('GET', '/chats/fictional-chat/pinned', None),
        ('POST', '/chats/fictional-chat/folder', {'folder_id': None}),
        ('POST', '/chats/read', None),
        ('POST', '/chats/archive/all', None),
        ('POST', '/chats/unarchive/all', None),
    ],
)
async def test_remaining_organizational_routes_use_the_platform_set(dark_api, method, path, payload):
    response = await dark_api.browser.request(method, '/api/v1' + path, json=payload)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert dark_api.calls
    assert 'messages' not in response.json() if isinstance(response.json(), dict) else True


@pytest.mark.asyncio
async def test_create_uses_runtime_first_input_then_organizational_row(dark_api):
    response = await dark_api.browser.post(
        '/api/v1/chats/new',
        json={
            'agent': 'fictional-agent',
            'input': {'text': 'Fictional first input'},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()['thread_id'] == 'fictional-created'
    assert dark_api.records.writes[-1].thread_id == 'fictional-created'
    assert dark_api.calls[-1].url.path == '/v1/chat/threads'
    dark_api.state['status'] = 503
    writes = len(dark_api.records.writes)
    response = await dark_api.browser.post(
        '/api/v1/chats/new',
        json={
            'agent': 'fictional-agent',
            'input': 'Fictional input',
        },
    )
    assert response.status_code == 503
    assert len(dark_api.records.writes) == writes


@pytest.mark.asyncio
async def test_delete_all_uses_runtime_per_owned_chat(dark_api):
    dark_api.records.records.append(Organization(id='fictional-second', thread_id='fictional-second-thread'))
    response = await dark_api.browser.delete('/api/v1/chats/')
    assert response.status_code == 200
    assert not dark_api.records.records
    assert [call.url.path for call in dark_api.calls] == [
        '/v1/chat/threads/fictional-thread',
        '/v1/chat/threads/fictional-second-thread',
    ]


@pytest.mark.asyncio
async def test_missing_empty_and_temporary_create_contracts_do_not_write(dark_api):
    for payload, status in [
        ({}, 422),
        ({'agent': 'fictional-agent'}, 422),
        ({'agent': 'fictional-agent', 'input': {}, 'temporary': True}, 409),
        ({'agent': 'fictional-agent', 'input': {}, 'thread_id': 'chosen'}, 422),
    ]:
        response = await dark_api.browser.post('/api/v1/chats/new', json=payload)
        assert response.status_code == status, response.text
    assert not dark_api.calls and not dark_api.records.writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'action,fields,expected',
    [
        ('approve', {}, {'approve': True}),
        ('reject', {}, {'approve': False}),
        (
            'answer',
            {'answers': {'fictional-choice': 'fictional-value'}},
            {'answers': {'fictional-choice': 'fictional-value'}, 'timed_out': False},
        ),
    ],
)
async def test_tool_approvals_use_platform_input_without_local_messages(dark_api, action, fields, expected):
    import json

    from open_webui.soev.thread_approvals import resolve
    from open_webui.utils.tool_approval import ResolveToolCallForm

    form = ResolveToolCallForm(call_id='root:7', action=action, **fields)
    result = await resolve(dark_api.records, 'fictional-chat', form, 'fictional-fern')
    assert result == {'status': True, 'chat_id': 'fictional-chat'}
    assert dark_api.calls[-1].url.path == '/v1/chat/threads/fictional-thread/inputs'
    assert json.loads(dark_api.calls[-1].content) == {'answering': 'root:7', 'input': expected}
    assert not dark_api.records.writes


@pytest.mark.asyncio
async def test_owned_branch_delete_keeps_the_clone_independent(dark_api):
    dark_api.records.records[0] = dark_api.records.records[0].model_copy(
        update={
            'thread_id': 'fictional-edit',
            'branch_thread_ids': ['fictional-thread', 'fictional-edit'],
        }
    )
    dark_api.records.records.append(Organization(id='fictional-clone', thread_id='fictional-cloned-thread'))
    response = await dark_api.browser.delete('/api/v1/chats/fictional-chat')
    assert response.status_code == 200
    assert [record.id for record in dark_api.records.records] == ['fictional-clone']
    assert {call.url.path for call in dark_api.calls} == {
        '/v1/chat/threads/fictional-thread',
        '/v1/chat/threads/fictional-edit',
    }


@pytest.mark.asyncio
async def test_standalone_thread_fork_gets_its_own_organization(dark_api):
    from open_webui.soev.thread_routes import create_thread_router

    async def create_fork(user_id, thread_id, at):
        organization = await ChatPaths(dark_api.client, dark_api.records).fork(user_id, thread_id, at, clone=True)
        return threads.CreatedThread(thread_id=organization.thread_id)

    dark_api.app.include_router(
        create_thread_router(dark_api.records.list, create_fork=create_fork), prefix='/api/v1/threads'
    )
    response = await dark_api.browser.post('/api/v1/threads/fictional-thread/fork', json={'at': 2})
    assert response.status_code == 200, response.text
    assert response.json() == {'thread_id': 'fictional-child'}
    assert dark_api.records.writes[-1].id == 'fictional-child'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['person', 'group', 'folder'])
async def test_live_share_list_unshare_with_requester_token(dark_api, kind):
    form = {'audience': {'kind': kind, 'id': 'fictional-audience'}, 'at': 2}
    base = '/api/v1/chats'
    shared = await dark_api.browser.post(
        base + '/fictional-chat/share', json=form, headers={'Content-Type': 'application/json; charset=utf-8'}
    )
    assert shared.status_code == 200 and shared.json() == {'share_id': 'fictional-thread'}
    listed = await dark_api.browser.get(base + '/shared/fictional-chat/access')
    assert listed.json() == {'items': [form], 'next_cursor': None}
    own = await dark_api.browser.get(base + '/shared')
    assert own.json()[0]['share_id'] == 'fictional-thread'
    removed = await dark_api.browser.delete(base + '/fictional-chat/share')
    assert removed.status_code == 200
    assert (await dark_api.browser.get(base + '/shared/fictional-chat/access')).json()['items'] == []
    assert not dark_api.records.writes
    assert all(r.headers['X-Soev-User-Token'] == 'fictional-login' for r in dark_api.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,path',
    [
        ('POST', '/fictional-chat/share'),
        ('DELETE', '/fictional-chat/share'),
        ('DELETE', '/share/all'),
        ('POST', '/fictional-thread/clone/shared'),
        ('POST', '/fictional-chat/clone'),
    ],
)
@pytest.mark.parametrize(
    'media_type,authorization,status',
    [
        ('text/plain', None, 415),
        ('text/plain', 'Bearer fictional-session', 415),
        ('application/x-www-form-urlencoded', 'Bearer fictional-session', 415),
        (None, 'Bearer fictional-session', 415),
        ('application/json', None, 401),
        ('application/json', 'Basic fictional-session', 401),
        ('application/json', 'Bearer ', 401),
    ],
)
async def test_share_mutations_refuse_csrf_before_platform_access(
    dark_api, method, path, media_type, authorization, status
):
    dark_api.browser.headers.pop('Authorization')
    dark_api.browser.headers.pop('Content-Type')
    dark_api.browser.cookies.set('token', 'fictional-cookie-session')
    headers = {'Origin': 'https://hostile.owui.invalid'}
    if media_type is not None:
        headers['Content-Type'] = media_type
    if authorization is not None:
        headers['Authorization'] = authorization
    response = await dark_api.browser.request(
        method,
        '/api/v1/chats' + path,
        headers=headers,
        content=json.dumps({'audience': {'kind': 'person', 'id': 'fictional-reader'}, 'at': 2}),
    )
    assert response.status_code == status
    assert response.json() == {'state': 'sign_in' if status == 401 else 'invalid_request'}
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['Vary'] == 'Authorization, Cookie'
    assert not dark_api.calls and not dark_api.records.writes


@pytest.mark.asyncio
async def test_share_mutation_validates_explicit_bearer_without_cookie_fallback(dark_api):
    dark_api.app.dependency_overrides.pop(get_verified_user)
    dark_api.browser.cookies.set('token', 'fictional-cookie-session')
    response = await dark_api.browser.post(
        '/api/v1/chats/fictional-chat/share',
        headers={'Authorization': 'Bearer fictional-invalid-session'},
        json={'audience': {'kind': 'person', 'id': 'fictional-reader'}, 'at': 2},
    )
    assert response.status_code == 401 and response.json() == {'state': 'sign_in'}
    assert not dark_api.calls and not dark_api.records.writes


@pytest.mark.asyncio
async def test_live_share_reader_refusal_missing_and_clone(dark_api):
    base = '/api/v1/chats'
    read = await dark_api.browser.get(base + '/share/fictional-thread')
    assert read.status_code == 200 and read.json()['messages']
    dark_api.state['allowed'] = False
    denied = await dark_api.browser.get(base + '/share/fictional-thread')
    missing = await dark_api.browser.get(base + '/share/fictional-missing')
    assert (denied.status_code, denied.content) == (missing.status_code, missing.content)
    assert denied.json() == {'state': 'share_refused'}
    dark_api.state['allowed'] = True
    clone = await dark_api.browser.post(base + '/fictional-thread/clone/shared')
    assert clone.json()['thread_id'] == 'fictional-child'
    assert dark_api.records.writes[-1].id == 'fictional-child'
    assert '/v1/threads/fictional-thread/fork' == dark_api.calls[-1].url.path


@pytest.mark.asyncio
async def test_anonymous_and_tokenless_shares_do_not_call_platform(dark_api):
    from fastapi import HTTPException

    def anonymous():
        raise HTTPException(401)

    dark_api.app.dependency_overrides[get_verified_user] = anonymous
    response = await dark_api.browser.get('/api/v1/chats/share/fictional-thread')
    assert response.status_code == 401 and response.json() == {'state': 'sign_in'}
    dark_api.app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-fern')
    resolver = acting._token_resolver.set(AsyncMock(return_value=None))
    try:
        response = await dark_api.browser.get('/api/v1/chats/share/fictional-thread')
        assert response.status_code == 401 and response.json() == {'state': 'sign_in'}
        assert not dark_api.calls
    finally:
        acting._token_resolver.reset(resolver)


@pytest.mark.asyncio
async def test_retired_share_owner_only_and_fallback_share_refused(dark_api):
    dark_api.records.legacy = {'fictional-retired': ('fictional-owner', 'fictional-chat', 'fictional-thread')}
    response = await dark_api.browser.get('/api/v1/chats/share/fictional-retired')
    assert response.status_code == 404 and response.json() == {'state': 'legacy_share'}
    assert not dark_api.calls
    dark_api.records.legacy['fictional-retired'] = ('fictional-fern', 'fictional-chat', 'fictional-thread')
    assert (await dark_api.browser.get('/api/v1/chats/share/fictional-retired')).json()['messages']
    dark_api.records.legacy['fictional-unmapped'] = ('fictional-fern', 'fictional-chat', None)
    calls = len(dark_api.calls)
    unmapped = await dark_api.browser.get('/api/v1/chats/share/fictional-unmapped')
    assert unmapped.status_code == 404 and unmapped.json() == {'state': 'legacy_share'}
    assert len(dark_api.calls) == calls
    dark_api.state['fallback'] = True
    response = await dark_api.browser.post(
        '/api/v1/chats/fictional-chat/share',
        json={
            'audience': {'kind': 'person', 'id': 'fictional-reader'},
            'at': 2,
        },
    )
    assert response.status_code == 404 and response.json() == {'state': 'share_refused'}


@pytest.mark.asyncio
async def test_shared_folder_joins_only_authorized_threads_before_counting_and_sorting(dark_api):
    service = ChatPaths(dark_api.client, dark_api.records)
    dark_api.visible[0]['owned'] = False
    dark_api.records.records = []  # The reader owns no row for either chat.
    dark_api.records.folders = {'fictional-folder': {'fictional-thread': 'fictional-owner'}}
    before = await service.listing('fictional-fern', folder_id='fictional-folder')
    counts = await service.folder_counts('fictional-fern', {'fictional-folder': None})
    dark_api.records.folders['fictional-folder']['fictional-hidden'] = 'fictional-owner'
    assert await service.listing('fictional-fern', folder_id='fictional-folder') == before
    assert await service.folder_counts('fictional-fern', {'fictional-folder': None}) == counts
    assert len(before) == 1 and before[0]['readonly'] is True
    assert counts == {'fictional-folder': 0}


@pytest.mark.asyncio
async def test_share_ceiling_live_grant_label_and_tenant_then_reader_owned_fork(dark_api):
    form = {'audience': {'kind': 'person', 'id': 'fictional-reader'}, 'at': 2}
    assert (await dark_api.browser.post('/api/v1/chats/fictional-chat/share', json=form)).status_code == 200
    dark_api.state['reader'] = True
    dark_api.records.records = []
    dark_api.app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-reader', role='user')
    read = await dark_api.browser.get('/api/v1/chats/share/fictional-thread')
    assert [message['position'] for message in read.json()['messages']] == [1, 2]
    cloned = await dark_api.browser.post('/api/v1/chats/fictional-thread/clone/shared')
    assert cloned.json()['id'] == 'fictional-reader-fork'
    assert dark_api.state['clone'] == {'owner': 'fictional-reader', 'label': 'fictional-source-label'}
    for key in ['label', 'same_tenant']:
        dark_api.state[key] = False
        denied = await dark_api.browser.get('/api/v1/chats/share/fictional-thread')
        missing = await dark_api.browser.get('/api/v1/chats/share/fictional-missing')
        assert (denied.status_code, denied.content) == (missing.status_code, missing.content)
        assert denied.json() == {'state': 'share_refused'}
        dark_api.state[key] = True
    dark_api.state['shares'] = []
    assert (await dark_api.browser.get('/api/v1/chats/share/fictional-thread')).json() == {'state': 'share_refused'}


@pytest.mark.asyncio
async def test_share_permission_fallback_and_non_owner_cannot_manage_grants(dark_api, monkeypatch):
    from open_webui.routers import chats

    form = {'audience': {'kind': 'person', 'id': 'fictional-person'}, 'at': 2}
    dark_api.state['owner'] = False
    assert (await dark_api.browser.post('/api/v1/chats/fictional-chat/share', json=form)).status_code == 404
    assert not dark_api.state.get('shares')
    dark_api.state['owner'] = True
    monkeypatch.setattr(chats, 'can_share_chats', AsyncMock(return_value=False))
    dark_api.calls.clear()
    assert (await dark_api.browser.post('/api/v1/chats/fictional-chat/share', json=form)).status_code == 404
    assert not dark_api.calls


@pytest.mark.asyncio
async def test_unshare_all_covers_previously_shared_inactive_branches(dark_api):
    dark_api.records.records[0].branch_thread_ids = ['fictional-thread', 'fictional-older']
    dark_api.visible.append(index_item('fictional-older'))
    dark_api.state['shares'] = [{'audience': {'kind': 'person', 'id': 'fictional-reader'}, 'at': 2}]
    result = await dark_api.browser.delete('/api/v1/chats/share/all')
    assert result.status_code == 200 and result.json() is True
    assert '/v1/threads/fictional-older/shares' in [r.url.path for r in dark_api.calls]


@pytest.mark.asyncio
async def test_share_link_keeps_the_shared_position_for_its_owner_too(dark_api):
    form = {'audience': {'kind': 'person', 'id': 'fictional-person'}, 'at': 1}
    await dark_api.browser.post('/api/v1/chats/fictional-chat/share', json=form)
    shared = await dark_api.browser.get('/api/v1/chats/share/fictional-thread')
    assert [m['position'] for m in shared.json()['messages']] == [1]
    own = await dark_api.browser.get('/api/v1/chats/fictional-chat')
    assert [m['position'] for m in own.json()['messages']] == [1, 2]


@pytest.mark.asyncio
async def test_folder_membership_is_not_disclosed_without_folder_access(dark_api, monkeypatch):
    from open_webui.models.folders import Folders

    monkeypatch.setattr(Folders, 'get_folder_by_id', AsyncMock(return_value=None))
    result = await dark_api.browser.get('/api/v1/chats/folder/fictional-folder/list')
    assert result.status_code == 404 and result.json() == {'state': 'unavailable'}
    assert not dark_api.calls


@pytest.mark.asyncio
async def test_flag_off_preserves_legacy_public_snapshot(dark_api, monkeypatch):
    from open_webui.routers import chats

    monkeypatch.setattr(threads, 'enabled', lambda: False)
    dark_api.app.dependency_overrides[chats.get_optional_verified_user] = lambda: None
    monkeypatch.setattr(
        chats.SharedChats, 'get_by_id', AsyncMock(return_value=SimpleNamespace(user_id='fictional-owner'))
    )
    monkeypatch.setattr(chats, 'is_open_shared_chat', AsyncMock(return_value=True))
    legacy = {
        'id': 'fictional-retired',
        'user_id': 'fictional-owner',
        'title': 'Fictional snapshot',
        'chat': {'messages': [{'content': 'Fictional historical text'}]},
        'updated_at': 1,
        'created_at': 1,
        'archived': False,
    }
    monkeypatch.setattr(chats.Chats, 'get_chat_by_share_id', AsyncMock(return_value=SimpleNamespace(**legacy)))
    result = await dark_api.browser.get('/api/v1/chats/share/fictional-retired')
    assert result.status_code == 200 and result.json()['chat'] == legacy['chat']
    assert not dark_api.calls


@pytest.mark.asyncio
async def test_readonly_folder_rows_do_not_stay_unread_after_reading(dark_api):
    service = ChatPaths(dark_api.client, dark_api.records)
    dark_api.records.folders = {
        'fictional-folder': {
            'fictional-thread': 'fictional-fern',
            'fictional-shared': 'fictional-owner',
        }
    }
    dark_api.visible.append(index_item('fictional-shared', owned=False))
    assert await service.folder_counts('fictional-fern', {'fictional-folder': None}) == {'fictional-folder': 1}
    await service.organize('fictional-fern', 'fictional-chat', {'last_read_at': 2_000_000_000})
    assert await service.folder_counts('fictional-fern', {'fictional-folder': None}) == {'fictional-folder': 0}
    rows = await service.listing('fictional-fern', folder_id='fictional-folder')
    assert len(rows) == 2
    assert next(row for row in rows if row['readonly'])['last_read_at'] is None


@pytest.mark.asyncio
async def test_unavailable_owned_share_does_not_block_listing_or_unshare_all(dark_api, monkeypatch):
    dark_api.records.records[0].branch_thread_ids = ['fictional-unavailable']
    dark_api.visible.insert(0, index_item('fictional-unavailable'))
    dark_api.state['shares'] = [{'audience': {'kind': 'person', 'id': 'fictional-reader'}, 'at': 2}]
    original = dark_api.client.shares

    async def shares(thread_id, **kwargs):
        if thread_id == 'fictional-unavailable':
            raise ThreadError(404, 'unavailable')
        return await original(thread_id, **kwargs)

    monkeypatch.setattr(dark_api.client, 'shares', shares)
    listed = await dark_api.browser.get('/api/v1/chats/shared')
    assert listed.status_code == 200
    assert [item['thread_id'] for item in listed.json()] == ['fictional-thread']
    deleted = await dark_api.browser.delete('/api/v1/chats/share/all')
    assert deleted.status_code == 200 and deleted.json() is True
    assert not dark_api.state['shares']


@pytest.mark.asyncio
async def test_share_listing_still_refuses_transient_platform_failures(dark_api, monkeypatch):
    monkeypatch.setattr(dark_api.client, 'shares', AsyncMock(side_effect=ThreadError(503, 'reconnect')))
    response = await dark_api.browser.get('/api/v1/chats/shared')
    assert response.status_code == 503 and response.json() == {'state': 'reconnect'}


@pytest.mark.asyncio
async def test_unshare_all_tolerates_a_thread_disappearing_after_listing(dark_api, monkeypatch):
    dark_api.state['shares'] = [{'audience': {'kind': 'person', 'id': 'fictional-reader'}, 'at': 2}]
    monkeypatch.setattr(dark_api.client, 'unshare', AsyncMock(side_effect=ThreadError(404, 'unavailable')))
    response = await dark_api.browser.delete('/api/v1/chats/share/all')
    assert response.status_code == 200 and response.json() is True


@pytest.mark.asyncio
async def test_legacy_access_update_cannot_create_a_live_share(dark_api):
    response = await dark_api.browser.post(
        '/api/v1/chats/shared/fictional-chat/access/update',
        json={'audience': {'kind': 'person', 'id': 'fictional-reader'}, 'at': 2},
    )
    assert response.status_code == 409 and response.json() == {'state': 'unavailable'}
    assert not dark_api.calls and not dark_api.state.get('shares')
