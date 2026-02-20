# Email Invite Links Implementation Plan

## Overview

Add an invite system for admin-created users. Instead of requiring admins to set a password and share it out-of-band, admins get three creation modes: **send email invite** (via Microsoft Graph API), **copy invite link**, and **set password** (existing flow). The invite acceptance page lets new users set their own password.

**Research document**: `thoughts/shared/research/2026-02-16-email-invite-links.md`

## Current State Analysis

- Admin user creation: `POST /auths/add` requires `password` (mandatory on `SignupForm`)
- `AddUserModal.svelte` has two tabs: form + CSV import
- No invite table, model, or routes exist
- Microsoft Graph API infrastructure exists for OneDrive but email will use separate credentials
- Auth guard in root layout only exempts exact path `/auth` — needs broadening

### Key Discoveries:
- `AddUserForm` extends `SignupForm` which has `password: str` as required — `backend/open_webui/models/auths.py:70-78`
- `Auths.insert_new_auth()` receives pre-hashed password — `backend/open_webui/models/auths.py:82-112`
- Config pattern: `PersistentConfig` → `app.state.config` → `/api/config` features — `backend/open_webui/config.py:165-221`
- Admin config endpoints: GET/POST pairs in `routers/configs.py` with Pydantic models
- Admin settings tabs: Array in `src/lib/utils/features.ts:87-101`, rendered in `Settings.svelte`
- Root layout auth guard: `src/routes/+layout.svelte:775` — `$page.url.pathname !== '/auth'`
- Current Alembic head: `2c5f92a9fd66` (add_knowledge_type_column)

## Desired End State

Admins can create users via three modes in the Add User modal:
1. **Send Email Invite**: Creates invite record, sends email via Graph API with accept link
2. **Copy Invite Link**: Creates invite record, shows copyable URL
3. **Set Password**: Existing flow, unchanged

Invited users visit `/auth/invite/[token]`, set a password, and get logged in. Admins can view pending invites, resend emails, and revoke invites. An admin settings "Email" tab configures Graph API credentials.

### How to verify:
1. With `ENABLE_EMAIL_INVITES=true` and Graph API configured, admin can create user via email invite → user receives email → clicks link → sets password → logged in
2. Admin can create user via "Copy Link" → copies URL → shares it → user sets password → logged in
3. With `ENABLE_EMAIL_INVITES=false`, modal shows only "Set Password" mode (unchanged behavior)
4. Admin can view, resend, and revoke pending invites
5. Email settings tab allows configuring Graph API credentials

## What We're NOT Doing

- SMTP/generic email provider support — Graph API only for now
- Self-service password reset (separate feature)
- Invite link single-use enforcement via IP/device fingerprinting
- Bulk invite rate limiting beyond simple `asyncio.sleep(2)` spacing
- Email templates with branding/logo customization UI

## Implementation Approach

Two phases, each independently deployable. Phase 1 delivers the core invite flow. Phase 2 adds admin management UI. All changes are feature-gated behind `ENABLE_EMAIL_INVITES` (default `False`).

New code goes in new files to minimize upstream merge conflicts. The invites router is always mounted (not gated) so "Copy Link" works without Graph API setup.

---

## Phase 1: Core Invite System (MVP)

### Overview
Database model, email service, API endpoints, redesigned AddUserModal with three creation modes, and invite acceptance page.

### Changes Required:

#### 1. Database: Invite Table + Alembic Migration

**New file**: `backend/open_webui/models/invites.py`

