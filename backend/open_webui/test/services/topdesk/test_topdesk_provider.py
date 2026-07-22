"""TopdeskSyncProvider + TopdeskTokenManager + factory registration.

Uses ``asyncio.run`` (no pytest-asyncio dependency), matching the other TOPdesk
tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock

from open_webui.services.topdesk.auth import TOPDESK_AUTH_SENTINEL
from open_webui.services.topdesk.provider import TopdeskSyncProvider, TopdeskTokenManager
from open_webui.services.topdesk.sync_worker import TopdeskSyncWorker
from open_webui.services.sync.provider import (
    PROVIDER_FILE_ID_PREFIXES,
    file_id_prefix_for,
    get_sync_provider,
    get_token_manager,
)


def _run(coro):
    return asyncio.run(coro)


# ── TokenManager ─────────────────────────────────────────────────────────────


def test_token_manager_returns_sentinel_when_configured():
    tm = TopdeskTokenManager()
    with patch(
        'open_webui.services.topdesk.provider.service_auth_configured',
        new_callable=AsyncMock,
        return_value=True,
    ):
        assert _run(tm.get_valid_access_token('u', 'kb')) == TOPDESK_AUTH_SENTINEL
        assert _run(tm.has_stored_token('u', 'kb')) is True


def test_token_manager_returns_none_when_unconfigured():
    tm = TopdeskTokenManager()
    with patch(
        'open_webui.services.topdesk.provider.service_auth_configured',
        new_callable=AsyncMock,
        return_value=False,
    ):
        assert _run(tm.get_valid_access_token('u', 'kb')) is None
        assert _run(tm.has_stored_token('u', 'kb')) is False


def test_token_manager_delete_is_noop():
    tm = TopdeskTokenManager()
    assert _run(tm.delete_token('u', 'kb')) is False


# ── SyncProvider ─────────────────────────────────────────────────────────────


def test_provider_type_and_meta_key():
    p = TopdeskSyncProvider()
    assert p.get_provider_type() == 'topdesk'
    assert p.get_meta_key() == 'topdesk_sync'
    assert isinstance(p.get_token_manager(), TopdeskTokenManager)


def test_create_worker_forces_shared_loader_false():
    """TOPdesk has no loader-worker source client — use_shared_loader must be
    forced False even when the caller passes True (e.g. global flag on)."""
    p = TopdeskSyncProvider()
    app = type('App', (), {'state': type('S', (), {'config': type('C', (), {'USE_SHARED_LOADER': True})})})
    worker = p.create_worker(
        knowledge_id='kb-1',
        sources=[],
        access_token=TOPDESK_AUTH_SENTINEL,
        user_id='u',
        app=app,
        token_provider=None,
        use_shared_loader=True,
    )
    assert isinstance(worker, TopdeskSyncWorker)
    assert worker._use_shared_loader is False


# ── Factory registration ─────────────────────────────────────────────────────


def test_factory_returns_topdesk_provider_and_token_manager():
    assert isinstance(get_sync_provider('topdesk'), TopdeskSyncProvider)
    assert isinstance(get_token_manager('topdesk'), TopdeskTokenManager)


def test_registry_prefix_for_topdesk():
    assert PROVIDER_FILE_ID_PREFIXES['topdesk'] == 'topdesk-'
    assert file_id_prefix_for('topdesk') == 'topdesk-'
