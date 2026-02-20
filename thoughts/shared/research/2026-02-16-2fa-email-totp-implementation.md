---
date: 2026-02-16T16:00:00+01:00
researcher: claude
git_commit: 2be7bd7a4a6f207ac7e7985f70ba0b35bab4395d
branch: feat/sync-improvements
repository: open-webui
topic: "2FA via Email OTP and TOTP for Email+Password Users"
tags: [research, codebase, authentication, 2fa, totp, otp, security]
status: complete
last_updated: 2026-02-16
last_updated_by: claude
last_updated_note: "Added client decisions on scope, email infra, and bypass rules"
---

# Research: 2FA via Email OTP and TOTP for Email+Password Users

**Date**: 2026-02-16T16:00:00+01:00
**Researcher**: claude
**Git Commit**: `2be7bd7a4a6f207ac7e7985f70ba0b35bab4395d`
**Branch**: `feat/sync-improvements`
**Repository**: open-webui

## Research Question

What would it take to offer 2FA via email and TOTP (like Microsoft Authenticator) on email+password users of Open WebUI (no SSO)?

## Summary

Open WebUI has **zero existing 2FA/MFA infrastructure**. There is no TOTP support, no email sending capability, and no intermediate auth state. Implementation requires:

1. **Backend**: New database columns on `auth` table + `recovery_code` table, 3-5 new API endpoints, modifications to the signin flow to support a two-step auth process, TOTP secret encryption, and email OTP infrastructure (SMTP client)
2. **Frontend**: New 2FA verification page/modal in the login flow, TOTP setup UI with QR code in account settings, recovery code display, admin toggle for 2FA enforcement
3. **Dependencies**: `pyotp` (TOTP), `qrcode[pil]` (QR codes), and an SMTP library (e.g. `aiosmtplib` or stdlib `smtplib`)
4. **Estimated scope**: Medium-large feature. ~15-20 files touched, 1 Alembic migration, ~1500-2500 lines of new code

The hardest part is the **intermediate auth state**: currently, signin immediately issues a full JWT. 2FA requires a two-step flow with a short-lived partial token.

## Detailed Findings

### 1. Current Authentication System

#### Backend Auth Flow (`backend/open_webui/routers/auths.py`)

The signin endpoint (`POST /api/v1/auths/signin`, line 510-634) has three branches:
- **Trusted header auth** (reverse proxy SSO) - lines 518-548
- **Auth disabled** (hardcoded admin) - lines 549-569
- **Standard email+password** (the target for 2FA) - lines 570-588

The standard flow:
1. Rate limit check: `signin_rate_limiter.is_limited(email)` — 15 attempts per 3min (line 571)
2. Bcrypt password verification via `Auths.authenticate_user()` (line 586)
3. JWT creation with `create_token(data={"id": user.id})` (line 597)
4. Cookie set + JSON response with full session (lines 609-632)

**Critical gap**: There is no intermediate step between password verification and JWT issuance. 2FA needs to insert a challenge step here.

#### JWT System (`backend/open_webui/utils/auth.py`)

- **Token creation** (line 191): Payload `{"id": user_id, "jti": uuid, "exp": ...}`, signed with HS256 using `WEBUI_SECRET_KEY`
- **Token validation** (line 269, `get_current_user()`): Extracts token from Bearer header or `token` cookie, decodes, checks Redis revocation, looks up user
- **Token revocation** (line 213): Redis-based JTI blacklist (no-op without Redis)

#### Database Models

**Auth table** (`backend/open_webui/models/auths.py:17-23`):
| Column | Type | Notes |
|--------|------|-------|
| `id` | String (PK) | UUID shared with `user` table |
| `email` | String | User email |
| `password` | Text | bcrypt hash |
| `active` | Boolean | Account active flag |

**No 2FA fields exist.** No `totp_secret`, no `totp_enabled`, no backup codes.

**User table** (`backend/open_webui/models/users.py:45-76`): 23 columns for profile/settings. The `info` (JSON) and `settings` (JSON) columns could theoretically hold 2FA state but dedicated columns are cleaner.

