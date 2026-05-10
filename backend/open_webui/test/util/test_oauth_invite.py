"""Integration tests for the OAuth + invite signup flow.

These tests mock just enough of the OAuth/authlib stack to drive
``OAuthManager.handle_callback`` end-to-end on the *new-user* branch
(which is where invite consumption + ``OAUTH_INVITE_REQUIRED`` decisions
live). The DB layer is in-memory SQLite; the remote OAuth client is
mocked to return canned ``authorize_access_token`` / ``userinfo`` data.

We don't try to exercise every branch of the upstream callback —
just the new behaviours wired in by the SSO-invite work:

  * pending invite drives role + bypasses ``OAUTH_ALLOWED_DOMAINS``
  * ``OAUTH_INVITE_REQUIRED`` denies signup when no invite is present
  * expired invite is treated as no invite
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models import invites as invites_module
from open_webui.models.invites import Invite, Invites
from open_webui.utils import oauth as oauth_module
from open_webui.utils.oauth import OAuthManager, auth_manager_config


@pytest.fixture
def db_session(monkeypatch):
    """Sharded in-memory SQLite for the Invite table only.

    The OAuth callback also touches ``Users``, ``Auths``, ``OAuthSessions``,
    and ``Groups``; we don't want them hitting a real DB so we mock those
    callsites instead of binding all the metadata.
    """
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
    email: str,
    role: str = 'user',
    name: str = 'Invited User',
    expires_at: int | None = None,
    accepted_at: int | None = None,
    revoked_at: int | None = None,
) -> str:
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


@pytest.fixture
def oauth_manager_with_mocks(monkeypatch):
    """Construct an ``OAuthManager`` with the heavy dependencies mocked.

    Yields a tuple of ``(manager, fake_user_holder, set_userinfo)``:
      * manager: the OAuthManager instance
      * fake_user_holder: a list that the mocked ``Auths.insert_new_auth``
        appends provisioned users to (so tests can assert role/name)
      * set_userinfo: callable to seed the userinfo claims for a test
    """
    # Avoid running OAUTH_PROVIDERS' register callbacks in __init__.
    monkeypatch.setattr(oauth_module, 'OAUTH_PROVIDERS', {})

    manager = OAuthManager(app=MagicMock())

    # Provide a 'microsoft' entry that handle_callback dereferences via
    # OAUTH_PROVIDERS[provider].get('sub_claim') / .get('picture_url').
    monkeypatch.setitem(oauth_module.OAUTH_PROVIDERS, 'microsoft', {'sub_claim': 'sub'})

    userinfo: dict = {}

    def set_userinfo(**claims):
        userinfo.clear()
        userinfo.update(claims)

    fake_token = {'access_token': 'token-value', 'userinfo': None, 'expires_in': 3600}

    fake_client = MagicMock()
    fake_client.authorize_access_token = AsyncMock(return_value=fake_token)
    fake_client.userinfo = AsyncMock(side_effect=lambda token: dict(userinfo))
    manager._clients['microsoft'] = fake_client

    # Default auth manager config — tests override individual fields.
    auth_manager_config.OAUTH_EMAIL_CLAIM = 'email'
    auth_manager_config.OAUTH_USERNAME_CLAIM = 'name'
    auth_manager_config.OAUTH_SUB_CLAIM = 'sub'
    auth_manager_config.OAUTH_PICTURE_CLAIM = ''
    auth_manager_config.OAUTH_ROLES_CLAIM = ''
    auth_manager_config.OAUTH_GROUPS_CLAIM = ''
    auth_manager_config.OAUTH_ALLOWED_ROLES = ['user', 'admin']
    auth_manager_config.OAUTH_ADMIN_ROLES = ['admin']
    auth_manager_config.OAUTH_ALLOWED_DOMAINS = ['*']
    auth_manager_config.ENABLE_OAUTH_SIGNUP = True
    auth_manager_config.OAUTH_INVITE_REQUIRED = False
    auth_manager_config.OAUTH_MERGE_ACCOUNTS_BY_EMAIL = False
    auth_manager_config.ENABLE_OAUTH_ROLE_MANAGEMENT = False
    auth_manager_config.ENABLE_OAUTH_GROUP_MANAGEMENT = False
    auth_manager_config.OAUTH_UPDATE_NAME_ON_LOGIN = False
    auth_manager_config.OAUTH_UPDATE_EMAIL_ON_LOGIN = False
    auth_manager_config.OAUTH_UPDATE_PICTURE_ON_LOGIN = False
    auth_manager_config.OAUTH_AUDIENCE = ''
    auth_manager_config.WEBHOOK_URL = ''
    auth_manager_config.JWT_EXPIRES_IN = '1h'
    auth_manager_config.DEFAULT_USER_ROLE = 'user'

    # Mock all the DB-touching helpers handle_callback uses except Invites.
    provisioned: list = []

    def fake_insert_new_auth(*, email, password, name, profile_image_url, role, oauth, db=None):
        u = SimpleNamespace(
            id=str(uuid.uuid4()),
            email=email,
            name=name,
            role=role,
            profile_image_url=profile_image_url,
        )
        provisioned.append({'email': email, 'name': name, 'role': role, 'profile_image_url': profile_image_url})
        u.model_dump_json = MagicMock(return_value='{}')
        return u

    monkeypatch.setattr(oauth_module.Users, 'get_user_by_oauth_sub', MagicMock(return_value=None))
    monkeypatch.setattr(oauth_module.Users, 'get_user_by_email', MagicMock(return_value=None))
    monkeypatch.setattr(oauth_module.Users, 'update_user_oauth_by_id', MagicMock())
    monkeypatch.setattr(oauth_module.Auths, 'insert_new_auth', MagicMock(side_effect=fake_insert_new_auth))
    monkeypatch.setattr(oauth_module, 'apply_default_group_assignment', MagicMock())
    monkeypatch.setattr(oauth_module, 'create_token', MagicMock(return_value='jwt-token'))
    monkeypatch.setattr(oauth_module.OAuthSessions, 'get_sessions_by_user_id', MagicMock(return_value=[]))
    monkeypatch.setattr(oauth_module.OAuthSessions, 'create_session', MagicMock(return_value=None))
    monkeypatch.setattr(oauth_module.OAuthSessions, 'delete_session_by_id', MagicMock())

    return manager, provisioned, set_userinfo


def _fake_request():
    request = MagicMock()
    request.app.state.config.WEBUI_URL = 'http://localhost'
    request.app.state.config.DEFAULT_GROUP_ID = ''
    request.app.state.config.USER_PERMISSIONS = {}
    request.base_url = 'http://localhost'
    return request


def _fake_response():
    response = MagicMock()
    response.headers = {}
    return response


@pytest.mark.asyncio
async def test_oauth_signup_consumes_pending_invite_and_uses_invite_role(db_session, oauth_manager_with_mocks):
    manager, provisioned, set_userinfo = oauth_manager_with_mocks

    _make_invite(db_session, email='alice@example.com', role='admin', name='Alice')

    set_userinfo(sub='oauth-sub-1', email='Alice@Example.com', name='')

    await manager.handle_callback(_fake_request(), 'microsoft', _fake_response())

    assert len(provisioned) == 1
    assert provisioned[0]['email'] == 'alice@example.com'
    assert provisioned[0]['role'] == 'admin'
    # Falls back to invite name when the OAuth claim doesn't carry one.
    assert provisioned[0]['name'] == 'Alice'

    # The invite should be marked accepted.
    assert Invites.get_pending_invite_by_email('alice@example.com') is None


@pytest.mark.asyncio
async def test_oauth_signup_with_invite_required_and_no_invite_denied(db_session, oauth_manager_with_mocks):
    manager, provisioned, set_userinfo = oauth_manager_with_mocks
    auth_manager_config.OAUTH_INVITE_REQUIRED = True

    set_userinfo(sub='oauth-sub-1', email='nobody@example.com', name='Nobody')

    # The error is caught inside handle_callback; the redirect carries the
    # error message rather than raising. We assert no user was provisioned.
    await manager.handle_callback(_fake_request(), 'microsoft', _fake_response())

    assert provisioned == []


@pytest.mark.asyncio
async def test_invite_bypasses_domain_allowlist(db_session, oauth_manager_with_mocks):
    manager, provisioned, set_userinfo = oauth_manager_with_mocks
    auth_manager_config.OAUTH_ALLOWED_DOMAINS = ['soev.ai']

    _make_invite(db_session, email='external@other.com', role='user')
    set_userinfo(sub='oauth-sub-1', email='external@other.com', name='External')

    await manager.handle_callback(_fake_request(), 'microsoft', _fake_response())

    assert len(provisioned) == 1
    assert provisioned[0]['email'] == 'external@other.com'


@pytest.mark.asyncio
async def test_no_invite_no_signup_falls_back_to_domain_check(db_session, oauth_manager_with_mocks):
    """Existing behavior preserved: domain allowlist still denies non-matching
    emails when there's no invite and ``OAUTH_INVITE_REQUIRED`` is off."""
    manager, provisioned, set_userinfo = oauth_manager_with_mocks
    auth_manager_config.OAUTH_ALLOWED_DOMAINS = ['soev.ai']

    set_userinfo(sub='oauth-sub-1', email='outsider@other.com', name='Outsider')

    await manager.handle_callback(_fake_request(), 'microsoft', _fake_response())

    assert provisioned == []


