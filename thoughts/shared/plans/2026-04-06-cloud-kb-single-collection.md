# Remove Per-File Weaviate Collections for Cloud-Synced KBs

## Overview

Cloud sync creates a separate Weaviate collection per file (`file-{file_id}`) in addition to the single KB collection (`knowledge_id`). For a KB with 1000 files, that's 1001 collections. This plan removes per-file collection creation for cloud-synced KBs, reducing to 1 collection per KB. The trade-off: individual file attachment to chat won't work for cloud KB files — acceptable given cloud KBs can have 1000+ files and are meant to be queried as a whole.

## Current State Analysis

### Dual-collection write (`base_worker.py:693-727`)
`_split_embed_and_store()` builds two identical item lists (`items_kb` and `items_file` — same text, vectors, metadata, only UUIDs differ) and inserts into both `knowledge_id` and `file-{file_id}` collections.

### Hash-match path (`base_worker.py:354-409`)
`_ensure_vectors_in_kb()` calls `process_file()` which queries `file-{file_id}` to get docs, then **re-embeds** them into the KB collection. The per-file collection only saves re-extraction here, not re-embedding.

### Query-time resolution
- `retrieval/utils.py:1068` — `file-{item_id}` for individual file chat attachment
- `tools/builtin.py:2075` — `file-{item_id}` for RAG tool file references

### Frontend file selection
- `Commands/Knowledge.svelte:119` — `#` command searches files cross-KB via `searchKnowledgeFiles()` (backend returns `collection.type`)
- `InputMenu/Knowledge.svelte:218-236` — `+` menu has chevron expand per KB (`selectedItem.type` available)

### Key Discoveries:
- `items_kb` and `items_file` have **identical metadata** — the only difference is UUID (`base_worker.py:693-711`)
- Hash-match path re-embeds anyway — per-file cache only avoids re-extraction, not re-embedding (`retrieval.py:1581-1589`)
- `search_knowledge_files()` already joins `Knowledge` table and returns `collection.type` on each file result (`models/knowledge.py:364-370`)
- Weaviate `query()` supports filtering by metadata properties like `file_id` (`weaviate.py:275-305`)

## Desired End State

- Cloud sync creates **1 Weaviate collection per KB** (the KB UUID collection), not N+1
- Hash-matched files check the KB collection directly instead of querying a per-file collection
- Existing per-file collections are gradually cleaned up during sync
- Individual file selection in chat is hidden for cloud KBs (backend filter + frontend guard)
- Local KBs are completely unaffected

### Verification:
- Sync a cloud KB → only 1 Weaviate collection created
- Re-sync (hash match) → old per-file collections cleaned up
- `#` command → cloud KB files don't appear
- `+` menu → cloud KBs have no expand chevron
- Attach cloud KB as whole collection → RAG queries work
- Local KB → no regression

## What We're NOT Doing

- Changing local KB behavior (separate code path via `process_file()` in `retrieval.py`)
- One-time migration script for existing per-file collections (gradual cleanup instead)
- Optimizing hash-match to skip re-embedding by copying vectors directly (separate optimization)
- Changing how `retrieval/utils.py` or `tools/builtin.py` resolve `file-{id}` — they'll just get empty results for cloud files, which is fine since the frontend won't offer that path

## Implementation Approach

Work backend-first (stop creating, update hash-match, filter search), then frontend (hide file selection). Each phase is independently deployable.

---

## Phase 1: Stop Creating Per-File Collections

### Overview
Remove the per-file collection insert from `_split_embed_and_store()`. This is the core change that eliminates N extra Weaviate collections per cloud KB.

### Changes Required:

#### 1. `base_worker.py` — Remove per-file insert and `items_file` construction

**File**: `backend/open_webui/services/sync/base_worker.py`

**Remove** the `items_file` list construction (lines 703-711):
```python
            items_file = [
                {
                    'id': str(uuid.uuid4()),
                    'text': text,
                    'vector': embeddings[idx],
                    'metadata': metadatas[idx],
                }
                for idx, text in enumerate(texts)
            ]
```

