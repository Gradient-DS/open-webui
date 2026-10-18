"""Uploads emit their persisted status after process_file returns.
Submitted soev-api jobs emit processing, which both UI listeners ignore until
the poller emits a terminal event; native completion and failures emit directly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from open_webui.routers import files as files_router


def _run_args(*, file_status, collection_name=None):
    """Build the process_uploaded_file arguments + the File row it re-fetches."""
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(STT_SUPPORTED_CONTENT_TYPES=[], CONTENT_EXTRACTION_ENGINE='tika')
            )
        )
    )
    file = SimpleNamespace(filename='report.pdf', content_type='application/pdf')
    file_item = SimpleNamespace(id='file-1')
    user = SimpleNamespace(id='user-1')
    meta: dict = {}
    if file_status is not None:
        meta['status'] = file_status
    if collection_name is not None:
        meta['collection_name'] = collection_name
    file_data = SimpleNamespace(meta=meta)
    return request, file, file_item, user, file_data


def _patch(monkeypatch, *, file_data, process_raises=None):
    emit = AsyncMock()
    monkeypatch.setattr(files_router, 'emit_file_status', emit)
    monkeypatch.setattr(files_router, '_cleanup_local_cache', MagicMock())

    process = AsyncMock(side_effect=process_raises)
    monkeypatch.setattr(files_router, 'process_file', process)

    files = MagicMock()
    files.get_file_by_id = AsyncMock(return_value=file_data)
    files.set_status = AsyncMock()
    monkeypatch.setattr(files_router, 'Files', files)
    return emit, files, process


@pytest.mark.asyncio
async def test_processing_uses_derived_type_before_media_detection(monkeypatch):
    request, file, file_item, user, file_data = _run_args(file_status='completed')
    file.filename, file.content_type = 'notes.md', 'image/png'
    _, files, process = _patch(monkeypatch, file_data=file_data)
    monkeypatch.setattr(files_router, '_is_text_file', MagicMock(return_value=False))
    monkeypatch.setattr(files_router.Config, 'get', AsyncMock(side_effect=lambda key, default=None: default))
    await files_router.process_uploaded_file(request, file, 'uploads/notes.md', file_item, {}, user, db=object())
    process.assert_awaited_once()
    files.set_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_relabelling_updates_stored_content_type(monkeypatch):
    request, file, file_item, user, file_data = _run_args(file_status='completed')
    file.filename, file.content_type = 'source.ts', 'video/mp2t'
    _, files, process = _patch(monkeypatch, file_data=file_data)
    files.update_file_metadata_by_id = AsyncMock()
    monkeypatch.setattr(files_router, '_is_text_file', MagicMock(return_value=True))
    monkeypatch.setattr(files_router.Config, 'get', AsyncMock(side_effect=lambda key, default=None: default))
    session = object()
    await files_router.process_uploaded_file(request, file, 'uploads/source.ts', file_item, {}, user, db=session)
    files.update_file_metadata_by_id.assert_awaited_once_with('file-1', {'content_type': 'text/plain'}, db=session)
    process.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_completed_emits_completed(monkeypatch):
    request, file, file_item, user, file_data = _run_args(file_status='completed', collection_name='file-file-1')
    emit, files, _ = _patch(monkeypatch, file_data=file_data)

    await files_router.process_uploaded_file(
        request, file, 'uploads/file-1_r.pdf', file_item, {}, user, db=object(), knowledge_id=None
    )

    emit.assert_awaited_once()
    kwargs = emit.await_args.kwargs
    assert kwargs['status'] == 'completed'
    assert kwargs['collection_name'] == 'file-file-1'
    files.set_status.assert_not_awaited()  # success path never marks failed


@pytest.mark.asyncio
async def test_a_submitted_file_emits_processing_which_the_ui_ignores(monkeypatch):
    # soev-api: process_file returned right after submitting the job — persisted
    # status is still 'processing' and there is no collection yet.
    request, file, file_item, user, file_data = _run_args(file_status='processing')
    emit, files, _ = _patch(monkeypatch, file_data=file_data)

    await files_router.process_uploaded_file(
        request, file, 'uploads/file-1_r.pdf', file_item, {}, user, db=object(), knowledge_id=None
    )

    kwargs = emit.await_args.kwargs
    assert kwargs['status'] == 'processing'  # listeners ignore it → spinner persists
    assert kwargs['collection_name'] is None
    files.set_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_status_defaults_to_completed(monkeypatch):
    # A File row with no status key (or a missing row) falls back to 'completed'
    # so the native no-status path is byte-identical to before.
    request, file, file_item, user, file_data = _run_args(file_status=None)
    emit, files, _ = _patch(monkeypatch, file_data=file_data)

    await files_router.process_uploaded_file(
        request, file, 'uploads/file-1_r.pdf', file_item, {}, user, db=object(), knowledge_id=None
    )

    assert emit.await_args.kwargs['status'] == 'completed'


@pytest.mark.asyncio
async def test_process_file_raising_emits_failed(monkeypatch):
    request, file, file_item, user, file_data = _run_args(file_status='processing')
    emit, files, _ = _patch(monkeypatch, file_data=file_data, process_raises=RuntimeError('boom'))

    await files_router.process_uploaded_file(
        request, file, 'uploads/file-1_r.pdf', file_item, {}, user, db=object(), knowledge_id=None
    )

    files.set_status.assert_awaited_once()
    assert files.set_status.await_args.args[:2] == ('file-1', 'failed')
    kwargs = emit.await_args.kwargs
    assert kwargs['status'] == 'failed'
    assert 'boom' in kwargs['error']