```python
import time
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import BigInteger, Column, String, Text

from open_webui.internal.db import Base, get_db


class Invite(Base):
    __tablename__ = "invite"

    id = Column(String, primary_key=True)
    email = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    role = Column(String, default="user")
    invited_by = Column(String, nullable=False)  # admin user ID
    expires_at = Column(BigInteger, nullable=False)
    accepted_at = Column(BigInteger, nullable=True)  # null = pending
    revoked_at = Column(BigInteger, nullable=True)  # null = not revoked
    created_at = Column(BigInteger, nullable=False)


class InviteModel(BaseModel):
    id: str
    email: str
    name: str
    token: str
    role: str
    invited_by: str
    expires_at: int
    accepted_at: Optional[int] = None
    revoked_at: Optional[int] = None
    created_at: int

    model_config = {"from_attributes": True}


class InviteForm(BaseModel):
    name: str
    email: str
    role: Optional[str] = "user"
    send_email: Optional[bool] = True


class AcceptInviteForm(BaseModel):
    password: str
    name: Optional[str] = None  # allow override of pre-filled name


class Invites:
    def create_invite(
        self,
        email: str,
        name: str,
        role: str,
        invited_by: str,
        expires_at: int,
    ) -> Optional[InviteModel]:
        with get_db() as db:
            invite = Invite(
                id=str(uuid.uuid4()),
                email=email.lower(),
                name=name,
                token=str(uuid.uuid4()),
                role=role,
                invited_by=invited_by,
                expires_at=expires_at,
                created_at=int(time.time()),
            )
            db.add(invite)
            db.commit()
            db.refresh(invite)
            return InviteModel.model_validate(invite)

    def get_invite_by_token(self, token: str) -> Optional[InviteModel]:
        with get_db() as db:
            invite = db.query(Invite).filter_by(token=token).first()
            return InviteModel.model_validate(invite) if invite else None

    def get_invite_by_id(self, id: str) -> Optional[InviteModel]:
        with get_db() as db:
            invite = db.query(Invite).filter_by(id=id).first()
            return InviteModel.model_validate(invite) if invite else None

    def get_pending_invites(self) -> list[InviteModel]:
        with get_db() as db:
            invites = (
                db.query(Invite)
                .filter(Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))
                .order_by(Invite.created_at.desc())
                .all()
            )
            return [InviteModel.model_validate(i) for i in invites]

    def get_invite_by_email(self, email: str) -> Optional[InviteModel]:
        """Get the most recent pending invite for an email."""
        with get_db() as db:
            invite = (
                db.query(Invite)
                .filter(
                    Invite.email == email.lower(),
                    Invite.accepted_at.is_(None),
                    Invite.revoked_at.is_(None),
                )
                .order_by(Invite.created_at.desc())
                .first()
            )
            return InviteModel.model_validate(invite) if invite else None

    def accept_invite(self, token: str) -> Optional[InviteModel]:
        with get_db() as db:
            invite = db.query(Invite).filter_by(token=token).first()
            if invite:
                invite.accepted_at = int(time.time())
                db.commit()
                db.refresh(invite)
                return InviteModel.model_validate(invite)
            return None

    def revoke_invite(self, id: str) -> Optional[InviteModel]:
        with get_db() as db:
            invite = db.query(Invite).filter_by(id=id).first()
            if invite:
                invite.revoked_at = int(time.time())
                db.commit()
                db.refresh(invite)
                return InviteModel.model_validate(invite)
            return None


Invites = Invites()
```

**New file**: `backend/open_webui/migrations/versions/xxxx_create_invite_table.py`

Use `op.create_table("invite", ...)` pattern from `9f0c9cd09105_add_note_table.py`. Down revision: `2c5f92a9fd66`.

```python
def upgrade():
    op.create_table(
        "invite",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("role", sa.String(), server_default="user"),
        sa.Column("invited_by", sa.String(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_invite_email", "invite", ["email"])
    op.create_index("ix_invite_token", "invite", ["token"], unique=True)

def downgrade():
    op.drop_index("ix_invite_token", "invite")
    op.drop_index("ix_invite_email", "invite")
    op.drop_table("invite")
```

#### 2. Email Service: Graph API Client

**New file**: `backend/open_webui/services/email/__init__.py`
Empty.

**New file**: `backend/open_webui/services/email/auth.py`

Client credentials token acquisition with in-memory caching:

