### Current State

<!-- Mutable — update frequently, remove items when resolved. See methodology.md Section 6. -->

#### Current Work

- **DPIA compliance features** on branch `feat/dpia`: user data export and configurable data retention both implemented. Needs testing and PR.
- External agents package — active development and integration
- New cloud integrations (Google Drive recently completed, exploring more)

#### Pending Fixes

- **Cloud KB permission leak — code fix done, cleanup pending**: `_sync_permissions()` rewritten (owner-only check + suspension lifecycle). Still need to: (1) deploy code fix to gradient.soev.ai, (2) run grant cleanup SQL AFTER deploy.

#### Completed Recently

- **TOTP 2FA Phase 1** — merged via PR #61. Phase 2 (email OTP) deferred.
- **Security hardening** — Trivy CVE fixes, Docker slimming, TOTP replay token fix
- **Aesthetic polish** — merged via PR #62. KB logos, tab split, Dutch translations

#### Open Questions

#### Active Resources

- Sync abstraction cookbook: `collab/docs/external-integration-cookbook.md`
- Data export plan: `thoughts/shared/plans/2026-03-31-user-data-export.md`
- Data retention plan: `thoughts/shared/plans/2026-03-31-data-retention-ttl.md`
