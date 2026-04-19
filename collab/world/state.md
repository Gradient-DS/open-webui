### Current State

<!-- Mutable — update frequently, remove items when resolved. See methodology.md Section 6. -->

#### Current Work

- **Upstream merge (260416)** — merging 245 commits from upstream/dev into `merge/260416`. Major: async DB refactor, automations feature, 19 security fixes. 20-phase plan at `collab/docs/upstream-merge-260416-plan.md`. Phase 1 (pre-merge setup) not yet started.
  - **Soft-delete surface restoration** — 6-phase plan at `thoughts/shared/plans/2026-04-19-async-soft-delete-surface.md` complete through Phase 6 automated verification. Restored the `ChatTable` soft-delete surface dropped by upstream's async DB refactor and flipped the entire DeletionService / DataRetentionService / cleanup_worker chain to fully async. Manual smoke tests (admin delete-user flow, retention test endpoint, cleanup-worker 5-min clean-start) still pending.
  - **Async cascade fixes** — 6-phase plan at `thoughts/shared/plans/2026-04-19-merge-260416-async-cascade-fixes.md` complete through Phase 6 automated verification. Restored `Files.update_file_path_by_id`, `Feedbacks.get_conversation_feedback_by_chat_id_and_user_id` + endpoint; flipped OAuth auth modules (OneDrive + Google Drive + TokenManager + scheduler `_is_sync_due`) to async; added missing `await`s across invites/files/sync routers; flipped `process_file_with_external_pipeline`, `_bind_service_account`/`_unbind_service_account`, `register_external_agent_direct`/`load_external_agents_at_startup` to async; swapped two `Knowledges.get_suspension_info` shim calls to `async_get_suspension_info`; restored 4 ModelEditor feature-flag guards (knowledge/tools/skills/voice). Manual smoke tests pending.
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
