# Cloud Sync Abstraction Refactor — Implementation Plan

## Overview

The Google Drive and OneDrive integrations are near-identical copy-pastes (~2700 lines backend, ~700 lines frontend duplicated). This refactor extracts shared logic into a common `services/sync/` layer and a generic frontend API client, reducing duplication by ~40-50% and making future providers (Dropbox, SharePoint, Box) trivial to add.

## Current State Analysis

### Backend Structure (per provider)
Each provider has 7 files under `services/{provider}/`:
- `provider.py` — SyncProvider/TokenManager implementation
- `sync_events.py` — Socket.IO event emitters (3 functions)
- `scheduler.py` — Background sync scheduler (~158 lines)
- `token_refresh.py` — OAuth token refresh (~133 lines)
- `auth.py` — OAuth authorization flow (~189-248 lines)
- `sync_worker.py` — File sync worker (~1336-1373 lines)
- `drive_client.py` / `graph_client.py` — API client wrapper

Plus a router: `routers/{provider}_sync.py` (~518-556 lines)

### Key Discoveries
- `services/sync/provider.py` already defines `SyncProvider` and `TokenManager` ABCs — good foundation
- `sync_events.py`: 100% identical logic, only event name prefix differs
- `scheduler.py`: 100% identical logic, only config vars and meta key differ
- `provider.py`: `execute_sync()` is identical except meta key and worker class
- `token_refresh.py`: `get_valid_access_token()` and `_mark_needs_reauth()` are identical; only `_refresh_token()` differs (different token URLs and POST bodies)
- `auth.py`: `_cleanup_expired_flows()`, `_generate_pkce()`, state validation, token post-processing are identical; URL construction and config vars differ. OneDrive has extra legacy migration code.
- `sync_worker.py`: 11 methods identical/near-identical, 7 provider-specific. Shared: `__init__`, `_make_request`, `_get_user`, `_check_cancelled`, `_update_sync_status`, `_get_content_type`, `_save_sources`, `_handle_deleted_item`, `_ensure_vectors_in_kb`, `_process_file_via_api`, `sync` orchestration
- Routers: All 7 endpoints are structurally identical (sync, status, cancel, remove source, list collections, auth initiate, token status, revoke)
- Frontend: `KnowledgeBase.svelte` has ~700 lines of duplicated handler/socket/template code

### What the existing abstraction provides
- `SyncProvider` ABC with `execute_sync()`, `get_provider_type()`, `get_token_manager()`
- `TokenManager` ABC with `get_valid_access_token()`, `has_stored_token()`, `delete_token()`
- `get_sync_provider(provider_type)` and `get_token_manager(provider_type)` factory functions

## Desired End State

After this refactor:
1. Each provider directory contains **only** provider-specific code (auth config, API client, file enumeration/download, permission structure parsing)
2. All shared logic lives in `services/sync/` and is parameterized by provider type
3. Adding a new provider requires implementing ~4 files (auth, API client, worker subclass, config) — no copy-paste of scheduler, events, router, or base worker logic
4. Frontend uses a single set of sync handler functions parameterized by provider type
5. All existing behavior is preserved — no user-facing changes

### Verification
- All existing sync operations (manual + background) work identically for both providers
- Socket events still reach the frontend with correct provider prefixes
- OAuth flows still work for both providers
- No regressions in file processing, cancellation, or error handling

## What We're NOT Doing

- Changing the `knowledge.meta` structure (keeping `onedrive_sync` / `google_drive_sync` keys)
- Changing Socket.IO event names (keeping `onedrive:*` / `googledrive:*` prefixes)
- Changing API URL paths (keeping `/api/v1/onedrive/` and `/api/v1/google-drive/`)
- Changing the file picker utilities (they are inherently different per provider)
- Merging `auth.py` into a single file (OAuth flows differ enough + OneDrive has legacy migration)
- Merging `drive_client.py` / `graph_client.py` (completely different APIs)
- Changing the database schema or migration
- Modifying the `OAuthSessions` model

## Implementation Approach

Bottom-up: start with leaf modules (constants, events), then work up to the orchestration layers (scheduler, router, worker base). Each phase is independently deployable and testable. Frontend refactor comes last since it's isolated from backend changes.

---

## Phase 1: Shared Constants and Sync Events

### Overview
Extract duplicated constants and the sync event emitter into `services/sync/`.

### Changes Required:

#### 1. Create `services/sync/constants.py`
**File**: `backend/open_webui/services/sync/constants.py` (new)

Move from both `sync_worker.py` files:
- `SyncErrorType` enum
- `FailedFile` dataclass
- `SUPPORTED_EXTENSIONS` set
- `CONTENT_TYPES` dict

```python
"""Shared constants for cloud sync providers."""

from dataclasses import dataclass
from enum import Enum


class SyncErrorType(str, Enum):
    TIMEOUT = "timeout"
    EMPTY_CONTENT = "empty_content"
    PROCESSING_ERROR = "processing_error"
    DOWNLOAD_ERROR = "download_error"


@dataclass
class FailedFile:
    filename: str
    error_type: str
    error_message: str


SUPPORTED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".html", ".htm", ".json", ".xml", ".csv",
}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".xml": "application/xml",
    ".csv": "text/csv",
}
```

#### 2. Create `services/sync/events.py`
**File**: `backend/open_webui/services/sync/events.py` (new)

