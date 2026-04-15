---
date: 2026-03-24T14:30:00+01:00
researcher: Claude
git_commit: 73c1e1fb21476308a2180fcdd135daf0b22c50fe
branch: Feat/google-drive-integration
repository: open-webui
topic: 'Cloud Sync Abstraction Audit, Merge Strategy, and Integration Cookbook'
tags:
  [
    research,
    codebase,
    google-drive,
    onedrive,
    sync-abstraction,
    merge-strategy,
    integration-cookbook
  ]
status: complete
last_updated: 2026-03-24
last_updated_by: Claude
---

# Research: Cloud Sync Abstraction Audit, Merge Strategy, and Integration Cookbook

**Date**: 2026-03-24T14:30:00+01:00
**Researcher**: Claude
**Git Commit**: 73c1e1fb21476308a2180fcdd135daf0b22c50fe
**Branch**: Feat/google-drive-integration
**Repository**: open-webui

## Research Question

1. Audit the approach for external connections on the `Feat/google-drive-integration` branch
2. Determine if the abstractions are general enough for other integrations
3. Determine a merge strategy between this branch and `dev`
4. Write a recipe/cookbook for adding integrations like Topdesk/Confluence/Salesforce

## Summary

The abstraction layer on this branch is **well-designed and production-ready**. It cleanly separates shared sync orchestration from provider-specific API/auth logic using ABCs (`SyncProvider`, `TokenManager`, `BaseSyncWorker`) and shared utility modules (router helpers, scheduler, events, token refresh). Adding a new provider requires implementing ~4-5 files with no copy-paste of shared logic.

The merge with `dev` will require careful manual work on **4 high-conflict files**, primarily because dev added important bug fixes to `onedrive/sync_worker.py` which this branch completely restructured into `sync/base_worker.py`. The recommended approach is: merge dev into this branch, resolve conflicts file-by-file, and verify that dev's fixes are incorporated into the new abstraction layer.

---

## 1. Architecture Audit

### Abstraction Layer Structure

```
backend/open_webui/services/
├── sync/                          # Shared abstraction layer (NEW on this branch)
│   ├── __init__.py                # Re-exports SyncProvider, TokenManager, factories
│   ├── provider.py                # ABCs: SyncProvider, TokenManager + factory functions
│   ├── base_worker.py             # ABC: BaseSyncWorker (~993 lines of shared orchestration)
│   ├── constants.py               # SyncErrorType, FailedFile, SUPPORTED_EXTENSIONS, CONTENT_TYPES
│   ├── events.py                  # Generic Socket.IO event emitters (parameterized by prefix)
│   ├── scheduler.py               # SyncScheduler class (parameterized by provider config)
│   ├── token_refresh.py           # Generic token refresh + needs_reauth marking
│   └── router.py                  # Shared endpoint logic (13 handler functions)
│
├── onedrive/                      # OneDrive-specific (REFACTORED on this branch)
│   ├── provider.py                # OneDriveSyncProvider, OneDriveTokenManager
│   ├── auth.py                    # Microsoft OAuth + legacy session migration
│   ├── graph_client.py            # Microsoft Graph API v1.0 client
│   ├── sync_worker.py             # OneDriveSyncWorker(BaseSyncWorker) — provider-specific only
│   ├── token_refresh.py           # Microsoft token refresh (_refresh_token)
│   ├── sync_events.py             # Thin wrapper → sync/events.py
│   └── scheduler.py               # Thin wrapper → sync/scheduler.py
│
├── google_drive/                  # Google Drive-specific (NEW on this branch)
│   ├── provider.py                # GoogleDriveSyncProvider, GoogleDriveTokenManager
│   ├── auth.py                    # Google OAuth 2.0 + PKCE
│   ├── drive_client.py            # Google Drive API v3 client
│   ├── sync_worker.py             # GoogleDriveSyncWorker(BaseSyncWorker) — provider-specific only
│   ├── token_refresh.py           # Google token refresh (_refresh_token)
│   ├── sync_events.py             # Thin wrapper → sync/events.py
│   └── scheduler.py               # Thin wrapper → sync/scheduler.py
│
└── deletion.py                    # DeletionService (shared, pre-existing)
```

### Interface Design

**`SyncProvider`** (ABC) — 4 abstract methods + 1 concrete orchestration method:

- `get_provider_type()` → str (e.g., `"onedrive"`, `"google_drive"`)
- `get_meta_key()` → str (e.g., `"onedrive_sync"`, `"google_drive_sync"`)
- `get_token_manager()` → TokenManager
- `create_worker(...)` → BaseSyncWorker subclass
- `execute_sync(...)` — concrete method that handles token resolution and calls `worker.sync()`

**`TokenManager`** (ABC) — 3 abstract methods:

- `get_valid_access_token(user_id, knowledge_id)` → Optional[str]
- `has_stored_token(user_id, knowledge_id)` → bool
- `delete_token(user_id, knowledge_id)` → bool

**`BaseSyncWorker`** (ABC) — 7 abstract properties + 10 abstract methods:

Properties: `meta_key`, `file_id_prefix`, `event_prefix`, `internal_request_path`, `max_files_config`, `source_clear_delta_keys`

Methods: `_create_client()`, `_close_client()`, `_is_supported_file()`, `_collect_folder_files()`, `_collect_single_file()`, `_download_file_content()`, `_get_provider_storage_headers()`, `_get_provider_file_meta()`, `_sync_permissions()`, `_verify_source_access()`, `_handle_revoked_source()`

The `sync()` method (~290 lines) orchestrates the full workflow: source verification → file collection → limit enforcement → parallel processing with semaphore → cancellation detection → status updates.

### What's Shared vs Provider-Specific

| Layer                    | Shared (services/sync/)                                                                                                                                             | Provider-Specific                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| **Worker orchestration** | `sync()`, `_process_file_info()`, `_handle_deleted_item()`, `_ensure_vectors_in_kb()`, `_process_file_via_api()`, status updates, cancellation, parallel processing | File collection, download, support checks, permissions, access verification |
| **Router**               | All 8+ endpoint handlers (sync items, status, cancel, remove source, list collections, token status, revoke, auth callback HTML)                                    | Auth URL construction, SyncItem model shape, delta key names                |
| **Token refresh**        | Expiry check, refresh-or-reauth flow, `_mark_needs_reauth()`                                                                                                        | HTTP POST to provider token endpoint                                        |
| **Events**               | All 3 emitters (file processing, file added, sync progress)                                                                                                         | Event prefix string                                                         |
| **Scheduler**            | Full scheduler loop, due-check logic, stale recovery                                                                                                                | Config variables                                                            |
| **Auth**                 | PKCE generation, flow TTL, state validation                                                                                                                         | OAuth URLs, scopes, client config, legacy migration (OneDrive only)         |

### Assessment

The abstraction is **well-factored**. Key strengths:

1. **Template Method pattern** on BaseSyncWorker — shared orchestration calls provider-specific hooks
2. **Factory-singleton pattern** on SyncProvider — consistent with existing codebase patterns (StorageProvider, VectorDBBase)
3. **Router helper functions** eliminate all endpoint duplication while keeping provider-specific routing
4. **Each layer is independently parameterized** — no deep coupling between scheduler/events/worker

---

## 2. Generality Assessment for Future Integrations

### Will it work for Topdesk/Confluence/Salesforce/Dropbox/Box/SharePoint?

**Yes, with minor caveats.**

The abstraction handles the core sync lifecycle generically:

- OAuth token management (any OAuth 2.0 provider)
- File collection from folders (any hierarchical file system)
- Single file monitoring
- Incremental sync (via provider-specific change detection)
- Download → storage → process → vectorize pipeline
- Progress tracking and cancellation
- Permission sync to access_control
- Background scheduling

### Potential Friction Points for Specific Integrations

