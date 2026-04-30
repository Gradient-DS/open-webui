"""Guards the picker single-file unsupported-type filter.

The OneDrive cloud picker hands the worker a single driveItem; until this
fix the worker would queue *any* item the user picked, including unsupported
types like .png. The folder-walk path filtered them via _is_supported_file;
the single-file path now mirrors that behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker


def _make_worker():
    worker = OneDriveSyncWorker.__new__(OneDriveSyncWorker)
    worker._client = SimpleNamespace()
    return worker


@pytest.mark.asyncio
async def test_collect_single_file_unsupported_returns_none():
    worker = _make_worker()
    item = {
        'id': 'item-1',
        'name': 'screenshot.png',
        'size': 100,
        'file': {'mimeType': 'image/png'},
    }
    worker._client.get_item = AsyncMock(return_value=item)

    source = {
        'drive_id': 'drive-1',
        'item_id': 'item-1',
        'name': 'screenshot.png',
    }
    result = await worker._collect_single_file(source)

    assert result is None


@pytest.mark.asyncio
async def test_collect_single_file_supported_returns_dict():
    worker = _make_worker()
    item = {
        'id': 'item-2',
        'name': 'report.docx',
        'size': 1024,
        'file': {
            'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'hashes': {'sha256Hash': 'abc123'},
        },
    }
    worker._client.get_item = AsyncMock(return_value=item)

    source = {
        'drive_id': 'drive-1',
        'item_id': 'item-2',
        'name': 'report.docx',
    }
    result = await worker._collect_single_file(source)

    assert result is not None
    assert result['name'] == 'report.docx'
    assert result['source_type'] == 'file'
    assert result['source_item_id'] == 'item-2'
    assert result['drive_id'] == 'drive-1'
    assert result['item'] == item