Generic event emitter parameterized by `provider_prefix`:

```python
"""Generic Socket.IO event emitter for cloud sync progress."""

import logging
from typing import Optional, List, Dict, Any

log = logging.getLogger(__name__)


async def emit_file_processing(
    provider_prefix: str,
    user_id: str,
    knowledge_id: str,
    file_info: Dict[str, Any],
):
    try:
        from open_webui.socket.main import sio
        await sio.emit(
            f"{provider_prefix}:file:processing",
            {"knowledge_id": knowledge_id, "file": file_info},
            room=f"user:{user_id}",
        )
    except Exception as e:
        log.debug(f"Failed to emit file processing event: {e}")


async def emit_file_added(
    provider_prefix: str,
    user_id: str,
    knowledge_id: str,
    file_data: Dict[str, Any],
):
    try:
        from open_webui.socket.main import sio
        await sio.emit(
            f"{provider_prefix}:file:added",
            {"knowledge_id": knowledge_id, "file": file_data},
            room=f"user:{user_id}",
        )
    except Exception as e:
        log.debug(f"Failed to emit file added event: {e}")


async def emit_sync_progress(
    provider_prefix: str,
    user_id: str,
    knowledge_id: str,
    status: str,
    current: int = 0,
    total: int = 0,
    filename: str = "",
    error: Optional[str] = None,
    files_processed: int = 0,
    files_failed: int = 0,
    deleted_count: int = 0,
    failed_files: Optional[List[Dict]] = None,
):
    try:
        from open_webui.socket.main import sio
        await sio.emit(
            f"{provider_prefix}:sync:progress",
            {
                "knowledge_id": knowledge_id,
                "status": status,
                "current": current,
                "total": total,
                "filename": filename,
                "error": error,
                "files_processed": files_processed,
                "files_failed": files_failed,
                "deleted_count": deleted_count,
                "failed_files": failed_files,
            },
            room=f"user:{user_id}",
        )
    except Exception as e:
        log.debug(f"Failed to emit sync progress event: {e}")
```

#### 3. Update both provider `sync_events.py` to delegate
**Files**: `services/google_drive/sync_events.py`, `services/onedrive/sync_events.py`

Replace each function body with a delegation call to the shared module:

```python
# google_drive/sync_events.py
from open_webui.services.sync.events import (
    emit_file_processing as _emit_file_processing,
    emit_file_added as _emit_file_added,
    emit_sync_progress as _emit_sync_progress,
)

_PREFIX = "googledrive"

async def emit_file_processing(user_id, knowledge_id, file_info):
    await _emit_file_processing(_PREFIX, user_id, knowledge_id, file_info)

async def emit_file_added(user_id, knowledge_id, file_data):
    await _emit_file_added(_PREFIX, user_id, knowledge_id, file_data)

async def emit_sync_progress(user_id, knowledge_id, status, **kwargs):
    await _emit_sync_progress(_PREFIX, user_id, knowledge_id, status, **kwargs)
```

Same pattern for OneDrive with `_PREFIX = "onedrive"`.

This preserves the existing import signatures so no callers need to change.

#### 4. Update both `sync_worker.py` to import from shared constants
**Files**: `services/google_drive/sync_worker.py`, `services/onedrive/sync_worker.py`

Replace local definitions with imports:
```python
from open_webui.services.sync.constants import (
    SyncErrorType, FailedFile, SUPPORTED_EXTENSIONS, CONTENT_TYPES,
)
```

Remove the local `SyncErrorType`, `FailedFile`, `SUPPORTED_EXTENSIONS`, `CONTENT_TYPES` definitions.

### Success Criteria:

#### Automated Verification:
- [ ] Python imports resolve: `python -c "from open_webui.services.sync.constants import SyncErrorType, SUPPORTED_EXTENSIONS"`
- [ ] Python imports resolve: `python -c "from open_webui.services.sync.events import emit_sync_progress"`
- [ ] Existing provider imports still work: `python -c "from open_webui.services.google_drive.sync_events import emit_sync_progress"`
- [ ] Existing provider imports still work: `python -c "from open_webui.services.onedrive.sync_events import emit_sync_progress"`
- [ ] No import errors on app startup

#### Manual Verification:
- [ ] Trigger a sync for both providers — socket events arrive at frontend unchanged

---

## Phase 2: Generic Scheduler

### Overview
Extract the scheduler into a reusable class in `services/sync/scheduler.py`, parameterized by provider type and config.

### Changes Required:

#### 1. Create `services/sync/scheduler.py`
**File**: `backend/open_webui/services/sync/scheduler.py` (new)

