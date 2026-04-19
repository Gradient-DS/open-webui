---
date: 2026-04-19T09:30:00+02:00
researcher: Lex Lubbers
git_commit: 5f6f1905d5132f9f21a612309fd2fe48bb81fd45
branch: merge/260416
repository: open-webui (Gradient-DS fork)
topic: "What customizations were lost during the upstream merge (260416) — beyond deleted_at?"
tags: [research, upstream-merge, 260416, soft-delete, archival, async-db, regression-audit]
status: complete
last_updated: 2026-04-19
last_updated_by: Lex Lubbers
---

# Research: Lost Customizations in Upstream Merge (merge/260416)

**Date**: 2026-04-19 09:30 CEST
**Researcher**: Lex Lubbers
**Git Commit**: `5f6f1905d` (merge commit)
**Branch**: `merge/260416`
**Repository**: open-webui (Gradient-DS fork)
**Comparison**: pre-merge fork HEAD `457f01af2` → post-merge HEAD `5f6f1905d`

## Research Question

> We merged upstream on this branch but I think we might have lost some of our customizations. One was the `deleted_at` functionality which we are adding back already and converting to async. Can you have a look at the pre-merge state to see if we have lost any other customizations?

## Summary

**Your instinct was correct — substantially more than `deleted_at` was lost.** The merge plan's "accept upstream" phases on `models/chats.py`, `models/users.py`, `models/files.py` wholesale-replaced our soft-delete code, and several custom guards in `routers/knowledge.py` / `models/knowledge.py` were dropped during the heavy-conflict phases. There are also collateral breakages in services (missing awaits after async conversion) and PII log-stripping regressions in `oauth.py`.

**Severity tiers:**

| Tier | Item | Impact |
|------|------|--------|
| **P0 runtime-breaking** | Soft-delete pipeline (chats/knowledge/users) | 15+ model methods dropped, 3 services call them + async without await → crash on invocation |
| **P0 runtime-breaking** | Cloud sync stack collaterally broken | `get_knowledge_bases_by_type` + `get_knowledge_files_by_file_id` dropped → OneDrive/Google Drive sync AttributeError on 11+ call sites |
| **P0 runtime-breaking** | Missing awaits | `ArchiveService.create_archive` (2 sites), `cleanup_expired_archives` (1 site), `get_suspension_info` (2 sites) — each either throws or silently returns a coroutine |
| **P1 silent data/security regression** | Chat/KB delete now hard-deletes | Bypasses GDPR staged deletion, orphans vectors + storage |
| **P1 silent data/security regression** | PII stripping regressed in `oauth.py` | 8 log sites leak OAuth tokens / full user_data / emails; `UserModel.__repr__` defensive guard gone |
| **P1 feature regression** | Knowledge router guards lost | File-count limits, non-local file-delete guard, orphan cleanup, type-strip-on-update, access-grants-400 |
| **P1 feature regression** | Data-warnings bypassed in queue paths | `submitPrompt`/`submitHandler` split routes queued messages around the warning |
| **P2 partial regression** | Data-warnings model editor UI | `ModelEditor.svelte` lost all 12 DataWarnings integration lines; `utils/models.py` lost merge block |
| **P2 minor** | `requirements-min.txt` CVE pins | `wheel==0.46.2` + `cryptography==46.0.7` regressed to unpinned |
| **P2 minor** | `routers/auths.py:523` | Group sync log still dumps full `{user_groups}` list |
| **P2 missed upstream fix** | `routers/retrieval.py` fallback path | Sync `loader.load` + `Storage.get_file` on event loop in custom try-external-then-fallback branch |

**Clean / intact:**
- `main.py` wiring: all router mounts, schedulers, periodic tasks, state config
- `configs.py` endpoint bodies: byte-identical for all 6 custom groups
- `models/auths.py` + `routers/auths.py`: TOTP columns/methods/flows correctly async-converted; upstream security fixes adopted
- i18n: 0 keys lost, 0 Dutch values regressed (merge script worked)
- Helm chart + Dockerfile + `.github/` workflows: untouched by the merge
- Most frontend components: UserMenu, Chat.svelte (Google Drive), MessageInput, InputMenu (feature flag guards, cloud picker items), admin panel tabs, 2FA, data export UI, KB logos/badges

## Detailed Findings

### 1. Soft-delete / GDPR archival (backend/open_webui/models/* + services/deletion/ + services/retention/)

The Phase 2 "accept upstream" resolution for `models/chats.py`, `models/users.py`, `models/files.py` dropped the entire soft-delete custom layer.