| Integration    | Fit                  | Notes                                                                                                                                                                                                              |
| -------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Dropbox**    | Excellent            | Very similar to Google Drive. Has changes API equivalent. Fits perfectly.                                                                                                                                          |
| **Box**        | Excellent            | OAuth 2.0, hierarchical files, events API for changes. Direct fit.                                                                                                                                                 |
| **SharePoint** | Good                 | Uses Microsoft Graph (same as OneDrive). Could share `graph_client.py` with OneDrive. May want a shared "Microsoft" auth layer.                                                                                    |
| **Confluence** | Good with extensions | Not file-based — it's page/space based. `_collect_folder_files` maps to "list pages in space". Download = export page as PDF/HTML. `_is_supported_file` always returns true. Works but slightly mismatched naming. |
| **Salesforce** | Moderate             | Uses different auth pattern (JWT bearer + connected app, not user OAuth). `TokenManager` would need to handle non-interactive auth. Content model is record-based, not file-based — would need adapter.            |
| **Topdesk**    | Moderate             | API-key auth, not OAuth. `TokenManager` interface assumes OAuth tokens. Would need either: (a) a no-op TokenManager that returns API key as "token", or (b) a separate auth interface.                             |
| **Email/IMAP** | Poor fit             | Not OAuth-based, not file-based. Would need significant abstraction changes. Better as a separate integration pattern.                                                                                             |

### Recommended Abstraction Improvements

1. **Auth flexibility**: `TokenManager` assumes OAuth with refresh tokens. For API-key integrations (Topdesk), consider adding a `StaticTokenManager` that simply returns a configured API key. This is a one-file addition, not a refactor.

2. **Naming**: `_collect_folder_files` and `_collect_single_file` use file/folder terminology. For page-based systems (Confluence), this still works but the naming is slightly confusing. Consider documenting that "folder" = "container" and "file" = "item" in the ABCs.

3. **Non-hierarchical sources**: The current model assumes items live in folders. Salesforce records or Confluence spaces are flat or differently structured. The abstraction handles this fine (just return all items from `_collect_folder_files`), but it's worth documenting.

4. **Push vs Pull**: The current design is pull-based (scheduler polls). For integrations with webhooks (Salesforce, Confluence Cloud), consider adding a `handle_webhook()` method to `SyncProvider` that triggers an immediate sync. This can be added later without breaking existing providers.

---

## 3. Merge Strategy: Feat/google-drive-integration → dev

### Branch Divergence

**This branch (8 commits):** Google Drive backend + frontend, sync abstraction layer extraction, helm config.

**Dev branch (14 commits):** CI/CD workflows, OneDrive bug fixes (access control, sync status), KB deletion fixes, KnowledgeBase.svelte UI fixes (flicker, upload UX), Black formatting, feature flags, security hardening.

### High-Conflict Files (4)

| File                               | This Branch                                                | Dev                                              | Resolution Strategy                                                                                                                                       |
| ---------------------------------- | ---------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/onedrive/sync_worker.py` | **Gutted** (-1083 lines → thin subclass of BaseSyncWorker) | +191 lines of fixes (access control, sync fixes) | **CRITICAL**: Must port dev's fixes into `sync/base_worker.py` (if they're shared logic) or into the new `onedrive/sync_worker.py` (if OneDrive-specific) |
| `KnowledgeBase.svelte`             | +676 lines (Google Drive UI)                               | Bug fixes (flicker, upload UX)                   | Keep both — apply dev's fixes to the merged version                                                                                                       |
| `routers/knowledge.py`             | Google Drive type allowed                                  | Access control security, soft-delete             | Both changes are additive, should merge cleanly after resolving textual conflicts                                                                         |
| `routers/onedrive_sync.py`         | Refactored to use shared router helpers                    | Minor fixes                                      | Keep this branch's version, verify dev's fixes are captured in shared router.py                                                                           |

### Medium-Conflict Files (~10)

- `config.py` — additive on both sides (Google Drive config vars + feature flags)
- `main.py` — Google Drive router registration + Black formatting
- `services/onedrive/auth.py` — abstraction refactor + formatting fixes
- `services/onedrive/provider.py` — abstraction + feature addition
- `services/onedrive/scheduler.py` — abstraction + formatting
- `services/onedrive/token_refresh.py` — abstraction + formatting
- `helm/` files — additive on both sides
- `src/lib/stores/index.ts` — +1 store vs +6 stores

### Recommended Merge Procedure

```bash
# 1. Create a merge branch for safety
git checkout Feat/google-drive-integration
git checkout -b merge/google-drive-into-dev

# 2. Merge dev into this branch (not the other way around)
#    This preserves the abstraction as the "base" and applies dev's fixes on top
git merge dev

