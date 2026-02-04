---
date: 2026-02-04T18:30:00+01:00
researcher: Claude
git_commit: 73fddcf16c984775391c91ca1f6657a6266a5e47
branch: feat/simple-kb
repository: Gradient-DS/open-webui
topic: "Current State of OneDrive Sync System for Background Sync Planning"
tags: [research, codebase, onedrive, sync, background-sync, token-refresh, oauth]
status: complete
last_updated: 2026-02-04
last_updated_by: Claude
---

# Research: Current State of OneDrive Sync System for Background Sync Planning

**Date**: 2026-02-04T18:30:00+01:00
**Researcher**: Claude
**Git Commit**: 73fddcf16c984775391c91ca1f6657a6266a5e47
**Branch**: feat/simple-kb
**Repository**: Gradient-DS/open-webui

## Research Question

What is the current state of the OneDrive KB sync system, and what needs to change to enable background (scheduled) sync? The previous plan at `thoughts/shared/plans/2026-02-04-background-sync-multi-datasource.md` was written against an earlier version of the codebase.

## Summary

The OneDrive sync system is fully functional for **manual, user-triggered syncs** but completely inert for **background/scheduled syncs**. The core blocker is unchanged from the original plan: the system relies on short-lived MSAL tokens obtained client-side, and has no server-side token refresh mechanism. However, the sync worker, graph client, and frontend integration have matured significantly since the plan was written. Key differences from the original plan's assumptions:

1. **No `user_token` on backend** -- The `SyncItemsRequest` model has only `access_token`, not `user_token`. The frontend sends `user_token` in the body but the backend silently ignores it (Pydantic strips extra fields).
2. **No PermissionProvider pattern** -- The plan references `PermissionProvider(ABC)` and `PermissionProviderRegistry` but these don't exist. The codebase uses **factory-singleton** patterns (StorageProvider, VectorDBBase) instead of registries.
3. **`app` parameter required** -- The `OneDriveSyncWorker` constructor requires the FastAPI `app` instance (for constructing mock `Request` objects to call `process_file`). The original plan omits this.
4. **Permission sync built-in** -- The sync worker already syncs OneDrive folder permissions to KB access control (`_sync_permissions`). The original plan's `SyncProvider.get_permissions()` duplicates this.
5. **Knowledge `type` column** -- Now exists with values `"local"` and `"onedrive"`, validated at creation. This wasn't in the original plan.
6. **File processing is tightly coupled** -- The worker calls `process_file` from `routers/retrieval.py` which requires a mock `Request` with `app.state.config` and `app.state.ef`. This makes it hard to abstract into a generic provider.
7. **No `clear_exclusions` field** -- The plan mentions this but the actual `SyncItemsRequest` doesn't have it.

## Detailed Findings

### 1. Sync Worker Interface

**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Constructor signature:
```python
def __init__(
    self,
    knowledge_id: str,
    sources: List[Dict[str, Any]],
    access_token: str,
    user_id: str,
    app,  # FastAPI app instance -- REQUIRED
    event_emitter: Optional[Callable] = None,
)
```

The single public method is `sync()` which returns a dict with `files_processed`, `files_failed`, `total_found`, `deleted_count`, `failed_files`.

Key observations for background sync:
- The `app` parameter is mandatory -- used to construct mock `Request` objects for calling `process_file` (line 106-122). The scheduler will need access to the FastAPI app instance.
- The `access_token` is passed once at construction and never refreshed. For background sync, we need to either refresh before constructing the worker, or make the worker token-refresh-aware.
- There is no `user_token` parameter -- the worker doesn't make any authenticated HTTP calls to Open WebUI's own API. All file processing goes through direct function calls to `process_file`.

### 2. Router / Endpoint State

**File**: `backend/open_webui/routers/onedrive_sync.py`

The `SyncItemsRequest` model:
```python
class SyncItemsRequest(BaseModel):
    knowledge_id: str
    items: List[SyncItem]
    access_token: str  # Required, no Optional
```