```python
import time
import httpx

_token_cache = {"access_token": None, "expires_at": 0}


async def get_mail_access_token(app) -> str:
    """Get Graph API token using client_credentials flow."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 300:
        return _token_cache["access_token"]

    tenant_id = app.state.config.EMAIL_GRAPH_TENANT_ID
    client_id = app.state.config.EMAIL_GRAPH_CLIENT_ID
    client_secret = app.state.config.EMAIL_GRAPH_CLIENT_SECRET

    if not all([tenant_id, client_id, client_secret]):
        raise ValueError("Email Graph API credentials not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "scope": "https://graph.microsoft.com/.default",
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data["expires_in"]
    return data["access_token"]
```

**New file**: `backend/open_webui/services/email/graph_mail_client.py`

```python
import httpx
from open_webui.services.email.auth import get_mail_access_token

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


async def send_mail(app, to_address: str, subject: str, html_body: str) -> bool:
    """Send email via Microsoft Graph API. Returns True on success."""
    token = await get_mail_access_token(app)
    from_address = app.state.config.EMAIL_FROM_ADDRESS

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": False,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{GRAPH_BASE_URL}/users/{from_address}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            raise Exception(f"Rate limited. Retry after {retry_after}s")

        resp.raise_for_status()
        return True


def render_invite_email(
    invite_url: str, invited_by_name: str, app_name: str = "Soev"
) -> str:
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                max-width: 560px; margin: 0 auto; padding: 40px 20px;">
        <h2 style="color: #1a1a1a; margin-bottom: 8px;">
            You've been invited to {app_name}
        </h2>
        <p style="color: #4a4a4a; font-size: 16px; line-height: 1.5;">
            {invited_by_name} has invited you to join. Click the button below
            to create your account.
        </p>
        <a href="{invite_url}"
           style="display: inline-block; background: #0f172a; color: #ffffff;
                  padding: 12px 24px; border-radius: 8px; text-decoration: none;
                  font-weight: 500; margin: 24px 0;">
            Accept Invite
        </a>
        <p style="color: #9a9a9a; font-size: 13px; margin-top: 32px;">
            This invite expires in 7 days. If you didn't expect this email,
            you can safely ignore it.
        </p>
    </div>
    """
```

#### 3. Backend Config Variables

**File**: `backend/open_webui/config.py` (append after OneDrive section, ~line 2553)

```python
# Email Service (Microsoft Graph API)
ENABLE_EMAIL_INVITES = PersistentConfig(
    "ENABLE_EMAIL_INVITES",
    "email.enable_invites",
    os.environ.get("ENABLE_EMAIL_INVITES", "False").lower() == "true",
)

EMAIL_GRAPH_TENANT_ID = PersistentConfig(
    "EMAIL_GRAPH_TENANT_ID",
    "email.graph_tenant_id",
    os.environ.get("EMAIL_GRAPH_TENANT_ID", ""),
)

EMAIL_GRAPH_CLIENT_ID = PersistentConfig(
    "EMAIL_GRAPH_CLIENT_ID",
    "email.graph_client_id",
    os.environ.get("EMAIL_GRAPH_CLIENT_ID", ""),
)

EMAIL_GRAPH_CLIENT_SECRET = PersistentConfig(
    "EMAIL_GRAPH_CLIENT_SECRET",
    "email.graph_client_secret",
    os.environ.get("EMAIL_GRAPH_CLIENT_SECRET", ""),
)

EMAIL_FROM_ADDRESS = PersistentConfig(
    "EMAIL_FROM_ADDRESS",
    "email.from_address",
    os.environ.get("EMAIL_FROM_ADDRESS", "no-reply@soev.ai"),
)

EMAIL_FROM_NAME = PersistentConfig(
    "EMAIL_FROM_NAME",
    "email.from_name",
    os.environ.get("EMAIL_FROM_NAME", "Soev"),
)

INVITE_EXPIRY_HOURS = PersistentConfig(
    "INVITE_EXPIRY_HOURS",
    "email.invite_expiry_hours",
    int(os.environ.get("INVITE_EXPIRY_HOURS", "168")),
)
```

#### 4. Backend: Invites Router

**New file**: `backend/open_webui/routers/invites.py`