# 3. Resolve conflicts file-by-file (see below)
# 4. Test both OneDrive and Google Drive sync manually
# 5. Merge the merge branch into dev
```

### Conflict Resolution Guide

#### `services/onedrive/sync_worker.py` (CRITICAL)

1. Read dev's version of this file carefully to identify all fixes
2. For each fix, determine if it's:
   - **Shared logic** (e.g., `_process_file_info`, `sync()` orchestration) → port to `sync/base_worker.py`
   - **OneDrive-specific** (e.g., delta query handling, permission parsing) → keep in `onedrive/sync_worker.py`
3. Keep this branch's thin subclass structure
4. Key dev fixes to look for: access control integration, status handling improvements

#### `KnowledgeBase.svelte`

1. Start with this branch's version (has Google Drive UI)
2. Apply dev's specific fixes:
   - Flicker fix on file list
   - Upload UX improvements
3. These are likely in different sections of the file and shouldn't overlap with Google Drive additions

#### `config.py` and `main.py`

Mostly additive — accept both sides. Watch for import order and Black formatting differences.

#### Black formatting conflicts

Dev reformatted many files with Black. For files where this branch only made structural changes (not content), accept dev's formatting. For files with real content changes, resolve content first, then run `black` on the result.

### Verification Checklist

After merge:

- [ ] OneDrive manual sync works (create KB, pick folder, sync starts)
- [ ] OneDrive background sync triggers on schedule
- [ ] OneDrive permissions sync to access_control
- [ ] Google Drive manual sync works
- [ ] Google Drive background sync triggers
- [ ] Google Drive Workspace file export (Docs→docx, Sheets→xlsx)
- [ ] KB deletion works cleanly (dev's fix)
- [ ] File list doesn't flicker (dev's fix)
- [ ] Access control on external KBs works (dev's fix)
- [ ] Cancellation works for both providers
- [ ] `npm run build` succeeds
- [ ] Backend starts without import errors

---

## 4. Integration Cookbook: Adding a New Cloud Sync Provider

### Prerequisites

- The provider has an OAuth 2.0 compatible auth flow (or API key auth)
- The provider has an API for listing files/items and downloading content
- You have client credentials (client_id, client_secret) from the provider

### Step-by-Step Recipe

#### Step 1: Add Config Variables

**File**: `backend/open_webui/config.py`

```python
# Provider feature flag
ENABLE_PROVIDER_SYNC = PersistentConfig(
    "ENABLE_PROVIDER_SYNC",
    "provider.enable_sync",
    os.environ.get("ENABLE_PROVIDER_SYNC", "true").lower() == "true",
)

# OAuth credentials
PROVIDER_CLIENT_ID = PersistentConfig(
    "PROVIDER_CLIENT_ID",
    "provider.client_id",
    os.environ.get("PROVIDER_CLIENT_ID", ""),
)
PROVIDER_CLIENT_SECRET = PersistentConfig(
    "PROVIDER_CLIENT_SECRET",
    "provider.client_secret",
    os.environ.get("PROVIDER_CLIENT_SECRET", ""),
)

# Sync settings
PROVIDER_SYNC_INTERVAL_MINUTES = PersistentConfig(
    "PROVIDER_SYNC_INTERVAL_MINUTES",
    "provider.sync_interval_minutes",
    int(os.environ.get("PROVIDER_SYNC_INTERVAL_MINUTES", "30")),
)
PROVIDER_MAX_FILES_PER_SYNC = PersistentConfig(
    "PROVIDER_MAX_FILES_PER_SYNC",
    "provider.max_files_per_sync",
    int(os.environ.get("PROVIDER_MAX_FILES_PER_SYNC", "250")),
)
PROVIDER_MAX_FILE_SIZE_MB = int(os.environ.get("PROVIDER_MAX_FILE_SIZE_MB", "100"))
```

#### Step 2: Create the API Client

**File**: `backend/open_webui/services/{provider}/api_client.py`

This is 100% provider-specific. Model it after `google_drive/drive_client.py` or `onedrive/graph_client.py`.

Required capabilities:

- Authenticated HTTP requests with retry logic (401 → token refresh, 429 → retry-after, 5xx → backoff)
- List items in a container (folder/space/project)
- Download file content
- Get item metadata
- Get change delta (if the provider supports incremental sync)
- Get permissions (if provider has sharing model)

```python
class ProviderClient:
    def __init__(self, access_token: str, token_provider=None):
        self._access_token = access_token
        self._token_provider = token_provider

    async def list_folder_children(self, folder_id: str) -> List[Dict]:
        """List items in a container."""
        ...

    async def download_file(self, file_id: str) -> bytes:
        """Download file content."""
        ...

    async def get_file(self, file_id: str) -> Optional[Dict]:
        """Get item metadata."""
        ...

    async def close(self):
        ...
