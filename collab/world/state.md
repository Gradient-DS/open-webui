### Current State

<!-- Mutable — update frequently, remove items when resolved. See methodology.md Section 6. -->

#### Current Work

- **Upstream v0.9.5 merge** — Phases 0, 1 (merge), 1.5 (services-async-cascade) all committed on `feat/upstream-v0.9.5-merge`. Phase 1.5 turned out ~5x larger than the original handoff predicted (104 caller sites across 63 files, not just 5 services + 23 NOTE comments). All sync model methods, services, cloud-sync workers (incl. Option B refactor for sync inner functions in base_worker), routers, and OAuth helpers now async. Only carved-out `routers/retrieval.py` (16 sites) remains for Phase 4 audit. **Next:** Phase 2 (Alembic migration chain), Phase 3 (deps + version bump), Phase 4 (verification + carve-out audit + test mock updates), Phase 5 (PR + tag v1.2.0). Continue from `thoughts/shared/research/v0.9.5-phase-2-onwards-handoff.md`.
- **Confluence Cloud Sync admin panel** — implementing `thoughts/shared/plans/2026-05-21-confluence-cloud-sync-admin.md` (3 phases). **Phase 1 done + extended, awaiting manual verification**: new Cloud Sync admin tab with Confluence + Google Drive + OneDrive sections (all OAuth config admin-editable, no Helm/restart needed); `/api/v1/configs/{confluence,google_drive,onedrive}` GET/POST endpoints; all three sync routers mounted unconditionally + scheduler runtime-toggleable; OneDrive/GDrive enable toggles removed from Documents tab (Integration section deleted); env-only creds promoted to PersistentConfig. Scope was expanded mid-Phase-1 at user request to migrate OneDrive/GDrive too (plan amended). Phases 2 (Confluence username+API-token Basic auth) + 3 (shared read-only full-content KB) pending. Driving use case: unblock Intermax (Confluence Cloud, one shared KB for all users). **Note:** v0.9.5 merge work is on a worktree, so `dev` stays clean for verifying Confluence Phase 1.
- **Vink deployment** — large-scale client deployment (~1300 docs), active on `feat/vink` branch. Core sync worker issues resolved, plans created for next steps (single-collection architecture, batch-wait removal)
- External agents package — active development and integration
- New cloud integrations (Google Drive recently completed, exploring more)

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
