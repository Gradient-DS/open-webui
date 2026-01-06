---
date: 2026-01-06T15:45:00+01:00
researcher: Claude
git_commit: e0272831a4a5cc7a9fc0ffe361c8597f11fa487b
branch: feat/admin-config
repository: open-webui
topic: "OneDrive Folder Sync Implementation Plan for Knowledge Bases"
tags: [research, implementation-plan, onedrive, knowledge-base, delegated-auth, sync]
status: complete
last_updated: 2026-01-06
last_updated_by: Claude
related_research: ["2026-01-06-sharepoint-onedrive-integration-options.md"]
---

# Research: OneDrive Folder Sync Implementation Plan

**Date**: 2026-01-06T15:45:00+01:00
**Researcher**: Claude
**Git Commit**: e0272831a4a5cc7a9fc0ffe361c8597f11fa487b
**Branch**: feat/admin-config
**Repository**: open-webui

## Research Question

Building on [previous research](./2026-01-06-sharepoint-onedrive-integration-options.md), design an implementation for:
1. OneDrive folder selection with delegated permissions
2. Folder-based knowledge bases with automatic sync
3. Integration with Open WebUI's embedding pipeline
4. Code location decision (genai-utils vs soev-rag vs separate)

## Summary

**Feasibility: HIGH** - All required components exist and can be assembled.

**Recommended Architecture:**
- Build in **soev-rag** as a new `onedrive/` module alongside existing `sharepoint/`
- Adapt Open WebUI's file picker to support folder selection (`typesAndSources.mode: 'all'`)
- Store refresh tokens in Open WebUI's existing `oauth_sessions` table
- Submit chunks to Open WebUI's `/api/v1/retrieval/process/text` endpoint for embedding
- Track OneDrive folders as knowledge base data sources via `knowledge.meta.source`

**Key Implementation Steps:**
1. Frontend: Modify picker for folder selection + consent flow
2. Backend (Open WebUI): Store OAuth tokens, add sync trigger endpoints
3. Worker (soev-rag): New OneDrive sync module with user-scoped tokens
4. Integration: Worker calls Open WebUI API to embed chunks

## Detailed Findings

### 1. Folder Selection in OneDrive Picker

**Current Implementation** (`src/lib/utils/onedrive-file-picker.ts:214`):
```typescript
typesAndSources: {
    mode: 'files',  // Currently files only
}
```

**Required Change** - Set `mode: 'all'` or `mode: 'folders'`:
```typescript
typesAndSources: {
    mode: 'all',  // Enable both files and folders
    filters: ['folder', 'file'],
    pivots: {
        oneDrive: true,
        recent: true
    }
}
```

**MS Graph Folder Selection Response:**
```json
{
  "items": [{
    "id": "folder-id",
    "name": "Documents",
    "folder": { "childCount": 42 },
    "parentReference": { "driveId": "drive-id" },
    "@sharePoint.endpoint": "https://graph.microsoft.com/v1.0"
  }]
}
```

The `folder` property presence indicates a folder vs file.

### 2. OAuth Scopes Required

**Minimum scopes for folder sync:**
```
Files.Read.All offline_access
```

| Scope | Purpose |
|-------|---------|
| `Files.Read.All` | Read all files user can access (including shared) |
| `offline_access` | **Critical** - enables refresh tokens for background sync |

**Optional scopes:**
- `Sites.Read.All` - For SharePoint site document libraries
- `User.Read` - Get user profile for display

### 3. Token Storage for Background Sync

**Open WebUI already has OAuth session storage** (`backend/open_webui/models/oauth_sessions.py:24-42`):
```python
class OAuthSession(Base):
    __tablename__ = "oauth_session"
    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)      # "microsoft"
    token = Column(Text, nullable=False)         # Encrypted MSAL cache
    expires_at = Column(BigInteger)
```

**Token flow:**
1. Frontend completes OAuth popup with `offline_access` scope
2. Frontend receives tokens, sends to backend
3. Backend stores MSAL token cache (serialized) in `oauth_sessions`
4. Sync worker loads cache, uses `acquire_token_silent()` for refresh

**MSAL Python pattern:**
```python
from msal import ConfidentialClientApplication, SerializableTokenCache

cache = SerializableTokenCache()
cache.deserialize(stored_cache_json)

app = ConfidentialClientApplication(
    client_id=CLIENT_ID,
    client_credential=CLIENT_SECRET,
    authority=f"https://login.microsoftonline.com/common",
    token_cache=cache
)

# Silent acquisition automatically uses refresh token
result = app.acquire_token_silent(
    scopes=["Files.Read.All"],
    account=app.get_accounts()[0]
)

# Save updated cache after refresh
if cache.has_state_changed:
    save_to_db(user_id, cache.serialize())
```

### 4. Knowledge Base Data Source Model

