---
date: 2026-03-30
title: "TOTP 2FA — Phase 1 (Authenticator App)"
status: draft
branch: feat/totp-2fa
base: main
estimated_files: ~18
research: thoughts/shared/research/2026-02-16-2fa-email-totp-implementation.md
---

# TOTP 2FA — Phase 1 (Authenticator App)

## Goal

Allow email+password users to optionally enable TOTP-based two-factor authentication using any standard authenticator app (Microsoft Authenticator, Google Authenticator, Authy, etc.). Admins can enforce 2FA with a grace period. Phase 1 is TOTP-only — no email OTP.

## Scope Decisions (from prior research)

| Decision | Resolution |
|----------|-----------|
| 2FA scope | Email+password users ONLY |
| LDAP bypass | Yes |
| SSO/OAuth bypass | Yes — managed by identity provider |
| API key bypass | Yes |
| Trusted header bypass | Yes |
| Admin enforcement | `ENABLE_2FA` master toggle + `REQUIRE_2FA` + grace period |
| Recovery codes | 10 codes, bcrypt-hashed, one-time use |
| Email OTP | Deferred to Phase 2 |

## Success Criteria

1. User can enable TOTP in Account Settings → shown QR code scannable by Microsoft Authenticator
2. After enabling, signin requires a 6-digit TOTP code after password
3. Recovery codes work as fallback when authenticator is unavailable
4. Admin can toggle `ENABLE_2FA` (feature visibility) and `REQUIRE_2FA` (enforcement)
5. Grace period banner shown to non-compliant users when enforcement is active
6. Admin can force-disable 2FA for locked-out users
7. All bypass rules work (LDAP, SSO, API keys, trusted headers unaffected)
8. Feature is fully off by default (`ENABLE_2FA=false`)

---

## Implementation Steps

### Step 1: Dependencies

**File:** `pyproject.toml`

Add to the `dependencies` list (lines 8-121):
```
"pyotp==2.9.0",
"qrcode[pil]==8.0",
```

`pyotp` provides RFC 6238 TOTP generation/verification. `qrcode[pil]` generates QR codes for authenticator app enrollment. Both are small, well-maintained, no transitive dependency concerns.

---

### Step 2: Database Migration

**New file:** `backend/open_webui/migrations/versions/<hash>_add_totp_2fa.py`

**Down revision:** `a1b2c3d4e5f7` (current head)

Schema changes:

```sql
-- Add to auth table
ALTER TABLE auth ADD COLUMN totp_secret TEXT;           -- AES-GCM encrypted base32 secret
ALTER TABLE auth ADD COLUMN totp_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE auth ADD COLUMN totp_last_used_at BIGINT;   -- Replay protection (timecode)

-- New table for recovery codes
CREATE TABLE recovery_code (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES auth(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,      -- bcrypt hashed
    used BOOLEAN DEFAULT FALSE,
    used_at BIGINT,
    created_at BIGINT NOT NULL
);
CREATE INDEX idx_recovery_code_user_id ON recovery_code(user_id);
```

**Pattern:** Follow `2c5f92a9fd66_add_knowledge_type_column.py` — use `_column_exists()` guard for idempotent column additions. Use direct `op.add_column()` (not `batch_alter_table`) since we're only adding columns. Use `op.create_table()` for the recovery_code table.

Generate with:
```bash
cd backend/open_webui && alembic revision --autogenerate -m "add_totp_2fa"
```

Then review and adjust the generated migration.

---

### Step 3: Backend Models

#### 3a. Update Auth model

**File:** `backend/open_webui/models/auths.py`

Add columns to `Auth` SQLAlchemy model (after line 26):
```python
totp_secret = Column(Text, nullable=True)        # AES-GCM encrypted
totp_enabled = Column(Boolean, default=False)
totp_last_used_at = Column(BigInteger, nullable=True)
```

Add fields to `AuthModel` Pydantic model (after line 32):
```python
totp_enabled: bool = False
```

Note: `totp_secret` is intentionally excluded from the Pydantic model — it should never be serialized to the frontend.

