"""Guards ``BaseSyncWorker._save_sources`` — the "phantom folder" fix.

``_save_sources`` must never drop a stored source that still has linked files
(a targeted / cancelled / partial / concurrent sync carries only a subset of
the KB's sources). A blind replace orphaned those sources' files into raw-ID
folders in the tree browser. It MUST still drop a stored source that has no
linked files (the revoked/removed case, whose files were already deleted).
"""

from __future__ import annotations

from types import SimpleNamespace
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


def _make_worker(sources):
    worker = _StubWorker.__new__(_StubWorker)
    worker.knowledge_id = 'kb-test'
    worker.user_id = 'user-test'
    worker.sources = sources
    return worker


async def _run_save(worker, *, stored_sources, linked_ids):
    """Invoke _save_sources with mocked persistence; return the sources list
    that would be written to knowledge meta."""
    knowledge = SimpleNamespace(meta={'stub_sync': {'sources': stored_sources}})
    captured: dict = {}

    async def _update(knowledge_id, meta):
        captured['meta'] = meta

    with (
        patch(
            'open_webui.services.sync.base_worker.Knowledges.get_knowledge_by_id',
            new=AsyncMock(return_value=knowledge),
        ),
        patch(
            'open_webui.services.sync.base_worker.Knowledges.get_linked_source_item_ids',
            new=AsyncMock(return_value=set(linked_ids)),
        ),
        patch(
            'open_webui.services.sync.base_worker.Knowledges.update_knowledge_meta_by_id',
            new=AsyncMock(side_effect=_update),
        ),
    ):
        await worker._save_sources()

    return captured['meta']['stub_sync']['sources']


@pytest.mark.asyncio
async def test_save_sources_preserves_orphaned_source_with_linked_files():
    # This run only carries B, but stored meta also has A whose files are still
    # linked. A must survive (this is the phantom-folder bug).
    worker = _make_worker([{'item_id': 'B', 'name': 'Bank'}])
    saved = await _run_save(
        worker,
        stored_sources=[{'item_id': 'A', 'name': 'Alpha'}, {'item_id': 'B', 'name': 'Bank'}],
        linked_ids={'A', 'B'},
    )
    ids = [s['item_id'] for s in saved]
    assert ids == ['B', 'A']  # current run first, then preserved orphan


@pytest.mark.asyncio
async def test_save_sources_drops_stored_source_without_linked_files():
    # A is stored but has NO linked files (revoked/removed) → must NOT be kept.
    worker = _make_worker([{'item_id': 'B', 'name': 'Bank'}])
    saved = await _run_save(
        worker,
        stored_sources=[{'item_id': 'A', 'name': 'Alpha'}, {'item_id': 'B', 'name': 'Bank'}],
        linked_ids={'B'},
    )
    assert [s['item_id'] for s in saved] == ['B']


@pytest.mark.asyncio
async def test_save_sources_upserts_current_run_over_stored():
    # The current run's copy of a source (fresh delta_link/folder_map) wins.
    worker = _make_worker([{'item_id': 'A', 'name': 'Alpha', 'delta_link': 'new'}])
    saved = await _run_save(
        worker,
        stored_sources=[{'item_id': 'A', 'name': 'Alpha', 'delta_link': 'old'}],
        linked_ids={'A'},
    )
    assert saved == [{'item_id': 'A', 'name': 'Alpha', 'delta_link': 'new'}]
