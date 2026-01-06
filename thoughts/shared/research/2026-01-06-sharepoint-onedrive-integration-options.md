---
date: 2026-01-06T14:30:00+01:00
researcher: Claude
git_commit: e0272831a4a5cc7a9fc0ffe361c8597f11fa487b
branch: feat/admin-config
repository: open-webui
topic: "SharePoint and OneDrive Integration for Custom RAG Pipeline"
tags: [research, codebase, rag, sharepoint, onedrive, weaviate, integration]
status: complete
last_updated: 2026-01-06
last_updated_by: Claude
---

# Research: SharePoint and OneDrive Integration for Custom RAG Pipeline

**Date**: 2026-01-06T14:30:00+01:00
**Researcher**: Claude
**Git Commit**: e0272831a4a5cc7a9fc0ffe361c8597f11fa487b
**Branch**: feat/admin-config
**Repository**: open-webui

## Research Question

We have a custom RAG pipeline that parses/chunks documents and ingests into Weaviate via Open WebUI's embedding API. Now we want to add SharePoint/OneDrive integrations. Key questions:
1. Will this be automatically synced?
2. What access does a user have on OneDrive integration?
3. Can users pick folders to sync vs automatic full sync?
4. Can knowledge bases be used in models and tools?
5. Will we ingest into the same Weaviate database?
6. Can we use our custom parsing/chunking pipeline?
7. Can we reuse code from soev-rag SharePoint?

## Summary

**Current State:** Open WebUI has built-in OneDrive/SharePoint integration via file picker (manual selection), but **no automatic sync**. Users pick files via Microsoft Graph picker, files are downloaded and processed through standard RAG pipeline.

**Recommendation:** To add automatic SharePoint sync, you would need to extend Open WebUI with a background worker similar to soev-rag's implementation. The soev-rag code is highly reusable and can be adapted to work with Open WebUI's existing infrastructure.

## Detailed Findings

### 1. Automatic Sync - NO (Currently Manual Picker Only)

Open WebUI's OneDrive/SharePoint integration is **picker-based, not sync-based**:

- **Frontend Picker**: `src/lib/utils/onedrive-file-picker.ts` uses MSAL for OAuth
- **User Action Required**: Users click "OneDrive" button, authenticate, select files
- **One-time Download**: Selected files are downloaded once and processed
- **No Background Sync**: No worker process, no cron scheduler, no delta tracking

**Configuration** (`backend/open_webui/config.py:2406-2437`):
```python
ENABLE_ONEDRIVE_INTEGRATION = PersistentConfig(...)
ENABLE_ONEDRIVE_PERSONAL = env var check  # Consumer accounts
ENABLE_ONEDRIVE_BUSINESS = env var check  # Work/school accounts
ONEDRIVE_CLIENT_ID = env var
ONEDRIVE_SHAREPOINT_URL = PersistentConfig(...)  # For SharePoint sites
```

**Contrast with soev-rag**: soev-rag has full automatic sync with cron scheduling (`0 * * * *` hourly by default), delta tracking, and change detection.

### 2. User Access on OneDrive Integration

**Open WebUI Current Behavior:**
- OAuth uses user's own credentials (delegated permissions)
- User sees files **they have access to** in the picker
- File picker uses standard Microsoft Graph scopes
- No impersonation - it's the user's own OneDrive/SharePoint access

**soev-rag Approach** (different model):
- Uses **client credentials** (app-only auth) - `token_manager.py:13`
- Syncs everything the app registration has access to
- Tenant-level isolation via `entity_id` field
- All users within tenant see all synced documents

### 3. Folder Selection vs Full Sync

**Open WebUI (Current):**
- Users **pick individual files** via Microsoft file picker
- No folder sync functionality
- No recursive folder selection in picker UI
- Directory sync only exists for local uploads (`KnowledgeBase.svelte:328-420`)

