"""Runtime OAuth updates cannot restore a mutable platform account key."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.routers import auths


@pytest_asyncio.fixture
async def oauth_config_app(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    stored = {'oauth.sub_claim': 'sub', 'oauth.enable_signup': False}

    async def upsert(updates):
        stored.update(updates)

    write = AsyncMock(side_effect=upsert)
    read = AsyncMock(side_effect=lambda *keys: {key: stored[key] for key in keys if key in stored})
    monkeypatch.setattr(auths.Config, 'upsert', write)
    monkeypatch.setattr(auths.Config, 'get_many', read)
    app = FastAPI()
    app.include_router(auths.router, prefix='/api/v1/auths')
    app.dependency_overrides[auths.get_admin_user] = lambda: SimpleNamespace(id='cedar', role='admin')
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://chat.example.invalid'
    ) as http:
        yield SimpleNamespace(http=http, stored=stored, write=write, read=read)


@pytest.mark.asyncio
@pytest.mark.parametrize('claim', ['email', 'preferred_username', 'oid', 'SUB', ' sub '])
async def test_runtime_update_refuses_unsafe_subject_before_any_config_write(oauth_config_app, caplog, claim):
    ctx = oauth_config_app
    before = dict(ctx.stored)

    response = await ctx.http.post(
        '/api/v1/auths/admin/config/oauth', json={'OAUTH_SUB_CLAIM': claim, 'ENABLE_OAUTH_SIGNUP': True}
    )

    assert response.status_code == 403
    assert response.json() == {'detail': ERROR_MESSAGES.ACCESS_PROHIBITED}
    ctx.write.assert_not_awaited()
    ctx.read.assert_not_awaited()
    assert ctx.stored == before
    assert any(getattr(r, 'code', None) == 'oauth_unsafe_sub_claim_refused' for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'subject', [{}, {'OAUTH_SUB_CLAIM': None}, {'OAUTH_SUB_CLAIM': ''}, {'OAUTH_SUB_CLAIM': 'sub'}]
)
async def test_runtime_update_accepts_sub_or_unset_and_preserves_other_settings(oauth_config_app, subject):
    ctx = oauth_config_app
    response = await ctx.http.post('/api/v1/auths/admin/config/oauth', json={**subject, 'ENABLE_OAUTH_SIGNUP': True})
    assert response.status_code == 200
    expected = {'oauth.enable_signup': True}
    if subject.get('OAUTH_SUB_CLAIM') is not None:
        expected['oauth.sub_claim'] = subject['OAUTH_SUB_CLAIM']
    ctx.write.assert_awaited_once_with(expected)
    assert ctx.stored == {'oauth.sub_claim': 'sub', **expected}


@pytest.mark.asyncio
async def test_standalone_runtime_update_keeps_custom_subject_support(oauth_config_app, monkeypatch):
    ctx = oauth_config_app
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    response = await ctx.http.post('/api/v1/auths/admin/config/oauth', json={'OAUTH_SUB_CLAIM': 'email'})
    assert response.status_code == 200
    ctx.write.assert_awaited_once_with({'oauth.sub_claim': 'email'})
    assert ctx.stored['oauth.sub_claim'] == 'email'
