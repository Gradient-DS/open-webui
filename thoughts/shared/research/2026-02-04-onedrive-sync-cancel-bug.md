---
date: 2026-02-04T12:00:00+01:00
researcher: claude
git_commit: c8a5a93e8636629452ea0a57448ddb707f9bacb9
branch: feat/data-control
repository: open-webui
topic: "OneDrive sync cancellation bug and parent directory sync issue"
tags: [research, codebase, onedrive, sync, cancellation, race-condition, bug]
status: complete
last_updated: 2026-02-04
last_updated_by: claude
---

# Research: OneDrive Sync Cancellation Bug & Parent Directory Sync Issue

**Date**: 2026-02-04T12:00:00+01:00
**Researcher**: claude
**Git Commit**: c8a5a93e8636629452ea0a57448ddb707f9bacb9
**Branch**: feat/data-control
**Repository**: open-webui

## Research Question

OneDrive sync cancellation doesn't work. When the user tries to cancel, it sometimes starts syncing the parent directory, syncing 100's of files. What causes this and how to fix it?

## Summary

Three root causes were identified, all in the backend sync worker:

1. **Race condition in `_update_sync_status`** (PRIMARY): Progress updates overwrite the "cancelled" status back to "syncing" via a read-modify-write pattern, making cancellation ineffective.
2. **Cancellation check only runs once per file**: All coroutines check cancellation simultaneously at `asyncio.gather` startup (before the semaphore), then never check again. Files waiting on the semaphore proceed without re-checking.
3. **No concurrent sync guard**: The `sync_items` endpoint allows starting new syncs while one is running. Combined with broken cancellation, users retry and end up with multiple workers processing files simultaneously, causing the "hundreds of files" explosion.

The "parent directory" perception comes from lost delta links (not saved on cancel), forcing full re-scans that return ALL files in the folder tree, and from concurrent sync workers overlapping.

## Detailed Findings

### Root Cause 1: Race Condition in `_update_sync_status` (CRITICAL)

`sync_worker.py:143-166` — `_update_sync_status` does an unconditional read-modify-write:

```python
async def _update_sync_status(self, status, ...):
    knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})
    sync_info["status"] = status  # <-- Overwrites whatever is in DB
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
```

This is called from `process_with_semaphore` at line 597 with `status="syncing"` after every file completes. The race:

1. T=0: Worker processes file, status in DB = "syncing"
2. T=1: User clicks cancel, cancel endpoint sets status = "cancelled" in DB
3. T=2: Worker finishes next file, calls `_update_sync_status("syncing")`, reads DB (sees "cancelled"), **overwrites it back to "syncing"**, writes to DB
4. T=3: `_check_cancelled()` reads DB, sees "syncing" instead of "cancelled"

The cancel flag is effectively overwritten within milliseconds.

**Fix**: Check current DB status before overwriting:

```python
async def _update_sync_status(self, status, ...):
    knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
    if knowledge:
        meta = knowledge.meta or {}
        sync_info = meta.get("onedrive_sync", {})

        # Don't overwrite cancelled status with progress updates
        if sync_info.get("status") == "cancelled" and status == "syncing":
            return

        sync_info["status"] = status
        # ... rest unchanged
```

### Root Cause 2: Cancellation Check Only at Coroutine Start

`sync_worker.py:571-630` — All file processing coroutines are created upfront and submitted to `asyncio.gather`:

```python
tasks = [
    process_with_semaphore(file_info, i)
    for i, file_info in enumerate(all_files_to_process)
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

Inside `process_with_semaphore` (line 571), cancellation is checked BEFORE the semaphore:

```python
async def process_with_semaphore(file_info, index):
    # Check for cancellation — runs ONCE at coroutine start
    if self._check_cancelled():
        cancelled = True
        return FailedFile(...)

    async with semaphore:  # <-- Files wait here, no re-check after
        result = await self._process_file_info(file_info)
```

When `asyncio.gather` starts:
- All N coroutines run their `_check_cancelled()` check almost simultaneously (it's synchronous, no yielding)
- They all see "syncing" (not yet cancelled) and proceed to await the semaphore
- After that, no further cancellation checks ever happen

Even if Root Cause 1 is fixed, cancellation only takes effect for files whose coroutine hasn't yet reached the cancellation check — which is essentially none of them.

**Fix**: Add cancellation check after acquiring the semaphore and use the `cancelled` flag for fast-path:

```python
async def process_with_semaphore(file_info, index):
    nonlocal processed_count, failed_count, cancelled

    # Fast-path: another coroutine already detected cancellation
    if cancelled:
        return FailedFile(
            filename=file_info.get("name", "unknown"),
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message="Sync cancelled by user",
        )

    async with semaphore:
        # Re-check cancellation AFTER acquiring semaphore
        if cancelled or self._check_cancelled():
            cancelled = True
            return FailedFile(
                filename=file_info.get("name", "unknown"),
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message="Sync cancelled by user",
            )

        try:
            result = await self._process_file_info(file_info)
            # ... rest unchanged
```

### Root Cause 3: No Concurrent Sync Guard

`onedrive_sync.py:59-134` — The `sync_items` endpoint does not check if a sync is already in progress. It sets status to "syncing" and queues a new background task unconditionally:

```python
updated_sync = {
    "sources": all_sources,
    "status": "syncing",  # Overwrites any existing status
    ...
}
meta["onedrive_sync"] = updated_sync
Knowledges.update_knowledge_meta_by_id(request.knowledge_id, meta)

