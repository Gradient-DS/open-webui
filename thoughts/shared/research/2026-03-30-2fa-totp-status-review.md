---
date: 2026-03-30T12:40:00+02:00
researcher: claude
git_commit: 13c8f7c628634360bb7863422f93ef3c51f677a0
branch: feat/logos
repository: open-webui
topic: '2FA TOTP Status Review — Prior Research, Upstream Status, Implementation Readiness'
tags: [research, codebase, authentication, 2fa, totp, security, upstream-prs]
status: complete
last_updated: 2026-03-30
last_updated_by: claude
---

# Research: 2FA TOTP Status Review — Prior Research, Upstream Status, Implementation Readiness

**Date**: 2026-03-30T12:40:00+02:00
**Researcher**: claude
**Git Commit**: `13c8f7c628634360bb7863422f93ef3c51f677a0`
**Branch**: `feat/logos`
**Repository**: open-webui

## Research Question

What research has already been done on 2FA for email+password users? What is the current upstream status? How ready are we to implement TOTP via authenticator app?

## Summary

**We have extensive prior research** — a detailed design document from 2026-02-16 covering the full architecture (partial JWT flow, TOTP encryption, recovery codes, admin enforcement, bypass rules). All client decisions are resolved. **Nothing has been implemented yet** — no code, no migration, no dependencies added.

