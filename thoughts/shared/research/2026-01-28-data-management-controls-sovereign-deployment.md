---
date: 2026-01-28T12:00:00+01:00
researcher: Claude
git_commit: c3c55f82ba05b7e0a5eef93a2b91ee58c7a6349d
branch: feat/data-control
repository: open-webui
topic: "Data Management Controls for Government/Sovereign Deployment"
tags: [research, codebase, data-control, admin, governance, compliance, gdpr, sovereign]
status: complete
last_updated: 2026-01-28
last_updated_by: Claude
---

# Research: Data Management Controls for Government/Sovereign Deployment

**Date**: 2026-01-28T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: c3c55f82ba05b7e0a5eef93a2b91ee58c7a6349d
**Branch**: feat/data-control
**Repository**: open-webui

## Research Question

What data management controls exist at user, group, and admin levels in Open WebUI? What is missing for a fully sovereign/government deployment solution? Considerations include:
- Ability to save data of users who get their account removed
- Ability to export all data including files and vectors
- Standard data operations and controls
- Knowledge base deletion impact on referencing chats

## Summary

Open WebUI has a solid foundation for data management with cascade deletion, group-based access control, and audit logging. However, several critical gaps exist for government/sovereign deployments:

| Category | Current State | Gap for Sovereign Deployment |
|----------|---------------|------------------------------|
| User Data Archival | ❌ Not implemented | Need to preserve data before account deletion |
| Complete Data Export | ⚠️ Partial (chats only) | Need vectors, files, full user data export |
| Data Retention Policies | ❌ Not implemented | Need time-based expiration, quotas |
| KB Deletion Impact | ⚠️ Orphan references | Need chat notification/cleanup |
| Audit/Compliance | ⚠️ Basic logging | Need compliance dashboards, retention |
| GDPR Tools | ❌ Not implemented | Need erasure verification, data portability |

## Detailed Findings

### 1. User-Level Data Controls

**Entry Points:**
- `backend/open_webui/routers/users.py` - User settings and profile
- `backend/open_webui/routers/chats.py` - Chat operations
- `src/lib/components/chat/Settings/DataControls.svelte` - UI

#### Available Controls

| Feature | Endpoint | Permission |
|---------|----------|------------|
| Export all chats | `GET /chats/all` | `chat.export` |
| Import chats | `POST /chats/import` | - |
| Delete single chat | `DELETE /chats/{id}` | `chat.delete` |
| Delete all chats | `DELETE /chats/` | `chat.delete` |
| Archive chat | `POST /chats/{id}/archive` | - |
| Archive all chats | `POST /chats/archive/all` | - |
| Memory toggle | User settings | - |
| Clear all memories | `DELETE /memories/delete/user` | - |
| API key management | `/auths/api_key` | `features.api_keys` |

#### Chat Export Format
```json
{
  "id": "chat-uuid",
  "title": "Chat Title",
  "chat": {
    "messages": [...],
    "models": [...],
    "tags": [...]
  },
  "created_at": 1234567890,
  "updated_at": 1234567890
}
```

**Gap:** Export does not include:
- Associated files (only references)
- Vector embeddings
- File contents

### 2. Admin-Level Data Controls

**Entry Points:**
- `backend/open_webui/routers/configs.py` - Configuration
- `backend/open_webui/routers/utils.py` - Database download
- `backend/open_webui/services/deletion/service.py` - Cascade deletion

#### Available Controls

| Feature | Endpoint | Notes |
|---------|----------|-------|
| Export config | `GET /configs/export` | All settings as JSON |
| Import config | `POST /configs/import` | Restore settings |
| Download database | `GET /utils/db/download` | **SQLite only**, requires `ENABLE_ADMIN_EXPORT=True` |
| Export all chats | `GET /chats/all/db` | Requires `ENABLE_ADMIN_EXPORT=True` |
| Export users CSV | Frontend only | id, name, email, role |
| Delete user | `DELETE /users/{user_id}` | Full cascade deletion |
| Access any chat | Requires `ENABLE_ADMIN_CHAT_ACCESS=True` | |
| Reset vector DB | `POST /retrieval/reset` | Clears all vectors |
| Reset upload directory | `DELETE /files/all` | Deletes all files |

#### Configuration Flags (`env.py`)
```python
ENABLE_ADMIN_EXPORT = os.environ.get("ENABLE_ADMIN_EXPORT", "True")
ENABLE_ADMIN_CHAT_ACCESS = os.environ.get("ENABLE_ADMIN_CHAT_ACCESS", "True")
BYPASS_ADMIN_ACCESS_CONTROL = os.environ.get("BYPASS_ADMIN_ACCESS_CONTROL", "True")
```

**Gap:** No PostgreSQL database export capability - only SQLite download exists.

### 3. Group-Level Data Controls

**Entry Points:**
- `backend/open_webui/routers/groups.py` - Group CRUD
- `backend/open_webui/utils/access_control.py` - Permission checking
- `backend/open_webui/utils/db/access_control.py` - SQL-level filtering

