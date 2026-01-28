---
date: 2026-01-28T12:00:00+01:00
researcher: Claude
git_commit: 7ee9dcefcaef467484bdd79c18b8b3b95db5f2b5
branch: feat/data-control
repository: open-webui
topic: "Incomplete Data Deletion Cascade Analysis"
tags: [research, data-control, gdpr, deletion, orphaned-data, vector-db, storage]
status: complete
last_updated: 2026-01-28
last_updated_by: Claude
---

# Research: Incomplete Data Deletion Cascade Analysis

**Date**: 2026-01-28T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: 7ee9dcefcaef467484bdd79c18b8b3b95db5f2b5
**Branch**: feat/data-control
**Repository**: open-webui

## Research Question

Map the data control issues in this codebase, focusing on incomplete deletion of data across the three-layer storage architecture (Relational DB, File System, Vector DB).

## Summary

Open WebUI stores data across three independent layers that are not properly synchronized during deletion operations:

1. **Relational Database**: 26 tables with only 4 having CASCADE foreign keys
2. **File System**: Local disk or cloud storage (S3/GCS/Azure)
3. **Vector Database**: 10+ supported backends (ChromaDB default)

The analysis confirms **all 8 suspected issues** exist with varying severity. The most critical finding is that **user deletion (D5)** leaves 19 tables with orphaned records, plus vector collections and physical files. No automated retention policies, data export for individual users, or deletion audit trails exist.

---

## The Three-Layer Gap

| Layer | What's Stored | Delete Called? | Evidence |
|-------|---------------|----------------|----------|
| Relational DB | 26 tables (chat, file, user, memory, knowledge...) | Partial | Only 4 CASCADE FKs defined |
| File System | `/app/backend/data/uploads/` (or S3/GCS/Azure) | Often not | Chat/user deletion never calls `Storage.delete_file()` |
| Vector DB | Collections: `file-{id}`, `user-memory-{id}`, `{knowledge-id}` | Rarely | Only explicit file deletion cleans up vectors |

---

## Detailed Findings by Issue

### D1: Chat Deletion → Files/Vectors Orphaned (HIGH)

**Location**: `backend/open_webui/routers/chats.py:693-719`

**Problem Scope**:
When a chat is deleted via `DELETE /api/v1/chats/{id}`:

1. `Chat` record deleted from database
2. `ChatFile` junction records deleted via CASCADE (`chats.py:93`)
3. Tags cleaned up at router level (`chats.py:697-699`)

**NOT cleaned up**:
- `File` records in database (no FK to chat)
- Physical files in storage (`Storage.delete_file()` never called)
- Vector collections `file-{id}` remain in vector DB
- Feedback records with stale `meta.chat_id` references

**Code Evidence**:
```python
# chats.py:1193-1201 - Only DB delete, no cascade to files/vectors
def delete_chat_by_id(self, id: str) -> bool:
    with get_db() as db:
        db.query(Chat).filter_by(id=id).delete()
        db.commit()
        return True and self.delete_shared_chat_by_chat_id(id)
```

**Data at Risk**: Files uploaded to chat messages, their vector embeddings

---

### D2: File Deletion → DB Hash Remains (MEDIUM)

**Location**: `backend/open_webui/routers/files.py:852-890`

**Problem Scope**:
The file deletion endpoint `DELETE /api/v1/files/{id}` deletes:
1. Database record via `Files.delete_file_by_id(id)` - line 868
2. Physical storage via `Storage.delete_file(file.path)` - line 871
3. Vector collection via `VECTOR_DB_CLIENT.delete(collection_name=f"file-{id}")` - line 872

**Actual Issue**: The deletion order creates a race condition:
```python
# files.py:868-879
result = Files.delete_file_by_id(id)  # DB deleted FIRST
if result:
    try:
        Storage.delete_file(file.path)           # Then storage
        VECTOR_DB_CLIENT.delete(...)             # Then vectors
    except Exception as e:
        # DB record already gone, storage/vectors orphaned
```

