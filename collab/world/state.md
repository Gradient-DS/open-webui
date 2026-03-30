### Current State

<!-- Mutable — update frequently, remove items when resolved. See methodology.md Section 6. -->

#### Current Work

- **TOTP 2FA Phase 1 — implementation complete** on branch `feat/email-2fa`. All 16 plan steps done. Needs manual testing and PR. Phase 2 (email OTP) deferred.
- External agents package — active development and integration
- New cloud integrations (Google Drive recently completed, exploring more)
- Regular upstream merges to stay current (latest: v0.6.43 → v0.8.9, branch `merge/upstream-260329`)

#### Pending Fixes

- **Cloud KB permission leak — code fix done, cleanup pending**: `_sync_permissions()` rewritten (owner-only check + suspension lifecycle) on `feat/logos` branch. Still need to: (1) deploy code fix to gradient.soev.ai, (2) run grant cleanup SQL (see `fix_kb_gradient_soev.md`) AFTER deploy. Migration hardening (NULL → private) already applied.

#### Open Questions

#### Active Resources

- Sync abstraction cookbook: `collab/docs/external-integration-cookbook.md`
- Upstream merge research: `thoughts/shared/research/2026-03-20-upstream-merge-strategy.md`
