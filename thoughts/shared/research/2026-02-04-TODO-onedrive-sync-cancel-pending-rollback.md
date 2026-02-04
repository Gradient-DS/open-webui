---
date: 2026-02-04T12:00:00+01:00
researcher: claude
git_commit: 2f06a078a3174f445d354cc9be03d1a8f045ec24
branch: feat/data-control
repository: open-webui
topic: "OneDrive sync cancellation: pending files continue after cancel, rollback approach"
tags: [research, codebase, onedrive, sync, cancellation, rollback]
status: complete
last_updated: 2026-02-04
last_updated_by: claude
---

# Research: OneDrive Sync Cancellation — Pending Files Continue After Cancel

**Date**: 2026-02-04
**Researcher**: claude
**Git Commit**: 2f06a078a
**Branch**: feat/data-control
**Repository**: open-webui

## Research Question

When cancelling a sync job, files that are already "pending" (in-flight / actively processing) still continue and get added to the knowledge base. Only files that haven't started processing yet are cancelled correctly. Can we also cancel the pending ones? What would a rollback-based approach look like?

## Summary

The cancellation check only exists at the **semaphore boundary** in `process_with_semaphore`. Once a file passes through the semaphore into `_process_file_info`, it runs the full pipeline (download → upload → DB record → retrieval API → add to KB) with no cancellation checks. With `FILE_PROCESSING_MAX_CONCURRENT = 5`, up to 5 files are always in-flight and will complete despite cancellation.

Two approaches were evaluated:
1. **Mid-pipeline cancellation** — add checks inside `_process_file_info`. Simple but creates orphaned partial state.
2. **Rollback on cancel** — let in-flight files finish, then undo the results. Cleaner UX, no orphans, and `DeletionService.delete_file()` already handles full cleanup.

The rollback approach is recommended. Main trade-off: delta links must be cleared on rollback, causing the next sync to do a full folder scan instead of incremental.

## Detailed Findings

### Current Cancellation Mechanism

Cancellation uses a cooperative DB-polling pattern:

1. User clicks cancel → frontend calls `POST /api/v1/onedrive/sync/{knowledge_id}/cancel`
2. API endpoint sets `knowledge.meta.onedrive_sync.status = "cancelled"` in DB (`onedrive_sync.py:220`)
3. Worker checks via `_check_cancelled()` which reads the DB (`sync_worker.py:105-112`)

The check happens at two points in `process_with_semaphore` (`sync_worker.py:576-628`):
- **Line 582**: Fast-path check of local `cancelled` boolean BEFORE acquiring the semaphore
- **Line 591**: DB check AFTER acquiring the semaphore

Once cancellation is detected, a local `cancelled = True` flag is set so all remaining coroutines skip the DB read.

### Why Pending Files Continue

All file coroutines are launched via `asyncio.gather(*tasks)` at line 643. The semaphore (capacity 5) controls concurrency. When the user cancels:

- **In-flight files** (inside the semaphore, executing `_process_file_info`): No cancellation checks exist within this method. They run the full pipeline to completion.
- **Queued files** (waiting for the semaphore): Correctly cancelled at line 591 after acquiring the semaphore.

The `_process_file_info` pipeline (`sync_worker.py:780-942`) has these steps with zero cancellation checks between them:
1. Emit `onedrive:file:processing` Socket.IO event (line 790)
2. Download from OneDrive via Graph API (line 802)
3. Upload to storage (line 846)
4. Create/update file DB record (lines 879-902)
5. Process via retrieval API — 2 calls (line 905)
6. Add to knowledge base (line 910)
7. Emit `onedrive:file:added` Socket.IO event (line 922)

### Approach 1: Mid-Pipeline Cancellation (Not Recommended)

Add cancellation checks at key stages within `_process_file_info` (before download, before upload, before retrieval API, before KB addition).

**Pros**: Files stop sooner, less wasted work.
**Cons**: Creates orphaned partial state — e.g., file uploaded to storage but not in DB, or file in DB but not processed through retrieval. Existing orphan detection at lines 828-841 handles some cases on next sync, but not all (e.g., storage files without DB records are never cleaned up).