If storage or vector deletion fails, the DB record is already deleted, leaving orphaned data with no reference to clean it up.

**Secondary Issue**: The `file.hash` field (SHA256 of content) is stored in `knowledge_file` junction table but not cleaned up when the file is removed from a knowledge base without deleting the file itself.

---

### D3: File Deletion → Vector Embeddings Persist (HIGH)

**Location**: `backend/open_webui/routers/files.py:872`

**Problem Scope**:
File deletion attempts to delete the `file-{id}` collection, but:

1. **Files in knowledge bases** are embedded into the **knowledge base collection** (`{knowledge-id}`), not `file-{id}`
2. When a file is deleted, only `file-{id}` collection is deleted
3. Embeddings in knowledge base collections persist with stale `file_id` metadata

**Code Evidence**:
```python
# knowledge.py:453 - Files added to knowledge use knowledge ID as collection
process_file(
    request,
    ProcessFileForm(file_id=form_data.file_id, collection_name=id),  # id = knowledge_id
    user=user,
)
```

```python
# files.py:872 - File deletion only deletes file-{id} collection
VECTOR_DB_CLIENT.delete(collection_name=f"file-{id}")  # Doesn't touch knowledge collection
```

**Data at Risk**: Vector embeddings in knowledge base collections

---

### D4: Knowledge Deletion → Vectors Persist (HIGH)

**Location**: `backend/open_webui/routers/knowledge.py:651-710`

**Problem Scope**:
Knowledge base deletion via `DELETE /api/v1/knowledge/{id}/delete`:

1. Updates models that reference the knowledge base (lines 677-701)
2. Deletes the knowledge collection in vector DB (line 705)
3. Deletes the knowledge record from database (line 709)

**NOT cleaned up**:
- Associated `File` records remain in database
- Physical files remain in storage
- Individual `file-{id}` collections may exist if files were processed standalone before adding to knowledge

**Code Evidence**:
```python
# knowledge.py:703-710
try:
    VECTOR_DB_CLIENT.delete_collection(collection_name=id)  # Only knowledge collection
except Exception as e:
    log.debug(e)
    pass
result = Knowledges.delete_knowledge_by_id(id=id)  # No file cleanup
```

**Contrast with proper cleanup** in `remove_file_from_knowledge_by_id` (`knowledge.py:548-643`) which:
- Removes vectors by file_id filter
- Optionally deletes the file record and its `file-{id}` collection

---

### D5: User Deletion → Everything Orphaned (CRITICAL)

**Location**: `backend/open_webui/models/users.py:628-645`

**Problem Scope**:
User deletion via `DELETE /api/v1/users/{user_id}`:

**What IS cleaned up**:
| Table | Method | Location |
|-------|--------|----------|
| `auth` | Direct delete | `auths.py:186` |
| `user` | Direct delete | `users.py:638` |
| `group_member` | Manual delete | `groups.py:356-358` |
| `chat` | Direct delete | `chats.py:1218` |
| `chat_file` | CASCADE from chat | FK constraint |

**What is NOT cleaned up** (19 tables with `user_id` column):

| Table | Model File | Risk |
|-------|------------|------|
| `api_key` | `users.py:122` | API keys become orphaned |
| `memory` | `memories.py:18` | Memories orphaned |
| `file` | `files.py:19` | Files orphaned |
| `folder` | `folders.py:26` | Folders orphaned |
| `knowledge` | `knowledge.py:46` | Knowledge bases orphaned |
| `channel` | `channels.py:37` | Channels orphaned |
| `channel_member` | `channels.py:96` | Channel memberships orphaned |
| `message` | `messages.py:45` | Channel messages orphaned |
| `prompt` | `prompts.py:22` | Prompts orphaned |
| `note` | `notes.py:30` | Notes orphaned |
| `tool` | `tools.py:26` | Tools orphaned |
| `function` | `functions.py:21` | Functions orphaned |
| `model` | `models.py:60` | Model configs orphaned |
| `feedback` | `feedbacks.py:23` | Feedback orphaned |
| `tag` | `tags.py:22` | Tags orphaned |
| `oauth_session` | `oauth_sessions.py:28` | OAuth sessions orphaned |
| `channel_file` | `channels.py:155` | Channel files orphaned |
| `channel_webhook` | `channels.py:191` | Webhooks orphaned |
| `message_reaction` | `messages.py:25` | Reactions orphaned |