background_tasks.add_task(sync_items_to_knowledge, ...)
```

When cancellation fails and the user retries:
1. First worker is still running (cancel didn't work)
2. New `sync_items` request comes in, queues ANOTHER background task
3. Two workers run simultaneously, both processing files from the same folder
4. This doubles (or more) the number of files being processed

**Fix**: Check if a sync is already running and reject the request:

```python
# In sync_items endpoint, before starting
existing_sync = meta.get("onedrive_sync", {})
if existing_sync.get("status") == "syncing":
    raise HTTPException(
        status_code=409,
        detail="A sync is already in progress. Cancel it first or wait for it to complete."
    )
```

### Root Cause 4: Lost Delta Links on Cancellation

`sync_worker.py:680-681` — `_save_sources()` is only called on successful completion, not on cancellation:

```python
# Line 657-678: if cancelled, return early
if cancelled:
    await self._update_sync_status("cancelled", ...)
    return {...}  # <-- _save_sources() at line 681 is SKIPPED

# Line 681: Only reached on success
await self._save_sources()
```

When delta links aren't saved:
- The next sync has no delta_link for the folder sources
- Falls back to initial delta URL: `/drives/{drive_id}/items/{folder_id}/delta`
- This returns ALL items under the folder tree (full re-scan)
- A folder with 100+ files across subfolders returns them all

This explains the "hundreds of files" symptom and the "parent directory" perception — the full delta scan returns files from ALL nested subfolders, not just the immediate children.

**Fix**: Save sources even on cancellation (delta links are still valid for tracking position):

```python
if cancelled:
    # Save sources to preserve delta links for next sync
    await self._save_sources()

    await self._update_sync_status("cancelled", ...)
    return {...}
```

### The "Parent Directory" Perception

Confirmed that the OneDrive item picker correctly returns the selected item's own ID (`item.id`), NOT a parent's ID. The `driveId` comes from `item.parentReference.driveId` which is always the containing drive, not a folder.

The "parent directory" perception is caused by:
1. **Full delta re-scans** (Root Cause 4): Without delta links, the delta API returns ALL items recursively under the folder tree
2. **Concurrent sync workers** (Root Cause 3): Multiple workers processing overlapping file sets
3. **Microsoft Graph delta behavior**: The first delta call returns items from ALL nesting levels, including files in deeply nested subfolders that the user didn't intend to sync

### Additional Issue: `clear_exclusions` Amplifies the Problem

When the user opens the OneDrive picker again (via `oneDriveSyncHandler`), the request includes `clear_exclusions: true` (line 564 of `KnowledgeBase.svelte`). In the backend (`onedrive_sync.py:104-109`):

```python
if request.clear_exclusions:
    for source in all_sources:
        source.pop("delta_link", None)
        source.pop("content_hash", None)
```

This intentionally clears delta links, forcing a full re-scan. Combined with broken cancellation, this guarantees the "hundreds of files" scenario on retry.

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:143-166` — `_update_sync_status` race condition
- `backend/open_webui/services/onedrive/sync_worker.py:571-630` — Cancellation check only at coroutine start
- `backend/open_webui/services/onedrive/sync_worker.py:657-681` — Delta links not saved on cancel
- `backend/open_webui/routers/onedrive_sync.py:59-134` — No concurrent sync guard in `sync_items`
- `backend/open_webui/routers/onedrive_sync.py:192-218` — Cancel endpoint (correct, but ineffective due to race)
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:534-582` — `oneDriveSyncHandler` with `clear_exclusions: true`
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:644-652` — Cancel handler (correct, calls backend)
- `src/lib/apis/onedrive/index.ts:107-124` — `cancelSync` API client (correct)
- `backend/open_webui/services/onedrive/graph_client.py:129-159` — Delta query implementation (no filtering)

## Architecture Insights

- The cancellation mechanism uses a poll-based approach (DB flag) rather than an event-based one (e.g., `asyncio.Event`). This adds latency and creates race conditions with the read-modify-write pattern.
- The sync worker uses `asyncio.gather` with all tasks created upfront, which means cancellation must be checked AFTER semaphore acquisition, not before.
- FastAPI `BackgroundTasks` runs async tasks on the same event loop as web handlers, so synchronous DB calls in the worker can block the event loop during cancellation checks.
- The Microsoft Graph delta API returns items from the full subtree of the specified folder, not just direct children. This is by design but can surprise users when a full re-scan occurs.

## Recommended Fix Priority

1. **Fix `_update_sync_status` race condition** — Highest impact, simplest fix. Check DB status before overwriting.
2. **Add cancellation check after semaphore** — Required for cancellation to actually stop pending work.
3. **Add concurrent sync guard** — Prevents the worst-case scenario of multiple workers.
4. **Save delta links on cancellation** — Prevents full re-scans on retry, reducing compute cost.

## Open Questions

- Should we consider using `asyncio.Event` instead of DB polling for cancellation? Would reduce latency and eliminate the race condition entirely.
- Should the `clear_exclusions: true` behavior from the picker flow also be reconsidered? It forces full re-scans which are expensive.
- Should we add file count limits per sync that are more aggressive when multiple syncs have failed/been cancelled?
