# OneDrive Sync Cancellation Fixes — Implementation Plan

## Overview

Port three sync cancellation fixes from `feat/data-control` to `feat/simple-kb` to make the cancel button work reliably. Currently, cancellation can be silently overwritten by progress updates, duplicate syncs can be triggered, and the frontend double-refreshes on terminal states.

## Current State Analysis

The sync cancellation mechanism is cooperative: the cancel endpoint writes `status="cancelled"` to the DB, and the sync worker polls the DB via `_check_cancelled()` before each file. Three gaps prevent this from working reliably:

1. **Status race**: `_update_sync_status()` unconditionally writes the new status (`sync_worker.py:132`), so a progress update can overwrite "cancelled" back to "syncing" before the next `_check_cancelled()` poll
2. **No duplicate prevention**: The `POST /sync/items` endpoint (`onedrive_sync.py:57`) doesn't check if a sync is already running, allowing a second sync to start
3. **Double-refresh**: Both Socket.IO and HTTP polling call `init()` on terminal states without coordination (`KnowledgeBase.svelte:588-887`)

### Key Discoveries:
- `_update_sync_status` at `sync_worker.py:115-180` — no guard against overwriting cancelled status
- `sync_items` endpoint at `onedrive_sync.py:57-111` — no check for existing "syncing" status
- `pollOneDriveSyncStatus` at `KnowledgeBase.svelte:588-618` and `handleOneDriveSyncProgress` at `KnowledgeBase.svelte:807-887` — both independently refresh on terminal states
- No `_syncRefreshDone` flag exists in the current branch

## Desired End State

After implementation:
- Clicking "Cancel Sync" reliably stops the sync (no race condition where progress overwrites cancellation)
- Attempting to start a sync while one is running returns HTTP 409
- Frontend refreshes exactly once on sync completion/cancellation (no double-refresh flicker)

### Verification:
1. Start a sync, cancel it — sync stops, UI shows cancelled state cleanly
2. Start a sync, try to start another — second attempt gets a 409 error
3. Let a sync complete naturally — file list refreshes exactly once

## What We're NOT Doing

- **Rollback of in-flight files**: Files already past the semaphore (up to 5 concurrent) will still complete. The rollback approach from `thoughts/shared/research/2026-02-04-TODO-onedrive-sync-cancel-pending-rollback.md` is out of scope.
- **WEBUI_URL fix**: The 404 errors from `POST /api/v1/retrieval/process/file` are a separate config issue (tracked separately).
- **Public KB blocking**: The `feat/data-control` branch also blocks syncing to public KBs with `STRICT_SOURCE_PERMISSIONS` — not porting that here.

## Implementation Approach

Three small, independent changes to three files. No migrations, no new dependencies. Each fix is self-contained.

## Phase 1: Status Race Guard

### Overview
Prevent `_update_sync_status()` from overwriting a "cancelled" status with a "syncing" progress update.

### Changes Required:

#### 1. Backend sync worker
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Add early return in `_update_sync_status()` when current DB status is "cancelled" and new status is "syncing"

After line 131 (`sync_info = meta.get("onedrive_sync", {})`), before line 132 (`sync_info["status"] = status`), add:

```python
            # Don't overwrite cancelled status with progress updates
            if sync_info.get("status") == "cancelled" and status == "syncing":
                return
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] No syntax errors in modified file

#### Manual Verification:
- [ ] Start a sync with many files, cancel immediately — the status stays "cancelled" and doesn't flicker back to "syncing"

---

## Phase 2: Duplicate Sync Prevention

### Overview
Reject sync requests with HTTP 409 if a sync is already in progress for the same knowledge base.

### Changes Required:

#### 1. Backend router
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Changes**: Add a status check in `sync_items()` after reading `existing_sync`

After line 75 (`existing_sync = meta.get("onedrive_sync", {})`) and before line 76 (`existing_sources = existing_sync.get("sources", [])`), add:

```python
    # Prevent duplicate syncs
    if existing_sync.get("status") == "syncing":
        raise HTTPException(
            status_code=409,
            detail="A sync is already in progress. Cancel it first or wait for it to complete.",
        )
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] No syntax errors in modified file

#### Manual Verification:
- [ ] Start a sync, then try to trigger another sync for the same KB — second attempt shows an error toast instead of starting

---

## Phase 3: Frontend Double-Refresh Guard

### Overview
Add a `_syncRefreshDone` flag to coordinate between Socket.IO and HTTP polling, preventing both from refreshing the UI simultaneously on terminal states.

### Changes Required:

#### 1. Frontend KnowledgeBase component
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

**Change A**: Declare the flag near the existing sync state variables (around line 930-931 where `isSyncingOneDrive` and `oneDriveSyncStatus` are declared):

```typescript
let _syncRefreshDone = false;
```

**Change B**: In `handleOneDriveSyncProgress` (Socket.IO handler), set `_syncRefreshDone = true` before refreshing in the `completed`/`completed_with_errors` block (around line 860, before `isSyncingOneDrive = false`):

```typescript
    if (data.status === 'completed' || data.status === 'completed_with_errors') {
        // ... existing toast logic unchanged ...
        _syncRefreshDone = true;
        isSyncingOneDrive = false;
        // ... existing refresh logic unchanged ...
    }
```

And similarly in the `cancelled` block (around line 877):

```typescript
    } else if (data.status === 'cancelled') {
        toast.info($i18n.t('OneDrive sync cancelled'));
        _syncRefreshDone = true;
        isSyncingOneDrive = false;
        // ... existing refresh logic unchanged ...
    }
```

**Change C**: In `pollOneDriveSyncStatus`, guard the refresh calls with `!_syncRefreshDone` for `completed`/`completed_with_errors` (around line 603) and `cancelled` (around line 613):

For the completed block:
```typescript
        } else if (oneDriveSyncStatus.status === 'completed' || oneDriveSyncStatus.status === 'completed_with_errors') {
            isSyncingOneDrive = false;
            if (!_syncRefreshDone) {
                await init();
            }
            _syncRefreshDone = false;
```

For the cancelled block:
```typescript
        } else if (oneDriveSyncStatus.status === 'cancelled') {
            isSyncingOneDrive = false;
            if (!_syncRefreshDone) {
                await init();
            }
            _syncRefreshDone = false;
```

**Change D**: Reset `_syncRefreshDone = false` at the start of sync trigger functions (wherever `isSyncingOneDrive = true` is set, around lines 511 and 558) to ensure a fresh state for each sync.

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds: `npm run build`
- [x] No TypeScript errors in modified file

Note: Phase 3 was already implemented in the current branch. Only fix applied: typo `res2` → `res` on line 915.

#### Manual Verification:
- [ ] Start a sync, let it complete — file list refreshes exactly once (no flicker/double-load)
- [ ] Start a sync, cancel it — file list refreshes exactly once

---

## Testing Strategy

### Manual Testing Steps:
1. Start an OneDrive sync for a KB with many files
2. Cancel immediately — verify status stays "cancelled", files stop being added
3. Start a sync, try to start another — verify 409 rejection
4. Let a sync complete naturally — verify single refresh, no flicker
5. Cancel a sync and verify single refresh, no flicker

## References

- Research: `thoughts/shared/research/2026-02-04-sync-cancellation-and-404-errors.md`
- Rollback research (out of scope): `thoughts/shared/research/2026-02-04-TODO-onedrive-sync-cancel-pending-rollback.md`
- Reference branch: `feat/data-control`
