# Forgot Password (Email Users) Implementation Plan

## ⚠️ Worktree layout — READ FIRST (kickoff context)

Implemented in an isolated git worktree on branch `feat/forgot-password-email-users`.
Edit ONLY the worktree — never the main checkout.

| Repo (as written in this plan) | Worktree to edit |
|---|---|
| `open-webui/…` | `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/…` |

Absolute `cd` commands already target the worktree; repo-relative `Files:` paths
mean the worktree above. This is an OWUI-only plan (no genai-utils / soev-agents changes).

**Python env (backend):** `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python`
(already bootstrapped with `[soev]` extras by the stack). Frontend uses this worktree's `npm`.
Stack: FE http://localhost:18173 · BE http://localhost:18180 · Postgres localhost:18132.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add self-service password reset for email/password users — a "Forgot password?" link that emails a time-limited, single-use reset link via the existing Microsoft Graph mail sender.

**Architecture:** A new stateful `password_reset_token` table (mirroring the existing `invite` table) holds single-use, expiring tokens. The raw token travels in the emailed URL; only its SHA-256 hash is stored at rest. Two new unauthenticated endpoints on the existing `auths` router (`/password/forgot`, `/password/reset`) plus a token-validate endpoint drive the flow. The reset email reuses `graph_mail_client.send_mail` and a new render function. Frontend adds a `forgot` mode to the existing auth page and a `reset-password/[token]` route mirroring `invite/[token]`. Everything is gated behind a new `ENABLE_FORGOT_PASSWORD` PersistentConfig (default off), and is additive — no upstream code paths are modified beyond inserting new branches/keys.

**Tech Stack:** FastAPI + async SQLAlchemy (asyncpg/aiosqlite), Alembic, SvelteKit 5 (runes), Microsoft Graph mail, Helm.

## Global Constraints

- **Current branch is `dev`, and the auth/invite layer is ASYNC** — `Auths`/`Users`/`Invites` model methods are `async def` and called with `await`; endpoints use `db: AsyncSession = Depends(get_async_session)`. All new model/endpoint code MUST be async.
- **`authenticate_user(email, verify_password_callable, db)`** takes a CALLABLE, not a password string. (The existing `test_auths.py` uses the old sync convention and is stale on this branch — do NOT mirror its calling style.)
- **No email enumeration:** `/password/forgot` MUST return the identical generic response for every input (unknown email, ineligible user, rate-limited, feature disabled, success). Never reveal whether an account exists.
- **Additive only:** new files + new branches/keys in existing files. Do not alter existing endpoints, the `Auth` SQLAlchemy model, or upstream auth logic. Mirror the invite feature's patterns.
- **Feature flag:** `ENABLE_FORGOT_PASSWORD` PersistentConfig, default `False`, env `ENABLE_FORGOT_PASSWORD`. Reuses existing `EMAIL_GRAPH_*` / `EMAIL_FROM_ADDRESS` config (no new email credentials).
- **i18n:** every new user-facing string MUST be added to BOTH `src/lib/i18n/locales/en-US/translation.json` (value = `""`, meaning "use the key") and `src/lib/i18n/locales/nl-NL/translation.json` (Dutch value). Keys are alphabetically sorted.
- **Alembic:** new migration `down_revision = 'a1c2e3f4d5b6'` (the current single head, file `a1c2e3f4d5b6_backfill_knowledge_file_path_columns.py`). The `revision` id MUST be globally unique — after creating the file, run `grep -rn "<newid>" backend/open_webui/migrations/versions/` and confirm exactly one match.
- **Backend formatter:** Black/Ruff, line-length 120, single quotes. Project venv: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python`. (`timeout` is unavailable on macOS.)
- **Token decisions (locked):** stateful DB table; SHA-256 hash at rest; single-use via `used_at`; only the newest outstanding token per user is valid; default TTL 30 min; a successful reset does NOT force-log-out other sessions (v1 scope).
- **Eligibility (locked):** email/password users with an active local `Auth` row. Trusted-header mode and `ENABLE_PASSWORD_AUTH=False` short-circuit (generic response, no send). OAuth users are not special-cased (if password login is enabled, setting a password is valid and helpful). Pure-LDAP users have no local `Auth` row and are naturally skipped.

---

## File Structure

**New backend files:**
- `backend/open_webui/models/password_reset.py` — `PasswordResetToken` model + `PasswordResetTokens` table class.
- `backend/open_webui/utils/password_reset.py` — pure helpers: token generation, hashing, usability check.
- `backend/open_webui/migrations/versions/<id>_create_password_reset_token_table.py` — Alembic migration.

**New backend tests:**
- `backend/open_webui/test/util/test_password_reset_helpers.py`
- `backend/open_webui/test/util/test_password_reset_model.py`
- `backend/open_webui/test/util/test_password_reset_email_render.py`

**Modified backend files:**
- `backend/open_webui/services/email/graph_mail_client.py` — add reset strings + `render_password_reset_subject` + `render_password_reset_email`.
- `backend/open_webui/config.py` — add `ENABLE_FORGOT_PASSWORD`, `PASSWORD_RESET_EXPIRY_MINUTES`.
- `backend/open_webui/main.py` — attach the two configs to `app.state.config`; add `enable_forgot_password` to the `/api/config` features dict.
- `backend/open_webui/routers/auths.py` — add forms, rate limiter, and the 3 endpoints.

**New frontend files:**
- `src/routes/auth/reset-password/[token]/+page.svelte` — set-new-password page.

**Modified frontend files:**
- `src/lib/apis/auths/index.ts` — `requestPasswordReset`, `validatePasswordResetToken`, `resetPassword`.
- `src/routes/auth/+page.svelte` — `forgot` mode + link + handler.
- `src/lib/i18n/locales/en-US/translation.json`, `src/lib/i18n/locales/nl-NL/translation.json` — new keys.

**Modified deploy files:**
- `helm/open-webui-tenant/values.yaml`, `helm/open-webui-tenant/templates/open-webui/configmap.yaml`.

---

## Task 1: PasswordResetToken model + Alembic migration

**Files:**
- Create: `backend/open_webui/models/password_reset.py`
- Create: `backend/open_webui/migrations/versions/<id>_create_password_reset_token_table.py`
- Test: `backend/open_webui/test/util/test_password_reset_model.py`

**Interfaces:**
- Produces:
  - `class PasswordResetToken(Base)` — table `password_reset_token`, columns `id, user_id, token_hash, expires_at, used_at, created_at`.
  - `class PasswordResetTokenModel(BaseModel)` — `id, user_id, token_hash, expires_at, used_at: Optional[int], created_at`.
  - `PasswordResetTokens` (singleton of `PasswordResetTokenTable`) with:
    - `async create(user_id: str, token_hash: str, expires_at: int, db=None) -> PasswordResetTokenModel`
    - `async get_by_token_hash(token_hash: str, db=None) -> Optional[PasswordResetTokenModel]`
    - `async mark_used(id: str, db=None) -> bool`
    - `async invalidate_unused_for_user(user_id: str, db=None) -> None`

- [x] **Step 1: Write the failing test**

Create `backend/open_webui/test/util/test_password_reset_model.py`. The fixture mirrors `test_skill_files_model.py` / `test_invites_model.py` (in-memory aiosqlite, `StaticPool`, monkeypatched `get_async_db_context`):

```python
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
    created = await PasswordResetTokens.create(
        user_id='user-1', token_hash='hash-abc', expires_at=now + 1800
    )
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
    created = await PasswordResetTokens.create(
        user_id='user-1', token_hash='hash-used', expires_at=now + 1800
    )
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
```

- [x] **Step 2: Run test to verify it fails**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -m pytest backend/open_webui/test/util/test_password_reset_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'open_webui.models.password_reset'`.