### Approach 2: Rollback on Cancel (Recommended)

Let in-flight files finish naturally (no mid-pipeline interruption), then undo the results. This avoids orphans entirely.

#### Implementation Outline

1. **Track new vs. updated files**: In `_process_file_info`, after `Files.insert_new_file()` (line 901), append the `file_id` to a `self._new_file_ids` list. Files that were only updated (line 881) are NOT tracked — they existed before this sync.

2. **Rollback after gather**: In the cancellation block at line 671, after saving delta links, call `DeletionService.delete_file()` for each ID in `self._new_file_ids`. This performs complete cleanup:
   - Removes vectors from all KB collections referencing the file
   - Deletes the file's own vector collection (`file-{file_id}`)
   - Deletes from storage (`Storage.delete_file()`)
   - Deletes the DB file record (cascade-deletes KB junction records)

3. **Clear delta links**: Set `source["delta_link"] = None` for affected sources before saving. This ensures the next sync does a full folder scan and picks up the rolled-back files again.

4. **Frontend events**: Emit removal events for rolled-back files so the UI removes them from the file list.

#### Handling of Different Scenarios

| Scenario | Behavior |
|----------|----------|
| New source (first sync of a folder) | All files are new → all get rolled back on cancel |
| Re-sync (delta update of existing folder) | Only newly changed files are new additions → those get rolled back. Previously synced files are untouched. |
| Updated files (content changed for existing file) | Update stays in place — the file existed before this sync |

#### Trade-offs

- **Next sync is heavier**: Delta links are cleared, so the next sync does a full folder scan instead of incremental delta. This is the price for clean rollback semantics.
- **In-flight files still finish**: Up to `FILE_PROCESSING_MAX_CONCURRENT` (5) files complete before rollback. This is brief (~seconds per file) but means some work is done and then undone.
- **DeletionService error handling**: `DeletionService.delete_file()` is designed to continue through partial failures, collecting errors in `DeletionReport`. Rollback is resilient to individual cleanup failures.

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:576-628` — `process_with_semaphore` closure with cancellation checks
- `backend/open_webui/services/onedrive/sync_worker.py:105-112` — `_check_cancelled()` DB polling method
- `backend/open_webui/services/onedrive/sync_worker.py:780-942` — `_process_file_info` full pipeline (no cancellation checks)
- `backend/open_webui/services/onedrive/sync_worker.py:671-695` — Post-gather cancellation block
- `backend/open_webui/routers/onedrive_sync.py:200-226` — Cancel API endpoint
- `backend/open_webui/services/deletion/service.py:48-125` — `DeletionService.delete_file()` full cleanup
- `backend/open_webui/storage/provider.py:57` — `Storage.delete_file()` abstract method
- `backend/open_webui/retrieval/vector/main.py:74-81` — `VECTOR_DB_CLIENT.delete()` vector cleanup
- `backend/open_webui/models/knowledge.py:543-552` — `remove_file_from_knowledge_by_id()`
- `backend/open_webui/config.py:2831` — `FILE_PROCESSING_MAX_CONCURRENT` (default 5)

## Architecture Insights

- The sync system uses FastAPI `BackgroundTasks` (not Temporal/Celery). Cancellation is purely cooperative via DB flag polling — there's no way to interrupt a running coroutine.
- `DeletionService` follows a strict **Vectors → Storage → DB** deletion order and is the only component that performs complete cleanup across all four layers.
- The router-level file removal endpoint (`knowledge.py:582`) does NOT call `Storage.delete_file()`, so it's unsuitable for rollback. Only `DeletionService.delete_file()` performs full cleanup.
- Delta links from Microsoft Graph are folder-level change cursors. Once a delta has been consumed, rolled-back files won't appear in subsequent delta queries unless the delta link is cleared.

## Open Questions

- Should the rollback also remove the source entry from `knowledge.meta.onedrive_sync.sources` for brand-new sources (not previously synced), or just clear delta links and keep the source config?
- Should updated files (content changed during re-sync) also be reverted? This would require storing previous file state, adding significant complexity.
- Is there a UX need to show a "rolling back..." intermediate state in the frontend while cleanup runs?
