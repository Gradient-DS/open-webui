---
date: 2026-02-16T15:00:00+01:00
researcher: claude
git_commit: 2be7bd7a4a6f207ac7e7985f70ba0b35bab4395d
branch: feat/sync-improvements
repository: open-webui
topic: "Email invite links for admin-created users via Microsoft Graph API"
tags: [research, codebase, authentication, email, invitations, admin, microsoft-graph]
status: complete
last_updated: 2026-02-16
last_updated_by: claude
last_updated_note: "Focused on Graph API application permissions approach with all three creation modes"
---

# Research: Email Invite Links for Admin-Created Users

**Date**: 2026-02-16T15:00:00+01:00
**Researcher**: claude
**Git Commit**: 2be7bd7a4a6f207ac7e7985f70ba0b35bab4395d
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

What would it take to have admin users create normal users but instead of creating a password, the new user gets an invite link in their email? Specifically using Microsoft Graph API with `no-reply@soev.ai` (application permissions). The admin should have three options: **send email invite**, **set password manually**, and **copy invite link**.

## Summary

The implementation adds an invite system on top of the existing admin user creation flow. It uses Microsoft Graph API with application permissions (client_credentials flow) to send emails from `no-reply@soev.ai`. The architecture follows existing soev-specific patterns: new standalone files for the core logic, minimal targeted insertions in upstream files, and feature-flag gating so the fork behaves identically to upstream when invites are disabled.

**Key architectural decisions:**
- New `invite` database table for tracking (not stateless JWT tokens) — enables list/revoke/resend
- New `services/email/` module mirroring the `services/onedrive/` pattern
- Reuse of existing `GraphClient` retry/auth infrastructure
- Admin settings follow the dedicated `configs.py` endpoint pattern (not bundled into `AdminConfig`)
- All three creation modes in a single redesigned `AddUserModal`
- Feature-gated behind `ENABLE_EMAIL_INVITES` — falls back to current behavior when disabled

---

## Current State

### Admin User Creation (`POST /auths/add`)

- Endpoint at `backend/open_webui/routers/auths.py:840-889`
- Requires: name, email, **password** (mandatory), role
- `AddUserForm` extends `SignupForm` which has `password: str` as required field
- Frontend: `AddUserModal.svelte` — form mode + CSV import mode
- No email is sent — admin must communicate credentials out-of-band

### Existing Infrastructure We Can Reuse

| Component | Location | What It Provides |
|-----------|----------|-----------------|
| `GraphClient` | `services/onedrive/graph_client.py` | Async httpx client with 401 refresh, 429 backoff, 5xx retry. `_request_with_retry(method, url, ...)` supports any HTTP method. |
| `OAuthSessions` | `models/oauth_sessions.py` | Fernet-encrypted token storage keyed by `(provider, user_id)`. Adding `provider="email_service"` requires zero schema changes. |
| `PersistentConfig` | `config.py:165-221` | Config values that persist to DB and sync via Redis. Pattern: `PersistentConfig("ENV_NAME", "dotpath.key", default)`. |
| `AppConfig` | `config.py:224-283` | Runtime config on `app.state.config` — auto-persists on assignment, syncs across instances via Redis. |
| `create_token` | `utils/auth.py:191-202` | Generic JWT creation with custom payload + expiry. Can encode invite-specific claims. |
| `decode_token` | `utils/auth.py:205-210` | JWT decode with signature verification. |
| Admin config pattern | `routers/configs.py` | Pydantic model + GET/POST pair for feature-domain config. Used by Connections, CodeExecution, etc. |

---

## Architecture

### New Files (No Upstream Conflict)

```
backend/open_webui/
├── services/email/
│   ├── __init__.py
│   ├── graph_mail_client.py      # Graph API email sending
│   └── auth.py                   # Client credentials token acquisition + caching
├── models/invites.py             # Invite database model + table class
├── routers/invites.py            # Invite API endpoints
└── migrations/versions/
    └── xxxx_create_invite_table.py

src/
├── lib/
│   ├── apis/invites/index.ts     # Frontend API client for invites
│   └── components/admin/Users/UserList/
│       └── InvitesList.svelte    # Pending invites list (optional, phase 2)
└── routes/auth/
    └── invite/[token]/+page.svelte  # Invite acceptance page
```