#### Auth Dependencies (Route Guards)

Three FastAPI dependencies in `utils/auth.py`:
- `get_current_user()` (line 269) — validates JWT, returns `UserModel`
- `get_verified_user()` (line 400) — requires role `user` or `admin`
- `get_admin_user()` (line 409) — requires role `admin`

**Important**: `get_current_user()` must be modified to reject partial 2FA tokens from accessing normal endpoints.

### 2. Email Infrastructure — Does Not Exist

There is **no email sending capability** in Open WebUI:
- No SMTP configuration in `config.py` or `env.py`
- No email-related Python packages in `pyproject.toml` or `requirements.txt`
- No email templates directory
- No password reset flow (only authenticated password change)
- No "forgot password" feature at all

The only outbound notification system is **webhooks** (`utils/webhook.py`) supporting Slack, Discord, Teams, and generic HTTP POST.

**For email OTP, you must build from scratch:**
- SMTP configuration (host, port, username, password, TLS, from address)
- Email sending utility (async preferred for FastAPI)
- HTML email template for OTP codes
- Rate limiting for email sends

### 3. Frontend Auth Flow

#### Login Page (`src/routes/auth/+page.svelte`)

- Three modes: `signin`, `signup`, `ldap`
- `signInHandler()` (line 59) calls `userSignIn(email, password)` → `POST /api/v1/auths/signin`
- On success, `setSessionUser()` stores token in `localStorage.token` and redirects

**Gap**: There is no concept of a "2FA challenge" screen. After `userSignIn()` succeeds, the user is immediately redirected to the app.

#### Auth API Client (`src/lib/apis/auths/index.ts`)

- `userSignIn()` (line 257) — POSTs email+password, expects full session response
- `getSessionUser()` (line 85) — validates stored token
- All API calls use `Authorization: Bearer ${localStorage.token}`

#### Auth State Management

- `$user` store (`src/lib/stores/index.ts:16`) — set to `SessionUser` on login
- Root layout (`src/routes/+layout.svelte:746-778`) — checks `localStorage.token` on every page load
- Token expiry polling every 15 seconds (`+layout.svelte:584-601`)

#### Where 2FA Settings Would Go

- **User-facing**: `src/lib/components/chat/Settings/Account.svelte` — between "Change Password" (line 246) and "API Keys" (line 252)
- **Admin-facing**: `src/lib/components/admin/Settings/General.svelte` — in the "Authentication" section (line 305)

### 4. Alembic Migration System

- Config: `backend/open_webui/alembic.ini` + `backend/open_webui/migrations/env.py`
- Current head: `2c5f92a9fd66` (add_knowledge_type_column)
- Migrations auto-run at startup via `config.py:70` calling `command.upgrade(alembic_cfg, "head")`
- Pattern: Use `op.add_column()` for new columns, `op.batch_alter_table()` for SQLite compatibility

### 5. Rate Limiting Infrastructure

The existing `RateLimiter` class (`utils/rate_limit.py`) is Redis-backed with in-memory fallback. Already used for signin (15 attempts/3min). Can be reused for 2FA attempt limiting.

---

## Proposed Implementation Architecture

### New Dependencies

| Library | Version | Purpose |
|---------|---------|---------|
| `pyotp` | 2.9.0 | TOTP generation/verification (RFC 6238) |
| `qrcode[pil]` | 8.0 | QR code generation for authenticator app enrollment |
| `aiosmtplib` | 3.0+ | Async SMTP client for email OTP (or use stdlib `smtplib` in thread pool) |

Note: `cryptography` (for AES-GCM encryption of TOTP secrets) and `bcrypt` (for hashing recovery codes) are already in the project.

### Database Schema Changes (1 migration)

