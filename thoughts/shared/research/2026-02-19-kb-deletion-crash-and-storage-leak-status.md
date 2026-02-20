---
date: 2026-02-19T12:00:00+01:00
researcher: claude
git_commit: 8d50a2d4f9118bea8a5199d4dcd1bfa0466af5e3
branch: feat/sync-improvements
repository: open-webui
topic: "KB deletion crash + storage leak fix status"
tags: [research, codebase, knowledge, deletion, storage, s3, performance, crash, event-loop]
status: complete
last_updated: 2026-02-19
last_updated_by: claude
---

# Research: KB Deletion Crash + Storage Leak Fix Status

**Date**: 2026-02-19
**Researcher**: claude
**Git Commit**: 8d50a2d4f
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

1. Was the KB deletion storage leak (from `thoughts/shared/research/2026-02-14-kb-deletion-storage-leak.md`) fixed?
2. Why does deleting a large KB or all user data crash the entire deployment?
3. What specifically in `backend/open_webui/services/deletion/` causes the crash?

## Summary

**Storage leak: Partially fixed.** The knowledge router now uses `DeletionService.delete_file()` for KB deletion and file removal, which calls `Storage.delete_file()`. However, the **OneDrive sync worker** and **bulk chat deletion** still leak files.

**Deployment crash: Confirmed root cause.** The `DeletionService` performs all cleanup synchronously and sequentially. When called from `async def` FastAPI endpoints, it blocks the asyncio event loop for the entire duration. For a KB with 1000 files, this means ~4000 SQL queries, ~4000 vector DB calls, and ~1000 storage calls — all executing on the event loop thread without yielding. The server (default: 1 uvicorn worker, no request timeout) becomes completely unresponsive until the operation finishes (or crashes from resource exhaustion).

## Detailed Findings

### 1. Storage Leak Fix Status

#### Fixed (knowledge router)

The knowledge router at `backend/open_webui/routers/knowledge.py` now uses `DeletionService.delete_file()` in all three deletion paths:

| Path | Line | Status |
|------|------|--------|
| `DELETE /{id}/delete` (orphan cleanup) | `knowledge.py:828` | **Fixed** — calls `DeletionService.delete_file(file_id)` |
| `POST /{id}/file/remove` (local KB, `delete_file=True`) | `knowledge.py:726` | **Fixed** — calls `DeletionService.delete_file(form_data.file_id)` |
| `POST /{id}/file/remove` (non-local KB, orphan) | `knowledge.py:735` | **Fixed** — calls `DeletionService.delete_file(form_data.file_id)` |

#### NOT Fixed (sync worker)

The OneDrive sync worker at `backend/open_webui/services/onedrive/sync_worker.py` does **not** import or use `DeletionService`. It has its own inline cleanup that omits `Storage.delete_file()`:

| Path | Lines | Vectors | Storage | DB |
|------|-------|---------|---------|-----|
| `_handle_deleted_item` (delta deletion) | 916-956 | Cleaned | **Leaked** | Cleaned |
| `_handle_revoked_source` (source removed) | 547-607 | Cleaned | **Leaked** | Cleaned |
| File update (content changed) | 1062-1106 | N/A | Overwritten* | Updated |

\* File update uses deterministic filenames so storage is overwritten in-place — except if the file is renamed on OneDrive, which creates a new key and orphans the old one.

#### NOT Fixed (bulk chat deletion)

`DELETE /api/v1/chats/` at `chats.py:212` still calls `Chats.delete_chats_by_user_id(user.id)` directly without DeletionService.

### 2. Deployment Crash: Root Cause Analysis

#### The Problem: Synchronous Blocking of the Event Loop

Both deletion endpoints are declared as `async def` but call fully synchronous `DeletionService` methods inline:

```python
# knowledge.py:757 — async def, calls sync DeletionService.delete_file() in a loop
@router.delete("/{id}/delete", response_model=bool)
async def delete_knowledge_by_id(id: str, ...):
    ...
    for file_id in kb_file_ids:
        file_report = DeletionService.delete_file(file_id)  # blocks event loop

# users.py:581 — async def, calls sync DeletionService.delete_user()
@router.delete("/{user_id}", response_model=bool)
async def delete_user_by_id(request: Request, user_id: str, ...):
    ...
    report = DeletionService.delete_user(user_id)  # blocks event loop
```

In FastAPI, `async def` handlers run on the main asyncio event loop. Synchronous calls block the entire event loop — no other requests can be served.

#### Operation Count Per File

Each call to `DeletionService.delete_file()` (`service.py:48-125`) performs:

