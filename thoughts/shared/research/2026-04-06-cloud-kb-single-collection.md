---
date: 2026-04-06T14:14:39+02:00
researcher: Claude (Opus 4.6)
git_commit: 1d5f98208228917e7eabddfc21b7324f768f795f
branch: feat/vink
repository: open-webui
topic: "Remove per-file Weaviate collections for cloud-synced knowledge bases"
tags: [research, codebase, weaviate, collections, cloud-sync, knowledge-base, performance]
status: complete
last_updated: 2026-04-06
last_updated_by: Claude (Opus 4.6)
last_updated_note: "Resolved open questions: KB collection query for hash-match, gradual cleanup, backend filter"
---

# Research: Remove Per-File Weaviate Collections for Cloud-Synced KBs

**Date**: 2026-04-06T14:14:39+02:00
**Researcher**: Claude (Opus 4.6)
**Git Commit**: 1d5f98208228917e7eabddfc21b7324f768f795f
**Branch**: feat/vink
**Repository**: open-webui

## Research Question

Cloud sync creates a separate Weaviate collection per file (`file-{file_id}`), which becomes very heavy at 1000+ files. Can we remove per-file collections for cloud-synced KBs and use only the single KB collection? What frontend changes are needed since individual file attachment from cloud KBs would no longer work?

## Summary

The system already maintains a **single KB-level collection** (named by KB UUID) that aggregates all file vectors. The problem is that cloud sync **also** creates per-file collections (`file-{file_id}`) as a vector cache. For 1000 files, that's 1001 Weaviate collections. Removing the per-file collections for cloud KBs is straightforward — the main change is in `base_worker.py:_split_embed_and_store()`. The trade-off: individual file attachment to chat won't work for cloud KBs (the backend resolves `type: 'file'` items to `file-{id}` collections). Frontend needs to hide per-file selection for cloud KBs.

## Current Architecture

### Collection Creation in Cloud Sync

`base_worker.py:_split_embed_and_store()` writes vectors to **two** places:

1. **KB collection** (line 716): `VECTOR_DB_CLIENT.insert(collection_name=self.knowledge_id, items=items_kb)`
2. **Per-file collection** (lines 721-725):
   ```python
   file_collection = f'file-{file_id}'
   if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
       VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
   VECTOR_DB_CLIENT.insert(collection_name=file_collection, items=items_file)
   ```

Each `insert()` calls `WeaviateClient._ensure_collection()` → `_create_collection()` if absent.

### Why Per-File Collections Exist

They serve as a **vector cache** for two purposes:
1. **Cross-KB sharing**: When the same file is added to another KB, `process_file()` reads vectors from `file-{file_id}` instead of re-embedding (retrieval.py:1675)
2. **Individual file attachment**: When a user attaches a single file to chat, `get_sources_from_items()` queries `file-{item_id}` (retrieval/utils.py:1068)

### Query-Time Collection Resolution

In `retrieval/utils.py:get_sources_from_items()`:
- `type: 'collection'` → collection_name = KB UUID (line 1123)
- `type: 'file'` → collection_name = `file-{item_id}` (line 1068)

In `tools/builtin.py` (RAG tool):
- KB reference → `collection_names.append(item_id)` (line 2069)
- File reference → `collection_names.append(f'file-{item_id}')` (line 2075)

### Frontend File Selection

Two paths let users attach individual files from a KB:

1. **`#` command autocomplete** (`Commands/Knowledge.svelte`): `searchKnowledgeFiles()` returns individual files with `type: 'file'`
2. **`+` menu Knowledge tab** (`InputMenu/Knowledge.svelte`): Expandable KB list shows individual files; clicking one fires `onSelect({ type: 'file', ...file })`

Both paths exist for all KB types — no distinction between local and cloud KBs currently.

## Proposed Changes

### Backend Changes

#### 1. `base_worker.py:_split_embed_and_store()` — Skip per-file collection creation

