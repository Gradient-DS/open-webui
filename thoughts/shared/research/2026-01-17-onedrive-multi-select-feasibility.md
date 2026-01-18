---
date: 2026-01-17T10:30:00+01:00
researcher: Claude
git_commit: 4248118b6d93f135979ca7094edf5f44a3f53494
branch: feat/onedrive
repository: open-webui
topic: "OneDrive File Picker Multi-Select and Mixed Item Type Selection Feasibility"
tags: [research, codebase, onedrive, file-picker, knowledge-sync, multi-select]
status: complete
last_updated: 2026-01-17
last_updated_by: Claude
last_updated_note: "Added clarified requirements for multi-select implementation"
---

# Research: OneDrive File Picker Multi-Select and Mixed Item Type Selection Feasibility

**Date**: 2026-01-17T10:30:00+01:00
**Researcher**: Claude
**Git Commit**: 4248118b6d93f135979ca7094edf5f44a3f53494
**Branch**: feat/onedrive
**Repository**: open-webui

## Research Question

Can the OneDrive file picker support:
1. Selecting multiple folders (currently limited to one)
2. Selecting files (currently not available for knowledge sync)
3. Both behaviors for direct chat attachment AND knowledge base collection addition

## Summary

**Yes, multi-select with mixed file/folder types is fully supported by Microsoft's OneDrive File Picker v8 SDK.**

The current single-item limitation is an implementation choice in our codebase, not a Microsoft API constraint. The picker can be configured to:
- Allow selecting multiple items (`selection.mode: 'multiple'`)
- Allow selecting both files AND folders (`typesAndSources.mode: 'all'`)
- Maintain selections across folder navigation (`selection.enablePersistence: true`)

Implementation would require changes to:
1. Picker configuration parameters
2. Result handling (currently takes only `items[0]`)
3. API contracts (currently expect single folder)
4. Backend processing logic
5. Knowledge metadata schema

## Detailed Findings

### Current Implementation Analysis

#### 1. File Picker Entry Points

| Function | File:Line | Mode | Selection | Use Case |
|----------|-----------|------|-----------|----------|
| `openOneDrivePicker()` | `onedrive-file-picker.ts:383` | `files` | Single (takes `items[0]`) | Legacy popup picker |
| `openOneDriveFilePickerModal()` | `onedrive-file-picker.ts:588` | `files` | Single (takes `items[0]`) | Chat attachment |
| `openOneDriveFolderPicker()` | `onedrive-file-picker.ts:914` | `folders` | Single (takes `items[0]`) | Knowledge sync |
| `pickAndDownloadFile()` | `onedrive-file-picker.ts:537` | `files` | Single | Chat attachment wrapper |
| `pickAndDownloadFileModal()` | `onedrive-file-picker.ts:898` | `files` | Single | Chat attachment wrapper |

#### 2. Current Picker Configuration

**File Picker Params** (`onedrive-file-picker.ts:553-585`):
```typescript
typesAndSources: {
    mode: 'files',      // ← Only files selectable
    pivots: {
        oneDrive: true,
        recent: true,
        myOrganization: config.getAuthorityType() === 'organizations'
    }
}
// Note: NO selection property configured - defaults to single select
```

**Folder Picker Params** (`onedrive-file-picker.ts:269-301`):
```typescript
typesAndSources: {
    mode: 'folders',    // ← Only folders selectable
    pivots: {
        oneDrive: true,
        recent: false,
        myOrganization: config.getAuthorityType() === 'organizations'
    }
}
// Note: NO selection property configured - defaults to single select
```

#### 3. Single-Item Result Handling

All picker functions take only the first item from the result array:

- **Chat attachment** (`onedrive-file-picker.ts:907`):
  ```typescript
  const selectedFile = pickerResult.items[0];
  ```

- **Knowledge sync** (`onedrive-file-picker.ts:1192-1202`):
  ```typescript
  const folder = items[0];
  resolve({
      id: folder.id,
      name: folder.name,
      driveId: folder.parentReference?.driveId,
      path: folder.parentReference?.path || '',
      webUrl: folder.webUrl || ''
  });
  ```

#### 4. Backend Single-Folder Design

**Request Schema** (`onedrive_sync.py:18-26`):
```python
class SyncFolderRequest(BaseModel):
    knowledge_id: str
    drive_id: str          # Single drive
    folder_id: str         # Single folder
    folder_path: str       # Single path
    access_token: str
    user_token: str
```

**Knowledge Meta Storage** (`sync_worker.py:303-332`):
```python
meta["onedrive_sync"] = {
    "drive_id": str,       # Single drive
    "folder_id": str,      # Single folder
    "folder_path": str,    # Single path
    "delta_link": str,     # Single delta cursor
    # ... status fields
}
```