Key endpoints:
- `POST /` — Create invite (admin only). Validates email, creates invite record, optionally sends email.
- `GET /{token}/validate` — Validate invite token (public, no auth). Returns invite details if valid.
- `POST /{token}/accept` — Accept invite (public, no auth). Validates password, creates user, returns session.
- `GET /` — List pending invites (admin only). Phase 2 but define stub now.
- `POST /{id}/resend` — Resend invite email (admin only). Phase 2 but define stub now.
- `DELETE /{id}` — Revoke invite (admin only). Phase 2 but define stub now.

The create endpoint:
1. Validates email format, checks not already registered
2. Creates invite record with UUID token and expiry
3. If `send_email=true` and `ENABLE_EMAIL_INVITES` is true, sends email via Graph API
4. Returns invite details including `invite_url`

The accept endpoint:
1. Validates token (not expired, not accepted, not revoked)
2. Validates and hashes password
3. Creates Auth + User via `Auths.insert_new_auth()`
4. Applies default group assignment
5. Marks invite as accepted
6. Returns `SigninResponse` (user is logged in)

The validate and accept endpoints do NOT require authentication — they use `get_current_user_optional` or no auth dependency, since the invited user doesn't have an account yet.

#### 5. Wire Up in `main.py`

**File**: `backend/open_webui/main.py`

**Config assignment** (near line 1052, after OneDrive config assignments):
```python
app.state.config.ENABLE_EMAIL_INVITES = ENABLE_EMAIL_INVITES
app.state.config.EMAIL_GRAPH_TENANT_ID = EMAIL_GRAPH_TENANT_ID
app.state.config.EMAIL_GRAPH_CLIENT_ID = EMAIL_GRAPH_CLIENT_ID
app.state.config.EMAIL_GRAPH_CLIENT_SECRET = EMAIL_GRAPH_CLIENT_SECRET
app.state.config.EMAIL_FROM_ADDRESS = EMAIL_FROM_ADDRESS
app.state.config.EMAIL_FROM_NAME = EMAIL_FROM_NAME
app.state.config.INVITE_EXPIRY_HOURS = INVITE_EXPIRY_HOURS
```

**Router mount** (near line 1533, after OneDrive mount):
```python
# Invites API (always mounted - Copy Link works without Graph API)
app.include_router(invites.router, prefix="/api/v1/invites", tags=["invites"])
```

**Feature flag in `/api/config` response** (near line 2068, after OneDrive flags):
```python
"enable_email_invites": app.state.config.ENABLE_EMAIL_INVITES,
```

#### 6. Frontend: API Client

**New file**: `src/lib/apis/invites/index.ts`

```typescript
import { WEBUI_API_BASE_URL } from '$lib/constants';

export const createInvite = async (
    token: string,
    name: string,
    email: string,
    role: string,
    sendEmail: boolean
) => { /* POST /api/v1/invites */ };

export const validateInvite = async (inviteToken: string) => {
    /* GET /api/v1/invites/{token}/validate — no auth token */
};

export const acceptInvite = async (
    inviteToken: string,
    password: string,
    name?: string
) => { /* POST /api/v1/invites/{token}/accept — no auth token */ };

export const listInvites = async (token: string) => {
    /* GET /api/v1/invites */
};

export const resendInvite = async (token: string, inviteId: string) => {
    /* POST /api/v1/invites/{id}/resend */
};

export const revokeInvite = async (token: string, inviteId: string) => {
    /* DELETE /api/v1/invites/{id} */
};
```

Follow the pattern from `src/lib/apis/auths/index.ts` for error handling and response parsing.

#### 7. Frontend: Root Layout Auth Guard Fix

**File**: `src/routes/+layout.svelte:775`

Change:
```js
if ($page.url.pathname !== '/auth') {
```
To:
```js
if (!$page.url.pathname.startsWith('/auth')) {
```

This allows `/auth/invite/[token]` to load without being redirected to the login page.

#### 8. Frontend: Invite Acceptance Page

**New file**: `src/routes/auth/invite/[token]/+page.svelte`

States:
- **Loading**: Calling `validateInvite(token)` on mount
- **Valid**: Shows form with pre-filled name (editable) + email (readonly) + password + confirm password
- **Expired**: "This invite has expired. Please contact your administrator."
- **Already accepted**: "This invite has already been used." + link to sign in
- **Invalid**: "Invalid invite link."