```python
"""Generic background sync scheduler for cloud providers."""

import asyncio
import time
import logging
from typing import Optional, Callable

from open_webui.models.knowledge import Knowledges, KnowledgeModel

log = logging.getLogger(__name__)


class SyncScheduler:
    """Background sync scheduler, parameterized by provider config."""

    def __init__(
        self,
        provider_type: str,        # e.g. "onedrive", "google_drive"
        meta_key: str,             # e.g. "onedrive_sync", "google_drive_sync"
        enable_config,             # PersistentConfig with .value -> bool
        interval_config,           # PersistentConfig with .value -> int (minutes)
    ):
        self.provider_type = provider_type
        self.meta_key = meta_key
        self.enable_config = enable_config
        self.interval_config = interval_config
        self._task: Optional[asyncio.Task] = None
        self._app = None

    def start(self, app):
        if not self.enable_config.value:
            log.info("%s sync disabled, scheduler not started", self.provider_type)
            return
        self._app = app
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            log.info("%s background sync scheduler started", self.provider_type)

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            log.info("%s background sync scheduler stopped", self.provider_type)
        self._task = None

    async def _run(self):
        interval_seconds = self.interval_config.value * 60
        await asyncio.sleep(interval_seconds)
        while True:
            try:
                await self._execute_due_syncs()
            except asyncio.CancelledError:
                log.info("Scheduler cancelled")
                return
            except Exception:
                log.exception("Error in scheduler loop")
            await asyncio.sleep(interval_seconds)

    async def _execute_due_syncs(self):
        from open_webui.services.sync.provider import get_sync_provider

        kbs = Knowledges.get_knowledge_bases_by_type(self.provider_type)
        if not kbs:
            return

        now = time.time()
        interval_seconds = self.interval_config.value * 60
        provider = get_sync_provider(self.provider_type)

        for kb in kbs:
            if not self._is_sync_due(kb, now, interval_seconds, provider):
                continue
            log.info("Starting scheduled sync for KB %s (%s)", kb.id, kb.name)
            try:
                self._update_sync_status(kb.id, "syncing")
                result = await provider.execute_sync(
                    knowledge_id=kb.id, user_id=kb.user_id, app=self._app,
                )
                if result.get("error"):
                    if result.get("needs_reauth"):
                        log.warning("KB %s needs re-authorization", kb.id)
                    else:
                        log.error("Scheduled sync failed for KB %s: %s", kb.id, result["error"])
                        self._update_sync_status(kb.id, "failed", error=result["error"])
                else:
                    log.info("Scheduled sync completed for KB %s: %d files processed",
                             kb.id, result.get("files_processed", 0))
            except Exception:
                log.exception("Unexpected error during scheduled sync of KB %s", kb.id)
                self._update_sync_status(kb.id, "failed", error="Unexpected scheduler error")

    def _is_sync_due(self, kb: KnowledgeModel, now: float, interval_seconds: float, sync_provider=None) -> bool:
        meta = kb.meta or {}
        sync_info = meta.get(self.meta_key, {})
        if not sync_info.get("sources"):
            return False
        if sync_provider and not sync_provider.get_token_manager().has_stored_token(kb.user_id, kb.id):
            return False
        if sync_info.get("needs_reauth"):
            return False
        status = sync_info.get("status", "idle")
        if status == "syncing":
            sync_started = sync_info.get("sync_started_at")
            stale_threshold = 30 * 60
            is_stale = not sync_started or (now - sync_started) > stale_threshold
            if is_stale:
                log.warning("Stale sync detected for KB %s (started_at=%s), allowing re-sync", kb.id, sync_started)
            else:
                return False
        last_sync = sync_info.get("last_sync_at", 0)
        return (now - last_sync) >= interval_seconds

    def _update_sync_status(self, knowledge_id: str, status: str, error: str = None):
        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
        if not knowledge:
            return
        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})
        sync_info["status"] = status
        if status == "syncing":
            sync_info["sync_started_at"] = int(time.time())
        if error:
            sync_info["error"] = error
        meta[self.meta_key] = sync_info
        Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
```

#### 2. Replace provider schedulers with instances
**File**: `services/google_drive/scheduler.py`

Replace the entire file with:
```python
"""Google Drive Background Sync Scheduler."""

from open_webui.services.sync.scheduler import SyncScheduler
from open_webui.config import ENABLE_GOOGLE_DRIVE_SYNC, GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES

_scheduler = SyncScheduler(
    provider_type="google_drive",
    meta_key="google_drive_sync",
    enable_config=ENABLE_GOOGLE_DRIVE_SYNC,
    interval_config=GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES,
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
```

Same pattern for `services/onedrive/scheduler.py` with OneDrive config.

This preserves the `start_scheduler(app)` / `stop_scheduler()` API that `main.py` calls.

### Success Criteria:

#### Automated Verification:
- [ ] `python -c "from open_webui.services.sync.scheduler import SyncScheduler"`
- [ ] `python -c "from open_webui.services.google_drive.scheduler import start_scheduler, stop_scheduler"`
- [ ] `python -c "from open_webui.services.onedrive.scheduler import start_scheduler, stop_scheduler"`
- [ ] No import errors on app startup

#### Manual Verification:
- [ ] Background sync triggers correctly for both providers after the configured interval
- [ ] Stale sync recovery works (set a sync status to "syncing" with old timestamp, verify scheduler re-triggers)

---

## Phase 3: Generic Token Refresh

### Overview
Extract the shared token refresh flow into a base in `services/sync/token_refresh.py`. The `_refresh_token()` HTTP call stays provider-specific.

### Changes Required:

#### 1. Create `services/sync/token_refresh.py`
**File**: `backend/open_webui/services/sync/token_refresh.py` (new)

