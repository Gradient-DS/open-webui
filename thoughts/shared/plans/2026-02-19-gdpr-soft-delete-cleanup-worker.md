# GDPR Soft-Delete + Cleanup Worker Implementation Plan

## Overview

Replace the current fire-and-forget `BackgroundTasks.add_task()` deletion pattern with a GDPR-robust soft-delete + periodic cleanup worker approach for knowledge bases and chats. This ensures:

1. **Instant invisibility** - Deleted records are hidden from all queries immediately
2. **Crash safety** - If the server crashes mid-cleanup, the worker retries on next startup
3. **Idempotent cleanup** - The existing `DeletionService` is already safe to retry
4. **Complete file cleanup** - Chat files are properly cleaned up, not just orphaned KB files

## Current State Analysis

### Knowledge Base Deletion (`knowledge.py:756-828`)
- Router does inline work (model updates, vector collection, DB delete), then fires background task for file cleanup
- If server crashes after DB delete but before file cleanup, files leak permanently
- `BackgroundTasks.add_task(_cleanup_orphaned_kb_files, ...)` provides no retry mechanism

### Chat Deletion
- **Single chat** (`chats.py:713-751`): Synchronous via `DeletionService.delete_chat()` — deletes files eagerly (even files shared with other chats/KBs)
- **All chats** (`chats.py:201-225`): Deletes chat rows, then background task cleans orphaned files (only checks KB references, not other chat references)

### User Deletion (`users.py:581-648`)
- Runs `DeletionService.delete_user()` synchronously in a thread pool
- Takes a long time for users with many KBs/files — admin waits for full response
- If server crashes mid-deletion, partial cleanup with no retry

### Key Discoveries
- OneDrive scheduler at `services/onedrive/scheduler.py` provides the exact pattern to follow: `start_scheduler()`/`stop_scheduler()` lifecycle, `asyncio.create_task` infinite loop
- Current Alembic head: `eaa33ce2752e` (create invite table)
- `DeletionService` already follows vectors → storage → DB order for retryability
- `delete_orphaned_files_batch` only checks KB references (`Knowledges.get_referenced_file_ids`), not chat references — files shared across chats can be prematurely deleted

## Desired End State

After this plan is complete:

1. `DELETE /api/v1/knowledge/{id}/delete` sets `deleted_at` and returns instantly
2. `DELETE /api/v1/chats/{id}` sets `deleted_at` and returns instantly
3. `DELETE /api/v1/chats/` sets `deleted_at` on all user chats and returns instantly
4. `DELETE /api/v1/users/{user_id}` marks KBs/chats for deletion, deletes simple tables, returns faster
5. A cleanup worker runs every 60 seconds, processing pending KB and chat deletions
6. On startup, the worker immediately processes any pending deletions (crash recovery)
7. All read queries filter out soft-deleted records — they're invisible to users immediately
8. File orphan checking considers both KB and chat references

### Verification:
- Delete a KB → instantly disappears from UI, files cleaned up within 60s
- Delete a chat → instantly disappears from UI, orphaned files cleaned up within 60s
- Delete all chats → all disappear instantly, files cleaned up within 60s
- Kill server mid-cleanup → worker retries on restart, no data leaks
- Admin deletes user → returns faster, cleanup happens asynchronously

## What We're NOT Doing

- **Soft-delete on other tables** (tags, folders, prompts, tools, functions, etc.) — these are simple DB records with no external state (no vectors, no storage). Direct deletion is fine and fast.
- **Soft-delete on memories** — single vector collection per user, fast to delete synchronously.
- **Fixing the SCIM deletion path** — `scim.py:691-706` uses a legacy path that doesn't clean up KBs, files, or vectors. Pre-existing gap, out of scope.
- **Distributed locking** — deletion is idempotent, so concurrent workers processing the same record is safe (just wasteful). Not worth the complexity for a 60s interval.
- **Configurable cleanup interval** — hardcoded 60s is fine. Can make configurable later if needed.
- **Undo/restore functionality** — soft-delete is for crash safety, not for user-facing recycle bin.

## Implementation Approach

The soft-delete column is a nullable `BigInteger` timestamp (`deleted_at`). When null, the record is active. When set, it records when deletion was requested and the record becomes invisible to all queries.

