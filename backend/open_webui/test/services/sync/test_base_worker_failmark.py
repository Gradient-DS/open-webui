"""Guards _fail_mark_outstanding_stubs lifecycle behaviour.

The fail-mark sweep is the load-bearing piece of Phase 3: every non-clean
exit of `_sync_via_pipeline` (submit failure, timeout, cancellation) must
flip every outstanding stub File row to a terminal status, otherwise the
KB UI shows infinite spinners after a stuck sync (the 2026-04-29
incident).

Cases covered:
- stub in pending → 'error'
- stub in completed → unchanged (/ingest's terminal write wins)
- stub in error → unchanged (already terminal)
- missing _current_job_stub_file_ids → no-op
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from open_webui.services.sync.base_worker import BaseSyncWorker


class _StubWorker(BaseSyncWorker):
    """Minimal concrete subclass — abstract methods filled in with no-ops.

    Fail-mark only depends on instance attribute `_current_job_stub_file_ids`
    and the global `Files` model; constructor args are bypassed for testing.
    """

    meta_key = 'stub_sync'
    file_id_prefix = 'stub-'
    event_prefix = 'stub'
    provider_slug = 'stub'
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


def _make_worker():
    """Build a worker without invoking BaseSyncWorker.__init__ (DB-free)."""
    worker = _StubWorker.__new__(_StubWorker)
    worker.knowledge_id = 'kb-test'
    worker.user_id = 'user-test'
    return worker


@pytest.mark.asyncio
async def test_fail_mark_transitions_pending_stub_to_error():
    worker = _make_worker()
    worker._current_job_stub_file_ids = ['stub-pending']
    pending = SimpleNamespace(data={'status': 'pending'})

    updates: list[tuple[str, dict]] = []

    def fake_update(file_id, data):
        updates.append((file_id, data))
        return None

    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
            return_value=pending,
        ),
        patch(
            'open_webui.services.sync.base_worker.Files.update_file_data_by_id',
            side_effect=fake_update,
        ),
    ):
        changed = await worker._fail_mark_outstanding_stubs('boom')

    assert changed == 1
    assert updates == [('stub-pending', {'status': 'error', 'error': 'boom'})]


@pytest.mark.asyncio
async def test_fail_mark_skips_completed_stubs():
    worker = _make_worker()
    worker._current_job_stub_file_ids = ['stub-completed']
    completed = SimpleNamespace(data={'status': 'completed'})

    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
            return_value=completed,
        ),
        patch(
            'open_webui.services.sync.base_worker.Files.update_file_data_by_id',
        ) as mock_update,
    ):
        changed = await worker._fail_mark_outstanding_stubs('ignored')

    assert changed == 0
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_fail_mark_skips_already_errored_stubs():
    worker = _make_worker()
    worker._current_job_stub_file_ids = ['stub-error']
    errored = SimpleNamespace(data={'status': 'error'})

    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
            return_value=errored,
        ),
        patch(
            'open_webui.services.sync.base_worker.Files.update_file_data_by_id',
        ) as mock_update,
    ):
        changed = await worker._fail_mark_outstanding_stubs('ignored')

    assert changed == 0
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_fail_mark_no_op_when_attr_missing():
    worker = _make_worker()
    # Deliberately do NOT set _current_job_stub_file_ids — this is the
    # state when sync() is called before any submit happened.
    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
        ) as mock_get,
        patch(
            'open_webui.services.sync.base_worker.Files.update_file_data_by_id',
        ) as mock_update,
    ):
        changed = await worker._fail_mark_outstanding_stubs('nothing-to-do')

    assert changed == 0
    mock_get.assert_not_called()
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_fail_mark_uses_custom_error_status_for_cancellation():
    worker = _make_worker()
    worker._current_job_stub_file_ids = ['stub-cancelled']
    pending = SimpleNamespace(data={'status': 'downloading'})
    updates: list[tuple[str, dict]] = []

    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
            return_value=pending,
        ),
        patch(
            'open_webui.services.sync.base_worker.Files.update_file_data_by_id',
            side_effect=lambda fid, d: updates.append((fid, d)),
        ),
    ):
        changed = await worker._fail_mark_outstanding_stubs(
            'Sync cancelled by user',
            error_status='cancelled',
        )

    assert changed == 1
    assert updates[0][1]['status'] == 'cancelled'