```

#### Step 3: Create the Auth Module

**File**: `backend/open_webui/services/{provider}/auth.py`

Copy the structure from `google_drive/auth.py`. Customize:

- OAuth URLs (authorization endpoint, token endpoint)
- Scopes
- Client credentials config references
- Any provider-specific auth parameters

Key functions to implement:

- `get_authorization_url(user_id, knowledge_id, redirect_uri)` → str
- `exchange_code_for_tokens(code, state, user_id)` → dict
- `get_pending_flow(state)` → Optional[dict]
- `remove_pending_flow(state)` → None
- `get_stored_token(user_id)` → Optional[dict]
- `delete_stored_token(user_id)` → bool

#### Step 4: Create the Token Refresh Module

**File**: `backend/open_webui/services/{provider}/token_refresh.py`

Thin module — delegates to shared logic, provides provider-specific refresh HTTP call.

```python
from open_webui.services.sync.token_refresh import (
    get_valid_access_token as _generic_get_valid_access_token,
)

async def get_valid_access_token(user_id: str, knowledge_id: str) -> Optional[str]:
    return await _generic_get_valid_access_token(
        provider="provider_name",
        meta_key="provider_sync",
        user_id=user_id,
        knowledge_id=knowledge_id,
        refresh_fn=_refresh_token,
    )

async def _refresh_token(token_data: dict) -> Optional[dict]:
    """POST to provider's token endpoint with refresh_token grant."""
    # Provider-specific HTTP call
    ...
```

#### Step 5: Create the Sync Worker

**File**: `backend/open_webui/services/{provider}/sync_worker.py`

Subclass `BaseSyncWorker`. Implement all abstract properties and methods.

```python
from open_webui.services.sync.base_worker import BaseSyncWorker

class ProviderSyncWorker(BaseSyncWorker):

    # --- Properties ---
    @property
    def meta_key(self) -> str:
        return "provider_sync"

    @property
    def file_id_prefix(self) -> str:
        return "provider-"

    @property
    def event_prefix(self) -> str:
        return "provider"

    @property
    def internal_request_path(self) -> str:
        return "/internal/provider-sync"

    @property
    def max_files_config(self) -> int:
        return PROVIDER_MAX_FILES_PER_SYNC

    @property
    def source_clear_delta_keys(self) -> list[str]:
        return ["delta_link"]  # provider-specific delta tracking keys

    # --- Methods ---
    def _create_client(self):
        return ProviderClient(self.access_token, token_provider=self._token_provider)

    async def _close_client(self):
        if self._client:
            await self._client.close()

    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """Check extension, size, MIME type."""
        ...

    async def _collect_folder_files(self, source) -> tuple[List[Dict], int]:
        """List files from container. Return (files_to_process, deleted_count)."""
        ...

    async def _collect_single_file(self, source) -> Optional[Dict]:
        """Check if single file changed. Return file_info or None."""
        ...

    async def _download_file_content(self, file_info) -> bytes:
        """Download file bytes."""
        ...

    def _get_provider_storage_headers(self, item_id: str) -> dict:
        return {"OpenWebUI-Source": "provider_name", "OpenWebUI-Provider-Item-Id": item_id}

    def _get_provider_file_meta(self, item_id, source_item_id, relative_path, name, content_type, size, file_info=None) -> dict:
        return {
            "name": name,
            "content_type": content_type,
            "size": size,
            "source": "provider_name",
            "provider_item_id": item_id,
            "source_item_id": source_item_id,
            "relative_path": relative_path,
            "last_synced_at": int(time.time()),
        }

    async def _sync_permissions(self) -> None:
        """Map provider permissions to Open WebUI users. Optional."""
        pass  # Skip if provider doesn't have sharing model

    async def _verify_source_access(self, source) -> bool:
        """Check if user still has access to source."""
        ...

    async def _handle_revoked_source(self, source) -> int:
        """Remove files from revoked source. Return count."""
        ...