**Remove** the per-file collection insert block (lines 720-727):
```python
            # Insert into per-file collection (overwrite if exists)
            file_collection = f'file-{file_id}'
            log.info(f'[sync:{filename}] >>> WEAVIATE FILE INSERT START ({len(items_file)} vectors)')
            if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
            VECTOR_DB_CLIENT.insert(collection_name=file_collection, items=items_file)
            t_file = time.time()
            log.info(f'[sync:{filename}] <<< WEAVIATE FILE INSERT END ({t_file - t_kb:.1f}s)')
```

**Update** the timing log at line 735 to use `t_kb` instead of `t_file`:
```python
            log.info(f'[sync:{filename}] DONE total={t_kb - t0:.1f}s')
```

#### 2. `base_worker.py` — Update file record `collection_name`

**File**: `backend/open_webui/services/sync/base_worker.py`

**Change** line 731 from:
```python
Files.update_file_metadata_by_id(file_id, {'collection_name': file_collection}, db=session)
```
To:
```python
Files.update_file_metadata_by_id(file_id, {'collection_name': self.knowledge_id}, db=session)
```

**Change** line 743 (empty content fallback) from:
```python
Files.update_file_metadata_by_id(file_id, {'collection_name': f'file-{file_id}'}, db=session)
```
To:
```python
Files.update_file_metadata_by_id(file_id, {'collection_name': self.knowledge_id}, db=session)
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] No references to `file_collection` variable remain in `_split_embed_and_store`

#### Manual Verification:
- [ ] Sync a new cloud KB with a few files → verify only 1 Weaviate collection created (the KB UUID)
- [ ] Check file records have `collection_name` set to KB UUID, not `file-{id}`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 2: Update Hash-Match Path

### Overview
Replace the `process_file()` call in `_ensure_vectors_in_kb()` with a direct query to the KB collection filtered by `file_id`. If vectors are found, skip (they're already there). If not, fall through to re-extract + re-embed. Also add gradual cleanup of existing per-file collections.

### Changes Required:

#### 1. `base_worker.py` — Rewrite `_ensure_vectors_in_kb()`

**File**: `backend/open_webui/services/sync/base_worker.py`

Replace the entire `_ensure_vectors_in_kb` method (lines 354-409) with:

```python
    async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
        """Verify vectors for this file exist in the KB collection.

        Queries the KB collection filtered by file_id. If vectors are found,
        the file is already indexed — no work needed. If not found, returns a
        FailedFile so the orchestrator falls back to full re-processing.

        Also performs gradual cleanup: if a legacy per-file collection
        (file-{file_id}) exists, delete it.
        """
        try:
            def _check():
                log.info(f'[sync:ensure:{file_id}] >>> KB QUERY START')
                t0 = time.time()

                # Check if vectors already exist in KB collection
                result = VECTOR_DB_CLIENT.query(
                    collection_name=self.knowledge_id,
                    filter={'file_id': file_id},
                    limit=1,
                )

                has_vectors = (
                    result is not None
                    and len(result.ids) > 0
                    and len(result.ids[0]) > 0
                )

                # Gradual cleanup: remove legacy per-file collection if it exists
                file_collection = f'file-{file_id}'
                if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                    log.info(f'[sync:ensure:{file_id}] Cleaning up legacy per-file collection')
                    VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)

                log.info(
                    f'[sync:ensure:{file_id}] <<< KB QUERY END ({time.time() - t0:.1f}s) '
                    f'has_vectors={has_vectors}'
                )
                return has_vectors

            has_vectors = await asyncio.to_thread(_check)

            if has_vectors:
                return None  # Success — vectors already in KB collection

            # No vectors found — signal for re-processing
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Vectors not found in KB collection',
            )

        except Exception as e:
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f'Error checking vectors: {str(e)}'[:80],
            )