### Microsoft OneDrive File Picker v8 Capabilities

#### Multi-Select Configuration

The picker supports a `selection` object with these properties:

| Property | Type | Description |
|----------|------|-------------|
| `mode` | `"single" \| "multiple" \| "pick"` | Selection mode. Use `"multiple"` for multi-select. |
| `enablePersistence` | `boolean` | Maintain selection across folder navigation |
| `enableNotifications` | `boolean` | Notify host when selection changes |
| `maximumCount` | `number` | Maximum selectable items |
| `sourceItems` | `IItem[]` | Pre-selected items |

#### Mixed Item Type Configuration

The `typesAndSources.mode` property controls selectable item types:

| Mode Value | Behavior |
|------------|----------|
| `"files"` | Only files selectable (current chat attachment config) |
| `"folders"` | Only folders selectable (current knowledge sync config) |
| `"all"` | **Both files AND folders selectable** |

#### Example Multi-Select Configuration

```typescript
const options = {
  sdk: "8.0",
  entry: { oneDrive: {} },
  messaging: {
    origin: window.location.origin,
    channelId: channelId
  },
  selection: {
    mode: "multiple",           // Enable multi-select
    enablePersistence: true,    // Keep selections across navigation
    maximumCount: 50            // Optional limit
  },
  typesAndSources: {
    mode: "all",                // Allow both files AND folders
    filters: ["folder", "file"],
    pivots: {
      oneDrive: true,
      recent: true
    }
  }
};
```

### Feasibility Assessment

#### Direct Chat Attachment (Files Only → Multiple Files)

**Current Flow:**
1. User clicks OneDrive in InputMenu
2. `pickAndDownloadFileModal()` opens picker with `mode: 'files'`
3. User selects ONE file
4. File downloaded and uploaded to backend
5. Added to message attachments

**Multi-Select Changes Required:**
1. Add `selection: { mode: 'multiple' }` to picker params
2. Return full `items` array instead of `items[0]`
3. Loop through items, download and upload each
4. Handle partial failures gracefully

**Complexity**: Low - mostly frontend changes to picker config and result handling.

#### Knowledge Base Addition (Single Folder → Multiple Items)

**Current Flow:**
1. User clicks "Sync from OneDrive" in AddContentMenu
2. `openOneDriveFolderPicker()` opens with `mode: 'folders'`
3. User selects ONE folder
4. Backend syncs folder contents via Graph API delta queries
5. Files processed and added to knowledge base

**Multi-Select Changes Required:**

**Frontend:**
1. Add `selection: { mode: 'multiple' }` to picker params
2. Change `typesAndSources.mode` from `'folders'` to `'all'`
3. Return full items array with type discrimination
4. Update API call to send array of items

**Backend:**
1. Update `SyncFolderRequest` to accept arrays:
   ```python
   class SyncItemsRequest(BaseModel):
       knowledge_id: str
       items: List[SyncItem]  # folders and files
       access_token: str
       user_token: str

   class SyncItem(BaseModel):
       type: Literal['file', 'folder']
       drive_id: str
       item_id: str
       item_path: str
   ```

2. Update `knowledge.meta.onedrive_sync` schema:
   ```python
   {
       "sources": [
           {
               "type": "folder",
               "drive_id": str,
               "item_id": str,
               "item_path": str,
               "delta_link": str  # Per-folder delta cursor
           },
           {
               "type": "file",
               "drive_id": str,
               "item_id": str,
               "item_path": str
           }
       ],
       "status": str,
       "last_sync_at": int,
       # ...
   }
   ```

3. Update `OneDriveSyncWorker` to:
   - Process multiple folders with separate delta queries
   - Handle individual file items directly
   - Track per-source sync state

**Complexity**: Medium-High - requires schema changes, backend refactoring.

### Recommended Approach

#### Option A: Minimal Change (Multiple of Same Type)

Keep separate picker modes but enable multi-select:
- Chat: Multiple files via `selection.mode: 'multiple'`
- Knowledge: Multiple folders via `selection.mode: 'multiple'`

**Pros**: Simpler backend changes, clear mental model
**Cons**: Still can't mix files and folders

#### Option B: Full Flexibility (Mixed Types)

Single picker mode allowing files AND folders:
- Use `typesAndSources.mode: 'all'`
- Backend handles both item types

**Pros**: Maximum flexibility, better UX
**Cons**: More complex backend, schema migration needed

#### Option C: Hybrid

