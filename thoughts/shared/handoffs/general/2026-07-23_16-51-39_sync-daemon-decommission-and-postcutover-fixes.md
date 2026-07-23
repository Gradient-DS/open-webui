---
date: 2026-07-23T16:51:39+02:00
researcher: Lex Lubbers (with Claude)
git_commit: open-webui dev 781d0a2a0 · genai-utils dev (post #319) · soev-gitops main (post #70)
branch: dev (all fixes merged)
repository: soev monorepo (genai-utils, open-webui, soev-gitops)
topic: "Sync-daemon: loader-worker/search-api decommission DONE + post-cutover bug fixes DONE (all merged); remaining = staging re-validation + NEO/mkbot promotion gate"
tags: [sync-daemon, decommission, cutover, confluence, bugfix, data-loss, staging, handoff]
status: complete (all PRs merged) — awaiting post-redeploy staging re-validation
last_updated: 2026-07-23
last_updated_by: Lex Lubbers
---

# Handoff: Sync-daemon decommission + post-cutover fixes (all merged)

## Task(s)

Two phases, **both complete and merged**:

1. **Decommission / clean slate** (D-10 step 5) — remove the dead loader-worker + deprecated `api/` Search API + OWUI in-pod cloud-sync machinery, and wire manual "Sync now" through the daemon. **DONE.**
2. **Post-cutover bug fixes** — three live bugs found on staging after the daemon took over cloud sync (Confluence data-loss + not-syncing; OneDrive single-file→folder). **DONE.**

Remaining is **not code** — it's staging re-validation after the daemon/OWUI dev images redeploy, plus the standing NEO/mkbot promotion gate. See Action Items.

## Recent changes (all MERGED to dev/main)

**Decommission:**
- **genai-utils #310** — deleted `api/gateway/loader_worker/` + the root `api/` vector Search API; dropped `build-loader-worker` + `build-api.yml`; cleaned stack/compose refs. Kept `api/gateway/` (live Gradient-Gateway + doc_processor).
- **open-webui #232** — deleted in-pod `services/sync/{base_worker,scheduler,pipeline_client,constants}.py` + connector `sync_worker`/`scheduler`; removed 6 scheduler start/stop blocks in `main.py`; stripped dead `execute_sync`/`create_worker` from the kept provider files; removed `LOADER_WORKER_URL`. **Built the OWUI→daemon "Sync now" forward** (`services/sync/daemon_client.py` `trigger_sync_run`/`cancel_sync_run`; `/sync/items` ×3 + confluence `/shared/sync` + cancel now POST the daemon's `/sync/run` + `/sync/{kb}/cancel`; config `SYNC_DAEMON_URL`+`SYNC_DAEMON_API_KEY`; chart adds `SYNC_DAEMON_API_KEY` to the OWUI deployment — URL already in the configmap). **KEPT `html_renderer.py`** (live Confluence-picker endpoint, NOT the loader — D-9 only covers the sync path) and **`TENANT_NAME`** (feedback_report).
- **soev-gitops #66** — removed the `loaderWorker` block + `loaderWorkerDbPassword` ESO field from `tenants/previder-prod/staging/helmrelease.yaml` (staging only).

**Post-cutover fixes (daemon = genai-utils, tree = open-webui):**
- **genai-utils #316** — `engine/run.py`: (a) **empty-full-manifest wipe guard** — a full-mode manifest drives `/sync/diff`'s absence-based deletions, so an EMPTY full manifest reported every file absent → cleanup wiped the KB. Guard: an empty full-mode enumeration never drives deletions. (b) **revoked-source preservation** — a transient Confluence 403/404 maps to `SourceAccessRevokedError`; the run dropped the source from the persisted registry → OWUI's clean-terminal wrote a shrunken `meta.sources` → the KB lost its cloud binding (empty "select spaces" screen). Now the source is preserved (frozen cursor) + only reported as revoked. Also LOCAL_DEV.md loader-worker docs cleanup.
- **genai-utils #319** — **THE "0 files, completed" fix.** `sources/confluence_enum.py:_list_source_pages` keyed on `type` (which is `'folder'` for spaces — the retired worker's folder/file routing hint), not `confluence_type` (`'space'`). So every space mis-routed to page handling → `GET /pages/{space_id}` 404 → source dropped → 0-file "completed". Fix: key on `confluence_type` (fallback `type`→`space_id`). Test fixtures updated to the real OWUI shape (`type:'folder'`+`confluence_type:'space'`). **NB: #319 was pushed AFTER #316 merged, so it is a separate PR — both are now merged.**
- **open-webui #235** — `models/knowledge.py:_tree_sources_level`: a single picked file rendered as a one-file wrapper folder because the daemon stamps `source_item_id` on every file and the tree rolled every `source_item_id` into a folder node. Fix (tree-only): `type:'file'` sources render as loose root files (new `_file_source_files` helper). `source_item_id` untouched → remove-source still works; fixes already-synced files with no re-sync.
- **soev-gitops #70** — removed the inert `useSharedLoader:"true"` staging value (chart never templates it).

## Learnings

- **The Confluence source-shape mismatch is the crux (genai-utils #319).** OWUI stores Confluence sources with a legacy `type` field = `'folder'`/`'file'` (the retired in-pod worker's routing hint) and the REAL kind in `confluence_type` = `'space'`/`'page'` (see `routers/confluence_sync.py` `_translate_type` + the `new_sources` dict ~lines 210-231). The daemon's enumerator was written (and unit-tested) against `type: 'space'`, so it never handled real spaces. **Any future daemon connector work must read `confluence_type`, not `type`.** The daemon's own tests used the wrong shape — a test-vs-reality gap that passed cutover validation.
- **Why the saga looked like a wipe:** the OLD in-pod worker synced the Confluence space fine (routing on `type:'folder'`), so content existed. Post-cutover the daemon mis-routed → 0 items → empty full manifest → `/sync/diff` said "all deleted" → wipe. Three fixes stack: enumerate correctly (#319), never wipe on empty (#316a), never drop a source on a transient 403/404 (#316b).
- **Auth was never the problem for the shared Confluence KB.** It uses OAuth (admin-connected account); the run reported `completed` (not `needs_reauth`), which means the token resolved. The shared KB's `user_id` holds the admin's OAuth session (broker keys on `(provider, user_id)`, `routers/sync_daemon.py:issue_provider_token`).
- **Manifest modes matter for deletion safety** (`engine/run.py` + `sources/enumerator.py:combine_manifest_modes`): FULL mode (Confluence, first-sync/reset) trusts `/sync/diff`'s absence→deleted; PARTIAL mode (OneDrive/GDrive incremental) ignores diff absence and deletes only from the feed's removed-markers. That's why OneDrive never wiped. `combine_manifest_modes([])` = FULL, so the empty-all-revoked case is still guarded.
- **`_apply_run_summary` (`routers/sync_daemon.py:277`) REPLACES `meta.sources` with the daemon's reported sources on any clean `completed` terminal** — which is why dropping a source from the daemon's `_updated_sources` silently deleted it. Preserve, don't omit.
- **Chart env plumbing:** OWUI gets `SYNC_DAEMON_URL` + `SYNC_DAEMON_ENABLED` from the tenant configmap via `envFrom: configMapRef`; the OWUI→daemon key `SYNC_DAEMON_API_KEY` is a secretKeyRef added to the deployment (#232). `syncDaemonApiKey` ESO field on staging = true.

## Artifacts

- **Design doc (authoritative):** `thoughts/shared/plans/2026-07-22-sync-daemon-design.md` (in the monorepo-root thoughts) — §3 disposition table, §5.2 manifest modes, §10 cutover, D-1…D-10.
- **Prior handoffs (root thoughts):** `2026-07-22_20-03-00_sync-daemon-staging-deploy-validation.md`, `2026-07-23_11-31-16_loader-worker-searchapi-decommission.md`.
- **Merged PRs:** genai-utils #310/#316/#319; open-webui #232/#235; soev-gitops #66/#70.
- **Memory (cross-session state, read first):** `~/.claude/projects/-Users-lexlubbers-Code-soev-genai-utils/memory/sync-daemon-workstream-state.md`.
- **Daemon service:** `genai-utils/services/sync_daemon/` (its own CLAUDE.md).

## Action Items & Next Steps

1. **Re-validate on staging after the dev images redeploy** (daemon image from genai-utils dev post-#319; OWUI image from open-webui dev post-#235):
   - **Shared Confluence KB:** click "Nu synchroniseren" → it should now enumerate "Lex Lubbers" + "My first space" and sync their pages (was 0 files). If STILL 0 files → pull daemon logs and check the enumeration URL/count: `stern -n open-webui-staging sync-daemon --since 5m | grep -iE "manifest mode|enumerated|404|spaces/"`. A residual 0 would point to a `space_id` (numeric vs key) or `cloud_id` (OAuth gateway) issue in the source data. NOTE: no previder-prod kube context was available from the working machine this session — logs must be pulled from an env with that context.
   - **OneDrive single files:** re-pick 3 single files → they should appear as loose root files, not wrapper folders (existing wrongly-wrapped ones also flatten once #235 deploys).
   - **Confluence not wiping:** confirm a re-sync that returns nothing (or a transient blip) leaves the KB + its sources intact.
2. **STANDING GATE — do NOT promote genai-utils dev → test/main until NEO + mkbot are migrated off the search-api.** `deploy/projects/{neo,mkbot}` compose projects still build `vector-db-api`+`init-weaviate` from the deleted `api/`, and the GKN/cert deploy pipeline references those images. dev is safe (staging runs dev; staging's searchApi is off), but promotion would break NEO-class deploys.
3. **Optional low-risk follow-ups:** the daemon's confluence enumerator uses `space_id` for `/spaces/{id}/pages` — confirm the stored `space_id` is the NUMERIC id (v2 API requirement), not the space key, for the shared KB's spaces. Consider a real-tenant Confluence E2E test in `services/sync_daemon/tests/e2e` using the correct `confluence_type` shape.

## Other Notes

- **All session worktrees + the local dev stacks (docker `stack-1`, `stack-1-dp`) were torn down at the end of this session.** Recreate a stack via the `kickoff`/`stack` skills if needed for local E2E.
- The daemon and OWUI fixes are independent images: the Confluence fixes (#316/#319) ride the **daemon** image; the single-file tree fix (#235) rides the **OWUI** image. Both redeploy to staging via the dev channels.
- Cutover is no longer reversible on staging (loader-worker pod removed by gitops #66) — the daemon is the only cloud-sync path there now.
