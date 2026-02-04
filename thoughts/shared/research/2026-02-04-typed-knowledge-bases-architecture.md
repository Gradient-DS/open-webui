---
date: 2026-02-04T18:40:00+0100
researcher: Claude
git_commit: bb16bd5951b33aac5eaa113a15a9257870f25fb0
branch: feat/simple-kb
repository: open-webui
topic: "Typed Knowledge Bases: Separating Local and External Source KBs"
tags: [research, codebase, knowledge-base, onedrive, access-control, deduplication, architecture]
status: complete
last_updated: 2026-02-04
last_updated_by: Claude
---

# Research: Typed Knowledge Bases - Separating Local and External Source KBs

**Date**: 2026-02-04T18:40:00+0100
**Researcher**: Claude
**Git Commit**: bb16bd5951b33aac5eaa113a15a9257870f25fb0
**Branch**: feat/simple-kb
**Repository**: open-webui

## Research Question

What changes are needed to separate knowledge bases into typed categories (Local vs OneDrive/external), with different creation flows, sharing rules, hash-based deduplication for cross-user document reuse, upstream permission enforcement, and file count limits? How should we phase this work?

## Summary

The current system stores all knowledge bases in a single model with no `type` column. OneDrive KBs are identified implicitly via `meta.onedrive_sync` and the `onedrive-` file ID prefix. Local and OneDrive files can coexist in the same KB (mixed mode). Sharing uses a flexible JSON-based `access_control` field.

To implement typed KBs, six areas need changes:

1. **Data model**: Add a `type` column to the Knowledge table
2. **Creation flow**: Split "New Knowledge" into a dropdown with type-specific flows
3. **Access control by type**: External KBs are always private; local KBs use existing sharing
4. **Document deduplication**: Share File records across users via content hash matching
5. **Upstream permission enforcement**: Remove files when user loses OneDrive access
6. **File count limits**: Enforce 250-file cap for external KBs

The work naturally divides into 4 phases with clear milestones.

## Current State Analysis

### Knowledge Model
**File**: `backend/open_webui/models/knowledge.py:42-70`

The `Knowledge` table has NO `type` or `source_type` column. The only way to detect an external-source KB is:
- `meta.onedrive_sync` exists in the `meta` JSON field
- Associated files have IDs prefixed with `onedrive-`

Columns: `id`, `user_id`, `name`, `description`, `meta` (JSON), `access_control` (JSON), `created_at`, `updated_at`.

### KnowledgeFile Join Table
**File**: `backend/open_webui/models/knowledge.py:90-108`

Links knowledge bases to files. Columns: `id`, `knowledge_id` (FK), `file_id` (FK), `user_id`, timestamps. Has a unique constraint on `(knowledge_id, file_id)`.

### File Model
**File**: `backend/open_webui/models/files.py:16-31`

Files have: `id`, `user_id`, `hash` (SHA-256 of extracted text), `filename`, `path`, `data` (JSON with `content` and `status`), `meta` (JSON with `source`, `collection_name`, etc.), `access_control`, timestamps.

OneDrive files use `meta.source = "onedrive"` and IDs like `onedrive-{item_id}`.

### Current OneDrive Sync
**File**: `backend/open_webui/services/onedrive/sync_worker.py:641-936`

The sync worker processes each file:
1. Downloads from OneDrive via Graph API
2. Computes SHA-256 hash of content
3. Checks if file exists by ID (`onedrive-{item_id}`) and compares hash -- skips if unchanged
4. Uploads to storage, creates/updates File record
5. Calls internal retrieval API to extract text + embed into per-file collection
6. Copies embeddings into KB collection
7. Creates KnowledgeFile association

### Current Access Control
**File**: `backend/open_webui/utils/access_control.py:124-150`

Three states for `access_control`:
- `None` (null): Public -- all users can read
- `{}` (empty dict): Private -- only owner
- `{read: {group_ids, user_ids}, write: {group_ids, user_ids}}`: Granular sharing

The OneDrive sync worker currently writes to `access_control` based on OneDrive folder permissions (`sync_worker.py:298-394`), mapping emails to Open WebUI users.

### Current Creation Flow (Frontend)
**File**: `src/lib/components/workspace/Knowledge.svelte:140-148`

