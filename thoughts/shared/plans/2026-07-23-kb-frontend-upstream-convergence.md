# KB Frontend Convergence onto Upstream v0.10.2 Directory UI (Option A / P2-8) — Implementation Plan

## ⚠️ Worktree layout — READ FIRST (dev_stack context)

Implemented in an isolated git worktree on branch `feat/kb-upstream-convergence`, brought up by
`stackctl`. Edit ONLY the worktrees below — never the main checkouts
(soev-agents' main checkout sits on `main`, never a feature branch).

| Repo (as written in this plan) | Worktree to edit |
|---|---|
| `genai-utils/…` | `/Users/lexlubbers/Code/soev/genai-utils/.worktrees/feat/kb-upstream-convergence/…` |
| `open-webui/…` | `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/kb-upstream-convergence/…` |

Absolute `cd` commands already target the worktrees; repo-relative `Files:`
paths mean the matching worktree above. Stack status/URLs:
`stackctl status feat/kb-upstream-convergence` (from `soev-gitops/local/`).

**Note:** branch `feat/kb-upstream-convergence` already carries 2 commits of Phase 1 work
(`56d4dbdf2` directory materialization, `805089d27` metadata-mode test repairs) — review
`git log dev..HEAD` before implementing; Phase 1 backend may be partially or fully done.

**Date:** 2026-07-23
**Repo:** `open-webui/` (branch off `dev`, post-v0.10.2-merge)
**Status:** Draft for review
**Research:** `thoughts/shared/research/2026-07-23-kb-frontend-fork-vs-upstream-audit.md`
**Interlocks:** `thoughts/shared/plans/2026-07-22-sync-daemon-design.md` (D-8: P2-8 must land before the daemon completes the folder seam)

## Overview

Converge the KB frontend onto upstream v0.10.2's `knowledge_directory` UI (Option A from the audit): re-base the three conflict-heavy shared files on upstream, un-orphan the directory components, keep all fork capabilities as additive type-gated modules, then delete the fork's path-string tree stack. Local KBs gain full folder UX (subfolders, structure-preserving upload, incremental checksum-diff sync); cloud KBs get read-only directory browsing of sync-written structure. The recurring `KnowledgeBase.svelte` merge-conflict carve-out is retired.

## Locked product decisions — the "removed on purpose, do NOT restore" ledger

These fork removals are deliberate product decisions (confirmed by Lex 2026-07-23). The re-base MUST strip these from the upstream code it adopts:

| # | Upstream feature | Status | Converged behavior |
|---|---|---|---|
| 1 | **File rename** (dblclick inline editor + menu item, `renameFileById`) | ❌ do not restore | No rename affordance anywhere. API client fn stays additive-unused. |
| 2 | **File content editing** (Drawer + textarea + Save, `updateFileDataContentById`) | ❌ do not restore | Row click opens the fork's read-only `FileItemModal` preview; doc-icon = direct download (fork pattern kept). |
| 3 | **Per-file ellipsis action menu** (Rename/Download/Delete dropdown) | ❌ do not restore | Fork's direct Download + Delete icon buttons kept. |
| 4 | **Unlink-only file delete** (upstream retains File row/storage/vectors) | ❌ never adopt | All delete paths route through fork cascade (`DeletionService.delete_file`: vectors by file_id+hash in every containing KB, `file-{id}` collection, S3 object, DB row; owner/admin-gated; orphan cleanup). See Phase 1 for the directory-delete gap. |
| 5 | **Pending-files 5s polling** (`getPendingKnowledgeFiles` merge + interval) | ❌ do not restore | Fork socket-driven status + 30s-armed poll fallback kept; dead client fns deleted in Phase 4. |
| 6 | **Always-on AccessControl** | ❌ keep fork gating | AccessControl only for `type === 'local'`/untyped (+ integration-provider read view); cloud KBs show "Private". |
| 7 | TOPdesk surfaces | removed by `2026-07-03-remove-topdesk-and-legacy-sync-path.md` | Stays gone. |

**Fork features that MUST survive the re-base** (acceptance bar): multi-select + bulk delete (files AND sources) · typed KBs + create flow + `start_*_sync` · cloud sync panel (pickers, start/resync/cancel, SyncProgress, error-taxonomy toasts, `buildSyncToast`) · background-sync OAuth + needs_reauth · suspension UI · per-source removal · quota header · upload hardening (concurrency 5, extension allow-list, `metadata_only` fetches) · zero-content warning triangle · EmptyStateCards · builder `returnTo` · Confluence shared-KB handling · integration-provider (push) KB handling.

**Upstream features restored by the re-base** (previously carve-out casualties): structure-preserving directory upload, incremental checksum-diff local folder sync, standalone Reset (local KBs only — fixes the currently-unreachable Reset), copy-KB-ID, "File content" search toggle (off by default; explicit opt-in bears the de-TOAST cost), external-KB Test Query panel, directory create/rename/delete/move + breadcrumbs + drag-drop.

## Current State (from the audit — verified 2026-07-23, commit `781d0a2a0`)

- Both data models live on dev. Upstream's `knowledge_directory` + `KnowledgeFile.directory_id` were adopted additively (D2) with a **one-directional bridge** (`derive_relative_path_from_directory`, `routers/knowledge.py:139-167`) — but every UI entry point is unreachable: `DirectoryRow`/`KnowledgeBreadcrumbs`/`NewDirectoryModal` orphaned, directory API client caller-less.
- Fork tree renders from `knowledge_file.relative_path`/`source_item_id` (`/tree` + `/search`, `models/knowledge.py:1219-1670`). Local KBs: flat list, no folders; local directory upload flattens paths into filenames.
- `GET /{id}/files?directory_id=` already returns `{items, total, directories, breadcrumbs}` per level (`models/knowledge.py:873-1097`).
- `/sync/diff`, `/sync/cleanup`, `/dirs/create` already accept the sync-daemon machine principal (`get_sync_daemon_or_verified_user`, `routers/knowledge.py:2127-2160`).
- Cloud sync (loader-worker era, pre-daemon-cutover) writes only `meta.relative_path` — never `directory_id`.

## Design decisions (resolved for this plan)

1. **Reverse bridge at link time, not daemon-dependent.** `add_file_to_knowledge_by_id` (+ `set_path_fields_by_file_id` ingest branch) gains find-or-create directory materialization: when a file arrives with `relative_path` + folder-like `source_item_id` but no caller-provided `directory_id`, ensure the directory chain exists and set `directory_id`. Both the current sync path AND the future daemon then converge on the same rows; the UI never depends on cutover timing. Caller-provided `directory_id` always wins (daemon `/stage` path, placement-preservation fix of 2026-07-22 unchanged).
2. **Sources become root directories (folder-like sources only).** Chain = `<source display name>/<relative_path dirs>`. Source root name from `meta[metaKey].sources[].name` (fallback `item_path` basename); file-type sources get NO wrapper (files at root — preserves PR #235 semantics). The created root directory id is recorded as `sources[].root_directory_id` in the provider meta blob (no schema divergence) — used by remove-source cleanup and by the UI for the remove-source affordance.
3. **Status rollups as additive fields.** `directories[]` entries in the files response gain `child_count` + `status_counts {pending, completed, failed, unknown}` (recursive CTE over `knowledge_directory.parent_id` + `File.meta['status']` — same buckets as today's `/tree`). Fork-only field additions to an already-fork-touched response builder; no new endpoint.
4. **Search breadcrumbs from `meta.relative_path`.** The files response already carries `meta`; the search-mode UI renders breadcrumb segments from `meta.relative_path` (kept as derived metadata). No backend change.
5. **Write-op gating by KB type.** New single derived guard `structureEditable = knowledge.write_access && isLocalType` gates: New directory, dir rename/delete/move, file move drag-drop, uploads-into-directory, incremental sync, Reset. Cloud KBs: browse-only + fork sync chrome; integration-provider KBs: browse-only.
6. **Rollout kill-switch.** `localStorage.kbUpstreamUi !== 'false'` (lazy-tree precedent): legacy components stay importable until Phase 4; flipping the key restores the old view without redeploy on staging.
7. **Cross-repo interlock (daemon differ, genai-utils):** manifest `path` MUST be prefixed with the source root directory name for folder-like sources (matches decision 2), and `/stage` should pass `directory_id` from `directory_map`. Recorded here; implemented in the daemon workstream (design doc §5.2 already assumes directory reconciliation).

## What We're NOT Doing

- No daemon implementation work (separate workstream; only the manifest-prefix convention is interlocked).
- No re-base of `Knowledge.svelte` list page or `CreateKnowledgeBase.svelte` (fork versions kept — typed badges/sync/suspension are product identity; divergence is contained).
- No removal of `knowledge_file.relative_path`/`source_item_id` columns (demoted to derived metadata; agent surface `/internal/retrieval/.../files` and soev-agents folder browsing keep reading `meta.relative_path`).
- No empty-folder support for cloud KBs (mkdir derives from file paths, unchanged).
- No `meta` → `jsonb` migration; no MT/gitops changes; no agent-side tool changes.
- No restoration of anything in the do-not-restore ledger.

---

## Phase 1 — Backend: directory materialization + rollups + delete-cascade parity

**Status (2026-07-23): implemented on `feat/kb-upstream-convergence`; automated criteria verified; awaiting manual verification below.**

### Changes

- [x] 1. **Reverse bridge** (`models/knowledge.py`): `_ensure_directory_chain(knowledge_id, source_entry, relative_path) -> directory_id` (find-or-create per segment, unique `(knowledge_id, parent_id, name)` makes it idempotent; race-safe via retry-on-IntegrityError). Wire into `add_file_to_knowledge_by_id` and `set_path_fields_by_file_id` per decision 1. Stamp `sources[].root_directory_id` on first creation (decision 2). *(Impl note: NULL-parent root levels are outside the unique constraint on both backends — added a per-KB asyncio lock + canonical-row convergence in `_find_or_create_directory`; stamping is re-applied whenever the stored id diverges, so it self-heals past concurrent sync-worker meta writes.)*
- [x] 2. **Backfill migration** (Alembic `e2c7a94b1f38`, off head `2332928227f6`, grep-verified single match): batched (500, keyset by id — offset would skip rows as updates shrink the filter set) walk of `knowledge_file` rows with `relative_path IS NOT NULL AND directory_id IS NULL`, materializing chains via inlined helper logic. Idempotent; loose/local files untouched.
- [x] 3. **Rollup fields** (decision 3) on the files-response directory entries (`KnowledgeDirectoryEntry` + `get_directory_rollups` recursive CTE, cross-DB).
- [x] 4. **Directory-delete cascade parity** (ledger #4): new `DeletionService.delete_directory` (KB vectors by file_id+hash → dir/join rows → `delete_orphaned_files_batch`); router delete + `/sync/cleanup` dir branch routed through it; KB-scope guard added on cleanup `dir_ids` (design doc 4b). *(Also fixed: model `delete_directory` relied on FK cascade for subdirectories, which SQLite never enforces (no `PRAGMA foreign_keys`) — subtree rows are now deleted explicitly.)*
- [x] 5. **Remove-source cleanup**: after the existing prefix+`source_item_id` file sweep, delete the source's `root_directory_id` subtree (empty by then) when present; `source` now passed through all three provider wrappers.

### Success Criteria

**Automated (all verified 2026-07-23):**
- [x] alembic upgrade/downgrade clean on Postgres (throwaway pg16 container, incl. seeded-data smoke: chains + `root_directory_id` stamp) + SQLite (scratch DB); single head `e2c7a94b1f38`
- [x] new pytest suites (31 tests): chain find-or-create idempotency + race retry + NULL-parent twin convergence, backfill correctness (nested/multi-source/file-type-source/loose/pre-placed/batching), rollup counts vs seeded statuses (also exercised against real Postgres), dir-delete full-cascade (vectors+storage mocked, File rows gone, cross-KB shared file survives), remove-source subtree removal
- [x] existing `test_knowledge_file_path_columns.py` + `test_knowledge_tree.py` stay green (tree endpoint untouched; tree/metadata fixtures now create `knowledge_directory` — the metadata_mode suite was failing 10/11 on dev pre-change; 3 of its tests also carried stale pre-v0.10.2 expectations (content search without `include_content`, non-deferred `data`) and were updated to pin the locked upstream behavior)
- [x] pylint on changed files: rating unchanged vs baseline (7.89); black applied

**Manual:** staging big Drive KB (~1400 files): backfill populates `directory_id` (`SELECT count(*) FROM knowledge_file WHERE directory_id IS NOT NULL`); `GET /{id}/files?directory_id=` walks the hierarchy with correct rollups; a normal cloud sync run (loader-worker path) lands new files in the right directories.

**Pause for manual confirmation before Phase 2.**

---

## Phase 2 — Frontend re-base: upstream navigation + file ops (local KBs complete)

**Status (2026-07-28): implemented; automated criteria verified; awaiting manual verification below.**

### Changes

- [x] 1. **Re-base `KnowledgeBase.svelte` on upstream v0.10.2** (invert the carve-out): adopt `currentDirectoryId`/`directoryItems`/`breadcrumbs` state, `getItemsPage` per-level fetch (passing fork `metadata_only=true` + existing `limit`), `navigateToDirectory`, directory CRUD handlers, drag-drop (external drop w/ folder expansion, file/dir move MIME types), structure-preserving `uploadDirectoryEntries` + incremental `syncDirectoryHandler` (browser `/sync/diff` pipeline), standalone Reset confirm, copy-KB-ID, `includeContent` toggle, external-KB panel. Then apply the **do-not-restore ledger** (strip rename/content-editor/pending-poll) and re-attach fork modules: `CLOUD_PROVIDERS` + sync handlers/sockets/pollers, selection, quota, suspension, EmptyStateCards, FileItemModal preview, `returnTo`, upload hardening. All structure-write affordances behind `structureEditable` (decision 5). *(Impl notes: legacy view branches kept in-file behind the kill-switch — `upstreamUiActive = kbUpstreamUi flag && isLocalKnowledgeType(type)`, so cloud + push KBs stay on their legacy views until Phase 3. Directory-entry uploads reuse the fork's concurrency-5 pool + extension allow-list (`filterAllowedEntries`, new `{{count}} file(s) skipped` toast). Upstream's typed-i18n context (carve-out ledger row 1 candidate) adopted, which removes the file's whole `$i18n`-store error class.)*
- [x] 2. **Re-base `Files.svelte`**: upstream `DirectoryRow` rendering + drag targets restored; fork additions re-applied (SelectCheckbox + selection wiring incl. directory rows for nav-only, warning triangle, `added_at` preference, direct Download/Delete buttons per ledger #2/#3; drop VirtualList — 30/page per level makes it unnecessary). *(Impl note: native file-row drag is suspended while a selection is active so drag-paint multi-select keeps working; from an empty selection, row-drag = move-file and paint-select starts via checkbox/ctrl-click.)*
- [x] 3. **Re-base `AddContentMenu.svelte`** from upstream (restores New directory / Sync directory / Reset with correct prop names — fixes the latent `onSync`/`onOneDriveSync` + `onReset` defects by construction); items conditioned on `structureEditable`.
- [x] 4. **Un-orphan** `DirectoryRow`/`KnowledgeBreadcrumbs`/`NewDirectoryModal` (strip DirectoryRow's rename affordance per ledger #1 — keep dir rename via… note: **directory** rename is a structure op, allowed for local KBs; only FILE rename is banned). *(DirectoryRow kept byte-identical to upstream except `draggable`/dragstart now gated on `writeAccess`.)*
- [x] 5. Search mode: query present → flat results from the files endpoint (server query within KB) with breadcrumbs from `meta.relative_path` (decision 4); "File content" toggle off by default.
- [x] 6. Kill-switch `kbUpstreamUi` (decision 6); legacy components untouched this phase.
- [x] 7. i18n: all new/restored strings in `en-US` + `nl-NL` (alphabetical keys). *(Most upstream keys already landed with the v0.10.2 merge incl. `_one`/`_other` plural variants; added the new skipped-files key pair and filled 36 empty nl-NL values for the restored directory/sync/external strings.)*

### Success Criteria

**Automated (all verified 2026-07-28):**
- [x] `npm run check` net-zero new errors vs baseline — net **−72** (10599 vs 10671 at `805089d27`): the typed-i18n port removes more baseline errors than the new code's implicit-any/possibly-null instances add; zero new error *kinds* in changed files, and the latent `onSync`-prop-mismatch type error is gone
- [x] `npm run test:frontend` green — 259 passed (selection/syncToast/treeStatus suites intact; new `utils/structure.test.ts` covers the `structureEditable` matrix; breadcrumb derivation already pinned by `treeStatus.test.ts`)
- [x] ESLint on changed files: no new rule classes (fixed `no-constant-condition` + `svelte/no-inner-declarations` introduced by upstream idioms); remaining counts are the files' pre-existing untyped-callback/`no-explicit-any` idiom, matching baseline classes
- [x] Prettier clean on changed `.svelte`/`.ts` files (locale JSONs keep their i18next-parser 4-space format — they were never prettier-clean)
- [x] `npm run build` passes

**Manual (staging, local KB):** create/rename/delete/move directories; breadcrumb + drag-drop moves; structure-preserving folder upload; incremental re-sync (only changed files upload, deletions removed, "Sync complete" counts correct); Reset works with confirm; file delete fully purges (spot-check S3 + Weaviate); bulk select/delete works; preview modal read-only; NO rename/edit affordance anywhere; kill-switch restores old UI.

### Follow-ups from first manual round (2026-07-28, all fixed same day)

- [x] **Multi-select drag-move**: dragging a *selected* row now carries the whole selection (`application/x-kb-file-move` payload is `{fileIds: [...]}`; DirectoryRow/breadcrumb drop handlers + a batched `moveFilesToDirectoryHandler` with one toast + one refresh). Unselected rows stay drag-paint targets. Verified in-browser: 2 selected files dropped on a folder both moved.
- [x] **Session-expiry "refresh storm"** (pre-existing, not Phase 2): the 401 interceptor sets `user = null` then SPA-navigates to `/auth`; `/auth` onMount treated `null !== undefined` as "signed in" and bounced back, while the `(app)` layout's null-guard bounced forward — an infinite zero-network SPA navigation loop (renderer pegged at 100% CPU; reproduced deterministically, a hard reload was the only escape). Fixed: `/auth` redirect gate is now `if ($user)`.
- [x] **OneDrive "internal server error" + stuck "sync already in progress"** (daemon-era sync path, not Phase 1/2): this branch's `/sync/items` unconditionally triggers the external sync-daemon; the dev stack's daemon (:18110) wasn't running, so the trigger 500'd AFTER `status='syncing'` was persisted — wedging the KB for the 30-min staleness window. Fixed in `services/sync/router.py`: `SyncDaemonError` now rolls the status back to `failed` + returns 502 with a clear message; cancel is best-effort tolerant of an unreachable daemon. Stack fixed too: sync-daemon window recreated (was missing from the tmux session); stuck KB meta reset via SQL.
- [x] **OneDrive picker `/api/config` bursts**: `OneDriveConfig.getCredentials()` re-fetched `/api/config` on every `ensureInitialized()` (~10×/picker action); now cached per session.
- **Cloud structure adherence** (user note): already locked — `structureEditable` is local-only, so cloud/OneDrive KBs get zero move/rename/delete-directory affordances on the new scaffold; that scaffold reaches cloud KBs in Phase 3 (today they still render the legacy tree).

**Pause for manual confirmation before Phase 3.**

---

## Phase 3 — Cloud KB chrome on the upstream scaffold

**Status (2026-07-28): implemented; automated criteria verified; awaiting manual verification below.**

### Changes

- [x] 1. Cloud KBs render the same per-level directory browsing (read-only structure): source root directories at KB root with remove-source affordance mapped via `sources[].root_directory_id` (+ sync spinner on the syncing source), folder rows showing `child_count` + `status_counts` badges (`folderBadge` reused), file rows with status icons. *(Impl: `utils/sourceMap.ts` maps root_directory_id → source; DirectoryRow gained additive `source`/`isSyncing`/`onRemoveSource`/rollup-badge/checkbox props — defaults keep local rows unchanged except the new child-count text + badges, which apply to local dirs too. File rows use `fileBadge` (spinner/error/idle), upgrading the old uploading-only spinner. File-type sources stay loose files at root (no wrapper, PR #235 semantics), matching the legacy lazy tree.)*
- [x] 2. Sync liveness: socket handlers drive a 2s-coalesced level refresh (port `scheduleTreeRefresh` semantics to `getItemsPage`); 4s poll while syncing (`syncLevelPoll`); resume/adopt-external-sync unchanged.
- [x] 3. Bulk selection spans files + sources on the new rows (existing `selection.ts` kinds; source-root rows get checkboxes, ordered dirs-then-files for shift-range); bulk delete replays fork endpoints (unchanged `bulkRemoveHandler`).
- [x] 4. Quota header (now fed by a cheap KB-wide `kbFileTotal` fetch — the level-scoped total would under-report), sync panel/progress/badges, needs_reauth, cancel — header modules untouched by the ladder change. *(Also: `upstreamUiActive` now covers all KB types; cloud drops blocked with a toast (decision 5); EmptyStateCards regained the cloud/integration actions + the "Starting sync..." branch on the new scaffold.)*
- [x] 5. Integration-provider KBs: browse-only + EmptyStateCards message (unchanged behavior on new scaffold; push KBs get the flat per-level list, no structure affordances, drops still blocked by the existing integration toast).

### Success Criteria

**Automated (verified 2026-07-28):**
- [x] frontend suite green — 263 passed (new `utils/sourceMap.test.ts` covers the source-root remove-affordance mapping; rollup badge logic already pinned by `treeStatus.test.ts` `folderBadge` — the repo has no svelte component-render test setup, so badge coverage is at the helper seam)
- [x] `npm run check` 10611 — still 60 below the pre-Phase-2 baseline (10671); build passes; ESLint deltas match file idiom (sourceMap files fully clean)

**Manual (staging):** big cloud KB browses per-level fast (<300ms/level); folder status badges correct during + after a live sync; remove source deletes files + its root directory; cancel/resync/needs_reauth flows intact; no structure-write affordances on cloud KBs; Confluence shared KB + suspension UI intact.

**Note (2026-07-28):** existing cloud KBs synced BEFORE Phase 1 landed only get source-root directories after the backfill migration runs (dev stack: already migrated) AND `root_directory_id` stamping happens on the next sync run — a cloud KB that never re-synced post-Phase-1 shows its directories without the remove-source affordance until then (files/folders still browse fine).

**Daemon-path stamping gap — found + fixed live (2026-07-28):** the daemon creates directories itself and passes `directory_id` at `/stage`, so Phase 1's link-time reverse bridge (which does the `root_directory_id` stamping) never runs on that path. Fixed in `routers/sync_daemon.py`: terminal run summaries now stamp `sources[].root_directory_id` by matching folder-like sources to KB-root directories named after them (decision 2 convention; self-healing on every terminal summary). Verified end-to-end on the dev stack: full OneDrive daemon sync (token broker → Graph enumeration → SharePoint download → MinIO staging → 16 files, 2 source-root dirs, correct placement) + delta re-run stamped both folder sources. `test_sync_daemon_router.py` + `test_cloud_sync_status.py` (25) and the genai-utils daemon suite (161) green. Related daemon fix (genai-utils `engine/run.py`): pre-run failures (config/registry/credential) now post a terminal failed summary before re-raising — without it, a credential failure after `/sync/items` wedged the KB at 'syncing' for the 30-min staleness window.

**Pause for manual confirmation before Phase 4.**

---

## Phase 4 — Legacy deletion + demotion (fork-shrink payoff)

### Changes

1. Delete `LazyKnowledgeTree/LazyTreeNode/LazyKnowledgeSearch/SourceGroupedFiles/FolderTreeNode` + `treeHelpers` (+tests) + the `kbUpstreamUi`/`lazyKnowledgeTree` kill-switches; prune `treeStatus.ts` to the kept helpers (`folderBadge`/`fileBadge`/`breadcrumbSegments`).
2. Remove `GET /{id}/tree` + `GET /{id}/search` routes, `list_tree_level`/`search_tree` + helpers, their client fns + tests.
3. Dead-code sweep: `getPendingKnowledgeFiles`/`streamPendingKnowledgeFiles` client fns, unused imports, upstream's `deleteFileById` import, etc.
4. `relative_path`/`source_item_id` columns stay (derived metadata + provider mapping — decision, audit constraint 8); the composite index `ix_kf_kb_source_relpath` stays while `/internal/retrieval` and remove-source still filter on them.
5. Update `collab/docs/external-integration-cookbook.md` + CLAUDE.md pointers; note the retired carve-out in the next merge's recipe list.

### Success Criteria

**Automated:** full frontend + backend suites green; `grep -rn "getKnowledgeTree\|searchKnowledgeTree\|SourceGroupedFiles\|LazyKnowledgeTree" src/ backend/` → zero hits; build passes.
**Manual:** one week of staging soak incl. a full cloud re-sync and an upstream-merge dry-run (`git merge --no-commit upstream/main` on a scratch branch) confirming `KnowledgeBase.svelte` conflicts are tractable.

---

## Testing Strategy

- **Unit:** directory-chain idempotency/races; backfill shapes (nested, multi-source, file-source, loose, Confluence-flat); rollup buckets; cascade deletes (shared-file survival); `structureEditable` matrix (local/cloud/push/managed/read-only).
- **Contract:** existing daemon-era diff/cleanup contract tests (merge plan Phase 7 3c) must stay green — Phase 1's materialization must not perturb `/sync/diff` verdicts (paths reconstructed from `parent_id` chains now match manifest paths incl. source prefix).
- **E2E (owui-e2e / validate-owui):** local-KB folder lifecycle scenario; cloud-KB browse + sync scenario.
- **Manual:** per-phase lists above.

## Migration Notes

- Backfill is additive + idempotent; rollback = downgrade migration (drops nothing — directories created remain, harmless to old UI which ignores them).
- Phases are independently shippable; the old UI keeps working through Phase 1-3 via the kill-switch because the derive bridge keeps `relative_path` populated in both directions.
- Daemon interlock: communicate decision 7 (manifest source-prefix + `/stage` `directory_id`) to the sync-daemon workstream **before** its differ implementation freezes (D-8 sequencing requirement).

## References

- Audit: `thoughts/shared/research/2026-07-23-kb-frontend-fork-vs-upstream-audit.md`
- Carve-out ledger: `open-webui/thoughts/shared/research/v0.10.2-carveout-diffs/KnowledgeBase.svelte.md`
- Sync-daemon design (D-8, §5.2): `thoughts/shared/plans/2026-07-22-sync-daemon-design.md`
- Lazy tree origin: `thoughts/shared/plans/2026-06-25-kb-lazy-folder-tree.md`
- Deletion service: `backend/open_webui/services/deletion/service.py:49-117`
- Upstream reference: `git show v0.10.2:src/lib/components/workspace/Knowledge/...`
