---
date: 2026-02-16T15:00:00+01:00
researcher: claude
git_commit: 2be7bd7a4a6f207ac7e7985f70ba0b35bab4395d
branch: feat/sync-improvements
repository: open-webui
topic: "Email invite links for admin-created users"
tags: [research, codebase, authentication, email, invitations, admin, microsoft-graph]
status: complete
last_updated: 2026-02-16
last_updated_by: claude
last_updated_note: "Added Microsoft Graph API email sending research"
---

# Research: Email Invite Links for Admin-Created Users

**Date**: 2026-02-16T15:00:00+01:00
**Researcher**: claude
**Git Commit**: 2be7bd7a4a6f207ac7e7985f70ba0b35bab4395d
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

What would it take to have admin users create normal users (already possible) but instead of creating a password for them, the new user gets an invite link in their email?

**Follow-up**: Can we use Microsoft Graph API to send emails from `no-reply@soev.ai`, or even send on behalf of the admin user from the client company?

## Summary

Open WebUI has **no email infrastructure** but has a **mature Microsoft Graph API integration** for OneDrive sync that provides reusable building blocks (GraphClient, OAuth token storage, token refresh). Using Microsoft Graph API to send invite emails is the most natural fit for the enterprise context.

There are **two viable approaches** for email delivery:

1. **Application permissions** — Send as `no-reply@soev.ai` from soev.ai's own Microsoft 365 tenant. Requires a one-time Azure app registration with `Mail.Send` permission. Simple, fully under soev.ai's control.

2. **Delegated permissions** — Send as/on behalf of the admin user at the client company. Uses the same OAuth pattern as OneDrive sync (admin authenticates once, we store refresh token). More complex but emails come from a trusted sender within the client's own organization.

## Detailed Findings

### Current Admin User Creation Flow

The admin creates users via `POST /auths/add` (`backend/open_webui/routers/auths.py:840-889`), which requires:
- **name** (required)
- **email** (required)
- **password** (required) — validated and bcrypt-hashed
- **role** (optional, defaults to `"pending"`)

The frontend `AddUserModal.svelte` collects these fields in a form and also supports CSV batch import (Name, Email, Password, Role columns).

**Key limitation**: Password is mandatory — `AddUserForm` extends `SignupForm` which has `password: str` as a required field.

### Existing Microsoft Graph API Infrastructure

The codebase already has a **full Graph API integration** for OneDrive sync that provides reusable infrastructure:

#### GraphClient (`backend/open_webui/services/onedrive/graph_client.py`)
- Async HTTP client wrapping `httpx` against `https://graph.microsoft.com/v1.0`
- `_request_with_retry()` handles 401 (token refresh), 429 (rate limit backoff), 5xx (exponential retry)
- Accepts a `token_provider` callback for mid-request token refresh
- Currently only has GET-based convenience methods, but `_request_with_retry()` supports any HTTP method

#### OAuth Token Storage (`backend/open_webui/models/oauth_sessions.py`)
- `OAuthSessions` table with Fernet-encrypted token JSON
- Keyed by `(provider, user_id)` — provider discriminates token types:
  - `"microsoft"` — login SSO (scope: `openid email profile`)
  - `"onedrive"` — background sync (scope: `Files.Read.All offline_access`)
- Adding a new provider like `"microsoft_mail"` requires zero schema changes

#### OneDrive OAuth Pattern (`backend/open_webui/services/onedrive/auth.py`)
- Implements full OAuth code flow with PKCE for Graph API scopes
- Auth code exchange + token storage + refresh logic
- Uses `MICROSOFT_CLIENT_SECRET` (shared between login and OneDrive flows)
- Token refresh in `token_refresh.py` with 5-minute expiry buffer

#### Existing Config Variables
- `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` / `MICROSOFT_CLIENT_TENANT_ID` — login OAuth
- `ONEDRIVE_CLIENT_ID_BUSINESS` / `ONEDRIVE_SHAREPOINT_TENANT_ID` — OneDrive OAuth
- The client secret is shared between flows

### No Email Infrastructure Exists

- No SMTP libraries, config, or sending code
- No password reset flow
- No email templates
- Only notification mechanism is webhooks (HTTP POST to Slack/Discord/Teams)

### JWT System Can Support Invite Tokens

`create_token(data: dict, expires_delta)` at `backend/open_webui/utils/auth.py:191-202` accepts any dict payload and can be used for invite-specific tokens.

---

## Microsoft Graph API Email Sending Options

### Option A: Application Permissions — Send as `no-reply@soev.ai`

**How it works**: Register an app in soev.ai's Azure/Entra ID tenant with `Mail.Send` application permission. The backend uses client_credentials flow (no user interaction) to get tokens and sends email via `POST /users/no-reply@soev.ai/sendMail`.

**Azure setup (one-time)**:
1. Register app in Entra ID → get Client ID + Client Secret
2. Add `Mail.Send` **application** permission → admin consent
3. Create `no-reply@soev.ai` as a shared mailbox in Microsoft 365
4. (Recommended) Create Application Access Policy to restrict the app to only `no-reply@soev.ai`

