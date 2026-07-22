"""ConfluenceSyncWorker._sync_permissions — false-positive suspension fixes.

These pin the classification + consecutive-failure gate that keeps a transient
404/403/timeout (or a single 401 blip) from suspending the shared Confluence KB:

* a ``None`` probe result (HTTP 404) is NOT a credential denial → no suspension;
* a single 401 increments the counter but does not suspend (threshold is 2);
* a second consecutive 401 suspends, with the basic-mode reason in basic auth;
* a 403 / transient error never suspends and never increments the counter;
* any access success resets the counter and un-suspends a suspended KB.

The worker is built with ``__new__`` + manual attribute assignment so the
heavyweight ``BaseSyncWorker.__init__`` is skipped. The per-source client is
injected by patching ``_client_for``. Runs without pytest-asyncio via
``asyncio.run``, matching the other service worker tests.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from open_webui.services.confluence.sync_worker import ConfluenceSyncWorker


def _run(coro):
    return asyncio.run(coro)


def _make_worker(auth_mode: str = 'basic') -> ConfluenceSyncWorker:
    """Build a worker without running BaseSyncWorker.__init__.

    A single page source with a cloud_id so the probe loop runs.
    """
    worker = ConfluenceSyncWorker.__new__(ConfluenceSyncWorker)
    worker.knowledge_id = 'kb-test'
    worker.user_id = 'user-test'
    worker._auth_mode = auth_mode
    worker.sources = [{'cloud_id': 'c1', 'item_id': 'page-1', 'type': 'file', 'name': 'Page 1'}]
    return worker


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request('GET', 'https://example.atlassian.net')
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError('error', request=request, response=response)


def _patch_models(kb, update_meta):
    """Patch Knowledges.get/update used by all three helper paths."""
    return (
        patch(
            'open_webui.services.confluence.sync_worker.Knowledges.get_knowledge_by_id',
            new=AsyncMock(return_value=kb),
        ),
        patch(
            'open_webui.services.confluence.sync_worker.Knowledges.update_knowledge_meta_by_id',
            new=update_meta,
        ),
    )


# ---------------------------------------------------------------------
# 404 / None → NOT a credential denial
# ---------------------------------------------------------------------


def test_none_result_404_does_not_suspend():
    """A probe returning None (404) must not suspend and must not count as a 401."""
    worker = _make_worker()
    client = SimpleNamespace(get_page=AsyncMock(return_value=None), get_space=AsyncMock(return_value=None))
    worker._client_for = AsyncMock(return_value=client)
    update_meta = AsyncMock()
    p_get, p_update = _patch_models(SimpleNamespace(meta={'confluence_sync': {}}), update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=AsyncMock()):
        _run(worker._sync_permissions())
    # No positive access and no 401 → state untouched, no meta write at all.
    update_meta.assert_not_awaited()


# ---------------------------------------------------------------------
# 401 → consecutive-failure gate (threshold 2)
# ---------------------------------------------------------------------


def test_single_401_increments_counter_without_suspending():
    worker = _make_worker(auth_mode='basic')
    client = SimpleNamespace(get_page=AsyncMock(side_effect=_http_status_error(401)))
    worker._client_for = AsyncMock(return_value=client)
    update_meta = AsyncMock()
    suspend_status = AsyncMock()
    p_get, p_update = _patch_models(SimpleNamespace(meta={'confluence_sync': {}}), update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=suspend_status):
        _run(worker._sync_permissions())

    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert meta_arg['confluence_sync'].get('suspended_at') is None
    assert meta_arg['confluence_sync']['auth_fail_count'] == 1
    suspend_status.assert_not_awaited()


def test_second_consecutive_401_suspends_basic_reason():
    worker = _make_worker(auth_mode='basic')
    client = SimpleNamespace(get_page=AsyncMock(side_effect=_http_status_error(401)))
    worker._client_for = AsyncMock(return_value=client)
    # Counter already at 1 from a prior cycle.
    kb = SimpleNamespace(meta={'confluence_sync': {'auth_fail_count': 1}})
    update_meta = AsyncMock()
    p_get, p_update = _patch_models(kb, update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=AsyncMock()):
        _run(worker._sync_permissions())

    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert meta_arg['confluence_sync']['suspended_at'] is not None
    assert meta_arg['confluence_sync']['suspended_reason'] == 'service_credential_invalid'
    assert meta_arg['confluence_sync']['auth_fail_count'] == 2


def test_second_consecutive_401_suspends_oauth_reason():
    worker = _make_worker(auth_mode='oauth')
    client = SimpleNamespace(get_page=AsyncMock(side_effect=_http_status_error(401)))
    worker._client_for = AsyncMock(return_value=client)
    kb = SimpleNamespace(meta={'confluence_sync': {'auth_fail_count': 1}})
    update_meta = AsyncMock()
    p_get, p_update = _patch_models(kb, update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=AsyncMock()):
        _run(worker._sync_permissions())

    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert meta_arg['confluence_sync']['suspended_reason'] == 'owner_access_lost'


# ---------------------------------------------------------------------
# 403 / transient → no suspension, no counter increment
# ---------------------------------------------------------------------


def test_403_does_not_suspend_or_increment():
    worker = _make_worker()
    client = SimpleNamespace(get_page=AsyncMock(side_effect=_http_status_error(403)))
    worker._client_for = AsyncMock(return_value=client)
    update_meta = AsyncMock()
    p_get, p_update = _patch_models(SimpleNamespace(meta={'confluence_sync': {}}), update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=AsyncMock()):
        _run(worker._sync_permissions())
    update_meta.assert_not_awaited()


def test_transient_error_does_not_suspend_or_increment():
    worker = _make_worker()
    client = SimpleNamespace(get_page=AsyncMock(side_effect=httpx.ConnectError('down')))
    worker._client_for = AsyncMock(return_value=client)
    update_meta = AsyncMock()
    p_get, p_update = _patch_models(SimpleNamespace(meta={'confluence_sync': {}}), update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=AsyncMock()):
        _run(worker._sync_permissions())
    update_meta.assert_not_awaited()


# ---------------------------------------------------------------------
# success → reset counter + un-suspend
# ---------------------------------------------------------------------


def test_access_success_resets_counter_and_unsuspends():
    worker = _make_worker()
    client = SimpleNamespace(get_page=AsyncMock(return_value={'id': 'page-1'}))
    worker._client_for = AsyncMock(return_value=client)
    kb = SimpleNamespace(meta={'confluence_sync': {'suspended_at': 123, 'suspended_reason': 'x', 'auth_fail_count': 2}})
    update_meta = AsyncMock()
    p_get, p_update = _patch_models(kb, update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=AsyncMock()):
        _run(worker._sync_permissions())

    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert 'suspended_at' not in meta_arg['confluence_sync']
    assert 'suspended_reason' not in meta_arg['confluence_sync']
    assert 'auth_fail_count' not in meta_arg['confluence_sync']


def test_access_success_resets_counter_without_suspension():
    """Success after a single failure clears the counter (nothing was suspended)."""
    worker = _make_worker()
    client = SimpleNamespace(get_page=AsyncMock(return_value={'id': 'page-1'}))
    worker._client_for = AsyncMock(return_value=client)
    kb = SimpleNamespace(meta={'confluence_sync': {'auth_fail_count': 1}})
    update_meta = AsyncMock()
    p_get, p_update = _patch_models(kb, update_meta)
    with p_get, p_update, patch.object(worker, '_update_sync_status', new=AsyncMock()):
        _run(worker._sync_permissions())

    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert 'auth_fail_count' not in meta_arg['confluence_sync']
    assert 'suspended_at' not in meta_arg['confluence_sync']