```sql
-- Add to auth table
ALTER TABLE auth ADD COLUMN totp_secret TEXT;          -- AES-GCM encrypted base32 secret
ALTER TABLE auth ADD COLUMN totp_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE auth ADD COLUMN totp_last_used_at BIGINT;  -- Replay protection (timecode)

-- New table for recovery codes
CREATE TABLE recovery_code (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES auth(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,     -- bcrypt hashed
    used BOOLEAN DEFAULT FALSE,
    used_at BIGINT,
    created_at BIGINT NOT NULL
);
CREATE INDEX idx_recovery_code_user_id ON recovery_code(user_id);

-- For email OTP (if using DB storage instead of Redis)
CREATE TABLE email_otp (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES auth(id) ON DELETE CASCADE,
    otp_hash TEXT NOT NULL,     -- SHA-256 hashed (short-lived, rate-limited)
    attempts INTEGER DEFAULT 0,
    expires_at BIGINT NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at BIGINT NOT NULL
);
CREATE INDEX idx_email_otp_user_id ON email_otp(user_id);
```

### New API Endpoints

| Method | Route | Auth | Purpose |
|--------|-------|------|---------|
| `POST` | `/api/v1/auths/2fa/totp/setup` | Full JWT | Generate TOTP secret + QR code |
| `POST` | `/api/v1/auths/2fa/totp/enable` | Full JWT + password | Verify first code, activate, return recovery codes |
| `POST` | `/api/v1/auths/2fa/totp/disable` | Full JWT + password | Deactivate TOTP |
| `POST` | `/api/v1/auths/2fa/verify` | Partial JWT only | Verify TOTP/recovery code during login |
| `POST` | `/api/v1/auths/2fa/email/send` | Partial JWT only | Send email OTP |
| `POST` | `/api/v1/auths/2fa/email/verify` | Partial JWT only | Verify email OTP |
| `POST` | `/api/v1/auths/2fa/recovery/regenerate` | Full JWT + password | Generate new recovery codes |
| `GET`  | `/api/v1/auths/2fa/status` | Full JWT | Check 2FA enrollment status |

### Modified Existing Code

#### `backend/open_webui/routers/auths.py` — Signin endpoint modification

After password verification succeeds (line 586), check if user has 2FA enabled. If yes, return a partial response instead of a full session:

```python
# After successful password verification:
if auth_record.totp_enabled:
    partial_token = create_token(
        data={"id": user.id, "purpose": "2fa_pending"},
        expires_delta=timedelta(minutes=5),
    )
    return JSONResponse(status_code=200, content={
        "requires_2fa": True,
        "partial_token": partial_token,
        "methods": ["totp", "email", "recovery"],  # available 2FA methods
    })
# else: proceed with normal full token flow
```

#### `backend/open_webui/utils/auth.py` — Reject partial tokens

In `get_current_user()` (line 269), add a check:

```python
data = decode_token(token)
if data and data.get("purpose") == "2fa_pending":
    raise HTTPException(401, detail="2FA verification required")
```

### SMTP Configuration (New Environment Variables)

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=noreply@example.com
SMTP_PASSWORD=secret
SMTP_FROM=noreply@example.com
SMTP_FROM_NAME=Open WebUI
SMTP_USE_TLS=true
ENABLE_EMAIL_OTP=true
```

### Frontend Changes

#### New Components
- `src/lib/components/auth/TwoFactorChallenge.svelte` — TOTP/email code input during login
- `src/lib/components/chat/Settings/Account/TwoFactorSetup.svelte` — TOTP enrollment with QR code
- `src/lib/components/chat/Settings/Account/RecoveryCodes.svelte` — Display/regenerate recovery codes

#### Modified Components
- `src/routes/auth/+page.svelte` — Handle `requires_2fa` response, show 2FA challenge
- `src/lib/apis/auths/index.ts` — New API client functions for 2FA endpoints
- `src/lib/components/chat/Settings/Account.svelte` — Add 2FA section
- `src/lib/components/admin/Settings/General.svelte` — Admin 2FA enforcement toggle

### TOTP Secret Encryption

TOTP secrets must be **encrypted** (not hashed) at rest because the server needs plaintext to verify codes. Use AES-GCM with a key derived from `WEBUI_SECRET_KEY`:

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import hashlib, os, base64

def get_encryption_key() -> bytes:
    return hashlib.sha256(WEBUI_SECRET_KEY.encode()).digest()

def encrypt_secret(plaintext: str) -> str:
    key = get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ciphertext).decode()

def decrypt_secret(encrypted: str) -> str:
    key = get_encryption_key()
    aesgcm = AESGCM(key)
    raw = base64.b64decode(encrypted)
    return aesgcm.decrypt(raw[:12], raw[12:], None).decode()
```