There are **no auth callback endpoints**. No `/auth/initiate`, `/auth/callback`, `/token-status`, or `/revoke-token` endpoints exist. These all need to be created.

The `sync_items_to_knowledge` background task function (lines 115-132) creates the worker:
```python
worker = OneDriveSyncWorker(
    knowledge_id=knowledge_id,
    sources=sources,
    access_token=access_token,
    user_id=user_id,
    app=app,
)
```

Note: the `app` is obtained from `fastapi_request.app` in the endpoint handler and passed through.

### 3. Scheduler State

**File**: `backend/open_webui/services/onedrive/scheduler.py`

Completely placeholder. Key facts:
- Has `start_scheduler()` and `stop_scheduler()` functions
- The main loop (`run_scheduled_syncs`) sleeps for `ONEDRIVE_SYNC_INTERVAL_MINUTES`, then calls `_check_and_report_due_syncs()` which only logs
- Lines 87-92 explicitly say: "Scheduled sync execution requires token refresh implementation"
- **Never wired into `main.py`** -- `start_scheduler()` is exported but never called
- Uses `Knowledges.get_knowledge_bases()` which has a default limit of 30 -- may miss KBs in large deployments

### 4. Config State

**File**: `backend/open_webui/config.py`

| Variable | Type | Default | Exists? |
|----------|------|---------|---------|
| `ENABLE_ONEDRIVE_INTEGRATION` | PersistentConfig | `False` | Yes |
| `ENABLE_ONEDRIVE_SYNC` | PersistentConfig | `False` | Yes |
| `ONEDRIVE_SYNC_INTERVAL_MINUTES` | PersistentConfig | `60` | Yes |
| `ONEDRIVE_CLIENT_ID_BUSINESS` | env var | `""` | Yes |
| `ONEDRIVE_CLIENT_SECRET_BUSINESS` | env var | - | **NO** |
| `ONEDRIVE_SHAREPOINT_TENANT_ID` | PersistentConfig | `""` | Yes |
| `ONEDRIVE_MAX_FILES_PER_SYNC` | env var | `500` | Yes |
| `ONEDRIVE_MAX_FILE_SIZE_MB` | env var | `100` | Yes |

The `ONEDRIVE_CLIENT_SECRET_BUSINESS` config variable needs to be added for the confidential client auth flow.

### 5. OAuth Sessions Model

**File**: `backend/open_webui/models/oauth_sessions.py`

The `OAuthSession` model is production-ready with Fernet encryption:
- Stores encrypted token dict (can contain `access_token`, `refresh_token`, `expires_at`, etc.)
- Indexed on `(user_id, provider)` -- perfect for lookup by `f"onedrive:{knowledge_id}"`
- Has `create_session`, `get_session_by_provider_and_user_id`, `update_session_by_id`, `delete_session_by_id`
- Encryption key derived from `WEBUI_SECRET_KEY` via SHA-256

**No OneDrive code currently uses OAuthSessions.** Zero references from the `services/onedrive/` directory.

### 6. Graph Client

**File**: `backend/open_webui/services/onedrive/graph_client.py`

- Takes a single `access_token` at construction, no refresh capability
- **No 410 Gone handling** -- delta token expiry causes an unhandled `HTTPStatusError`
- Retry logic covers 429 (rate limit) and 5xx only
- 401 Unauthorized passes through without retry (token expiry during long sync)
- All methods are async with `httpx.AsyncClient`

### 7. Frontend Token Flow

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Two MSAL token acquisitions happen per sync:
1. **Picker token** -- SharePoint-scoped, used inside the iframe picker (via `getToken()`)
2. **Graph API token** -- `Files.Read.All` scoped, passed to backend (via `getGraphApiToken()`)