```python
"""Generic OAuth token refresh logic for cloud sync providers."""

import time
import logging
from typing import Optional, Callable, Awaitable, Dict, Any

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)

_REFRESH_BUFFER_SECONDS = 300  # 5 minutes


async def get_valid_access_token(
    provider: str,
    meta_key: str,
    user_id: str,
    knowledge_id: str,
    refresh_fn: Callable[[dict], Awaitable[Optional[dict]]],
) -> Optional[str]:
    """
    Get a valid access token, refreshing if needed.

    Args:
        provider: OAuth provider string (e.g. "google_drive", "onedrive")
        meta_key: Knowledge meta key (e.g. "google_drive_sync", "onedrive_sync")
        user_id: User ID
        knowledge_id: Knowledge base ID
        refresh_fn: Provider-specific function to refresh a token dict
    """
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return None

    token_data = session.token
    expires_at = token_data.get("expires_at", 0)

    if time.time() + _REFRESH_BUFFER_SECONDS < expires_at:
        return token_data.get("access_token")

    log.info("Refreshing token for user %s, KB %s", user_id, knowledge_id)
    new_token_data = await refresh_fn(token_data)

    if new_token_data is None:
        log.warning("Token refresh failed for user %s, KB %s -- marking as needs_reauth", user_id, knowledge_id)
        _mark_needs_reauth(provider, meta_key, user_id)
        return None

    OAuthSessions.update_session_by_id(session.id, new_token_data)
    return new_token_data.get("access_token")


def _mark_needs_reauth(provider_type: str, meta_key: str, user_id: str):
    """Mark all knowledge bases of this provider type for a user as needing re-auth."""
    kbs = Knowledges.get_knowledge_bases_by_type(provider_type)
    for kb in kbs:
        if kb.user_id != user_id:
            continue
        meta = kb.meta or {}
        sync_info = meta.get(meta_key, {})
        sync_info["needs_reauth"] = True
        sync_info["has_stored_token"] = False
        meta[meta_key] = sync_info
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)
```

#### 2. Update provider `token_refresh.py` files
Each provider keeps only its `_refresh_token()` implementation and delegates the shared flow:

**File**: `services/google_drive/token_refresh.py`
```python
"""Google Drive Token Refresh Service."""
import time
import logging
from typing import Optional
import httpx

from open_webui.config import GOOGLE_DRIVE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from open_webui.services.sync.token_refresh import (
    get_valid_access_token as _generic_get_valid_access_token,
)

log = logging.getLogger(__name__)
_TOKEN_URL = "https://oauth2.googleapis.com/token"


async def get_valid_access_token(user_id: str, knowledge_id: str) -> Optional[str]:
    return await _generic_get_valid_access_token(
        provider="google_drive",
        meta_key="google_drive_sync",
        user_id=user_id,
        knowledge_id=knowledge_id,
        refresh_fn=_refresh_token,
    )


async def _refresh_token(token_data: dict) -> Optional[dict]:
    # ... existing Google-specific implementation unchanged ...
```

Same pattern for OneDrive, keeping its tenant-aware `_refresh_token()`.

### Success Criteria:

#### Automated Verification:
- [ ] `python -c "from open_webui.services.sync.token_refresh import get_valid_access_token"`
- [ ] Existing imports still work for both providers
- [ ] No import errors on app startup

#### Manual Verification:
- [ ] Background sync correctly refreshes an expired Google token
- [ ] Background sync correctly refreshes an expired OneDrive token
- [ ] Token revocation is detected and `needs_reauth` is set for both providers

---

## Phase 4: Enhanced Provider Base Class

### Overview
Push the shared `execute_sync()` logic from both provider.py files into `SyncProvider` as a concrete method, leaving only a few abstract properties for subclasses.

### Changes Required:

#### 1. Update `services/sync/provider.py`
Add concrete `execute_sync()` to `SyncProvider`, using abstract properties:

```python
class SyncProvider(ABC):

    @abstractmethod
    def get_provider_type(self) -> str: ...

    @abstractmethod
    def get_meta_key(self) -> str:
        """Return the knowledge meta key (e.g. 'onedrive_sync')."""
        ...

    @abstractmethod
    def get_token_manager(self) -> TokenManager: ...

    @abstractmethod
    def create_worker(self, knowledge_id, sources, access_token, user_id, app, token_provider):
        """Create the provider-specific sync worker instance."""
        ...

    async def execute_sync(
        self, knowledge_id, user_id, app, access_token=None,
    ) -> Dict[str, Any]:
        """Execute sync -- shared logic, delegates to create_worker()."""
        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
        if not knowledge:
            return {"error": "Knowledge base not found"}

        meta = knowledge.meta or {}
        sync_info = meta.get(self.get_meta_key(), {})
        sources = sync_info.get("sources", [])
        if not sources:
            return {"error": "No sync sources configured"}

        token_provider = None
        if access_token:
            effective_token = access_token
        else:
            effective_token = await self.get_token_manager().get_valid_access_token(
                user_id, knowledge_id
            )
            if not effective_token:
                return {"error": "No valid token available", "needs_reauth": True}
            tm = self.get_token_manager()
            async def _refresh():
                return await tm.get_valid_access_token(user_id, knowledge_id)
            token_provider = _refresh

        worker = self.create_worker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=effective_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
        )
        return await worker.sync()
```

#### 2. Simplify both provider.py files
Each provider reduces to:

```python
class GoogleDriveSyncProvider(SyncProvider):
    def __init__(self):
        self._token_manager = GoogleDriveTokenManager()

    def get_provider_type(self) -> str:
        return "google_drive"

    def get_meta_key(self) -> str:
        return "google_drive_sync"

    def get_token_manager(self) -> TokenManager:
        return self._token_manager

    def create_worker(self, **kwargs):
        from open_webui.services.google_drive.sync_worker import GoogleDriveSyncWorker
        return GoogleDriveSyncWorker(**kwargs)
```

