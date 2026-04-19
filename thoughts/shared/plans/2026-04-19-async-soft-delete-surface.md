# Restore Soft-Delete Surface as Fully Async Implementation Plan

## Overview

The upstream `dev` merge (commit `5f6f1905d` on branch `merge/260416`) converted
`backend/open_webui/models/chats.py` from sync to async (upstream commit
`27169124f`, "refac: async db"). During conflict resolution, **the entire
custom soft-delete surface on `ChatTable` was silently dropped** while the
backing DB column (`chat.deleted_at`, migration `d4e5f6a7b8d0`) and every
caller survived.

Backend now crashes at startup on the very first tick of the cleanup worker:

```
AttributeError: 'ChatTable' object has no attribute 'get_pending_deletions'
  File "backend/open_webui/services/deletion/cleanup_worker.py", line 114
```

Rather than bolt the methods back on as sync (and live with a mixed
sync/async shape that diverges further from upstream each merge), we are
going fully async: reimplement the dropped `ChatTable` surface on the
async engine, flip the companion `KnowledgeTable` soft-delete methods to
async for consistency, and rewrite every caller (`DeletionService`,
`DataRetentionService`, `cleanup_worker`, and three external routers/sync
workers) to use direct `await` instead of threadpool wrappers.

## Current State Analysis

### What the merge dropped from `backend/open_webui/models/chats.py`

1. `Chat.deleted_at` column and `ChatModel.deleted_at` field (DB column
   still exists via migration `d4e5f6a7b8d0`).
2. Ten methods on `ChatTable`:
   - `get_pending_deletions`
   - `get_stale_chats`
   - `soft_delete_by_id`
   - `soft_delete_by_user_id`
   - `soft_delete_by_user_id_and_folder_id`
   - `get_chat_by_id_unfiltered`
   - `get_referenced_file_ids`
   - `get_files_by_chat_id`
   - `count_chats_by_tag_name_and_user_id` — the upstream version lost our
     `filter(Chat.deleted_at.is_(None))` guard
   - `delete_chat_by_id` — exists upstream but our soft-delete filter on
     sibling reads was dropped
3. The `filter(Chat.deleted_at.is_(None))` guard on every list/get query
   that should hide soft-deleted chats (17 methods pre-merge).

### Mixed sync/async shape left in `knowledge.py`

`KnowledgeTable` survived the merge but now has a split personality:

- `get_pending_deletions` (line 702) — **sync** (`with get_db()`)
- `get_suspended_expired_knowledge` (line 786) — **async**
  (`async with get_async_db_context()`)
- `soft_delete_by_id`, `soft_delete_by_user_id`, `get_stale_knowledge`,
  `get_referenced_file_ids`, `get_knowledge_by_id_unfiltered` —
  **sync**

`cleanup_worker.py:163` calls `Knowledges.get_suspended_expired_knowledge(limit=10)`
**without `await`** — lurking second crash as soon as the chat-deletion
crash is fixed.

### Service layer — everything is sync

| Module | Function | Shape |
|---|---|---|
| `services/deletion/service.py:45` | `DeletionService` — all 6 methods | sync `def` |
| `services/retention/service.py:29` | `DataRetentionService.run_cleanup` | async, **but `_cleanup_*` internals are sync** |
| `services/deletion/cleanup_worker.py:40` | `_run_cleanup_loop` | async, but calls sync `_process_*` via `run_in_threadpool` |

### External callers of the service layer

- `backend/open_webui/routers/users.py:716` — `await run_in_threadpool(DeletionService.delete_user, user_id)`
- `backend/open_webui/routers/integrations.py:639` — `delete_collection` (plain `def`) calls `Knowledges.soft_delete_by_id` at line 666
- `backend/open_webui/services/sync/base_worker.py:350` — `await asyncio.to_thread(DeletionService.delete_file, file_id)`
- `backend/open_webui/services/google_drive/sync_worker.py:347` — `await asyncio.to_thread(DeletionService.delete_file, file.id)`
- `backend/open_webui/services/onedrive/sync_worker.py:410` — `await asyncio.to_thread(DeletionService.delete_file, file.id)`
- `backend/open_webui/main.py:764` — `await DataRetentionService.run_cleanup(...)` (already async, no change)
- `backend/open_webui/routers/configs.py:991` — `await DataRetentionService.run_cleanup(...)` (already async, no change)

## Desired End State

1. Backend starts cleanly on `merge/260416` — no `AttributeError` at
   cleanup worker tick.
2. `ChatTable` exposes the 10 missing soft-delete methods, all `async
   def`, following the post-merge `AsyncSession` + `select(...)` pattern
   already used elsewhere in the file.
3. `Chat`/`ChatModel` expose `deleted_at` again.
4. 20 `ChatTable` list/get queries filter out soft-deleted rows (17 that
   had the guard pre-merge + 3 new ones on archived/pinned queries — see
   Decision D2).
5. `KnowledgeTable`'s soft-delete surface (6 methods) is uniformly
   `async def`; no sync method uses `get_db()` from the model any more.
6. `DeletionService` and `DataRetentionService._cleanup_*` are fully
   async; `cleanup_worker._process_*` are `async def` called directly
   from `_run_cleanup_loop`; `run_in_threadpool` / `asyncio.to_thread`
   wrappers at call sites are replaced with direct `await`.
