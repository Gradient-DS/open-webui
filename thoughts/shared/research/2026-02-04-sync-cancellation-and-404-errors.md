---
date: 2026-02-04T21:30:00+01:00
researcher: claude
git_commit: 0977c3485bcfea9af544093ec695798fad13c57d
branch: feat/simple-kb
repository: open-webui
topic: "OneDrive sync cancellation gaps vs feat/data-control + 404 process/file errors"
tags: [research, codebase, onedrive, sync, cancellation, retrieval, 404, config]
status: complete
last_updated: 2026-02-04
last_updated_by: claude
---

# Research: Sync Cancellation Gaps & 404 Retrieval Errors

**Date**: 2026-02-04T21:30:00+01:00
**Researcher**: claude
**Git Commit**: 0977c3485bcfea9af544093ec695798fad13c57d
**Branch**: feat/simple-kb
**Repository**: open-webui

## Research Questions

1. Are the sync cancellation fixes from `feat/data-control` present in `feat/simple-kb`?
2. Why is `POST /api/v1/retrieval/process/file` returning 404?

## Summary

### Cancellation: Three fixes missing from feat/simple-kb

The `feat/data-control` branch has three cancellation-related improvements that `feat/simple-kb` lacks:

| Fix | feat/data-control | feat/simple-kb |
|-----|-------------------|----------------|
| Status race guard (prevents progress overwriting "cancelled") | Yes | **No** |
| Duplicate sync prevention (409 if already syncing) | Yes | **No** |
| `_syncRefreshDone` flag (prevents double-refresh on cancel) | Yes | **No** |

Neither branch implements the **rollback approach** researched in `thoughts/shared/research/2026-02-04-TODO-onedrive-sync-cancel-pending-rollback.md`. In both branches, files that are already past the semaphore (up to `FILE_PROCESSING_MAX_CONCURRENT=5`) will complete despite cancellation.

### 404 Errors: WEBUI_URL misconfigured for dev mode

The sync worker calls `POST http://localhost:5173/api/v1/retrieval/process/file` because `.env` sets `WEBUI_URL=http://localhost:5173`. Port 5173 is the Vite dev server, which has no API routes. The FastAPI backend on port 8080 is where the endpoint actually lives. Fix: change `WEBUI_URL=http://localhost:8080` in `.env`.

## Detailed Findings

### Issue 1: Cancellation Gaps

#### Current cancellation flow (feat/simple-kb)

```
User clicks cancel → POST /onedrive/sync/{id}/cancel
  → Router writes status="cancelled" to DB (onedrive_sync.py:189)
  → Worker polls DB via _check_cancelled() (sync_worker.py:106-113)
  → Check happens once per file, BEFORE semaphore acquire (sync_worker.py:606)
  → In-flight files (inside semaphore) are NOT interrupted
```

#### Missing fix 1: Status race guard

In `feat/data-control`, `_update_sync_status()` has a guard:

```python
# Don't overwrite cancelled status with progress updates
if sync_info.get("status") == "cancelled" and status == "syncing":
    return
```

Without this, a concurrent progress update from the sync worker can overwrite the "cancelled" status back to "syncing" before the worker's next `_check_cancelled()` call, effectively ignoring the cancellation.

**Location**: `sync_worker.py:_update_sync_status()` (~line 290 in feat/data-control)

#### Missing fix 2: Duplicate sync prevention

In `feat/data-control`, the sync start endpoint rejects concurrent syncs:

```python
if existing_sync.get("status") == "syncing":
    raise HTTPException(
        status_code=409,
        detail="A sync is already in progress."
    )
```

Without this, a user (or auto-start) can trigger a second sync while one is running, leading to race conditions.

**Location**: `onedrive_sync.py` router, sync start endpoint

#### Missing fix 3: _syncRefreshDone flag

In `feat/data-control`, the frontend uses a `_syncRefreshDone` flag to prevent both Socket.IO and HTTP polling from refreshing the UI simultaneously when a sync completes or is cancelled. Without this, double-refresh can occur.

