---
date: 2026-02-14T18:30:00+01:00
researcher: claude
git_commit: e2832132eb3e68bfb92a62bbdd765625bb5f39af
branch: feat/sync-improvements
repository: open-webui
topic: "OneDrive relative path computation for recursive folder tree display"
tags: [research, codebase, onedrive, graph-api, delta-query, relative-path, folder-tree]
status: complete
last_updated: 2026-02-14
last_updated_by: claude
---

# Research: OneDrive relative path computation for recursive folder tree display

**Date**: 2026-02-14T18:30:00+01:00
**Researcher**: claude
**Git Commit**: e2832132eb3e68bfb92a62bbdd765625bb5f39af
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question
How to correctly compute relative paths for OneDrive files in subfolders, so the UI can display a recursive folder tree. The current implementation relies on `parentReference.path` from the Graph API delta response, but this may not be populated.

## Summary

**Root cause**: Microsoft Graph API delta responses do **not reliably include** `parentReference.path`. The official docs state: *"The parentReference property on items will not include a value for path. This occurs because renaming a folder does not result in any descendants of the folder being returned from delta. When using delta you should always track items by id."*

This means the current `_collect_folder_files()` code at `sync_worker.py:277` (`item.get("parentReference", {}).get("path", "")`) gets an empty string, causing all files to fall through to flat `relative_path = item_name` on line 294.

**Solution**: Build an in-memory folder ID → relative path map from delta items. Delta returns both folder and file items recursively. Use `parentReference.id` (which IS available) to build parent-child relationships.

**Secondary issue**: Existing KBs never get `relative_path` backfilled because delta returns 0 items when nothing changed. Need a mechanism to force re-enumeration.

## Detailed Findings

### 1. The Delta `parentReference.path` Problem