### Modified Upstream Files (Minimal, Targeted)

| File | Changes | Conflict Risk |
|------|---------|--------------|
| `backend/open_webui/config.py` | Add `ENABLE_EMAIL_INVITES` + email config vars at end of file | Low — appended |
| `backend/open_webui/main.py` | Mount invites router (~3 lines), add feature flag to config response (~2 lines) | Low — small insertions near existing soev additions |
| `backend/open_webui/routers/auths.py` | Make password optional in `AddUserForm` when invite mode | Medium — modifies existing model |
| `backend/open_webui/routers/configs.py` | Add email config GET/POST endpoints | Low — new endpoint pair appended |
| `src/lib/apis/configs/index.ts` | Add `getEmailConfig` / `setEmailConfig` functions | Low — appended |
| `src/lib/components/admin/Users/UserList/AddUserModal.svelte` | Add creation mode selector, conditionally hide password | Medium — modifies existing component |
| `src/lib/components/admin/Settings.svelte` | Add "Email" tab button + rendering | Low — follows existing tab pattern |
| `src/routes/auth/+page.svelte` | No changes needed | None |
| `src/lib/i18n/locales/*/translation.json` | New keys auto-sorted by parser | Low — alphabetical insertion |

### Upstream Merge Strategy

Following established patterns from the OneDrive feature:

1. **Isolate core logic in new files** — `services/email/`, `models/invites.py`, `routers/invites.py` have zero upstream overlap
2. **Feature-flag gate all modifications** — `ENABLE_EMAIL_INVITES` (default `False`) means the fork is identical to upstream when disabled
3. **Append config vars** at the end of `config.py` (after OneDrive vars at line ~2553)
4. **Group main.py changes** near existing soev additions (router mounts around line 1530, config response around line 2060)
5. **Translation keys** are auto-sorted alphabetically by `npm run i18n:parse` — conflicts only occur if upstream adds keys alphabetically adjacent to ours
6. **New admin settings tab** follows existing tab pattern — adds to `ADMIN_SETTINGS_TABS` array and `Settings.svelte` conditional chain
7. **The `AddUserModal` modification is the highest conflict risk** — consider wrapping the invite-specific UI in a separate child component that the modal conditionally renders, keeping the diff to the upstream file small

---

## Admin Settings: Email Configuration

### Backend Config Variables

Add to `config.py` (after OneDrive section):

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
    int(os.environ.get("INVITE_EXPIRY_HOURS", "168")),  # 7 days
)
```

### Backend Config Endpoints

Add to `routers/configs.py` following the existing pattern:

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
    # ... assign all fields ...
    return form_data
```

### Frontend Admin Settings Tab: "Email"

New tab component `src/lib/components/admin/Settings/Email.svelte`:

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

**Tab registration**: Add `'email'` to `ADMIN_SETTINGS_TABS` in `src/lib/utils/features.ts` and the corresponding tab button + content block in `Settings.svelte`.

**"Test Email" button**: Sends a test email to the admin's own address to verify the Graph API config is working. Backend endpoint: `POST /api/v1/configs/email/test`.

---

## The Three Creation Modes

### Redesigned AddUserModal

The modal gets a **creation mode selector** shown before the form fields:

```
┌─────────────────────────────────────────────┐
│ Add New User                          [CSV] │
│                                             │
│ How should this user be created?            │
│                                             │
│ ┌─────────────┐ ┌──────────────┐ ┌────────┐│
│ │ 📧 Send     │ │ 📋 Copy      │ │ 🔑 Set ││
│ │ Email Invite│ │ Invite Link  │ │Password││
│ │ (selected)  │ │              │ │        ││
│ └─────────────┘ └──────────────┘ └────────┘│
│                                             │
│ Role     [User ▾]                           │
│ Name     [___________________________]      │
│ Email    [___________________________]      │
│                                             │
│ (password field hidden in invite modes)     │
│                                             │
│                    [Cancel] [Send Invite]    │
└─────────────────────────────────────────────┘
```