The "New Knowledge" button is a simple `<a>` link to `/workspace/knowledge/create`. No dropdown, no type selection.

**File**: `src/lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte`

Single form: name, description, access control. No concept of KB type.

### Document Processing Pipeline
**File**: `backend/open_webui/routers/retrieval.py:1568-1840`

When a file is added to a KB:
1. Existing chunks from `file-{id}` collection are retrieved
2. Chunks are copied into the KB's collection (named by KB UUID)
3. Hash-based dedup prevents duplicate insertion within the same collection

### Existing Plans
- `thoughts/shared/plans/2026-02-04-background-sync-multi-datasource.md` -- 5-phase plan for backend OAuth, token refresh, scheduler upgrade, multi-datasource abstraction. Focuses on enabling background sync. Does NOT address typed KBs or access control separation.
- `thoughts/shared/plans/2026-02-04-cosmetic-frontend-changes.md` -- Moves Knowledge to sidebar (separate from workspace). Relevant because the "New Knowledge" dropdown needs to work in the new sidebar location.

## Detailed Findings

### What Needs to Change

#### 1. Data Model: Add `type` Column

The Knowledge model needs a `type` field to distinguish KB sources at the schema level. This replaces the implicit detection via `meta.onedrive_sync`.

**New column on `knowledge` table:**
- `type`: Text, NOT NULL, default `"local"`, values: `"local"`, `"onedrive"`, (future: `"sharepoint"`, `"confluence"`, `"salesforce"`)

**Impact:**
- Alembic migration to add column with default value
- Data migration: existing KBs with `meta.onedrive_sync` get `type="onedrive"`
- `KnowledgeModel`, `KnowledgeForm`, `KnowledgeResponse` Pydantic models updated
- All list/search queries can now filter by type
- The `onedrive-` prefix convention on file IDs remains but is no longer the primary type indicator

#### 2. Creation Flow: Type-Specific KB Creation

The current "New Knowledge" button needs to become a dropdown:

| Option | Flow |
|--------|------|
| "Local Knowledge Base" | Existing flow: name, description, access control form |
| "From OneDrive" | New flow: name, description (no access control) → file/folder picker → create KB + start sync |

**Frontend changes:**
- `Knowledge.svelte`: Replace `<a>` button with dropdown menu component
- `CreateKnowledgeBase.svelte`: Accept a `type` query parameter, conditionally show/hide access control
- New: `CreateOneDriveKB.svelte` (or integrated into existing component with conditional UI)
- The OneDrive creation flow combines KB creation + file picker + sync start in one guided flow

**Backend changes:**
- `POST /knowledge/create`: Accept `type` field in `KnowledgeForm`
- Validate: if `type != "local"`, force `access_control = {}` (private)
- For OneDrive type: the sync is started separately via the existing `/onedrive/sync/items` endpoint

#### 3. Access Control by Type

**Rule:** External-source KBs are always private. Only the owner can access them. Sharing is handled upstream (e.g., OneDrive folder permissions determine who can even create a KB from that folder).

**Backend enforcement:**
- `POST /knowledge/create`: If `type != "local"`, force `access_control = {}`
- `POST /knowledge/{id}/update`: If `knowledge.type != "local"`, reject changes to `access_control`
- Remove `_sync_permissions()` from the OneDrive sync worker (currently at `sync_worker.py:298-394`). This function maps OneDrive folder permissions to KB `access_control`, which contradicts the "always private" rule.

**Frontend enforcement:**
- `KnowledgeBase.svelte:1221-1230`: Hide `AccessControlModal` when `knowledge.type !== "local"`
- `KnowledgeBase.svelte:1301-1315`: Hide "Access" button for non-local KBs
- `CreateKnowledgeBase.svelte:116-121`: Hide `AccessControl` component for non-local types

**What about the permission sync?** Instead of syncing OneDrive permissions to KB access_control, the system should:
- Let each user create their own private OneDrive KB
- The sync worker checks upstream permissions per-user (see section 5)
- Multiple users can have KBs pointing to the same OneDrive folder, with shared underlying document data (see section 4)

#### 4. Hash-Based Document Deduplication

**Goal:** When multiple users sync the same OneDrive document, embed it only once and share the underlying data.