7. The lurking `get_suspended_expired_knowledge` crash is fixed by being
   awaited from the now-async `_process_expired_suspensions`.
8. No regressions in: user deletion flow, stale-chat retention cleanup,
   onedrive/google_drive/base sync worker file-revocation path,
   integration `delete_collection` endpoint.

### Verification

- `open-webui dev` starts and idles for >60s without logging any
  `AttributeError`, `TypeError: object ... is not awaitable`, or
  `RuntimeWarning: coroutine '...' was never awaited`.
- `backend/open_webui/services/deletion/cleanup_worker.py` logs one
  clean cycle of `_process_pending_kb_deletions` /
  `_process_pending_chat_deletions` / `_process_expired_suspensions`.
- pytest suite under `backend/open_webui` passes (if present) — else a
  manual smoke test through the admin user-deletion UI plus triggering
  `POST /api/v1/configs/data-retention/test` succeeds end-to-end.

### Key Discoveries

- Pre-merge sync source for every dropped method is preserved at
  `git show 00313d5ee:backend/open_webui/models/chats.py` — use as
  source of truth for semantics (lines 1325, 1411, 1463, 1586, 1592,
  1604, 1618, 1630, 1642, 1651).
- Post-merge async conventions already in the file:
  - `async with get_async_db_context(db) as db:` accepts an optional
    external `AsyncSession` for session sharing.
  - Reads: `result = await db.execute(select(...))` then
    `result.scalars().first()` / `.scalars().all()` / `.all()`.
  - Writes: `await db.execute(update(...).filter_by(...).values(...))`
    or `await db.execute(delete(...).filter_by(...))` + `await db.commit()`.
  - Bulk update returning row count: use `stmt.execution_options(synchronize_session=False)`
    and read `result.rowcount` from the `await db.execute(...)` result.
- `knowledge.py` imports both `get_async_db_context` and the legacy
  `get_db` (line 9). After Phase 2, `get_db` becomes unused and the
  import can be dropped.
- Three `ChatTable` queries never had a `deleted_at` guard pre-merge
  (`get_archived_chat_list_by_user_id`, `get_pinned_chats_by_user_id`,
  `get_archived_chats_by_user_id`). Adding guards to them is a
  behavior change — see Decision D2, accepted.
- `count_chats_by_tag_name_and_user_id` is called from `cleanup_worker.py:133`
  inside a loop while iterating chat deletions; once async it must be
  awaited, and the call-site for-loop becomes an `async for` context
  (just awaiting in a regular `for` inside an `async def` is fine —
  noting it so the implementer doesn't introduce an unnecessary
  `async for`).
- FastAPI happily accepts `async def` route handlers; flipping
  `delete_collection` from `def` to `async def` in `routers/integrations.py`
  is safe and requires no external change.

## What We're NOT Doing

- Not adding any new soft-delete surface (e.g. folders, files, notes,
  channels, memories) — scope is restoring parity with pre-merge.
- Not altering the migration `d4e5f6a7b8d0` or adding a new migration —
  the DB shape is already correct.
- Not converting the rest of `KnowledgeTable` to be fully async
  end-to-end (only the soft-delete surface). `get_suspension_info`
  (line 714) keeps its sync/async bridge — it's called from already-sync
  code paths and re-touching it is out of scope.
