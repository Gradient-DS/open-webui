# OneDrive Relative Path & Folder Tree Implementation Plan

## Overview

Fix the broken relative path computation for OneDrive-synced files so the UI can display recursively nested folders. The current implementation relies on `parentReference.path` from Graph API delta responses, which Microsoft explicitly documents as **not populated** in delta. Replace with an ID-based folder map approach using `parentReference.id`.

## Current State Analysis

- **`_collect_folder_files`** (`sync_worker.py:273-294`) computes `relative_path` by comparing `item.parentReference.path` against the source folder's full path. Since `parentReference.path` is empty in delta responses, every file gets `relative_path = filename` (flat, no subfolder prefix).
- **`_process_file_info`** (`sync_worker.py:936-960`) has a fallback that also tries `parentReference.path` — equally broken for the same reason.
- **Frontend** (`SourceGroupedFiles.svelte`, `FolderTreeNode.svelte`) correctly builds a recursive folder tree from `relative_path` by splitting on `/`. These components work — they just never receive subfolder-prefixed paths.
- **`list_folder_items_recursive`** (`graph_client.py:133-155`) exists and correctly computes `_relative_path` using BFS over the `/children` endpoint. This proves the pattern works; we just need to adapt it for delta responses using `parentReference.id` instead.

### Key Discoveries:
- Delta responses include `parentReference.id` (parent folder's item ID) but NOT `parentReference.path` — per Microsoft docs
- Delta returns BOTH folder and file items recursively, so we can build a folder ID → path map in a single pass
- On incremental deltas, unchanged folders are NOT returned, so the map must be persisted across syncs
- The `graph_client.get_drive_delta()` (`graph_client.py:157-187`) uses no `$select`/`$expand` params — this is correct, no filtering issue

## Desired End State

1. Files synced from OneDrive folders with subfolders have correct `relative_path` values (e.g., `SubFolder/file.pdf` instead of just `file.pdf`)
2. The folder map is persisted in source metadata so incremental syncs can resolve paths for unchanged folders
3. Existing KBs automatically get `relative_path` backfilled on next sync via version-gated delta link clearing
4. The UI displays nested subfolders correctly using the existing `SourceGroupedFiles` / `FolderTreeNode` components

### How to verify:
- Sync a fresh KB with a OneDrive folder containing known subfolders (e.g., WBSO with `2407-2412 WBSO HIPE`, `2501-2512 WBSO HIPE` subfolders)
- Verify files have `relative_path` like `2407-2412 WBSO HIPE/document.pdf` in their metadata
- Verify the UI shows collapsible subfolder nodes
- Trigger a resync (incremental delta) — verify paths are still correct
- Test with an existing KB that was synced before this fix — verify it gets a full re-enumeration and files get backfilled

## What We're NOT Doing

- Changing the Graph API query or adding `$select`/`$expand` params (unnecessary)
- Modifying frontend components (already correct)
- Adding a manual "Refresh folder structure" button (auto-migration handles it)
- Changing how single-file sources work (they don't have subfolders)

## Implementation Approach

Three phases, all in `sync_worker.py`:
1. Replace the broken path computation with an ID-based folder map
2. Auto-migrate existing KBs by clearing delta links when folder map is missing
3. Clean up the redundant fallback in `_process_file_info`

---

## Phase 1: Fix `_collect_folder_files` with ID-Based Folder Map

### Overview
Replace the `parentReference.path`-based relative path computation with a two-pass approach: first build a folder ID → relative path map from delta items, then use it to compute file relative paths.

### Changes Required:

#### 1. Rewrite path computation in `_collect_folder_files`
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Method**: `_collect_folder_files` (lines 241-307)

Replace the entire relative path computation block (lines 265-305) with the folder map approach:

```python
async def _collect_folder_files(
    self, source: Dict[str, Any]
) -> tuple[List[Dict[str, Any]], int]:
    """Collect files from a folder using delta query."""
    delta_link = source.get("delta_link")

    try:
        items, new_delta_link = await self._client.get_drive_delta(
            source["drive_id"], source["item_id"], delta_link
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 410:
            log.info("Delta token expired for source %s, performing full sync", source["name"])
            source["delta_link"] = None
            items, new_delta_link = await self._client.get_drive_delta(
                source["drive_id"], source["item_id"], None
            )
        else:
            raise

    # Update source with new delta link
    source["delta_link"] = new_delta_link

    # Build folder ID → relative path map.
    # Load persisted map from previous syncs (incremental deltas may omit
    # unchanged folders, so we need the historical mapping).
    folder_map: Dict[str, str] = source.get("folder_map", {})
    # The source folder itself is always the root (empty relative path)
    folder_map[source["item_id"]] = ""

    # First pass: update folder_map with any folder items from delta.
    # Delta items may arrive in any order, so we loop until no new folders
    # can be resolved (handles nested folders whose parent appears later).
    changed = True
    folder_items = [
        item for item in items
        if "folder" in item and "@removed" not in item
    ]
    while changed:
        changed = False
        for item in folder_items:
            if item["id"] in folder_map:
                # Already mapped (possibly from a previous sync)
                # But update name in case of rename
                parent_id = item.get("parentReference", {}).get("id", "")
                if parent_id in folder_map:
                    parent_path = folder_map[parent_id]
                    new_path = f"{parent_path}/{item['name']}" if parent_path else item["name"]
                    if folder_map[item["id"]] != new_path:
                        folder_map[item["id"]] = new_path
                        changed = True
                continue
            parent_id = item.get("parentReference", {}).get("id", "")
            if parent_id in folder_map:
                parent_path = folder_map[parent_id]
                folder_map[item["id"]] = (
                    f"{parent_path}/{item['name']}" if parent_path else item["name"]
                )
                changed = True

    # Handle deleted folders
    for item in items:
        if "folder" in item and "@removed" in item:
            folder_map.pop(item.get("id", ""), None)

    # Persist updated folder_map back to source
    source["folder_map"] = folder_map

    # Second pass: separate files and deleted items, compute relative paths
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

            files_to_process.append(
                {
                    "item": item,
                    "drive_id": source["drive_id"],
                    "source_type": "folder",
                    "source_item_id": source["item_id"],
                    "name": item_name,
                    "relative_path": relative_path,
                }
            )

    return files_to_process, deleted_count
```

Key changes from current code:
- Removed `parentReference.path`-based computation entirely
- Added `folder_map` loading from persisted source metadata
- Added iterative first-pass to handle out-of-order folder items
- Added folder rename detection (re-computes path if name changed)
- Added deleted folder cleanup from map
- Persists `folder_map` back to source after processing

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] No new lint errors: `npm run lint:backend`

#### Manual Verification:
- [ ] Sync a fresh KB with a OneDrive folder containing known subfolders
- [ ] Verify files have correct `relative_path` with subfolder prefixes in their `meta` (check via DB or API: `GET /api/v1/knowledge/{id}/files`)
- [ ] Verify the UI shows collapsible subfolder nodes in `SourceGroupedFiles`
- [ ] Trigger an incremental resync — verify paths remain correct for unchanged files
- [ ] Check logs for any "path mismatch" warnings (should be none now)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Auto-Migrate Existing KBs

### Overview
Existing KBs that were synced before this fix have files without `relative_path` and sources without `folder_map`. Their delta link is up-to-date, so delta returns 0 items and nothing gets backfilled. Add a version-gated mechanism to force a full re-enumeration.

### Changes Required:

#### 1. Add version constant and delta link clearing
**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Add a module-level constant after the existing constants (around line 66):

```python
# Version tracking for folder_map schema. Bump this to force a full
# re-enumeration of all folder sources on next sync (clears delta_link).
FOLDER_MAP_VERSION = 1
```

In `_collect_folder_files`, before calling `get_drive_delta`, check the version:

```python
async def _collect_folder_files(
    self, source: Dict[str, Any]
) -> tuple[List[Dict[str, Any]], int]:
    """Collect files from a folder using delta query."""
    delta_link = source.get("delta_link")

    # Force full re-enumeration if folder_map is outdated or missing
    stored_version = source.get("folder_map_version", 0)
    if stored_version < FOLDER_MAP_VERSION:
        log.info(
            "Folder map version %d < %d for source %s, forcing full sync",
            stored_version, FOLDER_MAP_VERSION, source.get("name"),
        )
        delta_link = None
        source["folder_map"] = {}  # Clear stale map

    try:
        items, new_delta_link = await self._client.get_drive_delta(
            source["drive_id"], source["item_id"], delta_link
        )
    # ... rest of method
```

After persisting `folder_map`, also persist the version:

```python
    # Persist updated folder_map and version back to source
    source["folder_map"] = folder_map
    source["folder_map_version"] = FOLDER_MAP_VERSION
```

#### 2. Update existing file records with relative_path during hash-match path

The hash-match path in `_process_file_info` (lines 1006-1051) already updates `relative_path` when it differs. This will fire during the forced full re-enumeration, backfilling all existing files.

No additional changes needed — the Phase 1 code + forced re-enumeration handles this.

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] No new lint errors: `npm run lint:backend`