```

Key changes:
- Queries `self.knowledge_id` collection with `filter={'file_id': file_id}` + `limit=1` (just checking existence)
- If found → return `None` (success)
- If not found → return `FailedFile` so orchestrator falls back to `_process_and_embed()` (lines 1280-1282)
- Gradual cleanup: deletes `file-{file_id}` collection if it exists
- No longer imports `process_file` / `ProcessFileForm`
- The `EMPTY_CONTENT` special case is removed — if a file had no content, it won't have vectors in the KB collection, and the fallback to `_process_and_embed` will handle it correctly (it already handles empty content at line 740-745)

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors
- [x] No imports of `process_file` or `ProcessFileForm` remain in `_ensure_vectors_in_kb`

#### Manual Verification:
- [ ] Re-sync an already-synced cloud KB → hash-matched files succeed without re-embedding
- [ ] Any existing `file-{id}` collections for those files are deleted
- [ ] New files in the same sync are embedded correctly into the KB collection

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 3: Backend Filter for File Search

### Overview
Exclude cloud KB files from the `#` command file search by filtering on `Knowledge.type == 'local'` in the backend query.

### Changes Required:

#### 1. `models/knowledge.py` — Add type filter to `search_knowledge_files()`

**File**: `backend/open_webui/models/knowledge.py`

After the access control filter (line 342), add a type filter to only return files from local KBs:

```python
                # Only return files from local KBs — cloud KB files don't have
                # per-file vector collections and can't be attached individually
                query = query.filter(Knowledge.type == 'local')
```

Insert this between the access control filter block (ending line 342) and the filename search block (starting line 344).

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors

#### Manual Verification:
- [ ] `#` command search → cloud KB files don't appear in results
- [ ] `#` command search → local KB files still appear normally

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 4: Frontend — Hide Per-File Selection for Cloud KBs

### Overview
Hide the chevron expand button in the `+` menu for non-local KBs, so users can only select cloud KBs as whole collections.

### Changes Required:

#### 1. `InputMenu/Knowledge.svelte` — Conditionally show chevron

**File**: `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte`

Wrap the chevron button block (lines 218-236) in a conditional:

```svelte
{#if item.type === 'local' || !item.type}
    <Tooltip content={$i18n.t('Show Files')} placement="top">
        <!-- existing chevron button unchanged -->
    </Tooltip>
{/if}
```

This hides the "Show Files" chevron for `onedrive`, `google_drive`, and any future cloud types. The `|| !item.type` guard ensures backwards compatibility if `type` is undefined (treats as local).

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds without errors: `npm run build`

#### Manual Verification:
- [ ] `+` menu → local KBs have expand chevron, cloud KBs do not
- [ ] Cloud KBs can still be selected as whole collections
- [ ] Local KB file expansion still works normally

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Testing Strategy

### Manual Testing Steps:
1. **New cloud KB sync**: Create a new OneDrive/Google Drive KB, sync 5+ files → verify only 1 Weaviate collection
2. **Re-sync (hash match)**: Re-sync the same KB → hash-matched files succeed, any legacy per-file collections cleaned up
3. **Chat with cloud KB**: Attach the KB as a whole collection → verify RAG retrieval works
4. **`#` command**: Type `#` and search → cloud KB files hidden, local KB files visible
5. **`+` menu**: Open knowledge tab → cloud KBs have no expand chevron, local KBs do
6. **Local KB regression**: Create a local KB, add files → per-file collections still created, individual file attachment works

## Migration Notes

- **No database migration needed** — only behavior changes in sync worker and query filtering
- **Existing per-file collections**: Cleaned up gradually via Phase 2 (on next hash-match sync). Not harmful if they persist — just wasted Weaviate storage
- **File record `collection_name`**: Existing files will have `file-{id}` in metadata. New syncs will set it to the KB UUID. This is fine — the field is only used by `process_file()` which we no longer call from cloud sync

## References

- Research: `thoughts/shared/research/2026-04-06-cloud-kb-single-collection.md`
- `backend/open_webui/services/sync/base_worker.py:693-747` — dual-collection write (Phase 1)
- `backend/open_webui/services/sync/base_worker.py:354-409` — hash-match path (Phase 2)
- `backend/open_webui/models/knowledge.py:315-377` — file search query (Phase 3)
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:218-236` — chevron expand (Phase 4)