```

#### Step 6: Create the Provider Module

**File**: `backend/open_webui/services/{provider}/provider.py`

```python
from open_webui.services.sync.provider import SyncProvider, TokenManager

class ProviderTokenManager(TokenManager):
    async def get_valid_access_token(self, user_id, knowledge_id):
        from .token_refresh import get_valid_access_token
        return await get_valid_access_token(user_id, knowledge_id)

    def has_stored_token(self, user_id, knowledge_id):
        from .auth import get_stored_token
        return get_stored_token(user_id) is not None

    def delete_token(self, user_id, knowledge_id):
        from .auth import delete_stored_token
        return delete_stored_token(user_id)


class ProviderSyncProvider(SyncProvider):
    def __init__(self):
        self._token_manager = ProviderTokenManager()

    def get_provider_type(self) -> str:
        return "provider_name"

    def get_meta_key(self) -> str:
        return "provider_sync"

    def get_token_manager(self) -> TokenManager:
        return self._token_manager

    def create_worker(self, knowledge_id, sources, access_token, user_id, app, token_provider=None):
        from .sync_worker import ProviderSyncWorker
        return ProviderSyncWorker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=access_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
        )
```

#### Step 7: Create Thin Wrappers (Events + Scheduler)

**File**: `backend/open_webui/services/{provider}/sync_events.py`

```python
from open_webui.services.sync.events import (
    emit_file_processing as _emit_file_processing,
    emit_file_added as _emit_file_added,
    emit_sync_progress as _emit_sync_progress,
)

_PREFIX = "provider"

async def emit_sync_progress(user_id, knowledge_id, status, **kwargs):
    await _emit_sync_progress(_PREFIX, user_id, knowledge_id, status, **kwargs)
# ... same pattern for emit_file_processing, emit_file_added
```

**File**: `backend/open_webui/services/{provider}/scheduler.py`

```python
from open_webui.services.sync.scheduler import SyncScheduler
from open_webui.config import ENABLE_PROVIDER_SYNC, PROVIDER_SYNC_INTERVAL_MINUTES