**Behavior per mode:**

| Mode | Required Fields | Password Field | Submit Action | Submit Button Text |
|------|----------------|----------------|--------------|-------------------|
| **Send Email** | name, email, role | Hidden | `POST /api/v1/invites` (creates invite + sends email) | "Send Invite" |
| **Copy Link** | name, email, role | Hidden | `POST /api/v1/invites?send_email=false` (creates invite, returns link) | "Create Invite" |
| **Set Password** | name, email, password, role | Visible | `POST /api/v1/auths/add` (existing flow, unchanged) | "Add User" |

**When `ENABLE_EMAIL_INVITES` is false**: Only "Set Password" mode is available. The mode selector is hidden entirely. The modal looks and works exactly like it does today — zero behavioral change for upstream or unconfigured deployments.

**Implementation approach for upstream compatibility**: Extract the mode selector + invite form into a child component `InviteUserForm.svelte` that `AddUserModal.svelte` conditionally renders. The diff to the upstream file stays small:

```svelte
<!-- In AddUserModal.svelte, minimal change to upstream file -->
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

### CSV Import Extension

The existing CSV tab can also support invite mode:

| Current CSV columns | Invite CSV columns |
|--------------------|--------------------|
| Name, Email, Password, Role | Name, Email, Role |

Detection: If the CSV has 3 columns instead of 4, treat it as invite mode. Or add a toggle above the CSV upload: "Import as invites" vs "Import with passwords".

---

## Invite Flow: End-to-End

### 1. Admin Creates Invite

```
Admin clicks "Add User" → modal opens → selects "Send Email" or "Copy Link"
  → fills name, email, role → clicks submit
  → Frontend: POST /api/v1/invites { name, email, role, send_email: true/false }
  → Backend:
    1. Validate email format, check not already registered
    2. Generate invite token (UUID)
    3. Create invite record in DB (status: pending, expires_at: now + INVITE_EXPIRY_HOURS)
    4. If send_email=true:
       a. Get Graph API access token (client_credentials)
       b. POST /users/no-reply@soev.ai/sendMail with invite email
    5. Return { invite_id, token, invite_url, email_sent: bool }
  → Frontend (Send Email mode): toast "Invite sent to user@example.com"
  → Frontend (Copy Link mode): show copyable invite URL in a dialog
```

### 2. Invited User Accepts

```
User clicks link in email → browser opens /auth/invite/{token}
  → Frontend: GET /api/v1/invites/{token}/validate
  → Backend: Decode token, check not expired/accepted, return { email, name, role, invited_by }
  → Frontend: Shows acceptance form (pre-filled name + email, password input)
  → User sets password → clicks "Create Account"
  → Frontend: POST /api/v1/invites/{token}/accept { password }
  → Backend:
    1. Validate token again (not expired, not accepted)
    2. Validate + hash password
    3. Create Auth + User records via Auths.insert_new_auth()
    4. Mark invite as accepted (set accepted_at)
    5. Apply default group assignment
    6. Create session JWT + set cookie
    7. Return SigninResponse (user is now logged in)
  → Frontend: Store token in localStorage, redirect to /
```

### 3. Admin Manages Invites (Phase 2, Optional)

```
Admin panel → Users → "Pending Invites" tab
  → GET /api/v1/invites/list → shows table of pending invites
  → Each row has: email, role, invited_by, created_at, expires_at, [Resend] [Revoke]
  → Resend: POST /api/v1/invites/{id}/resend → sends email again
  → Revoke: DELETE /api/v1/invites/{id} → marks as revoked
```

---

## Database Model

### Invite Table

```python
# backend/open_webui/models/invites.py