@pytest.mark.asyncio
async def test_expired_invite_falls_through_to_domain_check(db_session, oauth_manager_with_mocks):
    """An expired invite should be treated as if no invite exists."""
    manager, provisioned, set_userinfo = oauth_manager_with_mocks
    auth_manager_config.OAUTH_INVITE_REQUIRED = True

    _make_invite(
        db_session,
        email='expired@example.com',
        role='admin',
        expires_at=int(time.time()) - 60,
    )
    set_userinfo(sub='oauth-sub-1', email='expired@example.com', name='Expired')

    await manager.handle_callback(_fake_request(), 'microsoft', _fake_response())

    assert provisioned == []


@pytest.mark.asyncio
async def test_oauth_role_invite_takes_precedence_over_default(db_session, oauth_manager_with_mocks):
    """Invite-supplied admin role should override the default role at signup."""
    manager, provisioned, set_userinfo = oauth_manager_with_mocks
    auth_manager_config.DEFAULT_USER_ROLE = 'user'

    _make_invite(db_session, email='admin@example.com', role='admin')
    set_userinfo(sub='oauth-sub-1', email='admin@example.com', name='Admin User')

    await manager.handle_callback(_fake_request(), 'microsoft', _fake_response())

    assert len(provisioned) == 1
    assert provisioned[0]['role'] == 'admin'