Both are acquired via MSAL's `PublicClientApplication` (SPA flow). These tokens:
- Expire in ~1 hour
- Cannot be refreshed server-side (SPA refresh tokens have 24-hour hard limit)
- Are obtained interactively (popup)

For background sync, the frontend needs to trigger a **backend OAuth flow** (confidential client) that stores a 90-day refresh token. The existing MSAL picker flow can remain unchanged for item selection.

### 8. Knowledge Meta Structure

The `onedrive_sync` metadata stored in `knowledge.meta`:

```python
{
    "onedrive_sync": {
        "sources": [
            {
                "type": "folder" | "file",
                "drive_id": "...",
                "item_id": "...",
                "item_path": "...",
                "name": "...",
                "delta_link": "...",      # folder only, after first sync
                "content_hash": "...",    # file only, after first sync
            }
        ],
        "status": "idle|syncing|completed|completed_with_errors|failed|cancelled|access_revoked|file_limit_exceeded",
        "progress_current": 0,
        "progress_total": 0,
        "error": "...",
        "last_sync_at": 1738000000,
        "last_result": {
            "files_processed": 5,
            "files_failed": 1,
            "total_found": 6,
            "deleted_count": 0,
            "failed_files": [...]
        }
    }
}
```

For background sync, we need to add:
- `has_stored_token: bool` -- whether a backend refresh token exists
- `needs_reauth: bool` -- whether the token was revoked
- `token_stored_at: int` -- when the token was last stored

### 9. Provider Patterns in Codebase

There is **no PermissionProvider/Registry pattern**. The existing patterns are:

**StorageProvider** (`storage/provider.py`):
- ABC with `@abstractmethod`
- Factory function (`get_storage_provider`) with if/elif
- Module-level singleton (`Storage`)
- All implementations in one file

**VectorDBBase** (`retrieval/vector/main.py`):
- ABC in `main.py`, factory in `factory.py`
- Implementations in separate files under `dbs/`
- `match/case` factory with lazy imports
- Module-level singleton (`VECTOR_DB_CLIENT`)

Both use **factory-singleton**, not **registry** patterns. The original plan's `SyncProviderRegistry` diverges from codebase conventions.

### 10. main.py Integration Points

**Lifespan function** (`main.py:625-715`):
- Background tasks use `asyncio.create_task()` for infinite loops
- Two existing examples: `periodic_usage_pool_cleanup()`, `periodic_archive_cleanup()`
- OneDrive scheduler is NOT started here -- needs to be added

**Router mounting** (`main.py:1520-1524`):
- Conditional on `ENABLE_ONEDRIVE_SYNC`
- Auth endpoints would go on the same router

**Config state** (`main.py:1042-1043`):
- `ENABLE_ONEDRIVE_INTEGRATION` and `ENABLE_ONEDRIVE_SYNC` assigned to `app.state.config`

**Config exposure** (`main.py:2049-2105`):
- Frontend receives OneDrive feature flags and client IDs

## Differences from Original Plan

| Aspect | Original Plan Assumes | Current Reality |
|--------|----------------------|-----------------|
| `SyncItemsRequest.user_token` | Exists, make optional | Does not exist on backend model |
| `SyncItemsRequest.clear_exclusions` | Exists | Does not exist |
| `PermissionProvider` ABC | Exists at `services/permissions/` | Does not exist anywhere |
| `PermissionProviderRegistry` | Exists at `services/permissions/` | Does not exist |
| `OneDrivePermissionProvider` | Exists | Does not exist |
| Worker constructor | `(knowledge_id, sources, access_token, user_id, user_token)` | `(knowledge_id, sources, access_token, user_id, app, event_emitter=None)` |
| Worker uses `user_token` | For internal HTTP calls to retrieval API | Uses direct function calls to `process_file`, no HTTP |
| `process_file` invocation | Via internal HTTP with `user_token` | Via direct function call with mock `Request` object |
| Knowledge `type` column | Not mentioned | Exists: `"local"` or `"onedrive"` |
| Provider pattern | Registry-based (`register/get/list`) | Factory-singleton (if/elif + module variable) |
| Permission sync | Part of `SyncProvider.get_permissions()` | Already built into `OneDriveSyncWorker._sync_permissions()` |
| 410 Gone handling | Not handled | Still not handled (unchanged) |