**Backend flow**:
```
1. Get token: POST https://login.microsoftonline.com/{soev-tenant}/oauth2/v2.0/token
   (client_credentials grant, scope: https://graph.microsoft.com/.default)
2. Send email: POST https://graph.microsoft.com/v1.0/users/no-reply@soev.ai/sendMail
   (Bearer token, JSON body with message)
```

**Pros**:
- Fully under soev.ai's control — no dependency on client tenant configuration
- No user interaction needed — pure backend service
- Simple: one set of credentials for all clients
- Emails always come from a consistent `no-reply@soev.ai` address

**Cons**:
- Requires a Microsoft 365 license for the `no-reply@soev.ai` mailbox (shared mailbox is free)
- Emails come from soev.ai domain, not the client's domain — may be less trusted by client's users
- Rate limit: 30 messages/minute, 10,000 recipients/24h per mailbox

**New config needed**:
```
INVITE_EMAIL_TENANT_ID     # soev.ai's Azure tenant ID
INVITE_EMAIL_CLIENT_ID     # App registration client ID
INVITE_EMAIL_CLIENT_SECRET # App registration client secret
INVITE_EMAIL_FROM_ADDRESS  # no-reply@soev.ai
```

### Option B: Delegated Permissions — Send as the Admin User

**How it works**: The admin user at the client company authenticates once via OAuth popup (exactly like OneDrive sync auth). We store their refresh token with `Mail.Send` scope. When sending invites, we use their token to send email via `POST /users/{admin-email}/sendMail`.

**What the admin sees**: A one-time consent popup requesting permission to "Send mail as you". After that, all invite emails are sent as the admin automatically.

**This mirrors the existing OneDrive pattern exactly**:
- OneDrive: Admin authenticates → we store refresh token with `Files.Read.All` scope → background sync uses it
- Email: Admin authenticates → we store refresh token with `Mail.Send` scope → invite emails use it

**Azure setup**:
- The existing soev.ai app registration already supports multi-tenant or needs the `Mail.Send` **delegated** permission added
- Each client's admin grants consent when they first authorize

**Backend flow**:
```
1. Admin clicks "Authorize email sending" → OAuth popup
   (scope: https://graph.microsoft.com/Mail.Send offline_access)
2. Exchange auth code for tokens → store in OAuthSessions (provider="microsoft_mail")
3. On invite: refresh token if needed → POST /users/{admin-email}/sendMail
```

**Pros**:
- Emails come from the admin's own address (`admin@clientcompany.com`) — highly trusted
- No soev.ai mailbox needed
- Leverages existing OAuth infrastructure (token storage, refresh logic)
- Each client controls their own email sending authorization

**Cons**:
- Requires per-client admin authorization (one-time OAuth consent)
- If admin's refresh token expires/revokes, email sending stops until re-auth
- Client's IT may need to approve the `Mail.Send` permission for the app
- Admin may not want invite emails sent "from" their personal mailbox
- More complex: need to handle token refresh failures, re-auth prompts

**Reusable code**:
- `GraphClient._request_with_retry()` — retry/refresh logic
- `OAuthSessions` — token storage with encryption
- `onedrive/auth.py` pattern — PKCE auth code flow, token exchange, state management
- `token_refresh.py` pattern — background token refresh with expiry buffer

### Option C: Hybrid — Fallback Chain

Use Option B (delegated) when the admin has authorized email, fall back to Option A (application) as the default. This gives the best of both worlds:
- Enterprise clients who want emails from their own domain: admin authorizes once
- Quick setup / smaller clients: emails come from `no-reply@soev.ai` with no client configuration

### Option D: "Send on Behalf of" (Compromise)

Using application permissions, set the `from` field to the admin's address while sending from `no-reply@soev.ai`. The recipient would see: *"no-reply@soev.ai on behalf of admin@clientcompany.com"*.

**Caveat**: This only works if the admin's mailbox grants "Send on Behalf of" permission to the soev.ai service account, which requires Exchange admin configuration in the client's tenant — effectively negating the simplicity advantage of application permissions. Not recommended.

---

## Recommended Approach

**Start with Option A (application permissions, `no-reply@soev.ai`)** for simplicity, with the architecture designed to support Option B later:

### Phase 1: Core Infrastructure
1. **Email service module** (`backend/open_webui/services/email/`) with:
   - `graph_mail_client.py` — Reuse/extend `GraphClient` with a `send_mail()` method
   - `auth.py` — Client credentials token acquisition + caching
   - `templates.py` — HTML invite email template
2. **Config** — `INVITE_EMAIL_*` env vars
3. **Invite system** — Database-backed invite table, API endpoints, frontend
4. **Admin settings UI** — Configure email sending in admin panel

### Phase 2: Delegated Flow (Optional)
5. Add OAuth consent flow for `Mail.Send` delegated permission (mirror OneDrive auth pattern)
6. Store per-admin refresh token in `OAuthSessions` with `provider="microsoft_mail"`
7. Use delegated token when available, fall back to application token

