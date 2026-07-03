"""Guards the identity self-heal in ``_create_stub_file_rows``.

The existing-row branch used to only re-link the file to the KB, leaving
stale ``source_item_id`` / ``relative_path`` on ``file.meta`` forever
(pre-canonicalization picker ids, ``"Papers/"``-prefixed paths from the
folder_map bug, loader-era overwrites). It now refreshes drifted identity
metadata BEFORE re-linking, so ``add_file_to_knowledge_by_id``'s idempotent
upsert mirrors the healed values onto the join row's denormalized path
columns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from open_webui.test.services.sync.test_base_worker_classify import _make_worker


def _file_info(item_id='F1', name='doc.pdf', source_item_id='CANON', relative_path='doc.pdf'):
    return {
        'item': {'id': item_id, 'name': name, 'size': 10},
        'name': name,
        'source_item_id': source_item_id,
        'relative_path': relative_path,
    }


def _existing(meta):
    row = AsyncMock()
    row.meta = meta
    return row


def _patches(existing):
    files = AsyncMock()
    files.get_file_by_id = AsyncMock(return_value=existing)
    knowledges = AsyncMock()
    return (
        patch('open_webui.services.sync.base_worker.Files', files),
        patch('open_webui.services.sync.base_worker.Knowledges', knowledges),
        patch('open_webui.services.sync.base_worker.emit_file_processing', AsyncMock()),
        files,
        knowledges,
    )


@pytest.mark.asyncio
async def test_stub_selfheal_updates_stale_identity_before_relink():
    worker = _make_worker()
    existing = _existing({'source_item_id': 'PICKER', 'relative_path': 'Papers/doc.pdf'})
    p_files, p_knowledges, p_emit, files, knowledges = _patches(existing)

    call_order = []
    files.update_file_metadata_by_id.side_effect = lambda *a, **k: call_order.append('meta')
    knowledges.add_file_to_knowledge_by_id.side_effect = lambda *a, **k: call_order.append('link')

    with p_files, p_knowledges, p_emit:
        touched = await worker._create_stub_file_rows([_file_info()])

    files.update_file_metadata_by_id.assert_awaited_once_with(
        'stub-F1', {'source_item_id': 'CANON', 'relative_path': 'doc.pdf'}
    )
    # Meta heal lands BEFORE the link upsert, so the join row's denormalized
    # path columns are refreshed from the healed values.
    assert call_order == ['meta', 'link']
    assert touched == ['stub-F1']
    files.insert_new_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_stub_selfheal_noop_when_identity_matches():
    worker = _make_worker()
    existing = _existing({'source_item_id': 'CANON', 'relative_path': 'doc.pdf'})
    p_files, p_knowledges, p_emit, files, knowledges = _patches(existing)

    with p_files, p_knowledges, p_emit:
        touched = await worker._create_stub_file_rows([_file_info()])

    files.update_file_metadata_by_id.assert_not_awaited()
    knowledges.add_file_to_knowledge_by_id.assert_awaited_once()
    assert touched == ['stub-F1']


@pytest.mark.asyncio
async def test_stub_selfheal_heals_partial_drift_only():
    """Only the drifted key is written; matching keys are left alone."""
    worker = _make_worker()
    existing = _existing({'source_item_id': 'CANON', 'relative_path': 'Papers/doc.pdf'})
    p_files, p_knowledges, p_emit, files, _ = _patches(existing)

    with p_files, p_knowledges, p_emit:
        await worker._create_stub_file_rows([_file_info()])

    files.update_file_metadata_by_id.assert_awaited_once_with('stub-F1', {'relative_path': 'doc.pdf'})


@pytest.mark.asyncio
async def test_stub_selfheal_skips_empty_computed_values():
    """A file_info without identity (single-file sources pass
    source_item_id=None on some providers) must not blank stored values."""
    worker = _make_worker()
    existing = _existing({'source_item_id': 'CANON', 'relative_path': 'doc.pdf'})
    p_files, p_knowledges, p_emit, files, _ = _patches(existing)

    info = _file_info(source_item_id=None, relative_path='doc.pdf')
    with p_files, p_knowledges, p_emit:
        await worker._create_stub_file_rows([info])

    files.update_file_metadata_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_stub_new_file_insert_path_unchanged():
    worker = _make_worker()
    p_files, p_knowledges, p_emit, files, knowledges = _patches(existing=None)

    with p_files, p_knowledges, p_emit:
        touched = await worker._create_stub_file_rows([_file_info()])

    files.insert_new_file.assert_awaited_once()
    files.update_file_metadata_by_id.assert_not_awaited()
    knowledges.add_file_to_knowledge_by_id.assert_awaited_once()
    assert touched == ['stub-F1']


# ---------- pending_cloud_hash staging (cloud-hash persistence fix) -------------------
#
# The shared-loader path never wrote cloud_hash anywhere, so every re-sync
# classified all prior members 'updated' and re-processed the whole KB. The
# stub write now stages the provider hash under meta.pending_cloud_hash;
# /ingest promotes it to cloud_hash only on a successful ingest, so a failed
# ingest keeps the old cloud_hash and the file retries as 'updated'.


@pytest.mark.asyncio
async def test_stub_new_row_stages_pending_cloud_hash():
    """New row: the provider hash is staged, never written as cloud_hash."""
    worker = _make_worker()
    p_files, p_knowledges, p_emit, files, _ = _patches(existing=None)

    info = {**_file_info(), 'cloud_hash': 'h1'}
    with p_files, p_knowledges, p_emit:
        await worker._create_stub_file_rows([info])

    file_form = files.insert_new_file.await_args.args[1]
    assert file_form.meta['pending_cloud_hash'] == 'h1'
    assert 'cloud_hash' not in file_form.meta


@pytest.mark.asyncio
async def test_stub_new_row_without_hash_stages_nothing():
    worker = _make_worker()
    p_files, p_knowledges, p_emit, files, _ = _patches(existing=None)

    with p_files, p_knowledges, p_emit:
        await worker._create_stub_file_rows([_file_info()])

    file_form = files.insert_new_file.await_args.args[1]
    assert 'pending_cloud_hash' not in file_form.meta
    assert 'cloud_hash' not in file_form.meta


@pytest.mark.asyncio
async def test_stub_selfheal_stages_pending_cloud_hash_on_existing_row():
    """Existing row with a stale (or absent) staged hash gets this sync's hash."""
    worker = _make_worker()
    existing = _existing({'source_item_id': 'CANON', 'relative_path': 'doc.pdf', 'pending_cloud_hash': 'h0'})
    p_files, p_knowledges, p_emit, files, _ = _patches(existing)

    info = {**_file_info(), 'cloud_hash': 'h1'}
    with p_files, p_knowledges, p_emit:
        await worker._create_stub_file_rows([info])

    files.update_file_metadata_by_id.assert_awaited_once_with('stub-F1', {'pending_cloud_hash': 'h1'})


@pytest.mark.asyncio
async def test_stub_selfheal_skips_matching_pending_cloud_hash():
    worker = _make_worker()
    existing = _existing({'source_item_id': 'CANON', 'relative_path': 'doc.pdf', 'pending_cloud_hash': 'h1'})
    p_files, p_knowledges, p_emit, files, _ = _patches(existing)

    info = {**_file_info(), 'cloud_hash': 'h1'}
    with p_files, p_knowledges, p_emit:
        await worker._create_stub_file_rows([info])

    files.update_file_metadata_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_stub_selfheal_none_hash_never_blanks_staged_value():
    """_get_cloud_hash returning None must not blank a previously staged hash."""
    worker = _make_worker()
    existing = _existing({'source_item_id': 'CANON', 'relative_path': 'doc.pdf', 'pending_cloud_hash': 'h1'})
    p_files, p_knowledges, p_emit, files, _ = _patches(existing)

    with p_files, p_knowledges, p_emit:
        await worker._create_stub_file_rows([_file_info()])

    files.update_file_metadata_by_id.assert_not_awaited()