- Direct attachment: Multiple files (Option A approach)
- Knowledge sync: Multiple folders OR multiple files, not mixed

**Pros**: Balances flexibility with implementation simplicity
**Cons**: Inconsistent UX between modes

## Code References

### Frontend Picker Implementation
- `src/lib/utils/onedrive-file-picker.ts:233-266` - File picker params (needs `selection` property)
- `src/lib/utils/onedrive-file-picker.ts:269-301` - Folder picker params (needs `selection` property)
- `src/lib/utils/onedrive-file-picker.ts:553-585` - Modal file picker params
- `src/lib/utils/onedrive-file-picker.ts:1192` - Single-item extraction point

### Knowledge Sync Flow
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:522-557` - Sync handler
- `src/lib/apis/onedrive/index.ts:3-10` - `SyncFolderRequest` interface
- `backend/open_webui/routers/onedrive_sync.py:18-26` - Backend request model
- `backend/open_webui/services/onedrive/sync_worker.py:252-359` - Sync worker main loop

### Direct Attachment Flow
- `src/lib/components/chat/MessageInput.svelte:1489-1503` - Upload handler
- `src/lib/components/chat/MessageInput/InputMenu.svelte:529-556` - OneDrive menu options

## Architecture Insights

1. **Picker SDK Agnostic**: The Microsoft File Picker v8 SDK is fully capable; limitations are in our integration code
2. **Delegated Auth Model**: Current token flow passes Graph API tokens from frontend to backend - this works for multi-item scenarios
3. **Delta Query Per Folder**: For multiple folder sync, each folder needs its own delta cursor tracked separately
4. **Schema Evolution**: `knowledge.meta.onedrive_sync` will need migration strategy for existing synced knowledge bases

## Open Questions

1. ~~**UX Decision**: Should mixed file/folder selection be allowed, or keep them separate?~~ **Resolved** - see follow-up below
2. **Sync Behavior**: For multiple folders, should they share one delta query or have independent cursors?
3. **Error Handling**: How to handle partial failures in multi-item scenarios?
4. **Migration**: How to handle existing single-folder synced knowledge bases?
5. **Limits**: What maximum item count should be allowed?

---

## Follow-up Research: Clarified Requirements (2026-01-17)

### User Requirements Confirmed

| Feature | Selection Type | Item Types | Picker Mode |
|---------|---------------|------------|-------------|
| **Direct Chat Attachment** | Multiple | Files only | `typesAndSources.mode: 'files'` |
| **Knowledge Collection Sync** | Multiple | Files AND Folders mixed | `typesAndSources.mode: 'all'` |

### Implementation Details

#### 1. Direct Chat Attachment: Multiple Files

**Picker Configuration Changes** (`getFilePickerParams` in `onedrive-file-picker.ts:553-585`):
```typescript
// Add selection property
selection: {
    mode: 'multiple',
    enablePersistence: true  // Keep selections when navigating folders
},
typesAndSources: {
    mode: 'files',  // Keep as files-only
    // ... existing config
}
```

**Result Handling Changes** (`pickAndDownloadFileModal` in `onedrive-file-picker.ts:898-911`):
```typescript
// Current: returns single file
const selectedFile = pickerResult.items[0];
return downloadOneDriveFile(selectedFile);

// New: return array of files
const files = await Promise.all(
    pickerResult.items.map(item => downloadOneDriveFile(item))
);
return files;  // Array of { blob, name }
```

**Upload Handler Changes** (`MessageInput.svelte:1489-1503`):
```typescript
// Current: single file
const fileData = await pickAndDownloadFileModal(authorityType);
if (fileData) {
    const file = new File([fileData.blob], fileData.name, { ... });
    await uploadFileHandler(file);
}

// New: multiple files
const filesData = await pickAndDownloadFilesModal(authorityType);  // Returns array
for (const fileData of filesData) {
    const file = new File([fileData.blob], fileData.name, { ... });
    await uploadFileHandler(file);  // Existing handler works for each file
}
```

**Complexity**: Low
- No backend changes required
- Existing `uploadFileHandler` works per-file
- Main changes in picker config and result handling

#### 2. Knowledge Collection Sync: Mixed Files and Folders

**Picker Configuration Changes** (`getFolderPickerParams` → `getItemPickerParams`):
```typescript
selection: {
    mode: 'multiple',
    enablePersistence: true
},
typesAndSources: {
    mode: 'all',  // Changed from 'folders'
    filters: ['file', 'folder'],  // Explicitly include both
    pivots: {
        oneDrive: true,
        recent: true,  // Enable recent for files
        myOrganization: config.getAuthorityType() === 'organizations'
    }
}
```

**New Return Type**:
```typescript
export interface ItemPickerResult {
    type: 'file' | 'folder';
    id: string;
    name: string;
    driveId: string;
    path: string;
    webUrl: string;
    size?: number;  // For files
}

