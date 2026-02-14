# OneDrive KB Source Management Implementation Plan

## Overview

Add the ability to remove synced OneDrive folders/files from a Knowledge Base. Currently, `meta.onedrive_sync.sources` is additive-only -- sources can be added but never removed, and removing individual files from the KB just causes them to re-sync on the next cycle. This plan introduces source-level management: tracking which source each file belongs to, a backend endpoint to remove sources, and a frontend UI to view and manage synced sources.

## Current State Analysis

- **Sources are additive-only**: `POST /onedrive/sync/items` (`onedrive_sync.py:89-103`) deduplicates by `item_id` and appends, never removes
- **File removal is broken for OneDrive KBs**: Removing a file clears delta links (`knowledge.py:644-664`) but leaves the source in the array, so the file re-syncs on the next cycle
- **No file-to-source attribution**: Files store `onedrive_drive_id` and `onedrive_item_id` but not which source folder they came from
- **`_handle_revoked_source`** (`sync_worker.py:485-535`) matches files by `drive_id` only, which incorrectly removes files from ALL sources on the same drive
- **Microsoft File Picker SDK** does not support pre-selecting items, ruling out a "show existing as selected" approach

### Key Discoveries:
- `_collect_folder_files` (`sync_worker.py:239-280`) already includes `source_type` and `drive_id` in file_info dicts -- adding `source_item_id` is a natural extension
- `_process_file_info` (`sync_worker.py:886-1087`) writes file metadata at two points: update (line 1004-1012) and create (line 1026-1034) -- both need `source_item_id`
- The `Files.svelte` component (`Files.svelte:100-114`) shows delete buttons for all files regardless of source -- needs conditional logic
- `KnowledgeBase.svelte` already renders source-related info in the header area (lines 1350-1438) -- the sources panel fits naturally below

## Desired End State

1. Each OneDrive-synced file tracks which source (folder/file) it was synced from via `source_item_id` in its metadata
2. A backend endpoint allows removing a specific source from a KB, cleaning up all associated files, vectors, and orphaned records
3. The KB detail page shows synced sources visually, with the ability to remove individual sources
4. Per-file delete buttons are hidden for OneDrive-sourced files, preventing the confusing "remove then re-sync" behavior

### How to verify:
- Add a OneDrive folder to a KB, sync it, verify files have `source_item_id` in their meta
- Remove the source via the new UI, verify all files from that source are removed and don't re-sync
- Verify adding a second folder still works, and removing one doesn't affect the other
- Verify the delete button is hidden for OneDrive files but still works for local files in mixed KBs

## What We're NOT Doing

