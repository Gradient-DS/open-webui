# Upstream Merge (260416) — Remaining Regression Fixes Implementation Plan

## Overview

The `merge/260416` branch merged 245 upstream commits including the
sync→async DB refactor. Research
(`thoughts/shared/research/2026-04-19-upstream-merge-260416-lost-customizations.md`)
found ~20 regression items. The soft-delete / GDPR-archival surface has
already been restored via the earlier plan at
`thoughts/shared/plans/2026-04-19-async-soft-delete-surface.md` (complete
through Phase 6 automated verification). This plan covers **everything
else**: P0 runtime-breaking items, P1 silent data/security regressions,
and P2 minor items, bundled into one PR in five phases.

## Current State Analysis

### Audit verdict (what still needs fixing after the soft-delete plan)

**P0 runtime-breaking — will crash on invocation**
- `Users.get_inactive_users` missing → `retention/service.py:115,184` `AttributeError`
- `Knowledges.get_knowledge_bases_by_type` missing → 5 call sites `AttributeError`
- `Knowledges.get_knowledge_files_by_file_id` exists but **5 of 6 callers miss `await`**, plus `Knowledges.get_knowledge_bases_by_user_id` is async with **2 unawaited callers** → coroutines used as lists
- `routers/users.py:705` `ArchiveService.create_archive(...)` not awaited
- `main.py:744` `ArchiveService.cleanup_expired_archives()` not awaited
- `retrieval/utils.py:1086` calls `Knowledges.is_suspended(...)` which doesn't exist → `AttributeError`
- Dropped Pydantic fields `KnowledgeUserModel.suspension_info` and `FileUserResponse.added_at`

**P1 silent data/security regressions**
- `routers/chats.py` three delete endpoints (`:505`, `:1042`, `:1060`) hard-delete; bypass GDPR staged deletion
- `routers/knowledge.py:1051` DELETE endpoint hard-deletes; same bypass
- `utils/oauth.py` 9 log sites leak full `{token}` / `{user_data}` / `{user.email}` / `{new_email}`
- `models/users.py` `UserModel.__repr__` / `__str__` defensive guard dropped (full-pydantic dump on any `f'...{user}...'`)
- `routers/knowledge.py` 6 custom guards dropped (type-strip-on-update, non-local access-400, file-count limits ×2, non-local `delete_file=False`, orphan cleanup for non-local)
- `routers/knowledge.py:505` type check reads `knowledge.meta.get('type', 'local')` instead of `knowledge.type`
- Dropped `type_filter` from list query + dropped local-only filter in `search_knowledge_files` + dropped suspension annotation on listed KBs
- `src/lib/components/chat/Chat.svelte:1577,3286` queue paths (`processNextInQueue`, `onQueueSendNow`) call `submitPrompt` directly, bypassing `checkDataWarnings` (guard lives only in `submitHandler`)

**P2 minor**
- `src/lib/components/workspace/Models/ModelEditor.svelte` — 12 DataWarnings integration sites removed (per-model data-warning config no longer editable)
- `backend/open_webui/utils/models.py` — `elif key == 'data_warnings':` merge block gone (admin default warnings no longer propagate)
- `backend/requirements-min.txt` — lost `cryptography==46.0.7` and `wheel==0.46.2` pins
- `backend/open_webui/routers/auths.py:523` log line dumps full `{user_groups}` list
- `backend/open_webui/routers/retrieval.py:1708,1742` — custom external-pipeline fallback path missed upstream async fix (sync `Storage.get_file` + `loader.load` on event loop); parallel path at `:1765,:1838` has the fix applied

### Key file inventory

| File | Role |
|---|---|
| `backend/open_webui/models/users.py` | Add `get_inactive_users` (async) + restore `__repr__`/`__str__` guard |
| `backend/open_webui/models/knowledge.py` | Add `get_knowledge_bases_by_type` (async); add `KnowledgeUserModel.suspension_info` + `FileUserResponse.added_at` fields; extend `search_knowledge_bases` with local-only filter for non-admins (port pre-merge behavior); restore `type_filter` in list endpoint |
| `backend/open_webui/services/retention/service.py` | Awaits for the now-restored `get_inactive_users` (likely already present since the soft-delete plan; verify only) |
| `backend/open_webui/routers/users.py` | Add `await` before `ArchiveService.create_archive` (line 705) |
| `backend/open_webui/main.py` | Add `await` before `ArchiveService.cleanup_expired_archives` (line 744) |
| `backend/open_webui/retrieval/utils.py` | Swap sync `Knowledges.is_suspended(...)` to `await Knowledges.async_is_suspended(...)` (line 1086) |
| `backend/open_webui/services/export/service.py` | Add `await` before `Knowledges.get_knowledge_bases_by_user_id` (line 130) and make enclosing function `async` |
| `backend/open_webui/services/sync/router.py` | Add `await` before `get_knowledge_bases_by_user_id` (:250), and `await` the 5 `get_knowledge_bases_by_type` restoration call sites once the method is back (:302, :335) + the other 2 in sync/scheduler.py + sync/token_refresh.py, and the `get_knowledge_files_by_file_id` callers in router (:434) + base_worker (:347, :987) |
| `backend/open_webui/services/google_drive/sync_worker.py` + `onedrive/sync_worker.py` | Add `await` before `get_knowledge_files_by_file_id` |
| `backend/open_webui/routers/integrations.py` | `await` restored `get_knowledge_bases_by_type` at :114 |
| `backend/open_webui/routers/chats.py` | Three delete endpoints → soft-delete pattern |
| `backend/open_webui/routers/knowledge.py` | DELETE `/{id}/delete` → soft-delete; restore 6 guards; swap meta-based → column-based type check; attach suspension annotation to list responses (router-side, keep model pure) |
| `backend/open_webui/utils/oauth.py` | 9 log-site sanitizations |
| `backend/open_webui/routers/auths.py` | Line 523 strip `user_groups` |
| `backend/open_webui/routers/retrieval.py` | Lines 1708 + 1742 — apply upstream async fix to fallback branch |
| `backend/open_webui/utils/models.py` | Restore `elif key == 'data_warnings':` merge block |
| `backend/requirements-min.txt` | Re-pin `cryptography==46.0.7`, restore `wheel==0.46.2` |
| `src/lib/components/chat/Chat.svelte` | Move `checkDataWarnings` guard into `submitPrompt` (preferred, single fix) |
| `src/lib/components/workspace/Models/ModelEditor.svelte` | Restore 12 DataWarnings integration sites (import, state, save, load, UI) |

