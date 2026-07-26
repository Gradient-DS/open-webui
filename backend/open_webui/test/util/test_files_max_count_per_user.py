"""Backend per-user file-count cap (Phase 1.4).

Today ``rag.file.max_count`` is a frontend per-message advisory only --
nothing stops a user from uploading an unbounded total number of files over
time. This adds a backend-enforced cap on a user's total stored file count,
via a new ``rag.file.max_count_per_user`` key (default ``None`` = unlimited).

Mirrors ``routers/automations.py``'s ``check_automation_limits`` max_count
pattern: 403 Forbidden, ``count >= cap`` (not ``>``), config value coerced
through ``int()`` and skipped entirely when falsy/unset.

Route-level tests drive ``upload_file_handler`` directly (mirroring
``test_files_image_reject.py`` / the route-level half of
``test_upload_guard.py``): monkeypatched ``Config.get`` + a stubbed
``Files.count_files_by_user_id`` (the existing per-user count method this
task reuses -- see the report for why a new ``count_by_user_id`` method was
not added). Storage/DB stay unmocked, so a request that clears the cap guard
fails downstream with a *different* error; that's the established way this
suite proves a guard did or didn't fire without doing a full upload.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile, status
from open_webui.routers import files as files_router
from open_webui.routers.files import upload_file_handler


def _patch_config(monkeypatch, *, max_count_per_user=None) -> None:
    """Route the handler's ``Config.get('rag.file.max_count_per_user')``
    read to a test value; every other key resolves to its caller-supplied
    default, keeping the test hermetic -- no config DB involved."""
    values = {'rag.file.max_count_per_user': max_count_per_user}

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(files_router.Config, 'get', staticmethod(fake_get))


def _patch_count(monkeypatch, *, count: int) -> AsyncMock:
    mock = AsyncMock(return_value=count)
    monkeypatch.setattr(files_router.Files, 'count_files_by_user_id', mock)
    return mock


def _upload_file(filename: str = 'doc.pdf', content_type: str = 'application/pdf') -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(b'fake'), headers={'content-type': content_type})


def _user() -> SimpleNamespace:
    return SimpleNamespace(id='u1', email='u@example.com', name='User', role='user')


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id='a1', email='a@example.com', name='Admin', role='admin')


def _request() -> MagicMock:
    return MagicMock()


@pytest.mark.asyncio
async def test_upload_rejected_when_at_cap(monkeypatch):
    _patch_config(monkeypatch, max_count_per_user=5)
    count_mock = _patch_count(monkeypatch, count=5)

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(), file=_upload_file(), process=False, process_in_background=False, user=_user()
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert '5' in str(exc_info.value.detail)
    count_mock.assert_awaited_once_with(user_id='u1', db=None)


@pytest.mark.asyncio
async def test_upload_rejected_when_over_cap(monkeypatch):
    _patch_config(monkeypatch, max_count_per_user=5)
    _patch_count(monkeypatch, count=9)

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(), file=_upload_file(), process=False, process_in_background=False, user=_user()
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_upload_accepted_when_under_cap(monkeypatch):
    _patch_config(monkeypatch, max_count_per_user=5)
    _patch_count(monkeypatch, count=4)

    # Guard passes -> fails downstream (Storage/DB unmocked), NOT a cap reject.
    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(), file=_upload_file(), process=False, process_in_background=False, user=_user()
        )

    assert exc_info.value.status_code != status.HTTP_403_FORBIDDEN
    assert 'File limit' not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_key_unset_skips_check_and_never_calls_count(monkeypatch):
    _patch_config(monkeypatch, max_count_per_user=None)
    count_mock = _patch_count(monkeypatch, count=9999)

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(), file=_upload_file(), process=False, process_in_background=False, user=_user()
        )

    assert exc_info.value.status_code != status.HTTP_403_FORBIDDEN
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_count_cap_guard_false_skips_check_even_at_cap(monkeypatch):
    """The internal opt-out (``count_cap_guard=False``) that server-generated
    upload paths (image/audio generation, agent-internal blobs) use must skip
    the cap entirely -- even when the user is already at/over it -- and must
    not even query the count. Locks in the "user upload path only" scoping."""
    _patch_config(monkeypatch, max_count_per_user=1)
    count_mock = _patch_count(monkeypatch, count=999)

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(),
            file=_upload_file(),
            process=False,
            process_in_background=False,
            user=_user(),
            count_cap_guard=False,
        )

    assert exc_info.value.status_code != status.HTTP_403_FORBIDDEN
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_bypasses_cap_even_at_or_over_it(monkeypatch):
    """Mirrors automations.py's ``check_automation_limits`` (admins bypass
    all limits). Also required operationally here: the key is
    env-authoritative, so an admin with no admin-UI escape hatch must never
    be lockable out of uploads by their own configured cap."""
    _patch_config(monkeypatch, max_count_per_user=1)
    count_mock = _patch_count(monkeypatch, count=999)

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(), file=_upload_file(), process=False, process_in_background=False, user=_admin()
        )

    assert exc_info.value.status_code != status.HTTP_403_FORBIDDEN
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_max_count_per_user_treated_as_unlimited(monkeypatch):
    """Mirrors automations.py's ``max_count > 0`` guard: a falsy-but-not-None
    configured value (0) is treated the same as unset."""
    _patch_config(monkeypatch, max_count_per_user=0)
    count_mock = _patch_count(monkeypatch, count=9999)

    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(), file=_upload_file(), process=False, process_in_background=False, user=_user()
        )

    assert exc_info.value.status_code != status.HTTP_403_FORBIDDEN
    count_mock.assert_not_awaited()
