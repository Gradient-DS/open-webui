"""The group reconciler never holds up application shutdown."""

import asyncio
from types import SimpleNamespace

import pytest

from open_webui.soev import group_sync


@pytest.mark.asyncio
async def test_stop_ends_a_reconciler_whose_cancellation_is_swallowed(monkeypatch):
    """A cancel lost inside a reconcile (as in a database context) still ends the loop."""
    started = asyncio.Event()
    calls = 0

    async def swallowing_reconcile(table, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass

    async def no_retry(table, **kwargs):
        return None

    monkeypatch.setattr(group_sync, 'reconcile', swallowing_reconcile)
    monkeypatch.setattr(group_sync, 'retry_pending', no_retry)
    monkeypatch.setattr('open_webui.config.SOEV_GROUP_RECONCILE_MINUTES', 0)
    app = SimpleNamespace(state=SimpleNamespace())
    group_sync.start_reconciler(app)
    await asyncio.wait_for(started.wait(), 5)

    await asyncio.wait_for(group_sync.stop_reconciler(app, timeout=5), 6)

    assert app.state.soev_group_reconciler.done()
    finished = calls
    await asyncio.sleep(0.3)
    assert calls == finished
