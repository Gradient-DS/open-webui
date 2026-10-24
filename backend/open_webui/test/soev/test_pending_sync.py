from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from open_webui.models import soev_pending_sync as store
from open_webui.soev import acting, assistant_files, folder_files, pending_sync
from open_webui.test.soev.test_keycloak_token import token_app as token_app
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def pending_store(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(store.SoevPendingSync.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    monkeypatch.setattr(store, 'get_async_db_context', context)
    monkeypatch.setattr(pending_sync, '_last_checked', {})
    yield store.PendingSyncs
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['folder_files', 'assistant_files'])
async def test_waiting_sync_writes_one_durable_owner_marker(pending_store, monkeypatch, kind):
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:fern')
    module = folder_files if kind == 'folder_files' else assistant_files
    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(module, 'sync', sync)
    await pending_sync.run('fern', kind, 'project')
    await pending_sync.run('fern', kind, 'project')
    rows = await pending_store.get_by_owner('fern')
    assert len(rows) == 1 and rows[0].kind == kind
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:lark')
    with pytest.raises(PermissionError):
        await pending_sync.run('fern', kind, 'project')
    assert sync.await_count == 2


@pytest.mark.asyncio
async def test_marker_settlement_is_owner_scoped_throttled_and_keeps_waiting_or_failed_work(pending_store, monkeypatch):
    monkeypatch.setattr(pending_sync, 'configured', lambda: True)
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:fern')
    for resource in ('done', 'waiting', 'failed'):
        await pending_store.mark('fern', 'assistant_files', resource)
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:lark')
    background = BackgroundTasks()
    await pending_sync.on_request('lark', background)
    assert not background.tasks
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:fern')

    async def sync(resource_id):
        if resource_id == 'failed':
            raise RuntimeError('fictional failure')
        return resource_id == 'waiting'

    monkeypatch.setattr(assistant_files, 'sync', AsyncMock(side_effect=sync))
    await pending_sync.on_request('fern', background)
    assert len(background.tasks) == 1
    await background()
    assert {r.resource_id for r in await pending_store.get_by_owner('fern')} == {'waiting', 'failed'}
    background = BackgroundTasks()
    await pending_sync.on_request('fern', background)
    assert not background.tasks


@pytest.mark.asyncio
async def test_a_new_marker_is_not_erased_by_settling_an_older_snapshot(pending_store, monkeypatch):
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:fern')
    await pending_store.mark('fern', 'folder_files', 'project')
    old = (await pending_store.get_by_owner('fern'))[0]
    await pending_store.mark('fern', 'folder_files', 'project')
    await pending_store.forget(old)
    assert len(await pending_store.get_by_owner('fern')) == 1


@pytest.mark.asyncio
async def test_owner_request_background_sync_keeps_the_inherited_oidc_token(token_app, pending_store, monkeypatch):
    ctx = token_app
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:fern')
    await pending_store.mark('fern', 'folder_files', 'project')
    monkeypatch.setattr(pending_sync, 'configured', lambda: True)

    async def sync(resource_id):
        assert resource_id == 'project'
        await ctx.api.get('/v1/owner-background', as_user=acting.requester_ref())
        return False

    monkeypatch.setattr(folder_files, 'sync', sync)
    assert (await ctx.browser.get('/call/product')).status_code == 200
    background = [r for r in ctx.requests if r.url.path == '/v1/owner-background']
    assert len(background) == 1
    assert background[0].headers['X-Soev-User-Token'] == 'fictional-access-token'
    assert not await pending_store.get_by_owner('fern')
