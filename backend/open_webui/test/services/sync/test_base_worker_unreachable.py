"""Guards sync()'s top-level connectivity handler attribution.

A connect/transport failure is transient and shares one handler — but the
*attribution* must be correct:
  * PipelineUnreachableError  → "Document ingestion service ... unreachable"
    (the loader-worker is down; the sync source was reached fine).
  * any other ConnectionError → "Sync source is temporarily unreachable ..."
    (OneDrive/GoogleDrive/Confluence source fetch failed).

Both keep the IDENTICAL skipped-cycle result shape (transient=True) and never
stamp last_sync_at, so the next tick retries. We drive the real handler by
making an early sync() step raise the target exception — the handler
classifies purely on exception type, not on origin.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from open_webui.services.sync.base_worker import BaseSyncWorker
from open_webui.services.sync.pipeline_client import PipelineUnreachableError


class _StubWorker(BaseSyncWorker):
    meta_key = 'stub_sync'
    file_id_prefix = 'stub-'
    event_prefix = 'stub'
    provider_slug = 'confluence'
    internal_request_path = '/internal/stub-sync'
    max_files_config = 100
    source_clear_delta_keys: list[str] = []

    def _create_client(self):
        return None

    async def _close_client(self):
        return None

    def _is_supported_file(self, item):
        return True

    async def _collect_folder_files(self, source):
        return [], 0

    async def _collect_single_file(self, source):
        return None

    async def _download_file_content(self, file_info):
        return b''

    def _get_provider_storage_headers(self, item_id):
        return {}

    def _get_provider_file_meta(self, **kwargs):
        return {}

    async def _sync_permissions(self):
        return None

    def _get_cloud_hash(self, file_info):
        return None

    async def _verify_source_access(self, source):
        return True

    async def _handle_revoked_source(self, source):
        return 0


def _make_worker() -> _StubWorker:
    """Build a worker without invoking BaseSyncWorker.__init__ (DB-free)."""
    worker = _StubWorker.__new__(_StubWorker)
    worker.knowledge_id = 'kb-test'
    worker.user_id = 'user-test'
    worker.sources = []
    return worker


async def _run_sync_with_error(worker: _StubWorker, error: Exception):
    """Drive sync() into its top-level connectivity handler.

    `_sync_permissions` runs early in sync() (before any source fetch); we
    raise the target exception there so the real classification branch in the
    `except (ConnectionError, httpx.TransportError)` handler executes. The
    captured `_update_sync_status` calls let us assert the attributed message.
    """
    status_calls: list[tuple[tuple, dict]] = []

    async def record_status(*args, **kwargs):
        status_calls.append((args, kwargs))

    worker._update_sync_status = AsyncMock(side_effect=record_status)
    worker._sync_permissions = AsyncMock(side_effect=error)

    result = await worker.sync()
    return result, status_calls


@pytest.mark.asyncio
async def test_pipeline_unreachable_attributes_to_ingestion_service():
    worker = _make_worker()
    err = PipelineUnreachableError('loader-worker unreachable at http://lw:8202/...: refused')

    result, status_calls = await _run_sync_with_error(worker, err)

    # Transient skipped-cycle result shape preserved.
    assert result['transient'] is True
    assert result['files_processed'] == 0
    assert result['files_failed'] == 0
    assert result['total_found'] == 0
    assert result['deleted_count'] == 0
    assert result['failed_files'] == []
    # Attributed to the ingestion service, NOT the source.
    assert result['error'] == 'document ingestion service (loader-worker) unreachable'

    # The 'failed' status carried the loader-worker-specific message.
    failed_calls = [(a, k) for (a, k) in status_calls if a and a[0] == 'failed']
    assert len(failed_calls) == 1
    _, kwargs = failed_calls[0]
    assert 'Document ingestion service is temporarily unreachable' in kwargs['error']

    # No success status was stamped → last_sync_at never written.
    assert not any(a and a[0] in ('completed', 'partial') for (a, _) in status_calls)


@pytest.mark.asyncio
async def test_source_connection_error_attributes_to_source():
    worker = _make_worker()
    err = ConnectionError('confluence DNS failure')

    result, status_calls = await _run_sync_with_error(worker, err)

    assert result['transient'] is True
    # Attributed to the source provider, unchanged behaviour.
    assert result['error'] == 'confluence unreachable'

    failed_calls = [(a, k) for (a, k) in status_calls if a and a[0] == 'failed']
    assert len(failed_calls) == 1
    _, kwargs = failed_calls[0]
    assert kwargs['error'] == (
        'Sync source is temporarily unreachable — the next scheduled sync will retry automatically.'
    )


@pytest.mark.asyncio
async def test_httpx_transport_error_attributes_to_source():
    """A raw httpx.TransportError (not the pipeline subclass) is a source failure."""
    worker = _make_worker()
    err = httpx.ConnectError('source connect failed')

    result, _ = await _run_sync_with_error(worker, err)

    assert result['transient'] is True
    assert result['error'] == 'confluence unreachable'
