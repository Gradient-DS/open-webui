---
date: 2026-03-29T12:00:00+02:00
researcher: Claude Code
git_commit: 84d62d6bd6abd3d83626a54906d97bb7381a619d
branch: fix/haute-bug
repository: Gradient-DS/open-webui
topic: 'Google Drive sync bugs: infinite spinning and flat folder picker'
tags: [research, codebase, google-drive, sync, picker, bug]
status: complete
last_updated: 2026-03-29
last_updated_by: Claude Code
---

# Research: Google Drive Sync Bugs

**Date**: 2026-03-29T12:00:00+02:00
**Researcher**: Claude Code
**Git Commit**: 84d62d6bd
**Branch**: fix/haute-bug
**Repository**: Gradient-DS/open-webui

## Research Question

Bug report on Google Drive integration:

1. Linking a folder keeps spinning/syncing infinitely (spinner never stops)
2. All subfolders shown at the same level as main folders in the picker (flat instead of hierarchical)

## Summary

**Bug 1 (Infinite sync):** The sync processes all files (screenshot shows "6 / 6") but the spinner persists. The most likely root cause is that the `sync()` method completes file processing but encounters an error during post-processing (source saving or final status update), OR the Socket.IO `completed` event is not reaching the frontend and polling gets stuck. Need server logs to confirm.

**Bug 2 (Flat folder picker):** The Google Picker is configured with `NAV_HIDDEN` and a single `DocsView` without `setParent()`. This causes the picker to show a flat search-like view of all drive items matching the MIME type filter, rather than a navigable hierarchical folder browser. The picker needs reconfiguration to show proper folder hierarchy.

## Detailed Findings

### Bug 1: Infinite Sync Spinner

#### Sync Flow (Normal Path)

1. User picks items in Google Picker → frontend calls `POST /api/v1/google-drive/sync/items`
2. Backend spawns `BackgroundTask` running `GoogleDriveSyncWorker.sync()` (`base_worker.py:736`)
3. Worker sets status to `"syncing"` (`base_worker.py:741`)
4. BFS folder traversal collects files (`sync_worker.py:380-427`)
5. Parallel file processing via `asyncio.gather` (`base_worker.py:915-920`)
6. Post-processing: `_save_sources()` → final status update to `"completed"` (`base_worker.py:975-995`)
7. Socket.IO event `googledrive:sync:progress` with `status: "completed"` emitted (`base_worker.py:997-1007`)

#### Frontend Status Tracking

- **Socket.IO handler** (`KnowledgeBase.svelte:959-1058`): Listens for `googledrive:sync:progress`, updates state reactively
- **HTTP polling fallback** (`KnowledgeBase.svelte:748-797`): Polls `GET /sync/{knowledgeId}` every 2 seconds while status is `"syncing"` or `"access_revoked"`

#### Potential Root Causes

**1. Post-processing exception (most likely)**
After all files are processed (6/6 shown), the worker calls:

- `_save_sources()` at `base_worker.py:975` — writes `folder_map` + `page_token` to KB meta
- Final status update at `base_worker.py:980-995` — reads KB, updates meta

If `_save_sources()` succeeds but the final `Knowledges.update_knowledge_meta_by_id()` at line 995 fails (e.g., concurrent write conflict, large `folder_map` serialization issue), the status remains `"syncing"` in the DB. The outer `except` at line 1022 would try to set `"failed"`, but if the DB is the problem, that might fail too.

The `folder_map` dict (line 424) stores `{folder_id: relative_path}` for ALL folders in the tree. For "Vink Bouw" with 50+ subfolders, this could be a substantial JSON blob. If the KB meta column has a size limit, serialization could fail silently.

**2. Socket.IO disconnect + stale polling**
If the WebSocket disconnects during sync, the `completed` event is lost. Polling (`KnowledgeBase.svelte:754`) continues every 2s and should eventually see `"completed"` in the DB. BUT if the DB status is stuck at `"syncing"` due to cause #1, polling runs forever.

**3. BFS traversal timeout for large folder trees**
If the user synced the parent "Vink Bouw" folder (not just "947 H - FINANCIEEL"), the BFS at `sync_worker.py:394-406` would visit all ~50+ subfolders. Each requires a Google Drive API call (`list_folder_children`). With rate limiting (429 responses, 60s retry at `drive_client.py:100-102`), this could take a very long time. The frontend would show the first few processed files while BFS is still running.

**4. Stale sync detection only at 30 minutes**
From `router.py:100-114`: a sync is considered stale after 30 minutes. Until then, the status remains `"syncing"` and new syncs are blocked. The user sees a spinner for up to 30 minutes before anything changes.

#### Diagnostic Steps

1. Check backend logs for errors after "Parallel processing completed" log message
2. Inspect the knowledge record's `meta.google_drive_sync.status` in the DB
3. Check if `folder_map` was saved (indicates post-processing reached that point)
4. Check for Google Drive API rate limiting (429 responses in logs)

### Bug 2: Flat Folder Structure in Picker

#### Current Picker Configuration

