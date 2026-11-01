"""Request-start refresh remains bounded without a database read per request."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from open_webui.soev import group_mirror


@pytest.mark.asyncio
async def test_concurrent_requests_share_the_due_check_and_pull(monkeypatch):
    monkeypatch.setattr(group_mirror.config, 'SOEV_API_URL', 'https://directory.invalid')
    entered, release = asyncio.Event(), asyncio.Event()

    async def due(**kwargs):
        entered.set()
        await release.wait()
        return True

    check, pull = AsyncMock(side_effect=due), AsyncMock()
    monkeypatch.setattr(group_mirror, '_pull_due', check)
    monkeypatch.setattr(group_mirror, 'reconcile', pull)
    requests = [asyncio.create_task(group_mirror.ensure_snapshot()) for _ in range(25)]
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*requests)
    assert check.await_count == pull.await_count == 1


@pytest.mark.asyncio
async def test_completed_pull_skips_db_until_next_scheduled_start(group_db, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(group_mirror, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_RECONCILE_MINUTES', 15)
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', 5)
    monkeypatch.setattr(group_mirror, '_fetch_groups', AsyncMock(return_value={}))
    await group_mirror.reconcile(db=group_db)
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(group_mirror, '_pull_due', check)
    for _ in range(20):
        await group_mirror.ensure_snapshot(db=group_db)
    check.assert_not_awaited()
    clock[0] += 5 / 3
    await group_mirror.ensure_snapshot(db=group_db)
    check.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_request_does_not_cancel_the_shared_pull(monkeypatch):
    monkeypatch.setattr(group_mirror.config, 'SOEV_API_URL', 'https://directory.invalid')
    entered, release = asyncio.Event(), asyncio.Event()

    async def pull(**kwargs):
        entered.set()
        await release.wait()

    reconcile = AsyncMock(side_effect=pull)
    monkeypatch.setattr(group_mirror, '_pull_due', AsyncMock(return_value=True))
    monkeypatch.setattr(group_mirror, 'reconcile', reconcile)
    first = asyncio.create_task(group_mirror.ensure_snapshot())
    await entered.wait()
    second = asyncio.create_task(group_mirror.ensure_snapshot())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    await second
    reconcile.assert_awaited_once()