The cleanup worker follows the OneDrive scheduler pattern: module-level `start_cleanup_worker()`/`stop_cleanup_worker()` functions, an `asyncio.create_task` infinite loop, registered in `main.py` lifespan.

---

## Phase 1: Database Migration

### Overview
Add `deleted_at` column to `knowledge` and `chat` tables. Single Alembic migration.

### Changes Required:

#### 1. Alembic Migration
**File**: `backend/open_webui/migrations/versions/a1b2c3d4e5f6_add_soft_delete_columns.py` (revision ID will be auto-generated)

```python
"""add soft delete columns

Revision ID: a1b2c3d4e5f6
Revises: eaa33ce2752e
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "eaa33ce2752e"
branch_labels = None
depends_on = None


def upgrade():
    # Knowledge table
    op.add_column("knowledge", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
    op.create_index("ix_knowledge_deleted_at", "knowledge", ["deleted_at"])

    # Chat table
    op.add_column("chat", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
    op.create_index("ix_chat_deleted_at", "chat", ["deleted_at"])


def downgrade():
    op.drop_index("ix_chat_deleted_at", table_name="chat")
    op.drop_column("chat", "deleted_at")

    op.drop_index("ix_knowledge_deleted_at", table_name="knowledge")
    op.drop_column("knowledge", "deleted_at")
```

#### 2. SQLAlchemy Models

**File**: `backend/open_webui/models/knowledge.py`
Add to `Knowledge` class (after `updated_at`):
```python
deleted_at = Column(BigInteger, nullable=True, index=True)
```

Add to `KnowledgeModel` pydantic model:
```python
deleted_at: Optional[int] = None
```

**File**: `backend/open_webui/models/chats.py`
Add to `Chat` class (after `updated_at`):
```python
deleted_at = Column(BigInteger, nullable=True, index=True)
```

Add to `ChatModel` pydantic model:
```python
deleted_at: Optional[int] = None
```

### Success Criteria:

#### Automated Verification:
- [x] Migration applies cleanly (forward and backward)
- [x] Existing data unaffected (all `deleted_at` values are NULL)
- [x] `npm run build` succeeds (frontend unchanged in this phase)

---

## Phase 2: Knowledge Model Query Filtering

### Overview
Add `deleted_at IS NULL` filtering to all user-facing knowledge query methods. Add helper methods for the cleanup worker.

### Changes Required:

#### 1. Knowledge Read Methods
**File**: `backend/open_webui/models/knowledge.py`

**Methods to add `deleted_at IS NULL` filter:**

| Method | Line | Change |
|--------|------|--------|
| `get_knowledge_bases` | 189 | Add `.filter(Knowledge.deleted_at.is_(None))` to query |
| `search_knowledge_bases` | 214 | Add `.filter(Knowledge.deleted_at.is_(None))` to query |
| `search_knowledge_files` | 277 | Add `.filter(Knowledge.deleted_at.is_(None))` to the Knowledge join/filter |
| `get_knowledge_bases_by_type` | 347 | Add `.filter(Knowledge.deleted_at.is_(None))` to query |
| `get_knowledge_items_by_user_id` | 372 | Add `.filter(Knowledge.deleted_at.is_(None))` to query |
| `get_knowledge_by_id` | 381 | Add `.filter(Knowledge.deleted_at.is_(None))` to query |
| `get_knowledges_by_file_id` | 404 | Add `.filter(Knowledge.deleted_at.is_(None))` to query |

**`get_knowledge_bases_by_user_id`** (line 358): Delegates to `get_knowledge_bases()`, so inherits the filter automatically.

**`check_access_by_user_id`** (line 338): Delegates to `get_knowledge_by_id()`, so inherits the filter automatically.

**Methods that should NOT filter (used by deletion/cleanup):**
- Junction table queries (`get_knowledge_files_by_file_id`, `get_referenced_file_ids`, `get_files_by_id`, `search_files_by_id`) — these query `KnowledgeFile`/`File` tables, not `Knowledge` directly. The cleanup worker needs these to find files associated with soft-deleted KBs.