### Pre-merge source of truth

Pre-merge fork HEAD is commit `457f01af2`. For every restored
block/method, the implementer should run
`git show 457f01af2:<path>` and port semantics forward, **converting
sync → async where the post-merge model surface is async** (e.g.
`get_inactive_users` sync `with get_db_context(...)` → async
`async with get_async_db_context(...)`).

## Desired End State

1. Backend boots clean and stays clean for ≥5 minutes on `merge/260416`
   — no `AttributeError`, no `TypeError: object ... is not awaitable`,
   no `RuntimeWarning: coroutine ... was never awaited` from any of:
   cleanup worker, retention loop, sync scheduler, token refresh,
   OneDrive/Google Drive sync worker, deletion service, archive service.
2. Retention service's inactive-user enforcement path runs end-to-end
   (archival, warning emails, actual deletion) — manual test via
   `POST /api/v1/configs/data-retention/test`.
3. All 5 call sites of `get_knowledge_bases_by_type` return the correct
   filtered list.
4. Chat and KB delete endpoints soft-delete (move to `deleted_at`
   column); hard-delete happens only via the cleanup worker after the
   retention window.
5. All 9 OAuth log sites sanitize PII. `UserModel.__repr__` returns
   `User(id=..., role=...)` regardless of other fields set.
6. Non-local KBs cannot have their type changed via update, cannot have
   access grants assigned via `/access/update`, respect
   `KNOWLEDGE_MAX_FILE_COUNT`, preserve underlying files on
   file-removal, and clean up orphaned non-local file records when a
   KB is deleted.
7. Data-warnings acceptance modal fires for queued-prompt submissions
   as well (not just direct-entry).
8. Admin-configured default `data_warnings` merge into per-model
   metadata when not overridden; model editor UI exposes per-model
   data-warning config again.
9. `cryptography` and `wheel` CVE pins restored in `requirements-min.txt`.
10. `routers/retrieval.py` fallback branch no longer blocks the event
    loop on PDF/DOCX loads.

### Verification

- `open-webui dev` clean-start + `pytest` (if any) green
- `npm run build` succeeds; `npm run lint:frontend` clean on
  `Chat.svelte` and `ModelEditor.svelte`
- `npm run lint:backend` clean on all touched backend files
- Manual smoke tests listed per phase below

### Key Discoveries

- `ArchiveService.create_archive` and `cleanup_expired_archives` are
  **already async** (`services/archival/service.py:104` and `:183`);
  the regression is purely missing `await` at the two call sites.
- `Knowledges.is_suspended` does not exist post-merge — only
  `async_is_suspended` (`models/knowledge.py:859`) and
  `get_suspension_info`/`async_get_suspension_info` (`:792`, `:835`).
  The pre-merge sync `is_suspended` was a shim; don't restore it.
- `get_knowledge_items_by_user_id` (pre-merge line 430) has **zero
  callers** in the post-merge codebase. Do not restore it — scope keeps
  to code that is actually used.
- Post-merge `get_knowledge_bases_by_user_id` (`knowledge.py:377`) is
  `async def` but two callers still treat it as sync:
  - `backend/open_webui/services/export/service.py:130` — inside
    `_collect_kb_data`; this is called from the export background task
    and will currently break zip generation.
  - `backend/open_webui/services/sync/router.py:250` — inside
    `get_user_sync_status` (FastAPI handler).
- Post-merge the pre-merge method `get_knowledge_bases_for_user_by_filter`
  no longer exists. The router's `GET /knowledge/` list now uses
  `search_knowledge_bases` + `get_knowledge_bases_by_user_id`. The
  pre-merge suspension annotation attached
  `KnowledgeUserModel.suspension_info` per row inside that method. In
  this plan we restore the **field** on the Pydantic model and do the
  annotation **router-side** (after calling the model methods) to stay
  close to upstream.
- Pre-merge `get_knowledge_bases_by_type` filtered `deleted_at IS NULL`;
  the async restoration must preserve that filter.
- Pre-merge `UserModel.__repr__` at `models/users.py:117` of commit
  `457f01af2` returns `f'User(id={self.id}, role={self.role})'` — that's
  the form to restore.
- `routers/retrieval.py:1708/1742` (fallback path) is structurally
  parallel to `:1765/1838` (main path). The latter has the async fix
  from upstream PR #23705 already applied. Apply the same diff to the
  fallback: wrap `Storage.get_file` in `asyncio.to_thread` and swap
  `loader.load` → `await loader.aload`.
- Pre-merge `utils/models.py` `data_warnings` merge block lives inside
  the `for key, value in default_metadata.items():` loop, as an `elif`
  between `capabilities` and the final `elif meta.get(key) is None:`.

## What We're NOT Doing