Add methods to `AuthsTable`:
- `get_auth_by_user_id(user_id, db)` — needed by 2FA endpoints to check TOTP state
- `update_totp(user_id, totp_secret, totp_enabled, db)` — enable/disable TOTP
- `update_totp_last_used(user_id, timecode, db)` — replay protection update

#### 3b. New RecoveryCode model

**New file:** `backend/open_webui/models/recovery_codes.py`

```python
class RecoveryCode(Base):
    __tablename__ = 'recovery_code'
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('auth.id', ondelete='CASCADE'))
    code_hash = Column(Text, nullable=False)
    used = Column(Boolean, default=False)
    used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)

class RecoveryCodeModel(BaseModel):
    id: str
    user_id: str
    used: bool = False

class RecoveryCodesTable:
    async def generate_codes(user_id, db) -> list[str]:  # returns plaintext codes (show once)
    async def verify_code(user_id, code, db) -> bool:     # verifies + marks used
    async def delete_all(user_id, db):                     # for regeneration
    async def count_unused(user_id, db) -> int:            # for status display
```

---

### Step 4: TOTP Utilities

**New file:** `backend/open_webui/utils/totp.py`

```python
# AES-GCM encryption for TOTP secrets (reference: models/oauth_sessions.py Fernet pattern)
def get_encryption_key() -> bytes:
    """Derive AES-256 key from WEBUI_SECRET_KEY."""

def encrypt_secret(plaintext: str) -> str:
    """Encrypt TOTP base32 secret with AES-GCM. Returns base64(nonce + ciphertext)."""

def decrypt_secret(encrypted: str) -> str:
    """Decrypt AES-GCM encrypted TOTP secret."""

# TOTP operations
def generate_totp_secret() -> str:
    """Generate a new base32 TOTP secret via pyotp."""

def generate_provisioning_uri(secret: str, email: str, issuer: str = "soev.ai") -> str:
    """Generate otpauth:// URI for QR code."""

def generate_qr_code_base64(uri: str) -> str:
    """Generate QR code as base64 PNG data URI."""

def verify_totp(secret: str, code: str, last_used_at: int | None) -> tuple[bool, int | None]:
    """Verify TOTP code with valid_window=1 and replay protection.
    Returns (is_valid, new_timecode_if_valid)."""

# Recovery code operations
def generate_recovery_codes(count: int = 10) -> list[str]:
    """Generate formatted recovery codes (XXXXX-XXXXX)."""
```

**Design notes:**
- Use AES-GCM (not Fernet) for TOTP secrets — the prior research chose this because it's more standard and we're already using the `cryptography` library. Key derived from `WEBUI_SECRET_KEY` via SHA-256.
- `verify_totp` uses `pyotp.TOTP.verify(code, valid_window=1)` — accepts codes from t-30s, t, and t+30s (handles clock drift).
- Replay protection: compare `pyotp.TOTP.timecode(datetime.now())` against `totp_last_used_at`. Reject if same timecode was already used.

---

### Step 5: Feature Flags & Config

#### 5a. Environment variables

**File:** `backend/open_webui/config.py`

Add near existing feature flags (around line 1748):

```python
# 2FA / TOTP
ENABLE_2FA = PersistentConfig(
    'ENABLE_2FA',
    'auth.enable_2fa',
    os.environ.get('ENABLE_2FA', 'False').lower() == 'true',
)

REQUIRE_2FA = PersistentConfig(
    'REQUIRE_2FA',
    'auth.require_2fa',
    os.environ.get('REQUIRE_2FA', 'False').lower() == 'true',
)

TWO_FA_GRACE_PERIOD_DAYS = PersistentConfig(
    'TWO_FA_GRACE_PERIOD_DAYS',
    'auth.2fa_grace_period_days',
    int(os.environ.get('TWO_FA_GRACE_PERIOD_DAYS', '7')),
)
```

Use `PersistentConfig` (not plain env vars) so admins can toggle at runtime without redeployment.

#### 5b. App state

**File:** `backend/open_webui/main.py`