On submit: `acceptInvite(token, password, name)` → store token in `localStorage.token` → redirect to `/`.

This page has its own self-contained UI (logo, centered card) — no nav/sidebar. It sits under the root layout but outside `(app)`, so it only gets the basic shell.

#### 9. Frontend: Modified AddUserModal

**File**: `src/lib/components/admin/Users/UserList/AddUserModal.svelte`

Strategy: Keep the upstream file diff minimal. Extract the invite-specific UI into a child component and conditionally render it.

**New file**: `src/lib/components/admin/Users/UserList/InviteUserForm.svelte`

Contains:
- Creation mode selector (three buttons: Send Email, Copy Link, Set Password)
- Form fields that adapt per mode (password field shown only for "Set Password")
- Submit handler that calls either `createInvite()` or `addUser()` based on mode
- Copy link dialog that appears after successful "Copy Link" creation

**Modified**: `AddUserModal.svelte`

Minimal diff — add conditional rendering:
```svelte
{#if $config?.features?.enable_email_invites}
    <InviteUserForm
        on:save
        on:close={() => (show = false)}
        bind:loading
    />
{:else}
    <!-- existing form code unchanged -->
{/if}
```

The mode selector UI:
```
┌─────────────┐ ┌──────────────┐ ┌────────────┐
│ Send Email   │ │ Copy Link    │ │ Set        │
│ Invite       │ │              │ │ Password   │
└─────────────┘ └──────────────┘ └────────────┘
```

- "Send Email" disabled with tooltip if Graph API not configured
- "Copy Link" always available when invites enabled
- "Set Password" = existing flow

#### 10. i18n Keys

Add `$i18n.t(...)` calls in new components, then run `npm run i18n:parse` to propagate. Key strings:
- "Accept Invite", "Copy Invite Link", "Create Invite", "Send Email Invite", "Set Password"
- "How should this user be created?"
- "Email invite sent to {{email}}", "Invite link copied to clipboard"
- "This invite has expired. Please contact your administrator."
- "This invite has already been used."
- "You've been invited by {{name}}", "You've been invited to join"
- "Enter the password for your new account", "Confirm Password"
- "Share this link with the user:", "This link expires in {{hours}} hours."
- "Invalid invite link", "Invite Created"

### Success Criteria:

#### Automated Verification:
- [ ] Alembic migration applies cleanly: `cd backend && alembic upgrade head`
- [ ] Backend starts without errors: `open-webui dev` (check no import errors)
- [ ] Frontend builds: `npm run build`
- [ ] `POST /api/v1/invites` with admin token returns invite record
- [ ] `GET /api/v1/invites/{token}/validate` returns invite details (no auth)
- [ ] `POST /api/v1/invites/{token}/accept` creates user and returns session
- [ ] `/auth/invite/[token]` route loads without auth redirect

#### Manual Verification:
- [ ] Admin creates user via "Set Password" mode — works identically to current flow
- [ ] Admin creates user via "Copy Link" — gets copyable URL, invite appears in DB
- [ ] Visiting copied invite link shows acceptance form with pre-filled name/email
- [ ] Setting password on acceptance form creates account and logs user in
- [ ] Visiting expired invite shows expiry message
- [ ] Visiting already-accepted invite shows "already used" message
- [ ] With `ENABLE_EMAIL_INVITES=false`, modal shows only "Set Password" (unchanged behavior)
- [ ] Admin creates user via "Send Email" — email arrives (requires Graph API config)

**Implementation Note**: After completing Phase 1 and all automated verification passes, pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Admin Management

### Overview
Email settings tab, pending invites list, resend/revoke functionality, test email endpoint, and CSV invite import.

### Changes Required:

#### 1. Backend Config Endpoints for Email

**File**: `backend/open_webui/routers/configs.py` (append)