**Location**: `KnowledgeBase.svelte` polling and socket handlers

#### In-flight file completion (both branches)

Both branches have the same fundamental limitation: `asyncio.gather(*tasks)` launches all file tasks at once, and the cancellation check only runs before semaphore acquisition. Up to `FILE_PROCESSING_MAX_CONCURRENT` (default 5) files that have already acquired the semaphore will complete their full pipeline (download → upload → DB record → retrieval API → KB addition) with no interruption.

The rollback approach (researched in the prior document) was never implemented in either branch.

### Issue 2: 404 on /api/v1/retrieval/process/file

#### Root cause

The sync worker constructs the retrieval API URL using `WEBUI_URL` from config:

```python
# sync_worker.py (lines ~992, ~1054, ~1118)
base_url = WEBUI_URL.value if WEBUI_URL.value else "http://localhost:8080"
```

The `.env` file at line 72 sets:
```
WEBUI_URL=http://localhost:5173
```

This is the Vite dev server port. The Vite dev server has no proxy for `/api/v1/*` routes (confirmed: `vite.config.ts` has no proxy configuration). The actual endpoint exists on the FastAPI backend at port 8080:

- Router mount: `main.py:1487` — `app.include_router(retrieval.router, prefix="/api/v1/retrieval")`
- Endpoint: `retrieval.py:1568` — `@router.post("/process/file")`

#### Why the frontend works fine in dev

The frontend hardcodes port 8080 for dev mode in `src/lib/constants.ts:6-8`:

```typescript
export const WEBUI_HOSTNAME = browser ? (dev ? `${location.hostname}:8080` : ``) : '';
```

So browser API calls go directly to port 8080, bypassing Vite.

#### Fix

Change `.env:72` from:
```
WEBUI_URL=http://localhost:5173
```
to:
```
WEBUI_URL=http://localhost:8080
```

The fallback value in the sync worker code is already `"http://localhost:8080"`, so alternatively you could unset `WEBUI_URL` entirely for local dev.

#### Impact

All 205 file processing attempts failed because of this:
```
Sync completed for e2870a9f-...: 0 processed, 205 failed
```

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:106-113` — `_check_cancelled()` DB polling
- `backend/open_webui/services/onedrive/sync_worker.py:600-612` — Pre-semaphore cancellation check
- `backend/open_webui/services/onedrive/sync_worker.py:646-684` — `asyncio.gather(*tasks)` parallel processing
- `backend/open_webui/services/onedrive/sync_worker.py:992-1002` — First `process/file` URL construction
- `backend/open_webui/services/onedrive/sync_worker.py:1054-1061` — Second `process/file` URL construction
- `backend/open_webui/services/onedrive/sync_worker.py:1118-1178` — `_process_file_via_api` with URL construction
- `backend/open_webui/routers/onedrive_sync.py:169-195` — Cancel endpoint
- `backend/open_webui/routers/retrieval.py:1568` — `POST /process/file` endpoint definition
- `backend/open_webui/main.py:1487` — Retrieval router mount
- `backend/open_webui/config.py:1138` — `WEBUI_URL` config definition
- `.env:72` — `WEBUI_URL=http://localhost:5173` (wrong port)
- `src/lib/constants.ts:6-8` — Frontend dev mode hardcoded port 8080

## Related Research

- `thoughts/shared/research/2026-02-04-TODO-onedrive-sync-cancel-pending-rollback.md` — Rollback approach for in-flight files (researched on feat/data-control, not implemented)

## Open Questions

1. Should the three cancellation fixes from `feat/data-control` be cherry-picked/ported to `feat/simple-kb`?
2. Should the rollback approach be implemented to handle in-flight files that complete despite cancellation?
3. Should `WEBUI_URL` default to `http://localhost:8080` in `.env` or should the sync worker use a different mechanism (e.g., direct function call instead of HTTP) to process files?
