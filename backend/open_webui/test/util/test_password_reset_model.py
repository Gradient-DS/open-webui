"""Unit tests for the PasswordResetTokenTable model — hermetic in-memory SQLite.

Mirrors the async-SQLite + monkeypatched get_async_db_context pattern used in
test_invites_model.py / test_skill_files_model.py.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from open_webui.models import password_reset as pr_module
from open_webui.models.password_reset import PasswordResetToken, PasswordResetTokens
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


# NOTE: this suite has no `asyncio_mode = auto`; every async test MUST carry
# `@pytest.mark.asyncio` explicitly (matching test_invites_model.py), or it
# will be collected-but-not-run (a silent false green).


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(PasswordResetToken.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _ctx(db=None):
        async with Session() as s:
            yield s

    monkeypatch.setattr(pr_module, 'get_async_db_context', _ctx)
    yield Session
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_get_by_token_hash(db_session):
    now = int(time.time())
    created = await PasswordResetTokens.create(user_id='user-1', token_hash='hash-abc', expires_at=now + 1800)
    assert created.user_id == 'user-1'
    assert created.token_hash == 'hash-abc'
    assert created.used_at is None

    fetched = await PasswordResetTokens.get_by_token_hash('hash-abc')
    assert fetched is not None
    assert fetched.id == created.id

    assert await PasswordResetTokens.get_by_token_hash('does-not-exist') is None


@pytest.mark.asyncio
async def test_mark_used(db_session):
    now = int(time.time())
    created = await PasswordResetTokens.create(user_id='user-1', token_hash='hash-used', expires_at=now + 1800)
    assert await PasswordResetTokens.mark_used(created.id) is True
    fetched = await PasswordResetTokens.get_by_token_hash('hash-used')
    assert fetched.used_at is not None


@pytest.mark.asyncio
async def test_invalidate_unused_for_user(db_session):
    now = int(time.time())
    await PasswordResetTokens.create(user_id='user-1', token_hash='old-1', expires_at=now + 1800)
    await PasswordResetTokens.create(user_id='user-1', token_hash='old-2', expires_at=now + 1800)
    await PasswordResetTokens.create(user_id='user-2', token_hash='other', expires_at=now + 1800)

    await PasswordResetTokens.invalidate_unused_for_user('user-1')

    assert (await PasswordResetTokens.get_by_token_hash('old-1')).used_at is not None
    assert (await PasswordResetTokens.get_by_token_hash('old-2')).used_at is not None
    # A different user's token is untouched.
    assert (await PasswordResetTokens.get_by_token_hash('other')).used_at is None