// Returns array
export type MultiItemPickerResult = ItemPickerResult[];
```

**Frontend API Changes** (`src/lib/apis/onedrive/index.ts`):
```typescript
export interface SyncItemsRequest {
    knowledge_id: string;
    items: SyncItem[];
    access_token: string;
    user_token: string;
}

export interface SyncItem {
    type: 'file' | 'folder';
    drive_id: string;
    item_id: string;
    item_path: string;
    name: string;
}
```

**Backend Schema Changes** (`onedrive_sync.py`):
```python
class SyncItem(BaseModel):
    type: Literal['file', 'folder']
    drive_id: str
    item_id: str
    item_path: str
    name: str

class SyncItemsRequest(BaseModel):
    knowledge_id: str
    items: List[SyncItem]
    access_token: str
    user_token: str
```

**Knowledge Meta Schema** (new structure):
```python
{
    "onedrive_sync": {
        "sources": [
            {
                "type": "folder",
                "drive_id": "...",
                "item_id": "...",
                "item_path": "/root:/Documents/Reports",
                "name": "Reports",
                "delta_link": "...",  # Only for folders
                "file_count": 42
            },
            {
                "type": "file",
                "drive_id": "...",
                "item_id": "...",
                "item_path": "/root:/Documents/manual.pdf",
                "name": "manual.pdf",
                "content_hash": "sha256:..."  # For change detection
            }
        ],
        "status": "idle" | "syncing" | "completed" | "failed",
        "last_sync_at": 1705500000,
        "last_result": {
            "files_processed": 50,
            "files_failed": 2,
            "folders_synced": 3,
            "files_synced": 5
        }
    }
}
```

**Sync Worker Changes** (`sync_worker.py`):
```python
async def sync(self):
    for source in self.sources:
        if source["type"] == "folder":
            await self._sync_folder(source)
        else:
            await self._sync_single_file(source)

async def _sync_folder(self, source: dict):
    # Existing delta query logic
    delta_link = source.get("delta_link")
    items, new_delta = await self.graph.get_drive_delta(
        source["drive_id"],
        source["item_id"],
        delta_link
    )
    # Process files...
    source["delta_link"] = new_delta

async def _sync_single_file(self, source: dict):
    # Direct file download and processing
    # Use content hash for change detection (no delta query for single files)
    file_meta = await self.graph.get_item(source["drive_id"], source["item_id"])
    if file_meta.get("file", {}).get("hashes", {}).get("sha256Hash") != source.get("content_hash"):
        await self._process_file(file_meta)
        source["content_hash"] = file_meta["file"]["hashes"]["sha256Hash"]
```

**Complexity**: Medium-High
- Frontend: New picker function, new return types
- API: New request schema
- Backend: Worker refactoring to handle mixed types
- Database: Schema migration for existing synced knowledge bases

### OAuth Scope Note

Current scope from `.env:86`:
```
MICROSOFT_OAUTH_SCOPE=openid email profile offline_access User.Read Files.Read Sites.Read.All
```

This scope is sufficient for the multi-select implementation:
- `Files.Read` - Read user's OneDrive files
- `Sites.Read.All` - Read SharePoint sites (for business accounts)

No scope changes needed.

### Migration Strategy for Existing Knowledge Bases

For knowledge bases with existing `onedrive_sync` config:

```python
def migrate_onedrive_sync_schema(knowledge):
    old_sync = knowledge.meta.get("onedrive_sync", {})
    if "sources" not in old_sync and "folder_id" in old_sync:
        # Migrate single folder to sources array
        knowledge.meta["onedrive_sync"] = {
            "sources": [{
                "type": "folder",
                "drive_id": old_sync["drive_id"],
                "item_id": old_sync["folder_id"],
                "item_path": old_sync["folder_path"],
                "name": old_sync.get("folder_name", "Synced Folder"),
                "delta_link": old_sync.get("delta_link")
            }],
            "status": old_sync.get("status", "idle"),
            "last_sync_at": old_sync.get("last_sync_at"),
            "last_result": old_sync.get("last_result")
        }
```

### Remaining Open Questions

1. **Sync Behavior**: Should all folders share progress reporting, or show per-folder progress?
2. **Error Handling**: If one folder fails, should other folders continue syncing?
3. **Limits**: Maximum number of items that can be selected at once?
4. **Re-sync UX**: How to add more items to an existing synced knowledge base?