**Microsoft Documentation** (https://learn.microsoft.com/en-us/graph/api/driveitem-delta):

> "The parentReference property on items will not include a value for path."

Fields that ARE available in delta responses:
- `item.id` - item's unique ID
- `item.name` - filename/folder name
- `item.parentReference.id` - **parent folder's item ID** (key for path building)
- `item.parentReference.driveId` - drive ID
- `item.folder` facet - present if item is a folder
- `item.file` facet - present if item is a file
- `item.@removed` / `item.deleted` - present if item was deleted

Fields NOT reliably available:
- `item.parentReference.path` - explicitly documented as absent in delta
- `item.parentReference.name` - not guaranteed

### 2. Current Implementation (Broken)

**File**: `backend/open_webui/services/onedrive/sync_worker.py:273-294`

```python
parent_ref_path = item.get("parentReference", {}).get("path", "")  # Always ""!
item_path = source.get("item_path", "")  # e.g. "/drives/abc/root:/Documents"
source_name = source.get("name", "")     # e.g. "WBSO"
source_path = f"{item_path}/{source_name}" if item_path else ""

# This comparison always fails because parent_ref_path is ""
if parent_ref_path.startswith(source_path):
    relative_parent = parent_ref_path[len(source_path):].lstrip("/")
```

Result: `relative_path` always equals just `item_name` (flat, no subfolder prefix).

### 3. Recommended Fix: ID-Based Folder Map

Delta returns ALL items recursively (folders + files). We can build a tree:

```python
async def _collect_folder_files(self, source):
    items, new_delta_link = await self._client.get_drive_delta(
        source["drive_id"], source["item_id"], delta_link
    )
    source["delta_link"] = new_delta_link

    # Step 1: Build folder ID → relative path map
    # The source folder itself is the root (empty relative path)
    folder_map = {source["item_id"]: ""}

    # First pass: register all folders from delta items
    for item in items:
        if "folder" in item and "@removed" not in item:
            parent_id = item.get("parentReference", {}).get("id", "")
            if parent_id in folder_map:
                parent_path = folder_map[parent_id]
                item_name = item.get("name", "")
                folder_map[item["id"]] = (
                    f"{parent_path}/{item_name}" if parent_path else item_name
                )

    # Second pass: compute file relative paths
    files_to_process = []
    deleted_count = 0

    for item in items:
        if "@removed" in item:
            await self._handle_deleted_item(item)
            deleted_count += 1
        elif self._is_supported_file(item):
            parent_id = item.get("parentReference", {}).get("id", "")
            parent_path = folder_map.get(parent_id, "")
            item_name = item.get("name", "unknown")
            relative_path = (
                f"{parent_path}/{item_name}" if parent_path else item_name
            )

            files_to_process.append({
                "item": item,
                "drive_id": source["drive_id"],
                "source_type": "folder",
                "source_item_id": source["item_id"],
                "name": item_name,
                "relative_path": relative_path,
            })

    return files_to_process, deleted_count
```

### 4. Handling Incremental Deltas (Key Edge Case)

On subsequent syncs, delta only returns **changed** items. Folder items may not be returned if the folder itself didn't change. This means `folder_map` won't have entries for unchanged parent folders.

**Solutions** (pick one):

**Option A: Persist `folder_map` in source metadata** (recommended)
- After each sync, save `folder_map` to `source["folder_map"]` in the knowledge metadata
- On incremental sync, load it first, then overlay new/changed folders from delta
- Handles: folder renames (folder appears in delta with new name), new subfolders, deletions

```python
# Load persisted map, overlay with current delta folders
folder_map = source.get("folder_map", {source["item_id"]: ""})
for item in items:
    if "folder" in item and "@removed" not in item:
        parent_id = item.get("parentReference", {}).get("id", "")
        if parent_id in folder_map:
            parent_path = folder_map[parent_id]
            folder_map[item["id"]] = f"{parent_path}/{item['name']}" if parent_path else item["name"]
    elif "folder" in item and "@removed" in item:
        folder_map.pop(item["id"], None)

# After processing, persist
source["folder_map"] = folder_map
```

**Option B: Fall back to `get_item_metadata` for unknown parents**
- When a file's `parentReference.id` is not in `folder_map`, call `GET /drives/{id}/items/{parent_id}` which DOES return `parentReference.path`
- Build the relative path from the full path response
- Pro: no persistence needed. Con: extra API calls, may hit rate limits

**Option C: Use `list_folder_items_recursive` as fallback**
- The existing `graph_client.py:133-155` method does a full recursive children enumeration and correctly computes `_relative_path`
- Could be used as a fallback when delta returns items but folder_map is incomplete
- Pro: already implemented. Con: O(N) API calls for N folders, doesn't benefit from delta efficiency

### 5. Migration for Existing KBs

For KBs that were synced before `relative_path` was implemented, delta returns 0 items (nothing changed), so files never get `relative_path` backfilled.

**Option A: "Force Full Sync" button** (recommended)
- Clear the `delta_link` for the source, forcing a full re-enumeration on next sync
- Could be exposed as a "Refresh folder structure" button in the UI
- Simple to implement: just set `source.delta_link = None` and trigger resync

**Option B: One-time backfill using `list_folder_items_recursive`**
- Before delta, call the recursive listing to get all files with `_relative_path`
- Match by item ID and update existing file meta
- Only needed once per source, could be gated by a `folder_map_version` field

**Option C: Version-based forced re-sync** (previously attempted, reverted)
- Track a `RELATIVE_PATH_VERSION` constant
- If stored version < current, clear delta link to force full sync
- Pro: automatic for all users. Con: forces full re-sync for everyone on upgrade

### 6. Frontend Components (Already Correct)

The frontend `SourceGroupedFiles.svelte` and `FolderTreeNode.svelte` are correctly implemented:

- `buildFolderTree()` at `SourceGroupedFiles.svelte:50-73` splits `relative_path` on `/` and builds a nested `FolderNode` tree
- `FolderTreeNode.svelte` recursively renders subfolders with collapse/expand
- Files are grouped by `source_item_id` into their respective folder sources

These components will work correctly once the backend provides proper `relative_path` values with subfolder prefixes.

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:241-307` - `_collect_folder_files` (needs fix)
- `backend/open_webui/services/onedrive/sync_worker.py:924-956` - `_process_file_info` relative_path fallback
- `backend/open_webui/services/onedrive/graph_client.py:157-187` - `get_drive_delta` implementation
- `backend/open_webui/services/onedrive/graph_client.py:133-155` - `list_folder_items_recursive` (existing, uses children endpoint)
- `src/lib/components/workspace/Knowledge/KnowledgeBase/SourceGroupedFiles.svelte:50-73` - `buildFolderTree()`
- `src/lib/components/workspace/Knowledge/KnowledgeBase/FolderTreeNode.svelte` - Recursive folder rendering
- `src/lib/utils/onedrive-file-picker.ts:1604-1611` - Picker returns `parentReference.path` for the selected item

## Architecture Insights

1. **Delta query is recursive**: It returns ALL items under a folder (not just direct children), including subfolder items. This is different from `/children` which only returns direct children.

2. **`item_path` on source objects**: This is `parentReference.path` of the **selected** item from the picker. It's the path of the folder that **contains** the selected item (e.g., `/drives/abc/root:/Documents` for a folder called "Reports" inside "Documents"). The sync worker correctly appends `source.name` to form the full source path.

3. **The `list_folder_items_recursive` method** (`graph_client.py:133-155`) already correctly computes relative paths using a BFS with `_relative_path`. This is a proven pattern but costs O(N) API calls. The delta approach should mirror this logic but using `parentReference.id` instead of the children endpoint.

4. **Persistence of folder_map**: The knowledge metadata JSON (`meta.onedrive_sync.sources[].folder_map`) is the natural place to persist the ID-to-path mapping. This dict is small (one entry per subfolder) and grows slowly.

## Open Questions

1. **Is `parentReference.path` actually populated for OneDrive for Business delta?** Some users report it works despite the docs. Worth adding a log line to check what we actually receive. If it IS populated in our environment, the fix is simpler (just handle the empty case as fallback).

2. **Folder rename handling**: If a folder is renamed in OneDrive, delta returns the folder item with the new name. The folder_map approach handles this naturally, but all child files also need their `relative_path` updated. Should we walk the stored file records and update them, or let the next sync handle it?

3. **How big can `folder_map` get?** For a source with hundreds of nested subfolders, the map could grow to ~100KB+ in the metadata JSON. This should be fine for most use cases but worth monitoring.
