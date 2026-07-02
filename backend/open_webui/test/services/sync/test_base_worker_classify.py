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


# ---------- empty-content guard (expect_nonempty_content) ----------------------------
#
# A 'completed' row with empty data['content'] is the residue of base_worker's
# empty-extraction branch (it marks completed even when no text was extracted).
# The cloud-hash short-circuit otherwise freezes such a row empty forever. For
# providers that always render non-empty content (Confluence: title + metadata
# front-matter for every page), an empty row must re-ingest instead of skip.


@pytest.mark.asyncio
async def test_classify_empty_content_resubmitted_when_nonempty_expected():
    """expect_nonempty_content=True: completed-but-empty row re-ingests."""
    worker = _make_worker()
    worker.expect_nonempty_content = True
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed', 'content': ''})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'updated'


@pytest.mark.asyncio
async def test_classify_whitespace_content_resubmitted_when_nonempty_expected():
    """Whitespace-only content is treated the same as empty."""
    worker = _make_worker()
    worker.expect_nonempty_content = True
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed', 'content': '   \n'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'updated'


@pytest.mark.asyncio
async def test_classify_empty_content_unchanged_when_empty_is_legitimate():
    """Default (expect_nonempty_content=False): binary-file providers legitimately
    extract image-only files to empty content — they must stay 'unchanged' so the
    cloud-hash skip keeps working. Guards against a regression for OneDrive/GDrive."""
    worker = _make_worker()  # flag defaults False
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed', 'content': ''})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'unchanged'


@pytest.mark.asyncio
async def test_classify_nonempty_content_unchanged_even_when_nonempty_expected():
    """The guard only re-submits empty rows — a populated 'completed' row with a
    matching hash still short-circuits, so the fast path stays fast."""
    worker = _make_worker()
    worker.expect_nonempty_content = True
    existing = SimpleNamespace(
        meta={'cloud_hash': 'h1'},
        data={'status': 'completed', 'content': '# Title\n\nbody'},
    )
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'unchanged'


def test_is_fully_ingested_contract():
    """The shared helper both cloud-hash short-circuits gate on."""
    worker = _make_worker()
    # Non-terminal status is never fully ingested.
    assert worker._is_fully_ingested(SimpleNamespace(data={'status': 'pending'})) is False
    # completed + flag off → trusted regardless of content (current behavior).
    assert worker._is_fully_ingested(SimpleNamespace(data={'status': 'completed'})) is True
    # completed + flag on → empty/whitespace content means not ingested.
    worker.expect_nonempty_content = True
    assert worker._is_fully_ingested(SimpleNamespace(data={'status': 'completed', 'content': '  '})) is False
    assert worker._is_fully_ingested(SimpleNamespace(data={'status': 'completed', 'content': 'x'})) is True


# ---------- KB-membership gate (cross-KB crosstalk fix, 2026-07-02) ------------------
#
# The global data.status=='completed' on a shared File row proves *some* KB
# ingested it — not that THIS KB's collection has vectors. Two KBs syncing
# overlapping OneDrive folders share file rows (file.id = onedrive-<item_id>),
# so KB B used to classify KB A's completed files 'unchanged' and never embed
# them into its own collection. Files net-new to the KB are now always
# submitted; the unchanged/updated logic applies only to prior members.


@pytest.mark.asyncio
async def test_classify_net_new_to_kb_added_despite_global_completed():
    worker = _make_worker()
    worker._kb_member_file_ids = set()  # this KB held nothing before this sync
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, fid = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'added'
    assert fid == 'stub-item-1'


@pytest.mark.asyncio
async def test_classify_prior_member_full_match_still_unchanged():
    worker = _make_worker()
    worker._kb_member_file_ids = {'stub-item-1'}
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'unchanged'


@pytest.mark.asyncio
async def test_classify_prior_member_changed_hash_updated():
    worker = _make_worker()
    worker._kb_member_file_ids = {'stub-item-1'}
    existing = SimpleNamespace(meta={'cloud_hash': 'old'}, data={'status': 'completed'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='new'))
    assert cat == 'updated'


@pytest.mark.asyncio
async def test_classify_gate_inactive_without_snapshot():
    """None snapshot (legacy call paths / class default) keeps the old
    global-status behavior — the gate only fires when sync() snapshotted."""
    worker = _make_worker()
    assert worker._kb_member_file_ids is None
    existing = SimpleNamespace(meta={'cloud_hash': 'h1'}, data={'status': 'completed'})
    with patch(
        'open_webui.services.sync.base_worker.Files.get_file_by_id',
        new=AsyncMock(return_value=existing),
    ):
        cat, _ = await worker._classify_for_submit(_file_info(cloud_hash='h1'))
    assert cat == 'unchanged'


def test_confluence_worker_opts_into_nonempty_content_guard():
    """Confluence renders title + metadata front-matter for every page, so an
    empty content row is always a failed ingest — it opts into the guard.
    The base default stays off so binary-file providers are unaffected."""
    from open_webui.services.confluence.sync_worker import ConfluenceSyncWorker

    assert ConfluenceSyncWorker.expect_nonempty_content is True
    assert BaseSyncWorker.expect_nonempty_content is False
