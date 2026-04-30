"""Guards `_sync_via_pipeline`'s counter math.

The toast must report what *actually changed* for the user — not the
loader-worker's raw `items_completed`. With pre-submit classification the
worker knows which submitted file_ids were originally `added` vs
`updated`; intersecting that with the loader-worker's per-item ok set
yields `files_added` / `files_updated`.

The cases below mirror the plan's parametrization and lock in the
"5 extra" regression: a re-sync that re-verifies 12 unchanged files must
NOT show `files_processed=17` when 5 were freshly added.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

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
    # _save_sources reads sources, but we patch it out below
    worker._update_sync_status = AsyncMock()
    worker._save_sources = AsyncMock()
    worker._submit_pipeline_job = AsyncMock(return_value='job-1')
    return worker


def _file_info(item_id: str) -> Dict[str, Any]:
    return {'item': {'id': item_id, 'name': f'{item_id}.docx'}}


@pytest.mark.asyncio
async def test_no_changes_case_routes_through_empty_branch():
    """0 added, 0 updated, 12 unchanged → no submit, files_processed=0."""
    worker = _make_worker()
    result = await worker._sync_via_pipeline(
        all_files_to_process=[],
        total_files=12,
        added_file_ids=set(),
        updated_file_ids=set(),
        unchanged_count=12,
        total_deleted=0,
    )
    assert result['files_processed'] == 0
    assert result['files_added'] == 0
    assert result['files_updated'] == 0
    assert result['files_unchanged'] == 12
    assert result['files_failed'] == 0
    worker._submit_pipeline_job.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'terminal, items_completed, items_failed, ok_ids, added_ids, updated_ids, '
    'unchanged_count, expect_added, expect_updated, expect_failed',
    [
        # 5 added, 0 updated, 0 unchanged, 0 failed
        ('completed', 5, 0, {f'stub-i{i}' for i in range(5)}, {f'stub-i{i}' for i in range(5)}, set(), 0, 5, 0, 0),
        # 5 added, 12 unchanged — must NOT inflate to 17 in files_processed
        ('completed', 5, 0, {f'stub-i{i}' for i in range(5)}, {f'stub-i{i}' for i in range(5)}, set(), 12, 5, 0, 0),
        # 0/2/0/0 → only updated bucket
        ('completed', 2, 0, {'stub-u0', 'stub-u1'}, set(), {'stub-u0', 'stub-u1'}, 0, 0, 2, 0),
        # 12 added + 2 failed (terminal=partial)
        ('partial', 12, 2, {f'stub-a{i}' for i in range(12)}, {f'stub-a{i}' for i in range(12)}, set(), 0, 12, 0, 2),
        # 0 completed + 1 failed (terminal=failed) — ok_ids empty
        ('failed', 0, 1, set(), {'stub-a0'}, set(), 0, 0, 0, 1),
    ],
)
async def test_counter_math_intersects_ok_ids_with_classification(
    terminal,
    items_completed,
    items_failed,
    ok_ids,
    added_ids,
    updated_ids,
    unchanged_count,
    expect_added,
    expect_updated,
    expect_failed,
):
    worker = _make_worker()
    fake_status = {
        'status': terminal,
        'items_completed': items_completed,
        'items_failed': items_failed,
        'items': [{'file_id': fid, 'stage': 'ok'} for fid in ok_ids],
        'errors': [],
        'stage_counts': {},
    }
    worker._track_job_progress = AsyncMock(return_value=fake_status)
    worker._fail_mark_outstanding_stubs = AsyncMock(return_value=0)

    fake_kb = SimpleNamespace(meta={'stub_sync': {}})
    submit_payload = [_file_info(f'i{i}') for i in range(items_completed + items_failed)]

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
            total_files=len(submit_payload) + unchanged_count,
            added_file_ids=set(added_ids),
            updated_file_ids=set(updated_ids),
            unchanged_count=unchanged_count,
            total_deleted=0,
        )

    assert result['files_added'] == expect_added
    assert result['files_updated'] == expect_updated
    assert result['files_failed'] == expect_failed
    assert result['files_unchanged'] == unchanged_count
    # Invariant: files_processed = files_added + files_updated, never includes unchanged
    assert result['files_processed'] == expect_added + expect_updated