#### 2. New Helper Methods
**File**: `backend/open_webui/models/knowledge.py`

```python
def get_pending_deletions(self, limit: int = 50) -> list[KnowledgeModel]:
    """Get knowledge bases marked for deletion (for cleanup worker)."""
    with get_db() as db:
        return [
            KnowledgeModel.model_validate(kb)
            for kb in db.query(Knowledge)
            .filter(Knowledge.deleted_at.isnot(None))
            .order_by(Knowledge.deleted_at.asc())
            .limit(limit)
            .all()
        ]

def soft_delete_by_id(self, id: str) -> bool:
    """Mark a knowledge base as deleted (soft-delete)."""
    import time
    with get_db() as db:
        result = (
            db.query(Knowledge)
            .filter_by(id=id)
            .filter(Knowledge.deleted_at.is_(None))
            .update({"deleted_at": int(time.time())})
        )
        db.commit()
        return result > 0

def soft_delete_by_user_id(self, user_id: str) -> int:
    """Soft-delete all knowledge bases for a user. Returns count."""
    import time
    with get_db() as db:
        result = (
            db.query(Knowledge)
            .filter_by(user_id=user_id)
            .filter(Knowledge.deleted_at.is_(None))
            .update({"deleted_at": int(time.time())})
        )
        db.commit()
        return result

def get_knowledge_by_id_unfiltered(self, id: str) -> Optional[KnowledgeModel]:
    """Get a knowledge base by ID, including soft-deleted ones. For internal/cleanup use only."""
    with get_db() as db:
        knowledge = db.query(Knowledge).filter_by(id=id).first()
        return KnowledgeModel.model_validate(knowledge) if knowledge else None
```

#### 3. DeletionService Update
**File**: `backend/open_webui/services/deletion/service.py`

Update `delete_knowledge()` (line 301) to use `get_knowledge_by_id_unfiltered()` instead of `get_knowledge_by_id()`, so it can process soft-deleted KBs:

```python
knowledge = Knowledges.get_knowledge_by_id_unfiltered(knowledge_id)
```

#### 4. OneDrive Scheduler Update
**File**: `backend/open_webui/services/onedrive/scheduler.py`

The `_update_sync_status` method at line 163 calls `Knowledges.get_knowledge_by_id()`. If a KB is soft-deleted while syncing, this would return `None`, which is the correct behavior (skip the update). No change needed.

The `get_knowledge_bases_by_type("onedrive")` at line 82 will automatically filter out soft-deleted KBs. Correct behavior — don't sync deleted KBs.

### Success Criteria:

#### Automated Verification:
- [x] All existing functionality works (soft-deleted records are invisible)
- [x] `Knowledges.get_pending_deletions()` returns only soft-deleted records
- [x] `Knowledges.soft_delete_by_id()` sets `deleted_at` timestamp
- [x] `Knowledges.get_knowledge_by_id_unfiltered()` returns soft-deleted records
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] KB list/search doesn't show soft-deleted records
- [ ] Direct access to soft-deleted KB by ID returns 404

---

## Phase 3: Chat Model Query Filtering

### Overview
Add `deleted_at IS NULL` filtering to all user-facing chat query methods. Add helper methods for the cleanup worker.

### Changes Required:

#### 1. Chat Read Methods
**File**: `backend/open_webui/models/chats.py`

**Methods to add `Chat.deleted_at.is_(None)` filter:**

| Method | Line | Notes |
|--------|------|-------|
| `get_chat_by_id` | 706 | Add filter |
| `get_chat_by_share_id` | 721 | Add filter |
| `get_chat_by_id_and_user_id` | 735 | Add filter |
| `get_chats` | 743 | Add filter |
| `get_chats_by_user_id` | 752 | Add filter |
| `get_chat_list_by_user_id` | 609 | Add filter |
| `get_chat_title_id_list_by_user_id` | 648 | Add filter |
| `get_chat_list_by_chat_ids` | 693 | Add filter |
| `get_pinned_chats_by_user_id` | 778 | Add filter |
| `get_archived_chats_by_user_id` | 787 | Add filter |
| `get_archived_chat_list_by_user_id` | 569 | Add filter |
| `get_chats_by_user_id_and_search_text` | 796 | Add filter |
| `get_chats_by_folder_id_and_user_id` | 1006 | Add filter |
| `get_chats_by_folder_ids_and_user_id` | 1024 | Add filter |
| `get_chat_list_by_user_id_and_tag_name` | 1060 | Add filter |
| `count_chats_by_tag_name_and_user_id` | 1114 | Add filter |
| `count_chats_by_folder_id_and_user_id` | 1150 | Add filter |
| `get_file_ids_by_user_id` | 1213 | Add join filter on Chat.deleted_at |
| `get_shared_chats_by_file_id` | 1363 | Add filter |