Import and set on `app.state.config` (near line 1269):
```python
app.state.config.ENABLE_2FA = ENABLE_2FA
app.state.config.REQUIRE_2FA = REQUIRE_2FA
app.state.config.TWO_FA_GRACE_PERIOD_DAYS = TWO_FA_GRACE_PERIOD_DAYS
```

Expose to frontend in `get_app_config()` (near line 2358, in the authenticated features dict):
```python
'enable_2fa': app.state.config.ENABLE_2FA,
'require_2fa': app.state.config.REQUIRE_2FA,
'two_fa_grace_period_days': app.state.config.TWO_FA_GRACE_PERIOD_DAYS,
```

Also expose in the unauthenticated section (near line 2296) — the login page needs to know if 2FA is enabled to handle the partial token response:
```python
'enable_2fa': app.state.config.ENABLE_2FA,
```

---

### Step 6: Backend 2FA Router

**New file:** `backend/open_webui/routers/totp.py`

Mount in `main.py` at `/api/v1/auths/2fa`.

#### Endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /status` | Full JWT | Returns `{ totp_enabled, recovery_codes_remaining }` |
| `POST /totp/setup` | Full JWT | Generates TOTP secret, returns `{ qr_code_base64, secret, provisioning_uri }` |
| `POST /totp/enable` | Full JWT + password | Verifies first TOTP code, activates, returns `{ recovery_codes: [...] }` |
| `POST /totp/disable` | Full JWT + password | Deactivates TOTP, deletes recovery codes |
| `POST /verify` | Partial JWT only | Verifies TOTP code or recovery code during login, returns full session |
| `POST /recovery/regenerate` | Full JWT + password | Deletes old codes, generates new ones |

#### Key implementation details

**`POST /totp/setup`** — generates a new secret but does NOT save it to DB yet. Returns the secret + QR code to the frontend. The secret is only persisted when `/totp/enable` is called with a valid verification code. This prevents half-configured states.

Approach: store the pending secret in the response and have the frontend send it back with the verification code in `/totp/enable`. The secret is encrypted before storage. Alternatively, use a short-lived cache (Redis or in-memory dict with TTL). The simpler approach is to return it and have the frontend send it back — it's a base32 string, not sensitive until stored.

**`POST /totp/enable`** request body:
```python
class TotpEnableForm(BaseModel):
    password: str       # re-verify password
    secret: str         # the secret from /setup
    code: str           # TOTP code to verify the secret works
```

**`POST /verify`** — this is the login completion endpoint. It accepts either a TOTP code or a recovery code:
```python
class TotpVerifyForm(BaseModel):
    code: str           # 6-digit TOTP code or XXXXX-XXXXX recovery code
```

Detection logic: if code matches `^\d{6}$` → TOTP verification. If code matches `^[A-Z0-9]{5}-[A-Z0-9]{5}$` → recovery code verification. Otherwise reject.

On success, issue a full session via `create_session_response()` (reuse the existing helper from `auths.py`).

**Rate limiting:** Create a new `RateLimiter` instance for 2FA verification — 5 attempts per 15 minutes per user. Use the existing `RateLimiter` class from `utils/rate_limit.py`.

---

### Step 7: Modify Signin Flow

**File:** `backend/open_webui/routers/auths.py`

**Target:** The standard email+password branch (lines 614-634), specifically after `Auths.authenticate_user()` returns a user (line 634) but before `create_session_response()` is called (line 637).

Insert 2FA check:

```python
# After line 634 (user = await Auths.authenticate_user(...))
# Before line 636 (if user:)

if user:
    # Check if 2FA is enabled for this user
    if request.app.state.config.ENABLE_2FA:
        auth_record = await Auths.get_auth_by_user_id(user.id, db)
        if auth_record and auth_record.totp_enabled:
            # Issue partial token instead of full session
            partial_token = create_token(
                data={"id": user.id, "purpose": "2fa_pending"},
                expires_delta=timedelta(minutes=5),
            )
            return JSONResponse(
                status_code=200,
                content={
                    "requires_2fa": True,
                    "partial_token": partial_token,
                    "methods": ["totp", "recovery"],
                },
            )
    # ... existing create_session_response() call
```

**File:** `backend/open_webui/utils/auth.py`