#### 1a. `models/chats.py` — column + 8 methods dropped

- **Dropped column** in `Chat` SQLAlchemy table + `ChatModel` Pydantic: `deleted_at = Column(BigInteger, nullable=True, index=True)` (pre-merge line ~40)
- **Dropped index**: `Index('user_id_archived_idx', 'user_id', 'archived')`
- **Dropped methods** (still called by services):
  - `soft_delete_by_user_id_and_folder_id` (pre `chats.py:1463`)
  - `get_pending_deletions` (pre `chats.py:~1592`) — used by `cleanup_worker.py:114`
  - `get_stale_chats` (pre `chats.py:1604`) — used by `retention/service.py:229`
  - `soft_delete_by_id` (pre `chats.py:1618`) — used by `retention/service.py:233`
  - `soft_delete_by_user_id` (pre `chats.py:1630`) — used by `deletion/service.py:438`
  - `get_chat_by_id_unfiltered` (pre `chats.py:~1636`) — used by `deletion/service.py:219`
  - `get_referenced_file_ids` (pre `chats.py:~1645`) — used by `deletion/service.py:152`
  - `get_files_by_chat_id` (pre `chats.py:~1556`) — used by `cleanup_worker.py:126`, `deletion/service.py:225`
- **Dropped filter** `.filter(Chat.deleted_at.is_(None))` from every list/get method (14+ methods)
- **Migration `d4e5f6a7b8d0_add_soft_delete_columns.py` still runs**, creating an orphan `deleted_at` column the ORM no longer maps

#### 1b. `models/knowledge.py` — 8 methods + 2 fields dropped

- **Dropped methods**:
  - `get_knowledge_bases_by_type` (pre `knowledge.py:399`) — **6 call sites in sync workers**: `routers/integrations.py:114`, `services/sync/router.py:302, 335`, `services/sync/scheduler.py:85`, `services/sync/token_refresh.py:70`
  - `get_knowledge_items_by_user_id` (pre `knowledge.py:430`)
  - `get_knowledge_files_by_file_id` (pre `knowledge.py:492`) — **6 call sites**: `services/deletion/service.py:80`, `services/onedrive/sync_worker.py:408`, `services/google_drive/sync_worker.py:345`, `services/sync/router.py:434`, `services/sync/base_worker.py:347, 987`
  - `get_referenced_file_ids` (pre `knowledge.py:501`)
  - `get_stale_knowledge` (pre `knowledge.py:760`)
  - `soft_delete_by_id` (pre `knowledge.py:782`) — used by `retention/service.py:251`, `routers/integrations.py:666`
  - `soft_delete_by_user_id` (pre `knowledge.py:794`) — used by `deletion/service.py:431`
  - `get_knowledge_by_id_unfiltered` (pre `knowledge.py:806`) — used by `deletion/service.py:285`
  - sync `is_suspended` (pre `knowledge.py:843`) — used by `retrieval/utils.py:1086`
- **Dropped Pydantic fields**: `KnowledgeUserModel.suspension_info: Optional[dict] = None`, `FileUserResponse.added_at: Optional[int] = None`
- **Dropped annotation logic** in `get_knowledge_bases_for_user_by_filter` (pre lines 284-312) that attached suspension info to cloud KBs in the list response
- **Dropped `type_filter`** in list query (pre lines 256-258)
- **Dropped local-only filter** in `search_knowledge_files` (pre line 335) — users now get search hits on cloud KB files that can't be attached
- **`get_suspension_info()` is a fragile sync shim** with `asyncio.get_event_loop() + ThreadPoolExecutor + asyncio.run` bridge; callers should switch to `async_get_suspension_info`

#### 1c. `models/users.py` — 1 method dropped

- `get_inactive_users` (pre `users.py:552`) — used by `retention/service.py:115, 184`
- Also: `UserModel.__repr__` / `__str__` defensive guard dropped (see §5 below)

#### 1d. Services calling dropped methods + missing awaits

`backend/open_webui/services/deletion/service.py` (blob unchanged, dependencies changed underneath):
- Calls **dropped methods**: lines 80, 151, 152, 219, 225, 285, 431, 438
- Calls **async methods without await**: lines 77, 101, 142, 168, 245, 247, 254, 273, 311, 323, 367, 370–478

`backend/open_webui/services/deletion/cleanup_worker.py` (blob unchanged):
- Calls dropped `Chats.get_pending_deletions` (line 114), `Chats.get_files_by_chat_id` (line 126)
- Calls async without await: lines 76, 133, 141, 161, 172