**Methods that should NOT filter:**
- `get_files_by_chat_id` (line 1343) — queries `ChatFile` junction table, needed by cleanup worker
- `get_chat_files_by_chat_id_and_message_id` (line 1329) — junction table query
- `delete_*` methods — operate on already-identified records

#### 2. New Helper Methods
**File**: `backend/open_webui/models/chats.py`

```python
def get_pending_deletions(self, limit: int = 100) -> list[ChatModel]:
    """Get chats marked for deletion (for cleanup worker)."""
    with get_db() as db:
        return [
            ChatModel.model_validate(chat)
            for chat in db.query(Chat)
            .filter(Chat.deleted_at.isnot(None))
            .order_by(Chat.deleted_at.asc())
            .limit(limit)
            .all()
        ]

def soft_delete_by_id(self, id: str) -> bool:
    """Mark a chat as deleted (soft-delete)."""
    import time
    with get_db() as db:
        result = (
            db.query(Chat)
            .filter_by(id=id)
            .filter(Chat.deleted_at.is_(None))
            .update({"deleted_at": int(time.time())})
        )
        db.commit()
        return result > 0

def soft_delete_by_user_id(self, user_id: str) -> int:
    """Soft-delete all chats for a user. Returns count of affected rows."""
    import time
    with get_db() as db:
        result = (
            db.query(Chat)
            .filter_by(user_id=user_id)
            .filter(Chat.deleted_at.is_(None))
            .update({"deleted_at": int(time.time())})
        )
        db.commit()
        return result

def get_chat_by_id_unfiltered(self, id: str) -> Optional[ChatModel]:
    """Get a chat by ID, including soft-deleted ones. For internal/cleanup use only."""
    with get_db() as db:
        chat = db.query(Chat).filter_by(id=id).first()
        return ChatModel.model_validate(chat) if chat else None

def get_referenced_file_ids(self, file_ids: list[str]) -> set[str]:
    """Return the subset of file_ids that are still referenced by active (non-deleted) chats."""
    if not file_ids:
        return set()
    with get_db() as db:
        result = (
            db.query(ChatFile.file_id)
            .join(Chat, Chat.id == ChatFile.chat_id)
            .filter(ChatFile.file_id.in_(file_ids))
            .filter(Chat.deleted_at.is_(None))
            .distinct()
            .all()
        )
        return {row[0] for row in result}
```

#### 3. Update `delete_orphaned_files_batch`
**File**: `backend/open_webui/services/deletion/service.py`

Update the orphan check (lines 155-160) to also check chat references:

```python
# 1. Find orphaned files (single query instead of N queries)
if force:
    orphaned_ids = file_ids
else:
    kb_referenced = Knowledges.get_referenced_file_ids(file_ids)
    chat_referenced = Chats.get_referenced_file_ids(file_ids)
    referenced_ids = kb_referenced | chat_referenced
    orphaned_ids = [fid for fid in file_ids if fid not in referenced_ids]
```

Add the `Chats` import at the top of the method (lazy import pattern already used):
```python
from open_webui.models.chats import Chats
```

#### 4. Update `DeletionService.delete_chat`
**File**: `backend/open_webui/services/deletion/service.py`

Update `delete_chat()` (line 235) to use `get_chat_by_id_unfiltered()` so it can process soft-deleted chats:

```python
chat = Chats.get_chat_by_id_unfiltered(chat_id)
```

### Success Criteria:

#### Automated Verification:
- [x] All existing chat functionality works (soft-deleted chats invisible)
- [x] `Chats.get_pending_deletions()` returns only soft-deleted chats
- [x] `Chats.soft_delete_by_id()` sets `deleted_at` timestamp
- [x] `Chats.get_referenced_file_ids()` only considers active chats
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Chat list/search doesn't show soft-deleted chats
- [ ] Direct access to soft-deleted chat by ID returns 404

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that chats and KBs are properly filtered before proceeding.

---

## Phase 4: Router Soft-Delete Endpoints

### Overview
Change deletion endpoints to use soft-delete instead of immediate hard-delete + background cleanup. Remove `BackgroundTasks` dependency from deletion endpoints.

### Changes Required:

#### 1. Knowledge Base Delete Endpoint
**File**: `backend/open_webui/routers/knowledge.py`

Replace the `delete_knowledge_by_id` function (lines 756-828) and remove `_cleanup_orphaned_kb_files` (lines 831-839):

```python
@router.delete("/{id}/delete", response_model=bool)
async def delete_knowledge_by_id(
    id: str,
    user=Depends(get_verified_user),
    _=Depends(require_feature("knowledge")),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    log.info(f"Soft-deleting knowledge base: {id} (name: {knowledge.name})")
    result = Knowledges.soft_delete_by_id(id)
    return result
```

Remove `BackgroundTasks` import dependency and `_cleanup_orphaned_kb_files` function.

#### 2. Chat Delete Endpoints
**File**: `backend/open_webui/routers/chats.py`

**Delete all chats** (lines 201-225) — replace with soft-delete:

```python
@router.delete("/", response_model=bool)
async def delete_all_user_chats(
    request: Request,
    user=Depends(get_verified_user),
):
    if user.role == "user" and not has_permission(
        user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    count = Chats.soft_delete_by_user_id(user.id)
    return count > 0
```

Remove `BackgroundTasks` parameter and `_cleanup_orphaned_chat_files` function (lines 228-232).

**Delete single chat** (lines 713-751) — replace with soft-delete:

```python
@router.delete("/{id}", response_model=bool)
async def delete_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        chat = Chats.get_chat_by_id(id)
        if not chat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
        return Chats.soft_delete_by_id(id)
    else:
        if not has_permission(
            user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        chat = Chats.get_chat_by_id_and_user_id(id, user.id)
        if not chat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
        return Chats.soft_delete_by_id(id)
```

#### 3. User Delete Endpoint
**File**: `backend/open_webui/routers/users.py`

The admin user deletion endpoint (lines 581-648) remains mostly the same but now `DeletionService.delete_user()` will be updated in Phase 5 to use soft-delete for KBs/chats, making it return faster.

#### 4. Shared Chat Deletion
The `delete_shared_chat_by_id` endpoint (chats.py:961-976) deletes **shared copies** of chats (separate rows with `user_id = "shared-{id}"`). These don't have files and don't need soft-delete — keep as hard-delete.

### Success Criteria:

#### Automated Verification:
- [x] KB delete returns instantly (no background task)
- [x] Chat delete returns instantly (no background task)
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Delete a KB via UI → disappears immediately from list
- [ ] Delete a chat via UI → disappears immediately from list
- [ ] Delete all chats via UI → all disappear immediately
- [ ] Note: files/vectors NOT cleaned up yet (cleanup worker not implemented yet) — expected in this phase

**Implementation Note**: After this phase, deletions will be instant but cleanup won't happen until Phase 5. This is a brief intermediate state.

---

## Phase 5: Cleanup Worker

### Overview
Create the periodic cleanup worker that processes pending KB and chat deletions, performing the full cascade cleanup (vectors, storage, DB).

### Changes Required:

#### 1. Cleanup Worker Module
**File**: `backend/open_webui/services/deletion/cleanup_worker.py` (new file)