**Current behavior:**
- Each file gets a unique `file-{uuid}` collection in the vector DB
- OneDrive files use `onedrive-{item_id}` as the File record ID
- The sync worker checks if `Files.get_file_by_id(f"onedrive-{item_id}")` exists and compares hashes

**New behavior:**
- OneDrive files use a deterministic ID: `onedrive-{item_id}` (unchanged -- already deterministic)
- When user A syncs a document, the File record and `file-{id}` collection are created
- When user B syncs the SAME document (same OneDrive item ID):
  - The File record already exists (same `onedrive-{item_id}`)
  - Hash matches → skip re-processing
  - Just create a KnowledgeFile association to user B's KB
  - Copy the existing vectors into user B's KB collection
- When the document changes upstream:
  - Hash mismatch detected during sync
  - Re-download, re-process, update the shared File record
  - Update vectors in ALL KBs that reference this file

**Key challenge: File ownership.** Currently `File.user_id` is a single user. With shared files, we need to decide:
- Option A: Keep `user_id` as the original uploader. Other users access via KnowledgeFile association. File deletion only if no KBs reference it.
- Option B: Add a `shared` or `system` flag to files that are managed by sync workers.

**Recommendation: Option A** with reference counting. When a user removes a file from their KB, only the KnowledgeFile record is deleted. The File record + vector data persist as long as any KnowledgeFile references remain. The `delete_file` parameter in `POST /knowledge/{id}/file/remove` should be `False` for shared external files.

**Implementation:**
- Modify `sync_worker.py:_process_file_info()`:
  - Before creating a new File, check `Files.get_file_by_id(file_id)`
  - If exists and hash matches: skip processing, just create KnowledgeFile + copy vectors
  - If exists and hash differs: re-process and update
  - If not exists: full processing pipeline
- Modify `knowledge.py` remove file endpoint:
  - For external-source files (detected by type or file ID prefix): set `delete_file=False`
  - Only delete the KnowledgeFile association and remove vectors from THIS KB's collection
  - Add cleanup: periodically check for orphaned files (no KnowledgeFile references)

#### 5. Upstream Permission Enforcement

**Rule:** If a user loses access to a OneDrive folder upstream, that folder's files should be removed from their KB during the next sync.

**Current behavior:** `_sync_permissions()` maps OneDrive folder permissions to KB `access_control`. This is being removed (see section 3).

**New behavior:**
- During sync, before processing files, check the user's access to each source folder
- Use `GraphClient.get_item(drive_id, folder_id)` to verify the user can still access the folder
- If the API returns 404 or 403: remove all files from that source from the user's KB
- Store the source-to-files mapping in `knowledge.meta.onedrive_sync.sources[].file_ids`

**Implementation:**
- Add `_verify_source_access()` to the sync worker
- Call before `_collect_folder_files()` for each source
- On access denied:
  - Remove all KnowledgeFile records for that source's files
  - Remove vectors from the KB collection
  - Remove the source from `knowledge.meta.onedrive_sync.sources`
  - Do NOT delete the underlying File records (other users may still reference them)
  - Emit a socket event so the frontend shows a notification

#### 6. File Count Limits

**Rule:** External-source KBs have a max of 250 files. If exceeded during sync, warn the user and ask them to select fewer files/smaller folders.

**Frontend enforcement:**
- After the file/folder picker returns items, estimate file count:
  - For individual files: count directly
  - For folders: either use Graph API to get folder children count, or warn that count will be verified during sync
- If estimated count > 250: show warning dialog before starting sync
- In the KB detail view: show current file count and limit

**Backend enforcement:**
- In `OneDriveSyncWorker.sync()`, after collecting all files to process:
  - If total exceeds `ONEDRIVE_MAX_FILES_PER_SYNC` (already exists, default 500, set to 250 in Helm values)
  - Set sync status to `"file_limit_exceeded"`
  - Emit socket event with the count so frontend can display the warning
  - Do NOT process any files (or process up to the limit with a warning)
- In `POST /knowledge/{id}/file/add` and batch add: check current file count + new files against limit for non-local KBs

**Configuration:**
- The limit already exists as `ONEDRIVE_MAX_FILES_PER_SYNC` (config.py:2551, default 500)
- Change default to 250 or add a separate `MAX_FILES_PER_EXTERNAL_KB` config