Remove lines 721-725 (the per-file insert). This is the core fix. The KB collection insert at line 716 stays.

Also remove the per-file `items_file` list construction — currently vectors are prepared twice with different metadata (KB items get `source` set to the knowledge_id, file items get `source` set to the file path).

#### 2. `base_worker.py:_ensure_vectors_in_kb()` — Handle missing per-file collection

Line 369 calls `process_file()` which tries to read from `file-{file_id}` first. For cloud-synced files where we no longer create per-file collections, this will find nothing and fall through to re-loading from disk + re-embedding. This is fine for the hash-match path — the fallback already exists. No change needed here, but it becomes a slightly slower path (re-embeds instead of copying from cache).

**Alternative**: If we want to avoid re-embedding on hash matches, we could query the KB collection filtering by `file_id` metadata. But this is an optimization, not required.

#### 3. `retrieval/utils.py:get_sources_from_items()` — Handle missing per-file collection for cloud files

When a user somehow tries to attach an individual cloud file, the query to `file-{id}` will find no collection. Current behavior: `query_collection()` would fail or return empty. We should either:
- Let it fail gracefully (return empty sources) — already handled
- Or prevent this path entirely via frontend (preferred)

#### 4. `tools/builtin.py` — Same consideration for RAG tool file references

Line 2075 uses `file-{item_id}`. If the file is from a cloud KB, this collection won't exist. The query would return empty results. As long as the frontend doesn't offer individual file selection for cloud KBs, this path won't be triggered.

### Frontend Changes

#### 5. `Commands/Knowledge.svelte` — Filter out cloud KB files from search results

The `getKnowledgeFileItems()` call (line 119) returns files from all KBs. We need to either:
- **Option A**: Backend filter — `searchKnowledgeFiles()` endpoint excludes files belonging to cloud KBs (knowledge.type != 'local')
- **Option B**: Frontend filter — filter results by checking parent KB type

Option A is cleaner since it avoids sending unnecessary data.

#### 6. `InputMenu/Knowledge.svelte` — Hide file expand for cloud KBs

The chevron expand button (lines 219-236) should be hidden when `item.type !== 'local'`. Cloud KBs should only be selectable as a whole collection.

#### 7. Consider: KB detail view (`KnowledgeBase.svelte`)

The KB detail page shows individual files. For cloud KBs, the file list is informational (sync status). Individual file selection for chat context isn't done from here — it's done from the chat input. No change needed here.

### Migration / Cleanup

#### 8. Existing per-file collections

For already-synced cloud KBs, existing `file-{id}` collections will remain in Weaviate. Options:
- **Lazy cleanup**: Don't clean up — they're just wasted space but not harmful
- **Active cleanup**: Add a one-time migration/script to delete `file-{id}` collections for files belonging to cloud KBs
- **Gradual cleanup**: Next time the sync worker processes these files (hash match), it could delete the per-file collection if it exists

Option 3 (gradual) is simplest — add a cleanup step to the hash-match path.

## Code References

- `backend/open_webui/services/sync/base_worker.py:540-747` — `_embed_to_collections()` and `_split_embed_and_store()`, dual-collection write
- `backend/open_webui/services/sync/base_worker.py:716` — KB collection insert
- `backend/open_webui/services/sync/base_worker.py:721-725` — Per-file collection insert (to remove)
- `backend/open_webui/services/sync/base_worker.py:354-402` — `_ensure_vectors_in_kb()`, hash-match path
- `backend/open_webui/routers/retrieval.py:1622-1943` — `process_file()`, reads from per-file collection
- `backend/open_webui/routers/retrieval.py:1675` — Query existing per-file collection
- `backend/open_webui/retrieval/utils.py:926-1183` — `get_sources_from_items()`, collection name resolution
- `backend/open_webui/retrieval/utils.py:1068` — `file-{id}` collection name for individual files
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:131-169` — Collection creation
- `backend/open_webui/tools/builtin.py:2046-2143` — RAG tool collection resolution
- `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte:119` — File search in # command
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:219-236` — File expand in + menu

