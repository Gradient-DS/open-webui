---
date: 2026-03-24T15:00:00+01:00
researcher: claude
git_commit: 5c27677b2e693f3e687af1496be5a4608886569d
branch: fix/test-bugs-daan-260323
repository: open-webui
topic: "KB and chat file deletion mechanism after upstream 0.8.9 merge"
tags: [research, codebase, knowledge, deletion, cleanup, vectors, files, chat, upstream-merge]
status: complete
last_updated: 2026-03-24
last_updated_by: claude
---

# Research: KB and Chat File Deletion After Upstream 0.8.9 Merge

**Date**: 2026-03-24
**Researcher**: claude
**Git Commit**: 5c27677b2
**Branch**: fix/test-bugs-daan-260323
**Repository**: open-webui

## Research Question

1. Did we keep our original KB deletion logic that cleans up all vectors and files after the upstream 0.8.9 merge?
2. Are documents attached to conversations cleaned up when conversations are deleted?

## Summary

**KB deletion: Our logic is preserved and working correctly.** The merge produced a hybrid that combines upstream's metadata embedding removal (synchronous) with our soft-delete + cleanup worker pattern (asynchronous). All vectors, files, model references, and DB records are cleaned up via the `DeletionService` + `cleanup_worker`.

**Chat file deletion: Broken — files and vectors are NOT cleaned up when chats are deleted.** The standard `DELETE /api/v1/chats/{id}` endpoint only removes the chat, messages, and junction rows. It never calls `DeletionService` and never triggers the soft-delete/cleanup-worker pipeline. The full cleanup path only activates during user deletion (admin action), not normal chat deletion.

## Detailed Findings

### 1. KB Deletion — Before vs After Merge

#### Before merge (our code)
- Route called `Knowledges.soft_delete_by_id(id)` — sets `deleted_at` timestamp
- No inline vector/file cleanup
- Cleanup worker picks up soft-deleted KBs and calls `DeletionService.delete_knowledge()`

#### Upstream 0.8.9
- Route did a **hard delete** with inline cleanup:
  - Iterates all models, removes KB references from `model.meta.knowledge`
  - Calls `VECTOR_DB_CLIENT.delete_collection(collection_name=id)`
  - Calls `remove_knowledge_base_metadata_embedding(id)` (new function)
  - Calls `Knowledges.delete_knowledge_by_id(id)` (SQL DELETE)

#### After merge (current state)
- Route calls `remove_knowledge_base_metadata_embedding(id)` (from upstream) — **synchronous**
- Route calls `Knowledges.soft_delete_by_id(id)` (from our branch) — **synchronous**
- Cleanup worker (0–60s later) calls `DeletionService.delete_knowledge()` which does:
  - `VECTOR_DB_CLIENT.delete_collection(knowledge_id)` — deletes KB vector collection
  - Scans all models, removes KB references from `model.meta.knowledge`
  - `Knowledges.delete_knowledge_by_id()` — revokes access grants, hard-deletes DB row
  - `DeletionService.delete_orphaned_files_batch()` — checks if files are still referenced by other KBs or chats; deletes orphaned files' vectors, storage, and DB records

**Verdict: Complete cleanup. Our soft-delete + worker pattern handles everything upstream did inline, plus orphaned file cleanup.**

### 2. Chat File Deletion — Gap Found

#### How files attach to chats
- `ChatFile` junction table (`models/chats.py:91-106`) with FK cascade on both `chat_id` and `file_id`
- Files inserted via `Chats.insert_chat_files()` during message processing (`main.py:2035-2052`)

#### Standard chat deletion (DELETE /api/v1/chats/{id})
**Route at `routers/chats.py:1107-1148`:**
1. Cleans up orphan tags
2. Calls `Chats.delete_chat_by_id()` or `delete_chat_by_id_and_user_id()`
3. **That's it** — no file cleanup, no vector cleanup, no storage cleanup

The `delete_chat_by_id` model method (`models/chats.py:1519-1528`) deletes `ChatMessage` rows, the `Chat` row, and the shared copy. `ChatFile` rows cascade-delete via FK. But `File` records, `file-{id}` vector collections, and physical storage files all remain.

#### What SHOULD happen (exists but isn't wired up)
- `DeletionService.delete_chat()` (`services/deletion/service.py:224-283`) does full cascade:
  - Gets files via `Chats.get_files_by_chat_id()`
  - Calls `DeletionService.delete_file()` for each — deletes vectors from KB collections, `file-{id}` collection, storage, DB record
  - Cleans up tags and chat records
