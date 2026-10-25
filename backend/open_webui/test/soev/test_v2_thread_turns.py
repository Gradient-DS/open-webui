"""The real completion path streams platform turns with no local content schema."""

import importlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from open_webui.models.config import Config
from open_webui.models.users import UserModel
from open_webui.soev import threads
from open_webui.test.soev import test_thread_organizations
from starlette.responses import StreamingResponse

main = importlib.import_module('open_webui.main')
organization_store = test_thread_organizations.organization_store


@pytest.mark.asyncio
async def test_full_v2_turn_streams_without_local_content(chat_http, organization_store, monkeypatch):
    from open_webui.utils import agent_v2

    repository, session, statements = organization_store
    model = {'id': 'fictional-model', 'owned_by': 'openai', 'info': {'meta': {}}}
    api = FastAPI()
    api.state.MODELS = {model['id']: model}
    api.state.thread_organizations = repository
    api.state.redis = None
    request = Request({'type': 'http', 'app': api, 'headers': [], 'state': {}})
    user = UserModel(
        id='fictional-fern',
        name='Fern',
        email='fern@example.invalid',
        role='admin',
        created_at=0,
        updated_at=0,
        last_active_at=0,
    )

    async def setting(key, default=None, **kwargs):
        return {'agent_api.selected_agent': 'test'}.get(key, default)

    monkeypatch.setattr(Config, 'get', setting)
    monkeypatch.setattr(main.Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(main, 'check_model_access', AsyncMock())
    monkeypatch.setattr(main.system_prompt, 'for_model', AsyncMock(return_value=None))
    monkeypatch.setattr(main, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=True))
    monkeypatch.setattr(agent_v2, '_documents_allowed', AsyncMock(return_value=True))
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    # Every facade is a tripwire even when a caller catches its exception.
    content_calls = []

    async def forbidden(*args, **kwargs):
        content_calls.append((args, kwargs))
        raise AssertionError('Local content facade called')

    for name, method in vars(type(main.Chats)).items():
        if not name.startswith('__') and callable(method):
            monkeypatch.setattr(main.Chats, name, forbidden)

    chat_id = None
    for index in range(2):
        form = {
            'model': model['id'],
            'stream': True,
            'messages': [{'role': 'user', 'content': f'Fictional turn {index}'}],
            'user_message': {'id': f'fictional-user-{index}', 'content': f'Fictional turn {index}'},
            'id': f'fictional-assistant-{index}',
            'parent_id': None if index == 0 else 'fictional-assistant-0',
            'chat_id': chat_id,
            'session_id': 'fictional-session',
        }
        response = await main.chat_completion(request, form, user)
        assert isinstance(response, StreamingResponse), response
        assert response.headers['cache-control'] == 'private, no-store'
        raw = ''.join([chunk.decode() if isinstance(chunk, bytes) else chunk async for chunk in response.body_iterator])
        assert '[DONE]' in raw
        chunks = [
            json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ') and line != 'data: [DONE]'
        ]
        assert not any('error' in chunk for chunk in chunks), raw
        assert any(chunk.get('choices') for chunk in chunks), raw
        records = await repository.list(user.id)
        assert len(records) == 1
        chat_id = records[0].id
    mutations = [r for r in chat_http.chat.requests if r.method == 'POST']
    assert [r.url.path for r in mutations] == ['/v1/chat/threads', '/v1/chat/threads/thr-1/inputs']
    assert [json.loads(r.content)['input']['text'] for r in mutations] == ['Fictional turn 0', 'Fictional turn 1']
    assert not content_calls
    assert not any('chat_message' in sql or 'summary' in sql for sql in statements)