_scheduler = SyncScheduler(
    provider_type="provider_name",
    meta_key="provider_sync",
    enable_config=ENABLE_PROVIDER_SYNC,
    interval_config=PROVIDER_SYNC_INTERVAL_MINUTES,
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
```

#### Step 8: Register in Factory Functions

**File**: `backend/open_webui/services/sync/provider.py`

Add to `get_sync_provider()`:

```python
elif provider_type == "provider_name":
    from open_webui.services.provider.provider import ProviderSyncProvider
    return ProviderSyncProvider()
```

Add to `get_token_manager()`:

```python
elif provider_type == "provider_name":
    from open_webui.services.provider.provider import ProviderTokenManager
    return ProviderTokenManager()
```

#### Step 9: Create the Router

**File**: `backend/open_webui/routers/{provider}_sync.py`

Follow the pattern from `onedrive_sync.py` or `google_drive_sync.py`:

1. Define constants: `_META_KEY`, `_PROVIDER_TYPE`, `_FILE_ID_PREFIX`, `_CLEAR_DELTA_KEYS`
2. Define `SyncItem` Pydantic model (provider-specific fields)
3. Create `APIRouter` with prefix `/api/v1/{provider}`
4. Delegate all endpoints to `services/sync/router.py` helper functions
5. Implement auth initiate and callback endpoints

#### Step 10: Register in main.py

**File**: `backend/open_webui/main.py`

```python
# Import router
from open_webui.routers.provider_sync import router as provider_sync_router

# Register router
app.include_router(provider_sync_router)

# Start scheduler in lifespan
from open_webui.services.provider.scheduler import start_scheduler as start_provider_scheduler, stop_scheduler as stop_provider_scheduler

# In lifespan startup:
start_provider_scheduler(app)

# In lifespan shutdown:
stop_provider_scheduler()
```

#### Step 11: Add Knowledge Type

**File**: `backend/open_webui/routers/knowledge.py`

Add `"provider_name"` to the allowed types list.

#### Step 12: Frontend (Minimal)

1. **API client**: Create `src/lib/apis/{provider}/index.ts` — copy from `googledrive/index.ts`, change URLs
2. **File picker**: Create `src/lib/utils/{provider}-file-picker.ts` — provider-specific picker UI
3. **KnowledgeBase.svelte**: Add provider case to sync handler, socket listeners, and UI conditionals
4. **TypeSelector.svelte**: Add provider option
5. **CreateKnowledgeBase.svelte**: Add provider to creation flow

### Effort Estimate per New Provider

| Component             | Files                 | Estimated Effort                          |
| --------------------- | --------------------- | ----------------------------------------- |
| API client            | 1                     | Medium (depends on API complexity)        |
| Auth module           | 1                     | Low-Medium (OAuth 2.0 is boilerplate)     |
| Token refresh         | 1                     | Low (mostly boilerplate)                  |
| Sync worker           | 1                     | Medium (file collection + download logic) |
| Provider + wrappers   | 3                     | Low (boilerplate)                         |
| Router                | 1                     | Low (delegates to shared helpers)         |
| Config + registration | 3 files modified      | Low                                       |
| Frontend              | 3-4                   | Medium (picker is the hard part)          |
| **Total**             | ~10 new + ~4 modified | **1-3 days** depending on API complexity  |

---

## Code References

- `backend/open_webui/services/sync/provider.py` — ABCs + factory functions
- `backend/open_webui/services/sync/base_worker.py` — Shared sync orchestration (~993 lines)
- `backend/open_webui/services/sync/router.py` — Shared endpoint handlers (13 functions)
- `backend/open_webui/services/sync/scheduler.py` — SyncScheduler class
- `backend/open_webui/services/sync/token_refresh.py` — Generic token refresh
- `backend/open_webui/services/sync/constants.py` — SUPPORTED_EXTENSIONS, CONTENT_TYPES, error types
- `backend/open_webui/services/sync/events.py` — Generic Socket.IO emitters
- `backend/open_webui/services/google_drive/` — Reference implementation (Google Drive)
- `backend/open_webui/services/onedrive/` — Reference implementation (OneDrive)
- `backend/open_webui/routers/google_drive_sync.py` — Reference router
- `backend/open_webui/routers/onedrive_sync.py` — Reference router

## Architecture Insights

1. **Template Method + Factory pattern** — `BaseSyncWorker.sync()` is the template method; provider factories create the concrete workers. This is the same pattern used elsewhere in the codebase (StorageProvider, VectorDBBase).

2. **Provider-specific code is minimal** — Google Drive's sync_worker.py is ~519 lines, OneDrive's is similar. The shared base_worker.py is ~993 lines. This means ~65% of sync logic is shared.

3. **Router delegation pattern** — Provider routers are thin and delegate to `sync/router.py` handler functions. This eliminates the most common source of copy-paste bugs (endpoint logic).

4. **Token lifecycle is cleanly separated** — The generic `token_refresh.py` handles the refresh-or-reauth decision; each provider only implements the HTTP POST to their token endpoint.

5. **Socket.IO events are prefixed** — `onedrive:sync:progress`, `googledrive:sync:progress`, etc. The frontend listens for provider-specific events. This allows multiple providers to sync simultaneously without event collision.

## Historical Context

- `thoughts/shared/plans/2026-03-24-cloud-sync-abstraction-refactor.md` — Detailed implementation plan for this refactor
- `thoughts/shared/plans/2026-02-04-typed-knowledge-bases.md` — Original typed KB feature plan
- `dev_notes/notes.md` — Gradient-DS custom features overview (documents the 9 custom features including OneDrive, typed KBs)

## Open Questions

1. **Dev's OneDrive fixes**: Need to audit exactly which fixes on dev's `onedrive/sync_worker.py` should be ported to `sync/base_worker.py` vs staying OneDrive-specific. This is the critical merge task.

2. **Frontend abstraction**: The current frontend duplicates some logic per provider in `KnowledgeBase.svelte`. The plan mentions a frontend refactor phase — is that in scope for this merge, or deferred?

3. **Push-based integrations**: `thoughts/shared/plans/2026-03-15-push-ingest-integration.md` and `thoughts/shared/research/2026-03-18-generic-push-interface-design.md` describe a push ingestion pattern. Should the sync abstraction accommodate webhook-triggered syncs, or is that a separate concern?

4. **SharePoint reuse**: If SharePoint is next, should it share the Microsoft Graph client with OneDrive, or be fully independent?