- [x] **Step 3: Create the model**

Create `backend/open_webui/models/password_reset.py`:

```python
import time
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import BigInteger, Column, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context


class PasswordResetToken(Base):
    __tablename__ = 'password_reset_token'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(BigInteger, nullable=False)
    used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class PasswordResetTokenModel(BaseModel):
    id: str
    user_id: str
    token_hash: str
    expires_at: int
    used_at: Optional[int] = None
    created_at: int

    model_config = {'from_attributes': True}


class PasswordResetTokenTable:
    async def create(
        self,
        user_id: str,
        token_hash: str,
        expires_at: int,
        db: Optional[AsyncSession] = None,
    ) -> PasswordResetTokenModel:
        async with get_async_db_context(db) as db:
            row = PasswordResetToken(
                id=str(uuid.uuid4()),
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                used_at=None,
                created_at=int(time.time()),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return PasswordResetTokenModel.model_validate(row)

    async def get_by_token_hash(
        self, token_hash: str, db: Optional[AsyncSession] = None
    ) -> Optional[PasswordResetTokenModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(PasswordResetToken).filter_by(token_hash=token_hash))
            row = result.scalars().first()
            return PasswordResetTokenModel.model_validate(row) if row else None

    async def mark_used(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(PasswordResetToken).filter_by(id=id).values(used_at=int(time.time()))
            )
            await db.commit()
            return result.rowcount == 1

    async def invalidate_unused_for_user(self, user_id: str, db: Optional[AsyncSession] = None) -> None:
        async with get_async_db_context(db) as db:
            await db.execute(
                update(PasswordResetToken)
                .filter(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
                .values(used_at=int(time.time()))
            )
            await db.commit()


PasswordResetTokens = PasswordResetTokenTable()
```

- [x] **Step 4: Run test to verify it passes**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -m pytest backend/open_webui/test/util/test_password_reset_model.py -v`
Expected: PASS (3 tests).

- [x] **Step 5: Create the Alembic migration**

Pick a fresh random revision id (example below uses `b7d4f9a1c3e2` — generate your own and confirm uniqueness). Create `backend/open_webui/migrations/versions/b7d4f9a1c3e2_create_password_reset_token_table.py`, mirroring `eaa33ce2752e_create_invite_table.py`:

```python
"""create password reset token table

Revision ID: b7d4f9a1c3e2
Revises: a1c2e3f4d5b6
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa

revision = 'b7d4f9a1c3e2'
down_revision = 'a1c2e3f4d5b6'
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade():
    if _table_exists('password_reset_token'):
        return
    op.create_table(
        'password_reset_token',
        sa.Column('id', sa.String(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False, unique=True),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('used_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_password_reset_token_user_id', 'password_reset_token', ['user_id'])
    op.create_index('ix_password_reset_token_token_hash', 'password_reset_token', ['token_hash'], unique=True)


def downgrade():
    op.drop_index('ix_password_reset_token_token_hash', 'password_reset_token')
    op.drop_index('ix_password_reset_token_user_id', 'password_reset_token')
    op.drop_table('password_reset_token')
```

- [x] **Step 6: Verify revision id uniqueness and head linkage**

Run: `grep -rn "b7d4f9a1c3e2" backend/open_webui/migrations/versions/`
Expected: exactly ONE match (the new file's `revision` line). If your generated id differs, grep for that.

Run: `grep -rn "down_revision" backend/open_webui/migrations/versions/ | grep "a1c2e3f4d5b6"`
Expected: exactly ONE match (your new file) — confirms you attached to the current head and didn't fork it.

- [x] **Step 7: Commit**

```bash
git add backend/open_webui/models/password_reset.py backend/open_webui/test/util/test_password_reset_model.py backend/open_webui/migrations/versions/b7d4f9a1c3e2_create_password_reset_token_table.py
git commit -m "feat(auth): add password_reset_token model + migration"
```

---

## Task 2: Token utility helpers

**Files:**
- Create: `backend/open_webui/utils/password_reset.py`
- Test: `backend/open_webui/test/util/test_password_reset_helpers.py`

**Interfaces:**
- Consumes: `PasswordResetTokenModel` (Task 1) — only its `used_at` / `expires_at` attributes, via duck typing.
- Produces:
  - `def generate_reset_token() -> tuple[str, str]` → `(raw_token, token_hash)`.
  - `def hash_reset_token(raw_token: str) -> str` → SHA-256 hex digest.
  - `def is_reset_token_usable(record, now: int) -> bool` → `record is not None and record.used_at is None and record.expires_at > now`.

- [x] **Step 1: Write the failing test**

Create `backend/open_webui/test/util/test_password_reset_helpers.py`:

```python
"""Unit tests for password-reset token helpers (pure functions)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from open_webui.utils.password_reset import (
    generate_reset_token,
    hash_reset_token,
    is_reset_token_usable,
)