**What's Needed for Folder/Site Sync:**
- Background worker to enumerate SharePoint sites/drives
- Recursive folder traversal (soev-rag's `list_all_drive_items_recursively`)
- UI for selecting sites/drives to sync
- Cursor-based tracking for incremental updates

### 4. Knowledge Bases in Models and Tools - YES

**Model Integration** (`middleware.py:1238-1278`):
- Models can have `info.meta.knowledge` array
- Knowledge bases automatically added to chat context
- Vector search or full context modes supported

```python
# From model config
model_knowledge = model.get("info", {}).get("meta", {}).get("knowledge", False)
# Added to files for RAG processing
files.extend(knowledge_files)
```

**Tool Integration**:
- Tools receive `__files__` parameter with attached files/knowledge
- No direct RAG access, but tools can access file content
- Tools configured via `model.meta.toolIds`

**UI Configuration:**
- `src/lib/components/workspace/Models/Knowledge.svelte` - Knowledge selector in model editor
- Knowledge bases can be marked as "full context" (inject all content) or "vector search" (retrieve top-k)

### 5. Same Weaviate Database - YES

**Open WebUI RAG uses pluggable vector DB** (`retrieval/vector/factory.py:78`):
```python
VECTOR_DB_CLIENT = Vector.get_vector(VECTOR_DB)  # Set VECTOR_DB=weaviate
```

**Weaviate Configuration** (`config.py:2215-2218`):
```python
WEAVIATE_HTTP_HOST = os.environ.get("WEAVIATE_HTTP_HOST", "")
WEAVIATE_HTTP_PORT = int(os.environ.get("WEAVIATE_HTTP_PORT", "8080"))
WEAVIATE_GRPC_PORT = int(os.environ.get("WEAVIATE_GRPC_PORT", "50051"))
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY")
```

**Collection Strategy:**
- Each knowledge base uses its UUID as collection name
- SharePoint files from soev-rag use `SharePointFiles` collection
- Your custom pipeline can ingest into same Weaviate, but consider:
  - Separate collections per data source (recommended)
  - Or shared collection with `entity_id` filtering

### 6. Using Custom Parsing/Chunking Pipeline - POSSIBLE

**Open WebUI's Default Pipeline** (`retrieval.py:1269-1473`):
1. Load via `Loader` class (PDF, DOCX, etc.)
2. Split via `RecursiveCharacterTextSplitter` or `TokenTextSplitter`
3. Embed via configured model
4. Insert via `VECTOR_DB_CLIENT.insert()`

**Using Your Own Pipeline:**
- **Option A: External Processing** - Process files externally, call Open WebUI's `/api/v1/retrieval/process/file` endpoint
- **Option B: Custom Loader** - Add external extraction engine (`CONTENT_EXTRACTION_ENGINE=external`)
- **Option C: Direct Weaviate Ingestion** - Bypass Open WebUI's pipeline entirely, ingest directly to Weaviate

**Recommendation:** Option C is cleanest. Your pipeline outputs chunks + embeddings → insert directly into Weaviate collection → Open WebUI can query via knowledge base.

### 7. Reusing soev-rag SharePoint Code - HIGHLY RECOMMENDED

**Reusable Components:**

| Component | Location | Adaptability |
|-----------|----------|--------------|
| Token Manager | `soev-rag/src/soev_rag/sharepoint/token_manager.py` | Direct reuse (MSAL pattern) |
| Graph Client | `soev-rag/src/soev_rag/sharepoint/graph_client.py` | Direct reuse (API wrapper) |
| Entities | `soev-rag/src/soev_rag/sharepoint/entities.py` | Direct reuse (Pydantic models) |
| Sync Worker | `soev-rag/src/soev_rag/sharepoint/sync_worker.py` | Adapt to Open WebUI |
| Scheduler | `soev-rag/src/soev_rag/worker/scheduler.py` | Direct reuse (APScheduler) |
| Cursor Service | `soev-rag/src/soev_rag/services/sync_cursor.py` | Direct reuse |

**Integration Strategy:**
1. Copy/adapt token manager and graph client (standalone, no deps)
2. Create Open WebUI worker process (similar to `main.py`)
3. Use Open WebUI's vector DB abstraction instead of direct Weaviate
4. Integrate with Open WebUI's knowledge base model

## Code References

### Open WebUI RAG
- `backend/open_webui/retrieval/vector/factory.py:78` - Vector DB singleton
- `backend/open_webui/retrieval/vector/dbs/weaviate.py` - Weaviate adapter
- `backend/open_webui/routers/retrieval.py:1269-1473` - Document processing
- `backend/open_webui/routers/knowledge.py` - Knowledge CRUD

### Open WebUI OneDrive Integration
- `src/lib/utils/onedrive-file-picker.ts` - Picker implementation
- `backend/open_webui/config.py:2406-2437` - OneDrive config
- `src/lib/components/chat/MessageInput/InputMenu.svelte:342-551` - UI integration

### soev-rag SharePoint
- `soev-rag/src/soev_rag/sharepoint/token_manager.py:16-98` - MSAL auth
- `soev-rag/src/soev_rag/sharepoint/graph_client.py:30-349` - Graph API client
- `soev-rag/src/soev_rag/sharepoint/sync_worker.py:25-320` - Sync orchestration
- `soev-rag/src/soev_rag/worker/scheduler.py:15-106` - Cron scheduling

### Model-Knowledge Integration
- `backend/open_webui/utils/middleware.py:1238-1278` - Knowledge extraction
- `backend/open_webui/retrieval/utils.py:1074-1116` - Knowledge base query

## Architecture Insights

### Data Flow Comparison

**Current Open WebUI OneDrive Flow:**
```
User clicks "OneDrive" → OAuth popup → File picker → Download file →
Process via RAG pipeline → Store in Weaviate → Add to knowledge base
```

**Desired SharePoint Auto-Sync Flow:**
```
Background worker (cron) → Enumerate sites/drives → Delta detection →
Download changed files → Custom parsing/chunking → Embed →
Store in Weaviate → Available in knowledge bases
```

### Integration Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Open WebUI                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────┐  │
│  │ Knowledge   │  │ Models      │  │ Chat Processing             │  │
│  │ Management  │  │ (meta.      │  │ (middleware.py)             │  │
│  │ UI          │  │  knowledge) │  │                             │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────────┬──────────────┘  │
│         │                │                        │                  │
│         ▼                ▼                        ▼                  │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    Weaviate (VECTOR_DB_CLIENT)                │  │
│  │  ┌────────────┐  ┌─────────────────┐  ┌───────────────────┐   │  │
│  │  │ Knowledge  │  │ SharePointFiles │  │ Custom Pipeline   │   │  │
│  │  │ Collections│  │ (new)           │  │ Collections       │   │  │
│  │  └────────────┘  └────────┬────────┘  └───────────────────┘   │  │
│  └───────────────────────────┼───────────────────────────────────┘  │
└──────────────────────────────┼──────────────────────────────────────┘
                               │
         ┌─────────────────────┴─────────────────────┐
         │         NEW: SharePoint Sync Worker        │
         │  (Adapted from soev-rag)                   │
         │  ┌──────────────┐  ┌────────────────────┐ │
         │  │ Token Manager│  │ Graph Client       │ │
         │  │ (MSAL)       │  │ (MS Graph API)     │ │
         │  └──────────────┘  └────────────────────┘ │
         │  ┌──────────────┐  ┌────────────────────┐ │
         │  │ Scheduler    │  │ Cursor Service     │ │
         │  │ (APScheduler)│  │ (Delta tracking)   │ │
         │  └──────────────┘  └────────────────────┘ │
         │  ┌────────────────────────────────────┐   │
         │  │ Your Custom Parse/Chunk Pipeline   │   │
         │  └────────────────────────────────────┘   │
         └───────────────────────────────────────────┘
```

## Open Questions

1. **Multi-tenant vs Single-tenant**: Will you support multiple SharePoint tenants, or just one per deployment?
2. **User-level Permissions**: Should SharePoint item permissions be synced and enforced, or tenant-wide access?
3. **Folder Selection UI**: How should users configure which SharePoint sites/drives to sync?
4. **Collection Strategy**: One collection for all SharePoint files, or per-site collections?
5. **OneDrive Personal**: Do you need OneDrive personal account sync, or just SharePoint/Business?