**Vector collections NOT cleaned up**:
- `user-memory-{user_id}` - User's memory embeddings
- `file-{id}` for each user's file
- Knowledge base collections

**Physical files NOT cleaned up**:
- All files uploaded by the user remain in storage

**Code Evidence**:
```python
# users.py:628-645 - Only cleans groups and chats
def delete_user_by_id(self, id: str) -> bool:
    Groups.remove_user_from_all_groups(id)  # Only this
    result = Chats.delete_chats_by_user_id(id)  # And this
    # No Files, Memories, Knowledge, etc.
```

---

### D6: No Retention Policies (HIGH)

**Location**: N/A (feature does not exist)

**Problem Scope**:
The codebase has **no automated data retention or purge mechanisms**:

1. No scheduled cleanup jobs for expired data
2. No configurable retention periods
3. No automatic purge of old chats, files, or sessions
4. OAuth sessions have `expires_at` column but no cleanup job

**Existing Background Tasks** (none do retention):
- OneDrive sync scheduler (`services/onedrive/scheduler.py`) - sync only
- WebSocket usage pool cleanup (`socket/main.py:165-200`) - connection cleanup only
- Asyncio task cleanup (`tasks.py:80-93`) - memory cleanup only

**Configuration Gap**:
No environment variables for retention periods exist in `env.py` or `config.py`.

---

### D7: No Data Export - Article 20 (MEDIUM)

**Location**: Various export endpoints exist but are incomplete

**Problem Scope**:
GDPR Article 20 requires data portability - users must be able to export their personal data.

**What EXISTS**:
| Resource | Endpoint | Scope |
|----------|----------|-------|
| Chats | `GET /api/v1/chats/all` | User's chats as JSON |
| PDF export | `POST /api/v1/utils/pdf` | Single chat to PDF |
| DB download | `GET /api/v1/utils/db/download` | Admin only, SQLite only |

**What is MISSING**:
- No single endpoint to export ALL user data
- No export for: files, memories, knowledge bases, notes, prompts, tools
- No standardized export format (e.g., GDPR data package)
- File content not included in chat export (only references)

**Permission Control** (`config.py:1518-1521`):
- `USER_PERMISSIONS_CHAT_EXPORT`: Controls user export (default: True)
- No similar permissions for other data types

---

### D8: No Deletion Audit Trail (MEDIUM)

**Location**: `backend/open_webui/utils/audit.py`

**Problem Scope**:
Audit logging exists but is **disabled by default** and **incomplete for deletion tracking**:

**Configuration** (`env.py:777-805`):
```
AUDIT_LOG_LEVEL = NONE  # Disabled by default
AUDIT_EXCLUDED_PATHS = /chats,/chat,/folders  # Deletions excluded!
```

**Audit Levels**:
| Level | What's Logged |
|-------|---------------|
| `NONE` | Nothing (default) |
| `METADATA` | User, verb, URI, IP, user agent |
| `REQUEST` | Above + request body |
| `REQUEST_RESPONSE` | Above + response body |

**Critical Gap**: Even when enabled, `/chats` and `/folders` are in the default exclusion list, meaning chat deletions are never audited.

**What's NOT audited regardless of level**:
- Internal cascade deletions (file cleanup, vector cleanup)
- Background task deletions
- Database-level CASCADE deletes

---

## Code References