`backend/open_webui/services/retention/service.py` (blob unchanged):
- Calls dropped: `Users.get_inactive_users` (115, 184), `Chats.get_stale_chats` (229), `Chats.soft_delete_by_id` (233), `Knowledges.get_stale_knowledge` (247), `Knowledges.soft_delete_by_id` (251)
- Calls async without await: `Users.update_user_by_id` (142), `ArchiveService.create_archive` (195 — note: pre-merge was 180)

`backend/open_webui/routers/users.py:706` — `ArchiveService.create_archive(...)` not awaited (→ `TypeError: coroutine has no attribute 'success'`).
`backend/open_webui/main.py:744` — `ArchiveService.cleanup_expired_archives()` not awaited.
`backend/open_webui/retrieval/utils.py:1086` — sync `Knowledges.is_suspended(...)` (should be `await async_is_suspended`).

#### 1e. Router delete endpoints silently switched to hard-delete

- `routers/chats.py` delete endpoints: pre-merge used `soft_delete_by_id/user_id`, post-merge calls `delete_chat_by_id[_and_user_id]` / `delete_chats_by_user_id` → **immediate hard-delete**, orphaning vectors + storage files.
- `routers/knowledge.py DELETE /{id}/delete`: same regression, plus the `log.info(f'Soft-deleting knowledge base: ...')` log line is gone.

### 2. `routers/knowledge.py` custom guards dropped

Cloud KB guards lost during Phase 14 resolution:

- **Update endpoint strip-type guard** (pre line 512) — `form_data.type = None` to prevent changing type after creation. Users can now PATCH a KB's `type`.
- **Non-local access-grants rejection** (pre line 587) — `/{id}/access/update` no longer returns 400 for non-local KBs.
- **File count limits against `KNOWLEDGE_MAX_FILE_COUNT`** — two sites lost: pre-merge line 719 (`/add` single-file) and 1161 (`/files/batch/add` multi-file).
- **Never-delete-underlying-file for non-local KBs** (pre line 874) — `if knowledge.type != 'local': delete_file = False`. Files from OneDrive/Google Drive sync are now physically deleted when removed from a KB (breaking shared references).
- **Orphaned-file cleanup for non-local KBs** (pre lines 1011-1017) — gone.
- **Meta-based vs column-based type check** at post-merge line 505 reads `knowledge.meta.get('type', 'local')` (upstream pattern) instead of `knowledge.type` (our column). Inconsistent with the model — update path bypasses the DB column.

### 3. Frontend data-warnings bypass in queue paths

`src/lib/components/chat/Chat.svelte`: Upstream refactor split `submitPrompt` into `submitHandler` (wraps `checkDataWarnings` at line 2212) and `submitPrompt` (lines 2062-2089, no warnings check). Two local callers now bypass the guard:

- `Chat.svelte:1577` — `processNextInQueue` → `submitPrompt(...)` (was warnings-checked pre-merge line 1467)
- `Chat.svelte:3286` — `onQueueSendNow` → `submitPrompt(...)` (was warnings-checked pre-merge line 3191)

Fix: swap to `submitHandler` at both sites, OR move the `checkDataWarnings` guard into `submitPrompt` itself (preferred — defensive against future upstream).

### 4. Data-warnings admin/editor partial regression

- `src/lib/components/workspace/Models/ModelEditor.svelte` — went 940 → 921 lines. All 12 DataWarnings integration sites removed (import, state, meta-save, default-load, model-load, UI). Per-model data-warning config can no longer be edited from the model editor UI.
- `backend/open_webui/utils/models.py:308-311` — `elif key == 'data_warnings':` merge block gone. Admin default `data_warnings` no longer propagate to models that don't explicitly override.

(`models/data_warnings.py`, `routers/data_warnings.py`, admin `Settings/Models/ModelSettingsModal.svelte`, `chat/Chat.svelte` `checkDataWarnings`, migration, API client, config `ENABLE_DATA_WARNINGS`, router mount, `features.enable_data_warnings` flag → all present.)

### 5. PII / credential log stripping regressions (`utils/oauth.py`)

Security fix `9ab055993` regressed to upstream in 8 sites in `backend/open_webui/utils/oauth.py`:

| Line | Content |
|------|---------|
| 931  | `log.error(f'Invalid token response for client_id {client_id}: {token}')` — should be `error_desc` only |
| 1495 | `log.warning(f'OAuth callback failed, user data is missing: {token}')` — token should be removed |
| 1505 | `log.warning(f'OAuth callback failed, sub is missing: {user_data}')` — full user_data leak |
| 1550 | `log.warning(f'... email is missing: {user_data}')` |
| 1559 | `log.warning(f'... domain is not allowed: {user_data}')` |
| 1588 | `log.debug(f'Updated name for user {user.email}')` — should be `user.id` |
| 1598 | `f'Cannot update email to {new_email} for user {user.id}...'` — `{new_email}` should be dropped |
| 1618 | `log.debug(f'Updated profile picture for user {user.email}')` |
| 1954 | **NEW upstream line** `f'(email={user.email}, provider={matched_provider}, ...)'` — needs our sanitization applied |

Also lost: `backend/open_webui/models/users.py` `UserModel.__repr__` / `__str__` that restricted output to `id` + `role`. Any future f-string log of a `UserModel` instance will dump the full pydantic repr (email, name, etc.).

Minor: `backend/open_webui/routers/auths.py:523` — still `log.info(f'Successfully synced groups for user {user.id}: {user_groups}')` (should be `{len(user_groups)} groups`).

### 6. `routers/retrieval.py` missed upstream fix in custom fallback

