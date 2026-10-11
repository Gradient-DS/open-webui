"""Verify the privilege and browser boundaries around configurable base URLs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from open_webui.routers import configs, ollama, openai, tools
from open_webui.utils import auth, chat

from .conftest import INTERNAL


@pytest.mark.parametrize(
    'module,path',
    [
        (configs, '/tool_servers/verify'),
        (configs, '/terminal_servers/verify'),
        (openai, '/verify'),
        (ollama, '/verify'),
        (tools, '/load/url'),
    ],
)
@pytest.mark.asyncio
async def test_connection_verification_requires_admin(module, path, user, http_boundary):
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(path, json={'url': INTERNAL, 'key': ''})
    assert response.status_code == 401, response.text
    assert http_boundary.sent == []


@pytest.mark.asyncio
async def test_user_direct_connection_is_dispatched_to_browser(user, http_boundary, monkeypatch):
    event = AsyncMock(return_value={'choices': []})
    monkeypatch.setattr(chat, 'get_event_call', AsyncMock(return_value=event))
    model = {'id': 'direct', 'direct': True, 'url': INTERNAL}
    form = {'model': 'direct', 'messages': [], 'metadata': {'user_id': user.id, 'session_id': 'test'}}
    await chat.generate_direct_chat_completion(SimpleNamespace(), form, user, {'direct': model})
    assert event.call_args.args[0]['type'] == 'request:chat:completion'
    assert event.call_args.args[0]['data']['model'] == model
    assert http_boundary.sent == []