The `execute_sync()` override is removed entirely.

### Success Criteria:

#### Automated Verification:
- [ ] `python -c "from open_webui.services.sync.provider import get_sync_provider; p = get_sync_provider('onedrive'); print(p.get_meta_key())"`
- [ ] No import errors on app startup

#### Manual Verification:
- [ ] Manual sync via the UI works for both providers
- [ ] Background sync works for both providers

---

## Phase 5: Generic Sync Router

### Overview
Extract the shared router endpoint logic into a factory or base module. Each provider's router becomes a thin configuration layer.

### Changes Required:

#### 1. Create `services/sync/router.py`
**File**: `backend/open_webui/services/sync/router.py` (new)

A factory function that creates a FastAPI `APIRouter` with all shared endpoints, parameterized by provider config:

```python
"""Generic sync router factory for cloud providers."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from starlette.requests import Request
from pydantic import BaseModel
from typing import Optional, List, Callable, Awaitable
import time
import logging
import json

from starlette.responses import HTMLResponse
from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel
from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)


class FailedFileInfo(BaseModel):
    filename: str
    error_type: str
    error_message: str


class SyncStatusResponse(BaseModel):
    knowledge_id: str
    status: str
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    last_sync_at: Optional[int] = None
    error: Optional[str] = None
    source_count: Optional[int] = None
    failed_files: Optional[List[FailedFileInfo]] = None


class RemoveSourceRequest(BaseModel):
    item_id: str


def create_sync_router(
    provider_type: str,           # "google_drive" or "onedrive"
    meta_key: str,                # "google_drive_sync" or "onedrive_sync"
    file_id_prefix: str,          # "googledrive-" or "onedrive-"
    sync_items_model: type,       # Provider-specific SyncItemsRequest Pydantic model
    create_worker_fn: Callable,   # async fn(knowledge_id, sources, access_token, user_id, app) -> worker
    get_auth_url_fn: Callable,    # fn(user_id, knowledge_id, redirect_uri) -> str
    client_secret_config,         # Config value to check
    oauth_redirect_path: str,     # e.g. "/oauth/google/callback"
    remove_files_fn: Callable,    # fn(knowledge_id, source_item_id, source) -> int
    extract_sources_fn: Callable, # fn(request_items) -> List[dict]  -- convert SyncItem models to source dicts
    clear_delta_state_fn: Callable,  # fn(source) -> None  -- clear provider-specific delta state on a source
) -> APIRouter:
    """Create a sync router with all standard endpoints."""
    router = APIRouter()

    # ... all shared endpoints defined here, using the parameters above ...
    # (sync/items, sync/{id}, sync/{id}/cancel, sync/{id}/sources/remove,
    #  synced-collections, auth/initiate, auth/token-status/{id}, auth/revoke/{id})

    return router
```

**Note**: The exact implementation will follow the existing endpoint patterns from both routers. The key parameterization points are:
- `meta_key` for reading/writing knowledge meta
- `file_id_prefix` for file matching in `_remove_files_for_source`
- `sync_items_model` since OneDrive includes `drive_id` in its SyncItem
- `extract_sources_fn` to convert provider-specific SyncItem models to source dicts
- `clear_delta_state_fn` to clear `page_token` vs `delta_link` on cancellation
- `remove_files_fn` to handle the slightly different file matching logic (OneDrive has legacy `drive_id` fallback)

#### 2. Simplify both provider routers
Each router becomes a thin configuration file that calls `create_sync_router()`:

**File**: `routers/google_drive_sync.py`
```python
from open_webui.services.sync.router import create_sync_router
# ... provider-specific imports and models ...

class SyncItem(BaseModel):
    type: Literal["file", "folder"]
    item_id: str
    item_path: str
    name: str

class SyncItemsRequest(BaseModel):
    knowledge_id: str
    items: List[SyncItem]
    access_token: str

def _extract_sources(items):
    return [{"type": i.type, "item_id": i.item_id, "item_path": i.item_path, "name": i.name} for i in items]

def _clear_delta_state(source):
    source.pop("page_token", None)
    source.pop("folder_map", None)

router = create_sync_router(
    provider_type="google_drive",
    meta_key="google_drive_sync",
    file_id_prefix="googledrive-",
    sync_items_model=SyncItemsRequest,
    # ... etc
)

# Keep the auth callback handler (called from main.py)
async def handle_google_drive_auth_callback(request):
    # ... existing implementation ...
```

The `handle_*_auth_callback` functions stay in their respective router files since they're called by name from `main.py`.

### Success Criteria:

#### Automated Verification:
- [ ] `python -c "from open_webui.services.sync.router import create_sync_router"`
- [ ] All API endpoints respond correctly: test each endpoint with curl/httpie
- [ ] No import errors on app startup

#### Manual Verification:
- [ ] Full sync flow works via UI for both providers (start, monitor, complete)
- [ ] Cancel sync works for both providers
- [ ] Remove source works for both providers
- [ ] OAuth background sync authorization flow works for both providers

**Implementation Note**: This is the highest-risk phase due to the number of endpoints. After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 6: Sync Worker Base Class

### Overview
Extract the shared file processing pipeline into a `BaseSyncWorker` class. This is the largest single change but also the highest duplication reduction (~11 shared methods).