| Operation | Type | Count |
|-----------|------|-------|
| `Files.get_file_by_id()` | SQL query | 1 |
| `Knowledges.get_knowledge_files_by_file_id()` | SQL query | 1 |
| `VECTOR_DB_CLIENT.delete(filter={"file_id": ...})` | Vector DB call (per KB ref) | M |
| `VECTOR_DB_CLIENT.delete(filter={"hash": ...})` | Vector DB call (per KB ref) | M |
| `VECTOR_DB_CLIENT.has_collection(f"file-{id}")` | Vector DB call | 1 |
| `VECTOR_DB_CLIENT.delete_collection(f"file-{id}")` | Vector DB call | 1 |
| `Storage.delete_file(file.path)` | S3/Azure/GCS call | 1 |
| `Files.delete_file_by_id()` | SQL query | 1 |

Each DB query opens a new `SessionLocal()` via `get_db()` context manager (`internal/db.py:152-160`).

#### Total Operations for KB Deletion (N files, each in 1 KB)

The knowledge router also has redundancy — it calls `get_knowledge_files_by_file_id()` in the orphan check loop (line 825), and then `DeletionService.delete_file()` calls it again internally (service.py:76).

| Resource | Count | Notes |
|----------|-------|-------|
| SQL queries | ~4N + 3 | N orphan checks + N×3 inside delete_file + 3 setup queries |
| Vector DB calls | ~4N + 1 | N×(delete by file_id + delete by hash + has_collection + delete_collection) + 1 KB collection |
| Storage calls | N | 1 per file |
| **Total for 1000 files** | **~9000 calls** | All sequential, all synchronous, all blocking |

#### Total Operations for User Deletion

`DeletionService.delete_user()` (`service.py:306-506`) cascades through:

1. Delete memories (2 vector DB calls)
2. For each KB: `delete_knowledge()` → for each file: `delete_file()` (same counts as above)
3. For each standalone file: `delete_file()` (with dedup tracking)
4. Delete chats (1 DB call — but no file cleanup!)
5. 14 more sequential DB deletions (messages, channels, tags, folders, prompts, tools, functions, models, feedbacks, notes, oauth_sessions, groups, API keys, auth)

For a user with 5 KBs × 200 files each: ~5000+ SQL queries, ~5000+ vector DB calls, ~1000 storage calls, plus 14 table cleanups.

#### Server Configuration Makes It Worse

| Setting | Default | Impact |
|---------|---------|--------|
| `UVICORN_WORKERS` | `1` | Single worker — blocking it blocks everything |
| Request timeout | **None** | No timeout at uvicorn, middleware, or FastAPI level |
| Thread offloading | **Not used** | Neither endpoint uses `run_in_threadpool()` |
| Vector DB interface | Synchronous | `VectorDBBase` defines all methods as plain `def` |
| Storage interface | Synchronous | All providers use synchronous SDK calls (boto3, Azure SDK, GCS) |

#### Why It Crashes

With 1 worker and no timeout:
1. The deletion request starts executing synchronously
2. The event loop is blocked — no other requests are served (including health checks)
3. For remote vector DBs (HTTP Chroma, Qdrant): each of ~4000 calls has network latency (50-200ms each = 200-800 seconds total)
4. The `DeletionReport` accumulates thousands of entries in memory
5. Health check probes fail → orchestrator (K8s/Docker) restarts the container
6. The deletion is interrupted mid-way, leaving partially deleted data

### 3. Additional Issues in DeletionService

#### Redundant `get_all_models()` Call

The KB delete endpoint calls `Models.get_all_models()` at `knowledge.py:787` to check/update model references. `DeletionService.delete_knowledge()` at `service.py:238` does the exact same thing. The router's inline implementation duplicates this work.

#### No Batching Support

All operations are one-at-a-time:
- Vector DB deletes could be batched (most vector DBs support batch delete)
- Storage deletes could be batched (S3 supports `delete_objects` for up to 1000 keys per call)
- DB deletes could use `IN` clauses instead of individual `delete_file_by_id` calls

#### DeletionService.delete_user() Doesn't Clean Up Chat Files

At `service.py:384-388`, chats are deleted via `Chats.delete_chats_by_user_id(user_id)` directly — the same bypass identified in the original research. Chat files are not cleaned up through DeletionService:

```python
# 4. Delete chats (FK cascades chat_files)
try:
    Chats.delete_chats_by_user_id(user_id)
    report.add_db("chat")
```

## Suggested Fixes

