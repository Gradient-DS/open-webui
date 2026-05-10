"""Unit tests for the Invite model — focuses on the new
``InviteTable.consume_invite_by_email`` method and the expiry filter
added to ``get_pending_invite_by_email`` for the SSO-invite flow.

We use an in-memory SQLite database so the tests are hermetic and don't
depend on the project's normal DB state. The Invite model is bound to
a temporary engine via monkeypatching ``open_webui.models.invites.get_db``.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models import invites as invites_module
from open_webui.models.invites import Invite, Invites


@pytest.fixture
def db_session(monkeypatch):
    """In-memory SQLite session, with the Invite table created and the
    ``invites`` module's ``get_db`` patched to yield from it."""
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False})
    Invite.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    @contextmanager
    def _get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(invites_module, 'get_db', _get_db)
    yield Session
    engine.dispose()


def _make_invite(
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
    with Session() as s:
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
        s.commit()
    return invite_id


class TestConsumeInviteByEmail:
    def test_returns_invite_and_marks_accepted(self, db_session):
        _make_invite(db_session, email='a@example.com', role='admin')

        consumed = Invites.consume_invite_by_email('a@example.com')

        assert consumed is not None
        assert consumed.email == 'a@example.com'
        assert consumed.role == 'admin'
        assert consumed.accepted_at is not None
        # The DB row is also marked accepted.
        with db_session() as s:
            row = s.query(Invite).filter_by(email='a@example.com').first()
            assert row.accepted_at is not None

    def test_idempotent_second_call_returns_none(self, db_session):
        _make_invite(db_session, email='a@example.com')

        first = Invites.consume_invite_by_email('a@example.com')
        second = Invites.consume_invite_by_email('a@example.com')

        assert first is not None
        assert second is None

    def test_returns_none_when_already_accepted(self, db_session):
        _make_invite(
            db_session,
            email='a@example.com',
            accepted_at=int(time.time()) - 60,
        )

        assert Invites.consume_invite_by_email('a@example.com') is None

    def test_returns_none_when_revoked(self, db_session):
        _make_invite(
            db_session,
            email='a@example.com',
            revoked_at=int(time.time()) - 60,
        )

        assert Invites.consume_invite_by_email('a@example.com') is None

    def test_returns_none_when_expired(self, db_session):
        _make_invite(
            db_session,
            email='a@example.com',
            expires_at=int(time.time()) - 60,
        )

        assert Invites.consume_invite_by_email('a@example.com') is None

    def test_returns_none_when_email_unknown(self, db_session):
        assert Invites.consume_invite_by_email('nope@example.com') is None

    def test_email_match_is_case_insensitive(self, db_session):
        _make_invite(db_session, email='Alice@Example.COM')

        consumed = Invites.consume_invite_by_email('alice@example.com')

        assert consumed is not None
        assert consumed.email == 'alice@example.com'


class TestGetPendingInviteByEmail:
    def test_returns_active_invite(self, db_session):
        _make_invite(db_session, email='a@example.com', role='user')
        invite = Invites.get_pending_invite_by_email('a@example.com')
        assert invite is not None
        assert invite.role == 'user'

    def test_filters_out_expired(self, db_session):
        _make_invite(
            db_session,
            email='a@example.com',
            expires_at=int(time.time()) - 1,
        )
        assert Invites.get_pending_invite_by_email('a@example.com') is None

    def test_filters_out_accepted(self, db_session):
        _make_invite(
            db_session,
            email='a@example.com',
            accepted_at=int(time.time()) - 60,
        )
        assert Invites.get_pending_invite_by_email('a@example.com') is None

    def test_filters_out_revoked(self, db_session):
        _make_invite(
            db_session,
            email='a@example.com',
            revoked_at=int(time.time()) - 60,
        )
        assert Invites.get_pending_invite_by_email('a@example.com') is None