def test_generate_reset_token_returns_raw_and_matching_hash():
    raw, token_hash = generate_reset_token()
    assert isinstance(raw, str) and len(raw) >= 32
    assert token_hash == hashlib.sha256(raw.encode()).hexdigest()
    # Two calls produce different tokens.
    raw2, _ = generate_reset_token()
    assert raw != raw2


def test_hash_reset_token_is_deterministic():
    assert hash_reset_token('abc') == hashlib.sha256(b'abc').hexdigest()


def test_is_reset_token_usable():
    now = 1000
    assert is_reset_token_usable(None, now) is False
    assert is_reset_token_usable(SimpleNamespace(used_at=None, expires_at=now + 1), now) is True
    # Expired.
    assert is_reset_token_usable(SimpleNamespace(used_at=None, expires_at=now - 1), now) is False
    # Already used.
    assert is_reset_token_usable(SimpleNamespace(used_at=now - 5, expires_at=now + 100), now) is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -m pytest backend/open_webui/test/util/test_password_reset_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'open_webui.utils.password_reset'`.

- [x] **Step 3: Create the helper module**

Create `backend/open_webui/utils/password_reset.py`:

```python
import hashlib
import secrets


def hash_reset_token(raw_token: str) -> str:
    """SHA-256 hex digest of a reset token. The token is high-entropy random,
    so a fast unsalted hash is appropriate (unlike user passwords)."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_reset_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). The raw token goes in the email URL;
    only the hash is persisted."""
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_reset_token(raw_token)


def is_reset_token_usable(record, now: int) -> bool:
    """A token is usable iff it exists, is unused, and is not expired."""
    return record is not None and record.used_at is None and record.expires_at > now
```

- [x] **Step 4: Run test to verify it passes**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -m pytest backend/open_webui/test/util/test_password_reset_helpers.py -v`
Expected: PASS (3 tests).

- [x] **Step 5: Commit**

```bash
git add backend/open_webui/utils/password_reset.py backend/open_webui/test/util/test_password_reset_helpers.py
git commit -m "feat(auth): add password-reset token helpers"
```

---

## Task 3: Reset email template (render functions + i18n strings)

**Files:**
- Modify: `backend/open_webui/services/email/graph_mail_client.py`
- Test: `backend/open_webui/test/util/test_password_reset_email_render.py`

**Interfaces:**
- Produces:
  - `def render_password_reset_subject(locale: str = 'en') -> str`
  - `def render_password_reset_email(reset_url: str, locale: str = 'en', expiry_minutes: int = 30) -> str`

- [x] **Step 1: Write the failing test**

Create `backend/open_webui/test/util/test_password_reset_email_render.py` (mirrors `test_invite_email_render.py`):

```python
"""Tests for the password-reset email renderer (EN + NL)."""

from __future__ import annotations

from open_webui.services.email.graph_mail_client import (
    render_password_reset_email,
    render_password_reset_subject,
)


def test_renders_english_reset_email():
    html = render_password_reset_email(
        reset_url='https://example.com/auth/reset-password/tok',
        locale='en',
        expiry_minutes=30,
    )
    assert 'https://example.com/auth/reset-password/tok' in html
    assert 'Reset password' in html
    assert '30 minutes' in html


def test_renders_dutch_reset_email():
    html = render_password_reset_email(
        reset_url='https://example.com/auth/reset-password/tok',
        locale='nl',
        expiry_minutes=30,
    )
    assert 'Wachtwoord resetten' in html
    assert '30 minuten' in html


def test_reset_subject_localised():
    assert 'Reset' in render_password_reset_subject(locale='en')
    assert 'Reset' in render_password_reset_subject(locale='nl')  # Dutch copy also contains "Reset je ..."
```

- [x] **Step 2: Run test to verify it fails**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -m pytest backend/open_webui/test/util/test_password_reset_email_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_password_reset_email'`.

- [x] **Step 3: Add strings + render functions**

In `backend/open_webui/services/email/graph_mail_client.py`, add immediately after the existing `_RETENTION_STRINGS` dict (or after `_STRINGS`; place near the other string dicts). `APP_NAME` and `APP_NAME_HTML` are existing module constants — reuse them:

```python
_PASSWORD_RESET_STRINGS = {
    'en': {
        'subject': f'Reset your {APP_NAME} password',
        'heading': f'Reset your {APP_NAME_HTML} password',
        'body': 'We received a request to reset your password. Click the button below to choose a new one.',
        'button': 'Reset password',
        'footer': "This link expires in {expiry_minutes} minutes. If you didn't request a password reset, you can safely ignore this email.",
    },
    'nl': {
        'subject': f'Reset je {APP_NAME}-wachtwoord',
        'heading': f'Reset je {APP_NAME_HTML}-wachtwoord',
        'body': 'We hebben een verzoek ontvangen om je wachtwoord opnieuw in te stellen. Klik op de onderstaande knop om een nieuw wachtwoord te kiezen.',
        'button': 'Wachtwoord resetten',
        'footer': 'Deze link verloopt over {expiry_minutes} minuten. Als je geen wachtwoordreset hebt aangevraagd, kun je deze e-mail veilig negeren.',
    },
}
```