#### Manual Verification:
- [ ] Take an existing KB with known subfolders (e.g., WBSO) that was synced before this fix
- [ ] Trigger a resync — verify logs show "Folder map version 0 < 1 ... forcing full sync"
- [ ] After sync completes, verify files now have correct `relative_path` with subfolder prefixes
- [ ] Verify the UI shows the nested folder structure
- [ ] Trigger another resync — verify it's incremental (not forced), delta returns 0 items, paths remain correct
- [ ] Verify `folder_map_version: 1` is persisted in the source metadata

**Implementation Note**: After completing this phase, pause for manual verification before proceeding to Phase 3.

---

## Phase 3: Clean Up `_process_file_info` Fallback

### Overview
Remove the redundant `parentReference.path`-based fallback in `_process_file_info` that can never work with delta responses. The folder map approach in `_collect_folder_files` now handles all cases.

### Changes Required:

#### 1. Simplify `_process_file_info` relative_path handling
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Method**: `_process_file_info` (lines 936-960)

Replace the entire fallback block with a simple read from `file_info`:

```python
async def _process_file_info(self, file_info: Dict[str, Any]) -> Optional[FailedFile]:
    """Download and process a single file from file_info structure."""
    item = file_info["item"]
    drive_id = file_info["drive_id"]
    item_id = item["id"]
    name = item["name"]
    source_item_id = file_info.get("source_item_id")
    relative_path = file_info.get("relative_path", name)

    log.info(f"Processing file: {name} (id: {item_id}, relative_path: {relative_path})")

    # ... rest of method unchanged, uses `relative_path` variable as before
```

