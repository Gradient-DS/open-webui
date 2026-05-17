# Hybrid SSO Invite + Auto-Provisioning Implementation Plan

## Overview

Wire the existing email-invite system into the OAuth signup path so that admins on multitenant EntraID SSO orgs can invite users by email; on first SSO login, the user is auto-provisioned with the role from their pending invite — no password ever set. Existing domain-allowlist and password-acceptance paths keep working. Hybrid, not a replacement. Fully configurable via `.env` and Helm chart values.

**Pain we're fixing:** Today the admin "Add user" form (`/admin/users/overview`) requires a password. SSO users authenticate against EntraID, never against that locally stored password, so they hit "wrong password" on first login. The whole password ceremony is dead weight in SSO orgs.

## Current State Analysis

Two relevant subsystems already exist independently and don't talk to each other:

1. **OAuth auto-provisioning works.** `backend/open_webui/utils/oauth.py:1540-1592` creates a user on first OAuth login when `ENABLE_OAUTH_SIGNUP=True`, using a random UUID password (line 1566) that's never used. The role assignment falls back to `DEFAULT_USER_ROLE` or whatever Entra role-claims dictate via `ENABLE_OAUTH_ROLE_MANAGEMENT`. There's a domain-level allowlist gate at lines 1478-1483.

2. **Invite system exists but is password-only.** `backend/open_webui/routers/invites.py` lets admins create invites (with role + email), Graph API sends an HTML email, the user clicks the token link → `src/routes/auth/invite/[token]/+page.svelte` → password form → `POST /invites/{token}/accept` (lines 228-337) creates the user. The accept endpoint hard-requires a password (line 263).

3. **The admin "Add user" modal (`AddUserModal.svelte:151-158`) already conditionally renders an `InviteUserForm` when `$config?.features?.enable_email_invites` is on**, falling back to the password form. So the UX scaffolding is already split.

4. **Frontend SSO detection already exists.** `$config?.oauth?.providers?.microsoft` is already used at `src/routes/auth/+page.svelte:488-519` to render the "Continue with Microsoft" button. We can reuse this exact predicate.

5. **The Helm chart is at `helm/open-webui-tenant/`** (NOT `kubernetes/helm/...` as I assumed in the brief). Env vars flow: `values.yaml` → `templates/open-webui/configmap.yaml` → `envFrom: configMapRef` in `templates/open-webui/deployment.yaml`. No helper transformation layer.

### Key Discoveries

- OAuth signup passes through a domain allowlist at `utils/oauth.py:1478-1483` *before* the user-lookup branch, meaning today the domain check applies to existing users too. We must keep that semantics for the existing-user branch but allow an invite to bypass it for new users (the invite IS the explicit grant, more specific than a domain rule).
- `Invites.get_pending_invite_by_email` at `models/invites.py:98-110` filters `accepted_at` and `revoked_at` but **does NOT check `expires_at`**. Expired invites still come back as "pending". This is a latent bug in the password-flow too (the router checks expiry separately at `routers/invites.py:204-208`); we'll fix it at the model layer with a new active-only method so both flows benefit.
- `Invites.accept_invite(token)` at `models/invites.py:112-120` is a non-conditional UPDATE; two concurrent accepts (e.g., user clicks link AND signs in via OAuth at the same time) both succeed. The downstream `Auths.insert_new_auth` would fail on the email-unique constraint, but the error path is unclear. We'll switch to a conditional UPDATE (`WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now`) returning rowcount.
- All English invite-related i18n keys exist as keys but with empty `""` values (use-key-as-fallback). All Dutch translations are already populated. We need to ADD ~6 new keys for the SSO branch and FILL ~5 existing empty en-US values that we'll touch.
- No backend tests exist for `routers/invites.py`. Adding a `test_invites.py` is in scope; the minimum bar is the new SSO path.
- `.env.example` has no OAuth/email-invite vars documented today. Adding them is in scope.

## Desired End State