- **But this method is never called from any route or worker.**

#### Cleanup worker path (only for user deletion)
- `_process_pending_chat_deletions()` (`cleanup_worker.py:111-163`) handles soft-deleted chats
- But `Chats.soft_delete_by_id()` is only called from `DeletionService.delete_user()` (user deletion), never from chat deletion endpoints
- Normal chat deletion uses hard delete directly, bypassing the soft-delete → worker pipeline entirely

#### DELETE all chats (DELETE /api/v1/chats/)
Same problem — `routers/chats.py:530-546` calls `Chats.delete_chats_by_user_id()` with no file/vector cleanup.

## Architecture: Deletion Pipeline

```
KB DELETION (working correctly):
  DELETE /api/v1/knowledge/{id}/delete
    → remove_knowledge_base_metadata_embedding(id)     [sync]
    → Knowledges.soft_delete_by_id(id)                  [sync]
    ... 0-60s ...
    → cleanup_worker._process_pending_kb_deletions()
       → collect file IDs from knowledge_file junction
       → DeletionService.delete_knowledge(kb.id)
          → delete KB vector collection
          → remove KB refs from all models
          → hard-delete KB record + access grants
       → DeletionService.delete_orphaned_files_batch()
          → check KB + chat references
          → delete orphaned file vector collections  [parallel, 10 workers]
          → delete orphaned storage files
          → delete orphaned file DB records

CHAT DELETION (broken):
  DELETE /api/v1/chats/{id}
    → delete orphan tags
    → Chats.delete_chat_by_id()
       → DELETE ChatMessage rows
       → DELETE Chat row (ChatFile rows cascade)
    [File records, vectors, storage: LEAKED]
```

## Code References

- `backend/open_webui/routers/knowledge.py:1053-1089` — KB delete endpoint (soft-delete + metadata embedding)
- `backend/open_webui/routers/knowledge.py:92-102` — `remove_knowledge_base_metadata_embedding()`
- `backend/open_webui/models/knowledge.py:834-844` — `soft_delete_by_id()`
- `backend/open_webui/models/knowledge.py:799-807` — `delete_knowledge_by_id()` (hard delete)
- `backend/open_webui/models/knowledge.py:822-832` — `get_pending_deletions()`
- `backend/open_webui/services/deletion/service.py:286-376` — `DeletionService.delete_knowledge()`
- `backend/open_webui/services/deletion/service.py:136-222` — `delete_orphaned_files_batch()`
- `backend/open_webui/services/deletion/service.py:224-283` — `DeletionService.delete_chat()` (exists but UNUSED)
- `backend/open_webui/services/deletion/cleanup_worker.py:67-108` — KB cleanup worker
- `backend/open_webui/services/deletion/cleanup_worker.py:111-163` — Chat cleanup worker (only triggered by user deletion)
- `backend/open_webui/routers/chats.py:1107-1148` — Chat delete endpoint (no file cleanup)
- `backend/open_webui/routers/chats.py:530-546` — Delete all chats (no file cleanup)
- `backend/open_webui/models/chats.py:1519-1528` — `delete_chat_by_id()` (hard delete, no file cleanup)

## Historical Context

- `thoughts/shared/research/2026-02-14-kb-deletion-storage-leak.md` — Original discovery of KB deletion storage leak
- `thoughts/shared/research/2026-02-19-kb-deletion-crash-and-storage-leak-status.md` — Status check showing partial fix (KB fixed, OneDrive + bulk chat still leaking)
- Merge commit: `c26ae48d6` (merge: upstream version 0.8.9 260320), via PR #38

## Open Questions

1. **Should chat deletion trigger soft-delete + cleanup worker?** The infrastructure exists (`DeletionService.delete_chat()`, `_process_pending_chat_deletions()`), but the chat delete endpoints use hard delete directly. Changing to soft-delete would enable full cleanup but is a behavioral change.
2. **How much orphaned data has accumulated?** Files uploaded to chats and never manually deleted will have leaked vectors and storage. May need a one-time cleanup migration.
3. **Should `DeletionService.delete_chat()` be called directly from the route?** This would be simpler than adding soft-delete, but risks slower delete responses if many files are attached.
