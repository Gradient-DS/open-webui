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
    group_mirror.start_reconciler(app)
    await asyncio.wait_for(started.wait(), 5)

    await asyncio.wait_for(group_mirror.stop_reconciler(app, timeout=5), 6)

    assert app.state.soev_group_reconciler.done()
    finished = calls
    await asyncio.sleep(0.3)
    assert calls == finished