The `cryptography` library is already a dependency (used for Fernet encryption of OAuth tokens in `models/oauth_sessions.py`).

---

## Security Considerations

### TOTP Best Practices
- **`valid_window=1`**: Accept codes from t-30s, t, and t+30s (handles clock drift up to ~89s)
- **Replay protection**: Store `totp_last_used_at` (timecode, not timestamp) and reject reused codes
- **Rate limit**: 5 TOTP attempts per 15-minute window using existing `RateLimiter`
- **Require password re-entry** to enable/disable 2FA

### Email OTP Best Practices
- **6 digits**, numeric only, 5-minute expiration
- **Max 3 attempts** per code, then require resend
- **60-second resend cooldown**, max 3 codes per 15 minutes
- **Hash** the OTP in storage (SHA-256 is sufficient given short TTL + rate limiting)
- **Constant-time comparison** via `hmac.compare_digest()`
- **Never reveal** whether the email exists

### Recovery Codes
- **10 codes**, 10 alphanumeric chars formatted as `XXXXX-XXXXX`
- **bcrypt-hashed** individually in DB (long-lived, unlike OTPs)
- **One-time use**: mark `used=True` + `used_at` after consumption
- Show plaintext **once** during 2FA setup, with "download as text file" option
- Regeneration requires password confirmation

### General
- Partial 2FA tokens must be **rejected by all normal endpoints** (critical)
- Admin should be able to **force-disable** a user's 2FA (for lockout recovery)
- **Audit log** 2FA enable/disable/recovery code use events

---

## Code References

- `backend/open_webui/routers/auths.py:510-634` — Signin endpoint (needs 2FA branching)
- `backend/open_webui/utils/auth.py:191-202` — `create_token()` (needs `purpose` field support)
- `backend/open_webui/utils/auth.py:269-364` — `get_current_user()` (needs partial token rejection)
- `backend/open_webui/models/auths.py:17-23` — Auth ORM model (needs new columns)
- `backend/open_webui/models/auths.py:114-134` — `authenticate_user()` (stays as-is)
- `backend/open_webui/utils/rate_limit.py:6` — `RateLimiter` class (reuse for 2FA)
- `backend/open_webui/models/oauth_sessions.py:76-105` — Fernet encryption pattern (reference for TOTP encryption)
- `backend/open_webui/config.py:53-70` — `run_migrations()` (auto-runs Alembic at startup)
- `backend/open_webui/migrations/versions/2c5f92a9fd66_*.py` — Current Alembic head
- `src/routes/auth/+page.svelte:59-102` — Login form submit handlers (needs 2FA response handling)
- `src/lib/apis/auths/index.ts:257-288` — `userSignIn()` API client (needs 2FA response handling)
- `src/lib/components/chat/Settings/Account.svelte:246-252` — Where 2FA settings UI would go
- `src/lib/components/admin/Settings/General.svelte:305-461` — Admin auth settings section
- `docs/SECURITY.md:119` — 2FA listed as feature request

## Architecture Insights

### Why Partial JWT (Not Session-Based)

Open WebUI is stateless (JWT-based, no server-side sessions). Using a short-lived JWT with `"purpose": "2fa_pending"` keeps the 2FA flow stateless too, fitting the existing architecture. The alternative (Redis-backed session for the intermediate state) would work but adds complexity for deployments without Redis.

### Email OTP Storage: Redis vs DB

If Redis is available, storing email OTPs in Redis with TTL is cleaner (auto-expiration, no cleanup needed). If not, a DB table with a periodic cleanup job works. The `RateLimiter` already handles this dual-mode pattern.

### TOTP vs Email OTP Priority