Then add the two render functions (place near `render_invite_email`). The HTML mirrors `render_invite_email`'s 560px card exactly:

```python
def render_password_reset_subject(locale: str = 'en') -> str:
    strings = _PASSWORD_RESET_STRINGS.get(locale, _PASSWORD_RESET_STRINGS['en'])
    return strings['subject']


def render_password_reset_email(
    reset_url: str,
    locale: str = 'en',
    expiry_minutes: int = 30,
) -> str:
    strings = _PASSWORD_RESET_STRINGS.get(locale, _PASSWORD_RESET_STRINGS['en'])
    heading = strings['heading']
    body = strings['body']
    button = strings['button']
    footer = strings['footer'].format(expiry_minutes=expiry_minutes)

    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no, url=no">
<style type="text/css">
u + #body a {{
    color: inherit !important;
    text-decoration: none !important;
    font-size: inherit !important;
    font-weight: inherit !important;
}}
</style>
</head>
<body id="body">
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 560px; margin: 0 auto; padding: 40px 20px;">
    <h2 style="color: #1a1a1a; margin-bottom: 8px;">
        {heading}
    </h2>
    <p style="color: #4a4a4a; font-size: 16px; line-height: 1.5;">
        {body}
    </p>
    <a href="{reset_url}"
       style="display: inline-block; background: #0f172a; color: #ffffff;
              padding: 12px 24px; border-radius: 8px; text-decoration: none;
              font-weight: 500; margin: 24px 0;">
        <span style="color: #ffffff;">{button}</span>
    </a>
    <p style="color: #9a9a9a; font-size: 13px; margin-top: 32px;">
        {footer}
    </p>
</div>
</body>
</html>"""
```

- [x] **Step 4: Run test to verify it passes**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -m pytest backend/open_webui/test/util/test_password_reset_email_render.py -v`
Expected: PASS (3 tests).

- [x] **Step 5: Commit**

```bash
git add backend/open_webui/services/email/graph_mail_client.py backend/open_webui/test/util/test_password_reset_email_render.py
git commit -m "feat(auth): add password-reset email template (en/nl)"
```

---

## Task 4: Config + main.py wiring

**Files:**
- Modify: `backend/open_webui/config.py`
- Modify: `backend/open_webui/main.py`

**Interfaces:**
- Produces: `ENABLE_FORGOT_PASSWORD`, `PASSWORD_RESET_EXPIRY_MINUTES` (PersistentConfig), available as `app.state.config.ENABLE_FORGOT_PASSWORD` / `.PASSWORD_RESET_EXPIRY_MINUTES`, and `features.enable_forgot_password` in `/api/config`.

- [x] **Step 1: Add the PersistentConfig entries**

In `backend/open_webui/config.py`, in the `Email Service (Microsoft Graph API)` section (after the existing `EMAIL_INVITE_HEADING` block, ~line 3434), add:

```python
ENABLE_FORGOT_PASSWORD = PersistentConfig(
    'ENABLE_FORGOT_PASSWORD',
    'email.enable_forgot_password',
    os.environ.get('ENABLE_FORGOT_PASSWORD', 'False').lower() == 'true',
)

PASSWORD_RESET_EXPIRY_MINUTES = PersistentConfig(
    'PASSWORD_RESET_EXPIRY_MINUTES',
    'email.password_reset_expiry_minutes',
    int(os.environ.get('PASSWORD_RESET_EXPIRY_MINUTES', '30')),
)
```

- [x] **Step 2: Import the configs in main.py**

In `backend/open_webui/main.py`, in the config import block that already lists `EMAIL_INVITE_HEADING` (~line 421-426), add:

```python
    ENABLE_FORGOT_PASSWORD,
    PASSWORD_RESET_EXPIRY_MINUTES,
```

- [x] **Step 3: Attach to app.state.config**

In `backend/open_webui/main.py`, immediately after the existing `app.state.config.EMAIL_INVITE_HEADING = EMAIL_INVITE_HEADING` line (~line 1564), add:

```python
app.state.config.ENABLE_FORGOT_PASSWORD = ENABLE_FORGOT_PASSWORD
app.state.config.PASSWORD_RESET_EXPIRY_MINUTES = PASSWORD_RESET_EXPIRY_MINUTES
```

- [x] **Step 4: Expose the flag in the `/api/config` features dict**

In `backend/open_webui/main.py`, in the `'features': { ... }` dict (~line 3257), after the `'enable_login_form': app.state.config.ENABLE_LOGIN_FORM,` line, add:

```python
            'enable_forgot_password': app.state.config.ENABLE_FORGOT_PASSWORD,
```

- [x] **Step 5: Verify the app imports cleanly**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -c "import open_webui.main"`
Expected: no ImportError / NameError (exit code 0). A DB-connection warning is acceptable; a traceback mentioning `ENABLE_FORGOT_PASSWORD` is not.

- [x] **Step 6: Commit**

```bash
git add backend/open_webui/config.py backend/open_webui/main.py
git commit -m "feat(auth): wire ENABLE_FORGOT_PASSWORD config + features flag"
```

---

## Task 5: Backend endpoints (forgot / validate / reset)

**Files:**
- Modify: `backend/open_webui/routers/auths.py`

