"""Platform config writers refuse before local account or OAuth side effects."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.routers import archives, auths, configs
from open_webui.soev import policy_jobs, product_config
from open_webui.soev.client import SoevApiError
from open_webui.utils.auth import get_admin_user, get_verified_user

main = importlib.import_module('open_webui.main')


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_platform_user_writers_refuse_before_config_or_identity_changes(monkeypatch, role):
    monkeypatch.setattr(product_config, 'v2_enabled', lambda: True)
    monkeypatch.setattr(policy_jobs, 'v2_enabled', lambda: True)
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
    for response in (signup, oauth):
        assert response.status_code == 403, response.text
        assert response.json() == {'detail': ERROR_MESSAGES.ACTION_PROHIBITED}
    writes.assert_not_called()
    create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('path', 'payload'),
    [
        ('/configs/import', {'config': {'ui.banners': []}}),
        ('/archives/admin/config', {'enable_user_archival': True}),
    ],
)
async def test_retained_admin_config_routes_return_fixed_403(monkeypatch, path, payload):
    monkeypatch.setattr(product_config, 'v2_enabled', lambda: True)
    app = FastAPI()
    app.add_exception_handler(SoevApiError, main.soev_api_error_handler)
    app.include_router(configs.router, prefix='/configs')
    app.include_router(archives.router, prefix='/archives')
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(id='fictional-admin', role='admin')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as api:
        response = await api.post(path, json=payload)
    assert response.status_code == 403, response.text
    assert response.json()['detail']['constraint'] == 'product_config:read_only'