## Architecture Insights

1. The KB-level collection already aggregates all file vectors — removing per-file collections doesn't lose query capability for whole-KB retrieval
2. Per-file collections are a **caching optimization** (avoid re-embedding), not a structural requirement
3. The only functional loss is individual file attachment from cloud KBs — which is a reasonable trade-off given that cloud KBs can have 1000+ files
4. Local KBs are unaffected — they go through `process_file()` in `retrieval.py` which creates per-file collections via a different code path

## Impact Assessment

| Aspect | Impact |
|--------|--------|
| Weaviate collections | Reduced from N+1 to 1 per cloud KB |
| Sync performance | Faster — fewer collection create/delete operations |
| Re-embedding on hash match | Slightly slower if per-file cache is gone (but can filter KB collection by file_id as alternative) |
| Individual file chat attachment | Not available for cloud KBs (acceptable trade-off) |
| Local KBs | No impact — separate code path |
| Existing data | Needs cleanup strategy for existing per-file collections |

## Open Questions

All resolved — see decisions below.

## Decisions

1. **Hash-match optimization: Query KB collection by `file_id` metadata.** Instead of re-embedding when the per-file collection is missing, query the KB collection filtered by `file_id` to retrieve existing vectors. This preserves the performance benefit of avoiding re-embedding without needing per-file collections.

2. **Cleanup strategy: Gradual.** On next sync when processing a hash-matched file, delete the per-file collection if it exists. No one-time migration script needed — existing per-file collections will be cleaned up naturally as syncs run.

3. **File search filtering: Backend filter.** The `searchKnowledgeFiles()` endpoint should exclude files belonging to non-local KBs. This is cleaner than frontend filtering — avoids sending unnecessary data and keeps the logic in one place.

## Implementation Plan

### Phase 1: Backend — Stop creating per-file collections

**1a. `base_worker.py:_split_embed_and_store()`** — Remove per-file collection insert (lines 721-725) and the `items_file` list construction. Keep only the KB collection insert at line 716.

**1b. `base_worker.py:_ensure_vectors_in_kb()`** — Replace the `process_file()` call (which reads from `file-{id}`) with a direct query to the KB collection filtered by `file_id` metadata. Reconstruct `Document` objects from the results and insert them back (for cross-KB scenarios). If KB collection query returns nothing, fall through to re-load + re-embed as today.

**1c. `base_worker.py` hash-match path** — After successfully processing a hash-matched file, check if `file-{file_id}` collection exists and delete it (gradual cleanup).

### Phase 2: Backend — Filter cloud files from search

**2a. `models/knowledge.py:search_knowledge_files()`** — Add a join/filter to exclude files belonging to KBs where `knowledge.type != 'local'`. This affects the `GET /api/v1/knowledge/search/files` endpoint used by the `#` command.

**2b. `routers/knowledge.py` file list endpoint** — The `GET /api/v1/knowledge/{id}/files` endpoint used by InputMenu expand doesn't need filtering (we'll hide the expand button in frontend), but could optionally add a flag.

### Phase 3: Frontend — Hide individual file selection for cloud KBs

**3a. `InputMenu/Knowledge.svelte:219-236`** — Conditionally hide the chevron expand button when `item.type !== 'local'`. Cloud KBs show as whole-collection items only.

**3b. `Commands/Knowledge.svelte`** — No frontend change needed here since the backend filter (2a) will exclude cloud KB files from search results.

### Phase 4: Verification

- Sync a cloud KB → verify only 1 Weaviate collection created (KB UUID)
- Re-sync (hash match) → verify gradual cleanup deletes old per-file collections
- `#` command search → verify cloud KB files don't appear in results
- `+` menu → verify cloud KBs have no expand button
- Attach cloud KB as whole collection → verify RAG queries work
- Local KB → verify no regression (per-file collections still created)