After this plan:
- Admin opens `/admin/users` → "Add user" → fills email, name, optional role → clicks "Send invitation" → user receives a Graph API email with a "Sign in with Microsoft" CTA → clicks → Entra login → lands logged-in to the platform with the role from the invite. Zero password handling end-to-end.
- A new env var `OAUTH_INVITE_REQUIRED` (default `false`) lets ops lock down a deployment to invite-only OAuth signup. `false` keeps the existing domain-allowlist behavior as a fallback.
- The token-link accept page (`/auth/invite/{token}`) still works for password-fallback orgs (`ENABLE_OAUTH_SIGNUP=False`) AND offers a one-click SSO button for SSO orgs.
- All existing Open WebUI deployments that don't flip the new flag behave **identically** to today.
- Helm `values.yaml` exposes the new flag; `.env.example` documents it.

### Verification

The behavior matrix below must hold (rows = configuration, columns = inputs, cells = outcome):

| `ENABLE_OAUTH_SIGNUP` | `OAUTH_INVITE_REQUIRED` | `OAUTH_ALLOWED_DOMAINS` | Has pending invite? | Domain matches? | Outcome |
| --- | --- | --- | --- | --- | --- |
| false | * | * | * | * | OAuth signup denied (existing) — only pre-existing users can OAuth-login |
| true | false | `*` | yes | * | Provision, role from invite, mark invite accepted |
| true | false | `*` | no | * | Provision, role from claim/default (existing behavior) |
| true | false | `soev.ai` | yes | yes | Provision via invite (invite accepted) |
| true | false | `soev.ai` | yes | no | Provision via invite (invite bypasses domain check) |
| true | false | `soev.ai` | no | yes | Provision via domain allowlist (existing behavior) |
| true | false | `soev.ai` | no | no | Denied (existing behavior) |
| true | true | * | yes | * | Provision via invite |
| true | true | * | no | * | Denied — invite required |

Existing users (matched by `oauth.sub` or by email if `OAUTH_MERGE_ACCOUNTS_BY_EMAIL=True`) skip the signup branch entirely and continue to obey the existing domain check at `utils/oauth.py:1478-1483` — no change.

## What We're NOT Doing

- **Not building a parallel email-allowlist mechanism.** Considered, rejected. Reuses Invite scaffolding instead.
- **Not changing the EntraID multitenant configuration.** `MICROSOFT_CLIENT_TENANT_ID` and friends are out of scope.
- **Not migrating the invite-acceptance UI to a wizard or restructuring the existing flow.** We add a conditional SSO button branch; we don't rebuild the page.
- **Not adding admin UI for managing the new flag.** It's an `.env`/Helm setting only; admin-panel toggle can come later if requested.
- **Not adding frontend vitest tests for AddUserModal or the invite page.** None exist today; not adding new ones for this work.
- **Not changing how `Auths.insert_new_auth` is called from OAuth.** We continue to use a random UUID password — that pattern is established and works.

## Implementation Approach

Three phases, each independently shippable. Phase 1 is gated entirely behind the new flag (default off) → safe to merge in isolation. Phase 2 lights up the UX. Phase 3 backfills the test gap that turned out to exist for the entire invite system.

---

## Phase 1: Backend Logic & Config Plumbing

### Overview

Add the `OAUTH_INVITE_REQUIRED` flag, wire invite consumption into the OAuth signup branch, fix the expiry filtering and race-window in the invite model, and surface the flag through `.env.example` + Helm.

### Changes Required

#### 1. New PersistentConfig flag

**File**: `backend/open_webui/config.py`
**Change**: Add `OAUTH_INVITE_REQUIRED` next to `ENABLE_OAUTH_SIGNUP` (around line 326).

```python
OAUTH_INVITE_REQUIRED = PersistentConfig(
    'OAUTH_INVITE_REQUIRED',
    'oauth.invite_required',
    os.environ.get('OAUTH_INVITE_REQUIRED', 'False').lower() == 'true',
)
```

#### 2. Wire flag into `auth_manager_config`

**File**: `backend/open_webui/utils/oauth.py`
**Change**: Import the new flag (around L36-67), assign it to `auth_manager_config` (around L113-138).

```python
from open_webui.config import (
    ...,
    ENABLE_OAUTH_SIGNUP,
    OAUTH_INVITE_REQUIRED,  # NEW
    ...,
)

auth_manager_config.OAUTH_INVITE_REQUIRED = OAUTH_INVITE_REQUIRED  # NEW, near L115
```

