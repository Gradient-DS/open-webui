### Current State

<!-- Mutable — update frequently, remove items when resolved. See methodology.md Section 6. -->

#### Current Work

- External agents package — active development and integration
- New cloud integrations (Google Drive recently completed, exploring more)

#### Pending Fixes

- **Cloud KB permission leak — code fix done, cleanup pending**: `_sync_permissions()` rewritten (owner-only check + suspension lifecycle). Still need to: (1) deploy code fix to gradient.soev.ai, (2) run grant cleanup SQL AFTER deploy.

#### Completed Recently

- **DPIA compliance** — merged via PR #66. Data export, data retention, file upload improvements, zip export security fix, cloud sync hardening.
- **KB file limit increase** — hotfix `3bda20910` (01-04-2026)
- **TOTP 2FA Phase 1** — merged via PR #61. Phase 2 (email OTP) deferred.
- **Security hardening** — Trivy CVE fixes, Docker slimming, TOTP replay token fix
- **Aesthetic polish** — merged via PR #62. KB logos, tab split, Dutch translations

#### Open Questions

#### Active Resources

- Sync abstraction cookbook: `collab/docs/external-integration-cookbook.md`