## Proposed Phases

### Phase 1: Data Model + Type-Aware Backend (Foundation)

**Goal:** Add the `type` column, enforce type-based rules in the backend, and ensure existing functionality doesn't break.

**Changes:**
1. Alembic migration: add `type` column to `knowledge` table (default `"local"`)
2. Data migration: set `type="onedrive"` where `meta` contains `onedrive_sync`
3. Update `KnowledgeModel`, `KnowledgeForm`, `KnowledgeResponse` Pydantic models
4. Update `POST /create`: accept `type`, force `access_control = {}` for non-local
5. Update `POST /{id}/update`: reject `access_control` changes for non-local KBs
6. Update list/search endpoints: add `type` filter parameter
7. Update `POST /{id}/file/remove`: set `delete_file=False` for external-source files

**Milestone:** Backend correctly creates typed KBs, enforces private-only for external types. All existing APIs continue working. Existing KBs migrated.

**No frontend changes** -- existing UI still works, just creates `"local"` type by default.

### Phase 2: Split Creation Flow + Type-Specific UI (Frontend)

**Goal:** Give users distinct creation paths for local vs OneDrive KBs, with appropriate UI for each type.

**Changes:**
1. Replace "New Knowledge" button with dropdown menu (options: "Local Knowledge Base", "From OneDrive")
2. Local flow: existing `CreateKnowledgeBase.svelte` (unchanged, passes `type="local"`)
3. OneDrive flow: new component or mode -- name/description → OneDrive picker → create KB + start sync (combines creation and initial sync into one guided flow)
4. KB detail page: hide sharing/access control UI for non-local KBs
5. KB detail page: show KB type badge ("Local" / "OneDrive")
6. KB list page: show type indicator on each card
7. Enforce 250-file limit in the OneDrive picker flow (frontend warning)

**Milestone:** Users can create separate local and OneDrive KBs through distinct flows. OneDrive KBs show no sharing options.

### Phase 3: Hash-Based Document Deduplication (Performance)

**Goal:** Embed each external-source document only once, regardless of how many users sync it.

**Changes:**
1. Modify `sync_worker._process_file_info()`: check for existing File record by deterministic ID before processing
2. If File exists with matching hash: skip processing, create KnowledgeFile + copy vectors only
3. If File exists with different hash: re-process, update File record, update all referencing KBs
4. Modify file removal: never delete underlying File for external-source files
5. Add orphan cleanup: background check for files with no KnowledgeFile references
6. Update re-sync to propagate changes to all KBs referencing updated files

**Milestone:** Two users syncing the same OneDrive folder results in documents being embedded only once. File changes propagate to all referencing KBs.

### Phase 4: Upstream Permission Enforcement (Security)

**Goal:** Automatically remove access when users lose upstream permissions.

**Changes:**
1. Add `_verify_source_access()` to sync worker
2. Before collecting files, verify user's access to each source folder via Graph API
3. On access denied: remove source's files from KB, emit notification
4. Track source-to-file mappings in KB metadata for efficient removal
5. Handle edge case: user regains access (re-add source, re-sync)

**Milestone:** When a user is removed from a OneDrive folder's permissions, their KB is automatically cleaned up on the next sync cycle.

## Architecture Insights

### Why Not Mixed KBs
Mixed KBs (local + OneDrive files in one KB) create several problems:
- Sharing rules conflict: local files can be shared, OneDrive files should respect upstream permissions
- Sync behavior is confusing: resync might re-download OneDrive files but not touch local files
- Delete semantics differ: local file deletion is permanent, OneDrive file deletion should preserve shared data
- Access control is ambiguous: who should see the KB if it contains both public local files and private OneDrive files?

Typed KBs cleanly separate these concerns.

### Document Sharing Architecture

```
User A's OneDrive KB          User B's OneDrive KB
(type="onedrive")             (type="onedrive")
  |                               |
  ├── KnowledgeFile(file_id="onedrive-abc")
  |        |                      ├── KnowledgeFile(file_id="onedrive-abc")
  |        |                      |        |
  |        └───────┬──────────────┘        |
  |                |                       |
  |         File(id="onedrive-abc")        |
  |         hash="sha256..."               |
  |                |                       |
  |    file-onedrive-abc (vector collection)
  |    (embedded once, shared)             |
  |                                        |
  ├── KB-A collection (vectors)  ├── KB-B collection (vectors)
  |   (copies of shared vectors) |   (copies of shared vectors)
```