#### 3. Add active-only invite lookup (fixes expiry leak + supports race-safety)

**File**: `backend/open_webui/models/invites.py`
**Change**: Add `Invite.expires_at > int(time.time())` to `get_pending_invite_by_email` (line 98-110). Add a new `consume_invite_by_email(email)` method that performs a conditional UPDATE in a single SQL statement and returns the consumed invite or None if it was already taken/expired/revoked.

```python
def get_pending_invite_by_email(self, email: str) -> Optional[InviteModel]:
    with get_db() as db:
        invite = (
            db.query(Invite)
            .filter(
                Invite.email == email.lower(),
                Invite.accepted_at.is_(None),
                Invite.revoked_at.is_(None),
                Invite.expires_at > int(time.time()),  # NEW
            )
            .order_by(Invite.created_at.desc())
            .first()
        )
        return InviteModel.model_validate(invite) if invite else None

def consume_invite_by_email(self, email: str) -> Optional[InviteModel]:
    """Atomically mark the active pending invite for this email as accepted.
    Returns the consumed invite, or None if no active invite existed
    (already accepted, revoked, expired, or never existed)."""
    now = int(time.time())
    with get_db() as db:
        rows = (
            db.query(Invite)
            .filter(
                Invite.email == email.lower(),
                Invite.accepted_at.is_(None),
                Invite.revoked_at.is_(None),
                Invite.expires_at > now,
            )
            .order_by(Invite.created_at.desc())
            .with_for_update(skip_locked=True)  # PG-friendly; falls through on SQLite
            .first()
        )
        if not rows:
            return None
        rows.accepted_at = now
        db.commit()
        db.refresh(rows)
        return InviteModel.model_validate(rows)
```

Note on `with_for_update(skip_locked=True)`: SQLite ignores it (single-writer model already serializes), Postgres uses it. Either way, the email-unique constraint on `Auths.insert_new_auth` is the ultimate backstop.

#### 4. Restructure OAuth signup branch

**File**: `backend/open_webui/utils/oauth.py`
**Change**: Modify the signup branch (lines 1540-1592) and weaken the domain-allowlist check at lines 1478-1483 to bypass when an active invite exists. The existing-user branch is untouched.

Replace lines 1478-1483 with:

```python
# Lookup active pending invite once — used as both an allowlist bypass
# and as the source of truth for role/name when provisioning.
pending_invite = Invites.get_pending_invite_by_email(email)

# Domain allowlist applies unless an explicit invite grants access.
if (
    not pending_invite
    and '*' not in auth_manager_config.OAUTH_ALLOWED_DOMAINS
    and email.split('@')[-1] not in auth_manager_config.OAUTH_ALLOWED_DOMAINS
):
    log.warning('OAuth callback failed, e-mail domain is not in the list of allowed domains')
    raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)
```

Replace lines 1540-1592 (the `else` branch of the user-existence check) with:

```python
else:
    # User does not exist — decide whether to provision.
    consumed_invite = None
    if pending_invite:
        # Race-safe consume — None means a concurrent request beat us to it.
        consumed_invite = Invites.consume_invite_by_email(email)
        if not consumed_invite:
            # Either consumed by parallel password-flow or expired between
            # the lookup and the consume. Fall through to non-invite path.
            pass

    if not consumed_invite:
        if auth_manager_config.OAUTH_INVITE_REQUIRED:
            log.warning(
                'OAuth signup denied — OAUTH_INVITE_REQUIRED is set and no active invite found for %s',
                email,
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        if not auth_manager_config.ENABLE_OAUTH_SIGNUP:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

    # Existing email-uniqueness guard — concurrent invite-accept could
    # have created the user between our checks; treat that as success.
    existing_user = Users.get_user_by_email(email, db=db)
    if existing_user:
        # Attach OAuth sub to the just-created user (covers race window
        # where the password-accept flow created the user concurrently).
        Users.update_user_oauth_by_id(existing_user.id, provider, sub, db=db)
        user = existing_user
    else:
        picture_claim = auth_manager_config.OAUTH_PICTURE_CLAIM
        if picture_claim:
            picture_url = user_data.get(
                picture_claim,
                OAUTH_PROVIDERS[provider].get('picture_url', ''),
            )
            picture_url = await self._process_picture_url(picture_url, token.get('access_token'))
        else:
            picture_url = '/user.png'
        username_claim = auth_manager_config.OAUTH_USERNAME_CLAIM
        name = user_data.get(username_claim)
        if not name:
            # Prefer the invite-supplied display name over the bare email
            name = consumed_invite.name if consumed_invite else email

        # Invite-supplied role wins over OAuth role-claim at provisioning time.
        # Subsequent logins still let ENABLE_OAUTH_ROLE_MANAGEMENT reconcile.
        role = consumed_invite.role if consumed_invite else self.get_user_role(None, user_data)

        user = Auths.insert_new_auth(
            email=email,
            password=get_password_hash(str(uuid.uuid4())),  # Random password, not used
            name=name,
            profile_image_url=picture_url,
            role=role,
            oauth=oauth_data,
            db=db,
        )

        if auth_manager_config.WEBHOOK_URL:
            await post_webhook(
                WEBUI_NAME,
                auth_manager_config.WEBHOOK_URL,
                WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
                {
                    'action': 'signup',
                    'message': WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
                    'user': user.model_dump_json(exclude_none=True),
                },
            )

        apply_default_group_assignment(request.app.state.config.DEFAULT_GROUP_ID, user.id, db=db)
```

Add the imports at the top of `oauth.py` (alongside the existing `Users`/`Auths`/`Groups` imports):

```python
from open_webui.models.invites import Invites
```

#### 5. Also consume invites on the merge-by-email path

**File**: `backend/open_webui/utils/oauth.py`
**Change**: At lines 1488-1494, after `Users.update_user_oauth_by_id` succeeds (existing user matched by email and OAuth sub attached), also fire `Invites.consume_invite_by_email(email)` and ignore the return — keeps admin's pending-invites list clean when an existing legacy password user OAuth-logs in for the first time.

```python
if user:
    # ... existing update_user_oauth_by_id ...
    # If there was a pending invite, mark it consumed too (housekeeping).
    Invites.consume_invite_by_email(email)
```

Place this inside the `if auth_manager_config.OAUTH_MERGE_ACCOUNTS_BY_EMAIL:` block at lines 1489-1494, only after the merge succeeds.

#### 6. `.env.example` documentation

**File**: `.env.example`
**Change**: Append a new section documenting the OAuth + invite flags. Use the `# OAUTH_INVITE_REQUIRED=false` commented-default style.

```bash
# --- OAuth / SSO (Microsoft EntraID) ---
# Enable OAuth signup (auto-create users on first SSO login)
# ENABLE_OAUTH_SIGNUP=false
# Restrict OAuth signup to invited emails only.
# When true, only emails with a valid pending invite can sign up via OAuth.
# When false (default), domain-allowlist + invite both grant access (hybrid).
# OAUTH_INVITE_REQUIRED=false
# Comma-separated allowed domains (e.g. soev.ai,example.org). '*' means any.
# OAUTH_ALLOWED_DOMAINS=*

# --- Email Invites (Microsoft Graph API) ---
# ENABLE_EMAIL_INVITES=false
# EMAIL_GRAPH_TENANT_ID=
# EMAIL_GRAPH_CLIENT_ID=
# EMAIL_GRAPH_CLIENT_SECRET=
# EMAIL_FROM_ADDRESS=no-reply@soev.ai
# EMAIL_FROM_NAME=Soev
# INVITE_EXPIRY_HOURS=168
```

#### 7. Helm chart wiring

**File**: `helm/open-webui-tenant/values.yaml`
**Change**: Add `oauthInviteRequired: "false"` in the Authentication block (around line 192, after `enableOauthSignup`).

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
**Change**: Add a single line after `ENABLE_OAUTH_SIGNUP` (around line 33).

```yaml
OAUTH_INVITE_REQUIRED: {{ .Values.openWebui.config.oauthInviteRequired | quote }}
```

No deployment.yaml changes — `envFrom: configMapRef` ingests it automatically.

