"""Focused unit test for ``FilesTable.count_files_by_user_id`` (models/files.py).

Phase 1.4 (backend per-user file-count cap) reuses this existing method
instead of adding a new ``Files.count_by_user_id`` — see the task report for
why. It had no dedicated unit test before this task; this mirrors the
in-memory-SQLite + monkeypatched ``get_async_db_context`` pattern used for
other DB-coupled model methods (test_invites_model.py /
test_password_reset_model.py / test_config_env_authoritative.py).
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from open_webui.models import files as files_module
from open_webui.models.files import File, Files
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """Async in-memory SQLite session, with the File table created and the
    ``files`` module's ``get_async_db_context`` patched to yield sessions on
    the test engine."""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(File.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        async with Session() as s:
            yield s

    monkeypatch.setattr(files_module, 'get_async_db_context', _get_async_db_context)
    yield Session
    await engine.dispose()


async def _make_file(Session, *, user_id: str) -> str:
    file_id = str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        s.add(File(id=file_id, user_id=user_id, filename='doc.pdf', created_at=now, updated_at=now))
        await s.commit()
    return file_id


@pytest.mark.asyncio
async def test_counts_only_the_given_users_files(db_session):
    await _make_file(db_session, user_id='u1')
    await _make_file(db_session, user_id='u1')
    await _make_file(db_session, user_id='u2')

    assert await Files.count_files_by_user_id(user_id='u1') == 2
    assert await Files.count_files_by_user_id(user_id='u2') == 1


@pytest.mark.asyncio
async def test_zero_for_user_with_no_files(db_session):
    await _make_file(db_session, user_id='u1')

    assert await Files.count_files_by_user_id(user_id='u2') == 0


@pytest.mark.asyncio
async def test_no_user_id_counts_all_files(db_session):
    await _make_file(db_session, user_id='u1')
    await _make_file(db_session, user_id='u2')

    assert await Files.count_files_by_user_id() == 2