```python
class EmailConfigForm(BaseModel):
    ENABLE_EMAIL_INVITES: bool
    EMAIL_GRAPH_TENANT_ID: str
    EMAIL_GRAPH_CLIENT_ID: str
    EMAIL_GRAPH_CLIENT_SECRET: str
    EMAIL_FROM_ADDRESS: str
    EMAIL_FROM_NAME: str
    INVITE_EXPIRY_HOURS: int

@router.get("/email", response_model=EmailConfigForm)
async def get_email_config(request: Request, user=Depends(get_admin_user)):
    return EmailConfigForm(
        ENABLE_EMAIL_INVITES=request.app.state.config.ENABLE_EMAIL_INVITES,
        EMAIL_GRAPH_TENANT_ID=request.app.state.config.EMAIL_GRAPH_TENANT_ID,
        EMAIL_GRAPH_CLIENT_ID=request.app.state.config.EMAIL_GRAPH_CLIENT_ID,
        EMAIL_GRAPH_CLIENT_SECRET=request.app.state.config.EMAIL_GRAPH_CLIENT_SECRET,
        EMAIL_FROM_ADDRESS=request.app.state.config.EMAIL_FROM_ADDRESS,
        EMAIL_FROM_NAME=request.app.state.config.EMAIL_FROM_NAME,
        INVITE_EXPIRY_HOURS=request.app.state.config.INVITE_EXPIRY_HOURS,
    )

@router.post("/email", response_model=EmailConfigForm)
async def set_email_config(
    request: Request, form_data: EmailConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.ENABLE_EMAIL_INVITES = form_data.ENABLE_EMAIL_INVITES
    request.app.state.config.EMAIL_GRAPH_TENANT_ID = form_data.EMAIL_GRAPH_TENANT_ID
    request.app.state.config.EMAIL_GRAPH_CLIENT_ID = form_data.EMAIL_GRAPH_CLIENT_ID
    request.app.state.config.EMAIL_GRAPH_CLIENT_SECRET = form_data.EMAIL_GRAPH_CLIENT_SECRET
    request.app.state.config.EMAIL_FROM_ADDRESS = form_data.EMAIL_FROM_ADDRESS
    request.app.state.config.EMAIL_FROM_NAME = form_data.EMAIL_FROM_NAME
    request.app.state.config.INVITE_EXPIRY_HOURS = form_data.INVITE_EXPIRY_HOURS
    return form_data
```

#### 2. Test Email Endpoint

**File**: `backend/open_webui/routers/configs.py` (append)

```python
@router.post("/email/test")
async def test_email_config(request: Request, user=Depends(get_admin_user)):
    """Send a test email to the admin's own address."""
    from open_webui.services.email.graph_mail_client import send_mail
    await send_mail(
        request.app,
        to_address=user.email,
        subject="Test email from Soev",
        html_body="<p>This is a test email. Your email configuration is working.</p>",
    )
    return {"status": "ok", "message": f"Test email sent to {user.email}"}
```

#### 3. Complete Invites Router Endpoints

**File**: `backend/open_webui/routers/invites.py` (complete the stubs from Phase 1)

- `GET /` — List pending invites with invited_by user name resolved
- `POST /{id}/resend` — Re-send invite email (update token for fresh expiry, send new email)
- `DELETE /{id}` — Revoke invite (set revoked_at)

#### 4. Frontend Config API Client

**File**: `src/lib/apis/configs/index.ts` (append)

```typescript
export const getEmailConfig = async (token: string) => { /* GET /api/v1/configs/email */ };
export const setEmailConfig = async (token: string, config: object) => { /* POST /api/v1/configs/email */ };
export const testEmailConfig = async (token: string) => { /* POST /api/v1/configs/email/test */ };
```

#### 5. Admin Settings: Email Tab

**New file**: `src/lib/components/admin/Settings/Email.svelte`

