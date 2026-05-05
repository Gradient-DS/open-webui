"""Guards `_handle_revoked_item` and the source_access_revoked error
loop branch in `_sync_via_pipeline`.

When the loader-worker reports a file as ``source_access_revoked`` the
worker must:
  * remove the row from the KB join table,
  * purge that file_id's vectors from the KB collection,
  * hard-delete the File row only if no other KB references it,
  * emit a Socket.IO ``<provider>:file:deleted`` event for live UI update,
  * count the file as "removed" (not "failed") in the toast counters.

Item must NOT appear in ``failed_files`` (it's a remove, not a failure).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_webui.services.sync.base_worker import BaseSyncWorker


class _StubWorker(BaseSyncWorker):
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
    worker = _StubWorker.__new__(_StubWorker)
    worker.knowledge_id = 'kb-test'
    worker.user_id = 'user-test'
    worker.sources = []
    return worker


@pytest.mark.asyncio
async def test_handle_revoked_item_removes_from_kb():
    worker = _make_worker()
    existing = SimpleNamespace(id='stub-item-1')

    sio_mock = AsyncMock()

    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
            return_value=existing,
        ),
        patch(
            'open_webui.services.sync.base_worker.Knowledges.remove_file_from_knowledge_by_id',
        ) as mock_remove,
        patch(
            'open_webui.services.sync.base_worker.VECTOR_DB_CLIENT.delete',
        ) as mock_delete,
        patch(
            'open_webui.services.sync.base_worker.Knowledges.get_knowledge_files_by_file_id',
            return_value=[],
        ),
        patch(
            'open_webui.services.sync.base_worker.DeletionService.delete_file',
        ) as mock_delete_file,
        patch(
            'open_webui.services.sync.base_worker.asyncio.to_thread',
            new=AsyncMock(),
        ) as mock_to_thread,
        patch(
            'open_webui.socket.main.sio',
            sio_mock,
        ),
    ):
        result = await worker._handle_revoked_item('stub-item-1')

    assert result == 1
    mock_remove.assert_called_once_with('kb-test', 'stub-item-1')
    mock_delete.assert_called_once()
    mock_to_thread.assert_awaited_once()
    sio_mock.emit.assert_awaited_once()
    args, kwargs = sio_mock.emit.call_args
    assert args[0] == 'stub:file:deleted'
    assert args[1]['reason'] == 'access_revoked'
    assert args[1]['file_id'] == 'stub-item-1'


@pytest.mark.asyncio
async def test_handle_revoked_item_no_existing_file():
    worker = _make_worker()
    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
            return_value=None,
        ),
        patch(
            'open_webui.services.sync.base_worker.Knowledges.remove_file_from_knowledge_by_id',
        ) as mock_remove,
    ):
        result = await worker._handle_revoked_item('stub-missing')
    assert result == 0
    mock_remove.assert_not_called()


@pytest.mark.asyncio
async def test_handle_revoked_item_other_kb_references_preserve_file():
    """When another KB still references the file, don't hard-delete it."""
    worker = _make_worker()
    existing = SimpleNamespace(id='stub-item-1')
    other_ref = SimpleNamespace(id='other-kb', file_id='stub-item-1')

    with (
        patch(
            'open_webui.services.sync.base_worker.Files.get_file_by_id',
            return_value=existing,
        ),
        patch(
            'open_webui.services.sync.base_worker.Knowledges.remove_file_from_knowledge_by_id',
        ),
        patch(
            'open_webui.services.sync.base_worker.VECTOR_DB_CLIENT.delete',
        ),
        patch(
            'open_webui.services.sync.base_worker.Knowledges.get_knowledge_files_by_file_id',
            return_value=[other_ref],
        ),
        patch(
            'open_webui.services.sync.base_worker.DeletionService.delete_file',
        ) as mock_delete_file,
        patch(
            'open_webui.services.sync.base_worker.asyncio.to_thread',
            new=AsyncMock(),
        ) as mock_to_thread,
        patch(
            'open_webui.socket.main.sio',
            AsyncMock(),
        ),
    ):
        result = await worker._handle_revoked_item('stub-item-1')

    assert result == 1
    # KB join row was removed (we don't assert on remove_file_from_knowledge_by_id
    # here — the prior test covers it). Critical assertion: the File row was NOT
    # hard-deleted because another KB still has it.
    mock_to_thread.assert_not_called()
    mock_delete_file.assert_not_called()


@pytest.mark.asyncio
async def test_sync_via_pipeline_revoked_count_in_total_deleted():
    """End-to-end: an error with code='source_access_revoked' rolls into
    files_removed (not files_failed) and the file is excluded from
    failed_files."""
    worker = _make_worker()
    worker._update_sync_status = AsyncMock()
    worker._save_sources = AsyncMock()
    worker._submit_pipeline_job = AsyncMock(return_value='job-1')
    worker._fail_mark_outstanding_stubs = AsyncMock(return_value=0)
    worker._handle_revoked_item = AsyncMock(return_value=1)

    fake_status = {
        'status': 'partial',
        'items_completed': 1,
        'items_failed': 1,
        'items': [{'file_id': 'stub-ok', 'stage': 'ok'}],
        'errors': [
            {
                'file_id': 'stub-revoked',
                'error_code': 'source_access_revoked',
                'error': 'access lost',
            },
        ],
        'stage_counts': {},
    }
    worker._track_job_progress = AsyncMock(return_value=fake_status)

    fake_kb = SimpleNamespace(meta={'stub_sync': {}})
    submit_payload: list[Dict[str, Any]] = [
        {'item': {'id': 'ok', 'name': 'ok.docx'}},
        {'item': {'id': 'revoked', 'name': 'revoked.docx'}},
    ]

    with (
        patch(
            'open_webui.services.sync.base_worker.Knowledges.get_knowledge_by_id',
            return_value=fake_kb,
        ),
        patch(
            'open_webui.services.sync.base_worker.Knowledges.update_knowledge_meta_by_id',
        ),
    ):
        result = await worker._sync_via_pipeline(
            all_files_to_process=submit_payload,
            total_files=2,
            added_file_ids={'stub-ok'},
            updated_file_ids={'stub-revoked'},
            unchanged_count=0,
            total_deleted=0,
        )

    worker._handle_revoked_item.assert_awaited_once_with('stub-revoked')
    # The revoked file rolled into files_removed.
    assert result['files_removed'] == 1
    assert result['files_added'] == 1
    # And dropped out of files_failed.
    assert result['files_failed'] == 0
    # And does NOT appear in failed_files.
    assert all(f['filename'] != 'stub-revoked' for f in result['failed_files'])