### Routers (API Layer)
- `backend/open_webui/routers/chats.py:693-719` - Chat deletion endpoint
- `backend/open_webui/routers/files.py:852-890` - File deletion endpoint
- `backend/open_webui/routers/users.py:579-611` - User deletion endpoint
- `backend/open_webui/routers/knowledge.py:651-710` - Knowledge deletion endpoint
- `backend/open_webui/routers/memories.py:200-210` - Memory deletion endpoint

### Models (Database Layer)
- `backend/open_webui/models/chats.py:87-102` - ChatFile junction table with CASCADE
- `backend/open_webui/models/users.py:628-645` - User deletion (incomplete cascade)
- `backend/open_webui/models/auths.py:179-193` - Auth deletion entry point
- `backend/open_webui/models/files.py:289` - File DB deletion

### Vector Database
- `backend/open_webui/retrieval/vector/factory.py:78` - VECTOR_DB_CLIENT singleton
- `backend/open_webui/retrieval/vector/main.py:23-86` - VectorDBBase interface
- Collection patterns: `file-{id}`, `user-memory-{user_id}`, `{knowledge_id}`

### Storage
- `backend/open_webui/storage/provider.py:41-58` - StorageProvider interface
- `backend/open_webui/storage/provider.py:80-87` - Local delete (silent on missing)
- `backend/open_webui/storage/provider.py:186-195` - S3 delete

### Audit
- `backend/open_webui/utils/audit.py:121-178` - AuditLoggingMiddleware
- `backend/open_webui/env.py:777-805` - Audit configuration

---

## Architecture Insights

### Foreign Key Relationships

Only 4 junction tables have CASCADE foreign keys:
1. `chat_file` → `chat.id`, `file.id`
2. `channel_file` → `channel.id`, `message.id`, `file.id`
3. `group_member` → `group.id`
4. `knowledge_file` → `knowledge.id`, `file.id`

**No table has a foreign key to `user.id`**. All `user_id` columns are plain text without constraints.

### Vector Collection Naming

| Entity Type | Collection Name | Created At | Deleted When |
|-------------|-----------------|------------|--------------|
| Standalone File | `file-{file_id}` | File processing | File deleted explicitly |
| Knowledge Base | `{knowledge_id}` | First file added | Knowledge deleted |
| User Memory | `user-memory-{user_id}` | First memory added | User deletes all memories |
| URL/Content | `{sha256_hash}` | Content processed | Never (no reference) |

### Storage Architecture

Files are stored with naming: `{uuid}_{original_filename}` in flat directory structure.

Cloud providers (S3, GCS, Azure) always maintain local cache in `UPLOAD_DIR` alongside remote storage, requiring dual cleanup on deletion.

---

## Open Questions

1. **Intentional design or oversight?** - Is the lack of cascade deletion intentional to preserve data for audit/compliance, or an oversight?

2. **Soft delete consideration** - Should some entities use soft delete (`deleted_at` timestamp) instead of hard delete for compliance?

3. **Vector DB orphan detection** - How to identify orphaned vector collections without scanning all collections?

4. **Multi-tenant considerations** - In multitenancy mode (Qdrant/Milvus), how does user deletion affect shared collections?

5. **Transaction boundaries** - Should DB/Storage/Vector operations be wrapped in distributed transactions?

---

## Issue Priority Matrix

| ID | Issue | Severity | Data Types Affected | Complexity |
|----|-------|----------|---------------------|------------|
| D5 | User deletion orphans everything | CRITICAL | 19 tables + vectors + files | High |
| D1 | Chat deletion orphans files/vectors | HIGH | files, vectors, feedback | Medium |
| D3 | File deletion leaves KB vectors | HIGH | vector embeddings | Medium |
| D4 | Knowledge deletion orphans files | HIGH | files, file vectors | Medium |
| D6 | No retention policies | HIGH | All data types | Medium |
| D2 | File deletion race condition | MEDIUM | storage, vectors | Low |
| D7 | No comprehensive data export | MEDIUM | All user data | Medium |
| D8 | No deletion audit trail | MEDIUM | Audit compliance | Low |