UI:
```
┌─────────────────────────────────────────────┐
│ Email Invitations                           │
│                                             │
│ ┌─ Enable Email Invites ──────────── [ON] ─┐│
│ │                                          ││
│ │ Microsoft Graph API                      ││
│ │ ┌──────────────────────────────────────┐ ││
│ │ │ Tenant ID     [________________________]││
│ │ │ Client ID     [________________________]││
│ │ │ Client Secret [________________________]││
│ │ └──────────────────────────────────────┘ ││
│ │                                          ││
│ │ Sender                                   ││
│ │ ┌──────────────────────────────────────┐ ││
│ │ │ From Address  [no-reply@soev.ai_____] ││
│ │ │ From Name     [Soev__________________] ││
│ │ └──────────────────────────────────────┘ ││
│ │                                          ││
│ │ Invite Settings                          ││
│ │ ┌──────────────────────────────────────┐ ││
│ │ │ Expiry (hours) [168________________] ││
│ │ └──────────────────────────────────────┘ ││
│ │                                          ││
│ │ [Test Email] [Save]                      ││
│ └──────────────────────────────────────────┘│
└─────────────────────────────────────────────┘
```

Graph API fields only shown when "Enable Email Invites" is on. "Test Email" sends to the admin's own address.

**Register tab**: Add `'email'` to `ADMIN_SETTINGS_TABS` in `src/lib/utils/features.ts:87-101`.

**Render tab**: Add import, tab button, and content block in `Settings.svelte` following the existing pattern (button after 'db', content in the if/else chain).

#### 6. Pending Invites List in Admin Panel

**New file**: `src/lib/components/admin/Users/UserList/InvitesList.svelte`

Table showing pending invites:
| Email | Role | Invited By | Created | Expires | Actions |
|-------|------|------------|---------|---------|---------|
| jane@co.com | user | Admin Name | 2 hours ago | in 7 days | [Resend] [Revoke] |

This component is rendered alongside the user list in the admin users page. Add a tab/toggle between "Users" and "Pending Invites".

**File**: `src/routes/(app)/admin/users/[tab]/+page.svelte` — Add conditional rendering for invites tab.

#### 7. CSV Invite Import Extension

**File**: `src/lib/components/admin/Users/UserList/InviteUserForm.svelte` or `AddUserModal.svelte`

Add a toggle in the CSV import tab: "Import as invites" vs "Import with passwords". When importing as invites, CSV expects 3 columns (Name, Email, Role) instead of 4. Each row creates an invite via `POST /api/v1/invites`.

For bulk invites (>30 rows), display a progress bar and space API calls ~2 seconds apart to respect Exchange rate limits.

#### 8. Additional i18n Keys

- "Email", "Email Invitations", "Microsoft Graph API"
- "Tenant ID", "Client ID", "Client Secret"
- "From Address", "From Name", "Expiry (hours)"
- "Test Email", "Send a test email", "Test email sent to {{email}}"
- "Email settings saved successfully"
- "Pending Invites", "Resend", "Revoke", "Invite revoked", "Invite sent"
- "Import as invites"

### Success Criteria:

#### Automated Verification:
- [ ] Frontend builds: `npm run build`
- [ ] `GET /api/v1/configs/email` returns config (admin auth)
- [ ] `POST /api/v1/configs/email` updates config (admin auth)
- [ ] `POST /api/v1/configs/email/test` sends test email (requires Graph API)
- [ ] `GET /api/v1/invites` returns pending invites list
- [ ] `POST /api/v1/invites/{id}/resend` resends invite email
- [ ] `DELETE /api/v1/invites/{id}` revokes invite
- [ ] Admin settings "Email" tab renders at `/admin/settings/email`

#### Manual Verification:
- [ ] Email settings tab loads and saves correctly
- [ ] Test email arrives at admin's address
- [ ] Pending invites list shows correct data
- [ ] Resend button sends new email and updates expiry
- [ ] Revoke button marks invite as revoked, link stops working
- [ ] CSV import with 3 columns creates invites
- [ ] Bulk CSV import (>30 rows) shows progress and doesn't hit rate limits

---

## New Files Summary

