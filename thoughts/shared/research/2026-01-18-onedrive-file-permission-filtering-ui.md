---
date: 2026-01-18T15:30:00+01:00
researcher: Claude
git_commit: 12fee92cd50173380d4050daea864a8853e957f2
branch: feat/onedrive
repository: open-webui
topic: "OneDrive file permission filtering in shared collections UI"
tags: [research, codebase, onedrive, knowledge-base, permissions, ui]
status: complete
last_updated: 2026-01-18
last_updated_by: Claude
---

# Research: OneDrive File Permission Filtering in Shared Collections UI

**Date**: 2026-01-18T15:30:00+01:00
**Researcher**: Claude
**Git Commit**: 12fee92cd50173380d4050daea864a8853e957f2
**Branch**: feat/onedrive
**Repository**: open-webui

## Research Question

When a user views a shared/public collection containing OneDrive files they don't have OneDrive permissions for, they currently see all files but can't use them for retrieval. Instead, the UI should:
1. Only show files the user has access to
2. Display a message: "There are X additional OneDrive files in this collection, but you do not have the correct permissions to access these"

Is this implementation feasible and what's the approach?

## Summary

**Yes, this is feasible with moderate complexity.** The implementation requires:

1. **Backend**: Store OneDrive permitted user IDs during sync, filter files by permission in the API, return restricted file count
2. **Frontend**: Display the restricted files message when count > 0
3. **Translations**: Add EN and NL translation strings

The key insight is that the OneDrive sync already extracts and maps permitted user IDs (`sync_worker.py:209-214`) - we just need to store and use this information for file-level filtering.

## Detailed Findings

### Current Architecture

#### How OneDrive Files Are Stored (`sync_worker.py:458-466`)

Files have OneDrive-specific metadata:
```python
meta={
    "name": name,
    "content_type": self._get_content_type(name),
    "size": len(content),
    "source": "onedrive",              # Key identifier
    "onedrive_item_id": item_id,
    "onedrive_drive_id": self.drive_id,
    "last_synced_at": int(time.time()),
}
```

Frontend detects OneDrive files via `file?.meta?.source === 'onedrive'` (`Files.svelte:60`).

#### Current Permission Sync (`sync_worker.py:170-259`)

The sync process:
1. Fetches OneDrive folder permissions via Graph API
2. Extracts permitted emails from `grantedTo`, `grantedToIdentities`, `grantedToIdentitiesV2`
3. Maps emails to Open WebUI user IDs
4. Updates Knowledge base `access_control` with these user IDs

**Current storage** (`sync_worker.py:226-244`):
```python
access_control = {
    "read": {
        "user_ids": permitted_user_ids,  # OneDrive permitted users
        "group_ids": [],
    },
    "write": {
        "user_ids": [self.user_id],  # Only owner
        "group_ids": [],
    },
}
```

#### The Gap

When a Knowledge base is made **public** (`access_control = None`) or shared with additional users:
- The OneDrive permission mapping is overwritten or bypassed
- All users with Knowledge base access see all files
- But retrieval correctly filters (separate permission check)

### Proposed Implementation

#### 1. Store OneDrive Permitted Users in Knowledge Meta

Modify `sync_worker.py:226-259` to also store permitted users in `meta`:

```python
# In _sync_permissions(), after line 214:
# Store permitted users separately in meta for file-level filtering
meta = knowledge.meta or {}
if "onedrive_sync" not in meta:
    meta["onedrive_sync"] = {}
meta["onedrive_sync"]["permitted_user_ids"] = permitted_user_ids

# Update knowledge with both access_control and meta
Knowledges.update_knowledge_by_id(
    self.knowledge_id,
    KnowledgeForm(
        name=knowledge.name,
        description=knowledge.description,
        access_control=access_control,
        meta=meta,  # Include updated meta
    ),
)
```

#### 2. Modify API to Filter and Count Restricted Files

**File**: `backend/open_webui/routers/knowledge.py:355-400`

Extend the endpoint to:
1. Check if knowledge has OneDrive sync enabled
2. If user is not in OneDrive permitted users, filter OneDrive files
3. Return count of restricted files

**New response model** (add to `knowledge.py`):
```python
class KnowledgeFileListResponse(BaseModel):
    items: list[FileUserResponse]
    total: int
    restricted_onedrive_count: int = 0  # NEW FIELD
```

