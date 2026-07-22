"""Guards the folder-source id canonicalization in ``_collect_folder_files``.

The OneDrive picker's driveItem id and the id Graph uses in ``/delta``
responses can differ for the same folder (picker/list-scoped vs
drive-relative). Before the fix, the folder_map was seeded only with the
picker id, so the folder's re-appearance in the delta stream mapped to its
own name — every direct child got a phantom ``"Papers/"`` path prefix, and
the tree endpoint rendered a raw-id node next to an empty registry twin.

The fix resolves the canonical id once per sync, rewrites the source in
place (persisted by the normal end-of-sync ``_save_sources()``), seeds the
folder map with BOTH id forms, and pins the source folder itself to the
map root so the delta re-emission can never remap it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from open_webui.services.onedrive.sync_worker import FOLDER_MAP_VERSION, OneDriveSyncWorker


def _make_worker(sources=None):
    worker = OneDriveSyncWorker.__new__(OneDriveSyncWorker)
    worker.knowledge_id = 'kb-1'
    worker.user_id = 'user-1'
    worker.sources = sources if sources is not None else []
    worker._client = AsyncMock()
    return worker


def _folder_source(item_id='PICKER', **extra):
    return {'drive_id': 'D1', 'item_id': item_id, 'name': 'Papers', 'type': 'folder', **extra}


def _delta_items():
    """The observed staging shape: /delta re-emits the picked folder under its
    canonical id, parented on the picker id; children are parented on the
    canonical id."""
    return [
        {'id': 'CANON', 'name': 'Papers', 'folder': {}, 'parentReference': {'id': 'PICKER'}},
        {'id': 'F1', 'name': 'doc.pdf', 'size': 10, 'file': {}, 'parentReference': {'id': 'CANON'}},
        {'id': 'SUB', 'name': 'Sub', 'folder': {}, 'parentReference': {'id': 'CANON'}},
        {'id': 'F2', 'name': 'nested.pdf', 'size': 10, 'file': {}, 'parentReference': {'id': 'SUB'}},
    ]


@pytest.mark.asyncio
async def test_canonicalize_rewrites_source_and_strips_leading_segment():
    source = _folder_source()
    worker = _make_worker(sources=[source])
    worker._client.get_item = AsyncMock(return_value={'id': 'CANON', 'name': 'Papers'})
    worker._client.get_drive_delta = AsyncMock(return_value=(_delta_items(), 'DL-NEW'))

    files, deleted = await worker._collect_folder_files(source)

    # Source rewritten in place to the canonical id.
    assert source['item_id'] == 'CANON'
    assert deleted == 0

    # Both id forms map to the root; the re-emitted source folder was NOT
    # remapped to 'Papers' (the double-nesting bug).
    assert source['folder_map']['CANON'] == ''
    assert source['folder_map']['PICKER'] == ''
    assert source['folder_map']['SUB'] == 'Sub'
    assert source['folder_map_version'] == FOLDER_MAP_VERSION

    by_id = {f['item']['id']: f for f in files}
    # Direct child: no 'Papers/' prefix.
    assert by_id['F1']['relative_path'] == 'doc.pdf'
    # Nested child: subfolder path only.
    assert by_id['F2']['relative_path'] == 'Sub/nested.pdf'
    # Files are stamped with the canonical source id.
    assert by_id['F1']['source_item_id'] == 'CANON'
    assert by_id['F2']['source_item_id'] == 'CANON'


@pytest.mark.asyncio
async def test_canonicalize_noop_when_ids_already_match():
    source = _folder_source(item_id='CANON')
    worker = _make_worker(sources=[source])
    worker._client.get_item = AsyncMock(return_value={'id': 'CANON', 'name': 'Papers'})
    items = [
        {'id': 'F1', 'name': 'doc.pdf', 'size': 10, 'file': {}, 'parentReference': {'id': 'CANON'}},
    ]
    worker._client.get_drive_delta = AsyncMock(return_value=(items, 'DL-NEW'))

    files, _ = await worker._collect_folder_files(source)

    assert source['item_id'] == 'CANON'
    assert files[0]['relative_path'] == 'doc.pdf'
    assert files[0]['source_item_id'] == 'CANON'


@pytest.mark.asyncio
async def test_canonicalize_survives_missing_item():
    """get_item returning None (transient 404) must not break enumeration."""
    source = _folder_source()
    worker = _make_worker(sources=[source])
    worker._client.get_item = AsyncMock(return_value=None)
    worker._client.get_drive_delta = AsyncMock(return_value=([], 'DL-NEW'))

    files, _ = await worker._collect_folder_files(source)

    assert source['item_id'] == 'PICKER'
    assert files == []


@pytest.mark.asyncio
async def test_rewritten_id_persists_via_save_sources():
    """The in-place rewrite rides the normal end-of-sync sources write —
    same mechanism Google Drive's shortcut resolution relies on."""
    source = _folder_source()
    worker = _make_worker(sources=[source])
    worker._client.get_item = AsyncMock(return_value={'id': 'CANON', 'name': 'Papers'})
    worker._client.get_drive_delta = AsyncMock(return_value=(_delta_items(), 'DL-NEW'))

    await worker._collect_folder_files(source)

    knowledge = AsyncMock()
    knowledge.meta = {'onedrive_sync': {'sources': []}}
    saved = {}

    async def _update_meta(knowledge_id, meta):
        saved['meta'] = meta

    with patch('open_webui.services.sync.base_worker.Knowledges') as knowledges:
        knowledges.get_knowledge_by_id = AsyncMock(return_value=knowledge)
        knowledges.update_knowledge_meta_by_id = AsyncMock(side_effect=_update_meta)
        await worker._save_sources()

    persisted = saved['meta']['onedrive_sync']['sources']
    assert persisted[0]['item_id'] == 'CANON'
    assert persisted[0]['delta_link'] == 'DL-NEW'


@pytest.mark.asyncio
async def test_stale_folder_map_version_forces_full_enumeration():
    """A pre-canonicalization folder_map (version < FOLDER_MAP_VERSION) may
    hold 'Papers/'-prefixed paths for subfolders the incremental delta will
    never re-emit — the version bump clears it and drops the delta link."""
    source = _folder_source(
        item_id='CANON',
        delta_link='DL-OLD',
        folder_map={'CANON': 'Papers', 'SUB': 'Papers/Sub'},
        folder_map_version=1,
    )
    worker = _make_worker(sources=[source])
    worker._client.get_item = AsyncMock(return_value={'id': 'CANON', 'name': 'Papers'})
    worker._client.get_drive_delta = AsyncMock(return_value=(_delta_items(), 'DL-NEW'))

    await worker._collect_folder_files(source)

    # Full sync forced: delta called without the stored link.
    worker._client.get_drive_delta.assert_awaited_once_with('D1', 'CANON', None)
    # Stale map rebuilt from scratch.
    assert source['folder_map']['CANON'] == ''
    assert source['folder_map']['SUB'] == 'Sub'
    assert source['folder_map_version'] == FOLDER_MAP_VERSION