- Replacing the Microsoft File Picker with a custom browser
- Adding pre-selection support to the Microsoft picker (not possible)
- Changing how the background sync scheduler works
- Modifying the OAuth flow
- Handling migration of existing files without `source_item_id` (they'll get it on next sync)

## Implementation Approach

Four phases, each independently deployable:
1. **Backend: source tracking** -- add `source_item_id` to file metadata during sync
2. **Backend: source removal** -- new endpoint + improved cleanup logic
3. **Frontend: source management UI** -- visual source list with remove buttons
4. **Frontend: disable per-file delete** -- hide delete for OneDrive files

---

## Phase 1: Backend - Add `source_item_id` to File Metadata

### Overview
Thread the source's `item_id` through the sync pipeline so each file knows which source folder/file it was synced from.

### Changes Required:

#### 1. `_collect_folder_files` - Add source_item_id to file_info
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Add `source_item_id` to the file_info dict returned for each file in a folder

```python
# In _collect_folder_files, around line 271-278
files_to_process.append(
    {
        "item": item,
        "drive_id": source["drive_id"],
        "source_type": "folder",
        "source_item_id": source["item_id"],  # <-- ADD THIS
        "name": item.get("name", "unknown"),
    }
)
```

#### 2. `_collect_single_file` - Add source_item_id to file_info
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Add `source_item_id` to the returned dict

```python
# In _collect_single_file, around line 328-333
return {
    "item": item,
    "drive_id": source["drive_id"],
    "source_type": "file",
    "source_item_id": source["item_id"],  # <-- ADD THIS
    "name": item.get("name", source["name"]),
}
```

#### 3. `_process_file_info` - Store source_item_id in file meta
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Read `source_item_id` from file_info and include it in file metadata for both create and update paths

```python
# At the top of _process_file_info, around line 892-896
item = file_info["item"]
drive_id = file_info["drive_id"]
item_id = item["id"]
name = item["name"]
source_item_id = file_info.get("source_item_id")  # <-- ADD THIS

# In the update path, around line 1004-1012
meta={
    "name": name,
    "content_type": self._get_content_type(name),
    "size": len(content),
    "source": "onedrive",
    "onedrive_item_id": item_id,
    "onedrive_drive_id": drive_id,
    "source_item_id": source_item_id,  # <-- ADD THIS
    "last_synced_at": int(time.time()),
},

# In the create path, around line 1026-1034
meta={
    "name": name,
    "content_type": self._get_content_type(name),
    "size": len(content),
    "source": "onedrive",
    "onedrive_item_id": item_id,
    "onedrive_drive_id": drive_id,
    "source_item_id": source_item_id,  # <-- ADD THIS
    "last_synced_at": int(time.time()),
},
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] No new lint errors in changed files: `npm run lint:backend`

#### Manual Verification:
- [ ] Sync a OneDrive folder, verify file records have `source_item_id` in their meta (check via API or DB)
- [ ] Sync a single file, verify it also has `source_item_id`
- [ ] Existing files without `source_item_id` continue to work normally

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Backend - Source Removal Endpoint

### Overview
Add a new endpoint to remove a source from `meta.onedrive_sync.sources` and clean up all associated files. Also improve `_handle_revoked_source` to use `source_item_id` for precise matching.

### Changes Required:

#### 1. New Pydantic model for the request
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Changes**: Add request model after `SyncItemsRequest`

```python
class RemoveSourceRequest(BaseModel):
    """Request to remove a source from a KB's sync configuration."""
    item_id: str
```

#### 2. New endpoint: `POST /sync/{knowledge_id}/sources/remove`
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Changes**: Add new endpoint after the `cancel_sync` endpoint (after line 207)

```python
@router.post("/sync/{knowledge_id}/sources/remove")
async def remove_source(
    knowledge_id: str,
    request: RemoveSourceRequest,
    user: UserModel = Depends(get_verified_user),
):
    """Remove a source (folder/file) from a KB's OneDrive sync configuration."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    # Don't allow source removal while syncing
    if sync_info.get("status") == "syncing":
        raise HTTPException(
            status_code=409,
            detail="Cannot remove source while sync is in progress.",
        )

    sources = sync_info.get("sources", [])

    # Find the source to remove
    source_to_remove = None
    remaining_sources = []
    for source in sources:
        if source["item_id"] == request.item_id:
            source_to_remove = source
        else:
            remaining_sources.append(source)

    if not source_to_remove:
        raise HTTPException(status_code=404, detail="Source not found")

    # Remove associated files
    removed_count = _remove_files_for_source(
        knowledge_id=knowledge_id,
        source_item_id=request.item_id,
        source_drive_id=source_to_remove.get("drive_id"),
    )

    # Update sources
    sync_info["sources"] = remaining_sources
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    log.info(
        f"Removed source '{source_to_remove.get('name')}' from KB {knowledge_id}, "
        f"{removed_count} files cleaned up"
    )

    return {
        "message": "Source removed",
        "source_name": source_to_remove.get("name"),
        "files_removed": removed_count,
    }
```

#### 3. Helper function for file cleanup
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Changes**: Add helper function used by both the endpoint and potentially `_handle_revoked_source`

```python
def _remove_files_for_source(
    knowledge_id: str,
    source_item_id: str,
    source_drive_id: str,
) -> int:
    """Remove all files associated with a specific source from a KB.

    Matches files by source_item_id (preferred) or falls back to drive_id
    for legacy files that don't have source_item_id.
    """
    from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
    from open_webui.models.files import Files

    files = Knowledges.get_files_by_id(knowledge_id)
    if not files:
        return 0

    removed_count = 0
    for file in files:
        if not file.id.startswith("onedrive-"):
            continue

        file_meta = file.meta or {}
        file_source_item_id = file_meta.get("source_item_id")

        # Match by source_item_id if available, otherwise fall back to drive_id
        if file_source_item_id:
            if file_source_item_id != source_item_id:
                continue
        else:
            # Legacy file without source_item_id: match by drive_id
            if file_meta.get("onedrive_drive_id") != source_drive_id:
                continue

        # Remove KnowledgeFile association
        Knowledges.remove_file_from_knowledge_by_id(knowledge_id, file.id)

        # Remove vectors from KB collection
        try:
            VECTOR_DB_CLIENT.delete(
                collection_name=knowledge_id,
                filter={"file_id": file.id},
            )
        except Exception as e:
            log.warning(f"Failed to remove vectors for {file.id}: {e}")

        # Check for orphans (no other KB references this file)
        remaining = Knowledges.get_knowledge_files_by_file_id(file.id)
        if not remaining:
            try:
                VECTOR_DB_CLIENT.delete_collection(f"file-{file.id}")
            except Exception:
                pass
            Files.delete_file_by_id(file.id)

        removed_count += 1

    return removed_count
```

#### 4. Update `_handle_revoked_source` to use `source_item_id`
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Improve the file matching logic in `_handle_revoked_source` (lines 485-535) to prefer `source_item_id` over `drive_id`

Replace the matching condition at line 505:
```python
# OLD: if file_drive_id and source_drive_id and file_drive_id == source_drive_id:
# NEW:
file_source_item_id = file_meta.get("source_item_id")
source_item_id = source.get("item_id")

if file_source_item_id:
    # Precise match by source_item_id
    if file_source_item_id != source_item_id:
        continue
else:
    # Legacy fallback: match by drive_id (may over-match for same-drive sources)
    if not (file_drive_id and source_drive_id and file_drive_id == source_drive_id):
        continue
```

#### 5. Update file removal endpoint to skip delta clearing when source is being removed
**File**: `backend/open_webui/routers/knowledge.py`
**Changes**: No changes needed here -- the per-file removal endpoint still works as-is. When we disable the delete button in Phase 4, this code path won't be hit for OneDrive files anyway.

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] No new lint errors: `npm run lint:backend`

#### Manual Verification:
- [ ] Add two folders from the same drive to a KB, sync
- [ ] Call `POST /onedrive/sync/{kb_id}/sources/remove` with one source's `item_id`
- [ ] Verify only files from that source are removed, the other source's files remain
- [ ] Verify the removed source no longer appears in `meta.onedrive_sync.sources`
- [ ] Trigger a resync -- verify only the remaining source's files sync, the removed source doesn't come back
- [ ] Test removing a source while sync is in progress -- should get 409 error

**Implementation Note**: After completing this phase, pause for manual verification before proceeding to Phase 3.

---

## Phase 3: Frontend - Source Management UI

### Overview
Add a visual list of synced sources in the KB detail page header area. Each source shows its name, type, and a remove button with confirmation dialog.

### Changes Required:

#### 1. New API client function
**File**: `src/lib/apis/onedrive/index.ts`
**Changes**: Add `removeSource` function

```typescript
export async function removeSource(
	token: string,
	knowledgeId: string,
	itemId: string
): Promise<{ message: string; source_name: string; files_removed: number }> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/sync/${knowledgeId}/sources/remove`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ item_id: itemId })
	});

	if (!res.ok) {
		const error = await res.json();
		throw new Error(error.detail || 'Failed to remove source');
	}

	return res.json();
}
```

#### 2. Source list in KB header area
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**:

**2a. Add import** for `removeSource` (around line 41):
```typescript
import { startOneDriveSyncItems, getSyncStatus, cancelSync, getTokenStatus, removeSource, type SyncStatusResponse, type SyncItem, type FailedFile, type SyncErrorType } from '$lib/apis/onedrive';
```

**2b. Add state variables** (around line 1007-1009):
```typescript
let showRemoveSourceConfirm = false;
let sourceToRemove: { item_id: string; name: string } | null = null;
```

**2c. Add remove source handler** (after `oneDriveResyncHandler`, around line 607):
```typescript
const removeSourceHandler = async (itemId: string, sourceName: string) => {
    try {
        const result = await removeSource(localStorage.token, knowledge.id, itemId);
        toast.success($i18n.t('Source "{{name}}" removed. {{count}} file(s) cleaned up.', {
            name: result.source_name,
            count: result.files_removed
        }));
        // Refresh knowledge data and file list
        await init();
    } catch (error) {
        console.error('Remove source error:', error);
        toast.error($i18n.t('Failed to remove source: {{error}}', {
            error: error instanceof Error ? error.message : String(error)
        }));
    }
};
```

**2d. Add source list UI** below the description row (after line 1480, after the `</div>` that closes the description input):

```svelte
{#if knowledge?.type === 'onedrive' && knowledge?.meta?.onedrive_sync?.sources?.length && knowledge?.write_access}
    <div class="flex flex-wrap gap-1.5 mt-1.5">
        {#each knowledge.meta.onedrive_sync.sources as source}
            <div class="flex items-center gap-1 text-xs bg-gray-50 dark:bg-gray-850 rounded-lg px-2 py-1 group">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3 text-gray-400 shrink-0">
                    {#if source.type === 'folder'}
                        <path d="M2 4.5A2.5 2.5 0 0 1 4.5 2h1.382a1 1 0 0 1 .894.553L7.382 4H11.5A2.5 2.5 0 0 1 14 6.5v4a2.5 2.5 0 0 1-2.5 2.5h-7A2.5 2.5 0 0 1 2 10.5v-6Z" />
                    {:else}
                        <path fill-rule="evenodd" d="M4 2a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7.414A2 2 0 0 0 13.414 6L10 2.586A2 2 0 0 0 8.586 2H4Z" clip-rule="evenodd" />
                    {/if}
                </svg>
                <span class="text-gray-600 dark:text-gray-300 truncate max-w-[200px]" title={source.name}>
                    {source.name}
                </span>
                <button
                    class="p-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition opacity-0 group-hover:opacity-100"
                    on:click={() => {
                        sourceToRemove = { item_id: source.item_id, name: source.name };
                        showRemoveSourceConfirm = true;
                    }}
                >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3">
                        <path d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z" />
                    </svg>
                </button>
            </div>
        {/each}
    </div>
{/if}
```

**2e. Add confirmation dialog** (near the existing `SyncConfirmDialog`, around line 1262):

```svelte
<SyncConfirmDialog
    bind:show={showRemoveSourceConfirm}
    title={$i18n.t('Remove Source')}
    message={$i18n.t('This will remove "{{name}}" and all its synced files from this knowledge base. The files will no longer sync from OneDrive.', { name: sourceToRemove?.name ?? '' })}
    confirmLabel={$i18n.t('Remove')}
    on:confirm={() => {
        if (sourceToRemove) {
            removeSourceHandler(sourceToRemove.item_id, sourceToRemove.name);
        }
        showRemoveSourceConfirm = false;
        sourceToRemove = null;
    }}
    on:cancel={() => {
        showRemoveSourceConfirm = false;
        sourceToRemove = null;
    }}
/>
```

Note: Check the exact props of the `SyncConfirmDialog` (which is imported as `ConfirmDialog.svelte`) and adapt accordingly. The existing cancel sync dialog at line 1262-1270 serves as a reference for the pattern.

### Success Criteria:

#### Automated Verification:
- [ ] Frontend builds: `npm run build`
- [ ] No new lint errors in changed files: `npm run lint:frontend`

#### Manual Verification:
- [ ] Open an OneDrive KB with synced sources -- source chips appear below the description
- [ ] Each chip shows folder/file icon and name
- [ ] Hovering a chip reveals the remove (X) button
- [ ] Clicking remove shows confirmation dialog with source name
- [ ] Confirming removal removes the source chip and cleans up files
- [ ] The "+" button still opens the Microsoft picker to add new sources
- [ ] Sources are not shown for non-owners (no write_access)

**Implementation Note**: After completing this phase, pause for manual verification before proceeding to Phase 4.

---

## Phase 4: Frontend - Disable Per-File Delete for OneDrive Files

### Overview
Hide the delete button for OneDrive-sourced files in the file list. Show a tooltip explaining that files are managed by OneDrive sync.

### Changes Required:

#### 1. Update Files.svelte to conditionally show delete button
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte`
**Changes**: Replace the delete button block (lines 100-114) to check for OneDrive source

```svelte
{#if knowledge?.write_access}
    <div class="flex items-center">
        {#if file?.meta?.source === 'onedrive'}
            <Tooltip content={$i18n.t('Managed by OneDrive sync. Remove the source to stop syncing.')}>
                <div class="p-1 text-gray-300 dark:text-gray-600 cursor-default">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3">
                        <path fill-rule="evenodd" d="M8 1a3.5 3.5 0 0 0-3.5 3.5V7A1.5 1.5 0 0 0 3 8.5v5A1.5 1.5 0 0 0 4.5 15h7a1.5 1.5 0 0 0 1.5-1.5v-5A1.5 1.5 0 0 0 11.5 7V4.5A3.5 3.5 0 0 0 8 1Zm2 6V4.5a2 2 0 1 0-4 0V7h4Z" clip-rule="evenodd" />
                    </svg>
                </div>
            </Tooltip>
        {:else}
            <Tooltip content={$i18n.t('Delete')}>
                <button
                    class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
                    type="button"
                    on:click={() => {
                        onDelete(file?.id ?? file?.tempId);
                    }}
                >
                    <XMark />
                </button>
            </Tooltip>
        {/if}
    </div>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [ ] Frontend builds: `npm run build`
- [ ] No new lint errors: `npm run lint:frontend`

#### Manual Verification:
- [ ] OneDrive KB: delete buttons are replaced with lock icons for OneDrive-sourced files
- [ ] Hovering the lock icon shows "Managed by OneDrive sync. Remove the source to stop syncing."
- [ ] Local KB: delete buttons still appear and work normally
- [ ] Files currently being uploaded (status: 'uploading') are unaffected

---

## Testing Strategy

### Manual Testing Steps:
1. Create a new OneDrive KB, add a folder, sync it
2. Verify files have `source_item_id` in their metadata
3. Add a second folder from the same OneDrive drive
4. Verify both folders appear as source chips in the header
5. Remove one source via the chip's X button
6. Verify only that source's files are removed, other source's files remain
7. Trigger a resync -- verify only the remaining source syncs
8. Verify delete buttons are hidden for OneDrive files
9. Test with a mix of local and OneDrive KBs in the knowledge list
10. Test removing the last source from a KB -- should leave an empty KB

### Edge Cases:
- Legacy files without `source_item_id` (synced before Phase 1) -- should fall back to `drive_id` matching
- Removing a source while background sync is scheduled -- endpoint returns 409 if syncing
- KB with both folder and file sources -- each should be independently removable
- Multiple KBs sharing the same OneDrive file (via cross-KB propagation) -- orphan cleanup should only delete when no other KB references the file

## i18n Keys

New translation keys to add to `src/lib/i18n/locales/en-US/translation.json`:
- `"Managed by OneDrive sync. Remove the source to stop syncing."`: `""`
- `"Remove Source"`: `""`
- `"This will remove \"{{name}}\" and all its synced files from this knowledge base. The files will no longer sync from OneDrive."`: `""`
- `"Source \"{{name}}\" removed. {{count}} file(s) cleaned up."`: `""`
- `"Failed to remove source: {{error}}"`: `""`

(Empty string values in en-US mean "use the key itself" per project convention.)

## References

- Research document: `thoughts/shared/research/2026-02-14-onedrive-sync-folder-management.md`
- Sync worker: `backend/open_webui/services/onedrive/sync_worker.py`
- Sync router: `backend/open_webui/routers/onedrive_sync.py`
- Knowledge router: `backend/open_webui/routers/knowledge.py`
- KB detail page: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- File list component: `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte`
- OneDrive API client: `src/lib/apis/onedrive/index.ts`