### Success Criteria

#### Automated Verification

- [x] Backend imports cleanly: `cd backend && python -c "from open_webui.utils.oauth import OAuthManager"`
- [x] Linting passes: `npm run lint:backend` (only pre-existing pylint errors remain — `Exception.detail`, `func.now`, `redis.Redis` — unrelated to this change)
- [x] Black formatting clean: `npm run format:backend` then `git diff --quiet`
- [x] Helm template renders without error: `helm template helm/open-webui-tenant --set openWebui.config.oauthInviteRequired=true | grep OAUTH_INVITE_REQUIRED`
- [x] Existing pytest tests still pass: `pytest backend/open_webui/test/` (158 unit tests pass; 5 pre-existing Postgres/Redis integration-test collection errors are unrelated)

#### Manual Verification

- [ ] With `ENABLE_OAUTH_SIGNUP=False` and no flag changes, an existing OAuth login flow behaves exactly as before (regression check)
- [ ] With `ENABLE_OAUTH_SIGNUP=True`, `OAUTH_INVITE_REQUIRED=False`, `OAUTH_ALLOWED_DOMAINS=*`, and a pending invite for `test@example.com` with role `admin`: signing in via Microsoft as `test@example.com` provisions the user with role `admin` and the invite shows as accepted in `/admin/users` invite list
- [ ] Same setup but no pending invite: still provisions (existing behavior preserved)
- [ ] With `OAUTH_INVITE_REQUIRED=True` and no pending invite: SSO login is denied with 403
- [ ] With `OAUTH_ALLOWED_DOMAINS=soev.ai` and a pending invite for `external@other.com`: SSO login succeeds (invite bypasses domain check)
- [ ] With `OAUTH_ALLOWED_DOMAINS=soev.ai` and NO pending invite for `external@other.com`: SSO login is denied (existing domain-allowlist behavior preserved)
- [ ] Race scenario: open password-accept page in browser tab A, sign in via OAuth in tab B simultaneously — exactly one user is created, both tabs end logged in (or one shows "already accepted")
- [ ] Expired invite + SSO login: behaves as if no invite (falls through to domain allowlist or `OAUTH_INVITE_REQUIRED` denial)

**Implementation note**: Pause for manual verification before Phase 2.

---

## Phase 2: Frontend & Email Template

### Overview

Make the user-facing surfaces SSO-aware: invite-acceptance page offers SSO button, AddUserModal copy reflects SSO mode, the invite email gets an SSO-aware CTA, and i18n strings exist in both languages.

### Changes Required

#### 1. Invite-accept page — SSO button

**File**: `src/routes/auth/invite/[token]/+page.svelte`
**Change**: Above the existing password form (around line 167), add a conditional block that renders "Sign in with Microsoft" when `$config?.oauth?.providers?.microsoft` is truthy. Both options can coexist — SSO button on top, "or set a password" expander below — but for SSO-strict orgs (`!$config?.features?.enable_login_form` or similar), hide the password form entirely.

Outline:

```svelte
{#if $config?.oauth?.providers?.microsoft}
  <button
    type="button"
    on:click={() => {
      window.location.href = `${WEBUI_BASE_URL}/oauth/microsoft/login`;
    }}
    class="..."
  >
    {$i18n.t('Continue with {{provider}}', { provider: 'Microsoft' })}
  </button>

  {#if $config?.features?.enable_login_form}
    <div class="separator">{$i18n.t('or')}</div>
    <!-- existing password form -->
  {/if}
{:else}
  <!-- existing password form unchanged -->
{/if}
```

The SSO branch trusts the backend Phase 1 logic to consume the invite on callback — the frontend just kicks off the OAuth flow.

#### 2. Email template — SSO CTA

**File**: `backend/open_webui/services/email/graph_mail_client.py`
**Change**: Extend `render_invite_email` to take an `oauth_signup_enabled: bool = False` parameter (passed in from `routers/invites.py`). When true, change the button copy and link target.