TOTP is the more valuable 2FA method (works offline, no email infrastructure needed). If the client wants to phase the implementation, **start with TOTP only** and add email OTP later. TOTP requires no external services beyond the authenticator app.

### Admin Override Considerations

An admin should be able to reset/disable 2FA for a locked-out user. This should go through the existing admin user management endpoint (`routers/users.py:541-548`), with an explicit `totp_enabled: false` update.

## Open Questions

1. ~~**Email provider**: For email OTP, should we use direct SMTP or an email service API?~~ **RESOLVED**: Microsoft Graph API via `no-reply@soev.ai` — client is building this infra separately.
2. ~~**Enforcement**: Should admins be able to require 2FA for all users?~~ **RESOLVED**: Yes, fine-grained admin settings with grace period and enforcement.
3. ~~**LDAP users**: Should 2FA also apply to LDAP-authenticated users?~~ **RESOLVED**: No. LDAP bypasses 2FA.
4. ~~**Trusted header auth**: Should 2FA be skipped for SSO?~~ **RESOLVED**: Yes. SSO/OAuth bypasses 2FA — managed by identity provider (e.g. Entra ID).
5. ~~**API key access**: Should API key authentication bypass 2FA?~~ **RESOLVED**: Yes. API keys bypass 2FA.
6. **Phase approach**: Should we implement TOTP first and email OTP as a follow-up? (Email infra coming soon via Graph API, so both could ship together.)

## Follow-up: Client Decisions (2026-02-16)

### Resolved Decisions

| Decision | Answer |
|----------|--------|
| **Email infrastructure** | Microsoft Graph API via `no-reply@soev.ai` (being built separately) |
| **2FA scope** | Email+password users ONLY |
| **LDAP bypass** | Yes — LDAP users skip 2FA |
| **SSO/OAuth bypass** | Yes — managed by identity provider (Entra ID, etc.) |
| **API key bypass** | Yes — API keys authenticate directly |
| **Trusted header bypass** | Yes — reverse proxy SSO skips 2FA |
| **Admin enforcement** | Fine-grained settings with grace period |

### Updated Bypass Logic

The 2FA challenge should only trigger in the **standard email+password signin branch** (`routers/auths.py:570-588`). The other auth paths are unaffected:

```
POST /api/v1/auths/signin (Branch C: standard email+password)
  → Password verified ✓
  → Check if user has 2FA enabled
    → YES: return partial_token + requires_2fa=true
    → NO: return full session (existing flow)

POST /api/v1/auths/signin (Branch A: trusted header) → NO 2FA
POST /api/v1/auths/signin (Branch B: auth disabled)  → NO 2FA
POST /api/v1/auths/ldap                              → NO 2FA
GET  /oauth/{provider}/callback                       → NO 2FA
Authorization: Bearer sk-*  (API key)                 → NO 2FA
```

### Admin Enforcement Model

Fine-grained admin settings for 2FA enforcement:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `ENABLE_2FA` | Boolean | `false` | Master toggle — enables 2FA feature globally |
| `REQUIRE_2FA` | Boolean | `false` | When true, all email+password users must set up 2FA |
| `2FA_GRACE_PERIOD_DAYS` | Integer | `7` | Days after enforcement before locking out non-compliant users |
| `2FA_METHODS` | List | `["totp"]` | Enabled 2FA methods: `totp`, `email` |

**Grace period flow:**
1. Admin enables `REQUIRE_2FA`
2. Users who haven't set up 2FA see a dismissible banner: "Your admin requires 2FA. Set it up before [date]."
3. After grace period expires, users without 2FA are redirected to a mandatory setup screen on login (can't dismiss)
4. Admin can always force-disable 2FA for locked-out users

### Email OTP via Graph API

Instead of SMTP, the email OTP will use the Microsoft Graph API `sendMail` endpoint (already being built for `no-reply@soev.ai`). This means:
- No `aiosmtplib` dependency needed
- Reuse the Graph API client being built in the OneDrive sync service
- Email sending is an authenticated API call, not a direct SMTP connection
- Better deliverability (Microsoft-authenticated sender)
