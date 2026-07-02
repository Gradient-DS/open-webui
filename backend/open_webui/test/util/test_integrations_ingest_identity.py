"""Guards /ingest's set-if-absent promotion of OWUI-owned identity keys.

The sync worker stamps ``source_item_id`` (canonical source id) and
``relative_path`` (computed from the provider delta) on the stub File row.
The loader's values for those keys are derived and historically wrong —
its doc_processor-era metadata carried the Graph *canonical* item id while
the registry held the *picker* id, which split the folder tree into a
raw-id node plus an empty twin. ``_create_or_update_file_record`` now only
accepts loader identity values when the row has none; everything else
(cloud_hash, provider ids, last_synced_at) keeps overwrite semantics.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from open_webui.routers import integrations as integrations_router
from open_webui.routers.integrations import ChunkedTextDocument


def _doc(metadata: dict) -> ChunkedTextDocument:
    return ChunkedTextDocument(
        source_id='item-1',
        filename='doc.pdf',
        chunks=['chunk'],
        metadata=metadata,
    )


def _patch_persistence(monkeypatch, *, existing_meta=None):
    """Patch the DB seams; returns (files, knowledges)."""
    existing_file = None
    if existing_meta is not None:
        existing_file = MagicMock()
        existing_file.meta = existing_meta
        existing_file.path = '/p'

    files = MagicMock()
    files.get_file_by_id = AsyncMock(return_value=existing_file)
    files.insert_new_file = AsyncMock()
    files.update_file_metadata_by_id = AsyncMock()
    files.update_file_data_by_id = AsyncMock()
    files.update_file_path_by_id = AsyncMock()
    monkeypatch.setattr(integrations_router, 'Files', files)

    knowledges = MagicMock()
    knowledges.add_file_to_knowledge_by_id = AsyncMock()
    knowledges.set_path_fields_by_file_id = AsyncMock()
    monkeypatch.setattr(integrations_router, 'Knowledges', knowledges)

    return files, knowledges


async def _run(doc, *, knowledge_id='kb-1'):
    return await integrations_router._create_or_update_file_record(
        file_id='onedrive-item-1',
        doc=doc,
        content_text='text',
        file_path='',
        provider='onedrive',
        knowledge_id=knowledge_id,
        user_id='user-1',
    )


@pytest.mark.asyncio
async def test_populated_identity_keys_not_overwritten(monkeypatch):
    files, knowledges = _patch_persistence(
        monkeypatch,
        existing_meta={'source_item_id': 'CANON', 'relative_path': 'doc.pdf'},
    )
    doc = _doc({'source_item_id': 'STALE', 'relative_path': 'Papers/doc.pdf', 'cloud_hash': 'h2'})

    status = await _run(doc)

    assert status == 'updated'
    written_meta = files.update_file_metadata_by_id.await_args.args[1]
    # Identity keys skipped — the stub's values stay authoritative.
    assert 'source_item_id' not in written_meta
    assert 'relative_path' not in written_meta
    # Loader-owned keys keep overwrite semantics.
    assert written_meta['cloud_hash'] == 'h2'

    # The join-row path refresh merges existing meta first, so the healed
    # identity (not the loader's) lands on the denormalized columns.
    merged = knowledges.set_path_fields_by_file_id.await_args.args[1]
    assert merged['source_item_id'] == 'CANON'
    assert merged['relative_path'] == 'doc.pdf'


@pytest.mark.asyncio
async def test_absent_identity_keys_accept_loader_values(monkeypatch):
    """Rows the sync worker never stamped (legacy stubs, push integrations)
    still take the loader's identity so folder rendering keeps working."""
    files, _ = _patch_persistence(monkeypatch, existing_meta={'name': 'doc.pdf'})
    doc = _doc({'source_item_id': 'LOADER', 'relative_path': 'Sub/doc.pdf'})

    await _run(doc)

    written_meta = files.update_file_metadata_by_id.await_args.args[1]
    assert written_meta['source_item_id'] == 'LOADER'
    assert written_meta['relative_path'] == 'Sub/doc.pdf'


@pytest.mark.asyncio
async def test_new_row_accepts_loader_identity(monkeypatch):
    files, _ = _patch_persistence(monkeypatch, existing_meta=None)
    doc = _doc({'source_item_id': 'LOADER', 'relative_path': 'doc.pdf'})

    status = await _run(doc)

    assert status == 'created'
    inserted_form = files.insert_new_file.await_args.args[1]
    assert inserted_form.meta['source_item_id'] == 'LOADER'
    assert inserted_form.meta['relative_path'] == 'doc.pdf'