#### Permission Structure (`config.py:1482-1540`)
```python
DEFAULT_USER_PERMISSIONS = {
    "workspace": {
        "models": True,
        "knowledge": True,
        "prompts": True,
        "tools": True,
        "models_import": True, "models_export": True,
        "prompts_import": True, "prompts_export": True,
        "tools_import": True, "tools_export": True,
    },
    "sharing": {
        "models": True, "public_models": False,
        "knowledge": True, "public_knowledge": False,
        "prompts": True, "public_prompts": False,
        "tools": True, "public_tools": False,
        "notes": True, "public_notes": False,
    },
    "chat": {
        "file_upload": True, "delete": True, "edit": True,
        "share": True, "export": True, "temporary": True,
        "temporary_enforced": False,  # Force all chats to be temporary
    },
    "features": {
        "api_keys": False, "web_search": True,
        "image_generation": True, "code_interpreter": True,
    },
}
```

#### Access Control Schema
Resources (knowledge, tools, prompts, models, notes, channels) support:
- `None` - Public (read-only to all users)
- `{}` - Private (owner only)
- Custom: `{"read": {"group_ids": [...], "user_ids": [...]}, "write": {...}}`

#### Permission Aggregation
Multiple group memberships use **most permissive** logic - if any group grants permission, it's granted.

### 4. Cascade Deletion Implementation

**Entry Point:** `backend/open_webui/services/deletion/service.py`

#### Deletion Order
```
Vectors → Storage → Database
```
This ensures DB references remain if vector/storage cleanup fails, enabling retry.

#### User Deletion Cascade (in order)

| Step | Data | Method |
|------|------|--------|
| 1 | Memories | Vector collection + DB |
| 2 | Knowledge bases | Vectors + optionally files |
| 3 | Standalone files | Vectors + storage + DB |
| 4 | Chats | DB (FK cascades chat_file) |
| 5 | Messages | DB |
| 6 | Channel memberships | DB |
| 7 | User-owned channels | DB |
| 8 | Tags | DB |
| 9 | Folders | DB |
| 10 | Prompts | DB |
| 11 | Tools | DB |
| 12 | Functions | DB |
| 13 | Models (custom) | DB |
| 14 | Feedbacks | DB |
| 15 | Notes | DB |
| 16 | OAuth sessions | DB |
| 17 | Group memberships | DB |
| 18 | User-owned groups | DB |
| 19 | API keys | DB |
| 20 | Auth + User records | DB |

#### Knowledge Base Deletion
```python
# Current behavior in knowledge.py:677-679
report = DeletionService.delete_knowledge(id, delete_files=False)
# Files are preserved by default
```

**Gap:** When a knowledge base is deleted:
- Vector collection is deleted
- Model references are cleaned up
- **Chats referencing the KB retain orphan references** (no cleanup or notification)

### 5. Database and Storage Support

#### Database Backends
| Type | Support | Export |
|------|---------|--------|
| SQLite | ✅ Full | ✅ File download |
| PostgreSQL | ✅ Full | ❌ No export |
| SQLCipher | ✅ Full | ⚠️ Encrypted file |

#### Storage Providers (`storage/provider.py`)
- Local filesystem
- Amazon S3
- Google Cloud Storage
- Azure Blob Storage

#### Vector Database Adapters (`retrieval/vector/`)
- ChromaDB (default)
- **Weaviate** ✅
- **pgvector** ✅
- Qdrant
- Milvus
- Pinecone
- Elasticsearch
- OpenSearch
- Oracle23AI
- S3Vector

### 6. Audit Logging

**Entry Point:** `backend/open_webui/utils/audit.py`

#### Configuration
```python
AUDIT_LOG_LEVEL = "NONE"  # NONE, METADATA, REQUEST, REQUEST_RESPONSE
AUDIT_LOGS_FILE_PATH = f"{DATA_DIR}/audit.log"
AUDIT_LOG_FILE_ROTATION_SIZE = "10MB"
AUDIT_EXCLUDED_PATHS = "/chats,/chat,/folders"
```

#### Log Entry Structure
```json
{
  "id": "uuid",
  "timestamp": 1234567890,
  "user": {"id": "...", "name": "...", "email": "...", "role": "..."},
  "audit_level": "REQUEST_RESPONSE",
  "verb": "POST",
  "request_uri": "https://...",
  "response_status_code": 200,
  "source_ip": "...",
  "user_agent": "...",
  "request_object": "...",
  "response_object": "..."
}
```

#### Limitations
- No structured query interface for audit logs
- No retention policy for audit logs
- No compliance reporting dashboard
- Password redaction exists but limited PII handling

## What's Missing for Sovereign Deployment

### 1. User Data Archival (Critical)

**Current:** When a user is deleted, all data is permanently removed.

**Needed:**
```python
# Proposed endpoint
POST /admin/users/{user_id}/archive
{
    "archive_chats": true,
    "archive_files": true,
    "archive_knowledge_bases": true,
    "archive_to": "s3://bucket/archives/{user_id}/",
    "delete_after_archive": false
}
```