**Extend Knowledge.meta** for OneDrive source tracking:
```json
{
  "source": {
    "type": "onedrive",
    "folders": [
      {
        "folder_id": "abc123",
        "drive_id": "xyz789",
        "name": "Project Documents",
        "path": "/Documents/Project"
      }
    ],
    "last_sync": "2026-01-06T15:00:00Z",
    "sync_status": "completed",
    "delta_link": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=..."
  }
}
```

**File.meta extension** for tracking external origin:
```json
{
  "external_source": "onedrive",
  "external_id": "item-id-from-graph",
  "external_modified": "2026-01-06T14:30:00Z",
  "external_path": "/Documents/Project/report.pdf"
}
```

### 5. Embedding via Open WebUI API

**Endpoint**: `POST /api/v1/retrieval/process/text`

**Request:**
```json
{
  "name": "report.pdf - chunk 1",
  "content": "The chunked text content...",
  "collection_name": "knowledge-base-uuid"
}
```

**Authentication**: Bearer token (JWT or API key `sk-xxx`)

**Behavior**:
- Text is chunked (unless under chunk size)
- Embedded using configured `RAG_EMBEDDING_MODEL`
- Stored in vector DB under specified collection

**Important**: This ensures Open WebUI controls the embedding model, keeping query/document embeddings aligned.

### 6. Build Location Decision

| Factor | genai-utils | soev-rag | Open WebUI |
|--------|-------------|----------|------------|
| User auth model | API key only | JWT with entity_id | Full user model |
| Graph API code | None | Full SharePoint impl | Minimal picker only |
| Worker pattern | Batch only | Cron scheduler | None |
| Deployment | Separate | Can share Weaviate | Monolith |
| Integration effort | High | Low | Medium |

**Recommendation: Build in soev-rag**

Rationale:
1. **Existing SharePoint code** provides 80% of needed Graph API infrastructure
2. **User-scoped entity_id** already wired through auth middleware
3. **Worker process pattern** exists with APScheduler
4. **Separate deployment** allows independent scaling
5. **JWT compatibility** with Open WebUI for API calls

### 7. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Open WebUI                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                         Frontend (Svelte)                               ││
│  │  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────┐  ││
│  │  │ Knowledge UI     │  │ OneDrive Picker    │  │ Folder Consent     │  ││
│  │  │ (data source     │  │ (mode: 'all')      │  │ Modal              │  ││
│  │  │  selection)      │  │                    │  │                    │  ││
│  │  └────────┬─────────┘  └─────────┬──────────┘  └─────────┬──────────┘  ││
│  └───────────┼──────────────────────┼───────────────────────┼─────────────┘│
│              │                      │                       │              │
│  ┌───────────┼──────────────────────┼───────────────────────┼─────────────┐│
│  │           │      Backend (FastAPI)                       │             ││
│  │           ▼                      ▼                       ▼             ││
│  │  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────┐  ││
│  │  │ Knowledge Router │  │ OAuth Session      │  │ Retrieval API      │  ││
│  │  │ (source config)  │  │ (token storage)    │  │ /process/text      │  ││
│  │  └────────┬─────────┘  └─────────┬──────────┘  └─────────▲──────────┘  ││
│  └───────────┼──────────────────────┼───────────────────────┼─────────────┘│
│              │                      │                       │              │
│              │                      │                       │              │
└──────────────┼──────────────────────┼───────────────────────┼──────────────┘
               │                      │                       │
               │         ┌────────────┴────────────┐          │
               │         │      PostgreSQL         │          │
               │         │  ┌─────────────────┐    │          │
               │         │  │ oauth_sessions  │    │          │
               │         │  │ (MSAL cache)    │    │          │
               │         │  └─────────────────┘    │          │
               │         │  ┌─────────────────┐    │          │
               │         │  │ knowledge       │    │          │
               │         │  │ (meta.source)   │    │          │
               │         │  └─────────────────┘    │          │
               │         └─────────────────────────┘          │
               │                      │                       │
┌──────────────┼──────────────────────┼───────────────────────┼──────────────┐
│              │         soev-rag OneDrive Worker             │              │
│              ▼                      ▼                       │              │
│  ┌──────────────────┐  ┌────────────────────┐  ┌───────────┴────────────┐ │
│  │ Scheduler        │  │ OAuth Manager      │  │ Sync Worker            │ │
│  │ (APScheduler)    │  │ (MSAL delegated)   │  │ - Delta query          │ │
│  │                  │  │                    │  │ - Download files       │ │
│  └────────┬─────────┘  └─────────┬──────────┘  │ - Parse/chunk          │ │
│           │                      │             │ - Call /process/text   │ │
│           │                      │             └───────────┬────────────┘ │
│           ▼                      ▼                         │              │
│  ┌────────────────────────────────────────────┐            │              │
│  │              Graph Client                  │            │              │
│  │  (Reuse from sharepoint/ with adaptations) │            │              │
│  └────────────────────────────────────────────┘            │              │
│                                                            │              │
└────────────────────────────────────────────────────────────┼──────────────┘
                                                             │
                                                             ▼
                                                    ┌────────────────┐
                                                    │    Weaviate    │
                                                    │ (via Open WebUI│
                                                    │  API)          │
                                                    └────────────────┘