class Invite(Base):
    __tablename__ = "invite"

    id = Column(String, primary_key=True)            # UUID
    email = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)             # Pre-filled name from admin
    token = Column(String, unique=True, nullable=False, index=True)  # UUID for URL
    role = Column(String, default="user")             # Role to assign on acceptance
    invited_by = Column(String, nullable=False)       # Admin user ID
    expires_at = Column(BigInteger, nullable=False)   # Epoch timestamp
    accepted_at = Column(BigInteger, nullable=True)   # Null = pending, set on acceptance
    created_at = Column(BigInteger, nullable=False)   # Epoch timestamp
```

Alembic migration creates this table. No foreign key to `user` table to avoid complications if the inviting admin is later deleted.

---

## Email Sending via Microsoft Graph API

### Token Acquisition (`services/email/auth.py`)

Client credentials flow — no user interaction, no stored refresh tokens needed:

```python
import httpx
import time

_token_cache = {"access_token": None, "expires_at": 0}

async def get_mail_access_token(app) -> str:
    """Get Graph API token using client_credentials flow. Caches until expiry."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 300:
        return _token_cache["access_token"]

    tenant_id = app.state.config.EMAIL_GRAPH_TENANT_ID
    client_id = app.state.config.EMAIL_GRAPH_CLIENT_ID
    client_secret = app.state.config.EMAIL_GRAPH_CLIENT_SECRET

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

### Email Sending (`services/email/graph_mail_client.py`)

Reuses the retry pattern from `services/onedrive/graph_client.py`:

```python
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

async def send_mail(
    app,
    to_address: str,
    subject: str,
    html_body: str,
) -> bool:
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
        return True  # 202 Accepted
```

### Email Template

Simple, clean HTML email:

```python
def render_invite_email(invite_url: str, invited_by_name: str, app_name: str = "Soev") -> str:
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

### Azure Setup Required (One-Time)

1. **Register app** in soev.ai's Entra ID → get `tenant_id`, `client_id`, `client_secret`
2. **Add `Mail.Send` application permission** → grant admin consent
3. **Create shared mailbox** `no-reply@soev.ai` in Microsoft 365 (free, no license needed)
4. **(Recommended) Application Access Policy** to restrict the app to only `no-reply@soev.ai`:
   ```powershell
   New-ApplicationAccessPolicy -AppId "{client_id}" `
     -PolicyScopeGroupId "no-reply@soev.ai" `
     -AccessRight RestrictAccess
   ```

### Rate Limits

| Limit | Value | Impact |
|-------|-------|--------|
| Graph API requests | 10,000 / 10 min / mailbox | Not a concern |
| Exchange message rate | **30 messages/minute** per mailbox | Relevant for CSV bulk invites |
| Exchange recipients/day | 10,000 / 24h | Fine for invite volumes |

For CSV bulk invites with >30 rows: queue emails with ~2-second spacing. A simple `asyncio.sleep(2)` between sends is sufficient.

---

## API Endpoints

### Invite Router (`routers/invites.py`)

```python
# POST /api/v1/invites — Create invite (admin only)
class InviteForm(BaseModel):
    name: str
    email: str
    role: Optional[str] = "user"
    send_email: Optional[bool] = True

class InviteResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    token: str
    invite_url: str
    email_sent: bool
    expires_at: int
    created_at: int

# GET /api/v1/invites — List pending invites (admin only)
# Returns list of InviteResponse

# GET /api/v1/invites/{token}/validate — Validate invite (public, no auth)
class InviteValidation(BaseModel):
    email: str
    name: str
    role: str
    invited_by_name: str
    expires_at: int

# POST /api/v1/invites/{token}/accept — Accept invite (public, no auth)
class AcceptInviteForm(BaseModel):
    password: str
    name: Optional[str] = None  # Allow override of pre-filled name

# POST /api/v1/invites/{id}/resend — Resend invite email (admin only)
# DELETE /api/v1/invites/{id} — Revoke invite (admin only)
```

### Mounting

In `main.py`, near the existing router mounts:

```python
from open_webui.routers import invites

# Near line 1530, after the OneDrive mount
if app.state.config.ENABLE_EMAIL_INVITES:
    app.include_router(invites.router, prefix="/api/v1/invites", tags=["invites"])
```

**Note**: Even when `ENABLE_EMAIL_INVITES` is False, the "Copy Link" mode still works since it doesn't require Graph API config. Consider always mounting the router but gating the email-sending codepath.

Actually, better approach: **always mount the router**. The endpoints work for both invite modes. Only the email-sending codepath checks for Graph API config. This way "Copy Link" works even without Microsoft Graph setup — useful for admins who just want passwordless invite links without email infrastructure.

---

## Frontend Implementation

### Invite Acceptance Page (`src/routes/auth/invite/[token]/+page.svelte`)

```
┌─────────────────────────────────────────────┐
│                                             │
│            [Soev Logo]                      │
│                                             │
│     You've been invited by {admin_name}     │
│                                             │
│ Name     [Jane Doe________________] (pre-filled, editable)
│ Email    [jane@company.com________] (pre-filled, readonly)
│ Password [________________________]         │
│ Confirm  [________________________]         │
│                                             │
│         [Create Account]                    │
│                                             │
│                          Powered by soev.ai │
└─────────────────────────────────────────────┘
```

**States**:
- Loading: validating token
- Valid: show form
- Expired: "This invite has expired. Please contact your administrator."
- Already accepted: "This invite has already been used." + link to sign in
- Invalid: "Invalid invite link."

**On submit**: `POST /api/v1/invites/{token}/accept` → on success, store token in `localStorage.token`, redirect to `/`.

This page does NOT modify the existing auth page (`src/routes/auth/+page.svelte`) — it's a completely new route, avoiding upstream conflicts.

### Copy Link Dialog

When the admin selects "Copy Link" mode and submits, show a dialog with:

```
┌─────────────────────────────────────────────┐
│ Invite Created                              │
│                                             │
│ Share this link with the user:              │
│ ┌─────────────────────────────────────────┐ │
│ │ https://app.soev.ai/auth/invite/abc123  │ │
│ │                                   [📋]  │ │
│ └─────────────────────────────────────────┘ │
│                                             │
│ This link expires in 7 days.                │
│                                             │
│                                     [Done]  │
└─────────────────────────────────────────────┘
```

The copy button uses `navigator.clipboard.writeText()`.

### Frontend API Client (`src/lib/apis/invites/index.ts`)

```typescript
export const createInvite = async (
    token: string, name: string, email: string,
    role: string, sendEmail: boolean
) => { /* POST /api/v1/invites */ };

export const validateInvite = async (inviteToken: string) => {
    /* GET /api/v1/invites/{token}/validate — no auth token needed */
};

export const acceptInvite = async (
    inviteToken: string, password: string, name?: string
) => { /* POST /api/v1/invites/{token}/accept — no auth token needed */ };

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

---

## Translations (i18n)

### Convention

Keys ARE the English text. Use `$i18n.t('English text here')` in components, then run `npm run i18n:parse` to propagate to all 59 locales.

### New Keys Needed

```json
"Accept Invite": "",
"Accept invite and create your account": "",
"Confirm Password": "",
"Copy Invite Link": "",
"Copy link": "",
"Create Invite": "",
"Email Invitations": "",
"Email invite sent to {{email}}": "",
"Email settings saved successfully": "",
"Enter the password for your new account": "",
"Expiry (hours)": "",
"From Address": "",
"From Name": "",
"How should this user be created?": "",
"Invalid invite link": "",
"Invite Created": "",
"Invite expired": "",
"Invite has already been accepted": "",
"Invite link copied to clipboard": "",
"Invite revoked": "",
"Invite sent": "",
"Invited by {{name}}": "",
"Microsoft Graph API": "",
"Pending Invites": "",
"Resend": "",
"Resend Invite": "",
"Revoke": "",
"Send Email Invite": "",
"Send Invite": "",
"Send a test email": "",
"Set Password": "",
"Share this link with the user:": "",
"Test Email": "",
"Test email sent to {{email}}": "",
"This invite has expired. Please contact your administrator.": "",
"This invite has already been used.": "",
"This link expires in {{hours}} hours.": "",
"You've been invited by {{name}}": "",
"You've been invited to join": ""
```

All keys follow the flat, alphabetical convention. After adding `$i18n.t(...)` calls in Svelte components, `npm run i18n:parse` auto-inserts these into all locale files at their alphabetical positions.

**Dutch translations** (for `nl-NL/translation.json`) can be filled in afterwards:

```json
"Accept Invite": "Uitnodiging accepteren",
"Copy Invite Link": "Uitnodigingslink kopiëren",
"Email Invitations": "E-mailuitnodigingen",
"Send Email Invite": "E-mailuitnodiging versturen",
"Set Password": "Wachtwoord instellen",
"You've been invited by {{name}}": "Je bent uitgenodigd door {{name}}"
```

---

## Feature Flag Exposure

In `main.py`'s `/api/config` response (around line 2060, near existing soev feature flags):

```python
"enable_email_invites": app.state.config.ENABLE_EMAIL_INVITES,
```

Frontend reads this via `$config?.features?.enable_email_invites` to conditionally show/hide invite UI in AddUserModal.

---

## Phasing

### Phase 1: Core (MVP)

- [ ] Database: `invite` table + Alembic migration
- [ ] Backend: `services/email/auth.py` + `services/email/graph_mail_client.py`
- [ ] Backend: `models/invites.py` — model + table class
- [ ] Backend: `routers/invites.py` — create, validate, accept endpoints
- [ ] Backend: Config vars in `config.py`, wiring in `main.py`
- [ ] Frontend: `apis/invites/index.ts` — API client
- [ ] Frontend: Modified `AddUserModal.svelte` — three creation modes
- [ ] Frontend: `routes/auth/invite/[token]/+page.svelte` — acceptance page
- [ ] Frontend: Copy link dialog
- [ ] i18n: New translation keys

### Phase 2: Admin Management

- [ ] Backend: list, resend, revoke endpoints
- [ ] Frontend: Admin settings "Email" tab with Graph API config
- [ ] Frontend: Pending invites list in admin panel
- [ ] Backend: "Test Email" endpoint
- [ ] CSV invite import (3-column format)

---

## Code References

| What | Where |
|------|-------|
| Current admin add user endpoint | `backend/open_webui/routers/auths.py:840-889` |
| `AddUserForm` / `SignupForm` models | `backend/open_webui/models/auths.py:70-78` |
| `Auths.insert_new_auth()` | `backend/open_webui/models/auths.py:82-112` |
| `create_token()` / `decode_token()` | `backend/open_webui/utils/auth.py:191-210` |
| `GraphClient` (reusable) | `backend/open_webui/services/onedrive/graph_client.py` |
| OneDrive OAuth pattern (reference) | `backend/open_webui/services/onedrive/auth.py` |
| Token refresh pattern (reference) | `backend/open_webui/services/onedrive/token_refresh.py` |
| `OAuthSessions` model | `backend/open_webui/models/oauth_sessions.py` |
| `PersistentConfig` class | `backend/open_webui/config.py:165-221` |
| Admin config endpoint pattern | `backend/open_webui/routers/configs.py` |
| `AddUserModal` component | `src/lib/components/admin/Users/UserList/AddUserModal.svelte` |
| Admin settings tab container | `src/lib/components/admin/Settings.svelte` |
| Admin settings tab registry | `src/lib/utils/features.ts:87-101` |
| Auth page (unchanged) | `src/routes/auth/+page.svelte` |
| i18n config | `i18next-parser.config.ts` |
| Translation file | `src/lib/i18n/locales/en-US/translation.json` |
