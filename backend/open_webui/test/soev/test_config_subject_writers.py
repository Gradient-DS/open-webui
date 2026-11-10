"""All config upsert callers validate platform identity policy before any writes."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from open_webui import config
from open_webui.models import config as config_store
from open_webui.models.config import Config
from open_webui.routers import configs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def config_writer(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(Config, 'PERSISTENT_ENABLED', True)
    monkeypatch.setattr(Config, 'OAUTH_PERSISTENT_ENABLED', True)
    initial = {'oauth.sub_claim': 'sub', 'ui.enable_signup': False}
    monkeypatch.setattr(Config, 'DEFAULTS', dict(initial))
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Config.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(config_store, 'get_async_db', context)
    await Config.upsert(initial)
    event = AsyncMock()
    monkeypatch.setattr(configs, 'publish_event', event)
    app = FastAPI()
    app.include_router(configs.router, prefix='/api/v1/configs')
    app.dependency_overrides[configs.get_admin_user] = lambda: SimpleNamespace(id='cedar', role='admin')
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://chat.example.invalid'
    ) as http:
        yield SimpleNamespace(http=http, sessions=sessions, initial=initial, event=event)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('persistent,oauth_persistent', [(True, True), (True, False), (False, False)])
@pytest.mark.parametrize('claim', ['email', 'preferred_username', {'claim': 'sub'}])
async def test_upsert_rejects_entire_batch_before_database_or_default_mutation(
    config_writer, monkeypatch, persistent, oauth_persistent, claim
):
    ctx = config_writer
    monkeypatch.setattr(Config, 'PERSISTENT_ENABLED', persistent)
    monkeypatch.setattr(Config, 'OAUTH_PERSISTENT_ENABLED', oauth_persistent)
    with pytest.raises(HTTPException) as error:
        await Config.upsert({'ui.enable_signup': True, 'oauth.sub_claim': claim})
    assert error.value.status_code == 403
    assert Config.DEFAULTS == ctx.initial
    async with ctx.sessions() as db:
        assert dict((await db.execute(select(Config.key, Config.value))).all()) == ctx.initial


@pytest.mark.asyncio
async def test_config_import_refuses_custom_subject_without_partial_write_or_event(config_writer):
    ctx = config_writer
    response = await ctx.http.post(
        '/api/v1/configs/import', json={'config': {'ui.enable_signup': True, 'oauth.sub_claim': 'email'}}
    )
    assert response.status_code == 403
    assert await Config.get_all() == ctx.initial
    assert Config.DEFAULTS == ctx.initial
    ctx.event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('claim', [None, '', 'sub'])
async def test_config_import_accepts_sub_or_unset_and_other_changes(config_writer, claim):
    ctx = config_writer
    response = await ctx.http.post(
        '/api/v1/configs/import', json={'config': {'ui.enable_signup': True, 'oauth.sub_claim': claim}}
    )
    assert response.status_code == 200
    assert await Config.get('oauth.sub_claim') == claim
    assert await Config.get('ui.enable_signup') is True
    ctx.event.assert_awaited_once()


@pytest.mark.asyncio
async def test_standalone_upsert_keeps_custom_subject_configuration(config_writer, monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    await Config.upsert({'oauth.sub_claim': 'email'})
    assert await Config.get('oauth.sub_claim') == 'email'