**Target:** `get_current_user()` (line 279), in the JWT validation path (around line 321).

Add partial token rejection after decoding:

```python
data = decode_token(token)
if data and data.get("purpose") == "2fa_pending":
    raise HTTPException(
        status_code=403,
        detail="2FA verification required"
    )
```

This ensures partial tokens can't access any normal endpoint — they can only be used at `POST /api/v1/auths/2fa/verify`.

---

### Step 8: Admin 2FA Management

#### 8a. Admin config endpoints

**File:** `backend/open_webui/routers/configs.py`

Add 2FA admin config GET/POST endpoints (follow the `ENABLE_AGENT_PROXY` pattern at line 860):

```python
class TwoFAConfigForm(BaseModel):
    ENABLE_2FA: bool
    REQUIRE_2FA: bool
    TWO_FA_GRACE_PERIOD_DAYS: int

@router.get('/2fa')
async def get_2fa_config(request, user=Depends(get_admin_user)):
    ...

@router.post('/2fa')
async def set_2fa_config(request, form_data: TwoFAConfigForm, user=Depends(get_admin_user)):
    ...
```

#### 8b. Admin force-disable endpoint

**File:** `backend/open_webui/routers/users.py`

Add an endpoint for admin to force-disable a user's 2FA (for lockout recovery):

```python
@router.post('/{user_id}/2fa/disable')
async def admin_disable_user_2fa(user_id, request, user=Depends(get_admin_user), db=...):
    # Clear totp_secret, set totp_enabled=False, delete recovery codes
```

This goes alongside the existing admin user management endpoints.

---

### Step 9: Frontend API Client

**File:** `src/lib/apis/auths/index.ts`

Add new functions following the existing pattern (fetch + error handling):

```typescript
export const get2FAStatus = async (token: string) => { ... }
export const setup2FATOTP = async (token: string) => { ... }
export const enable2FATOTP = async (token: string, password: string, secret: string, code: string) => { ... }
export const disable2FATOTP = async (token: string, password: string) => { ... }
export const verify2FA = async (partialToken: string, code: string) => { ... }
export const regenerateRecoveryCodes = async (token: string, password: string) => { ... }
```

**File:** `src/lib/apis/configs/index.ts`

Add admin config functions:
```typescript
export const get2FAConfig = async (token: string) => { ... }
export const set2FAConfig = async (token: string, config: object) => { ... }
```

---

### Step 10: Frontend — Login 2FA Challenge

**New file:** `src/lib/components/auth/TwoFactorChallenge.svelte`

A modal/inline component shown after `userSignIn()` returns `{ requires_2fa: true }`.

UI:
- Title: "Two-Factor Authentication"
- Subtitle: "Enter the 6-digit code from your authenticator app"
- 6-digit code input (auto-focus, numeric, auto-submit on 6th digit)
- "Use a recovery code" toggle → switches to alphanumeric input with `XXXXX-XXXXX` format
- Submit button
- Error display for invalid codes
- Back/cancel button → returns to login form

**File:** `src/routes/auth/+page.svelte`

Modify `signInHandler` (line 71):

```javascript
const signInHandler = async () => {
    const response = await userSignIn(email, password).catch((error) => {
        toast.error(`${error}`);
        return null;
    });

    if (response?.requires_2fa) {
        // Show 2FA challenge instead of completing login
        partialToken = response.partial_token;
        show2FAChallenge = true;
        return;
    }

    await setSessionUser(response);
};
```

Add `verify2FAHandler`:
```javascript
const verify2FAHandler = async (code: string) => {
    const sessionUser = await verify2FA(partialToken, code).catch((error) => {
        toast.error(`${error}`);
        return null;
    });
    if (sessionUser) {
        show2FAChallenge = false;
        await setSessionUser(sessionUser);
    }
};
```

Conditionally render `TwoFactorChallenge` when `show2FAChallenge` is true, instead of the normal login form.

---

### Step 11: Frontend — TOTP Setup in Account Settings

**New file:** `src/lib/components/chat/Settings/Account/TwoFactorSetup.svelte`

A self-contained component (following the `UpdatePassword.svelte` pattern) with three states:

