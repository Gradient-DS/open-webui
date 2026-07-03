"""Unit tests for the Invite model — focuses on the new
``InviteTable.consume_invite_by_email`` method and the expiry filter
added to ``get_pending_invite_by_email`` for the SSO-invite flow.

We use an in-memory SQLite database so the tests are hermetic and don't
depend on the project's normal DB state. The Invite model is bound to a
temporary async engine via monkeypatching
``open_webui.models.invites.get_async_db_context``.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from open_webui.models import invites as invites_module
from open_webui.models.invites import Invite, Invites


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """Async in-memory SQLite session, with the Invite table created and
    the ``invites`` module's ``get_async_db_context`` patched to yield
    sessions on the test engine.

    Returns the async sessionmaker so individual tests can read rows back
    for direct assertions.
    """
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Invite.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        async with Session() as s:
            yield s

    monkeypatch.setattr(invites_module, 'get_async_db_context', _get_async_db_context)
    yield Session
    await engine.dispose()


async def _make_invite(
    Session,
    *,
    email: str = 'invitee@example.com',
    role: str = 'user',
    name: str = 'Invited User',
    expires_at: int | None = None,
    accepted_at: int | None = None,
    revoked_at: int | None = None,
) -> str:
    """Insert an Invite row directly and return its id."""
    invite_id = str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        invite = Invite(
            id=invite_id,
            email=email.lower(),
            name=name,
            token=str(uuid.uuid4()),
            role=role,
            invited_by='admin-id',
            expires_at=expires_at if expires_at is not None else now + 86400,
            accepted_at=accepted_at,
            revoked_at=revoked_at,
            created_at=now,
        )
        s.add(invite)
        await s.commit()
    return invite_id


class TestConsumeInviteByEmail:
    @pytest.mark.asyncio
    async def test_returns_invite_and_marks_accepted(self, db_session):
        await _make_invite(db_session, email='a@example.com', role='admin')

        consumed = await Invites.consume_invite_by_email('a@example.com')

        assert consumed is not None
        assert consumed.email == 'a@example.com'
        assert consumed.role == 'admin'
        assert consumed.accepted_at is not None
        # The DB row is also marked accepted.
        async with db_session() as s:
            row = (await s.execute(select(Invite).where(Invite.email == 'a@example.com'))).scalar_one()
            assert row.accepted_at is not None

    @pytest.mark.asyncio
    async def test_idempotent_second_call_returns_none(self, db_session):
        await _make_invite(db_session, email='a@example.com')

        first = await Invites.consume_invite_by_email('a@example.com')
        second = await Invites.consume_invite_by_email('a@example.com')

        assert first is not None
        assert second is None

    @pytest.mark.asyncio
    async def test_returns_none_when_already_accepted(self, db_session):
        await _make_invite(
            db_session,
            email='a@example.com',
            accepted_at=int(time.time()) - 60,
        )

        assert await Invites.consume_invite_by_email('a@example.com') is None

    @pytest.mark.asyncio
    async def test_returns_none_when_revoked(self, db_session):
        await _make_invite(
            db_session,
            email='a@example.com',
            revoked_at=int(time.time()) - 60,
        )

        assert await Invites.consume_invite_by_email('a@example.com') is None

    @pytest.mark.asyncio
    async def test_returns_none_when_expired(self, db_session):
        await _make_invite(
            db_session,
            email='a@example.com',
            expires_at=int(time.time()) - 60,
        )

        assert await Invites.consume_invite_by_email('a@example.com') is None

    @pytest.mark.asyncio
    async def test_returns_none_when_email_unknown(self, db_session):
        assert await Invites.consume_invite_by_email('nope@example.com') is None

    @pytest.mark.asyncio
    async def test_email_match_is_case_insensitive(self, db_session):
        await _make_invite(db_session, email='Alice@Example.COM')

        consumed = await Invites.consume_invite_by_email('alice@example.com')

        assert consumed is not None
        assert consumed.email == 'alice@example.com'


class TestGetPendingInviteByEmail:
    @pytest.mark.asyncio
    async def test_returns_active_invite(self, db_session):
        await _make_invite(db_session, email='a@example.com', role='user')
        invite = await Invites.get_pending_invite_by_email('a@example.com')
        assert invite is not None
        assert invite.role == 'user'

    @pytest.mark.asyncio
    async def test_filters_out_expired(self, db_session):
        await _make_invite(
            db_session,
            email='a@example.com',
            expires_at=int(time.time()) - 1,
        )
        assert await Invites.get_pending_invite_by_email('a@example.com') is None

    @pytest.mark.asyncio
    async def test_filters_out_accepted(self, db_session):
        await _make_invite(
            db_session,
            email='a@example.com',
            accepted_at=int(time.time()) - 60,
        )
        assert await Invites.get_pending_invite_by_email('a@example.com') is None

    @pytest.mark.asyncio
    async def test_filters_out_revoked(self, db_session):
        await _make_invite(
            db_session,
            email='a@example.com',
            revoked_at=int(time.time()) - 60,
        )
        assert await Invites.get_pending_invite_by_email('a@example.com') is None