- Button text: in `_STRINGS`, add `button_sso` (`'Sign in with Microsoft'` / `'Aanmelden met Microsoft'`).
- Body text: replace "Click the button below to **create your account**" with "Click the button below to **sign in and activate your account**" when SSO is on.
- Link target: still `invite_url` (the token-link page) — the page renders the SSO button from #1, so the user lands there and one-clicks. This keeps a paper trail per-invite (the token gets validated even though we don't use it for password-set), and it's the URL admins can copy from the invite list.

In `routers/invites.py:140-147` and `:416-423` (`render_invite_email` calls), pass `oauth_signup_enabled=request.app.state.config.ENABLE_OAUTH_SIGNUP`.

#### 3. AddUserModal — SSO copy

**File**: `src/lib/components/admin/Users/UserList/AddUserModal.svelte`
**Change**: When `$config?.oauth?.providers?.microsoft` is set AND `$config?.features?.enable_email_invites` is set, change helper copy in the existing `InviteUserForm` branch (lines 151-158) to make the SSO behavior explicit:

```svelte
{#if $config?.features?.enable_email_invites}
  {#if $config?.oauth?.providers?.microsoft}
    <p class="help-text">{$i18n.t('User invitations are managed via your SSO provider')}</p>
  {/if}
  <InviteUserForm bind:this={inviteFormRef} ... />
{:else}
  <!-- password form -->
{/if}
```

No structural changes — just an additional helper line.