**State 1: Not enrolled**
- "Two-Factor Authentication" heading with "Set Up" button
- Brief description: "Add an extra layer of security with an authenticator app"

**State 2: Setup flow** (after clicking "Set Up")
1. Call `setup2FATOTP()` → get QR code + secret
2. Show QR code image (scannable by Microsoft Authenticator)
3. Show secret as text (manual entry fallback)
4. Password re-entry field
5. Verification code input (6 digits)
6. "Enable" button → calls `enable2FATOTP(password, secret, code)`
7. On success → show recovery codes (State 3)

**State 3: Recovery codes display** (shown once after enabling)
- List of 10 recovery codes
- "Download as text file" button
- "I've saved these codes" checkbox + "Done" button
- Warning: "These codes will not be shown again"

**State 4: Already enrolled**
- "Two-Factor Authentication" heading with green "Enabled" badge
- "X recovery codes remaining" info
- "Regenerate recovery codes" button (requires password)
- "Disable 2FA" button (requires password)

**File:** `src/lib/components/chat/Settings/Account.svelte`

Add after the `UpdatePassword` section (after line 255), guarded by feature flag:

```svelte
{#if $config?.features.enable_2fa && $config?.features.enable_login_form}
    <hr class="border-gray-50 dark:border-gray-850" />
    <div class="mt-2">
        <TwoFactorSetup />
    </div>
{/if}
```

The `enable_login_form` guard ensures the section only shows for email+password users (not SSO-only setups).

---

### Step 12: Frontend — Admin 2FA Settings

**File:** `src/lib/components/admin/Settings/General.svelte`

Add a "Two-Factor Authentication" subsection in the Authentication section (after the JWT Expiration setting at line 493, before LDAP at line 495). Follow the existing toggle pattern:

```svelte
<!-- Two-Factor Authentication -->
<div class="mb-2.5 flex w-full justify-between pr-2">
    <div class="self-center text-xs font-medium">{$i18n.t('Enable Two-Factor Authentication')}</div>
    <Switch bind:state={adminConfig.ENABLE_2FA} />
</div>

{#if adminConfig.ENABLE_2FA}
    <div class="mb-2.5 flex w-full justify-between pr-2">
        <div class="self-center text-xs font-medium">{$i18n.t('Require 2FA for All Users')}</div>
        <Switch bind:state={adminConfig.REQUIRE_2FA} />
    </div>

    {#if adminConfig.REQUIRE_2FA}
        <div class="mb-2.5">
            <div class="text-xs font-medium mb-1">{$i18n.t('Grace Period (days)')}</div>
            <input type="number" min="0" max="90" bind:value={adminConfig.TWO_FA_GRACE_PERIOD_DAYS} ... />
        </div>
    {/if}
{/if}
```

Wire up to the 2FA admin config API endpoints for load/save.

---

### Step 13: Frontend — Enforcement Banner

**File:** `src/routes/+layout.svelte`

When `REQUIRE_2FA` is enabled and the user hasn't set up 2FA, show a dismissible banner:

```svelte
{#if $config?.features.require_2fa && !user2FAEnabled}
    <Banner type="warning">
        Your administrator requires two-factor authentication.
        <a href="/settings/account">Set it up now</a>
        {#if gracePeriodRemaining > 0}
            — {gracePeriodRemaining} days remaining
        {/if}
    </Banner>
{/if}
```

The `user2FAEnabled` status can be fetched via `GET /api/v1/auths/2fa/status` on layout mount (only when `enable_2fa` is true).

After grace period expires: redirect to a mandatory setup screen on login (non-dismissible). This can be a query param on the auth page (`?setup_2fa=required`) or a dedicated route.

---

### Step 14: Frontend — Admin User 2FA Management

**File:** `src/lib/components/admin/Users/` (or wherever the admin user detail view is)

Add a "Disable 2FA" button visible to admins when viewing a user who has 2FA enabled. Calls the admin force-disable endpoint from Step 8b.

---

### Step 15: i18n

**File:** `src/lib/i18n/locales/en-US/translation.json`

Add translation keys (alphabetically sorted):