This removes:
- The `if not relative_path and source_item_id:` block (lines 940-960) that tried to reconstruct from `parentReference.path`
- The matching source lookup
- The `parentReference.path` comparison logic
- The warning log about path computation

The `relative_path` is now always set by `_collect_folder_files` (for folder sources) or defaults to just `name` (for single file sources, which don't have subfolders).

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] No new lint errors: `npm run lint:backend`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] Sync a fresh KB — verify relative paths still work correctly
- [ ] Resync an existing KB — verify no regressions
- [ ] Verify no "Computed relative_path in _process_file_info" log messages (that code path is gone)

---

## Testing Strategy

### Manual Testing Steps:
1. **Fresh KB with subfolders**: Create a new OneDrive KB syncing a folder with known subfolders (WBSO). Verify UI shows nested folders.
2. **Existing KB migration**: Resync an existing KB (WBSO). Verify forced full sync happens once, files get `relative_path` backfilled, UI shows folders.
3. **Incremental delta**: After initial sync, resync again without OneDrive changes. Verify delta returns 0 items, paths stay correct.
4. **Mixed structure**: Test a folder with files at root level AND in subfolders. Verify root files appear at top, subfolder files are nested.
5. **Deep nesting**: If possible, test with 2+ levels of nesting (e.g., `FolderA/SubB/file.pdf`).

### Edge Cases:
- Folder with only subfolders (no root-level files) — tree should still render
- Single file source (no subfolders) — should work as before, `relative_path = name`
- Delta items arriving out of order (child folder before parent) — the iterative loop handles this
- Folder rename on OneDrive side — next delta should update the folder_map and file paths

## References

- Research document: `thoughts/shared/research/2026-02-14-onedrive-relative-path-folder-tree.md`
- Source management plan: `thoughts/shared/plans/2026-02-14-onedrive-source-management.md`
- Microsoft docs on delta `parentReference.path`: https://learn.microsoft.com/en-us/graph/api/driveitem-delta
- Sync worker: `backend/open_webui/services/onedrive/sync_worker.py`
- Graph client: `backend/open_webui/services/onedrive/graph_client.py`
- Frontend tree components: `src/lib/components/workspace/Knowledge/KnowledgeBase/SourceGroupedFiles.svelte`, `FolderTreeNode.svelte`