### Quick Fix: Prevent Crash (Low Effort)

Move the synchronous deletion to a background thread so it doesn't block the event loop:

```python
# In knowledge.py and users.py:
from starlette.concurrency import run_in_threadpool

# Knowledge deletion
result = await run_in_threadpool(lambda: DeletionService.delete_knowledge(id, delete_files=True))

# User deletion
report = await run_in_threadpool(lambda: DeletionService.delete_user(user_id))
```

This doesn't reduce total time but prevents the server from becoming unresponsive.

### Medium Fix: Background Task (Medium Effort)

Use FastAPI's `BackgroundTasks` to return immediately and clean up asynchronously:

```python
@router.delete("/{id}/delete")
async def delete_knowledge_by_id(id: str, background_tasks: BackgroundTasks, ...):
    # Quick validation
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    # ... permission checks ...

    # Mark KB as "deleting" (new status field) so UI shows it's being cleaned up
    # Then return immediately
    background_tasks.add_task(DeletionService.delete_knowledge, id, delete_files=True)
    return True
```

### Full Fix: Batch Operations + Async (High Effort)

1. Add batch methods to vector DB and storage interfaces
2. Collect all file paths/IDs upfront, then delete in batches
3. Use `S3.delete_objects()` for bulk S3 cleanup (1000 keys per call)
4. Use `IN` clauses for bulk DB deletes
5. Add a `deletion_status` field to knowledge/user to track progress

### Sync Worker Fix: Use DeletionService

Replace inline cleanup in `sync_worker.py` with `DeletionService.delete_file()`:

```python
# In _handle_deleted_item and _handle_revoked_source:
from open_webui.services.deletion import DeletionService

# Instead of inline vector + DB cleanup:
file_report = DeletionService.delete_file(file_id)
```

## Code References

- `backend/open_webui/services/deletion/service.py:48-125` — `DeletionService.delete_file()` (per-file cascade)
- `backend/open_webui/services/deletion/service.py:186-272` — `DeletionService.delete_knowledge()` (KB cascade)
- `backend/open_webui/services/deletion/service.py:306-506` — `DeletionService.delete_user()` (full user cascade)
- `backend/open_webui/routers/knowledge.py:756-832` — KB delete endpoint (async def, sync calls)
- `backend/open_webui/routers/knowledge.py:591-748` — File removal from KB
- `backend/open_webui/routers/users.py:580-647` — User delete endpoint (async def, sync call)
- `backend/open_webui/routers/chats.py:201-213` — Bulk chat delete (bypasses DeletionService)
- `backend/open_webui/services/onedrive/sync_worker.py:916-956` — Delta deletion (no Storage cleanup)
- `backend/open_webui/services/onedrive/sync_worker.py:547-607` — Revoked source cleanup (no Storage cleanup)
- `backend/open_webui/retrieval/vector/main.py:23-86` — `VectorDBBase` abstract class (sync interface)
- `backend/open_webui/storage/provider.py:186-195` — S3 `delete_file()` (sync boto3 call)
- `backend/open_webui/internal/db.py:144-160` — Session factory (new session per `get_db()` call)
- `backend/open_webui/env.py:391-398` — `UVICORN_WORKERS` default = 1

## Architecture Insights

- The `DeletionService` was designed to centralize cascade deletion across all three layers (vectors, storage, DB), and it correctly handles the storage layer — but it was never designed for scale. It operates file-by-file with no batching.
- The knowledge router partially uses `DeletionService` (for file-level cleanup) but still has its own inline logic for model reference cleanup and KB vector collection deletion, rather than delegating fully to `DeletionService.delete_knowledge()`.
- The sync worker was written independently and doesn't use `DeletionService` at all, duplicating cleanup logic and missing the storage layer.
- All three interfaces (vector DB, storage, DB) are synchronous. FastAPI's async endpoints call them without thread offloading, blocking the single event loop.
- The default deployment (1 uvicorn worker, no timeouts) is especially vulnerable — a single large deletion blocks all traffic.

## Related Research

- `thoughts/shared/research/2026-02-14-kb-deletion-storage-leak.md` — Original research identifying the storage leak bug

## Open Questions

1. Should the fix prioritize quick (thread offloading) or proper (background tasks + batching)?
2. How much orphaned data already exists in S3 from the sync worker leaks?
3. Should `DeletionService` be made async, or is thread offloading sufficient?
4. Should the knowledge router delegate fully to `DeletionService.delete_knowledge()` instead of its current hybrid approach?
5. Is a "deleting" status/state needed to handle UI feedback for long-running deletions?