```python
"""
Periodic cleanup worker for soft-deleted knowledge bases and chats.

Architecture:
- Runs as an asyncio.Task started from main.py lifespan
- Processes pending deletions every CLEANUP_INTERVAL_SECONDS
- On startup, immediately processes any pending deletions (crash recovery)
- Uses existing DeletionService for the actual cleanup (idempotent, safe to retry)
"""

import asyncio
import logging
from typing import Optional
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 60

_cleanup_task: Optional[asyncio.Task] = None


def start_cleanup_worker():
    """Start the background cleanup worker. Called from main.py lifespan."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_run_cleanup_loop())
        log.info("Deletion cleanup worker started (interval: %ds)", CLEANUP_INTERVAL_SECONDS)


def stop_cleanup_worker():
    """Stop the background cleanup worker. Called from main.py lifespan shutdown."""
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        log.info("Deletion cleanup worker stopped")
    _cleanup_task = None


async def _run_cleanup_loop():
    """Main cleanup loop. Processes pending deletions immediately on startup, then periodically."""
    # Process immediately on startup (crash recovery)
    try:
        await run_in_threadpool(_process_pending_deletions)
    except Exception:
        log.exception("Error in initial cleanup run")

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            await run_in_threadpool(_process_pending_deletions)
        except asyncio.CancelledError:
            log.info("Cleanup worker cancelled")
            return
        except Exception:
            log.exception("Error in cleanup loop")


def _process_pending_deletions():
    """Process all pending KB and chat deletions. Runs in thread pool."""
    _process_pending_kb_deletions()
    _process_pending_chat_deletions()


def _process_pending_kb_deletions():
    """Process knowledge bases marked for deletion."""
    from open_webui.models.knowledge import Knowledges
    from open_webui.services.deletion import DeletionService

    pending_kbs = Knowledges.get_pending_deletions(limit=50)
    if not pending_kbs:
        return

    log.info("Processing %d pending KB deletions", len(pending_kbs))

    for kb in pending_kbs:
        try:
            # Collect file IDs before deletion (junction rows cascade on KB delete)
            kb_files = Knowledges.get_files_by_id(kb.id)
            kb_file_ids = [f.id for f in kb_files]

            # Full cascade: vector collection, model updates, hard-delete KB row
            report = DeletionService.delete_knowledge(kb.id, delete_files=False)

            if report.has_errors:
                log.warning("KB %s cleanup had errors: %s", kb.id, report.errors)

            # Clean up orphaned files (checks KB and chat references)
            if kb_file_ids:
                file_report = DeletionService.delete_orphaned_files_batch(kb_file_ids)
                if file_report.has_errors:
                    log.warning("KB %s file cleanup errors: %s", kb.id, file_report.errors)
                log.info(
                    "KB %s file cleanup: %d storage, %d vectors, %d DB records",
                    kb.id, file_report.storage_files,
                    file_report.vector_collections, file_report.total_db_records,
                )

            log.info("KB %s (%s) cleanup complete", kb.id, kb.name)

        except Exception:
            log.exception("Failed to cleanup KB %s", kb.id)


def _process_pending_chat_deletions():
    """Process chats marked for deletion."""
    from open_webui.models.chats import Chats
    from open_webui.models.tags import Tags
    from open_webui.services.deletion import DeletionService

    pending_chats = Chats.get_pending_deletions(limit=100)
    if not pending_chats:
        return

    log.info("Processing %d pending chat deletions", len(pending_chats))

    # Collect all file IDs across all pending chats
    all_file_ids: list[str] = []

    for chat in pending_chats:
        try:
            # Collect file IDs from this chat
            chat_files = Chats.get_files_by_chat_id(chat.id)
            all_file_ids.extend(cf.file_id for cf in chat_files)

            # Clean up orphaned tags
            if chat.meta and chat.meta.get("tags"):
                for tag_name in chat.meta.get("tags", []):
                    try:
                        if Chats.count_chats_by_tag_name_and_user_id(tag_name, chat.user_id) == 0:
                            Tags.delete_tag_by_name_and_user_id(tag_name, chat.user_id)
                    except Exception as e:
                        log.warning("Failed to cleanup tag %s: %s", tag_name, e)

            # Hard-delete the chat (and its shared copy)
            Chats.delete_chat_by_id(chat.id)

        except Exception:
            log.exception("Failed to cleanup chat %s", chat.id)

    # Batch cleanup orphaned files from all processed chats
    if all_file_ids:
        unique_file_ids = list(set(all_file_ids))
        file_report = DeletionService.delete_orphaned_files_batch(unique_file_ids)
        if file_report.has_errors:
            log.warning("Chat file cleanup errors: %s", file_report.errors)
        log.info(
            "Chat file cleanup: %d storage, %d vectors, %d DB records",
            file_report.storage_files,
            file_report.vector_collections, file_report.total_db_records,
        )
```