**Interfaces:**
- Consumes: `PasswordResetTokens` (Task 1), `generate_reset_token` / `hash_reset_token` / `is_reset_token_usable` (Task 2), `render_password_reset_email` / `render_password_reset_subject` / `send_mail` (Task 3), `app.state.config.ENABLE_FORGOT_PASSWORD` / `.PASSWORD_RESET_EXPIRY_MINUTES` (Task 4), existing `Users.get_user_by_email`, `Auths.get_auth_by_user_id`, `Auths.update_user_password_by_id`, `validate_password`, `get_password_hash`, `validate_email_format`, `ENABLE_PASSWORD_AUTH`, `WEBUI_AUTH_TRUSTED_EMAIL_HEADER`, `DEFAULT_LOCALE`, `RateLimiter`, `get_redis_client`.
- Produces (HTTP):
  - `POST /api/v1/auths/password/forgot` body `{email}` → `200 {"detail": "<generic>"}` always.
  - `GET /api/v1/auths/password/reset/{token}/validate` → `200 {"valid": bool}`.
  - `POST /api/v1/auths/password/reset` body `{token, new_password}` → `200 true` or `400`.

> **Testing note:** The router integration harness (`AbstractPostgresTest` in `test_auths.py`) is mid-async-migration on this branch and uses the stale sync calling convention, so we do NOT add automated endpoint tests here. The bug-prone logic (token lifecycle, hashing, usability, email copy) is already unit-tested in Tasks 1–3; these endpoints are thin glue over that tested logic and are covered by the manual verification steps below.

- [x] **Step 1: Add imports**

In `backend/open_webui/routers/auths.py`, extend the existing imports. Add to the `from open_webui.utils.auth import (...)` block: nothing new needed (already imports `validate_password`, `verify_password`, `get_password_hash`, `create_token`). Add these new imports near the top with the other `open_webui` imports:

```python
from open_webui.models.password_reset import PasswordResetTokens
from open_webui.utils.password_reset import (
    generate_reset_token,
    hash_reset_token,
    is_reset_token_usable,
)
from open_webui.config import DEFAULT_LOCALE
from open_webui.env import WEBUI_AUTH_TRUSTED_EMAIL_HEADER  # already imported — verify, do not duplicate
```

(Note: `WEBUI_AUTH_TRUSTED_EMAIL_HEADER`, `ENABLE_PASSWORD_AUTH`, `validate_email_format`, `RateLimiter`, `get_redis_client`, `Users`, `Auths`, `get_async_session`, `AsyncSession`, `time`, `datetime` are already imported in this file — do NOT re-import.)

- [x] **Step 2: Add forms, rate limiter, and constant**

In `backend/open_webui/routers/auths.py`, near the existing `signin_rate_limiter` definition (~line 98), add:

```python
password_reset_rate_limiter = RateLimiter(redis_client=get_redis_client(), limit=5, window=60 * 15)

GENERIC_PASSWORD_RESET_MESSAGE = 'If an account with that email exists, a password reset link has been sent.'


class ForgotPasswordForm(BaseModel):
    email: str


class ResetPasswordForm(BaseModel):
    token: str
    new_password: str
```

- [x] **Step 3: Add the `/password/forgot` endpoint**

Add to `backend/open_webui/routers/auths.py` (anywhere among the route handlers, e.g. right after `update_password`):

```python
@router.post('/password/forgot')
async def forgot_password(
    request: Request,
    form_data: ForgotPasswordForm,
    db: AsyncSession = Depends(get_async_session),
):
    """Self-service password reset request. Always returns the same generic
    response (no account enumeration). Sends a reset email only when the
    feature is enabled and the email maps to an active local password user."""
    email = form_data.email.lower().strip()
    try:
        eligible = (
            request.app.state.config.ENABLE_FORGOT_PASSWORD
            and ENABLE_PASSWORD_AUTH
            and not WEBUI_AUTH_TRUSTED_EMAIL_HEADER
            and validate_email_format(email)
            and not password_reset_rate_limiter.is_limited(email)
        )
        if eligible:
            user = await Users.get_user_by_email(email, db=db)
            if user:
                auth = await Auths.get_auth_by_user_id(user.id, db=db)
                if auth and auth.active:
                    raw_token, token_hash = generate_reset_token()
                    expiry_minutes = int(request.app.state.config.PASSWORD_RESET_EXPIRY_MINUTES)
                    expires_at = int(time.time()) + expiry_minutes * 60

                    # Only the newest link should work.
                    await PasswordResetTokens.invalidate_unused_for_user(user.id, db=db)
                    await PasswordResetTokens.create(
                        user_id=user.id, token_hash=token_hash, expires_at=expires_at, db=db
                    )

                    base_url = str(request.base_url).rstrip('/')
                    reset_url = f'{base_url}/auth/reset-password/{raw_token}'

                    from open_webui.services.email.graph_mail_client import (
                        render_password_reset_email,
                        render_password_reset_subject,
                        send_mail,
                    )

                    locale = str(DEFAULT_LOCALE) or 'en'
                    html_body = render_password_reset_email(
                        reset_url=reset_url, locale=locale, expiry_minutes=expiry_minutes
                    )
                    await send_mail(
                        app=request.app,
                        to_address=email,
                        subject=render_password_reset_subject(locale=locale),
                        html_body=html_body,
                    )
    except Exception as e:
        # Never reveal failure detail to the caller; log and return the generic message.
        log.error(f'Password reset request failed for {email}: {e}')

    return {'detail': GENERIC_PASSWORD_RESET_MESSAGE}
```

- [x] **Step 4: Add the validate + reset endpoints**

Add to `backend/open_webui/routers/auths.py`:

```python
@router.get('/password/reset/{token}/validate')
async def validate_password_reset_token(
    token: str,
    db: AsyncSession = Depends(get_async_session),
):
    record = await PasswordResetTokens.get_by_token_hash(hash_reset_token(token), db=db)
    return {'valid': is_reset_token_usable(record, int(time.time()))}


@router.post('/password/reset', response_model=bool)
async def reset_password(
    request: Request,
    form_data: ResetPasswordForm,
    db: AsyncSession = Depends(get_async_session),
):
    if not request.app.state.config.ENABLE_FORGOT_PASSWORD:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    record = await PasswordResetTokens.get_by_token_hash(hash_reset_token(form_data.token), db=db)
    if not is_reset_token_usable(record, int(time.time())):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='This password reset link is invalid or has expired.',
        )

    try:
        validate_password(form_data.new_password)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    hashed = get_password_hash(form_data.new_password)
    updated = await Auths.update_user_password_by_id(record.user_id, hashed, db=db)
    if not updated:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Could not update password. Please request a new reset link.',
        )

    await PasswordResetTokens.mark_used(record.id, db=db)
    return True
```

