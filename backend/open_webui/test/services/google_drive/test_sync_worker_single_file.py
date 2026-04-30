"""Guards the picker single-file unsupported-type filter.

The Google Drive cloud picker hands the worker a single Drive file; until
this fix the worker would queue any item the user picked, including
unsupported types like .png. The folder-walk path filtered them via
_is_supported_file; the single-file path now mirrors that behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.services.google_drive.sync_worker import GoogleDriveSyncWorker


def _make_worker():
    worker = GoogleDriveSyncWorker.__new__(GoogleDriveSyncWorker)
    worker._client = SimpleNamespace()
    worker._client.resolve_if_shortcut = AsyncMock(return_value=('item-x', False))
    return worker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'item',
    [
        {
            'id': 'item-png',
            'name': 'photo.png',
            'mimeType': 'image/png',
        },
        {
            'id': 'item-exe',
            'name': 'installer.exe',
            'mimeType': 'application/x-msdownload',
        },
        {
            'id': 'item-form',
            'name': 'survey',
            'mimeType': 'application/vnd.google-apps.form',
        },
    ],
)
async def test_collect_single_file_unsupported_returns_none(item):
    worker = _make_worker()
    worker._client.get_file = AsyncMock(return_value=item)
    worker._client.resolve_if_shortcut = AsyncMock(return_value=(item['id'], False))

    source = {'item_id': item['id'], 'name': item['name']}
    result = await worker._collect_single_file(source)

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'item, expected_name',
    [
        (
            {
                'id': 'item-docx',
                'name': 'report.docx',
                'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'md5Checksum': 'abc',
            },
            'report.docx',
        ),
        (
            {
                'id': 'item-gdoc',
                'name': 'design notes',
                'mimeType': 'application/vnd.google-apps.document',
                'modifiedTime': '2026-04-30T00:00:00Z',
            },
            'design notes.docx',
        ),
    ],
)
async def test_collect_single_file_supported_returns_dict(item, expected_name):
    worker = _make_worker()
    worker._client.get_file = AsyncMock(return_value=item)
    worker._client.resolve_if_shortcut = AsyncMock(return_value=(item['id'], False))

    source = {'item_id': item['id'], 'name': item['name']}
    result = await worker._collect_single_file(source)

    assert result is not None
    assert result['name'] == expected_name
    assert result['source_type'] == 'file'
    assert result['source_item_id'] == item['id']
    assert result['item'] == item
