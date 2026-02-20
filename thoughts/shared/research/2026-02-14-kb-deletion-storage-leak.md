---
date: 2026-02-14T12:00:00+01:00
researcher: claude
git_commit: 367bafc2907ce2ca9c9d8d3a783d09dafd8ba916
branch: feat/sync-improvements
repository: open-webui
topic: "KB deletion does not clean up files from S3/storage"
tags: [research, codebase, knowledge, files, storage, s3, deletion, bug]
status: complete
last_updated: 2026-02-14
last_updated_by: claude
---

# Research: KB Deletion Does Not Clean Up Files From S3/Storage

**Date**: 2026-02-14
**Researcher**: claude
**Git Commit**: 367bafc29
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

When a Knowledge Base is deleted, uploaded files remain in S3 storage. The file count keeps going up. Deleting a conversation with a single file attached does properly clean up S3. Where is the bug?

## Summary

**Root cause: The knowledge router's delete endpoint does not call `Storage.delete_file()`.** It only deletes the database record and vector collections, leaving the physical file in S3/local/GCS/Azure storage.

A `DeletionService` exists that does proper full cleanup (including `Storage.delete_file()`), and it is used by the single-chat deletion endpoint — which is why chat file deletion works. However, the KB deletion endpoint uses its own inline cleanup logic that omits the storage call.

## Detailed Findings

### The Bug: Missing `Storage.delete_file()` Call

**KB deletion endpoint** (`backend/open_webui/routers/knowledge.py:712-794`):

The orphaned file cleanup loop at lines 780-792 does:
1. Delete the file's vector collection (`file-{id}`) from vector DB
2. Delete the file's DB record via `Files.delete_file_by_id(file_id)`

But it does **NOT** call `Storage.delete_file(file.path)` — so the physical file remains in S3.

**Same bug in file removal from KB** (`knowledge.py:589-703`):

The `POST /{id}/file/remove` endpoint has the same issue at lines 666-678 (for local KBs with `delete_file=True`) and lines 681-693 (orphan cleanup for non-local KBs).

### Why Chat Deletion Works

**Single chat deletion** (`backend/open_webui/routers/chats.py:694-732`):

Calls `DeletionService.delete_chat(id, user.id)` which:
1. Gets all files via `ChatFile` junction table
2. Calls `DeletionService.delete_file()` for each file
3. `DeletionService.delete_file()` at `service.py:105-110` calls `Storage.delete_file(file.path)` — **this is the key difference**

### Where `Storage.delete_file()` IS Called

| Path | Called? | Location |
|------|---------|----------|
| `DELETE /chats/{id}` (single chat) | YES | via `DeletionService.delete_file()` → `service.py:107` |
| `DELETE /files/{id}` (standalone file) | YES | `files.py:871` |
| `DELETE /files/all` (all files) | YES | via `Storage.delete_all_files()` at `files.py:488` |
| `DeletionService.delete_user()` | YES | via `DeletionService.delete_knowledge()` → `DeletionService.delete_file()` |

### Where `Storage.delete_file()` is NOT Called (Bugs)

| Path | Location | Impact |
|------|----------|--------|
| `DELETE /knowledge/{id}/delete` | `knowledge.py:780-792` | **S3 files leaked on KB deletion** |
| `POST /knowledge/{id}/file/remove` | `knowledge.py:666-678` | **S3 files leaked on file removal from KB** |
| `POST /knowledge/{id}/file/remove` (orphan) | `knowledge.py:693` | **S3 files leaked on orphan cleanup** |
| `DELETE /chats/` (delete ALL chats) | `chats.py:212` | **S3 files leaked on bulk chat deletion** (calls `Chats.delete_chats_by_user_id()` directly, bypasses DeletionService) |
| OneDrive sync file removal | `sync_worker.py:526, 880` | **S3 files leaked during OneDrive sync updates** |

### The DeletionService Already Has the Right Logic

`DeletionService.delete_knowledge()` at `service.py:186-272` does proper cleanup:
- Delegates to `DeletionService.delete_file()` which calls `Storage.delete_file()`
- But this method is **only** called from `DeletionService.delete_user()` (line 357)
- The KB delete endpoint does NOT use it

## Suggested Fix

The simplest fix is to add `Storage.delete_file()` calls in the knowledge router's cleanup paths. For each orphaned file, before calling `Files.delete_file_by_id()`:

```python
# In knowledge.py orphan cleanup (lines 780-792):
for file_id in kb_file_ids:
    remaining_refs = Knowledges.get_knowledge_files_by_file_id(file_id)
    if not remaining_refs:
        file = Files.get_file_by_id(file_id)  # Need to get file.path first
        # ... existing vector cleanup ...
        if file and file.path:
            Storage.delete_file(file.path)  # <-- ADD THIS
        Files.delete_file_by_id(file_id)
```

Same pattern needed in:
- `knowledge.py:666-678` (file removal with `delete_file=True`)
- `knowledge.py:681-693` (orphan cleanup for non-local KBs)

Alternatively, refactor the KB deletion to use `DeletionService.delete_file()` instead of inline cleanup, which would be more maintainable and consistent.

## Code References

- `backend/open_webui/routers/knowledge.py:712-794` — KB delete endpoint (missing Storage call)
- `backend/open_webui/routers/knowledge.py:589-703` — File removal from KB (missing Storage call)
- `backend/open_webui/routers/knowledge.py:780-792` — Orphan file cleanup in KB delete
- `backend/open_webui/services/deletion/service.py:47-125` — DeletionService.delete_file() (correct implementation)
- `backend/open_webui/services/deletion/service.py:128-183` — DeletionService.delete_chat() (calls delete_file)
- `backend/open_webui/services/deletion/service.py:186-272` — DeletionService.delete_knowledge() (correct but unused by router)
- `backend/open_webui/routers/files.py:852-890` — Standalone file delete (correct)
- `backend/open_webui/routers/chats.py:694-732` — Chat delete (correct, uses DeletionService)
- `backend/open_webui/storage/provider.py:186-195` — S3StorageProvider.delete_file()
- `backend/open_webui/models/files.py:286-294` — Files.delete_file_by_id() (DB-only, no storage)

## Architecture Insights

- The `DeletionService` was designed to be the single source of truth for cascade deletions across all three layers (vectors, storage, DB)
- The knowledge router predates or was written separately from the DeletionService and uses inline cleanup logic
- This inconsistency creates storage leaks wherever the knowledge router handles file deletion
- The `Files.delete_file_by_id()` model method is misleadingly named — it only deletes the DB record, not the file itself

## Additional Bug: Bulk Chat Deletion

`DELETE /api/v1/chats/` at `chats.py:201-213` calls `Chats.delete_chats_by_user_id()` directly, bypassing the DeletionService entirely. This means deleting all chats also leaks files in storage.

## Open Questions

1. Should the fix add Storage calls inline, or refactor to use DeletionService consistently?
2. Are there orphaned files already in S3 that need a cleanup migration/script?
3. Should `Files.delete_file_by_id()` be updated to also call `Storage.delete_file()` to prevent this class of bug in the future? (This would be a bigger change with more risk.)
