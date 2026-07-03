"""Guards the synchronous 415 reject for image/video uploads when the
configured extraction engine cannot handle them.

Without this guard, the upload succeeds, the background ``_process_handler``
raises ``Exception('File type image/png is not supported for processing')``,
and the only signal to the frontend is a single Socket.IO ``file:status``
emit. If that emit drops (the 2026-04-29 incident pattern), the spinner
spins forever. Rejecting up front converts the failure into an HTTP 415
that ``uploadFile()`` propagates synchronously.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status
from fastapi import UploadFile

from open_webui.routers import files as files_router
from open_webui.routers.files import upload_file_handler


def _patch_config(monkeypatch, *, engine: str, stt: list | None = None, allowed: list | None = None) -> None:
    """Route the handler's per-key Config reads to test values.

    upload_file_handler reads ``rag.content_extraction_engine``
    (CONTENT_EXTRACTION_ENGINE), ``audio.stt.supported_content_types``
    (STT_SUPPORTED_CONTENT_TYPES) and ``rag.file.allowed_extensions``
    (ALLOWED_FILE_EXTENSIONS) via ``await Config.get(...)``; patching
    ``Config.get`` keeps the tests hermetic — no config DB involved.
    ``rag.file.max_size`` intentionally resolves to None (no size cap).
    """
    values = {
        'rag.content_extraction_engine': engine,
        'audio.stt.supported_content_types': stt or [],
        'rag.file.allowed_extensions': allowed,
    }

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(files_router.Config, 'get', staticmethod(fake_get))


def _request() -> MagicMock:
    """Minimal FastAPI Request stand-in — config now comes from the per-key
    store (see ``_patch_config``), so no real ASGI scope is needed."""
    return MagicMock()


def _upload_file(filename: str, content_type: str) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(b'fake'), headers={'content-type': content_type})


def _user() -> SimpleNamespace:
    return SimpleNamespace(id='u1', email='u@example.com', name='User', role='user')


@pytest.mark.asyncio
async def test_image_upload_rejected_when_engine_does_not_support(monkeypatch):
    _patch_config(monkeypatch, engine='tika')
    request = _request()
    file = _upload_file('photo.png', 'image/png')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert 'image/png' in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_video_upload_rejected_when_engine_does_not_support(monkeypatch):
    _patch_config(monkeypatch, engine='tika')
    request = _request()
    file = _upload_file('clip.mp4', 'video/mp4')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.parametrize('engine', ['external', 'datalab_marker', 'mistral_ocr'])
@pytest.mark.asyncio
async def test_image_upload_passes_415_check_for_image_capable_engines(engine, monkeypatch):
    """The 415 guard returns control to the rest of the handler for engines
    that DO process images. The handler then fails downstream because we
    haven't mocked Storage/DB — that's fine; this test only asserts the
    415 didn't fire."""
    _patch_config(monkeypatch, engine=engine)
    request = _request()
    file = _upload_file('photo.png', 'image/png')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code != status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.asyncio
async def test_audio_upload_passes_415_check_when_stt_handles_it(monkeypatch):
    """STT-supported content types skip the 415 (transcribe path takes over)."""
    _patch_config(monkeypatch, engine='tika', stt=['audio/mpeg'])
    request = _request()
    file = _upload_file('clip.mp3', 'audio/mpeg')

    # audio/* doesn't match the image/video startswith filter, so it never
    # hits the 415 branch in the first place — this test guards against a
    # future regression where audio gets bundled into the same filter.
    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code != status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.asyncio
async def test_unsupported_content_type_with_process_false_skips_check(monkeypatch):
    """process=False means the file is stored but not parsed; the 415 only
    fires when processing is requested."""
    _patch_config(monkeypatch, engine='tika')
    request = _request()
    file = _upload_file('photo.png', 'image/png')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=False, process_in_background=False, user=_user())

    assert exc_info.value.status_code != status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.asyncio
async def test_disallowed_extension_rejected_by_allowlist(monkeypatch):
    """A file with a real, non-allowed extension fast-rejects with a 400."""
    _patch_config(monkeypatch, engine='external', allowed=['pdf'])
    request = _request()
    file = _upload_file('logo.svg', 'image/svg+xml')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert 'svg is not allowed' in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_empty_extension_passes_allowlist(monkeypatch):
    """A genuinely extension-less document (unknown content type → no derived
    extension) must NOT be rejected by the allow-list — legit no-extension docs
    (e.g. exported pages) have to be ingestable. It fails downstream here
    (Storage/DB unmocked); we only assert the allow-list 400 did not fire."""
    _patch_config(monkeypatch, engine='external', allowed=['pdf'])
    request = _request()
    file = _upload_file('ASB - Microsoft Entra admin center', 'application/x-unknowntype')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert 'is not allowed' not in str(exc_info.value.detail)
