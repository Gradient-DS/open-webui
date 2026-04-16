### Current State

<!-- Mutable — update frequently, remove items when resolved. See methodology.md Section 6. -->

#### Current Work

- **Upstream merge (260416)** — merging 245 commits from upstream/dev into `merge/260416`. Major: async DB refactor, automations feature, 19 security fixes. 20-phase plan at `collab/docs/upstream-merge-260416-plan.md`. Phase 1 (pre-merge setup) not yet started.
- **Vink deployment** — large-scale client deployment (~1300 docs), active on `feat/vink` branch. Core sync worker issues resolved, plans created for next steps (single-collection architecture, batch-wait removal)
- External agents package — active development and integration

#### Pending Fixes

- **Cloud KB permission leak — code fix done, cleanup pending**: `_sync_permissions()` rewritten (owner-only check + suspension lifecycle). Still need to: (1) deploy code fix to gradient.soev.ai, (2) run grant cleanup SQL AFTER deploy.

#### Completed Recently

- **Sync worker performance overhaul** — concurrent document processing in `base_worker.py`, unified frontend upload concurrency (01-04-2026)
- **Vink large-scale fixes** — asyncio.gather chunking, `DOCUMENT_PROCESSING_TIMEOUT`, parsing upgrades, sync logging (03–06-04-2026)
- **DPIA compliance** — merged via PR #66. Data export, data retention, file upload improvements, zip export security fix, cloud sync hardening.
- **TOTP 2FA Phase 1** — merged via PR #61. Phase 2 (email OTP) deferred.
- **Security hardening** — Trivy CVE fixes, Docker slimming, TOTP replay token fix
- **Aesthetic polish** — merged via PR #62. KB logos, tab split, Dutch translations

#### Open Questions

#### Active Resources

- Sync abstraction cookbook: `collab/docs/external-integration-cookbook.md`
- Upstream merge plan: `collab/docs/upstream-merge-260416-plan.md`