**Upstream has no 2FA either.** Two community PRs were attempted (#11953, #16461) but neither was merged. The feature request (issue #1225, 57+ upvotes) remains open with no maintainer commitment. This means we'd be building a custom feature, consistent with our additive fork approach.

## Prior Research (2026-02-16)

**Document**: `thoughts/shared/research/2026-02-16-2fa-email-totp-implementation.md`

This is a comprehensive 400-line research document covering:

### Architecture Decisions Already Made

| Decision              | Resolution                                                                           |
| --------------------- | ------------------------------------------------------------------------------------ |
| **Auth flow**         | Partial JWT with `"purpose": "2fa_pending"`, 5-min TTL — keeps system stateless      |
| **2FA scope**         | Email+password users ONLY                                                            |
| **Bypass rules**      | LDAP, SSO/OAuth, API keys, trusted headers all skip 2FA                              |
| **TOTP encryption**   | AES-GCM using key derived from `WEBUI_SECRET_KEY` (cryptography lib already present) |
| **Recovery codes**    | 10 codes, bcrypt-hashed, one-time use, `XXXXX-XXXXX` format                          |
| **Admin enforcement** | `ENABLE_2FA`, `REQUIRE_2FA`, `2FA_GRACE_PERIOD_DAYS`, `2FA_METHODS` settings         |
| **Email OTP**         | Via Microsoft Graph API (not SMTP), reusing OneDrive Graph client                    |
| **Dependencies**      | `pyotp`, `qrcode[pil]` (email OTP uses Graph API, no `aiosmtplib` needed)            |

### Proposed API Endpoints (8 total)

| Endpoint                                     | Purpose                                            |
| -------------------------------------------- | -------------------------------------------------- |
| `POST /api/v1/auths/2fa/totp/setup`          | Generate TOTP secret + QR code                     |
| `POST /api/v1/auths/2fa/totp/enable`         | Verify first code, activate, return recovery codes |
| `POST /api/v1/auths/2fa/totp/disable`        | Deactivate TOTP                                    |
| `POST /api/v1/auths/2fa/verify`              | Verify TOTP/recovery code during login             |
| `POST /api/v1/auths/2fa/email/send`          | Send email OTP                                     |
| `POST /api/v1/auths/2fa/email/verify`        | Verify email OTP                                   |
| `POST /api/v1/auths/2fa/recovery/regenerate` | Generate new recovery codes                        |
| `GET /api/v1/auths/2fa/status`               | Check 2FA enrollment status                        |

### Database Changes (1 migration)

- **Auth table**: Add `totp_secret` (encrypted), `totp_enabled` (bool), `totp_last_used_at` (replay protection)
- **New table**: `recovery_code` (bcrypt-hashed codes, used flag)
- **New table**: `email_otp` (hashed OTP, attempts, expiry)

### Frontend Components Needed

- `TwoFactorChallenge.svelte` — code input during login
- `TwoFactorSetup.svelte` — TOTP enrollment with QR code in account settings
- `RecoveryCodes.svelte` — display/regenerate recovery codes
- Modifications to `auth/+page.svelte`, `Account.svelte`, admin `General.svelte`

### Key Code Touchpoints

- `backend/open_webui/routers/auths.py:570-588` — standard signin branch (insert 2FA check)
- `backend/open_webui/utils/auth.py:269` — `get_current_user()` (reject partial tokens)
- `backend/open_webui/models/auths.py:17-23` — Auth ORM model (add columns)
- `src/routes/auth/+page.svelte:59` — login handler (handle `requires_2fa` response)
- `src/lib/apis/auths/index.ts:257` — `userSignIn()` (handle 2FA response)

### Open Question

- **Phase approach**: TOTP first, email OTP later? Email infra (Graph API) is being built separately. The research recommends starting with TOTP since it requires no external services.

## Upstream Status (as of March 2026)

### No 2FA in upstream Open WebUI

- `docs/SECURITY.md` lists 2FA as a "feature request" under "Not a Vulnerability"
- No 2FA code, models, config, or dependencies exist in the codebase

### Community Attempts (both failed to merge)

| PR                                                            | Author         | Status              | Key Issues                                                                                   |
| ------------------------------------------------------------- | -------------- | ------------------- | -------------------------------------------------------------------------------------------- |
| [#11953](https://github.com/open-webui/open-webui/pull/11953) | TensorTemplar  | Draft (2025-03-22)  | Missing deps, 2FA on by default, broken cancel state, migration errors                       |
| [#16461](https://github.com/open-webui/open-webui/pull/16461) | jeremy-windsor | Closed (2025-11-17) | More polished (379 lines backend, RFC 6238), but author deleted fork. Missing rate limiting. |

### Community Demand

| Source                                                                          | Upvotes | Status                             |
| ------------------------------------------------------------------------------- | ------- | ---------------------------------- |
| [Issue #1225](https://github.com/open-webui/open-webui/issues/1225)             | 57+     | Open since 2024-03-20              |
| [Discussion #9594](https://github.com/open-webui/open-webui/discussions/9594)   | 44+     | Open, no maintainer response       |
| [Discussion #16338](https://github.com/open-webui/open-webui/discussions/16338) | —       | Jeremy-windsor's pre-PR discussion |

### Lessons from Failed PRs

1. **Must be opt-in** — PR #11953 was rejected partly because 2FA was on by default
2. **Rate limiting required** — Community flagged missing rate limiting on #16461
3. **Clean migrations** — PR #11953 had migration errors when switching branches
4. **Maintainer engagement low** — tjbck marked #16461 as draft twice with no substantive review

## Implementation Readiness Assessment

### What's Ready

- **Full architecture designed** — partial JWT, encryption, endpoints, DB schema, frontend components
- **All client decisions resolved** — bypass rules, enforcement model, email provider
- **No upstream conflicts** — zero existing 2FA code means no merge risk
- **Dependencies available** — `pyotp` and `qrcode[pil]` are mature, well-maintained
- **Existing patterns to follow** — OAuth session encryption (Fernet) for TOTP secret encryption, RateLimiter for attempt limiting

### What's Needed Before Implementation

1. **Create implementation plan** — turn the research into a step-by-step plan with task breakdown
2. **Decide on phasing** — TOTP-only first, or TOTP + email OTP together?
3. **Verify line references** — the research was done on commit `2be7bd7a`, codebase has changed significantly since (v0.6.43 → v0.8.9 merge). Line numbers in `auths.py`, `auth.py`, `Account.svelte` etc. likely shifted.
4. **Check Alembic head** — migration head was `2c5f92a9fd66` in Feb, likely different now
5. **Feature flag pattern** — follow existing `FEATURE_*` env var pattern for `ENABLE_2FA`

### Estimated Scope (from prior research)

- ~15-20 files touched
- 1 Alembic migration
- ~1500-2500 lines of new code
- Medium-large feature

## Code References

- `thoughts/shared/research/2026-02-16-2fa-email-totp-implementation.md` — Full prior research document
- `backend/open_webui/routers/auths.py` — Signin endpoint (2FA branching point)
- `backend/open_webui/utils/auth.py` — JWT creation/validation (partial token support)
- `backend/open_webui/models/auths.py` — Auth ORM model (needs new columns)
- `backend/open_webui/models/oauth_sessions.py` — Fernet encryption reference pattern
- `backend/open_webui/utils/rate_limit.py` — RateLimiter class (reuse for 2FA)
- `src/routes/auth/+page.svelte` — Login page (handle 2FA challenge)
- `src/lib/apis/auths/index.ts` — Auth API client (new 2FA functions)
- `docs/SECURITY.md:149` — Upstream lists 2FA as feature request

## Related Research

- `thoughts/shared/research/2026-02-16-2fa-email-totp-implementation.md` — Original comprehensive research

## Open Questions

1. **Phase approach**: Start with TOTP-only (no external deps) or ship TOTP + email OTP together?
2. **Line number drift**: Prior research references need re-verification against current codebase (post v0.8.9 merge)
3. **Upstream contribution**: Worth offering this back upstream? Low chance of acceptance given maintainer disengagement, but could build goodwill