Note: confirm `ERROR_MESSAGES.NOT_FOUND` exists in `open_webui.constants`; if not, replace with the literal `detail='Not found'`.

- [x] **Step 5: Verify the app imports and routes register**

Run: `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -c "import open_webui.main"`
Expected: exit code 0, no traceback referencing `auths`.

- [x] **Step 6: Commit**

```bash
git add backend/open_webui/routers/auths.py
git commit -m "feat(auth): add forgot/validate/reset password endpoints"
```

- [ ] **Step 7: Manual verification (requires running backend + DB + `ENABLE_FORGOT_PASSWORD=true`)**

With `alembic upgrade head` applied and a known test user seeded:

```bash
# 1. Unknown email -> generic 200, no email sent
curl -s -X POST http://localhost:8080/api/v1/auths/password/forgot -H 'Content-Type: application/json' -d '{"email":"nobody@example.com"}'
# Expect: {"detail":"If an account with that email exists, a password reset link has been sent."}

# 2. Known email -> same generic 200; backend log shows send_mail call (or a logged send error if Graph not configured locally)
curl -s -X POST http://localhost:8080/api/v1/auths/password/forgot -H 'Content-Type: application/json' -d '{"email":"<known-user>"}'

# 3. Validate a bogus token -> {"valid": false}
curl -s http://localhost:8080/api/v1/auths/password/reset/bogus/validate

# 4. Reset with bogus token -> 400
curl -s -X POST http://localhost:8080/api/v1/auths/password/reset -H 'Content-Type: application/json' -d '{"token":"bogus","new_password":"NewPassw0rd!"}'
```
Then copy a real token from the `password_reset_token` table (or the logged reset URL), validate it (`{"valid": true}`), reset, confirm `200 true`, confirm the same token now returns `{"valid": false}`, and confirm signin works with the new password.

---

## Task 6: Frontend API client functions

**Files:**
- Modify: `src/lib/apis/auths/index.ts`

**Interfaces:**
- Produces:
  - `requestPasswordReset(email: string) => Promise<{detail: string}>` → `POST /auths/password/forgot`.
  - `validatePasswordResetToken(token: string) => Promise<{valid: boolean}>` → `GET /auths/password/reset/{token}/validate`.
  - `resetPassword(token: string, newPassword: string) => Promise<boolean>` → `POST /auths/password/reset`.

- [x] **Step 1: Add the three functions**

Append to `src/lib/apis/auths/index.ts`, mirroring the existing `userSignIn` / `updateUserPassword` fetch-wrapper pattern:

```typescript
export const requestPasswordReset = async (email: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/auths/password/forgot`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ email })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const validatePasswordResetToken = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/auths/password/reset/${token}/validate`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const resetPassword = async (token: string, newPassword: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/auths/password/reset`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ token, new_password: newPassword })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
```

- [x] **Step 2: Type-check**

Run: `npm run check 2>&1 | grep -i "apis/auths" || echo "no new auths errors"`
Expected: `no new auths errors` (the repo has ~8000 pre-existing svelte-check errors; only confirm you added none in this file).

- [x] **Step 3: Commit**

```bash
git add src/lib/apis/auths/index.ts
git commit -m "feat(auth): add password-reset API client functions"
```

---

## Task 7: Frontend auth page — `forgot` mode + link

**Files:**
- Modify: `src/routes/auth/+page.svelte`

**Interfaces:**
- Consumes: `requestPasswordReset` (Task 6), `$config?.features?.enable_forgot_password` + `enable_login_form` (Task 4).

- [x] **Step 1: Import the API function**

In the `<script>` of `src/routes/auth/+page.svelte`, add `requestPasswordReset` to the existing import from `$lib/apis/auths`:

```typescript
	import {
		ldapUserSignIn,
		getSessionUser,
		userSignIn,
		userSignUp,
		updateUserTimezone,
		verify2FA,
		requestPasswordReset
	} from '$lib/apis/auths';
```

- [x] **Step 2: Add the forgot handler**

In the same `<script>`, add (near `signInHandler`):

```typescript
	let forgotSubmitted = false;

	const forgotPasswordHandler = async () => {
		const res = await requestPasswordReset(email).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res) {
			forgotSubmitted = true;
			toast.success(
				$i18n.t('If an account with that email exists, a password reset link has been sent.')
			);
		}
	};
```

- [x] **Step 3: Route the submit handler**

In `submitHandler`, add the `forgot` branch:

```typescript
	const submitHandler = async () => {
		if (mode === 'ldap') {
			await ldapSignInHandler();
		} else if (mode === 'signin') {
			await signInHandler();
		} else if (mode === 'forgot') {
			await forgotPasswordHandler();
		} else {
			await signUpHandler();
		}
	};