- Not investigating other custom implementations that may also have
  been dropped by the merge — user is researching that separately. If
  more missing surface is discovered, it either gets folded into this
  plan (if it's in the same files) or lands in a follow-up plan.
- Not re-running or modifying the merge itself (`merge/260416` stays as
  is — we fix forward).

## Implementation Approach

Six phases, each independently verifiable. Phases 1–4 can be committed
one at a time without breaking anything else (no call sites change
until Phase 5). Phase 5 is the cut-over. Phase 6 is verification.

---

## Phase 1: Restore `deleted_at` column + async soft-delete methods + guards on `ChatTable`

### Overview

Reintroduce the `deleted_at` column on `Chat` and `ChatModel`, add the
10 missing methods as `async def`, and re-add
`filter(Chat.deleted_at.is_(None))` guards to the 20 list/get queries
that should hide soft-deleted chats. No caller changes — everything
outside this file still crashes at the same line; this phase just makes
the model side correct.

### Changes Required

**File:** `backend/open_webui/models/chats.py`

#### 1. Add `deleted_at` to `Chat` (around line 49, after `updated_at`)

```python
class Chat(Base):
    __tablename__ = 'chat'

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String)
    title = Column(Text)
    chat = Column(JSON)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)
    deleted_at = Column(BigInteger, nullable=True, index=True)

    share_id = Column(Text, unique=True, nullable=True)
    # ...rest unchanged...
```

#### 2. Add `deleted_at` to `ChatModel` (around line 82, after `updated_at`)

```python
class ChatModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    chat: dict

    created_at: int
    updated_at: int
    deleted_at: Optional[int] = None

    share_id: Optional[str] = None
    # ...rest unchanged...
```

#### 3. Append the 10 new async methods before the `Chats = ChatTable()`
module-level assignment (after `get_chat_tasks_by_id` at line 1679)

```python
    async def get_pending_deletions(
        self, limit: int = 100, db: Optional[AsyncSession] = None
    ) -> list[ChatModel]:
        """Get chats marked for deletion (for cleanup worker)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Chat)
                .filter(Chat.deleted_at.isnot(None))
                .order_by(Chat.deleted_at.asc())
                .limit(limit)
            )
            return [ChatModel.model_validate(chat) for chat in result.scalars().all()]

    async def get_stale_chats(
        self,
        stale_before: int,
        limit: int = 100,
        exclude_user_ids: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> list[ChatModel]:
        """Find non-deleted chats whose updated_at is before the given timestamp."""
        async with get_async_db_context(db) as db:
            stmt = (
                select(Chat)
                .filter(Chat.deleted_at.is_(None))
                .filter(Chat.updated_at < stale_before)
            )
            if exclude_user_ids:
                stmt = stmt.filter(Chat.user_id.notin_(exclude_user_ids))
            stmt = stmt.order_by(Chat.updated_at.asc()).limit(limit)
            result = await db.execute(stmt)
            return [ChatModel.model_validate(chat) for chat in result.scalars().all()]

    async def soft_delete_by_id(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        """Mark a chat as deleted (soft-delete)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Chat)
                .filter_by(id=id)
                .filter(Chat.deleted_at.is_(None))
                .values(deleted_at=int(time.time()))
            )
            await db.commit()
            return (result.rowcount or 0) > 0

    async def soft_delete_by_user_id(self, user_id: str, db: Optional[AsyncSession] = None) -> int:
        """Soft-delete all chats for a user. Returns count of affected rows."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Chat)
                .filter_by(user_id=user_id)
                .filter(Chat.deleted_at.is_(None))
                .values(deleted_at=int(time.time()))
            )
            await db.commit()
            return result.rowcount or 0

    async def soft_delete_by_user_id_and_folder_id(
        self, user_id: str, folder_id: str, db: Optional[AsyncSession] = None
    ) -> int:
        """Soft-delete all chats in a folder for a user. Returns count of affected rows."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Chat)
                .filter_by(user_id=user_id, folder_id=folder_id)
                .filter(Chat.deleted_at.is_(None))
                .values(deleted_at=int(time.time()))
            )
            await db.commit()
            return result.rowcount or 0

    async def get_chat_by_id_unfiltered(
        self, id: str, db: Optional[AsyncSession] = None
    ) -> Optional[ChatModel]:
        """Get a chat by ID, including soft-deleted ones. Internal/cleanup use only."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Chat).filter_by(id=id))
                chat = result.scalars().first()
                return ChatModel.model_validate(chat) if chat else None
        except Exception:
            return None

    async def get_referenced_file_ids(
        self, file_ids: list[str], db: Optional[AsyncSession] = None
    ) -> set[str]:
        """Return the subset of file_ids still referenced by active (non-deleted) chats."""
        if not file_ids:
            return set()
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(ChatFile.file_id)
                .join(Chat, Chat.id == ChatFile.chat_id)
                .filter(ChatFile.file_id.in_(file_ids))
                .filter(Chat.deleted_at.is_(None))
                .distinct()
            )
            return {row[0] for row in result.all()}

    async def get_files_by_chat_id(
        self, chat_id: str, db: Optional[AsyncSession] = None
    ) -> list[ChatFileModel]:
        """Get all chat_file records for a given chat_id."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(ChatFile).filter_by(chat_id=chat_id).order_by(ChatFile.created_at.asc())
            )
            return [ChatFileModel.model_validate(cf) for cf in result.scalars().all()]
```

Note: `count_chats_by_tag_name_and_user_id` and `delete_chat_by_id`
already exist in the post-merge file — they do **not** need to be
added, only modified per the next section.

#### 4. Add `Chat.deleted_at.is_(None)` guard to 20 existing async queries

Apply the same one-line filter addition to each of the following. The
pattern is always: add `.filter(Chat.deleted_at.is_(None))` to the
`select(...)` statement (or the Session query) immediately after the
first user-scoping filter.

Queries that had the guard pre-merge (re-add):

| Line | Method |
|---|---|
| 455 | `get_chat_title_by_id` |
| 797 | `get_chat_list_by_user_id` |
| 851 | `get_chat_title_id_list_by_user_id` |
| 898 | `get_chat_list_by_chat_ids` |
| 912 | `get_chat_by_id` |
| 927 | `get_chat_by_share_id` |
| 940 | `get_chat_by_id_and_user_id` |
| 951 | `is_chat_owner` |
| 963 | `get_chat_folder_id` |
| 976 | `get_chats` |
| 982 | `get_chats_by_user_id` |
| 1060 | `get_chats_by_user_id_and_search_text` |
| 1254 | `get_chats_by_folder_id_and_user_id` |
| 1291 | `get_chats_by_folder_ids_and_user_id` |
| 1330 | `get_chat_list_by_user_id_and_tag_name` |
| 1399 | `count_chats_by_tag_name_and_user_id` |
| 1447 | `count_chats_by_folder_id_and_user_id` |

Queries that did NOT have the guard pre-merge (behavior change — add
anyway per Decision D2):

| Line | Method |
|---|---|
| 688 | `get_archived_chat_list_by_user_id` |
| 1030 | `get_pinned_chats_by_user_id` |
| 1053 | `get_archived_chats_by_user_id` |

Example shape (for `get_chat_by_id` at line 912):

```python
async def get_chat_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[ChatModel]:
    try:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Chat).filter_by(id=id).filter(Chat.deleted_at.is_(None))
            )
            chat_item = result.scalars().first()
            if chat_item is None:
                return None
            # ...sanitize logic unchanged...
```

For `is_chat_owner` (line 951), the filter is added inside the
`and_(...)` clause:

```python
result = await db.execute(
    select(exists().where(
        and_(Chat.id == id, Chat.user_id == user_id, Chat.deleted_at.is_(None))
    ))
)
```

### Success Criteria

#### Automated Verification

- [x] Backend boots without Python import error: `cd backend && python -c "from open_webui.models.chats import Chats; print(hasattr(Chats, 'get_pending_deletions'))"` prints `True`
- [x] `npm run lint:backend` passes on `backend/open_webui/models/chats.py`
- [x] `npm run format:backend` produces no diff on `backend/open_webui/models/chats.py`
- [x] Every new async method is annotated with `async def`: `grep -c "^    async def get_pending_deletions\|^    async def get_stale_chats\|^    async def soft_delete_by_id\|^    async def soft_delete_by_user_id\|^    async def soft_delete_by_user_id_and_folder_id\|^    async def get_chat_by_id_unfiltered\|^    async def get_referenced_file_ids\|^    async def get_files_by_chat_id" backend/open_webui/models/chats.py` returns 8

#### Manual Verification

- [ ] Opening the admin chat list still shows chats (the added `deleted_at.is_(None)` guards haven't broken ordinary reads)
- [ ] Archived chats view still displays archived chats — now also hides any soft-deleted ones

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Convert `KnowledgeTable` soft-delete surface to async

### Overview

Flip the six remaining sync soft-delete methods on `KnowledgeTable` to
async so the sync/async split inside the class is eliminated. After
this phase `knowledge.py` no longer imports `get_db` from
`open_webui.internal.db`.

### Changes Required

**File:** `backend/open_webui/models/knowledge.py`

Convert these six methods (lines 702–... pre-change) to async:

- `get_pending_deletions(limit=50)` (line 702)
- `soft_delete_by_id(id)` — currently sync somewhere in the file
- `soft_delete_by_user_id(user_id)` — currently sync
- `get_stale_knowledge(stale_before, limit, exclude_user_ids)` — currently sync
- `get_referenced_file_ids(file_ids)` — currently sync
- `get_knowledge_by_id_unfiltered(id)` — currently sync

Example rewrite (`get_pending_deletions`):

```python
async def get_pending_deletions(
    self, limit: int = 50, db: Optional[AsyncSession] = None
) -> list[KnowledgeModel]:
    """Get knowledge bases marked for deletion (for cleanup worker)."""
    async with get_async_db_context(db) as db:
        result = await db.execute(
            select(Knowledge)
            .filter(Knowledge.deleted_at.isnot(None))
            .order_by(Knowledge.deleted_at.asc())
            .limit(limit)
        )
        return [KnowledgeModel.model_validate(kb) for kb in result.scalars().all()]
```

Apply the same pattern shift to the other five methods (mirroring the
`ChatTable` Phase 1 patterns — `update(...).values(...)` for bulk
updates with `result.rowcount`; `select(...).filter(...)` for reads).

Drop the now-unused `get_db` from the top-of-file import:

```python
# Before
from open_webui.internal.db import Base, JSONField, get_async_db_context, get_db

# After
from open_webui.internal.db import Base, JSONField, get_async_db_context
```

**Leave alone:**
- `get_suspension_info` (line 714) — the sync/async bridge stays
- `async_get_suspension_info`, `async_is_suspended`, `get_suspended_expired_knowledge` — already async, no change

### Success Criteria

#### Automated Verification

- [x] `grep -n "with get_db()" backend/open_webui/models/knowledge.py` returns no matches
- [x] `grep -c "async def get_pending_deletions\|async def soft_delete_by_id\|async def soft_delete_by_user_id\|async def get_stale_knowledge\|async def get_referenced_file_ids\|async def get_knowledge_by_id_unfiltered" backend/open_webui/models/knowledge.py` returns 6
- [x] `npm run lint:backend` passes on `backend/open_webui/models/knowledge.py`

#### Manual Verification

- [x] `python -c "from open_webui.models.knowledge import Knowledges; import inspect; print(inspect.iscoroutinefunction(Knowledges.get_pending_deletions))"` prints `True`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Convert `DeletionService` to async

### Overview

All six public methods on `DeletionService` become `async def`. Internal
service-to-service calls (e.g. `delete_user` → `delete_memories`)
become direct `await`s. Model calls become awaits. The module-level
`_vector_cleanup_pool` ThreadPoolExecutor stays — it's used for
concurrent vector-DB deletions inside `delete_orphaned_files_batch`,
which is still appropriate for blocking HTTP-based vector client calls.

### Changes Required

**File:** `backend/open_webui/services/deletion/service.py`

Convert to `async def`:
- `delete_file(file_id, deleted_file_ids=None)` (line 54)
- `delete_orphaned_files_batch(file_ids, force=False)` (line 126)
- `delete_chat(chat_id, user_id)` (line 205)
- `delete_knowledge(knowledge_id, delete_files=False, deleted_file_ids=None)` (line 263)
- `delete_memories(user_id)` (line 352)
- `delete_user(user_id)` (line 383)

Replace internal sync calls with awaited calls:

| Line | Change |
|---|---|
| 74 | `file = Files.get_file_by_id(file_id)` — check if this is sync (likely yes, no change needed); leave unless it errors. Add `await` only if `Files.get_file_by_id` is async. |
| 80 | `Knowledges.get_knowledge_files_by_file_id(file_id)` — inspect; if async, add `await` |
| 151 | `kb_referenced = await Knowledges.get_referenced_file_ids(file_ids)` |
| 152 | `chat_referenced = await Chats.get_referenced_file_ids(file_ids)` |
| 163 | `Files.get_files_by_ids(orphaned_ids)` — inspect for async |
| 197 | `Files.delete_files_by_ids(orphaned_ids)` — inspect for async |
| 219 | `chat = await Chats.get_chat_by_id_unfiltered(chat_id)` |
| 225 | `chat_files = await Chats.get_files_by_chat_id(chat_id)` |
| 229 | `file_report = await DeletionService.delete_file(chat_file.file_id)` |
| 244 | `if await Chats.count_chats_by_tag_name_and_user_id(tag_name, user_id) == 1:` |
| 252 | `result = await Chats.delete_chat_by_id(chat_id)` |
| 285 | `knowledge = await Knowledges.get_knowledge_by_id_unfiltered(knowledge_id)` |
| 300 | `knowledge_files = await Knowledges.get_files_by_id(knowledge_id)` (already async — just add `await`) |
| 302 | `file_report = await DeletionService.delete_file(file.id, deleted_file_ids)` |
| 315 | `models = Models.get_all_models()` — inspect for async |
| 334 | `Models.update_model_by_id(model.id, model_form)` — inspect for async |
| 341 | `result = await Knowledges.delete_knowledge_by_id(knowledge_id)` (already async) |
| 423 | `memory_report = await DeletionService.delete_memories(user_id)` |
| 431 | `kb_count = await Knowledges.soft_delete_by_user_id(user_id)` |
| 438 | `chat_count = await Chats.soft_delete_by_user_id(user_id)` |
| 552 | `Auths.delete_auth_by_id(user_id)` — inspect |

**Rule of thumb**: after Phase 2 every `Chats.*` and `Knowledges.*`
soft-delete-surface call in this file must be awaited. Other model
calls (`Files.*`, `Models.*`, `Auths.*`, `Users.*`, `Tags.*`,
`Folders.*`, `Prompts.*`, `Tools.*`, `Functions.*`, `Feedbacks.*`,
`Notes.*`, `Channels.*`, `Messages.*`, `OAuthSessions.*`, `Groups.*`,
`Memories.*`) are already async post-merge — verify each with a quick
signature check and add `await` where needed. If any of those are
still sync in the post-merge codebase, wrap them in
`await asyncio.to_thread(...)` rather than block the event loop — but
in practice they should all already be async after upstream's async DB
refactor.

The vector-cleanup executor block inside `delete_orphaned_files_batch`
(lines 171–184) stays unchanged — that thread pool concurrency is
independent of DB async.

### Success Criteria

#### Automated Verification

- [x] `grep -c "^    async def delete_" backend/open_webui/services/deletion/service.py` returns 6
- [x] No remaining `def delete_file\|def delete_orphaned_files_batch\|def delete_chat\|def delete_knowledge\|def delete_memories\|def delete_user` top-level (non-async): `grep "^    def delete_" backend/open_webui/services/deletion/service.py` is empty
- [x] `npm run lint:backend` passes on `backend/open_webui/services/deletion/service.py`
- [x] No `RuntimeWarning: coroutine ... was never awaited` when module is imported: `python -W error::RuntimeWarning -c "from open_webui.services.deletion.service import DeletionService"`

#### Manual Verification

- [ ] Admin deletes a test user via UI — user disappears from admin list, their chats and KBs are soft-deleted in DB (`SELECT deleted_at FROM chat WHERE user_id = ?` returns a timestamp)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Convert `DataRetentionService._cleanup_*` helpers to async

### Overview

The three private helpers become `async def` and the outer already-async
`run_cleanup` (and `_send_warning_emails`) `await` them. This also
unblocks the `await DeletionService.delete_user(...)` call that Phase 3
just made async.

### Changes Required

**File:** `backend/open_webui/services/retention/service.py`

1. Make the three helpers async:

   - `_cleanup_inactive_users` (line 173) → `async def`
   - `_cleanup_stale_chats` (line 226) → `async def`
   - `_cleanup_stale_knowledge` (line 244) → `async def`

2. Inside them, replace sync calls:

   | Line | Change |
   |---|---|
   | 184 | `users = Users.get_inactive_users(...)` — inspect; add `await` if async (likely is post-merge) |
   | 195 | `result = ArchiveService.create_archive(...)` → `result = await ArchiveService.create_archive(...)` (already async per research) |
   | 211 | `deletion_report = DeletionService.delete_user(user.id)` → `deletion_report = await DeletionService.delete_user(user.id)` |
   | 229 | `stale_chats = Chats.get_stale_chats(...)` → `stale_chats = await Chats.get_stale_chats(...)` |
   | 233 | `Chats.soft_delete_by_id(chat.id)` → `await Chats.soft_delete_by_id(chat.id)` |
   | 247 | `stale_kbs = Knowledges.get_stale_knowledge(...)` → `stale_kbs = await Knowledges.get_stale_knowledge(...)` |
   | 251 | `Knowledges.soft_delete_by_id(kb.id)` → `await Knowledges.soft_delete_by_id(kb.id)` |
   | 160 | `Users.update_user_by_id(user.id, {'info': info})` in `_send_warning_emails` — inspect; `await` if async |

3. Update `run_cleanup` (line 70, 77, 82) to `await` the three helpers:

   ```python
   await DataRetentionService._cleanup_inactive_users(
       effective_user_ttl, enable_archival, archive_retention_days, report
   )
   # ...
   await DataRetentionService._cleanup_stale_chats(effective_chat_ttl, report)
   # ...
   await DataRetentionService._cleanup_stale_knowledge(effective_kb_ttl, report)
   ```

### Success Criteria

#### Automated Verification

- [x] `grep -c "^    async def _cleanup_" backend/open_webui/services/retention/service.py` returns 3
- [x] `grep "^    def _cleanup_" backend/open_webui/services/retention/service.py` is empty
- [x] `grep -n "await DataRetentionService._cleanup_" backend/open_webui/services/retention/service.py` returns three lines (70, 77, 82 before changes — numbers may shift)
- [x] `npm run lint:backend` passes (pylint not installed globally; ran via venv — no new issues; only pre-existing style warnings + documented E1101 on missing `Users.get_inactive_users`)
- [x] Dry-run coroutine import: `python -W error::RuntimeWarning -c "from open_webui.services.retention.service import DataRetentionService"` clean

#### Manual Verification

- [ ] Admin triggers `POST /api/v1/configs/data-retention/test` via admin UI or curl — returns 200, report body shows non-zero counts (or zero with no errors if the DB is clean)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 5: Convert `cleanup_worker` + flip external callers to direct await

### Overview

This is the cut-over phase. `cleanup_worker._process_*` helpers become
async and are awaited directly from `_run_cleanup_loop` (no more
`run_in_threadpool`). The three external callers drop their threadpool
wrappers:

- `routers/users.py:716` → direct `await DeletionService.delete_user(user_id)`
- `routers/integrations.py:639` `delete_collection` → flips to `async def`
- Three sync-worker sites → direct `await DeletionService.delete_file(...)`

### Changes Required

#### 1. `backend/open_webui/services/deletion/cleanup_worker.py`

Make the four processing helpers async:

- `_process_pending_deletions()` (line 59) → `async def`
- `_process_pending_kb_deletions()` (line 66) → `async def`
- `_process_pending_chat_deletions()` (line 108) → `async def`
- `_process_expired_suspensions()` (line 158) → `async def`

Update the async loop to await them directly (lines 43, 50):

```python
async def _run_cleanup_loop():
    """Main cleanup loop. Processes pending deletions immediately on startup, then periodically."""
    try:
        await _process_pending_deletions()
    except Exception:
        log.exception('Error in initial cleanup run')

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            await _process_pending_deletions()
        except asyncio.CancelledError:
            log.info('Cleanup worker cancelled')
            return
        except Exception:
            log.exception('Error in cleanup loop')
```

And inside `_process_pending_deletions`:

```python
async def _process_pending_deletions():
    await _process_pending_kb_deletions()
    await _process_pending_chat_deletions()
    await _process_expired_suspensions()
```

Inside the three helpers, replace every sync call with an await:

| Line | Change |
|---|---|
| 71 | `pending_kbs = await Knowledges.get_pending_deletions(limit=50)` |
| 80 | `kb_files = await Knowledges.get_files_by_id(kb.id)` (already async) |
| 84 | `report = await DeletionService.delete_knowledge(kb.id, delete_files=False)` |
| 91 | `file_report = await DeletionService.delete_orphaned_files_batch(kb_file_ids)` |
| 114 | `pending_chats = await Chats.get_pending_deletions(limit=100)` |
| 126 | `chat_files = await Chats.get_files_by_chat_id(chat.id)` |
| 133 | `if await Chats.count_chats_by_tag_name_and_user_id(tag_name, chat.user_id) == 0:` |
| 134 | `Tags.delete_tag_by_name_and_user_id(tag_name, chat.user_id)` — inspect; `await` if async (should be, post-merge) |
| 139 | `await Chats.delete_chat_by_id(chat.id)` |
| 147 | `file_report = await DeletionService.delete_orphaned_files_batch(unique_file_ids)` |
| 163 | `expired_kbs = await Knowledges.get_suspended_expired_knowledge(limit=10)` — **fixes lurking bug** |
| 171 | `kb_files = await Knowledges.get_files_by_id(kb.id)` |
| 174 | `report = await DeletionService.delete_knowledge(kb.id, delete_files=False)` |
| 180 | `file_report = await DeletionService.delete_orphaned_files_batch(kb_file_ids)` |

Remove the now-unused import at line 14:

```python
# Before
from starlette.concurrency import run_in_threadpool

# After (line removed)
```

#### 2. `backend/open_webui/routers/users.py`

Line 716:

```python
# Before
report = await run_in_threadpool(DeletionService.delete_user, user_id)

# After
report = await DeletionService.delete_user(user_id)
```

If `run_in_threadpool` is only used for this call in that file, remove
its import too (verify with a grep before removing).

#### 3. `backend/open_webui/routers/integrations.py`

Line 639 — flip `delete_collection` signature:

```python
# Before
def delete_collection(source_id: str, ...):
    ...
    Knowledges.soft_delete_by_id(knowledge.id)

# After
async def delete_collection(source_id: str, ...):
    ...
    await Knowledges.soft_delete_by_id(knowledge.id)
```

Audit the function body for other sync model calls that need `await`
once their methods became async in Phase 2 (notably any `Knowledges.*`
access). Leave third-party/http calls as-is.

Same pattern review for `delete_document` at line 672 if it touches
any of the converted methods (research says it does not call
`soft_delete_by_id`, but grep for `Knowledges.` inside it just to
confirm).

#### 4. `backend/open_webui/services/sync/base_worker.py`

Line 350:

```python
# Before
await asyncio.to_thread(DeletionService.delete_file, file_id)

# After
await DeletionService.delete_file(file_id)
```

#### 5. `backend/open_webui/services/google_drive/sync_worker.py`

Line 347:

```python
# Before
await asyncio.to_thread(DeletionService.delete_file, file.id)

# After
await DeletionService.delete_file(file.id)
```

#### 6. `backend/open_webui/services/onedrive/sync_worker.py`

Line 410:

```python
# Before
await asyncio.to_thread(DeletionService.delete_file, file.id)

# After
await DeletionService.delete_file(file.id)
```

After these three edits, check each sync-worker file for other
uses of `asyncio.to_thread(DeletionService.*, ...)` — if none, the
pattern is fully cleaned up.

### Success Criteria

#### Automated Verification

- [x] `grep -c "^async def _process_" backend/open_webui/services/deletion/cleanup_worker.py` returns 4
- [x] `grep "run_in_threadpool" backend/open_webui/services/deletion/cleanup_worker.py` is empty
- [x] `grep "asyncio.to_thread(DeletionService" backend/open_webui/services` is empty (3 sites cleaned)
- [x] `grep "run_in_threadpool(DeletionService" backend/open_webui` is empty
- [x] `grep -n "^async def delete_collection" backend/open_webui/routers/integrations.py` has one match
- [ ] `open-webui dev` starts without `AttributeError`, `TypeError`, or `RuntimeWarning: coroutine ... was never awaited` for ≥2 cleanup cycles (~2 min)
- [x] `npm run lint:backend` passes across all touched files (black clean; pylint errors-only reports only pre-existing pre-Phase-5 dropped-by-merge issues: `get_knowledge_bases_by_type`, `update_file_path_by_id`)
- [x] `npm run format:backend` produces no diff (black --check clean on all 6 touched files)

#### Manual Verification

- [ ] Cleanup worker logs one clean `_process_pending_deletions` cycle (INFO log line in `_process_pending_kb_deletions`/`_process_pending_chat_deletions` fires if there is any pending row; otherwise no error for 2 min)
- [ ] Admin UI deletes a user — no 500; user row gone; their chats/KBs soft-deleted and then hard-deleted by the next cleanup cycle
- [ ] Google Drive KB with revoked source triggers `_handle_revoked_source` — file is removed cleanly, no deadlock/error
- [ ] OneDrive equivalent: same as above
- [ ] Integration `DELETE /collections/{source_id}` endpoint returns 200 and soft-deletes the knowledge

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 6: Verification and cleanup

### Overview

Final pass to confirm the system is coherent after the cut-over. No
functional changes — this is a check-and-sweep phase.

### Changes Required

1. **Grep sweep for leftover sync artifacts**
   - `grep -rn "run_in_threadpool(DeletionService\|run_in_threadpool(Chats\|run_in_threadpool(Knowledges" backend/open_webui` — must be empty
   - `grep -rn "asyncio.to_thread(DeletionService" backend/open_webui` — must be empty
   - `grep -n "with get_db()" backend/open_webui/models/chats.py backend/open_webui/models/knowledge.py` — must be empty
2. **Unused import sweep** on the six files touched in Phases 3–5
   (ruff will catch these if configured; otherwise eyeball)
   - Removed unused `import asyncio` from
     `backend/open_webui/services/onedrive/sync_worker.py` — the only
     `asyncio.*` reference in that file was the `asyncio.to_thread`
     wrapper we removed in Phase 5.
   - `backend/open_webui/services/google_drive/sync_worker.py` still
     uses `asyncio.Semaphore` / `asyncio.gather` elsewhere — import
     kept.
   - `backend/open_webui/services/deletion/cleanup_worker.py` still
     uses `asyncio.Task` / `asyncio.create_task` — import kept;
     `run_in_threadpool` already removed in Phase 5.
   - Other touched files had no newly-orphaned imports.
3. **Note to update state.md** — add a line marking this plan as
   complete and referencing the resulting commit/PR.

### Success Criteria

#### Automated Verification

- [x] All grep sweeps above return empty
- [x] Full `npm run lint:backend` clean (pylint errors-only reports only pre-existing dropped-by-merge issues — `get_inactive_users`, `get_knowledge_bases_by_type`, `update_file_path_by_id` — plus false-positive `func.count is not callable` on SQLAlchemy idiom; nothing new from this plan)
- [x] Full `npm run format:backend` produces no diff on the 10 touched files (chats.py has a pre-existing upstream formatting quirk at lines 1186/1236 unrelated to this work — verified by stashing my changes: the diff reproduces against `git HEAD`)
- [x] `cd backend && pytest open_webui/services/deletion backend/open_webui/services/retention -q` — no tests present (skipped)
- [ ] `open-webui dev` clean-start test: no errors/warnings for 5 minutes with one seeded pending soft-deleted chat and one pending soft-deleted KB (setup script: `UPDATE chat SET deleted_at = extract(epoch from now())::bigint WHERE id = 'test-chat-id'` + similar for knowledge). After ≥60s the cleanup worker cycle picks them up and hard-deletes both.

#### Manual Verification

- [ ] End-to-end delete-user flow via admin UI succeeds
- [ ] End-to-end admin trigger of retention test endpoint succeeds
- [ ] Cleanup worker has run at least one cycle without errors in the logs

**Implementation Note**: This is the final phase. On completion, the user may want to close out the work with a commit + a note in `collab/notes.md` + an update to `collab/world/state.md`.

---

## Testing Strategy

### Unit Tests (if present / to add)

- `ChatTable.get_pending_deletions` returns only rows with `deleted_at IS NOT NULL`, ordered oldest-first
- `ChatTable.soft_delete_by_id` on an already-deleted row returns `False` (no rowcount)
- `ChatTable.soft_delete_by_user_id` returns correct count; does not touch other users' chats
- `ChatTable.get_referenced_file_ids` excludes file_ids whose only referencing chat is soft-deleted
- `ChatTable.get_chat_by_id` returns `None` for a soft-deleted chat; `get_chat_by_id_unfiltered` returns it
- Same matrix for `KnowledgeTable` equivalents

### Integration Tests (existing E2E)

- `cypress/` settings/chat/documents suites unchanged — they should continue to pass
- Admin user-deletion flow (if covered) continues to pass

### Manual Testing Steps

1. **Crash reproduction (baseline)**
   - Before any edits: `open-webui dev` → verify the `AttributeError: 'ChatTable' object has no attribute 'get_pending_deletions'` reproduces
2. **Post-Phase-1 sanity**
   - Boot backend; chats still load; archived/pinned lists render
3. **Post-Phase-5 end-to-end**
   - Seed: create two chats as a test user, mark one `deleted_at`
   - Trigger cleanup (wait 60s or restart)
   - Verify: the marked chat is gone, `chat_file` junction rows removed, any file without other KB/chat refs also gone
4. **Retention test endpoint**
   - Enable `DATA_RETENTION_TTL_DAYS` temporarily
   - Hit `POST /api/v1/configs/data-retention/test`
   - Verify a 200 + non-error report

## Performance Considerations

- Moving `_process_*` helpers from `run_in_threadpool` to direct async
  means they now run **on the event loop** instead of a worker thread.
  All DB calls within them are themselves async (asyncpg / aiosqlite)
  and therefore non-blocking, so this is a pure win — no thread-pool
  contention, one less context switch per operation.
- Exception: `_vector_cleanup_pool` in `delete_orphaned_files_batch`
  stays — the vector DB client is a blocking HTTP client and must keep
  its own threadpool.
- Bulk `update(...).values(deleted_at=...)` is a single round-trip; no
  N+1 regression versus pre-merge.
- Guards added to 20 list queries cost a single extra indexed filter
  against `ix_chat_deleted_at` (created by migration `d4e5f6a7b8d0`) —
  negligible, and the same cost pre-merge bore.

## Migration Notes

No DB migration needed. `d4e5f6a7b8d0` already created both
`chat.deleted_at` and `knowledge.deleted_at` (plus their indexes).

## References

- Crash report: `AttributeError: 'ChatTable' object has no attribute 'get_pending_deletions'` from `backend/open_webui/services/deletion/cleanup_worker.py:114`
- Pre-merge source of truth for ChatTable soft-delete surface: `git show 00313d5ee:backend/open_webui/models/chats.py` lines 1325, 1411, 1463, 1586, 1592, 1604, 1618, 1630, 1642, 1651
- Upstream async DB refactor that dropped the surface: commit `27169124f` ("refac: async db")
- Merge commit: `5f6f1905d` on branch `merge/260416`
- DB migration defining the column: `backend/open_webui/migrations/versions/d4e5f6a7b8d0_add_soft_delete_columns.py`
- Lurking second bug fixed in Phase 5: `backend/open_webui/services/deletion/cleanup_worker.py:163` calling `Knowledges.get_suspended_expired_knowledge` (already async at `backend/open_webui/models/knowledge.py:786`) without `await`
- Related historical plans: `thoughts/shared/plans/2026-02-19-gdpr-soft-delete-cleanup-worker.md`, `thoughts/shared/plans/2026-03-31-data-retention-ttl.md`
- Upstream merge master plan: `collab/docs/upstream-merge-260416-plan.md`
