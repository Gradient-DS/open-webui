"""Platform config writers refuse before local account or OAuth side effects."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from open_webui import config, env
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.routers import archives, auths, configs
from open_webui.soev.client import SoevApiError
from open_webui.utils.auth import get_admin_user, get_verified_user

main = importlib.import_module('open_webui.main')


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_platform_user_writers_refuse_before_config_or_identity_changes(monkeypatch, role):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    app = FastAPI()
    app.include_router(auths.router, prefix='/auths')
    app.add_api_route('/oauth/clients/{client_id}/authorize', main.oauth_client_authorize)
    actor = SimpleNamespace(id='fictional-person', role=role)
    app.dependency_overrides[get_verified_user] = lambda: actor
    app.dependency_overrides[get_async_session] = lambda: None
    writes = AsyncMock(side_effect=AssertionError('No configuration writes permitted'))
    create = AsyncMock(side_effect=AssertionError('No account creation permitted'))
    monkeypatch.setattr(Config, 'upsert', writes)
    monkeypatch.setattr(auths.Auths, 'insert_new_auth', create)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as api:
        signup = await api.post(
            '/auths/signup',
            json={
                'email': 'fern@example.invalid',
                'name': 'Fern',
                'password': 'Fictional-Password!42',
            },
        )
        oauth = await api.get('/oauth/clients/mcp:fictional-disabled/authorize')
    for response, detail in ((signup, ERROR_MESSAGES.ACCESS_PROHIBITED), (oauth, ERROR_MESSAGES.ACTION_PROHIBITED)):
        assert response.status_code == 403, response.text
        assert response.json() == {'detail': detail}
    writes.assert_not_called()
    create.assert_not_called()
