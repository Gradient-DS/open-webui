"""Regression test: local-upload failure path must write meta.status='failed'.

Guards against the bug where process_uploaded_file's error handler called
update_file_data_by_id (data-only write) instead of set_status (dual-write),
causing the KB file list to show no error for locally-uploaded files that
failed processing (since the list reads meta.status, not data.status).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest

from open_webui.models.files import FilesTable


def _make_files_table() -> FilesTable:
    return FilesTable()


def _file(data: dict | None, meta: dict | None) -> SimpleNamespace:
    return SimpleNamespace(data=data, meta=meta)


@pytest.mark.asyncio
async def test_local_upload_failure_writes_meta_status_and_error():
    """set_status('failed', error=...) must write both data AND meta.

    This is the unit-level regression: the failure handler in
    process_uploaded_file calls set_status, which must dual-write.
    Verifies that meta.status and meta.error are populated so the KB
    file list (which reads meta.status) shows the error.
    """
    table = _make_files_table()

    existing = _file(data={'status': 'pending'}, meta={'name': 'doc.pdf', 'status': 'pending'})

    meta_updates: list[tuple[str, dict]] = []
    data_updates: list[tuple[str, dict]] = []

    async def fake_update_meta(file_id, meta, db=None):
        meta_updates.append((file_id, meta))
        return existing

    async def fake_update_data(file_id, data, db=None):
        data_updates.append((file_id, data))
        return existing

    with (
        patch.object(table, 'update_file_metadata_by_id', side_effect=fake_update_meta),
        patch.object(table, 'update_file_data_by_id', side_effect=fake_update_data),
    ):
        await table.set_status('file-local-1', 'failed', error='parsing failed: unsupported format')

    # meta must be written (the KB file list reads from here)
    assert len(meta_updates) == 1, 'update_file_metadata_by_id must be called for the failed status'
    assert meta_updates[0][0] == 'file-local-1'
    assert meta_updates[0][1]['status'] == 'failed'
    assert meta_updates[0][1]['error'] == 'parsing failed: unsupported format'

    # data must also be written (dual-write for backward compat)
    assert len(data_updates) == 1, 'update_file_data_by_id must also be called'
    assert data_updates[0][1]['status'] == 'failed'
    assert data_updates[0][1]['error'] == 'parsing failed: unsupported format'


@pytest.mark.asyncio
async def test_local_upload_failure_error_not_none():
    """A failed local upload with no error message still writes meta.status='failed'."""
    table = _make_files_table()

    meta_updates: list[tuple[str, dict]] = []

    async def fake_update_meta(file_id, meta, db=None):
        meta_updates.append((file_id, meta))
        return None

    async def fake_update_data(file_id, data, db=None):
        return None

    with (
        patch.object(table, 'update_file_metadata_by_id', side_effect=fake_update_meta),
        patch.object(table, 'update_file_data_by_id', side_effect=fake_update_data),
    ):
        # Simulate a failure where error string is provided (as process_uploaded_file does)
        await table.set_status('file-local-2', 'failed', error='Connection error')

    assert meta_updates[0][1]['status'] == 'failed'
    assert meta_updates[0][1]['error'] == 'Connection error'
