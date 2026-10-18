"""Guards the pre-read Content-Length rejection on file upload.

Today the size check happens only AFTER the whole body is read and stored
(the post-read check in ``files.py``, which stays authoritative because
Content-Length can lie). This pre-check rejects obviously-oversized
requests from the declared Content-Length header before ``upload_file``
ever calls ``upload_file_handler`` -- so a client sending a 250 MB upload
against a 10 MB cap gets a 413 immediately instead of having the whole body
read and buffered first.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile, status
from open_webui.routers import files as files_router
from open_webui.routers.files import upload_file

_MULTIPART_OVERHEAD = 16 * 1024


def _patch_config(monkeypatch, *, max_size_mb=None) -> None:
    """Route ``upload_file``'s ``Config.get('rag.file.max_size')`` read to a
    test value; every other key resolves to its caller-supplied default
    (None), keeping the test hermetic -- no config DB involved."""

    async def fake_get(key, default=None):
        if key == 'rag.file.max_size':
            return max_size_mb
        return default

    monkeypatch.setattr(files_router.Config, 'get', staticmethod(fake_get))


def _stub_downstream(monkeypatch) -> AsyncMock:
    """Replace ``upload_file_handler`` and ``publish_event`` with no-op
    stand-ins so tests where the precheck is expected to PASS don't touch
    real Storage/DB. Whether/how often the returned mock was awaited is the
    signal that the precheck let the request through (or didn't)."""
    handler = AsyncMock(return_value={'id': 'f1', 'filename': 'doc.pdf', 'meta': {}})
    monkeypatch.setattr(files_router, 'upload_file_handler', handler)
    monkeypatch.setattr(files_router, 'publish_event', AsyncMock())
    return handler


def _request(content_length: str | None) -> MagicMock:
    request = MagicMock()
    request.headers = {'content-length': content_length} if content_length is not None else {}
    return request


def _upload_file(filename: str = 'doc.pdf', content_type: str = 'application/pdf') -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(b'fake'), headers={'content-type': content_type})


def _user() -> SimpleNamespace:
    return SimpleNamespace(id='u1', email='u@example.com', name='User', role='user')


@pytest.mark.asyncio
async def test_oversize_content_length_rejected_before_handler_runs(monkeypatch):
    """Declared Content-Length past the 10MB cap + overhead -> 413, and the
    handler (which would read/store the body) is never invoked."""
    _patch_config(monkeypatch, max_size_mb=10)
    handler = _stub_downstream(monkeypatch)
    oversize = 10 * 1024 * 1024 + _MULTIPART_OVERHEAD + 1
    request = _request(str(oversize))

    with pytest.raises(HTTPException) as exc_info:
        await upload_file(request, background_tasks=BackgroundTasks(), file=_upload_file(), user=_user(), db=None)

    assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert '10' in str(exc_info.value.detail)
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_declared_length_at_cap_plus_overhead_boundary_is_accepted(monkeypatch):
    """Exactly at the cap + overhead boundary (not over it) must pass
    through -- the check is a strict ``>``, not ``>=``."""
    _patch_config(monkeypatch, max_size_mb=10)
    handler = _stub_downstream(monkeypatch)
    at_boundary = 10 * 1024 * 1024 + _MULTIPART_OVERHEAD
    request = _request(str(at_boundary))

    result = await upload_file(request, background_tasks=BackgroundTasks(), file=_upload_file(), user=_user(), db=None)

    assert result['id'] == 'f1'
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_declared_length_under_cap_is_accepted(monkeypatch):
    _patch_config(monkeypatch, max_size_mb=10)
    handler = _stub_downstream(monkeypatch)
    request = _request(str(1024))

    result = await upload_file(request, background_tasks=BackgroundTasks(), file=_upload_file(), user=_user(), db=None)

    assert result['id'] == 'f1'
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_max_size_configured_skips_precheck(monkeypatch):
    """rag.file.max_size unset (None) -> no pre-check rejection regardless
    of how large the declared Content-Length is."""
    _patch_config(monkeypatch, max_size_mb=None)
    handler = _stub_downstream(monkeypatch)
    request = _request(str(500 * 1024 * 1024))

    result = await upload_file(request, background_tasks=BackgroundTasks(), file=_upload_file(), user=_user(), db=None)

    assert result['id'] == 'f1'
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_content_length_skips_precheck(monkeypatch):
    """No Content-Length header at all -> no pre-check rejection (falls
    through to existing behavior)."""
    _patch_config(monkeypatch, max_size_mb=10)
    handler = _stub_downstream(monkeypatch)
    request = _request(None)

    result = await upload_file(request, background_tasks=BackgroundTasks(), file=_upload_file(), user=_user(), db=None)

    assert result['id'] == 'f1'
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_malformed_content_length_does_not_crash(monkeypatch):
    """A non-integer Content-Length must not raise an unhandled 500 -- it
    falls through (the post-read check remains the authoritative guard)."""
    _patch_config(monkeypatch, max_size_mb=10)
    handler = _stub_downstream(monkeypatch)
    request = _request('not-a-number')

    result = await upload_file(request, background_tasks=BackgroundTasks(), file=_upload_file(), user=_user(), db=None)

    assert result['id'] == 'f1'
    handler.assert_awaited_once()