### Changes Required:

#### 1. Create `services/sync/base_worker.py`
**File**: `backend/open_webui/services/sync/base_worker.py` (new)

Base class containing all shared methods:

```python
"""Base sync worker with shared file processing pipeline."""

class BaseSyncWorker(ABC):
    """Base class for cloud sync workers.

    Subclasses must implement:
    - provider_type (property) -> str
    - meta_key (property) -> str
    - file_id_prefix (property) -> str
    - event_prefix (property) -> str
    - internal_request_path (property) -> str
    - max_files_config (property) -> int
    - max_file_size_mb_config (property) -> int
    - _create_client() -> client instance
    - _is_supported_file(item) -> bool
    - _collect_folder_files(source) -> (files_to_process, deleted_item_ids)
    - _collect_single_file(source) -> (files_to_process, deleted_item_ids)
    - _download_file_content(file_info) -> bytes
    - _get_file_metadata(file_info) -> dict  (provider-specific metadata fields)
    - _sync_permissions(sources) -> None
    - _verify_source_access(source) -> bool
    - _get_source_clear_delta_keys() -> list[str]  (keys to pop on cancellation)
    """

    def __init__(self, knowledge_id, sources, access_token, user_id, app, token_provider=None):
        # ... shared init ...

    # Shared methods moved here verbatim:
    # _make_request, _get_user, _check_cancelled, _update_sync_status,
    # _get_content_type, _save_sources, _handle_deleted_item,
    # _ensure_vectors_in_kb, _process_file_via_api,
    # _process_file_info (with abstract hooks for download + metadata),
    # sync (main orchestration, with abstract hooks for collect/verify/permissions)
```

Key design decisions:
- `_update_sync_status` uses `self.meta_key` and `self.event_prefix` instead of hardcoded strings
- `_process_file_info` calls `self._download_file_content(file_info)` and `self._get_file_metadata(file_info)` — abstract methods that each provider implements
- `_handle_deleted_item` uses `self.file_id_prefix`
- `sync()` calls abstract `_collect_folder_files()` / `_collect_single_file()` / `_sync_permissions()` / `_verify_source_access()`
- Cancellation cleanup uses `self._get_source_clear_delta_keys()` to know which keys to pop

#### 2. Refactor `google_drive/sync_worker.py`
Keep only Google-specific methods:
- `_is_workspace_file`, `_get_effective_filename`
- `_is_supported_file` (Google Workspace MIME type handling)
- `_collect_folder_files`, `_collect_folder_files_full`, `_collect_folder_files_incremental`, `_is_in_folder_tree`
- `_collect_single_file` (md5/modifiedTime based change detection)
- `_download_file_content` (export vs download dispatch)
- `_get_file_metadata` (google_drive-specific meta fields)
- `_sync_permissions` (Google permission structure)
- `_verify_source_access` (single-arg API call)
- `_handle_revoked_source` (single-field matching)
- Properties: `provider_type="google_drive"`, `meta_key="google_drive_sync"`, `file_id_prefix="googledrive-"`, `event_prefix="googledrive"`, etc.

#### 3. Refactor `onedrive/sync_worker.py`
Keep only OneDrive-specific methods:
- `_is_supported_file` (folder key check, no Workspace types)
- `_collect_folder_files` (delta API with folder_map multi-pass)
- `_collect_single_file` (sha256/quickXor hash based change detection)
- `_download_file_content` (direct download)
- `_get_file_metadata` (onedrive-specific meta fields including drive_id)
- `_sync_permissions` (Graph API permission structure with grantedTo/grantedToIdentities)
- `_verify_source_access` (two-arg API call)
- `_handle_revoked_source` (two-tier matching with legacy fallback)
- Properties: `provider_type="onedrive"`, `meta_key="onedrive_sync"`, `file_id_prefix="onedrive-"`, `event_prefix="onedrive"`, etc.

### Success Criteria:

#### Automated Verification:
- [ ] `python -c "from open_webui.services.sync.base_worker import BaseSyncWorker"`
- [ ] `python -c "from open_webui.services.google_drive.sync_worker import GoogleDriveSyncWorker"`
- [ ] `python -c "from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker"`
- [ ] No import errors on app startup

#### Manual Verification:
- [ ] Full sync from empty state works for both providers (all files downloaded and processed)
- [ ] Incremental sync works (add a file to the synced folder, trigger resync, only new file processed)
- [ ] File deletion detection works (delete a file from source, resync, file removed from KB)
- [ ] Cancellation mid-sync works for both providers
- [ ] Large folder sync (50+ files) processes correctly with progress events
- [ ] Permission sync correctly applies access control
- [ ] Revoked source access is detected and handled

**Implementation Note**: This is the largest phase. After completing this phase and all automated verification passes, pause here for thorough manual testing before proceeding to frontend changes.

---

## Phase 7: Frontend API Client Factory

### Overview
Create a generic sync API client factory to eliminate duplication across the two API modules.

### Changes Required:

#### 1. Create `src/lib/apis/sync/index.ts`
**File**: `src/lib/apis/sync/index.ts` (new)