```json
"Disable Two-Factor Authentication": "",
"Enable Two-Factor Authentication": "",
"Enter the 6-digit code from your authenticator app": "",
"Grace Period (days)": "",
"I've saved these codes": "",
"Recovery Codes": "",
"Regenerate Recovery Codes": "",
"Require 2FA for All Users": "",
"Scan this QR code with your authenticator app": "",
"Set Up Two-Factor Authentication": "",
"Two-Factor Authentication": "",
"Two-Factor Authentication Enabled": "",
"Use a recovery code": "",
"Your administrator requires two-factor authentication.": ""
```

---

### Step 16: Helm Chart

**File:** Helm values (if applicable to this repo)

Add environment variable support:
```yaml
ENABLE_2FA: "false"
REQUIRE_2FA: "false"
TWO_FA_GRACE_PERIOD_DAYS: "7"
```

---

## File Summary

| # | File | Action | Type |
|---|------|--------|------|
| 1 | `pyproject.toml` | Edit | deps |
| 2 | `backend/.../migrations/versions/<hash>_add_totp_2fa.py` | Create | migration |
| 3 | `backend/.../models/auths.py` | Edit | model |
| 4 | `backend/.../models/recovery_codes.py` | Create | model |
| 5 | `backend/.../utils/totp.py` | Create | utility |
| 6 | `backend/.../config.py` | Edit | config |
| 7 | `backend/.../main.py` | Edit | config exposure |
| 8 | `backend/.../routers/totp.py` | Create | API endpoints |
| 9 | `backend/.../routers/auths.py` | Edit | signin flow |
| 10 | `backend/.../utils/auth.py` | Edit | partial token rejection |
| 11 | `backend/.../routers/configs.py` | Edit | admin config |
| 12 | `backend/.../routers/users.py` | Edit | admin force-disable |
| 13 | `src/lib/apis/auths/index.ts` | Edit | API client |
| 14 | `src/lib/apis/configs/index.ts` | Edit | admin API client |
| 15 | `src/lib/components/auth/TwoFactorChallenge.svelte` | Create | login 2FA UI |
| 16 | `src/routes/auth/+page.svelte` | Edit | login flow |
| 17 | `src/lib/components/chat/Settings/Account/TwoFactorSetup.svelte` | Create | setup UI |
| 18 | `src/lib/components/chat/Settings/Account.svelte` | Edit | mount setup component |
| 19 | `src/lib/components/admin/Settings/General.svelte` | Edit | admin toggles |
| 20 | `src/routes/+layout.svelte` | Edit | enforcement banner |
| 21 | `src/lib/i18n/locales/en-US/translation.json` | Edit | translations |

## Implementation Order

The steps are ordered for incremental testability:

1. **Foundation** (Steps 1-5): deps, migration, models, utils, config — can be tested with unit tests
2. **Backend API** (Steps 6-8): 2FA router, signin modification, admin endpoints — testable via curl/Postman
3. **Frontend** (Steps 9-14): API client, login challenge, setup UI, admin UI — full integration testing
4. **Polish** (Steps 15-16): i18n, Helm

Each step builds on the previous. The backend can be fully tested before touching the frontend.

## Security Checklist

- [ ] TOTP secrets encrypted at rest with AES-GCM
- [ ] Recovery codes bcrypt-hashed individually
- [ ] Partial JWT tokens rejected by all normal endpoints
- [ ] Partial tokens expire in 5 minutes
- [ ] TOTP verification rate-limited (5 attempts / 15 min)
- [ ] Replay protection via timecode tracking
- [ ] Password re-entry required to enable/disable 2FA
- [ ] `valid_window=1` for TOTP (handles ~89s clock drift)
- [ ] Admin can force-disable for lockout recovery
- [ ] Feature fully off by default

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Users locked out of accounts | Recovery codes + admin force-disable |
| Clock drift between server and phone | `valid_window=1` accepts ±30s |
| WEBUI_SECRET_KEY rotation breaks encrypted secrets | Document in deployment guide; provide re-encryption utility if needed |
| Upstream adds 2FA differently | Our implementation is additive (new files + minimal edits to existing); reconciliation would be straightforward |