If `InviteUserForm.svelte` exposes a password field, hide it when SSO is on. (If it doesn't — confirm during implementation — no further changes needed.)

#### 4. i18n: en-US

**File**: `src/lib/i18n/locales/en-US/translation.json`
**Change**: Add new keys (alphabetically sorted) and fill empty values for keys we now actually render. With en-US, an empty string means "use the key as-is", so empty values are valid; we only fill where the literal key isn't the desired display string.

New keys:
- `"Sign in with Microsoft": ""`
- `"Set a password": ""`
- `"User invitations are managed via your SSO provider": ""`
- `"An invitation will be sent to this email address": ""`
- `"or": ""`

Existing-key audit (already empty, no change needed): `"Accept Invite"`, `"Continue with {{provider}}"`, `"You've been invited to join"`.

#### 5. i18n: nl-NL

**File**: `src/lib/i18n/locales/nl-NL/translation.json`
**Change**: Add Dutch translations for the new keys.

```json
"Sign in with Microsoft": "Aanmelden met Microsoft",
"Set a password": "Wachtwoord instellen",
"User invitations are managed via your SSO provider": "Gebruikersuitnodigingen worden beheerd via je SSO-provider",
"An invitation will be sent to this email address": "Er wordt een uitnodiging verstuurd naar dit e-mailadres",
"or": "of"
```

(Final wording can be polished by Lex during review — the keys are what locks in.)

### Success Criteria

#### Automated Verification

- [x] Frontend type-checks: `npm run check` (only pre-existing errors; new lines just propagate the existing `getContext('i18n')` typing pattern)
- [x] Linting passes: `npm run lint:frontend` (only pre-existing CSS/unused-import errors in changed files)
- [x] Frontend builds: `npm run build` (53s, 0 errors)
- [x] i18n parse runs cleanly: `npm run i18n:parse` (parser stripped two unused proposed keys — `Set a password`, `An invitation will be sent to this email address` — they were never used in source; the keys actually rendered in code are present in both locales)

#### Manual Verification

- [ ] With Microsoft OAuth configured + `ENABLE_OAUTH_SIGNUP=True` + `ENABLE_EMAIL_INVITES=True`: navigate to `/admin/users`, click "Add user", form shows the invite layout with SSO helper text. Submit with `test@example.com`.
- [ ] Email arrives at the address with the "Sign in with Microsoft" button (English locale; flip browser to Dutch and resend → "Aanmelden met Microsoft")
- [ ] Click the button → lands on `/auth/invite/{token}` → SSO button is shown → click → Entra login → land in app, logged in as the invited user with the right role
- [ ] With Microsoft OAuth NOT configured: invite-accept page shows the original password form unchanged
- [ ] With both configured but `ENABLE_OAUTH_SIGNUP=False`: invite email + accept page show the password form (SSO is rendered as available login but invite consumption requires the password path)
- [ ] Browser locale switching updates all new strings in both en-US and nl-NL

**Implementation note**: Pause for manual verification before Phase 3.

---

## Phase 3: Tests

### Overview

Backfill the test gap: there are no tests for `routers/invites.py` and no tests for the OAuth callback flow. Add the minimum bar: cover the new SSO-invite path and the existing token-link path.

### Changes Required

**Implementation note**: The upstream test scaffolding under `backend/open_webui/test/apps/webui/routers/` requires `AbstractPostgresTest` + a `test.util.mock_user` package that doesn't exist in the soev fork — those upstream tests are collection errors today and don't run. Instead of placing new tests in that broken location, the new tests live under `backend/open_webui/test/util/` (where the soev unit-test pattern actually runs) using in-memory SQLite + monkeypatching.

#### 1. Model-level tests — `test/util/test_invites_model.py`

Direct exercise of `InviteTable.consume_invite_by_email` and the new expiry filter on `get_pending_invite_by_email`:

- `test_returns_invite_and_marks_accepted`
- `test_idempotent_second_call_returns_none` (race-safety)
- `test_returns_none_when_already_accepted`
- `test_returns_none_when_revoked`
- `test_returns_none_when_expired`
- `test_returns_none_when_email_unknown`
- `test_email_match_is_case_insensitive`
- `test_get_pending_invite_returns_active_invite`
- `test_get_pending_invite_filters_out_expired/accepted/revoked`

#### 2. OAuth + invite integration — `test/util/test_oauth_invite.py`

Drives `OAuthManager.handle_callback` end-to-end on the new-user branch with the authlib client mocked. Covers the matrix in the verification table:

- `test_oauth_signup_consumes_pending_invite_and_uses_invite_role`
- `test_oauth_signup_with_invite_required_and_no_invite_denied`
- `test_invite_bypasses_domain_allowlist`
- `test_no_invite_no_signup_falls_back_to_domain_check`
- `test_expired_invite_falls_through_to_domain_check`
- `test_oauth_role_invite_takes_precedence_over_default`

#### 3. Email rendering — `test/util/test_invite_email_render.py`

Pins the new `oauth_signup_enabled` SSO copy and the existing password-flow copy in both en and nl:

- `test_renders_password_copy_when_oauth_signup_disabled`
- `test_renders_sso_copy_when_oauth_signup_enabled`
- `test_renders_dutch_sso_copy`
- `test_renders_dutch_password_copy_by_default`

#### Tests intentionally not added

The original plan listed router-level tests for `routers/invites.py` (`test_create_invite_*`, `test_validate_invite_*`, `test_accept_invite_*`, `test_resend_invite_*`, `test_revoke_invite_*`). These are pre-existing endpoints not modified by this work, and adding them in this PR would mean either (a) waking the broken upstream `AbstractPostgresTest` harness (out of scope) or (b) reproducing the auth+DB+Graph-mail mocking from scratch in the fork's pattern — which is a separate test-infra cleanup task. The new tests cover every code path *added or modified* by this work.

### Success Criteria

#### Automated Verification

- [x] All new tests pass: `pytest test/util/test_invite_email_render.py test/util/test_invites_model.py test/util/test_oauth_invite.py -v` (21/21 pass from `backend/open_webui/`)
- [x] Full backend unit test suite still passes: 179/179 (158 baseline + 21 new); 5 pre-existing Postgres/Redis collection errors unchanged
- [x] Coverage of new logic: all paths in `consume_invite_by_email`, the OAuth signup-with-invite branch, and the email SSO mode are exercised

#### Manual Verification

- [ ] Run the new tests in isolation; review output for any skipped tests indicating environment gaps
- [ ] If `OAUTH_INVITE_REQUIRED` toggling between tests reveals stale `auth_manager_config` state, document the workaround (likely a fixture that resets `auth_manager_config` per test)

---

## Testing Strategy

### Unit Tests
- Race-safety of `consume_invite_by_email` (concurrent calls, exactly one wins)
- Expiry filtering correctness in both `get_pending_invite_by_email` and `consume_invite_by_email`
- Email casing normalization end-to-end (invite stored lowercase; OAuth claim arrives mixed-case → matches)

### Integration Tests
- OAuth callback → invite consumption → user creation, in a single test
- Domain-allowlist bypass via invite

### Manual Testing Steps
1. Configure `helm/open-webui-tenant` locally with `enableOauthSignup: "true"`, `oauthInviteRequired: "false"`, Microsoft client ID/secret/tenant set, `enableEmailInvites: "true"`, Graph mail credentials set
2. As admin, invite `someone@example.com` with role `admin`
3. Confirm the email arrives with "Sign in with Microsoft" CTA
4. Click → SSO redirect → Entra login → confirm landed in app as admin
5. Re-test with `oauthInviteRequired: "true"` and a non-invited email — confirm 403
6. Re-test with an expired invite (set `expires_at` directly in DB) — confirm fallthrough behavior

## Performance Considerations

- The new invite lookup adds one indexed query (`Invite.email`, `Invite.expires_at`) per OAuth callback. Negligible — OAuth callbacks are user-initiated, not high-volume, and the `email` column already has an index (`models/invites.py:15`).
- `consume_invite_by_email` uses `with_for_update(skip_locked=True)` on Postgres — this is a single-row lock for ~1ms during the OAuth flow. SQLite serializes writes globally anyway.

## Migration Notes

- **No database migration required.** Schema is unchanged; we only add filter logic.
- **Default-off rollout.** All new config defaults to off/false. Existing deployments get zero behavior change unless `OAUTH_INVITE_REQUIRED=true` is set or admins start using the invite-on-OAuth flow. Safe to merge to `main`.
- **Existing pending invites in production**: continue to work via the password-accept page. After this lands, the same invites also become consumable via OAuth without any DB touch.
- **Helm values upgrade**: existing Helm releases will pick up the new `oauthInviteRequired: "false"` default automatically; no manual values change needed.

## References

- Code map for OAuth flow: `backend/open_webui/utils/oauth.py:1372-1593`
- Domain allowlist: `backend/open_webui/utils/oauth.py:1478-1483`
- Random-UUID password pattern: `backend/open_webui/utils/oauth.py:1564-1572`
- Invite model: `backend/open_webui/models/invites.py`
- Invite endpoints: `backend/open_webui/routers/invites.py`
- Email template: `backend/open_webui/services/email/graph_mail_client.py`
- AddUserModal: `src/lib/components/admin/Users/UserList/AddUserModal.svelte:151-158`
- Invite-accept page: `src/routes/auth/invite/[token]/+page.svelte:167-189`
- Frontend SSO detection example: `src/routes/auth/+page.svelte:488-519`
- Helm wiring: `helm/open-webui-tenant/values.yaml:186-203`, `helm/open-webui-tenant/templates/open-webui/configmap.yaml:26-49`
- Past custom features overview: `collab/index.md` 20-03-2026 entry
- Past TOTP / 2FA precedent for new auth-config flags: `collab/index.md` 30-03-2026 entry

## Note Proposal (post-ship)

After this lands and is verified, propose the following memory updates:

- **`collab/notes.md`** — append a note titled "Hybrid SSO Invite + Auto-Provisioning". Capture:
  - The decision to reuse the Invite system rather than build a parallel email allowlist (and why)
  - The behavior matrix for `OAUTH_INVITE_REQUIRED` × `OAUTH_ALLOWED_DOMAINS` × `has-invite`
  - The race-safe `consume_invite_by_email` pattern (useful precedent for any future single-use token flow)
  - Outcome: the "wrong password on first SSO login" pain mode is resolved
- **`collab/index.md`** — add a row:
  ```
  | YYYY-MM-DD | @lexlubbers | Hybrid SSO Invite + Auto-Provisioning | OAUTH_INVITE_REQUIRED flag + invite consumption on OAuth signup. Existing domain-allowlist + password paths preserved. Helm + .env wired. | sso, oauth, entraid, invites, OAUTH_INVITE_REQUIRED, consume_invite_by_email, helm |
  ```
- **`collab/world/state.md`** — remove from "Pending Fixes" once shipped (no current entry exists; if one is added during implementation, retire it then).

Do not write the note now — it goes in after verification, per Section 3 of the methodology.