```typescript
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type SyncErrorType = 'timeout' | 'empty_content' | 'processing_error' | 'download_error';

export interface FailedFile {
    filename: string;
    error_type: SyncErrorType;
    error_message: string;
}

export interface SyncStatusResponse {
    knowledge_id: string;
    status: 'idle' | 'syncing' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled';
    progress_current?: number;
    progress_total?: number;
    last_sync_at?: number;
    error?: string;
    source_count?: number;
    failed_files?: FailedFile[];
}

export interface TokenStatusResponse {
    has_token: boolean;
    is_expired?: boolean;
    needs_reauth?: boolean;
    token_stored_at?: number;
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
    const res = await fetch(url, init);
    if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(error.detail || `Request failed: ${res.status}`);
    }
    return res.json();
}

export function createSyncApi(basePath: string) {
    const base = `${WEBUI_API_BASE_URL}/${basePath}`;

    return {
        startSyncItems(token: string, request: Record<string, unknown>) {
            return apiFetch<{ message: string; knowledge_id: string }>(`${base}/sync/items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify(request),
            });
        },

        getSyncStatus(token: string, knowledgeId: string) {
            return apiFetch<SyncStatusResponse>(`${base}/sync/${knowledgeId}`, {
                headers: { Authorization: `Bearer ${token}` },
            });
        },

        cancelSync(token: string, knowledgeId: string) {
            return apiFetch<{ message: string; knowledge_id: string }>(`${base}/sync/${knowledgeId}/cancel`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
            });
        },

        getSyncedCollections(token: string) {
            return apiFetch<Array<{ id: string; name: string; sync_info: Record<string, unknown> }>>(`${base}/synced-collections`, {
                headers: { Authorization: `Bearer ${token}` },
            });
        },

        getTokenStatus(token: string, knowledgeId: string) {
            return apiFetch<TokenStatusResponse>(`${base}/auth/token-status/${knowledgeId}`, {
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            });
        },

        removeSource(token: string, knowledgeId: string, itemId: string) {
            return apiFetch<{ message: string; source_name: string; files_removed: number }>(
                `${base}/sync/${knowledgeId}/sources/remove`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                    body: JSON.stringify({ item_id: itemId }),
                }
            );
        },

        revokeToken(token: string, knowledgeId: string) {
            return apiFetch<{ revoked: boolean }>(`${base}/auth/revoke/${knowledgeId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            });
        },
    };
}
```

#### 2. Update provider API modules to use the factory

**File**: `src/lib/apis/googledrive/index.ts`
```typescript
import { createSyncApi } from '$lib/apis/sync';
export type { SyncStatusResponse, FailedFile, TokenStatusResponse, SyncErrorType } from '$lib/apis/sync';

const api = createSyncApi('google-drive');

export const startGoogleDriveSyncItems = api.startSyncItems;
export const getSyncStatus = api.getSyncStatus;
export const cancelSync = api.cancelSync;
export const getSyncedCollections = api.getSyncedCollections;
export const getTokenStatus = api.getTokenStatus;
export const removeSource = api.removeSource;
export const revokeToken = api.revokeToken;

// Provider-specific type (no drive_id)
export interface SyncItem { type: 'file' | 'folder'; item_id: string; item_path: string; name: string; }
export interface SyncItemsRequest { knowledge_id: string; items: SyncItem[]; access_token: string; }
```

Same pattern for `src/lib/apis/onedrive/index.ts`, keeping OneDrive-specific types (`drive_id`, `user_token`).

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles without errors
- [ ] No broken imports in Svelte components

#### Manual Verification:
- [ ] All sync operations still work via the UI for both providers

---

## Phase 8: Frontend KnowledgeBase.svelte Refactor

### Overview
Replace the duplicated handler/socket/template code in `KnowledgeBase.svelte` with provider-agnostic functions parameterized by type. This is the biggest frontend change.

### Changes Required:

#### 1. Create a provider config helper
**File**: Within `KnowledgeBase.svelte` (or a new `src/lib/utils/sync-providers.ts`)

Define a provider config object:
```typescript
interface SyncProviderConfig {
    type: string;                    // "onedrive" | "google_drive"
    metaKey: string;                 // "onedrive_sync" | "google_drive_sync"
    eventPrefix: string;             // "onedrive" | "googledrive"
    fileIdPrefix: string;            // "onedrive-" | "googledrive-"
    sourceMetaField: string;         // "onedrive" | "google_drive" (for file.meta.source)
    label: string;                   // "OneDrive" | "Google Drive"
    api: ReturnType<typeof createSyncApi>;
    openPicker: () => Promise<{items, accessToken}>;
    startSyncParam: string;          // "start_onedrive_sync" | "start_google_drive_sync"
}
```

#### 2. Unify state variables
Replace the 12 provider-specific state variables with a single reactive map:
```typescript
let syncState: Record<string, {
    isSyncing: boolean;
    isCancelling: boolean;
    syncStatus: SyncStatusResponse | null;
    bgSyncAuthorized: boolean;
    bgSyncNeedsReauth: boolean;
}> = {};
```

#### 3. Unify handler functions
Replace the 12 duplicate handler functions with 6 generic ones that take a `providerConfig` parameter:
- `syncHandler(config)` — replaces `oneDriveSyncHandler` + `googleDriveSyncHandler`
- `resyncHandler(config)` — replaces `oneDriveResyncHandler` + `googleDriveResyncHandler`
- `pollSyncStatus(config)` — replaces `pollOneDriveSyncStatus` + `pollGoogleDriveSyncStatus`
- `cancelSyncHandler(config)` — replaces `cancelOneDriveSyncHandler` + `cancelGoogleDriveSyncHandler`
- `authorizeBackgroundSync(config)` — replaces both authorize functions
- `removeSourceHandler(config, itemId)` — replaces both remove source functions

#### 4. Unify socket event handlers
Replace the 6 duplicate socket handlers with 3 generic ones:
- `handleFileProcessing(providerType, data)` — handles `*:file:processing`
- `handleFileAdded(providerType, data)` — handles `*:file:added`
- `handleSyncProgress(providerType, data)` — handles `*:sync:progress`

Register them dynamically based on configured providers:
```typescript
for (const config of enabledProviders) {
    $socket?.on(`${config.eventPrefix}:sync:progress`, (data) => handleSyncProgress(config.type, data));
    // ... etc
}
```

#### 5. Simplify template branching
Replace the many `{#if knowledge?.type === 'onedrive'}` / `{:else if knowledge?.type === 'google_drive'}` blocks with a single `{#if activeProvider}` block that uses the provider config.

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript/Svelte compiles without errors
- [ ] No broken imports

#### Manual Verification:
- [ ] Full sync flow via UI works for both providers
- [ ] Socket progress events display correctly
- [ ] Background sync authorization works for both
- [ ] Cancel sync works for both
- [ ] Remove source works for both
- [ ] Empty state cards render correctly for both providers
- [ ] Source badges display correctly in file lists
- [ ] Chat message input file picker works for both providers

**Implementation Note**: This phase touches the most user-facing code. After completing this phase, do a full manual regression test of all sync functionality for both providers.

---

## Testing Strategy

### Per-Phase Testing
Each phase preserves existing function signatures where possible, so existing behavior should be maintained. Test after each phase before moving on.

### Integration Tests
- Start a sync, cancel it, verify state is "cancelled"
- Start a sync, wait for completion, verify files in KB
- Revoke OAuth token, verify background sync marks `needs_reauth`
- Add a new source to an existing synced KB
- Remove a source from a synced KB

### Manual Testing Steps
1. Create a new Google Drive KB, pick a folder, sync to completion
2. Create a new OneDrive KB, pick a folder, sync to completion
3. Add a file to the source folder, trigger resync, verify only new file synced
4. Delete a file from source, resync, verify file removed from KB
5. Authorize background sync for both providers
6. Cancel a sync mid-progress, verify clean state
7. Remove a source, verify files cleaned up
8. Test with a folder containing 50+ files to verify progress events

## Performance Considerations

- No performance impact expected — this is a pure refactor with no behavioral changes
- The generic scheduler, events, and router add one level of function indirection — negligible overhead
- The base worker class uses standard inheritance — no additional overhead vs the current direct implementation

## Migration Notes

- No database migration required
- No API changes (URLs, request/response shapes all preserved)
- No Socket.IO event name changes
- Backward compatible: if any downstream code imports directly from provider modules, the thin wrappers preserve those import paths

## File Change Summary

### New Files
- `backend/open_webui/services/sync/constants.py`
- `backend/open_webui/services/sync/events.py`
- `backend/open_webui/services/sync/scheduler.py`
- `backend/open_webui/services/sync/token_refresh.py`
- `backend/open_webui/services/sync/router.py`
- `backend/open_webui/services/sync/base_worker.py`
- `src/lib/apis/sync/index.ts`

### Modified Files (reduced to thin wrappers/subclasses)
- `backend/open_webui/services/google_drive/sync_events.py` (thin wrapper)
- `backend/open_webui/services/onedrive/sync_events.py` (thin wrapper)
- `backend/open_webui/services/google_drive/scheduler.py` (instance creation)
- `backend/open_webui/services/onedrive/scheduler.py` (instance creation)
- `backend/open_webui/services/google_drive/token_refresh.py` (delegation + provider-specific refresh)
- `backend/open_webui/services/onedrive/token_refresh.py` (delegation + provider-specific refresh)
- `backend/open_webui/services/sync/provider.py` (concrete execute_sync + new abstract methods)
- `backend/open_webui/services/google_drive/provider.py` (simplified)
- `backend/open_webui/services/onedrive/provider.py` (simplified)
- `backend/open_webui/routers/google_drive_sync.py` (factory + provider config)
- `backend/open_webui/routers/onedrive_sync.py` (factory + provider config)
- `backend/open_webui/services/google_drive/sync_worker.py` (subclass of BaseSyncWorker)
- `backend/open_webui/services/onedrive/sync_worker.py` (subclass of BaseSyncWorker)
- `src/lib/apis/googledrive/index.ts` (re-exports from factory)
- `src/lib/apis/onedrive/index.ts` (re-exports from factory)
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte` (unified handlers)

### Unchanged Files
- `backend/open_webui/services/google_drive/auth.py`
- `backend/open_webui/services/onedrive/auth.py`
- `backend/open_webui/services/google_drive/drive_client.py`
- `backend/open_webui/services/onedrive/graph_client.py`
- `backend/open_webui/main.py` (no changes needed — existing import paths preserved)
- `src/lib/utils/google-drive-picker.ts`
- `src/lib/utils/onedrive-file-picker.ts`

## References

- Existing abstraction: `backend/open_webui/services/sync/provider.py`
- OneDrive sync worker: `backend/open_webui/services/onedrive/sync_worker.py` (1373 lines)
- Google Drive sync worker: `backend/open_webui/services/google_drive/sync_worker.py` (1336 lines)
