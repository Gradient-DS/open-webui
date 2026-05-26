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

from open_webui.routers.files import upload_file_handler


def _request(engine: str, stt: list | None = None) -> MagicMock:
    """Build a minimal FastAPI Request stand-in.

    The handler only touches ``request.app.state.config`` for these checks,
    so we don't need a real ASGI scope.
    """
    cfg = SimpleNamespace(
        CONTENT_EXTRACTION_ENGINE=engine,
        STT_SUPPORTED_CONTENT_TYPES=stt or [],
        ALLOWED_FILE_EXTENSIONS=None,
    )
    request = MagicMock()
    request.app.state.config = cfg
    return request


def _upload_file(filename: str, content_type: str) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(b'fake'), headers={'content-type': content_type})


def _user() -> SimpleNamespace:
    return SimpleNamespace(id='u1', email='u@example.com', name='User', role='user')


@pytest.mark.asyncio
async def test_image_upload_rejected_when_engine_does_not_support():
    request = _request(engine='tika')
    file = _upload_file('photo.png', 'image/png')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert 'image/png' in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_video_upload_rejected_when_engine_does_not_support():
    request = _request(engine='tika')
    file = _upload_file('clip.mp4', 'video/mp4')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.parametrize('engine', ['external', 'datalab_marker', 'mistral_ocr'])
@pytest.mark.asyncio
async def test_image_upload_passes_415_check_for_image_capable_engines(engine):
    """The 415 guard returns control to the rest of the handler for engines
    that DO process images. The handler then fails downstream because we
    haven't mocked Storage/DB — that's fine; this test only asserts the
    415 didn't fire."""
    request = _request(engine=engine)
    file = _upload_file('photo.png', 'image/png')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code != status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.asyncio
async def test_audio_upload_passes_415_check_when_stt_handles_it():
    """STT-supported content types skip the 415 (transcribe path takes over)."""
    request = _request(engine='tika', stt=['audio/mpeg'])
    file = _upload_file('clip.mp3', 'audio/mpeg')

    # audio/* doesn't match the image/video startswith filter, so it never
    # hits the 415 branch in the first place — this test guards against a
    # future regression where audio gets bundled into the same filter.
    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=True, process_in_background=False, user=_user())

    assert exc_info.value.status_code != status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.asyncio
async def test_unsupported_content_type_with_process_false_skips_check():
    """process=False means the file is stored but not parsed; the 415 only
    fires when processing is requested."""
    request = _request(engine='tika')
    file = _upload_file('photo.png', 'image/png')

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(request, file=file, process=False, process_in_background=False, user=_user())

    assert exc_info.value.status_code != status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