```

## Implementation Plan

### Phase 1: Frontend Folder Selection

**Files to modify:**
- `src/lib/utils/onedrive-file-picker.ts`
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

**Changes:**
1. Add picker mode parameter for folder selection
2. Create folder consent modal ("Sync all contents of X?")
3. Add "Add OneDrive Folder" option in Knowledge AddContentMenu
4. Store folder metadata in knowledge.meta.source

### Phase 2: Token Storage in Open WebUI

**Files to modify/create:**
- `backend/open_webui/routers/oauth.py` (new)
- `backend/open_webui/models/oauth_sessions.py` (exists)

**Changes:**
1. Create endpoint to receive MSAL token cache from frontend
2. Store encrypted token cache per user per provider
3. Create endpoint for sync worker to fetch user tokens

### Phase 3: soev-rag OneDrive Module

**New files:**
```
soev-rag/src/soev_rag/
├── onedrive/
│   ├── __init__.py
│   ├── oauth_manager.py      # Delegated auth (vs client credentials)
│   ├── graph_client.py       # Extend from sharepoint/
│   ├── entities.py           # Reuse from sharepoint/
│   └── sync_worker.py        # User-scoped sync logic
└── worker/
    ├── main.py               # Add OneDrive worker startup
    └── onedrive_scheduler.py # Per-user sync scheduling
```

**Key differences from SharePoint module:**
- `oauth_manager.py`: Uses `acquire_token_silent()` with stored refresh tokens
- Fetches tokens from Open WebUI's oauth_sessions API
- Scoped to individual user's accessible files

### Phase 4: Integration Testing

1. End-to-end folder selection → sync → query flow
2. Token refresh handling (simulate 90-day expiry)
3. Delta sync with file modifications
4. Error handling for revoked access

## Code References

### Open WebUI
- `src/lib/utils/onedrive-file-picker.ts:214` - Picker mode config
- `src/lib/utils/onedrive-file-picker.ts:123-167` - Token acquisition
- `backend/open_webui/models/oauth_sessions.py:24-42` - OAuth session model
- `backend/open_webui/models/knowledge.py:51` - Knowledge.meta field
- `backend/open_webui/routers/retrieval.py:1718` - `/process/text` endpoint
- `backend/open_webui/routers/knowledge.py:407-471` - Add file to knowledge

### soev-rag
- `src/soev_rag/sharepoint/token_manager.py:16-98` - MSAL pattern (client creds)
- `src/soev_rag/sharepoint/graph_client.py:30-349` - Graph API client
- `src/soev_rag/sharepoint/sync_worker.py:25-320` - Sync orchestration
- `src/soev_rag/worker/scheduler.py:15-106` - APScheduler cron
- `src/soev_rag/core/auth.py:152-169` - Entity ID extraction

## Architecture Insights

### Token Refresh Strategy

Refresh tokens last 90 days but must be used periodically:
1. Sync worker runs minimum every 2 weeks (configurable)
2. Each run calls `acquire_token_silent()` which refreshes if needed
3. Updated cache persisted back to oauth_sessions
4. On refresh failure → mark knowledge source as "needs_reauth"

### Delta Query for Incremental Sync

MS Graph provides delta queries to track changes:
```http
GET /me/drive/root/delta
GET /me/drive/items/{folder-id}/delta
```

Response includes `@odata.deltaLink` for subsequent calls. Store in `knowledge.meta.source.delta_link`.

### Chunk Submission Strategy

Two options considered:
1. **Direct Weaviate ingestion** - Bypasses Open WebUI embedding
2. **Via Open WebUI API** - Uses configured embedding model (RECOMMENDED)

Option 2 ensures embedding model consistency between indexing and querying.

## Open Questions

1. **Multi-folder support**: Can one knowledge base have multiple OneDrive folders?
   - Recommendation: Yes, store as array in meta.source.folders

2. **Sync frequency**: User-configurable or fixed?
   - Recommendation: Default hourly, user can trigger manual sync

3. **Conflict resolution**: What if file is updated during sync?
   - Recommendation: Use delta query's `@odata.nextLink` for pagination, skip files with newer timestamps

4. **Storage quotas**: Should we limit files per knowledge base?
   - Recommendation: Start without limits, add if needed

5. **Sharing**: If user loses access to shared folder?
   - Recommendation: Delta query returns `deleted` items, remove from knowledge base

## Related Research

- [2026-01-06-sharepoint-onedrive-integration-options.md](./2026-01-06-sharepoint-onedrive-integration-options.md) - Initial feasibility research

## External Sources

- [Microsoft Graph Delta API](https://learn.microsoft.com/en-us/graph/api/driveitem-delta)
- [OneDrive File Picker v8 Schema](https://learn.microsoft.com/en-us/onedrive/developer/controls/file-pickers/v8-schema)
- [MSAL Python Token Cache Serialization](https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization)
- [Refresh Tokens in Microsoft Identity Platform](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)
