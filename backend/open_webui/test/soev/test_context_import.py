"""Chat references travel as turn inputs; only the runtime imports their content."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from open_webui.soev import threads
from open_webui.soev.thread_routes import create_thread_router
from open_webui.soev.threads import Organization, ThreadClient
from open_webui.test.soev import test_thread_organizations
from open_webui.utils import agent_v2
from starlette.responses import JSONResponse

organization_store = test_thread_organizations.organization_store


@pytest.fixture
def context_turn(chat_http, organization_store, monkeypatch):
    repository, _, statements = organization_store
    local_calls = []

    async def forbidden(*args, **kwargs):
        local_calls.append((args, kwargs))
        raise AssertionError('Local chat content must not be read')

    for name, method in vars(type(agent_v2.Chats)).items():
        if not name.startswith('__') and callable(method):
            monkeypatch.setattr(agent_v2.Chats, name, forbidden)
    monkeypatch.setattr(agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='fictional-fern')))
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=False))
    monkeypatch.setattr(agent_v2, '_documents_allowed', AsyncMock(return_value=False))
    monkeypatch.setattr(agent_v2, 'get_event_emitter', AsyncMock(return_value=None))

    async def turn(files=(), knowledge=(), *, new=True):
        return await agent_v2.call_agent_v2(
            {'stream': False},
            {
                'chat_id': 'fictional-destination',
                'message_id': 'fictional-answer',
                'user_id': 'fictional-fern',
                'user_message': {'content': 'Fictional new question'},
                'new_thread': new,
                'files': list(files),
                'knowledge': list(knowledge),
            },
            agent='fictional-agent',
            organizations=repository,
        )

    yield SimpleNamespace(turn=turn, api=chat_http, repository=repository, statements=statements)
    assert not local_calls
    assert not any('chat_message' in sql or 'summary' in sql for sql in statements)


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['chat', 'assistant', 'both'])
async def test_v2_turn_sends_only_source_thread_ids(context_turn, origin):
    context = context_turn
    await context.repository.put(
        'fictional-other-owner', Organization(id='fictional-source-chat', thread_id='fictional-source-thread')
    )
    entry = {
        'type': 'chat',
        'id': 'fictional-source-chat',
        'name': 'Fictional private title',
        'text': 'Fictional private conversation',
        'thread_id': 'fictional-stale-thread',
    }
    for new in (True, False):
        result = await context.turn(
            [entry] if origin in ('chat', 'both') else [],
            [entry] if origin in ('assistant', 'both') else [],
            new=new,
        )
        assert not isinstance(result, JSONResponse), result.body if isinstance(result, JSONResponse) else result
    sent = [request for request in context.api.requests if request.method == 'POST']
    assert [request.url.path for request in sent] == ['/v1/chat/threads', '/v1/chat/threads/thr-1/inputs']
    for request in sent:
        body = json.loads(request.content)
        assert body['input']['chats'] == [{'thread_id': 'fictional-source-thread'}]
        assert body['input']['text'] == 'Fictional new question'
        assert 'texts' not in body['input']
        assert 'Fictional private' not in request.content.decode()
        assert request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:fictional-fern'


@pytest.mark.asyncio
async def test_shared_thread_reference_needs_no_local_chat(context_turn):
    result = await context_turn.turn(
        [{'type': 'chat', 'id': 'fictional-shared-thread', 'thread_id': 'fictional-shared-thread'}]
    )
    assert not isinstance(result, JSONResponse)
    body = json.loads(context_turn.api.chat.requests[-1].content)
    assert body['input']['chats'] == [{'thread_id': 'fictional-shared-thread'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['chat', 'assistant'])
@pytest.mark.parametrize('source', ['fictional-legacy', 'fictional-missing'])
async def test_chat_without_v2_thread_id_refuses_before_sending(context_turn, origin, source):
    entry = {'type': 'chat', 'id': source}
    result = await context_turn.turn([entry] if origin == 'chat' else [], [entry] if origin == 'assistant' else [])
    assert isinstance(result, JSONResponse)
    assert result.status_code == 409
    assert not context_turn.api.requests


@pytest.mark.asyncio
async def test_references_deduplicate_by_platform_thread_id(context_turn):
    await context_turn.repository.put(
        'fictional-fern', Organization(id='fictional-source', thread_id='fictional-source-thread')
    )
    result = await context_turn.turn(
        [{'type': 'chat', 'id': 'fictional-source'}],
        [{'type': 'chat', 'id': 'fictional-source-thread', 'thread_id': 'fictional-source-thread'}],
    )
    assert not isinstance(result, JSONResponse)
    body = json.loads(context_turn.api.chat.requests[-1].content)
    assert body['input']['chats'] == [{'thread_id': 'fictional-source-thread'}]


@pytest.mark.asyncio
async def test_browser_has_no_context_import_relay():
    app = FastAPI()
    app.include_router(create_thread_router(AsyncMock(return_value=[])), prefix='/api/v1/threads')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as browser:
        response = await browser.post(
            '/api/v1/threads/fictional-destination/context:import',
            json={'source_thread_id': 'fictional-source'},
            headers={'Authorization': 'Bearer fictional'},
        )
    assert response.status_code == 404
    assert not hasattr(ThreadClient, 'import_context')


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['chat', 'assistant'])
@pytest.mark.parametrize('count', [10, 11])
async def test_chat_references_keep_existing_attachment_limits(context_turn, origin, count):
    entries = [
        {'type': 'chat', 'id': f'fictional-thread-{index}', 'thread_id': f'fictional-thread-{index}'}
        for index in range(count)
    ]
    result = await context_turn.turn(entries if origin == 'chat' else [], entries if origin == 'assistant' else [])
    if origin == 'chat' and count > 10:
        assert isinstance(result, JSONResponse)
        assert result.status_code == 422
        assert not context_turn.api.requests
    else:
        assert not isinstance(result, JSONResponse)
        body = json.loads(context_turn.api.chat.requests[-1].content)
        assert body['input']['chats'] == [{'thread_id': entry['thread_id']} for entry in entries[:10]]
        assert 'texts' not in body['input']


@pytest.mark.asyncio
async def test_v2_chat_tools_remain_disabled(monkeypatch):
    from open_webui.tools import builtin
    from open_webui.utils import tools

    monkeypatch.setattr(threads, 'enabled', lambda: True)
    local = AsyncMock(side_effect=AssertionError('Local chat content must not be read'))
    monkeypatch.setattr(builtin.Chats, 'get_chats_by_user_id_and_search_text', local)
    monkeypatch.setattr(builtin.Chats, 'get_chat_by_id_and_user_id', local)
    monkeypatch.setattr(builtin.Chats, 'get_chat_by_id', local)
    monkeypatch.setattr(agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='fictional-fern')))
    request = Request({'type': 'http', 'headers': []})
    user = {'id': 'fictional-fern', 'role': 'admin'}
    search = await builtin.search_chats('Fictional', __request__=request, __user__=user)
    read = await builtin.view_chat('fictional-source', __request__=request, __user__=user)
    assert json.loads(search) == json.loads(read) == {'error': 'Chat not found or access denied'}
    monkeypatch.setattr(tools.Config, 'get_many', AsyncMock(return_value={}))
    available = await tools.get_builtin_tools(request, {'__user__': user})
    assert 'search_chats' not in available and 'view_chat' not in available
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_chat_tools_keep_their_local_reads(monkeypatch):
    from open_webui.tools import builtin

    monkeypatch.setattr(threads, 'enabled', lambda: False)
    chat = SimpleNamespace(
        id='fictional-source',
        title='Fictional title',
        updated_at=1,
        created_at=1,
        chat={
            'history': {
                'currentId': 'fictional-message',
                'messages': {'fictional-message': {'role': 'user', 'content': 'Fictional text'}},
            }
        },
    )
    search = AsyncMock(return_value=[chat])
    read = AsyncMock(return_value=chat)
    monkeypatch.setattr(builtin.Chats, 'get_chats_by_user_id_and_search_text', search)
    monkeypatch.setattr(builtin.Chats, 'get_chat_by_id_and_user_id', read)
    request, user = Request({'type': 'http', 'headers': []}), {'id': 'fictional-fern'}
    found = json.loads(await builtin.search_chats('Fictional', __request__=request, __user__=user))
    viewed = json.loads(await builtin.view_chat(chat.id, __request__=request, __user__=user))
    assert found[0]['id'] == chat.id
    assert viewed['messages'] == [{'role': 'user', 'content': 'Fictional text'}]
    search.assert_awaited_once()
    read.assert_awaited_once_with(chat.id, user['id'])