---

## Implementation Details

### Invite Token System

**Database-backed** (recommended over stateless JWT):
```python
class Invite(Base):
    __tablename__ = "invite"

    id = Column(String, primary_key=True)       # UUID
    email = Column(String, nullable=False)
    token = Column(String, unique=True, nullable=False)  # UUID for URL
    role = Column(String, default="user")
    invited_by = Column(String, ForeignKey("user.id"))
    expires_at = Column(BigInteger)              # epoch timestamp
    accepted_at = Column(BigInteger, nullable=True)  # null = pending
    created_at = Column(BigInteger)
```

### New Backend Endpoints

```
POST   /auths/invite          — Admin creates invite (email + role), sends email
GET    /auths/invite/{token}  — Validate invite token (public, returns email + status)
POST   /auths/invite/accept   — Accept invite: set name + password, activate account
DELETE /auths/invite/{token}  — Admin revokes invite
GET    /auths/invite/list     — Admin lists pending invites
POST   /auths/invite/{token}/resend — Admin resends invite email
```

### Graph API Send Mail Call

```python
async def send_invite_email(to_email: str, invite_token: str, invited_by_name: str):
    token = await get_mail_access_token()  # client_credentials or delegated

    invite_url = f"{WEBUI_URL}/auth/invite/{invite_token}"

    payload = {
        "message": {
            "subject": f"{invited_by_name} has invited you to join Soev",
            "body": {
                "contentType": "HTML",
                "content": render_invite_template(invite_url, invited_by_name),
            },
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ],
        },
        "saveToSentItems": False,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://graph.microsoft.com/v1.0/users/{from_address}/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        # 202 Accepted = success
```

### Frontend Changes

- **AddUserModal**: Toggle between "Set password" and "Send invite link"
- **New route** `src/routes/auth/invite/[token]/+page.svelte`: Invite acceptance page
- **Admin section**: Pending invites list with resend/revoke

### Rate Limits to Consider

| Limit | Value | Impact |
|-------|-------|--------|
| Graph API requests | 10,000 / 10 min / app / mailbox | Not a concern for invites |
| Exchange message rate | **30 messages/minute** per mailbox | Bottleneck for bulk CSV import |
| Exchange recipient rate | 10,000 / 24h per mailbox | Fine for invite volumes |

For CSV bulk invites: queue emails and send with 2-second spacing to stay within limits.

## Code References

- `backend/open_webui/routers/auths.py:840-889` — Current admin add user endpoint
- `backend/open_webui/utils/auth.py:191-202` — `create_token()` function
- `backend/open_webui/models/auths.py:70-78` — `SignupForm` and `AddUserForm` models
- `backend/open_webui/services/onedrive/graph_client.py` — Reusable Graph API client
- `backend/open_webui/services/onedrive/auth.py` — OAuth code flow pattern to mirror
- `backend/open_webui/services/onedrive/token_refresh.py` — Token refresh pattern
- `backend/open_webui/models/oauth_sessions.py` — Encrypted token storage
- `backend/open_webui/config.py:372-414` — Microsoft OAuth config vars
- `backend/open_webui/utils/webhook.py:11-62` — Webhook pattern reference
- `src/lib/components/admin/Users/UserList/AddUserModal.svelte` — Frontend add user modal
- `src/routes/auth/+page.svelte` — Auth page (sign-in/sign-up)

## Architecture Insights

1. **Graph API is the natural fit**: The codebase already has a mature Graph API integration (OneDrive sync) with client, auth, token management, and retry logic. Adding email sending follows the same patterns — no new infrastructure paradigm needed.
2. **No new pip dependencies needed**: The existing `httpx` client suffices for Graph API calls. Token acquisition is raw HTTP, same as OneDrive auth. No need for `msal`, `msgraph-sdk`, or email libraries.
3. **OAuthSessions provides free token storage**: Just add `provider="microsoft_mail"` — no schema changes, encryption comes for free.
4. **The OneDrive delegated auth pattern is directly replicable**: If we want to send as the admin user, the entire PKCE flow + token exchange + refresh logic is already implemented. Only the scope string changes from `Files.Read.All` to `Mail.Send`.
5. **Application permissions are simpler for MVP**: Client_credentials flow means no per-client OAuth consent, no refresh token management, no re-auth failure handling.

## Open Questions

1. **Application vs delegated permissions for v1?** Application (send as `no-reply@soev.ai`) is much simpler to ship. Delegated (send as admin) could be a follow-up.
2. **Should the invite option require email config, or should there be a "copy link" fallback?** A copy-link fallback is trivial to implement and useful even with email working.
3. **CSV bulk invite support?** Need to queue emails with spacing to respect Exchange's 30 msg/min limit.
4. **Invite expiry duration**: 7 days seems reasonable. Should it be admin-configurable?
5. **Do we need the soev.ai mailbox for other features too?** Password reset, notifications, etc. — if so, worth setting up the infrastructure generically.
