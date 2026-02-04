# Fix OneDrive Sync Cancellation Bug

## Overview

OneDrive sync cancellation is broken. When the user clicks cancel, the sync continues processing files. Retrying after a failed cancel can trigger hundreds of files to sync due to lost delta links and concurrent workers. This plan fixes all 4 root causes identified in the [research document](../research/2026-02-04-onedrive-sync-cancel-bug.md).

## Current State Analysis

The cancellation mechanism uses DB polling (`_check_cancelled()` reads the `onedrive_sync.status` field). The cancel endpoint correctly sets `status = "cancelled"` in the DB, but three issues prevent the worker from seeing it:

1. Progress updates immediately overwrite "cancelled" back to "syncing"
2. Cancellation is only checked once per coroutine (before the semaphore), not after
3. No guard prevents starting a second sync while one is running
4. Delta links are lost on cancel, causing full re-scans on retry

### Key Discoveries:
- `sync_worker.py:160` — `_update_sync_status` overwrites status unconditionally
- `sync_worker.py:577` — `_check_cancelled()` runs before `async with semaphore` (line 585), never after
- `sync_worker.py:658-678` — cancelled path returns without calling `_save_sources()` at line 681
- `onedrive_sync.py:85-86` — `existing_sync` is read but never checked for `status == "syncing"`
- `KnowledgeBase.svelte:534` — `oneDriveSyncHandler` always sends `clear_exclusions: true`
- `KnowledgeBase.svelte:584` — `oneDriveResyncHandler` does NOT send `clear_exclusions` (correct)

## Desired End State

After implementation:
- Clicking "Cancel" stops file processing within 1-2 seconds (after current file completes)
- The "cancelled" status in the DB is never overwritten by progress updates
- Starting a new sync while one is running returns HTTP 409 with a clear message
- Cancelling a sync preserves delta links so the next sync is incremental, not a full re-scan
- The frontend shows the 409 error to the user

### How to verify:
1. Start a OneDrive sync with 5+ files
2. Click cancel after 1-2 files complete
3. Verify remaining files are NOT processed (check backend logs)
4. Start a new sync — verify it's incremental (delta-based), not a full re-scan
5. While a sync is running, try to start another — verify 409 response and toast message

## What We're NOT Doing

- Not switching from DB polling to `asyncio.Event` for cancellation (would require larger refactor)
- Not changing the `clear_exclusions` behavior from the picker flow (that's intentional UX)
- Not adding file count limits on retry
- Not changing the semaphore concurrency model

## Implementation Approach

All 4 fixes are small, independent changes. They modify 2 backend files and 1 frontend file. No schema changes, no new endpoints, no new dependencies.

## Phase 1: Fix All Cancellation Bugs

### Changes Required:

#### 1. ~~Prevent `_update_sync_status` from overwriting "cancelled" status~~ DONE
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Lines**: 155-166

Add an early return when the DB status is "cancelled" and the caller is trying to set "syncing":

```python
async def _update_sync_status(
    self,
    status: str,
    current: int = 0,
    total: int = 0,
    filename: str = "",
    error: Optional[str] = None,
    files_processed: int = 0,
    files_failed: int = 0,
    deleted_count: int = 0,
    failed_files: Optional[List[FailedFile]] = None,
):
    """Update sync status in knowledge meta and emit Socket.IO event."""
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

#### 2. ~~Add cancellation check after semaphore acquisition + fast-path via `cancelled` flag~~ DONE
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Lines**: 571-615

Replace the `process_with_semaphore` inner function:

```python
async def process_with_semaphore(
    file_info: Dict[str, Any], index: int
) -> Optional[FailedFile]:
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

            # Update counters with lock for thread safety
            async with results_lock:
                if result is None:
                    processed_count += 1
                else:
                    failed_count += 1

                # Emit progress update
                await self._update_sync_status(
                    "syncing",
                    processed_count + failed_count,
                    total_files,
                    file_info.get("name", ""),
                    files_processed=processed_count,
                    files_failed=failed_count,
                )

            return result
        except Exception as e:
            log.error(f"Error processing file {file_info.get('name')}: {e}")
            async with results_lock:
                failed_count += 1
            return FailedFile(
                filename=file_info.get("name", "unknown"),
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )
```

#### 3. ~~Save delta links on cancellation~~ DONE
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Lines**: 658-678

Add `_save_sources()` call before the cancelled return:

```python
# Check if cancelled during processing
if cancelled:
    log.info(f"Sync cancelled by user for knowledge {self.knowledge_id}")

    # Save sources to preserve delta links for next sync
    await self._save_sources()

    await self._update_sync_status(
        "cancelled",
        # ... rest unchanged
```

#### 4. ~~Add concurrent sync guard to `sync_items` endpoint~~ DONE
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Lines**: 83-86 (after getting `existing_sync`)

Add a check after reading existing sync info:

```python
# Get existing sources or initialize empty list
meta = knowledge.meta or {}
existing_sync = meta.get("onedrive_sync", {})

# Reject if a sync is already in progress
if existing_sync.get("status") == "syncing":
    raise HTTPException(
        status_code=409,
        detail="A sync is already in progress. Cancel it first or wait for it to complete.",
    )

existing_sources = existing_sync.get("sources", [])
```

#### 5. ~~Handle 409 response in frontend~~ DONE (no changes needed — existing error handling surfaces 409)
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

In `oneDriveSyncHandler` (line 571-578), the error is already caught and displayed via `toast.error`. The 409 detail message "A sync is already in progress..." will be shown automatically since the `startOneDriveSyncItems` function in `src/lib/apis/onedrive/index.ts` (line 81-83) throws with `error.detail`.

In `oneDriveResyncHandler` (line 612-615), the error is also caught and displayed. No frontend changes needed — the existing error handling already surfaces the 409 message.

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] TypeScript check passes: `npm run check` (pre-existing 8080 errors, none from our changes)
- [ ] Linting passes: `npm run lint:frontend` (pre-existing errors, none from our changes)
- [x] Backend linting passes: `npm run lint:backend` (7.02/10, no new issues)

#### Manual Verification:
- [ ] Start a OneDrive sync with 5+ files, cancel after 1-2 files — remaining files stop processing
- [ ] After cancelling, start a new sync — verify it uses delta links (incremental, not full re-scan)
- [ ] While a sync is running, click the sync button again — verify toast shows "A sync is already in progress" message
- [ ] Complete a full sync successfully — verify all files are processed and status shows "completed"
- [ ] Cancel a sync, then re-sync — verify previously synced files are skipped (hash match)

## Testing Strategy

### Manual Testing Steps:
1. Pick a OneDrive folder with 10+ files
2. Start sync, immediately cancel — check backend logs for "Sync cancelled by user" and verify no files are processed after cancel
3. Start sync again — verify it's delta-based (check logs for "File unchanged (hash match)" for already-synced files)
4. Open two browser tabs, try to start sync from both — verify second tab gets 409 error
5. Start sync, let it complete — verify all files appear in KB

### Edge Cases:
- Cancel when all files are already past the semaphore (should still stop via re-check)
- Cancel when no files have started yet (should stop immediately via fast-path)
- Network error during cancel (cancel endpoint should still set DB flag)

## References

- Research: `thoughts/shared/research/2026-02-04-onedrive-sync-cancel-bug.md`
- Sync worker: `backend/open_webui/services/onedrive/sync_worker.py`
- Sync router: `backend/open_webui/routers/onedrive_sync.py`
- Frontend: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- API client: `src/lib/apis/onedrive/index.ts`