Each KB still has its own vector collection for RAG queries. The per-file collection (`file-onedrive-abc`) and the File database record are shared. KnowledgeFile records provide the per-user, per-KB association.

### Relationship to Existing Plans

| Existing Plan | Relationship |
|---|---|
| Background sync / multi-datasource (Phase 1-5) | **Complementary.** That plan focuses on token refresh + scheduler for automated sync. This plan focuses on KB types + access rules + dedup. They can be implemented in parallel. |
| Cosmetic frontend changes (Phase 4: KB to sidebar) | **Dependency.** The dropdown "New Knowledge" button needs to work in the new sidebar location. Implement sidebar move first, then add the dropdown. |
| Multi-datasource abstraction (SyncProvider, etc.) | **Future alignment.** The `type` column maps directly to `SyncProvider.source_type`. When adding SharePoint, Confluence, etc., each gets a new `type` value and a registered SyncProvider. |

## Code References

- `backend/open_webui/models/knowledge.py:42-70` -- Knowledge model (no type column)
- `backend/open_webui/models/knowledge.py:90-108` -- KnowledgeFile join table
- `backend/open_webui/models/files.py:16-31` -- File model with hash column
- `backend/open_webui/routers/knowledge.py:159-194` -- Create endpoint
- `backend/open_webui/routers/knowledge.py:300-347` -- Update endpoint
- `backend/open_webui/routers/knowledge.py:548-643` -- Remove file endpoint
- `backend/open_webui/services/onedrive/sync_worker.py:298-394` -- Permission sync (to be removed)
- `backend/open_webui/services/onedrive/sync_worker.py:641-936` -- File processing pipeline
- `backend/open_webui/services/onedrive/sync_worker.py:688-696` -- Hash-based dedup (current)
- `backend/open_webui/utils/access_control.py:124-150` -- has_access() function
- `backend/open_webui/config.py:2551` -- ONEDRIVE_MAX_FILES_PER_SYNC
- `src/lib/components/workspace/Knowledge.svelte:140-148` -- "New Knowledge" button
- `src/lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte` -- Creation form
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1221-1230` -- Access control modal
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:529-571` -- OneDrive sync handler
- `src/lib/components/workspace/Knowledge/KnowledgeBase/AddContentMenu.svelte:102-112` -- OneDrive sync menu item

## Historical Context (from thoughts/)

- `thoughts/shared/plans/2026-02-04-background-sync-multi-datasource.md` -- Complementary plan for token refresh and scheduler
- `thoughts/shared/research/2026-02-04-background-sync-multi-datasource-architecture.md` -- Architecture research for multi-datasource sync
- `thoughts/shared/plans/2026-02-04-cosmetic-frontend-changes.md` -- Moving Knowledge to sidebar (prerequisite for new dropdown)
- `thoughts/shared/research/2026-02-04-TODO-onedrive-sync-cancel-pending-rollback.md` -- Sync cancellation and rollback behavior

## Open Questions

1. **Migration strategy for existing mixed KBs:** If any existing KBs contain both local and OneDrive files, should we split them into two KBs, or classify them based on majority file type? Current data suggests KBs are either all-local or all-OneDrive in practice.

2. **File count estimation for folders:** When a user picks a OneDrive folder in the picker, we don't know the exact file count until we enumerate recursively. Should we do a quick recursive count before starting sync, or start sync and abort if the limit is exceeded?

3. **Cross-user hash update propagation:** When user A's sync detects a changed OneDrive document, updating the shared File record and re-embedding is straightforward. But user B's KB collection also needs its vectors updated. Should this happen immediately (cascading update) or lazily (on next sync of user B's KB)?

4. **Orphan file cleanup timing:** How aggressively should we clean up File records with no KnowledgeFile references? Options: immediately on last reference removal, periodic background job, or manual admin action.

5. **Future source types and the type column:** Should `type` be a free-form string or an enum? Free-form is more extensible but harder to validate. The SyncProviderRegistry already uses free-form `source_type` strings, so matching that pattern seems reasonable.