```

- [x] **Step 4: Add the "Forgot password?" link under the sign-in form**

In the template, locate the sign-in mode-switch block (the `{#if $config?.features.enable_signup ...}` "Don't have an account?" block, ~line 457). Directly BEFORE it, add a forgot-password link shown only in signin mode when the feature is enabled:

```svelte
{#if mode === 'signin' && $config?.features?.enable_login_form && $config?.features?.enable_forgot_password}
	<div class="mt-2 text-sm text-center">
		<button
			class="font-medium underline"
			type="button"
			on:click={() => {
				forgotSubmitted = false;
				mode = 'forgot';
			}}
		>
			{$i18n.t('Forgot password?')}
		</button>
	</div>
{/if}
```

- [x] **Step 5: Render the forgot view (email entry + back link)**

The simplest additive approach: in the form body, when `mode === 'forgot'`, show only the email field (the password field block is already guarded per-mode in this file — gate it so it does not render in `forgot` mode), and add a heading + back-to-sign-in link. Add this block where the mode-specific heading/links render (mirror the existing signup/signin conditional copy):

```svelte
{#if mode === 'forgot'}
	<div class="mt-4 text-sm text-center">
		{#if forgotSubmitted}
			<p class="text-gray-500">
				{$i18n.t('If an account with that email exists, a password reset link has been sent.')}
			</p>
		{/if}
		<button
			class="font-medium underline mt-2"
			type="button"
			on:click={() => {
				mode = 'signin';
			}}
		>
			{$i18n.t('Back to sign in')}
		</button>
	</div>
{/if}
```

Also gate the password input so it is hidden in `forgot` mode. Find the password `<input>` block and ensure its wrapping condition excludes `forgot` (e.g. wrap with `{#if mode !== 'forgot'}`). The submit button label should read `{$i18n.t('Reset password')}` when `mode === 'forgot'` — update the button's label expression to include a `forgot` case.

> Implementer note: this file's exact markup for the password field and submit button must be read in-place; the change is "exclude the password field and relabel the submit button when `mode === 'forgot'`," keeping all existing signin/signup/ldap branches intact.

- [ ] **Step 6: Manual verification**

Run the frontend (`npm run dev`) against a backend with `ENABLE_FORGOT_PASSWORD=true`. On `/auth`:
- "Forgot password?" link appears under the sign-in form (and is ABSENT when the flag is off).
- Clicking it switches to email-only view with a "Reset password" button and "Back to sign in".
- Submitting shows the generic success message regardless of whether the email exists.

- [x] **Step 7: Commit**

```bash
git add src/routes/auth/+page.svelte
git commit -m "feat(auth): add forgot-password mode + link to auth page"
```

---

## Task 8: Frontend reset-password page (`/auth/reset-password/[token]`)

**Files:**
- Create: `src/routes/auth/reset-password/[token]/+page.svelte`

**Interfaces:**
- Consumes: `validatePasswordResetToken`, `resetPassword` (Task 6).

- [x] **Step 1: Create the page (mirrors `auth/invite/[token]/+page.svelte` state machine)**

Create `src/routes/auth/reset-password/[token]/+page.svelte`:

```svelte
<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { validatePasswordResetToken, resetPassword } from '$lib/apis/auths';
	import { WEBUI_NAME } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');

	const token = $page.params.token;

	let state: 'loading' | 'valid' | 'invalid' | 'done' = 'loading';
	let password = '';
	let confirmPassword = '';
	let submitting = false;

	onMount(async () => {
		const res = await validatePasswordResetToken(token).catch(() => null);
		state = res?.valid ? 'valid' : 'invalid';
	});

	const submitHandler = async () => {
		if (password !== confirmPassword) {
			toast.error($i18n.t('Passwords do not match.'));
			return;
		}
		submitting = true;
		const res = await resetPassword(token, password).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		submitting = false;
		if (res) {
			state = 'done';
			toast.success($i18n.t('Your password has been reset. You can now sign in.'));
			setTimeout(() => goto('/auth'), 1500);
		}
	};
</script>

<div class="w-full h-screen flex items-center justify-center">
	<div class="w-full max-w-sm px-6">
		{#if state === 'loading'}
			<div class="flex justify-center"><Spinner /></div>
		{:else if state === 'invalid'}
			<h2 class="text-lg font-medium mb-2">{$i18n.t('Reset link invalid or expired')}</h2>
			<p class="text-sm text-gray-500 mb-4">
				{$i18n.t('This password reset link is invalid or has expired. Please request a new one.')}
			</p>
			<button class="font-medium underline text-sm" type="button" on:click={() => goto('/auth')}>
				{$i18n.t('Back to sign in')}
			</button>
		{:else if state === 'done'}
			<p class="text-sm text-gray-500">
				{$i18n.t('Your password has been reset. You can now sign in.')}
			</p>
		{:else}
			<h2 class="text-lg font-medium mb-4">{$i18n.t('Reset password')}</h2>
			<form on:submit|preventDefault={submitHandler} class="flex flex-col gap-3">
				<div>
					<label class="text-sm mb-1 block" for="new-password">{$i18n.t('New password')}</label>
					<SensitiveInput id="new-password" bind:value={password} required />
				</div>
				<div>
					<label class="text-sm mb-1 block" for="confirm-password"
						>{$i18n.t('Confirm password')}</label
					>
					<SensitiveInput id="confirm-password" bind:value={confirmPassword} required />
				</div>
				<button
					type="submit"
					disabled={submitting}
					class="bg-gray-900 text-white rounded-lg px-4 py-2 mt-2 disabled:opacity-50"
				>
					{$i18n.t('Reset password')}
				</button>
			</form>
		{/if}
	</div>
</div>
```

> Implementer note: confirm `SensitiveInput`'s prop API matches usage in `auth/+page.svelte` (it is imported there). If it does not accept `id`/`required` passthrough, fall back to a plain `<input type="password" bind:value={...} />` to keep the task unblocked.

- [x] **Step 2: Type-check**

Run: `npm run check 2>&1 | grep -i "reset-password" || echo "no new reset-password errors"`
Expected: `no new reset-password errors`.

- [ ] **Step 3: Manual verification**

With a valid token (from the email or `password_reset_token` table): visiting `/auth/reset-password/<token>` shows the new-password form; mismatched passwords show an error; a successful reset shows the success message and redirects to `/auth`; reusing the same link afterward shows "invalid or expired".

- [x] **Step 4: Commit**

```bash
git add src/routes/auth/reset-password/[token]/+page.svelte
git commit -m "feat(auth): add reset-password token page"
```

---

## Task 9: i18n keys (en-US + nl-NL)

**Files:**
- Modify: `src/lib/i18n/locales/en-US/translation.json`
- Modify: `src/lib/i18n/locales/nl-NL/translation.json`

- [x] **Step 1: Add keys to en-US (value = empty string, alphabetically placed)**

Add these keys to `src/lib/i18n/locales/en-US/translation.json` in their correct alphabetical positions (value `""`):

```json
"Back to sign in": "",
"Confirm password": "",
"Forgot password?": "",
"If an account with that email exists, a password reset link has been sent.": "",
"New password": "",
"Passwords do not match.": "",
"Reset link invalid or expired": "",
"Reset password": "",
"This password reset link is invalid or has expired. Please request a new one.": "",
"Your password has been reset. You can now sign in.": ""
```

- [x] **Step 2: Add the same keys to nl-NL with Dutch values (alphabetically placed)**

Add to `src/lib/i18n/locales/nl-NL/translation.json`:

```json
"Back to sign in": "Terug naar inloggen",
"Confirm password": "Bevestig wachtwoord",
"Forgot password?": "Wachtwoord vergeten?",
"If an account with that email exists, a password reset link has been sent.": "Als er een account met dat e-mailadres bestaat, is er een resetlink verstuurd.",
"New password": "Nieuw wachtwoord",
"Passwords do not match.": "Wachtwoorden komen niet overeen.",
"Reset link invalid or expired": "Resetlink ongeldig of verlopen",
"Reset password": "Wachtwoord resetten",
"This password reset link is invalid or has expired. Please request a new one.": "Deze wachtwoord-resetlink is ongeldig of verlopen. Vraag een nieuwe aan.",
"Your password has been reset. You can now sign in.": "Je wachtwoord is opnieuw ingesteld. Je kunt nu inloggen."
```

> Some keys may already exist (e.g. `"Reset password"`). If a key is already present, do NOT duplicate it — JSON objects must have unique keys. Grep first: `grep -n '"Reset password"' src/lib/i18n/locales/en-US/translation.json`.

- [x] **Step 3: Validate JSON**

Run: `node -e "JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/en-US/translation.json')); JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/nl-NL/translation.json')); console.log('valid')"`
Expected: `valid` (no duplicate-key or syntax errors).

- [x] **Step 4: Commit**

```bash
git add src/lib/i18n/locales/en-US/translation.json src/lib/i18n/locales/nl-NL/translation.json
git commit -m "feat(auth): add forgot-password i18n (en/nl)"
```

---

## Task 10: Helm wiring

**Files:**
- Modify: `helm/open-webui-tenant/values.yaml`
- Modify: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

**Interfaces:**
- Produces: `ENABLE_FORGOT_PASSWORD` and `PASSWORD_RESET_EXPIRY_MINUTES` env vars in the tenant deployment, defaulting off. No new secret (reuses `EMAIL_GRAPH_*`).

- [x] **Step 1: Add values**

In `helm/open-webui-tenant/values.yaml`, in the `openWebui.config` block alongside the email-invite settings (near `emailInviteHeading`), add:

```yaml
    # Forgot Password (self-service password reset; reuses Email Graph config)
    enableForgotPassword: "false"
    passwordResetExpiryMinutes: "30"
```

- [x] **Step 2: Wire env vars in the configmap**

In `helm/open-webui-tenant/templates/open-webui/configmap.yaml`, in the `# Email Invites (Microsoft Graph API)` section (after `EMAIL_INVITE_HEADING`), add:

```yaml
  # Forgot Password (self-service reset)
  ENABLE_FORGOT_PASSWORD: {{ .Values.openWebui.config.enableForgotPassword | quote }}
  PASSWORD_RESET_EXPIRY_MINUTES: {{ .Values.openWebui.config.passwordResetExpiryMinutes | quote }}
```

- [x] **Step 3: Lint the chart**

Run: `helm lint helm/open-webui-tenant` (or `helm template helm/open-webui-tenant | grep -A1 ENABLE_FORGOT_PASSWORD`)
Expected: no lint errors; the rendered configmap contains `ENABLE_FORGOT_PASSWORD: "false"`.

- [x] **Step 4: Commit**

```bash
git add helm/open-webui-tenant/values.yaml helm/open-webui-tenant/templates/open-webui/configmap.yaml
git commit -m "feat(auth): Helm wiring for ENABLE_FORGOT_PASSWORD"
```

---

## Final Verification (whole feature)

- [x] Run the full new-test suite:
  `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/forgot-password-email-users/.venv/bin/python -m pytest backend/open_webui/test/util/test_password_reset_helpers.py backend/open_webui/test/util/test_password_reset_model.py backend/open_webui/test/util/test_password_reset_email_render.py -v`
  Expected: all PASS.
- [x] `alembic upgrade head` succeeds on a scratch DB and creates `password_reset_token`.
- [ ] End-to-end (backend + frontend, `ENABLE_FORGOT_PASSWORD=true`, Graph mail configured): request reset → receive email → click link → set new password → sign in with new password → old link no longer works.
- [ ] With `ENABLE_FORGOT_PASSWORD=false`: the "Forgot password?" link is absent and `/password/forgot` still returns the generic 200 (no email sent), `/password/reset` returns 404.

## Out of Scope (v1)

- Forcing logout of the user's other active sessions on reset (decided: no built-in revoke-all primitive; user is typically already logged out everywhere).
- Disabling/resetting 2FA — a successful reset changes only the password; TOTP is still required at next login. Lost-password-AND-2FA remains an admin recovery task.
- Automated endpoint integration tests — deferred until the router test harness (`AbstractPostgresTest`) finishes its async migration. Logic is unit-tested; endpoints are manually verified.
