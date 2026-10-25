"""Uploaded audio goes unchanged through ordinary file ingestion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.routers import files


@pytest.mark.asyncio
async def test_audio_upload_uses_file_ingestion_without_transcription(monkeypatch):
    async def config(key, default=None):
        assert not key.startswith('audio.')
        return default

    monkeypatch.setattr(files.Config, 'get', config)
    ingest = AsyncMock()
    monkeypatch.setattr(files, 'process_file', ingest)
    monkeypatch.setattr(
        files.Files, 'get_file_by_id', AsyncMock(return_value=SimpleNamespace(meta={'status': 'processing'}))
    )
    failure = AsyncMock(side_effect=AssertionError('Audio should enter ingestion'))
    monkeypatch.setattr(files.Files, 'set_status', failure)
    monkeypatch.setattr(files, 'emit_file_status', AsyncMock())
    monkeypatch.setattr(files, '_cleanup_local_cache', lambda path: None)
    await files.process_uploaded_file(
        SimpleNamespace(),
        SimpleNamespace(filename='fictional-recording.mp3', content_type='audio/mpeg'),
        '/fictional/recording.mp3',
        SimpleNamespace(id='fictional-audio'),
        {},
        SimpleNamespace(id='fern'),
        db=SimpleNamespace(),
    )
    ingest.assert_awaited_once()
    assert ingest.call_args.args[1].file_id == 'fictional-audio'
    assert ingest.call_args.args[1].content is None
    failure.assert_not_awaited()
