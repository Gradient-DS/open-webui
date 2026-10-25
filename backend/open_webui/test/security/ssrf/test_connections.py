"""Verify the privilege and browser boundaries around configurable base URLs."""

import httpx
import pytest
from fastapi import FastAPI
from open_webui.routers import configs, ollama, tools
from open_webui.utils import auth

from .conftest import INTERNAL


@pytest.mark.parametrize(
    'module,path',
    [
        (configs, '/tool_servers/verify'),
        (configs, '/terminal_servers/verify'),
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