## Impact on Plan Revision

### What Stays the Same
1. **Core architecture**: Backend OAuth auth code flow with confidential client for 90-day refresh tokens
2. **`ONEDRIVE_CLIENT_SECRET_BUSINESS`** config variable needed
3. **OAuthSessions** for encrypted token storage with `provider = "onedrive:{kb_id}"`
4. **Token refresh service** using direct `httpx` calls to Microsoft token endpoint
5. **Auth endpoints**: `/auth/initiate`, `/auth/callback`, `/token-status`, `/revoke-token`
6. **Frontend auth popup flow** with `postMessage` for callback result
7. **410 Gone handling** in GraphClient still needed
8. **Scheduler wiring** into `main.py` lifespan still needed

### What Needs Updating
1. **Worker invocation in scheduler**: Must pass `app` instance, not `user_token`. The scheduler needs access to the FastAPI app (available via `asyncio.create_task()` inside lifespan where `app` is accessible).
2. **No `user_token` generation needed**: The worker doesn't use it. Remove all `create_token()` and `user_token` references from the plan.
3. **No `SyncItemsRequest` changes needed for `user_token`**: It's already not there. Only `access_token` needs to become optional for backend-token-resolved syncs.
4. **Drop PermissionProvider references**: No such pattern exists. Use factory-singleton if abstracting, or skip abstraction entirely.
5. **Simplify Phase 5 (Multi-Datasource)**: The registry pattern diverges from codebase conventions. Either follow the factory-singleton pattern or defer abstraction entirely. The OneDrive implementation is tightly coupled (file ID format, meta keys, socket events, permission mapping).
6. **Phase 3 frontend changes**: The `SyncItemsRequest` TypeScript interface has `user_token` but the backend ignores it. Making `access_token` optional on the frontend interface is the key change.
7. **`clear_exclusions` field**: Remove from plan -- doesn't exist.

## Architecture Insights

The sync worker is surprisingly self-contained despite its complexity (~1200 lines). It handles:
- Source verification and revocation cleanup
- Delta queries for incremental folder sync
- Content hash comparison for individual file sync
- Parallel file processing with semaphore concurrency control
- Permission sync from OneDrive to Open WebUI access control
- File count limits
- Cancellation via metadata polling
- Real-time progress via Socket.IO

The main coupling points for background sync are:
1. **Token**: Needs to come from stored refresh token instead of frontend MSAL
2. **App instance**: Needed for `process_file` mock requests
3. **Status tracking**: Already uses `knowledge.meta` -- just need to add token status fields

## Open Questions

1. **Should we skip Phase 5 (Multi-Datasource Abstraction)?** The factory-singleton pattern used elsewhere is simpler but doesn't support runtime registration. The sync worker is heavily OneDrive-specific. Abstracting may be premature until a second datasource is actually needed.
2. **How should the scheduler get the `app` instance?** Options: (a) pass it into `start_scheduler()` from lifespan, (b) import `app` from `main.py` (circular risk), (c) store it in a module-level variable during lifespan startup.
3. **Should `access_token` become optional on the backend model?** For re-syncs triggered by the scheduler, there's no frontend-provided token. The scheduler would call `sync_items_to_knowledge()` directly, bypassing the router. The router endpoint could remain unchanged (requiring `access_token` for manual syncs).
4. **KB limit in scheduler**: `Knowledges.get_knowledge_bases()` has a default limit of 30. The scheduler needs a method that returns ALL OneDrive-type KBs, possibly `Knowledges.get_knowledge_bases_by_type("onedrive")` or filtering by `meta` containing `onedrive_sync.has_stored_token`.