Upstream commit `a3ea7bf04` (PR #23705) converted sync loader calls to `await loader.aload(...)` to unblock the event loop. Our custom try-external-then-fallback path kept the sync calls:

- `backend/open_webui/routers/retrieval.py:1708` — `Storage.get_file(file_path)` → should be `await asyncio.to_thread(Storage.get_file, file_path)` (compare line 1765 which has the fix)
- `backend/open_webui/routers/retrieval.py:1742` — `loader.load(file.filename, file.meta.get('content_type'), file_path)` → should be `await loader.aload(...)` (compare line 1838 which has the fix)

Both are inside the "external pipeline unavailable → fallback to inline load" branch. Will freeze the event loop on non-trivial PDFs/DOCX during fallback.

### 7. `requirements-min.txt` CVE pin regressions

`backend/requirements-min.txt`:
- `cryptography==46.0.7` → regressed to unpinned `cryptography`
- `wheel==0.46.2` → line removed entirely

Both were pins from the `31-03-2026 Security Hardening Sprint`. Still present in sibling `requirements.txt` + `requirements-slim.txt`, so production Docker is unaffected. Restore for consistency.

### 8. What's intact (verified)

- **`main.py`**: all 10 custom router mounts, all periodic tasks + schedulers, all `app.state.config` wiring, all middlewares (Redirect, SecurityHeaders, SessionAutoload, Compress), agent API bypass, SOEV branding — verified present. The `BaseHTTPMiddleware` → pure-ASGI refactor is functional equivalence, no loss.
- **`configs.py`**: all 6 custom endpoint groups (invite content/settings, agent proxy, 2FA, data retention, integrations, greeting template) byte-identical for the custom portion.
- **`models/auths.py`** + **`routers/auths.py`**: TOTP columns + methods correctly async-converted; partial-JWT flow, acceptance modal config present; all 3 upstream security fixes adopted.
- **i18n**: 2471 → 2559 keys; 0 keys lost, 0 Dutch values regressed. Merge script worked.
- **Helm + Dockerfile + `.github/`**: untouched by the merge. All custom env vars, security context, CVE pins intact.
- **Custom frontend features**: Document Pane, DocumentCard, source reference list, Word/PDF export, admin panel tabs (Acceptance, Security, Database, IntegrationProviders, Integrations, Email), 2FA UI, data export UI, KB logos/type badges, agents/prompts tab split, feature flag utils, all API client modules — all present.
- **`routers/users.py`** admin 2FA status/disable endpoints: present and async.

## Code References

### P0 runtime-breaking fixes

- `backend/open_webui/models/chats.py` — restore 8 methods + `deleted_at` column + `ChatModel.deleted_at` field + `.filter(Chat.deleted_at.is_(None))` across list/get methods
- `backend/open_webui/models/knowledge.py` — restore 8 methods + 2 Pydantic fields + suspension annotation + `type_filter` + local-only search filter
- `backend/open_webui/models/users.py:552` — restore `get_inactive_users`
- `backend/open_webui/services/deletion/service.py` — async-refactor all methods + add awaits
- `backend/open_webui/services/deletion/cleanup_worker.py` — async-refactor + add awaits
- `backend/open_webui/services/retention/service.py` — async-refactor + add awaits
- `backend/open_webui/routers/users.py:706` — add `await` before `ArchiveService.create_archive`
- `backend/open_webui/main.py:744` — add `await` before `ArchiveService.cleanup_expired_archives`
- `backend/open_webui/retrieval/utils.py:1086` — switch to `await Knowledges.async_is_suspended(...)`

### P1 regressions

- `backend/open_webui/routers/chats.py` — revert delete endpoints to soft-delete pattern
- `backend/open_webui/routers/knowledge.py` — revert delete endpoints to soft-delete; restore guards (type strip, file count limits, non-local delete_file=False, orphan cleanup, access-grants 400); switch lines 421+495 to `async_get_suspension_info`
- `backend/open_webui/routers/integrations.py:114,666` — restore or re-implement callers of dropped methods
- `backend/open_webui/utils/oauth.py` — re-apply 8 log-sanitization sites + sanitize new line 1954
- `backend/open_webui/models/users.py` — restore `UserModel.__repr__` / `__str__`
- `backend/open_webui/utils/models.py` — restore `elif key == 'data_warnings':` merge block (~line 308)
- `src/lib/components/workspace/Models/ModelEditor.svelte` — restore DataWarnings integration (12 sites)
- `src/lib/components/chat/Chat.svelte:1577,3286` — route queue paths through `submitHandler` (or move `checkDataWarnings` into `submitPrompt`)

### P2 minor

- `backend/requirements-min.txt` — re-pin `cryptography==46.0.7`, restore `wheel==0.46.2`
- `backend/open_webui/routers/auths.py:523` — strip `{user_groups}` from log
- `backend/open_webui/routers/retrieval.py:1708,1742` — apply upstream async fix (PR #23705) to custom fallback branch

### Collateral (depend on P0 model restore)

- `backend/open_webui/services/sync/router.py:302,335,434`
- `backend/open_webui/services/sync/scheduler.py:85`
- `backend/open_webui/services/sync/token_refresh.py:70`
- `backend/open_webui/services/sync/base_worker.py:347,987`
- `backend/open_webui/services/onedrive/sync_worker.py:408`
- `backend/open_webui/services/google_drive/sync_worker.py:345`

## Architecture Insights

1. **Phase 2 "accept upstream" was the main loss vector.** The merge plan marked `models/chats.py`, `models/users.py`, `models/files.py` as "no custom code" and accepted upstream wholesale. This was incorrect — all three hosted soft-delete columns and methods from commit `d4e5f6a7b8c9_add_soft_delete_columns.py`. The check script at plan line 104 (`grep soev|gradient|onedrive|...`) didn't include `deleted_at|soft_delete|archived_at`, so it returned a false negative.

2. **The async-DB refactor created a hidden third-order break.** Even files with no merge conflict (services/deletion, services/retention, cleanup_worker) now interact with async models, so every sync call returns a coroutine. This is why the blob hashes of these services are unchanged pre/post yet they're broken.

3. **Upstream refactors reshape custom guard surfaces.** The `submitPrompt` → `submitHandler` split in Chat.svelte and the `BaseHTTPMiddleware` → pure-ASGI split in main.py are examples where upstream moved the code around without deleting it — but our guards that lived in the original function now need reapplying to the new shape. Automated merge can't catch this.

4. **Migrations can outlive their model code.** The `d4e5f6a7b8d0_add_soft_delete_columns.py` migration will still run in deploy, creating `deleted_at` columns that the post-merge ORM doesn't map. Harmless in isolation but a sign of the broken link between migration and model.

## Related Research

- `collab/docs/upstream-merge-260416-plan.md` — original 20-phase merge plan
- `collab/index.md` 16-04-2026 entry — pre-merge analysis (245 commits)
- `collab/index.md` 20-03-2026 entry — custom features inventory (9 features documented then)

## Open Questions

1. **Priority & batching for restoration:** restore everything in one big PR, or split P0-runtime (unblocks local smoke test) from P1-regression (security + data correctness) from P2-minor? Suggest: P0 = one PR (large, async-DB-aware, needs testing), P1 security (oauth.py + UserModel.__repr__) = separate small PR, P1 feature + P2 = bundled.
2. **Should `get_suspension_info()` sync shim be removed entirely** or kept for sync callers? All router callers can be migrated to `async_get_suspension_info`. Recommend delete the shim.
3. **`routers/knowledge.py` line 505** reads `knowledge.meta.get('type', 'local')` — is the upstream meta-based type a new upstream feature, or is this the merge accepting upstream over our column? Check upstream commit history.
4. **The `docker-compose.soev-local.yaml` new file** — was this intentional Gradient addition during merge, or did it slip in from another branch? Header says "Soev local development stack"; likely intentional but worth confirming.