Note on tag counting: `count_chats_by_tag_name_and_user_id` filters `deleted_at IS NULL` (from Phase 3), so the count will be 0 when the soft-deleted chat was the last one using that tag. The tag count threshold is `== 0` instead of `== 1` because the chat is still soft-deleted (not hard-deleted yet) but invisible to the count.

Actually, wait — the chat IS still in the DB (soft-deleted). And our count query filters `deleted_at IS NULL`. So the soft-deleted chat is NOT counted. If there were 2 chats using a tag and we soft-delete one, count returns 1 (the remaining active chat). If we soft-delete both, count returns 0. We should delete the tag when count == 0. This is correct as written above.

#### 2. Register in main.py Lifespan
**File**: `backend/open_webui/main.py`

Add after the OneDrive scheduler start (line 680):

```python
# Start deletion cleanup worker
from open_webui.services.deletion.cleanup_worker import start_cleanup_worker
start_cleanup_worker()
```

Add before the OneDrive scheduler stop (line 727):

```python
# Stop deletion cleanup worker
from open_webui.services.deletion.cleanup_worker import stop_cleanup_worker
stop_cleanup_worker()
```

### Success Criteria:

#### Automated Verification:
- [x] Worker starts on app startup (check logs for "Deletion cleanup worker started")
- [x] Worker stops on app shutdown (check logs for "Deletion cleanup worker stopped")
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Soft-delete a KB → files/vectors cleaned up within 60 seconds
- [ ] Soft-delete a chat → files cleaned up within 60 seconds
- [ ] Soft-delete all chats → all files cleaned up within 60 seconds
- [ ] Kill server mid-cleanup → restart → worker retries and completes cleanup
- [ ] Check logs for cleanup activity

**Implementation Note**: After completing this phase and all verification passes, pause here for manual confirmation that the full soft-delete + cleanup flow works end-to-end before proceeding.

---

## Phase 6: User Deletion Integration

### Overview
Update `DeletionService.delete_user()` to use soft-delete for KBs and chats, making user deletion faster and crash-safe. The cleanup worker handles the expensive parts.

### Changes Required:

#### 1. Update `DeletionService.delete_user()`
**File**: `backend/open_webui/services/deletion/service.py`

The key changes to `delete_user()` (line 398):

```python
@staticmethod
def delete_user(user_id: str) -> DeletionReport:
    """
    Delete a user and ALL associated data.

    Fast path (synchronous):
    1. Delete memories (single vector collection + DB)
    2. Soft-delete all user's knowledge bases (instant)
    3. Soft-delete all user's chats (instant)
    4. Delete simple DB tables (tags, folders, prompts, etc.)
    5. Delete auth and user records

    The cleanup worker handles the expensive parts:
    - KB vector collections, model reference updates, file cleanup
    - Chat file cleanup (vectors, storage, DB)
    """
    # ... (imports and user verification unchanged)

    report = DeletionReport()

    # Verify user exists
    user = Users.get_user_by_id(user_id)
    if not user:
        report.add_error(f"User {user_id} not found")
        return report

    # 1. Delete memories (vectors + DB) — fast, single collection
    memory_report = DeletionService.delete_memories(user_id)
    report.vector_collections += memory_report.vector_collections
    for table, count in memory_report.db_records.items():
        report.add_db(table, count)
    report.errors.extend(memory_report.errors)

    # 2. Soft-delete all knowledge bases (instant — cleanup worker handles the rest)
    try:
        kb_count = Knowledges.soft_delete_by_user_id(user_id)
        report.add_db("knowledge_soft_deleted", kb_count)
    except Exception as e:
        report.add_error(f"Failed to soft-delete knowledge bases: {e}")

    # 3. Soft-delete all chats (instant — cleanup worker handles the rest)
    try:
        chat_count = Chats.soft_delete_by_user_id(user_id)
        report.add_db("chat_soft_deleted", chat_count)
    except Exception as e:
        report.add_error(f"Failed to soft-delete chats: {e}")

    # 4. Delete remaining tables (all fast DB-only operations)
    # ... (steps 6 unchanged: messages, channels, tags, folders, prompts,
    #      tools, functions, models, feedbacks, notes, OAuth sessions,
    #      groups, API keys)

    # 5. Delete auth and user records
    # ... (step 7 unchanged)

    return report
```

