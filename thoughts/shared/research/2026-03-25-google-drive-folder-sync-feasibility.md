---
date: 2026-03-25T14:00:00+02:00
researcher: Claude
git_commit: 0ac933e8bd264fa786fd10f0fa4ec157e7de6b66
branch: feat/google-files
repository: open-webui
topic: "Can Google Drive folder selection work for KB sync (like OneDrive)?"
tags: [research, codebase, google-drive, knowledge-base, folder-sync]
status: complete
last_updated: 2026-03-25
last_updated_by: Claude
---

# Research: Google Drive Folder Sync Feasibility for Knowledge Bases

**Date**: 2026-03-25T14:00:00+02:00
**Researcher**: Claude
**Git Commit**: 0ac933e8bd264fa786fd10f0fa4ec157e7de6b66
**Branch**: feat/google-files
**Repository**: open-webui

## Research Question
Can users select Google Drive folders (not just files) for KB sync, similar to how OneDrive folder sync works?

## Summary

**Yes — folder sync is already fully implemented end-to-end.** The `createKnowledgePicker()` function in the Google Drive picker already presents a dedicated folder selection view alongside the files view. The backend `GoogleDriveSyncWorker` already implements folder traversal (both full and incremental via Google Drive Changes API). The entire pipeline — from picker to sync worker to background scheduler — is at full parity with OneDrive.

## Detailed Findings

### Frontend Picker: Already Supports Folders

`src/lib/utils/google-drive-picker.ts:131-193` — `createKnowledgePicker()` configures two views:

1. **Files view** (line 151-154): Shows documents with folders visible for navigation, but `setSelectFolderEnabled(false)` — folders can't be *selected* in this view.
2. **Folder view** (line 157-160): Dedicated tab with `setSelectFolderEnabled(true)` and `setMimeTypes('application/vnd.google-apps.folder')` — only folders are selectable.

When a folder is picked, the result includes `type: 'folder'` (line 174) based on the `application/vnd.google-apps.folder` mimeType.

### Frontend Sync Handler: Passes Folder Items Correctly

`src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:656-678` — The `cloudSyncHandler` for `google_drive` maps picker results to sync items preserving the `type` field (line 665: `type: item.type`), which will be `'folder'` for selected folders. These are sent to `startGoogleDriveSyncItems()`.

### Backend Router: Accepts Folder Items

`backend/open_webui/routers/google_drive_sync.py:65-101` — The `sync_items` endpoint delegates to the shared `handle_sync_items_request()` which stores items with their type in `knowledge.meta.google_drive_sync.sources`.

### Backend Sync Worker: Full Folder Traversal

`backend/open_webui/services/google_drive/sync_worker.py:108-117` — `_collect_folder_files()` dispatches to:

- **Full sync** (line 380+): BFS traversal of the folder tree using `drive_client.list_folder_children()`, captures a `startPageToken` for future incremental syncs.
- **Incremental sync** (line 429+): Uses Google Drive Changes API (`drive_client.get_changes()`) with stored `page_token` to detect only changed files since last sync. Falls back to full sync if Changes API fails.

### Backend Drive Client: All Required APIs

`backend/open_webui/services/google_drive/drive_client.py`:
- `list_folder_children()` (line 137) — Lists files in a folder with pagination
- `list_folder_children_recursive()` (line 167) — BFS traversal for nested folders
- `get_changes()` (line 192) — Incremental change detection
- `get_start_page_token()` (line 233) — Initial token for Changes API

### Background Scheduler: Handles Folders

`backend/open_webui/services/google_drive/scheduler.py` — Identical pattern to OneDrive. Runs every `GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES` (default 60), re-syncs all eligible Google Drive KBs using stored OAuth refresh tokens. Folder sources are re-synced incrementally via the stored `page_token`.

### Chat Picker: Does NOT Support Folders (By Design)

`src/lib/utils/google-drive-picker.ts:195-300` — `createPicker()` (used for in-chat file uploads) has `setIncludeFolders(false)` and `setSelectFolderEnabled(false)`. This is correct — chat uploads should only handle single files.

## Architecture Insights

The Google Drive sync implementation is a **1:1 mirror of OneDrive**. Both use the shared abstraction layer:

| Layer | OneDrive | Google Drive |
|-------|----------|-------------|
| Picker | `onedrive-file-picker.ts` | `google-drive-picker.ts` |
| API Client | `graph_client.py` (MS Graph) | `drive_client.py` (Drive API v3) |
| Sync Worker | `OneDriveSyncWorker` | `GoogleDriveSyncWorker` |
| Incremental Sync | MS Graph Delta API | Google Drive Changes API |
| Base Classes | `BaseSyncWorker`, `SyncProvider`, `TokenManager`, `SyncScheduler` |

## Code References

- `src/lib/utils/google-drive-picker.ts:131-193` — `createKnowledgePicker()` with folder view
- `src/lib/utils/google-drive-picker.ts:157-160` — Folder-specific DocsView configuration
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:656-678` — Google Drive sync handler
- `backend/open_webui/services/google_drive/sync_worker.py:108-117` — `_collect_folder_files()` dispatch
- `backend/open_webui/services/google_drive/sync_worker.py:380` — Full folder sync (BFS)
- `backend/open_webui/services/google_drive/sync_worker.py:429` — Incremental folder sync (Changes API)
- `backend/open_webui/services/google_drive/drive_client.py:137-237` — Folder listing and change detection APIs
- `backend/open_webui/services/google_drive/scheduler.py` — Background sync scheduler

## Open Questions

1. **Has folder sync been tested end-to-end?** The code is all there, but if it hasn't been exercised in practice, there may be edge cases (deeply nested folders, large folder trees, permission changes on subfolders).
2. **OAuth scopes**: The picker uses `drive.readonly` and `drive.file` scopes. For folder traversal the backend needs at minimum `drive.readonly` — this should already be covered by the server-side OAuth flow scope configuration.
