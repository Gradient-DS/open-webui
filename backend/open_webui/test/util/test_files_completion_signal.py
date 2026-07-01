"""Phase 1 (Track 1 honest signal): _process_handler emits the file's ACTUAL
persisted status after process_file returns, not a hard-coded 'completed'.

- Native path → process_file embedded + persisted 'completed' synchronously →
  emits 'completed' (byte-identical to before).
- warren path → process_file returned right after submitting the job → persisted
  status is still 'processing' (no vectors yet) → emits 'processing', which both
  file:status listeners (Chat.svelte, KnowledgeBase.svelte) ignore, so the
  spinner persists until /ingest emits the real 'completed'.
- process_file raising → emits 'failed' (unchanged).
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
    file = SimpleNamespace(content_type='application/pdf')  # not image/video → native process_file branch
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
async def test_warren_processing_emits_processing(monkeypatch):
    # warren: process_file returned right after submitting the job — persisted
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