The steps 2-5 from the current implementation (KB vector cleanup, file collection, batch file delete, chat delete) are replaced by two `soft_delete_by_user_id` calls. The cleanup worker handles the expensive cascade.

Note: The `delete_user` method still deletes the `User` and `Auth` records immediately. The soft-deleted KBs/chats reference a `user_id` that no longer exists in the `user` table, but this is fine — the cleanup worker doesn't need the user record, only the KB/chat records themselves.

### Success Criteria:

#### Automated Verification:
- [x] User deletion returns faster (no vector/storage operations during request)
- [x] Soft-deleted KBs and chats appear in `get_pending_deletions()` after user delete
- [x] Cleanup worker processes them on next cycle
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Admin deletes a user → response is fast
- [ ] User's KBs and chats disappear immediately from all views
- [ ] Files/vectors cleaned up within 60 seconds by cleanup worker
- [ ] Archive-before-delete still works correctly (archive created before soft-delete)

---

## Testing Strategy

### Unit Tests
- `Knowledges.soft_delete_by_id()` sets `deleted_at`, `get_knowledge_by_id()` returns None
- `Knowledges.get_pending_deletions()` returns soft-deleted records
- `Chats.soft_delete_by_id()` sets `deleted_at`, `get_chat_by_id()` returns None
- `Chats.get_pending_deletions()` returns soft-deleted records
- `Chats.get_referenced_file_ids()` only returns file IDs from active chats
- `delete_orphaned_files_batch` checks both KB and chat references

### Integration Tests
- Soft-delete KB → cleanup worker processes → KB row gone, files cleaned up
- Soft-delete chat → cleanup worker processes → chat row gone, files cleaned up
- File shared between KB and chat → delete chat → file preserved (still in KB)
- File in two chats → delete one → file preserved (still in other chat)
- Server crash mid-cleanup → restart → worker retries successfully

### Manual Testing Steps
1. Create a KB with files → delete KB → verify files cleaned up within 60s
2. Create a chat with uploaded files → delete chat → verify files cleaned up
3. Create a chat and KB sharing a file → delete chat → verify file preserved
4. Admin: delete user with many KBs/chats → verify fast response, cleanup happens
5. Kill server during cleanup → restart → verify cleanup completes

## Performance Considerations

- **Worker overhead**: A single SQL query every 60s to check for pending deletions. Negligible when no deletions pending (`SELECT ... WHERE deleted_at IS NOT NULL LIMIT 50` hits the index).
- **Index on `deleted_at`**: Added to both tables. The index is sparse (most values NULL) which databases handle efficiently.
- **Batch processing**: KBs processed individually (each has complex cascade). Chats batch their file cleanup (single `delete_orphaned_files_batch` call for all files from all processed chats).
- **No locking**: Deletion is idempotent. If two workers process the same record, the second attempt finds nothing to do. The 60s interval makes this unlikely anyway.

## Migration Notes

- **Forward migration**: Add `deleted_at` column, create indexes. No data migration needed.
- **Rollback**: Drop columns and indexes. Any records with `deleted_at` set would become visible again (which is safe — they just weren't cleaned up yet).
- **Deployment**: No downtime needed. The column is nullable with no default, so existing queries work before the code is deployed. Deploy code after migration runs.

## References

- OneDrive scheduler pattern: `backend/open_webui/services/onedrive/scheduler.py`
- Current KB deletion: `backend/open_webui/routers/knowledge.py:756-839`
- Current chat deletion: `backend/open_webui/routers/chats.py:201-232, 713-751`
- DeletionService: `backend/open_webui/services/deletion/service.py`
- User deletion endpoint: `backend/open_webui/routers/users.py:581-648`
- Typed KB feature plan: `thoughts/shared/plans/2026-02-04-typed-knowledge-bases.md`
