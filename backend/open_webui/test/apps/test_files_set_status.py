"""TDD tests for FilesTable.set_status dual-write helper.

Guard that:
- set_status writes status/error to BOTH data AND meta (dual-write)
- set_status does NOT clobber pre-existing data or meta keys
  (e.g. data.content, meta.relative_path survive a status update)
- error=None is written explicitly (so a previously-failed file
  gets its stale error cleared on success)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from open_webui.models.files import FilesTable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_files_table() -> FilesTable:
    """Return a fresh FilesTable instance without touching the DB."""
    return FilesTable()


def _file(data: dict | None, meta: dict | None) -> SimpleNamespace:
    """Build a minimal FileModel-like namespace for patching get_file_by_id."""
    return SimpleNamespace(data=data, meta=meta)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_status_writes_to_both_data_and_meta_on_completed():
    """Completed status is mirrored into data AND meta."""
    table = _make_files_table()

    existing = _file(
        data={'content': 'hello world', 'status': 'pending'},
        meta={'relative_path': 'docs/foo.txt', 'name': 'foo.txt'},
    )

    data_updates: list[tuple[str, dict]] = []
    meta_updates: list[tuple[str, dict]] = []

    async def fake_get(file_id, db=None):
        return existing

    async def fake_update_data(file_id, data, db=None):
        data_updates.append((file_id, data))
        return existing

    async def fake_update_meta(file_id, meta, db=None):
        meta_updates.append((file_id, meta))
        return existing

    with (
        patch.object(table, 'get_file_by_id', side_effect=fake_get),
        patch.object(table, 'update_file_data_by_id', side_effect=fake_update_data),
        patch.object(table, 'update_file_metadata_by_id', side_effect=fake_update_meta),
    ):
        await table.set_status('file-1', 'completed', error=None)

    # Both columns written
    assert len(data_updates) == 1, 'update_file_data_by_id must be called once'
    assert len(meta_updates) == 1, 'update_file_metadata_by_id must be called once'

    assert data_updates[0] == ('file-1', {'status': 'completed', 'error': None})
    assert meta_updates[0] == ('file-1', {'status': 'completed', 'error': None})


@pytest.mark.asyncio
async def test_set_status_writes_error_into_both_columns():
    """Error status + message lands in both data and meta."""
    table = _make_files_table()

    existing = _file(
        data={'content': 'text', 'status': 'completed'},
        meta={'name': 'bar.pdf'},
    )

    data_updates: list[tuple[str, dict]] = []
    meta_updates: list[tuple[str, dict]] = []

    async def fake_get(file_id, db=None):
        return existing

    async def fake_update_data(file_id, data, db=None):
        data_updates.append((file_id, data))
        return existing

    async def fake_update_meta(file_id, meta, db=None):
        meta_updates.append((file_id, meta))
        return existing

    with (
        patch.object(table, 'get_file_by_id', side_effect=fake_get),
        patch.object(table, 'update_file_data_by_id', side_effect=fake_update_data),
        patch.object(table, 'update_file_metadata_by_id', side_effect=fake_update_meta),
    ):
        await table.set_status('file-2', 'error', error='vector DB timeout')

    assert data_updates[0] == ('file-2', {'status': 'error', 'error': 'vector DB timeout'})
    assert meta_updates[0] == ('file-2', {'status': 'error', 'error': 'vector DB timeout'})


@pytest.mark.asyncio
async def test_set_status_does_not_clobber_existing_data_keys():
    """set_status only sends status+error; update_file_data_by_id's merge
    preserves existing data keys (content, etc.)."""
    table = _make_files_table()

    existing = _file(
        data={'content': 'must survive', 'status': 'pending'},
        meta={'relative_path': 'keep/me'},
    )

    data_updates: list[tuple[str, dict]] = []
    meta_updates: list[tuple[str, dict]] = []

    async def fake_get(file_id, db=None):
        return existing

    async def fake_update_data(file_id, data, db=None):
        data_updates.append((file_id, data))
        return existing

    async def fake_update_meta(file_id, meta, db=None):
        meta_updates.append((file_id, meta))
        return existing

    with (
        patch.object(table, 'get_file_by_id', side_effect=fake_get),
        patch.object(table, 'update_file_data_by_id', side_effect=fake_update_data),
        patch.object(table, 'update_file_metadata_by_id', side_effect=fake_update_meta),
    ):
        await table.set_status('file-3', 'completed')

    # Only status/error keys sent (the merge inside update_file_data_by_id
    # preserves content etc. — we must not send the whole data dict)
    sent_data = data_updates[0][1]
    assert 'content' not in sent_data, 'set_status must NOT send content — that wipes nothing but proves intent'
    assert sent_data['status'] == 'completed'

    sent_meta = meta_updates[0][1]
    assert 'relative_path' not in sent_meta, 'set_status must NOT send relative_path — let merge keep it'
    assert sent_meta['status'] == 'completed'


@pytest.mark.asyncio
async def test_set_status_pending_writes_to_both_columns():
    """Pending status (stub creation) is also mirrored."""
    table = _make_files_table()

    existing = _file(data={'status': 'pending'}, meta={})

    data_updates: list[tuple[str, dict]] = []
    meta_updates: list[tuple[str, dict]] = []

    async def fake_get(file_id, db=None):
        return existing

    async def fake_update_data(file_id, data, db=None):
        data_updates.append((file_id, data))
        return existing

    async def fake_update_meta(file_id, meta, db=None):
        meta_updates.append((file_id, meta))
        return existing

    with (
        patch.object(table, 'get_file_by_id', side_effect=fake_get),
        patch.object(table, 'update_file_data_by_id', side_effect=fake_update_data),
        patch.object(table, 'update_file_metadata_by_id', side_effect=fake_update_meta),
    ):
        await table.set_status('file-4', 'pending')

    assert data_updates[0][1]['status'] == 'pending'
    assert meta_updates[0][1]['status'] == 'pending'


@pytest.mark.asyncio
async def test_set_status_returns_file_model():
    """set_status returns the FileModel from update_file_data_by_id."""
    table = _make_files_table()

    expected = _file(data={'status': 'completed'}, meta={'status': 'completed'})

    async def fake_get(file_id, db=None):
        return _file(data={'status': 'pending'}, meta={})

    async def fake_update_data(file_id, data, db=None):
        return expected

    async def fake_update_meta(file_id, meta, db=None):
        return expected

    with (
        patch.object(table, 'get_file_by_id', side_effect=fake_get),
        patch.object(table, 'update_file_data_by_id', side_effect=fake_update_data),
        patch.object(table, 'update_file_metadata_by_id', side_effect=fake_update_meta),
    ):
        result = await table.set_status('file-5', 'completed')

    assert result is expected
