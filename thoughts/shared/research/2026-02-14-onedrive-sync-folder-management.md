---
date: 2026-02-14T12:00:00+01:00
researcher: claude
git_commit: 367bafc2907ce2ca9c9d8d3a783d09dafd8ba916
branch: feat/sync-improvements
repository: open-webui
topic: "OneDrive KB sync folder management - removing folders, pre-selection UI, file removal UX"
tags: [research, codebase, onedrive, sync, knowledge-base, ui-ux]
status: complete
last_updated: 2026-02-14
last_updated_by: claude
---

# Research: OneDrive KB Sync Folder Management

**Date**: 2026-02-14
**Git Commit**: 367bafc29
**Branch**: feat/sync-improvements

## Research Question

When syncing from a OneDrive folder to a KB, there is no way to remove that folder from sync. Even removing all files or adding different folders, the older folders still sync. How can we:
1. Allow removing synced folders while keeping multi-folder support
2. Show already-synced items as pre-selected when reopening the picker
3. Determine the best UX for file removal in OneDrive KBs

## Summary

The `meta.onedrive_sync.sources` array is **additive-only** -- there is no endpoint or UI to remove sources. Removing files from the UI clears delta links but leaves the source, causing re-sync on the next cycle. The Microsoft File Picker SDK v8 (iframe-based) does **not** support pre-selecting items, making the "show existing items as selected" approach impossible with the current picker. A custom "Manage Synced Sources" UI is needed.

## Detailed Findings

### Current Source Management Flow

**Sources are only added, never removed:**
- `POST /onedrive/sync/items` (`backend/open_webui/routers/onedrive_sync.py:89-103`) deduplicates by `item_id` and appends new sources
- No endpoint exists to remove or update the sources array
- The only removal happens automatically when OneDrive access is revoked (detected at sync time via `_verify_source_access()` at `sync_worker.py:451-483`)

**File removal doesn't remove the source:**
- `POST /knowledge/{id}/file/remove` (`backend/open_webui/routers/knowledge.py:589-704`) removes the file + vectors + junction row
- For OneDrive files (`file_id.startswith("onedrive-")`), it clears ALL delta links from sources (line 644-664)
- The source itself stays in the array, so next sync re-adds the file

### Microsoft File Picker SDK Limitations

The picker is loaded via iframe at `src/lib/utils/onedrive-file-picker.ts:1328-1646` using Microsoft's FilePicker.aspx endpoint. Key limitations:

- **No pre-selection API**: The picker SDK does not accept a list of pre-selected items
- **`selection.enablePersistence: true`** only persists selections within a single picker session (navigating between folders), not across openings
- **No way to customize the UI** beyond the params in `getItemPickerParams()` (line 320-355)
- The picker is a black-box Microsoft iframe -- we cannot inject custom UI into it

### Current "Add More" Flow

For existing OneDrive KBs, clicking the "+" button (`KnowledgeBase.svelte:1504-1523`) calls the same `oneDriveSyncHandler()` as initial creation. This opens a fresh picker with no indication of what's already synced.

### File Removal UX

Currently, the delete button on file rows (`Files.svelte:100-114`) works identically for local and OneDrive files. For OneDrive files, the backend sets `delete_file = False` to avoid breaking cross-KB references (`knowledge.py:606-607`).

## Proposed UI/UX Options

### Option A: "Manage Sources" Panel (Recommended)

Add a dedicated source management view that separates "manage existing" from "add new":

**UI Flow:**
1. OneDrive KB detail page shows synced sources as a list/chips in the header area (near the existing sync status)
2. Each source chip shows: folder/file name, path, and an "X" remove button
3. The existing "+" button opens the Microsoft picker to add more sources
4. Removing a source shows a confirmation dialog explaining files will be removed

**Backend changes:**
- New endpoint: `POST /onedrive/sync/{knowledge_id}/sources/remove` with `{ item_id: string }`
- This endpoint removes the source from `meta.onedrive_sync.sources`, removes all files associated with that source's `drive_id + item_id`, and cleans up vectors

**Pros:** Clear, intuitive, no dependency on Microsoft picker limitations
**Cons:** Adds UI complexity to the header area

### Option B: Intermediate Source Selection Modal

Show a custom modal before the Microsoft picker:

**UI Flow:**
1. User clicks "+" (or a new "Manage" button)
2. Custom modal opens showing currently synced sources as a checklist
3. User can uncheck sources to remove them
4. An "Add more from OneDrive" button opens the Microsoft picker
5. On confirmation, removed sources are unsynced and new ones are added

**Pros:** Single flow for add + remove, familiar checklist pattern
**Cons:** Extra step before the picker, could feel clunky

### Option C: Replace Microsoft Picker with Custom Browser

Build a custom OneDrive file/folder browser using the Graph API directly.

**Pros:** Full control over pre-selection, can show sync status per item
**Cons:** Significant engineering effort, must handle pagination/search/navigation, losing Microsoft's polished UI

### Recommendation

**Option A** is the most pragmatic. It's low effort, clear UX, and doesn't fight the Microsoft picker's limitations. The source chips in the header give users visibility into what's synced and a clear way to remove sources.

For file-level removal: **disable the delete button for OneDrive-sourced files** in the UI. Show a tooltip explaining "Files are managed by OneDrive sync. Remove the source folder to stop syncing." This prevents the confusing behavior where removed files reappear.

## Backend Changes Needed (All Options)

### New: Remove Source Endpoint

```python
# POST /api/v1/onedrive/sync/{knowledge_id}/sources/remove
# Body: { "item_id": "..." }
```

Logic:
1. Find and remove the source from `meta.onedrive_sync.sources` by `item_id`
2. Find all files in the KB with `meta.onedrive_drive_id == source.drive_id` (for folder sources, need to track which files came from which source)
3. Remove those files' KnowledgeFile junction rows
4. Remove vectors from the KB collection
5. Clean up orphaned File records
6. Save updated sources to meta

### Challenge: File-to-Source Attribution

Currently, files don't track which source (folder) they came from. The file's `meta` stores `onedrive_drive_id` and `onedrive_item_id` but not the parent folder's `item_id`. For folder sources, we'd need to either:
- Add `source_item_id` to file metadata during sync (tracks which source folder the file belongs to)
- Use the OneDrive `parentReference.path` to match files to folder sources (fragile, files can be nested)
- Query the Graph API to list folder contents and match by `item_id` (requires valid token)

**Recommendation**: Add `source_item_id` to file metadata in `_process_file_info()` at `sync_worker.py:998-1037`. This is the cleanest approach.

## Code References

- Source management (additive): `backend/open_webui/routers/onedrive_sync.py:89-103`
- File removal endpoint: `backend/open_webui/routers/knowledge.py:589-704`
- Delta link clearing: `backend/open_webui/routers/knowledge.py:644-664`
- Access revocation handling: `backend/open_webui/services/onedrive/sync_worker.py:451-535`
- Picker implementation: `src/lib/utils/onedrive-file-picker.ts:1328-1646`
- Picker params (no pre-selection): `src/lib/utils/onedrive-file-picker.ts:320-355`
- Add more button: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1504-1523`
- File delete button: `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte:100-114`
- Sync handler: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:528-571`
- Sources in meta: `backend/open_webui/services/onedrive/sync_worker.py:339-350`
- Background scheduler: `backend/open_webui/services/onedrive/scheduler.py:59-75`
