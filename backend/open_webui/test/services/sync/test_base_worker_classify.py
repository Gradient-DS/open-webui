"""Guards `_classify_for_submit`'s pre-submit cloud-hash short-circuit.

The shared-loader path used to submit every discovered file to the
loader-worker; with classification, files whose cloud_hash matches the
stored value AND whose KB row is `'completed'` skip submission entirely.
That lets the toast distinguish "Added 5" from "5 already there" — the
structural fix for the "5 extra" re-sync toast.
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
        return file_info.get('cloud_hash')

    async def _verify_source_access(self, source):
        return True

    async def _handle_revoked_source(self, source):
        return 0


def _make_worker():
    worker = _StubWorker.__new__(_StubWorker)
    worker.knowledge_id = 'kb-test'
    worker.user_id = 'user-test'
    return worker


def _file_info(item_id: str = 'item-1', cloud_hash: str | None = 'h1') -> dict:
    return {
        'item': {'id': item_id, 'name': 'doc.docx'},
        'cloud_hash': cloud_hash,
    }


@pytest.mark.asyncio
async def test_classify_added_no_existing_row():
    worker = _make_worker()
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=None),
    ):
        cat, fid = await worker._classify_for_submit(_file_info())
    assert cat == 'added'
    assert fid == 'stub-item-1'


@pytest.mark.asyncio
async def test_classify_updated_hash_mismatch():
    worker = _make_worker()
    existing = SimpleNamespace(meta={'cloud_hash': 'old-hash'}, data={'status': 'completed'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, fid = await worker._classify_for_submit(_file_info(cloud_hash='new-hash'))
    assert cat == 'updated'
    assert fid == 'stub-item-1'


@pytest.mark.asyncio
async def test_classify_updated_status_not_completed():
    worker = _make_worker()
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'pending'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'updated'


@pytest.mark.asyncio
async def test_classify_unchanged_full_match():
    worker = _make_worker()
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'unchanged'


@pytest.mark.asyncio
async def test_classify_no_cloud_hash_treated_as_updated():
    """Conservative fallback when the provider didn't surface a hash."""
    worker = _make_worker()
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash=None))
    assert cat == 'updated'