- Not restoring `get_knowledge_items_by_user_id` (zero callers, dead
  code — research doc flagged it but audit confirms it's unused).
- Not restoring a sync `Knowledges.is_suspended` shim — the one caller
  is migrated to `async_is_suspended`.
- Not re-running the merge or changing the 260416 merge commit.
- Not touching the sync worker abstraction's architecture — only
  threading `await` through newly-async calls.
- Not adding any new features; everything here is a regression fix.
- Not adding unit tests for pre-existing untested behavior (would
  bloat PR); manual smoke tests per phase + post-merge full regression
  walk-through cover correctness.
- Not re-verifying soft-delete-plan automated checks — that plan owns
  its own verification. This plan only re-exercises them incidentally
  via the startup smoke test.

## Implementation Approach

Five phases, each independently committable and verifiable. Run through
them in order — Phase 2 depends on Phase 1's restored methods existing;
Phase 3 depends on ChatTable/KnowledgeTable async soft-delete methods
(already done). Phases 4 and 5 are independent of 1–3 but grouped for
shipping convenience.

---

## Phase 1: P0 Model Surface Restoration

### Overview

Restore the two missing async model methods (`Users.get_inactive_users`,
`Knowledges.get_knowledge_bases_by_type`), two dropped Pydantic fields
(`KnowledgeUserModel.suspension_info`, `FileUserResponse.added_at`),
and the `UserModel.__repr__`/`__str__` defensive guard.

### Changes Required

#### 1. `backend/open_webui/models/users.py` — restore `get_inactive_users` (async)

Location: after `get_num_users_active_today` (which is at
`models/users.py:~545` in current post-merge file — grep to confirm).
Port the pre-merge implementation at `git show
457f01af2:backend/open_webui/models/users.py` lines 552–575 forward to
the async shape used by the rest of the post-merge `UsersTable`:

```python
async def get_inactive_users(
    self,
    inactive_since: int,
    limit: int = 50,
    exclude_roles: Optional[list[str]] = None,
    db: Optional[AsyncSession] = None,
) -> list[UserModel]:
    """Find users whose last_active_at is before the given timestamp.

    Args:
        inactive_since: epoch timestamp — users active before this are inactive
        limit: max users to return per batch
        exclude_roles: roles to skip (e.g., ['admin'] to protect admin accounts)
    """
    async with get_async_db_context(db) as db:
        stmt = select(User).filter(User.last_active_at < inactive_since)
        if exclude_roles:
            stmt = stmt.filter(User.role.notin_(exclude_roles))
        stmt = stmt.order_by(User.last_active_at.asc()).limit(limit)
        result = await db.execute(stmt)
        return [UserModel.model_validate(user) for user in result.scalars().all()]
```

Verify the two call sites already use `await`:
- `backend/open_webui/services/retention/service.py:~115` inside
  `_send_warning_emails`
- `backend/open_webui/services/retention/service.py:~184` inside
  `_cleanup_inactive_users`

If either was converted during the soft-delete plan's Phase 4 but left
without `await`, fix it now.

#### 2. `backend/open_webui/models/users.py` — restore `UserModel.__repr__`/`__str__`

Locate `class UserModel(BaseModel):` and add:

```python
def __repr__(self) -> str:
    return f'User(id={self.id}, role={self.role})'

def __str__(self) -> str:
    return self.__repr__()
```

Place them after the last field declaration and any `@field_validator`
blocks, before the next top-level class (match the shape at
`git show 457f01af2:backend/open_webui/models/users.py:117`).

#### 3. `backend/open_webui/models/knowledge.py` — restore `get_knowledge_bases_by_type` (async)

Append after `get_knowledge_bases_by_user_id` (line 377+). Port
pre-merge `457f01af2:models/knowledge.py:399`:

```python
async def get_knowledge_bases_by_type(
    self, type: str, db: Optional[AsyncSession] = None
) -> list[KnowledgeModel]:
    """Get all knowledge bases of a specific type (no pagination limit)."""
    async with get_async_db_context(db) as db:
        result = await db.execute(
            select(Knowledge)
            .filter_by(type=type)
            .filter(Knowledge.deleted_at.is_(None))
            .order_by(Knowledge.updated_at.desc())
        )
        return [KnowledgeModel.model_validate(kb) for kb in result.scalars().all()]
```

#### 4. `backend/open_webui/models/knowledge.py` — restore Pydantic fields

- `KnowledgeUserModel` (class around line 110): add
  ```python
  suspension_info: Optional[dict] = None
  ```
  after the existing `user: Optional[UserResponse] = None`.
- `FileUserResponse` (class around line 129): add
  ```python
  added_at: Optional[int] = None
  ```
  after `user: Optional[UserResponse] = None`.

Neither addition requires DB changes — they are response-time
annotations only.

### Success Criteria

#### Automated Verification

- [x] `grep -n "async def get_inactive_users" backend/open_webui/models/users.py` returns one match
- [x] `grep -n "async def get_knowledge_bases_by_type" backend/open_webui/models/knowledge.py` returns one match
- [x] `grep -n "def __repr__\|def __str__" backend/open_webui/models/users.py` returns two matches
- [x] `grep -n "suspension_info: Optional\[dict\]" backend/open_webui/models/knowledge.py` returns one match
- [x] `grep -n "added_at: Optional\[int\]" backend/open_webui/models/knowledge.py` returns one match (inside `FileUserResponse`)
- [ ] `python -W error::RuntimeWarning -c "from open_webui.models.users import Users; from open_webui.models.knowledge import Knowledges; import inspect; assert inspect.iscoroutinefunction(Users.get_inactive_users); assert inspect.iscoroutinefunction(Knowledges.get_knowledge_bases_by_type)"` exits 0
- [ ] `npm run lint:backend` passes on both files
- [ ] `npm run format:backend` produces no diff on both files

#### Manual Verification

- [ ] In a Python REPL: `repr(UserModel(id='x', role='admin', name='n', email='e@e', last_active_at=0, updated_at=0, created_at=0))` returns `'User(id=x, role=admin)'` (no email leaked)

**Implementation Note**: Pause here for human confirmation that manual verification passed before moving to Phase 2.

---

## Phase 2: P0 Missing Awaits

### Overview

Thread `await` through every call site that became async either from
Phase 1 of this plan or from the soft-delete plan but was missed by
the merge. Also fix the two `ArchiveService` await bugs and the
`is_suspended` method-doesn't-exist bug in `retrieval/utils.py`.

### Changes Required

#### 1. `backend/open_webui/routers/users.py` — line ~705

```python
# Before
archive_result = ArchiveService.create_archive(user=user, ...)

# After
archive_result = await ArchiveService.create_archive(user=user, ...)
```

Grep for `ArchiveService.create_archive` in `routers/users.py` to find
the exact line (delete-user handler). Confirm the enclosing function is
already `async def` (it is — it's the handler for the admin delete-user
endpoint).

#### 2. `backend/open_webui/main.py` — line ~744

```python
# Before
stats = ArchiveService.cleanup_expired_archives()

# After
stats = await ArchiveService.cleanup_expired_archives()
```

Grep for `cleanup_expired_archives` in `main.py`. Confirm the
enclosing scheduled-task callback is `async def`.

#### 3. `backend/open_webui/retrieval/utils.py` — line ~1086

```python
# Before
if knowledge_base and Knowledges.is_suspended(knowledge_base.id):

# After
if knowledge_base and await Knowledges.async_is_suspended(knowledge_base.id):
```

Enclosing function is already `async def` (grep up the file to confirm
— this is inside the retrieval pipeline which is fully async).

#### 4. `services/google_drive/sync_worker.py` — line ~345

```python
# Before
remaining = Knowledges.get_knowledge_files_by_file_id(file.id)

# After
remaining = await Knowledges.get_knowledge_files_by_file_id(file.id)
```

#### 5. `services/onedrive/sync_worker.py` — line ~407

Same pattern as step 4.

#### 6. `services/sync/router.py` — three await fixes

- Line ~250 (`get_user_sync_status` handler):
  `all_knowledge = await Knowledges.get_knowledge_bases_by_user_id(user.id)`
- Line ~302 (calls to-be-restored `get_knowledge_bases_by_type`):
  `kbs = await Knowledges.get_knowledge_bases_by_type(...)`
- Line ~335 (second call): same pattern
- Line ~434 (`get_knowledge_files_by_file_id`):
  `remaining = await Knowledges.get_knowledge_files_by_file_id(...)`

For each: ensure the enclosing function is `async def` (likely all are,
since `sync/router.py` is a FastAPI router). If any is a plain `def`
because it's a helper, flip it to `async def` and await its callers
(there should be none outside the router — verify with grep).

#### 7. `services/sync/base_worker.py` — two await fixes (lines ~347, ~987)

```python
# Before
kb_files = Knowledges.get_knowledge_files_by_file_id(file_id)

# After
kb_files = await Knowledges.get_knowledge_files_by_file_id(file_id)
```

Both call sites are inside already-async methods of `BaseSyncWorker`.

#### 8. `services/sync/scheduler.py` — line ~85 + `services/sync/token_refresh.py` — line ~70

Both are call sites of restored `get_knowledge_bases_by_type`. Grep and
add `await`; flip the enclosing function to `async def` if it's still
sync. These are typically called from a scheduled-task loop that is
already async-aware.

#### 9. `backend/open_webui/routers/integrations.py` — line 114

```python
# Before
kbs = Knowledges.get_knowledge_bases_by_type(provider)

# After
kbs = await Knowledges.get_knowledge_bases_by_type(provider)
```

Enclosing handler is `async def` (FastAPI route).

#### 10. `backend/open_webui/services/export/service.py` — line 130

```python
# Before
knowledge_bases = Knowledges.get_knowledge_bases_by_user_id(user_id)

# After
knowledge_bases = await Knowledges.get_knowledge_bases_by_user_id(user_id)
```

Audit the enclosing method (`_collect_kb_data` or similar) and its
caller chain up to the FastAPI export endpoint. Every function from
this call site up to an already-async handler must become `async def`
and be awaited. If the export runs inside a `BackgroundTasks` task,
confirm the background function is async (FastAPI supports
`BackgroundTasks.add_task` with async functions transparently).

### Success Criteria

#### Automated Verification

- [x] `grep -n "await ArchiveService.create_archive" backend/open_webui/routers/users.py` returns at least one match
- [x] `grep -n "await ArchiveService.cleanup_expired_archives" backend/open_webui/main.py` returns at least one match
- [x] `grep -n "await Knowledges.async_is_suspended" backend/open_webui/retrieval/utils.py` returns at least one match
- [x] `grep -rn "Knowledges.is_suspended\b" backend/open_webui` returns no matches (only `async_is_suspended`)
- [x] `grep -rn "= Knowledges.get_knowledge_files_by_file_id" backend/open_webui` returns no matches (all sites now have `await`)
- [x] `grep -rn "= Knowledges.get_knowledge_bases_by_user_id" backend/open_webui` returns no matches
- [x] `grep -rn "= Knowledges.get_knowledge_bases_by_type" backend/open_webui` returns no matches
- [ ] `open-webui dev` starts, runs for ≥120 seconds, logs zero `RuntimeWarning: coroutine ... was never awaited`
- [x] `npm run lint:backend` passes (no NEW errors on touched files; only pre-existing `update_file_path_by_id` no-member from merge & one pre-existing `logger.error` in main.py:1782)
- [x] `npm run format:backend` produces no diff (after one auto-format on knowledge.py from Phase 1 model additions)

#### Manual Verification

- [ ] Admin triggers delete-user on a test account that has archival
  enabled — archive completes, downloadable zip is produced, user row
  is soft-deleted (`deleted_at` set), cleanup worker hard-deletes it
  one cycle later.
- [ ] OneDrive or Google Drive sync worker processes one revoked-source
  event cleanly (check logs for `_handle_revoked_source` or equivalent
  — no coroutine-never-awaited warning).
- [ ] `GET /api/v1/sync/status` returns the logged-in user's sync
  state without a 500.

**Implementation Note**: Pause for human confirmation before Phase 3.

---

## Phase 3: P1 Soft-Delete Wiring in Routers

### Overview

Revert the four router delete endpoints to use the soft-delete model
methods restored by the soft-delete plan. After this phase, deleting a
chat or KB via the API marks it for retention-worker cleanup instead
of immediately orphaning vectors and storage.

### Changes Required

#### 1. `backend/open_webui/routers/chats.py` — three endpoints

All three handlers are already `async def`. Swap hard-delete for
soft-delete:

**`delete_all_user_chats` (line ~505)**:
```python
# Before
result = await Chats.delete_chats_by_user_id(user.id, db=db)

# After
result = await Chats.soft_delete_by_user_id(user.id, db=db)
```

Note the method now returns `int` (count) rather than `bool`. The
handler's `response_model=bool` contract must be preserved — either
change to `int` (API contract change) or coerce:
```python
count = await Chats.soft_delete_by_user_id(user.id, db=db)
return count > 0
```
Pre-merge behavior returned truthy-on-success; `count > 0` matches
that. Use the coerce form to keep API contract stable.

**`delete_chat_by_id` (line ~1042, admin branch)**:
```python
# Before
result = await Chats.delete_chat_by_id(id, db=db)

# After
result = await Chats.soft_delete_by_id(id, db=db)
```
`soft_delete_by_id` returns `bool` — signature-compatible.

**`delete_chat_by_id` (line ~1060, user branch)**:
```python
# Before
result = await Chats.delete_chat_by_id_and_user_id(id, user.id, db=db)

# After
result = await Chats.soft_delete_by_id(id, db=db)
```

The `get_chat_by_id_and_user_id` ownership check happens two lines
above (line ~1052) — that guard already filters out soft-deleted chats
(it uses `get_chat_by_id_and_user_id` which has the `deleted_at.is_(None)`
filter from the soft-delete plan). So `soft_delete_by_id` is safe here.

The `delete_orphan_tags_for_user` calls at lines 1040, 1058 stay — they
are tag-cleanup, independent of chat deletion.

#### 2. `backend/open_webui/routers/knowledge.py` — DELETE endpoint (line ~1051)

```python
# Before
result = await Knowledges.delete_knowledge_by_id(id=id, db=db)
return result

# After
result = await Knowledges.soft_delete_by_id(id=id, db=db)
return result
```

Keep the vector-DB `delete_collection` call at lines 1041–1046 and
the `remove_knowledge_base_metadata_embedding` call at 1049 — those
must happen on soft-delete too (they're cleanup for the search index,
not the authoritative record).

Add back a log line that the pre-merge code had:
```python
log.info(f'Soft-deleting knowledge base: {id}')
```
Before the `soft_delete_by_id` call.

### Success Criteria

#### Automated Verification

- [x] `grep -n "Chats.delete_chat_by_id\b\|Chats.delete_chat_by_id_and_user_id\|Chats.delete_chats_by_user_id" backend/open_webui/routers/chats.py` returns only call sites inside the cleanup worker or internal-use paths, NOT the three user-facing delete handlers. Expected result: zero matches in the three handlers identified above. (zero matches in the router)
- [x] `grep -n "Chats.soft_delete_by_id\|Chats.soft_delete_by_user_id" backend/open_webui/routers/chats.py` returns three matches (one per handler) (:505, :1042, :1060)
- [x] `grep -n "Knowledges.soft_delete_by_id\|Knowledges.delete_knowledge_by_id" backend/open_webui/routers/knowledge.py`: `soft_delete_by_id` used in the DELETE `/{id}/delete` handler; `delete_knowledge_by_id` not used there (check via inspection — the grep alone only narrows location) (one match at :1052, inside the DELETE handler)
- [x] `npm run lint:backend` passes (no new errors on touched files; score unchanged at 7.07/10)

#### Manual Verification

- [ ] Delete a chat via UI → chat disappears from list → row still in DB with `deleted_at IS NOT NULL`.
- [ ] Wait for cleanup-worker cycle (default 5 min or manually trigger) → chat row hard-deleted, associated `chat_file` junction rows gone, orphaned files deleted from storage.
- [ ] Delete a knowledge base via UI → KB disappears → row has `deleted_at`; vector collection still gone (that's by design — index cleanup happens on soft-delete, record cleanup happens on hard-delete).

**Implementation Note**: Pause for human confirmation before Phase 4.

---

## Phase 4: P1 Knowledge Router Guards + Suspension Annotation

### Overview

Restore the six custom guards in `routers/knowledge.py`, fix the
meta-based→column-based type check, and restore the `type_filter` +
local-only search filter + per-row suspension annotation, all
router-side (keeping models close to upstream).

### Changes Required

#### 1. `routers/knowledge.py:505` — meta-based → column-based type check

```python
# Before (line 505)
if knowledge.meta and knowledge.meta.get('type', 'local') != 'local':
    form_data.access_grants = []

# After
if knowledge.type != 'local':
    form_data.access_grants = []
```

#### 2. Update endpoint (`update_knowledge_by_id`) — strip `type` from form_data

In the same handler, before the `form_data.access_grants = []` block
above, add:
```python
# Prevent changing KB type after creation
form_data.type = None
```

Reference: pre-merge `routers/knowledge.py:~512`. The pre-merge
implementation set `form_data.type = None` on every update. This
assumes `KnowledgeUpdateForm` has a `type: Optional[str] = None` field —
verify and add it to the form class if missing, guarded by
`Optional`/default-None so existing callers don't break.

#### 3. `/{id}/access/update` endpoint — 400 for non-local

Locate `update_knowledge_access_by_id` (line ~545). After the
`get_knowledge_by_id` null-check and before the permission check:

```python
if knowledge.type != 'local':
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail='Access grants are not configurable for non-local knowledge bases.',
    )
```

Reference: pre-merge `routers/knowledge.py:~587`.

#### 4. `/add` single-file + `/files/batch/add` multi-file — enforce `KNOWLEDGE_MAX_FILE_COUNT`

Locate both handlers (grep for
`@router.post('/{id}/file/add'` and `/files/batch/add`). In each,
after the ownership/access check and before
`Knowledges.add_file_to_knowledge_by_id`, add:

```python
max_count = request.app.state.config.KNOWLEDGE_MAX_FILE_COUNT
if max_count and max_count > 0:
    current_files = await Knowledges.get_files_by_id(id, db=db) or []
    incoming = 1 if ... else len(form_data.file_ids)  # adapt per handler
    if len(current_files) + incoming > max_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Knowledge base file-count limit ({max_count}) exceeded.',
        )
```

Reference: pre-merge lines 719 (`/add`) and 1161 (`/files/batch/add`).
The exact form_data shape differs between the two — port each
independently from the pre-merge source.

Verify `KNOWLEDGE_MAX_FILE_COUNT` is still a config option in
`backend/open_webui/config.py` — if it was dropped by the merge, add
it back (also to `main.py`'s `app.state.config` wiring) with the same
default (pre-merge it was an `int`, default `0` = no limit, sourced
from env `KNOWLEDGE_MAX_FILE_COUNT`).

#### 5. `/files/remove` — `delete_file=False` for non-local

Locate the file-removal handler (line ~820). Before the
`if delete_file:` check that physically deletes the underlying file
(line ~954), add:

```python
if knowledge.type != 'local':
    delete_file = False
```

Reference: pre-merge `routers/knowledge.py:~874`. Files synced from
OneDrive/Google Drive are shared resources — removing them from a KB
should not delete the file record.

#### 6. Orphan-cleanup for non-local KBs on full delete

In the DELETE `/{id}/delete` handler (touched by Phase 3), add — after
the `soft_delete_by_id` call — a cleanup pass for non-local KBs:

```python
if knowledge.type != 'local':
    # Non-local KBs store file records that are orphans once the KB is gone
    # (files are not shared across KBs for cloud providers — each sync
    # creates its own File rows).
    kb_files = await Knowledges.get_files_by_id(id, db=db) or []
    for kf in kb_files:
        try:
            await Files.delete_file_by_id(kf.id, db=db)
        except Exception:
            log.exception(f'Failed to delete orphan non-local file {kf.id}')
```

Reference: pre-merge `routers/knowledge.py:~1011-1017`. This is
deliberately done synchronously inside the handler (not via the
cleanup worker) because the retention path for non-local files should
not wait — their underlying data lives in the cloud provider and is
already inaccessible once the KB is detached.

#### 7. `type_filter` in list endpoint

Locate the list endpoint in `routers/knowledge.py` (`@router.get('/')`).
Pre-merge it supported `?type=local|onedrive|google_drive|custom` as
a query parameter and filtered at the model layer via
`search_knowledge_bases(type_filter=...)`.

Two choices:
a. Extend `search_knowledge_bases` in `models/knowledge.py` with a
   `type_filter: Optional[str] = None` kwarg (router-side change is
   just forwarding the query param).
b. Filter router-side post-call.

**Decision**: go with (a) — it's a 3-line model change and keeps
router logic simple. The model change stays close to upstream
(additive kwarg with None default = upstream behavior unchanged when
caller omits it). Reference: pre-merge
`models/knowledge.py:~256-258` for the original filter predicate.

Add to `search_knowledge_bases` (post-merge line 219):
```python
async def search_knowledge_bases(
    self,
    query: Optional[str] = None,
    type_filter: Optional[str] = None,   # NEW
    ...existing kwargs...,
) -> list[...]:
    async with get_async_db_context(db) as db:
        stmt = select(Knowledge)...
        if type_filter:
            stmt = stmt.filter(Knowledge.type == type_filter)
        ...
```

And in the router:
```python
@router.get('/')
async def get_knowledge(
    type: Optional[str] = None,  # NEW
    ...,
):
    ...
    results = await Knowledges.search_knowledge_bases(
        query=..., type_filter=type, ...
    )
```

#### 8. Local-only filter in `search_knowledge_files`

Port pre-merge `models/knowledge.py:~335` — search_knowledge_files
filtered to `Knowledge.type == 'local'` so non-admin users couldn't
find (and attempt to attach) files from cloud KBs they can access but
shouldn't be able to reference in chat. Add to `search_knowledge_files`:

```python
stmt = stmt.filter(Knowledge.type == 'local')
```

Placement: right after the user-scope/access filter, before ordering.

#### 9. Suspension annotation — router-side

Locate the list handler's return path (same `GET /knowledge/` handler
as step 7). After the
`results = await Knowledges.search_knowledge_bases(...)` call, add an
annotation pass:

```python
for kb in results:
    if kb.type != 'local':
        kb.suspension_info = await Knowledges.async_get_suspension_info(
            kb.id, db=db
        )
```

Reference: pre-merge annotation block at
`models/knowledge.py:~284-312`. The post-merge model method
`async_get_suspension_info` already returns the same dict shape the
pre-merge method returned, so no additional translation is needed.

If the handler also serves a per-KB view (`GET /{id}`), apply the same
annotation there for consistency.

### Success Criteria

#### Automated Verification

- [x] `grep -n "knowledge.meta.get('type'" backend/open_webui/routers/knowledge.py` returns zero matches (all converted to `knowledge.type`)
- [x] `grep -n "form_data.type = None" backend/open_webui/routers/knowledge.py` returns one match (line 518)
- [x] `grep -n "KNOWLEDGE_MAX_FILE_COUNT" backend/open_webui/routers/knowledge.py` returns at least two matches (single-file handler at :715-718, batch handler at :1192-1196, plus import at :37)
- [x] `grep -n "type_filter" backend/open_webui/models/knowledge.py` returns at least two matches (signature at :227, use at :234-235)
- [x] `grep -n "Knowledge.type == 'local'" backend/open_webui/models/knowledge.py` returns at least one match (inside `search_knowledge_files` at :332; `get_stale_knowledge` at :749 also matches)
- [x] `grep -n "suspension_info = await" backend/open_webui/routers/knowledge.py` returns at least one match (two — :149 in `/`, :210 in `/search`)
- [x] `npm run lint:backend` clean (exit 0; score unchanged at 7.07/10; no new errors on touched files)
- [ ] Backend boots; `GET /api/v1/knowledge/?type=local` returns only local KBs; `GET /api/v1/knowledge/` returns mixed types including cloud KBs with `suspension_info` populated when applicable

#### Manual Verification

- [ ] Create a local KB, try to PATCH it with `{"type": "onedrive"}` — response body shows `type` unchanged (still `'local'`).
- [ ] Create a OneDrive KB, POST to `/{id}/access/update` — 400 with non-local error message.
- [ ] With `KNOWLEDGE_MAX_FILE_COUNT=1`, try to add a second file to a KB — 400.
- [ ] Remove a file from a OneDrive KB — file row stays in DB (still owned by the sync worker).
- [ ] Delete a OneDrive KB — KB + its file rows gone from DB (orphan cleanup fired).
- [ ] Admin KB list shows mixed types; suspend a cloud KB (simulate by setting `meta.suspended_at`); list response for a non-admin user includes `suspension_info.days_remaining` on the affected row.

**Implementation Note**: Pause for human confirmation before Phase 5.

---

## Phase 5: P1/P2 Security + UI + CVE pins

### Overview

Everything remaining: OAuth PII sanitization, `UserModel.__repr__`
(already in Phase 1 but re-verified), auths.py log strip, retrieval.py
fallback async fix, data-warnings queue bypass, model editor UI,
utils/models.py merge block, requirements-min.txt CVE pins.

### Changes Required

#### 1. `backend/open_webui/utils/oauth.py` — 9 log-site sanitizations

Port each of the 9 fix sites below. Line numbers are post-merge
current; exact lines may shift by ±2 when edits accumulate — grep for
the distinctive f-string before editing.

| Line | Fix |
|---|---|
| 931 | Replace `{token}` with `{error_desc}` where `error_desc = token.get('error_description') if isinstance(token, dict) else None` |
| 1495 | Drop `{token}` entirely (just `'OAuth callback failed, user data is missing'`) |
| 1505 | Drop `{user_data}`; keep only `sub` missing note |
| 1550 | Drop `{user_data}`; keep only `email` missing note |
| 1559 | Replace `{user_data}` with the offending `{email_domain}` only |
| 1588 | Swap `{user.email}` → `{user.id}` |
| 1598 | Drop `{new_email}` reference entirely (keep `{user.id}`) |
| 1618 | Swap `{user.email}` → `{user.id}` |
| 1952-1954 | Back-channel logout log: drop `email={user.email}`; keep `user={user.id}`, `provider=`, `sessions_deleted=` |

Reference: the 9 fix locations from the original security-fix commit
`9ab055993` — for each line, `git show 9ab055993 -- backend/open_webui/utils/oauth.py`
will show the expected post-fix shape. For the new upstream line at
~1954, apply the same principle (sanitize email, keep `user.id`).

#### 2. `backend/open_webui/routers/auths.py:523`

```python
# Before
log.info(f'Successfully synced groups for user {user.id}: {user_groups}')

# After
log.info(f'Successfully synced groups for user {user.id}: {len(user_groups)} groups')
```

#### 3. `backend/open_webui/routers/retrieval.py:1708 + 1742`

Fallback branch (already found during audit — this is the
`if file_path:` branch at line 1706):

```python
# Line 1708 — Before
file_path = Storage.get_file(file_path)
# After
file_path = await asyncio.to_thread(Storage.get_file, file_path)

# Line 1742 — Before
docs = loader.load(file.filename, file.meta.get('content_type'), file_path)
# After
docs = await loader.aload(file.filename, file.meta.get('content_type'), file_path)
```

Mirror the already-correct parallel path at `:1765` and `:1838`. No
other change needed — `asyncio` is already imported in that file
(verify with `grep -n "^import asyncio" backend/open_webui/routers/retrieval.py`).

#### 4. `backend/open_webui/utils/models.py` — restore `data_warnings` merge block

Locate the `if default_metadata:` block (grep for `default_metadata`).
Inside the `for key, value in default_metadata.items():` loop, add
between the existing `if key == 'capabilities':` block and the final
`elif meta.get(key) is None:`:

```python
elif key == 'data_warnings':
    # Merge data_warnings: defaults as base, per-model overrides win
    existing = meta.get('data_warnings') or {}
    meta['data_warnings'] = {**value, **existing}
```

Reference: pre-merge `utils/models.py:~308` lines (full block shown
above in the Key Discoveries section).

#### 5. `backend/requirements-min.txt` — restore CVE pins

```diff
- cryptography
+ cryptography==46.0.7
```

And add the `wheel` line back (location: near other build-system pins
if present; otherwise at the end of the requirements alphabetically):
```
wheel==0.46.2
```

Verify `requirements.txt` and `requirements-slim.txt` already have the
correct pins (research doc confirmed they do) — no change needed there.

#### 6. `src/lib/components/chat/Chat.svelte` — data-warnings in queue paths

**Chosen fix**: move the `checkDataWarnings` guard into `submitPrompt`
itself. This is defensive against any future upstream refactor that
splits functions further.

Locate `submitPrompt` (lines ~2062-2110). At the top, before the
existing body, add:
```javascript
if (!await checkDataWarnings()) return;
```

Keep the existing `checkDataWarnings` call in `submitHandler` at
line ~2212 (it will short-circuit harmlessly — `checkDataWarnings`
should be idempotent) OR remove it since `submitHandler` always calls
`submitPrompt` and the guard will fire there. **Decision**: remove the
duplicate in `submitHandler` to avoid a double-modal. Verify
`submitHandler` at line ~2212 ends with a `submitPrompt(...)` call —
if not (if it has an alternate code path that doesn't go through
`submitPrompt`), keep the guard in `submitHandler` as well.

#### 7. `src/lib/components/workspace/Models/ModelEditor.svelte` — restore 12 DataWarnings sites

The easiest path: pull the pre-merge file and diff against post-merge.
The 12 sites cluster into 5 semantic groups:

1. Import: `import DataWarningsConfig from '$lib/components/workspace/Models/DataWarningsConfig.svelte';`
   (exact component path — verify pre-merge).
2. State declaration: `let dataWarnings = $state({...});` (pre-merge
   used `let dataWarnings = {...}` — post-merge svelte 5 runes
   equivalent is `$state(...)`).
3. Default-load (`onMount` or reactive effect): load admin defaults
   into `dataWarnings` when the model being edited has none.
4. Model-load: when editing an existing model, initialize
   `dataWarnings` from `info.meta.data_warnings`.
5. Save path: include `data_warnings: dataWarnings` in the meta object
   passed to the update API.
6. UI: `<DataWarningsConfig bind:value={dataWarnings} />` somewhere in
   the meta-settings section of the form (pre-merge it was near the
   capabilities block).

Reference: `git show 457f01af2:src/lib/components/workspace/Models/ModelEditor.svelte`
— grep inside the git-show output for `data_warnings` / `dataWarnings`
/ `DataWarningsConfig` to find the exact 12 sites and port each
forward. Adapt any `let ... =` to `$state(...)` / `$derived(...)` per
svelte 5 runes conventions already used elsewhere in the file.

Verify `DataWarningsConfig.svelte` still exists — if it was dropped by
the merge, this phase expands to re-importing it too (grep
`src/lib/components/workspace/Models/` for `DataWarnings` files).

### Success Criteria

#### Automated Verification

- [x] `grep -n "{token}\|{user_data}\|{user.email}\|{new_email}" backend/open_webui/utils/oauth.py` returns no matches (zero)
- [x] `grep -n "len(user_groups)" backend/open_webui/routers/auths.py` returns one match (:523)
- [x] `grep -n "await loader.aload\|await asyncio.to_thread(Storage.get_file" backend/open_webui/routers/retrieval.py` returns four matches (:1708, :1742, :1765, :1838) plus one pre-existing `await loader.aload()` at :2500
- [x] `grep -n "loader.load(\|= Storage.get_file" backend/open_webui/routers/retrieval.py` returns no matches
- [x] `grep -n "data_warnings" backend/open_webui/utils/models.py` returns two matches (key check + merge body)
- [x] `grep -n "cryptography==\|wheel==" backend/requirements-min.txt` returns two matches (:12, :59)
- [x] `grep -n "DataWarningsConfig\|dataWarnings" src/lib/components/workspace/Models/ModelEditor.svelte` — 10 matches restored (our component is named `DataWarnings.svelte`, not `DataWarningsConfig.svelte`; used directly via `DataWarnings bind:dataWarnings`)
- [x] `grep -n "checkDataWarnings" src/lib/components/chat/Chat.svelte` — guard now runs inside `submitPrompt` (covers queue paths) AND remains in `submitHandler` (preserves user input on decline; idempotent via `acceptedDataWarnings`)
- [x] `npm run lint:backend` clean (7.07/10, unchanged)
- [x] `npm run lint:frontend` clean on the two frontend files (no new errors; only pre-existing `any`, self-closing-tag, and unused-import errors far from touched lines)
- [x] `npm run build` succeeds (built in 1m 5s)
- [ ] `npm run check` — no new svelte-check errors on touched files (pre-existing errors OK per CLAUDE.md)

#### Manual Verification

- [ ] Enable DEBUG log level, run an OAuth login — no log line contains a raw token, full user_data, or email.
- [ ] Queue a prompt via the chat queue, trigger send — data-warnings acceptance modal appears. Decline → prompt not sent. Accept → prompt sent.
- [ ] Open model editor for any model — DataWarnings config section renders, values round-trip after save.
- [ ] Admin sets default `data_warnings` in settings — open a model that doesn't override `data_warnings` and verify the defaults now appear in its effective config.
- [ ] `pip install -r backend/requirements-min.txt` picks up `cryptography==46.0.7` and `wheel==0.46.2`.
- [ ] Upload a large PDF with external pipeline disabled — request completes; monitor event-loop latency with a second request in parallel (should not block for the full load duration).

**Implementation Note**: This is the final phase. Pause for human
confirmation that all manual verification passed. Then: update
`collab/world/state.md`, write a collab note, and commit.

---

## Testing Strategy

### Unit Tests (only if infrastructure already supports them)

- `Users.get_inactive_users` returns ordered-oldest-first, respects
  `limit`, excludes `exclude_roles`
- `Knowledges.get_knowledge_bases_by_type` filters out soft-deleted KBs
- `UserModel.__repr__` always returns `User(id=..., role=...)` and
  never exposes email/name
- `utils/models.py` `data_warnings` merge block: defaults present +
  model overrides → per-model values win for overlapping keys,
  defaults fill gaps

### Integration / manual walk-through (primary verification)

One full run-through after Phase 5:

1. Start fresh backend, let it run 5 minutes — no RuntimeWarnings, no
   AttributeErrors in logs
2. Delete a test user via admin UI (archival enabled) — archive zip,
   soft-delete, later hard-delete all work
3. Delete a chat, then a KB — both soft-delete, cleanup worker
   finishes the job
4. OAuth login → grep logs for PII → clean
5. Queue a prompt + send → data-warnings modal fires
6. Edit a model's data-warnings config → save → round-trip
7. Hit `GET /api/v1/knowledge/?type=local` + `?type=onedrive` →
   filtered results
8. Non-local KB access-update → 400; non-local KB file-remove →
   underlying file stays; non-local KB delete → orphan file rows
   cleaned up
9. External-pipeline-disabled file upload → event loop stays responsive

### Cypress E2E

Existing suites should continue to pass. No new Cypress tests needed
for regression-fix scope.

## Performance Considerations

- Phase 2's added `await`s replace implicit-coroutine-never-awaited
  bugs with actual async DB calls — performance is strictly better
  (the buggy paths were never actually executing their queries).
- Phase 4's suspension annotation pass runs one `async_get_suspension_info`
  query per non-local KB in the list response. For tenants with many
  cloud KBs this could add N queries per list fetch. **Mitigation**
  (optional, not in this plan): batch-fetch suspension info in one
  query and map in Python. Defer to a follow-up if profiling shows it
  matters. Pre-merge had the same N+1 shape.
- Phase 5's `data_warnings` merge is a cheap dict-merge inside an
  existing loop; negligible.

## Migration Notes

No DB migrations. All column / table changes were done by the earlier
soft-delete plan.

## References

- Research doc: `thoughts/shared/research/2026-04-19-upstream-merge-260416-lost-customizations.md`
- Soft-delete plan (prerequisite): `thoughts/shared/plans/2026-04-19-async-soft-delete-surface.md`
- Merge plan (overall): `collab/docs/upstream-merge-260416-plan.md`
- Pre-merge fork HEAD: commit `457f01af2` — source of truth for every
  restored method/block
- Merge commit: `5f6f1905d` on branch `merge/260416`
- Upstream async DB refactor: commit `27169124f`
- Upstream async loader fix: PR #23705
- Original PII sanitization security fix: commit `9ab055993`
- Original soft-delete migration: `d4e5f6a7b8d0_add_soft_delete_columns.py`