**Modified query logic** (in `search_files_by_id()` around line 400):
```python
def search_files_by_id(
    self,
    knowledge_id: str,
    user_id: str,
    filter: dict,
    skip: int = 0,
    limit: int = 30,
) -> KnowledgeFileListResponse:
    with get_db() as db:
        knowledge = self.get_knowledge_by_id(knowledge_id)

        # Check OneDrive permissions
        onedrive_sync = (knowledge.meta or {}).get("onedrive_sync", {})
        permitted_user_ids = onedrive_sync.get("permitted_user_ids", [])
        has_onedrive_access = (
            user_id == knowledge.user_id  # Owner always has access
            or user_id in permitted_user_ids
        )

        # Base query
        query = (
            db.query(File, User)
            .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
            .outerjoin(User, User.id == KnowledgeFile.user_id)
            .filter(KnowledgeFile.knowledge_id == knowledge_id)
        )

        # Count restricted OneDrive files if user doesn't have access
        restricted_onedrive_count = 0
        if not has_onedrive_access:
            restricted_onedrive_count = (
                db.query(File)
                .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                .filter(KnowledgeFile.knowledge_id == knowledge_id)
                .filter(File.meta["source"].as_string() == "onedrive")
                .count()
            )
            # Filter out OneDrive files from main query
            query = query.filter(
                or_(
                    File.meta["source"].as_string() != "onedrive",
                    File.meta["source"].is_(None)
                )
            )

        # ... rest of existing filter/sort logic ...

        return KnowledgeFileListResponse(
            items=items,
            total=total,
            restricted_onedrive_count=restricted_onedrive_count,
        )
```

#### 3. Update Frontend API Client

**File**: `src/lib/apis/knowledge/index.ts:197-236`

Update response type:
```typescript
interface KnowledgeFileListResponse {
    items: FileUserResponse[];
    total: number;
    restricted_onedrive_count?: number;  // NEW
}
```

#### 4. Update KnowledgeBase.svelte UI

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Add state variable (around line 102):
```svelte
let restrictedOnedriveCount = 0;
```

Update `getItemsPage()` (around line 150):
```svelte
if (res) {
    fileItems = res.items;
    fileItemsTotal = res.total;
    restrictedOnedriveCount = res.restricted_onedrive_count ?? 0;
}
```

Add message display (after line 1199, inside the file list area):
```svelte
{#if restrictedOnedriveCount > 0}
    <div class="my-3 px-4 py-2 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg text-sm text-yellow-800 dark:text-yellow-200">
        <div class="flex items-center gap-2">
            <OneDrive class="size-4" />
            <span>
                {$i18n.t('There are {{count}} additional OneDrive files in this collection, but you do not have the correct permissions to access these.', {
                    count: restrictedOnedriveCount
                })}
            </span>
        </div>
    </div>
{/if}
```

#### 5. Add Translation Strings

**File**: `src/lib/i18n/locales/en-US/translation.json`
```json
"There are {{count}} additional OneDrive files in this collection, but you do not have the correct permissions to access these.": ""
```

**File**: `src/lib/i18n/locales/nl-NL/translation.json`
```json
"There are {{count}} additional OneDrive files in this collection, but you do not have the correct permissions to access these.": "Er zijn {{count}} extra OneDrive-bestanden in deze verzameling, maar je hebt niet de juiste machtigingen om deze te openen."
```

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:170-259` - Permission sync logic
- `backend/open_webui/services/onedrive/sync_worker.py:458-466` - OneDrive file metadata
- `backend/open_webui/routers/knowledge.py:355-400` - Files API endpoint
- `backend/open_webui/models/knowledge.py:391-471` - `search_files_by_id()` method
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:133-160` - File fetching
- `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte:60-69` - OneDrive icon display
- `src/lib/i18n/locales/en-US/translation.json:1838` - Existing permission message pattern

## Architecture Insights

1. **Permission Levels**: The system has two levels of permission:
   - **Knowledge base level**: Who can see/edit the collection (stored in `knowledge.access_control`)
   - **OneDrive file level**: Who has OneDrive folder permissions (needs separate storage in `knowledge.meta`)

2. **Retrieval Already Works**: The retrieval system likely does separate permission checks, which is why OneDrive files aren't used for unpermitted users. The UI just needs to match this behavior.

3. **Email-Based Mapping**: OneDrive permissions map to Open WebUI users via email matching (`sync_worker.py:210-214`). Users without matching emails won't have OneDrive access.

## Implementation Complexity Assessment

| Component | Complexity | Effort |
|-----------|------------|--------|
| Store permitted users in meta | Low | Small change in sync_worker.py |
| Backend file filtering | Medium | New query logic, response model update |
| Frontend state + UI | Low | Add variable, conditional display |
| Translations | Low | Two strings, two files |

**Overall**: Moderate complexity, well-scoped changes.

## Edge Cases to Consider

1. **Knowledge base owner**: Should always see all files (owner check in filtering logic)
2. **Admin users**: May want to always see all files (add admin check)
3. **Empty permitted_user_ids**: If OneDrive permissions aren't synced yet, default to showing files to owner only
4. **Mixed content**: Collections with both OneDrive and manually uploaded files - only filter OneDrive files

## Open Questions

1. Should admins bypass OneDrive file filtering?
2. Should the message be dismissible or always visible?
3. Should we show this message in the knowledge base card/list view as well, not just in the detail view?

## Related Research

- `thoughts/shared/research/2026-01-18-onedrive-sync-ui-improvements.md` (if exists)
