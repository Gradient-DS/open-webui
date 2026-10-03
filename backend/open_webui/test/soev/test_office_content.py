"""[Gradient] Office downloads are authorized by the chat and its stored attachments."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from open_webui.models import chats as chat_models
from open_webui.routers import chats
from open_webui.soev.client import SoevApiError


class OfficeBytes(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b'office-'
        yield b'bytes'

    async def aclose(self):
        self.closed = True


@pytest.fixture
def office(monkeypatch):
    user = SimpleNamespace(id='alice', role='user')
    attachment = {'type': 'office', 'element_id': 'element', 'thread_id': 'attached-thread', 'pages': 2}
    message = SimpleNamespace(files=[attachment], meta={'agent_v2': {'thread_id': 'different-bookmark'}})
    lookup = AsyncMock(return_value=SimpleNamespace(user_id='alice', meta={}, folder_id=None))
    messages = AsyncMock(return_value=[message])
    monkeypatch.setattr(chats.Chats, 'get_chat_by_id', lookup)

    async def owned(chat_id, user_id, db=None):
        return await lookup(chat_id, db=db) if user_id == 'alice' else None

    monkeypatch.setattr(chats.Chats, 'get_chat_by_id_and_user_id', owned)
    monkeypatch.setattr(chat_models, 'ENABLE_ADMIN_CHAT_ACCESS', True)
    monkeypatch.setattr(chat_models.AccessGrants, 'has_access', AsyncMock(return_value=False))
    monkeypatch.setattr(chats.ChatMessages, 'get_messages_by_chat_id', messages)
    stream = OfficeBytes()
    upstream = httpx.Response(
        200,
        stream=stream,
        headers={
            'Content-Type': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'Content-Disposition': 'attachment; filename="deck.pptx"',
            'Cache-Control': 'private, max-age=31536000, immutable',
            'X-Private-Upstream': 'not forwarded',
        },
    )
    get = AsyncMock(return_value=upstream)
    monkeypatch.setattr(chats.identity, 'build_client', lambda: SimpleNamespace(chat_get_stream=get))
    app = FastAPI()
    app.include_router(chats.router, prefix='/api/v1/chats')
    app.dependency_overrides[chats.get_verified_user] = lambda: user
    return SimpleNamespace(
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test'),
        user=user,
        attachment=attachment,
        lookup=lookup,
        messages=messages,
        get=get,
        stream=stream,
        upstream=upstream,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('admin', [False, True])
async def test_download_relays_bytes_and_headers_as_chat_owner(office, admin):
    if admin:
        office.user.id, office.user.role = 'admin', 'admin'
    async with office.client as client:
        response = await client.get('/api/v1/chats/chat/office/element/file?thread_id=request-thread')
    assert response.status_code == 200
    assert response.content == b'office-bytes'
    for header in ('content-type', 'content-disposition', 'cache-control'):
        assert response.headers[header] == office.upstream.headers[header]
    assert 'x-private-upstream' not in response.headers
    office.lookup.assert_awaited_once_with('chat', db=None)
    office.messages.assert_awaited_once_with('chat')
    office.get.assert_awaited_once_with(
        '/v1/chat/threads/attached-thread/office/element', as_user='owui:user:alice', params={'part': 'file'}
    )
    assert office.stream.closed


@pytest.mark.asyncio
async def test_page_part_is_one_based_and_relays_image_headers(office):
    office.upstream.headers['Content-Type'] = 'image/png'
    del office.upstream.headers['Content-Disposition']
    async with office.client as client:
        response = await client.get('/api/v1/chats/chat/office/element/page-2')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    assert 'content-disposition' not in response.headers
    assert office.get.call_args.kwargs['params'] == {'part': 'page:2'}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['foreign', 'unknown-chat', 'unknown-element', 'not-office', 'no-thread'])
async def test_unavailable_attachment_is_404_without_upstream_request(office, case):
    if case == 'foreign':
        office.user.id = 'bob'
    elif case == 'unknown-chat':
        office.lookup.return_value = None
    elif case == 'unknown-element':
        office.attachment['element_id'] = 'elsewhere'
    elif case == 'not-office':
        office.attachment['type'] = 'file'
    else:
        del office.attachment['thread_id']
    async with office.client as client:
        response = await client.get('/api/v1/chats/chat/office/element/file')
    assert response.status_code == 404
    office.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('part', ['workspace', 'page-0', 'page--1', 'page-1.5', 'page-01', 'page:1'])
async def test_only_file_and_positive_page_parts_are_allowed(office, part):
    async with office.client as client:
        response = await client.get(f'/api/v1/chats/chat/office/element/{part}')
    assert response.status_code == 404
    office.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [404, 502])
async def test_upstream_errors_are_resolved_before_response_headers(office, status):
    office.get.side_effect = SoevApiError(status, 'upstream_error', 'Unavailable')
    async with office.client as client:
        response = await client.get('/api/v1/chats/chat/office/element/file')
    assert response.status_code == status


@pytest.mark.asyncio
async def test_admin_cannot_download_when_admin_chat_access_is_disabled(office, monkeypatch):
    monkeypatch.setattr(chat_models, 'ENABLE_ADMIN_CHAT_ACCESS', False)
    office.user.id, office.user.role = 'admin', 'admin'
    async with office.client as client:
        response = await client.get('/api/v1/chats/chat/office/element/file')
    assert response.status_code == 404
    office.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'user_id,role,expected', [('alice', 'user', 200), ('bob', 'user', 404), ('admin', 'admin', 404)]
)
async def test_temporary_office_is_available_only_to_its_owner(office, monkeypatch, user_id, role, expected):
    class DetachedValues(dict):
        def get(self, key, default=None):
            return deepcopy(super().get(key, default))

    store = {
        'local:socket:chat': {
            'as_user': 'owui:user:alice',
            'bookmarks': {'answer': {'thread_id': 'attached-thread', 'position': 4}},
        }
    }
    store = DetachedValues(store)
    monkeypatch.setattr(chats.agent_threads, '_temporary', lambda: store)
    chats.agent_threads.remember_temporary_office('local:socket:chat', 'alice', [office.attachment])
    office.user.id, office.user.role = user_id, role
    async with office.client as client:
        response = await client.get('/api/v1/chats/local:socket:chat/office/element/file')
    assert response.status_code == expected
    office.lookup.assert_not_awaited()
    office.messages.assert_not_awaited()
    if expected == 200:
        assert response.content == b'office-bytes'
        office.get.assert_awaited_once_with(
            '/v1/chat/threads/attached-thread/office/element', as_user='owui:user:alice', params={'part': 'file'}
        )
    else:
        office.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['no-chat', 'no-element', 'wrong-owner-emission'])
async def test_temporary_office_requires_a_recorded_attachment(office, monkeypatch, case):
    store = {} if case == 'no-chat' else {'local:socket:chat': {'as_user': 'owui:user:alice', 'bookmarks': {}}}
    monkeypatch.setattr(chats.agent_threads, '_temporary', lambda: store)
    if case == 'wrong-owner-emission':
        chats.agent_threads.remember_temporary_office('local:socket:chat', 'bob', [office.attachment])
    async with office.client as client:
        response = await client.get('/api/v1/chats/local:socket:chat/office/element/file')
    assert response.status_code == 404
    office.get.assert_not_awaited()