```
backend/open_webui/
├── services/email/
│   ├── __init__.py
│   ├── auth.py                        # Client credentials token acquisition
│   └── graph_mail_client.py           # Graph API email sending + template
├── models/invites.py                  # Invite model + table class
├── routers/invites.py                 # Invite API endpoints
└── migrations/versions/
    └── xxxx_create_invite_table.py    # Alembic migration

src/
├── lib/
│   ├── apis/invites/index.ts          # Frontend API client
│   └── components/admin/
│       ├── Users/UserList/
│       │   ├── InviteUserForm.svelte  # Creation mode selector + invite form
│       │   └── InvitesList.svelte     # Pending invites list (Phase 2)
│       └── Settings/Email.svelte      # Email config tab (Phase 2)
└── routes/auth/
    └── invite/[token]/+page.svelte    # Invite acceptance page
```

## Modified Upstream Files Summary

| File | Phase | Change | Conflict Risk |
|------|-------|--------|---------------|
| `backend/open_webui/config.py` | 1 | Append 7 PersistentConfig vars after OneDrive section | Low |
| `backend/open_webui/main.py` | 1 | Config assignments (~7 lines), router mount (~2 lines), feature flag (~1 line) | Low |
| `backend/open_webui/routers/configs.py` | 2 | Append EmailConfigForm + GET/POST/test endpoints | Low |
| `src/routes/+layout.svelte` | 1 | Change `!== '/auth'` to `.startsWith('/auth')` | Low |
| `src/lib/components/admin/Users/UserList/AddUserModal.svelte` | 1 | Wrap form in conditional, import InviteUserForm | Medium |
| `src/lib/utils/features.ts` | 2 | Add `'email'` to ADMIN_SETTINGS_TABS | Low |
| `src/lib/components/admin/Settings.svelte` | 2 | Import Email, add tab button + content | Low |
| `src/lib/apis/configs/index.ts` | 2 | Append email config functions | Low |
| `src/lib/i18n/locales/*/translation.json` | 1+2 | New keys via i18n:parse | Low |

## Testing Strategy

### Unit Tests:
- Invite model CRUD operations (create, get_by_token, accept, revoke)
- Email template rendering (HTML output contains invite URL)
- Token validation (expired, accepted, revoked states)

### Integration Tests:
- Full invite flow: create → validate → accept → verify user exists
- Create invite with duplicate email → error
- Accept with invalid/expired token → appropriate error
- Accept with weak password → validation error

### Manual Testing Steps:
1. Enable email invites in admin settings, configure Graph API credentials
2. Send test email — verify it arrives
3. Create user via "Send Email" mode — verify email arrives with correct link
4. Click link in email — verify acceptance form loads with correct details
5. Set password and submit — verify logged in and can use the app
6. Try same link again — verify "already used" message
7. Create user via "Copy Link" — verify link is copyable and works
8. Disable email invites — verify modal only shows "Set Password"
9. Revoke a pending invite — verify link stops working
10. Resend an invite — verify new email arrives

## Performance Considerations

- In-memory token caching for Graph API (5-minute buffer before expiry)
- Bulk CSV invites: 2-second spacing between emails to respect Exchange rate limits (30 msg/min)
- Invite table indexes on `email` and `token` columns for fast lookups
- No N+1 queries — pending invites list resolves invited_by names in a single query

## Migration Notes

- New `invite` table — no data migration needed, purely additive
- `ENABLE_EMAIL_INVITES` defaults to `False` — zero behavioral change for existing deployments
- The root layout auth guard change (`startsWith('/auth')`) is backwards-compatible — existing `/auth` path still works

## References

- Research document: `thoughts/shared/research/2026-02-16-email-invite-links.md`
- Current admin add user endpoint: `backend/open_webui/routers/auths.py:840-889`
- AddUserForm model: `backend/open_webui/models/auths.py:70-78`
- GraphClient (OneDrive, reference): `backend/open_webui/services/onedrive/graph_client.py`
- PersistentConfig class: `backend/open_webui/config.py:165-221`
- Admin config endpoint pattern: `backend/open_webui/routers/configs.py:66-95`
- Admin settings tabs: `src/lib/utils/features.ts:87-101`
- AddUserModal: `src/lib/components/admin/Users/UserList/AddUserModal.svelte`
- Root layout auth guard: `src/routes/+layout.svelte:775`