**Use case:** Government employee changes role or leaves organization - data must be preserved for compliance but account access revoked.

### 2. Complete Data Export (Critical)

**Current gaps:**
- No vector embedding export
- No file content export (only references)
- No PostgreSQL database export
- No unified "export everything" function

**Needed:**
```python
# Proposed endpoint
GET /admin/users/{user_id}/export
Response: {
    "user": {...},
    "chats": [...],
    "files": [
        {"id": "...", "content_url": "presigned-url", "embeddings": [...]}
    ],
    "knowledge_bases": [
        {"id": "...", "vectors": [...]}
    ],
    "memories": [...],
    "settings": {...}
}
```

### 3. Data Retention Policies (High)

**Current:** No automatic data expiration or quotas.

**Needed:**
- Time-based chat expiration (e.g., delete after 90 days)
- Storage quotas per user/group
- Automatic archival rules
- Data lifecycle policies

```python
# Proposed config
DATA_RETENTION_POLICY = {
    "chats": {"max_age_days": 365, "action": "archive"},
    "files": {"max_size_mb": 1000, "action": "warn"},
    "memories": {"max_age_days": 180, "action": "delete"},
}
```

### 4. Knowledge Base Deletion Impact (Medium)

**Current:** Chats retain orphan references to deleted KBs.

**Needed options:**
1. **Soft delete** - Mark KB as deleted, chats still display (read-only)
2. **Reference cleanup** - Update chat metadata to remove KB references
3. **Notification** - Alert chat owners that referenced KB was deleted
4. **Block deletion** - Prevent KB deletion while chats reference it

### 5. GDPR/Compliance Tools (High)

**Current:** Basic audit logging, no compliance tools.

**Needed:**
- Data Subject Access Request (DSAR) endpoint
- Right to erasure verification (prove data is gone)
- Data portability export (machine-readable format)
- Consent tracking
- PII detection and classification

```python
# Proposed endpoints
GET /compliance/dsar/{user_id}        # All data for user
POST /compliance/erasure/{user_id}    # Delete + verify
GET /compliance/erasure/verify/{id}   # Verify deletion complete
```

### 6. Multi-Tenant Isolation (Medium)

**Current:** Group-based access control on same database.

**Needed for high-security environments:**
- Database-level tenant isolation
- Vector DB collection isolation per tenant
- Storage bucket isolation per tenant
- Network isolation options

### 7. Data Classification (Low-Medium)

**Current:** No sensitivity labels.

**Needed:**
- Sensitivity labels on chats/files (public, internal, confidential, restricted)
- Label-based access policies
- Label inheritance from KB to chats
- DLP (Data Loss Prevention) integration hooks

### 8. Export Verification (Low)

**Current:** No checksums or verification.

**Needed:**
- Export integrity verification (SHA-256 checksums)
- Import validation
- Export audit trail

## Code References

| File | Line | Description |
|------|------|-------------|
| `backend/open_webui/services/deletion/service.py` | 39-507 | DeletionService class |
| `backend/open_webui/routers/users.py` | 580-614 | User deletion endpoint |
| `backend/open_webui/routers/knowledge.py` | 652-683 | KB deletion endpoint |
| `backend/open_webui/routers/chats.py` | 426-433 | Export all chats (admin) |
| `backend/open_webui/routers/utils.py` | 106-124 | Database download |
| `backend/open_webui/utils/audit.py` | 121-283 | Audit logging middleware |
| `backend/open_webui/config.py` | 1482-1540 | Default user permissions |
| `backend/open_webui/utils/access_control.py` | 28-68 | Permission aggregation |

## Architecture Insights

### Current Data Flow
```
User Action → API Router → Permission Check → Model Operation → DB
                                ↓
                         DeletionService (for deletes)
                                ↓
                    Vectors → Storage → Database
```

### Recommended Architecture for Sovereign Deployment
```
User Action → API Router → Permission Check → Policy Engine → Model Operation
                                ↓                    ↓
                         Audit Logger         Retention Check
                                ↓                    ↓
                    Archive Service ←→ DeletionService
                                ↓
                    Vectors → Storage → Database
                                ↓
                    Compliance Reporter
```

## Recommended Implementation Priority

1. **Phase 1 (Critical):**
   - User data archival before deletion
   - Complete data export (including vectors)
   - PostgreSQL backup support

2. **Phase 2 (High):**
   - Data retention policies
   - GDPR compliance endpoints
   - KB deletion impact handling

3. **Phase 3 (Medium):**
   - Enhanced audit logging with compliance reporting
   - Multi-tenant isolation options
   - Export verification

4. **Phase 4 (Low):**
   - Data classification
   - DLP integration hooks

## Open Questions

1. Should archived user data maintain original structure or be flattened for long-term storage?
2. What vector format should be used for embedding export (JSON arrays, Parquet, native DB format)?
3. Should KB soft-delete be the default, with hard-delete requiring explicit flag?
4. What compliance standards are priority (GDPR, NIS2, BIO, AVG)?
5. Should multi-tenant isolation be at application or infrastructure level?