`src/lib/utils/google-drive-picker.ts:170-178`:

```typescript
const docsView = new google.picker.DocsView()
	.setIncludeFolders(true)
	.setSelectFolderEnabled(true)
	.setMimeTypes(SUPPORTED_MIME_TYPES);

const picker = new google.picker.PickerBuilder()
	.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
	.enableFeature(google.picker.Feature.NAV_HIDDEN)
	.addView(docsView);
```

#### Root Cause

The `NAV_HIDDEN` feature combined with a `DocsView` without `setParent()` causes the picker to display a flat search-like view rather than a navigable folder tree. The picker shows ALL items matching the MIME type filter (including subfolders from all depths) without hierarchical navigation.

Screenshot 2 confirms this: dozens of "947 - ..." subfolders from various depths are shown alongside the main category folders (947 A, 947 B, etc.) in a flat grid. Screenshot 3 shows the actual Drive hierarchy with only 9-10 top-level category folders under "Vink Bouw".

#### Fix Options

**Option A: Remove `NAV_HIDDEN` (simplest)**
Removing `NAV_HIDDEN` restores the Google Picker's built-in sidebar navigation ("My Drive", "Shared with me", "Recent") which provides hierarchical folder browsing. Users can navigate into folders to see their contents.

```typescript
const picker = new google.picker.PickerBuilder()
	.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
	// .enableFeature(google.picker.Feature.NAV_HIDDEN)  // Remove this
	.addView(docsView);
```

**Option B: Use `ViewId.FOLDERS` for folder-only view**
Add a separate view specifically for folder selection:

```typescript
const foldersView = new google.picker.DocsView(google.picker.ViewId.FOLDERS).setSelectFolderEnabled(
	true
);
```

**Option C: Use `setParent()` for scoped browsing**
If the user has already selected a root folder, set it as the picker's parent:

```typescript
const docsView = new google.picker.DocsView()
	.setIncludeFolders(true)
	.setSelectFolderEnabled(true)
	.setParent(rootFolderId)
	.setMimeTypes(SUPPORTED_MIME_TYPES);
```

**Recommendation:** Option A is the safest fix. The `NAV_HIDDEN` feature was previously used per the feedback memory for Google Picker (single combined view without tabs), but it has the side effect of flattening the folder hierarchy. Without `NAV_HIDDEN`, the picker restores proper folder navigation. Alternatively, keep `NAV_HIDDEN` but add `setParent('root')` to scope to the user's Drive root with hierarchical navigation.

## Code References

### Backend (Sync)

- `backend/open_webui/services/sync/base_worker.py:736-1028` — Main `sync()` method
- `backend/open_webui/services/sync/base_worker.py:223-269` — `_update_sync_status()` method
- `backend/open_webui/services/sync/base_worker.py:295-306` — `_save_sources()` method
- `backend/open_webui/services/google_drive/sync_worker.py:380-427` — `_collect_folder_files_full()` BFS
- `backend/open_webui/services/google_drive/sync_worker.py:429-509` — `_collect_folder_files_incremental()`
- `backend/open_webui/services/google_drive/drive_client.py:137-165` — `list_folder_children()`
- `backend/open_webui/services/sync/events.py:55-94` — `emit_sync_progress()`

### Frontend (Sync Status)

- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:748-797` — `pollCloudSyncStatus()`
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:959-1058` — `handleCloudSyncProgress()`
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1486-1500` — Socket event listeners

### Frontend (Picker)

- `src/lib/utils/google-drive-picker.ts:154-206` — `createKnowledgePicker()`
- `src/lib/utils/google-drive-picker.ts:170-178` — Picker view configuration (the bug)

### Folder Display

- `src/lib/components/workspace/Knowledge/KnowledgeBase/SourceGroupedFiles.svelte:51-74` — `buildFolderTree()`
- `src/lib/components/workspace/Knowledge/KnowledgeBase/FolderTreeNode.svelte` — Recursive tree node

## Architecture Insights

The sync system uses a provider-abstracted architecture shared between Google Drive and OneDrive:

- `BaseSyncWorker` handles common logic (file processing, status updates, concurrency)
- `GoogleDriveSyncWorker` overrides provider-specific methods (API calls, file collection)
- Socket.IO provides real-time updates; HTTP polling is a fallback
- The `folder_map` is persisted in KB meta for incremental sync support via the Changes API

The Google Picker is a third-party Google API component with limited customization. The `NAV_HIDDEN` + `DocsView` combination affects how items are presented.

## Open Questions

1. What do the server logs show after "6/6" files are processed? Is there an exception in `_save_sources()` or final status update?
2. Was the synced folder "947 H - FINANCIEEL" or the parent "Vink Bouw"? The BFS behavior differs significantly.
3. How large is the `folder_map` for the "Vink Bouw" folder tree? Could it exceed DB column limits?
4. Is the WebSocket connection stable during sync, or does it disconnect?
5. For the picker fix: should we keep `NAV_HIDDEN` (cleaner UI) and use `setParent('root')`, or restore full navigation?
