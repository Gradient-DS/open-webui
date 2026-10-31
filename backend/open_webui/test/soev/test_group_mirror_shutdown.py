"""The group reconciler never holds up application shutdown."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.soev import group_mirror


@pytest.mark.asyncio
async def test_stop_ends_a_reconciler_whose_cancellation_is_swallowed(monkeypatch):
    """A cancel lost inside a reconcile (as in a database context) still ends the loop."""
    started = asyncio.Event()
    calls = 0

    async def swallowing_reconcile(**kwargs):
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(group_mirror, 'reconcile', swallowing_reconcile)
    monkeypatch.setattr(group_mirror, '_pull_due', AsyncMock(return_value=True))
    monkeypatch.setattr('open_webui.config.SOEV_API_URL', 'https://directory.invalid')
    monkeypatch.setattr('open_webui.config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', 5)
    monkeypatch.setattr('open_webui.config.SOEV_GROUP_RECONCILE_MINUTES', 0)
    app = SimpleNamespace(state=SimpleNamespace())

    async def requested_refresh(stop):
        await group_mirror.ensure_snapshot()
        await stop.wait()

    monkeypatch.setattr(group_mirror, '_run', requested_refresh)
    group_mirror.start_reconciler(app)
    await asyncio.wait_for(started.wait(), 5)

    await asyncio.wait_for(group_mirror.stop_reconciler(app, timeout=5), 6)

    assert app.state.soev_group_reconciler.done()
    finished = calls
    await asyncio.sleep(0.3)
    assert calls == finished


@pytest.mark.asyncio
async def test_idle_application_never_polls_directory(monkeypatch):
    reconcile = AsyncMock()
    monkeypatch.setattr(group_mirror, 'reconcile', reconcile)
    app = SimpleNamespace(state=SimpleNamespace())
    group_mirror.start_reconciler(app)
    await asyncio.sleep(0.01)
    await group_mirror.stop_reconciler(app)
    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_cancels_a_pull_after_its_request_disconnected(monkeypatch):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def pull(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(group_mirror.config, 'SOEV_API_URL', 'https://directory.invalid')
    monkeypatch.setattr(group_mirror, '_pull_due', AsyncMock(return_value=True))
    monkeypatch.setattr(group_mirror, 'reconcile', pull)
    app = SimpleNamespace(state=SimpleNamespace())
    group_mirror.start_reconciler(app)
    request = asyncio.create_task(group_mirror.ensure_snapshot())
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await group_mirror.stop_reconciler(app)
    assert cancelled.is_set()
    assert group_mirror._snapshot_flight[1].done()


@pytest.mark.asyncio
@pytest.mark.parametrize('credential', ['jwt', 'sk-fictional'])
async def test_verified_requests_refresh_directory_on_demand(monkeypatch, credential):
    from fastapi import BackgroundTasks, Request, Response
    from fastapi.security import HTTPAuthorizationCredentials
    from open_webui.models.users import UserModel
    from open_webui.utils import auth

    user = UserModel(
        id='fictional-fern',
        name='Fern',
        email='fern@example.invalid',
        role='admin',
        created_at=0,
        updated_at=0,
        last_active_at=0,
    )
    ensure = AsyncMock()
    monkeypatch.setattr(group_mirror, 'ensure_snapshot', ensure)
    monkeypatch.setattr(auth, 'decode_token', lambda token: {'id': user.id})
    monkeypatch.setattr(auth, 'is_valid_token', AsyncMock(return_value=True))
    monkeypatch.setattr(auth.Users, 'get_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(auth.Users, 'get_user_by_api_key', AsyncMock(return_value=user))
    monkeypatch.setattr(auth.Users, 'update_last_active_by_id', AsyncMock())
    monkeypatch.setattr(auth.Config, 'get_many', AsyncMock(return_value={'auth.enable_api_keys': True}))
    app = SimpleNamespace(state=SimpleNamespace(redis=None))
    request = Request({'type': 'http', 'app': app, 'path': '/api/v1/chats/', 'headers': []})
    resolved = await auth.get_current_user(
        request,
        Response(),
        BackgroundTasks(),
        HTTPAuthorizationCredentials(scheme='Bearer', credentials=credential),
    )
    assert resolved.id == user.id
    ensure.assert_awaited_once_with()
