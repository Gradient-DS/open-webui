# External Integration Cookbook: Adding a New Cloud Sync Provider

This cookbook walks you through adding a new cloud sync provider (e.g., Dropbox, Confluence, Topdesk, Salesforce) to the Open WebUI sync abstraction layer. The architecture follows a **Template Method + Factory** pattern where ~65% of sync logic is shared and each provider implements a thin adapter layer.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Frontend (SvelteKit)                         │
├─────────────────────────────────────────────────────────────────────────┤
│  apis/sync/index.ts          Shared sync API factory                   │
│  apis/{provider}/index.ts    Provider-specific API + picker            │
│  utils/{provider}-picker.ts  Provider file/folder picker               │
│  KnowledgeBase.svelte        Main KB view (cloud provider config)      │
│  CreateKnowledgeBase.svelte  KB creation with type selection           │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ REST + Socket.IO
┌───────────────────────────────▼─────────────────────────────────────────┐
│                         Backend (FastAPI)                                │
├─────────────────────────────────────────────────────────────────────────┤
│  routers/{provider}_sync.py  Thin router → delegates to shared helpers │
│  services/sync/router.py     Shared endpoint logic (13 handler funcs)  │
│  services/sync/provider.py   SyncProvider + TokenManager ABCs + factory│
│  services/sync/base_worker.py  Shared sync orchestration (~1000 lines) │
│  services/sync/scheduler.py  Generic background sync scheduler         │
│  services/sync/token_refresh.py  Generic OAuth token refresh           │
│  services/sync/events.py     Generic Socket.IO event emitters          │
│  services/sync/constants.py  SUPPORTED_EXTENSIONS, error types         │
│                                                                         │
│  services/{provider}/         Provider-specific adapter layer:          │
│    sync_worker.py             BaseSyncWorker subclass                   │
│    provider.py                SyncProvider + TokenManager impl          │
│    auth.py                    OAuth flow                                │
│    token_refresh.py           Provider-specific refresh HTTP call       │
│    api_client.py              Provider API client                       │
│    scheduler.py               Thin wrapper → SyncScheduler              │
│    sync_events.py             Thin wrapper → shared events              │
└─────────────────────────────────────────────────────────────────────────┘
```

**Data flow during sync:**

1. User creates a KB with type `"{provider}"` and selects folders/files via the picker UI
2. Frontend calls `POST /api/v1/{provider}/sync` with selected items
3. Router validates, merges sources into `knowledge.meta["{provider}_sync"]`, starts background task
4. `SyncProvider.execute_sync()` resolves the access token and creates a `BaseSyncWorker` subclass
5. `BaseSyncWorker.sync()` orchestrates: verify access → collect files → download → upload to storage → process into vectors → emit Socket.IO events → save status
6. Scheduler re-runs sync on a configurable interval

## Prerequisites Checklist

Before starting, ensure you have:

- [ ] OAuth 2.0 client credentials (client_id, client_secret) from the provider
- [ ] API docs for: listing items in a container, downloading file content, getting permissions
- [ ] Understanding of the provider's folder/file model (IDs, paths, MIME types)
- [ ] Knowledge of the provider's change detection mechanism (delta tokens, modified dates, ETags)
- [ ] Test account with sample files

## Step-by-Step Recipe

### Step 1: Add Config Variables

**File:** `backend/open_webui/config.py`

Add near the existing `GOOGLE_DRIVE_*` / `ONEDRIVE_*` config blocks (~line 2930+):

```python
# --- {Provider} Integration ---
ENABLE_{PROVIDER}_INTEGRATION = PersistentConfig(
    "ENABLE_{PROVIDER}_INTEGRATION",
    "{provider}.enable_integration",
    os.environ.get("ENABLE_{PROVIDER}_INTEGRATION", "false").lower() == "true",
)

{PROVIDER}_CLIENT_ID = PersistentConfig(
    "{PROVIDER}_CLIENT_ID",
    "{provider}.client_id",
    os.environ.get("{PROVIDER}_CLIENT_ID", ""),
)

{PROVIDER}_CLIENT_SECRET = PersistentConfig(
    "{PROVIDER}_CLIENT_SECRET",
    "{provider}.client_secret",
    os.environ.get("{PROVIDER}_CLIENT_SECRET", ""),
)

ENABLE_{PROVIDER}_SYNC = PersistentConfig(
    "ENABLE_{PROVIDER}_SYNC",
    "{provider}.enable_sync",
    os.environ.get("ENABLE_{PROVIDER}_SYNC", "false").lower() == "true",
)

{PROVIDER}_SYNC_INTERVAL_MINUTES = PersistentConfig(
    "{PROVIDER}_SYNC_INTERVAL_MINUTES",
    "{provider}.sync_interval_minutes",
    int(os.environ.get("{PROVIDER}_SYNC_INTERVAL_MINUTES", "60")),
)

{PROVIDER}_MAX_FILES_PER_SYNC = int(
    os.environ.get("{PROVIDER}_MAX_FILES_PER_SYNC", "500")
)

{PROVIDER}_MAX_FILE_SIZE_MB = int(
    os.environ.get("{PROVIDER}_MAX_FILE_SIZE_MB", "100")
)
```

**Pattern:** Use `PersistentConfig` for values that admins should be able to change at runtime via the admin API. Use plain `int(os.environ.get(...))` for values that only change at deploy time.

**Reference:** `GOOGLE_DRIVE_*` config at `config.py:2931-2965`.

### Step 2: Create the API Client

**File:** `backend/open_webui/services/{provider}/api_client.py`

This is the most provider-specific component. Model after `google_drive/drive_client.py` or `onedrive/graph_client.py`.

Required capabilities:

```python
import aiohttp
import logging

log = logging.getLogger(__name__)

class ProviderClient:
    def __init__(self, access_token: str, token_provider=None):
        self._access_token = access_token
        self._token_provider = token_provider  # Callable for mid-sync token refresh
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._access_token}"}
            )
        return self._session

    async def _request(self, method, url, **kwargs):
        """Make authenticated request with retry for 401 (token refresh) and 429 (rate limit)."""
        session = await self._get_session()
        async with session.request(method, url, **kwargs) as resp:
            if resp.status == 401 and self._token_provider:
                new_token = await self._token_provider()
                if new_token:
                    self._access_token = new_token
                    # Recreate session with new token
                    await self._session.close()
                    self._session = None
                    return await self._request(method, url, **kwargs)
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                await asyncio.sleep(retry_after)
                return await self._request(method, url, **kwargs)
            resp.raise_for_status()
            return await resp.json()

    async def list_folder_children(self, folder_id: str) -> List[Dict]:
        """List items in a container. Must return dicts with at least 'id' and 'name'."""
        ...

    async def download_file(self, file_id: str) -> bytes:
        """Download file content as bytes."""
        ...

    async def get_file_metadata(self, file_id: str) -> Optional[Dict]:
        """Get item metadata (for single-file sync checks)."""
        ...

    async def get_permissions(self, item_id: str) -> List[Dict]:
        """Get sharing permissions (optional - only if provider has sharing model)."""
        ...

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
```

**Key:** The `token_provider` callback is injected by `BaseSyncWorker` and returns a fresh access token when called. Always support this for long-running syncs where tokens may expire mid-operation.

**Reference:** `services/google_drive/drive_client.py`

### Step 3: Create the Auth Module

**File:** `backend/open_webui/services/{provider}/auth.py`

Manages the OAuth flow: generating authorization URLs, tracking pending flows, exchanging codes for tokens.

```python
import secrets
import time
import logging
from typing import Optional
from open_webui.models.oauth import OAuthSessions

log = logging.getLogger(__name__)

# In-memory store for pending OAuth flows (short-lived)
_pending_flows: dict = {}

def get_authorization_url(
    user_id: str, knowledge_id: str, redirect_uri: str
) -> str:
    """Generate OAuth authorization URL and store pending flow state."""
    state = secrets.token_urlsafe(32)
    _pending_flows[state] = {
        "user_id": user_id,
        "knowledge_id": knowledge_id,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    }

    params = {
        "client_id": PROVIDER_CLIENT_ID.value,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "your.scopes.here",
        "state": state,
        "access_type": "offline",  # For refresh tokens
        "prompt": "consent",
    }
    return f"https://provider.com/oauth/authorize?{urlencode(params)}"

async def exchange_code_for_tokens(code: str, state: str, user_id: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    flow = _pending_flows.pop(state, None)
    if not flow:
        raise ValueError("Invalid or expired OAuth state")

    # POST to token endpoint
    async with aiohttp.ClientSession() as session:
        async with session.post("https://provider.com/oauth/token", data={
            "client_id": PROVIDER_CLIENT_ID.value,
            "client_secret": PROVIDER_CLIENT_SECRET.value,
            "code": code,
            "redirect_uri": flow["redirect_uri"],
            "grant_type": "authorization_code",
        }) as resp:
            token_data = await resp.json()

    # Store in OAuthSessions
    OAuthSessions.upsert_session(
        provider="provider_name",
        user_id=user_id,
        token_data={
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "expires_at": time.time() + token_data["expires_in"],
        },
    )
    return token_data
```

**Reference:** `services/google_drive/auth.py`

### Step 4: Create the Token Refresh Module

**File:** `backend/open_webui/services/{provider}/token_refresh.py`

Thin module that delegates to shared logic with a provider-specific refresh HTTP call.

```python
from typing import Optional
from open_webui.services.sync.token_refresh import (
    get_valid_access_token as _generic_get_valid_access_token,
)
from open_webui.config import PROVIDER_CLIENT_ID, PROVIDER_CLIENT_SECRET

async def get_valid_access_token(user_id: str, knowledge_id: str) -> Optional[str]:
    return await _generic_get_valid_access_token(
        provider="provider_name",
        meta_key="provider_sync",
        user_id=user_id,
        knowledge_id=knowledge_id,
        refresh_fn=_refresh_token,
    )

async def _refresh_token(token_data: dict) -> Optional[dict]:
    """POST to provider's token endpoint with refresh_token grant.
    Return updated token_data dict, or None if token was revoked."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post("https://provider.com/oauth/token", data={
            "client_id": PROVIDER_CLIENT_ID.value,
            "client_secret": PROVIDER_CLIENT_SECRET.value,
            "refresh_token": token_data["refresh_token"],
            "grant_type": "refresh_token",
        }) as resp:
            if resp.status != 200:
                body = await resp.json()
                # Check for revocation indicators
                if body.get("error") in ("invalid_grant", "invalid_token"):
                    return None  # Triggers needs_reauth flow
                return None

            result = await resp.json()

    return {
        "access_token": result["access_token"],
        # Some providers don't return a new refresh_token on every refresh
        "refresh_token": result.get("refresh_token", token_data["refresh_token"]),
        "expires_at": time.time() + result.get("expires_in", 3600),
    }
```

**How it works:** The generic `token_refresh.py` checks if the current token is still valid (with a 5-minute buffer). If expired, it calls your `_refresh_token`. If that returns `None`, it marks all of the user's KBs of this type as `needs_reauth=True`.

**Reference:** `services/google_drive/token_refresh.py`

### Step 5: Create the Sync Worker

**File:** `backend/open_webui/services/{provider}/sync_worker.py`

This is the core adapter. Subclass `BaseSyncWorker` and implement all abstract properties and methods.

```python
from open_webui.services.sync.base_worker import BaseSyncWorker
from open_webui.config import PROVIDER_MAX_FILES_PER_SYNC

class ProviderSyncWorker(BaseSyncWorker):

    # === Abstract Properties ===

    @property
    def meta_key(self) -> str:
        return "provider_sync"  # Key in knowledge.meta dict

    @property
    def file_id_prefix(self) -> str:
        return "provider-"  # Prefix for file IDs in DB

    @property
    def event_prefix(self) -> str:
        return "provider"  # Socket.IO event prefix (e.g., "provider:sync:progress")

    @property
    def internal_request_path(self) -> str:
        return "/internal/provider-sync"

    @property
    def max_files_config(self) -> int:
        return PROVIDER_MAX_FILES_PER_SYNC

    @property
    def source_clear_delta_keys(self) -> list[str]:
        return ["delta_link", "page_token"]  # Keys to clear on cancellation

    # === Abstract Methods ===

    def _create_client(self):
        """Create and return the provider API client."""
        from .api_client import ProviderClient
        return ProviderClient(self.access_token, token_provider=self._token_provider)

    async def _close_client(self):
        """Clean up the API client."""
        if self._client:
            await self._client.close()

    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """Check if file is processable (extension, size, MIME type)."""
        from open_webui.services.sync.constants import SUPPORTED_EXTENSIONS
        name = item.get("name", "")
        ext = Path(name).suffix.lower().lstrip(".")
        size = item.get("size", 0)
        return ext in SUPPORTED_EXTENSIONS and size <= PROVIDER_MAX_FILE_SIZE_MB * 1024 * 1024

    async def _collect_folder_files(self, source: Dict) -> tuple[List[Dict], int]:
        """Enumerate files in a folder/container. Returns (files_to_process, deleted_count).

        files_to_process: list of dicts, each with at least:
          - id: provider item ID
          - name: filename
          - size: file size in bytes
          - content_type: MIME type
          - modified_at: last modified timestamp (for change detection)
          - source_item_id: the source folder ID (for grouping)
          - relative_path: path within the folder (for display)

        deleted_count: number of files removed because they were deleted from the provider.
        """
        files = []
        deleted_count = 0

        # Use your API client to list items
        items = await self._client.list_folder_children(source["item_id"])
        for item in items:
            if not self._is_supported_file(item):
                continue
            # Check if file already synced and unchanged
            existing = self._find_existing_file(item["id"])
            if existing and existing.meta.get("modified_at") == item["modified_at"]:
                continue  # Skip unchanged files
            files.append({
                "id": item["id"],
                "name": item["name"],
                "size": item["size"],
                "content_type": item.get("mimeType", "application/octet-stream"),
                "modified_at": item["modified_at"],
                "source_item_id": source["item_id"],
                "relative_path": item.get("path", item["name"]),
            })

        # Detect deletions: files in our DB that are no longer in the provider
        # (implementation varies by provider - some use delta tokens, some require full listing)

        return files, deleted_count

    async def _collect_single_file(self, source: Dict) -> Optional[Dict]:
        """Check if a single file needs syncing. Return file_info dict or None."""
        metadata = await self._client.get_file_metadata(source["item_id"])
        if not metadata:
            return None
        # Compare with existing — skip if unchanged
        existing = self._find_existing_file(source["item_id"])
        if existing and existing.meta.get("modified_at") == metadata.get("modified_at"):
            return None
        return {
            "id": metadata["id"],
            "name": metadata["name"],
            "size": metadata["size"],
            "content_type": metadata.get("mimeType", "application/octet-stream"),
            "source_item_id": source["item_id"],
            "relative_path": metadata["name"],
        }

    async def _download_file_content(self, file_info: Dict) -> bytes:
        """Download raw file content from the provider."""
        return await self._client.download_file(file_info["id"])

    def _get_provider_storage_headers(self, item_id: str) -> dict:
        """Headers for the internal storage upload request."""
        return {
            "OpenWebUI-Source": "provider_name",
            "OpenWebUI-Provider-Item-Id": item_id,
        }

    def _get_provider_file_meta(
        self, item_id, source_item_id, relative_path, name, content_type, size, file_info=None
    ) -> dict:
        """Metadata stored on the file record in the DB."""
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
        """Map provider sharing permissions to Open WebUI access_grants.
        Optional — skip (pass) if provider doesn't have a sharing model."""
        # Fetch permissions from provider API
        # Map provider user identifiers to Open WebUI user IDs (e.g., by email)
        # Update knowledge access_grants using the access_grants format:
        #
        # access_grants = []
        # for user_id in permitted_user_ids:
        #     access_grants.append({
        #         "principal_type": "user",
        #         "principal_id": user_id,
        #         "permission": "read",
        #     })
        # access_grants.append({
        #     "principal_type": "user",
        #     "principal_id": self.user_id,
        #     "permission": "write",
        # })
        # Knowledges.update_knowledge_by_id(self.knowledge_id, KnowledgeForm(
        #     name=knowledge.name, description=knowledge.description,
        #     type=knowledge.type, access_grants=access_grants,
        # ))
        pass

    async def _verify_source_access(self, source: Dict) -> bool:
        """Check if user still has access to a source (folder/file)."""
        try:
            metadata = await self._client.get_file_metadata(source["item_id"])
            return metadata is not None
        except Exception:
            return False

    async def _handle_revoked_source(self, source: Dict) -> int:
        """Remove all files for a revoked source. Return count of removed files."""
        removed = 0
        for file in self._get_files_for_source(source["item_id"]):
            Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file.id)
            Files.delete_file_by_id(file.id)
            removed += 1
        return removed
```

**What BaseSyncWorker handles for you:** file deduplication, parallel processing with semaphore, cancellation checking, progress tracking, Socket.IO event emission, error aggregation, vector propagation across shared files, DB session management (`get_db()`), and status persistence.

**Reference:** `services/google_drive/sync_worker.py` (simpler, ~300 lines), `services/onedrive/sync_worker.py` (more complex, includes legacy migration and delta sync)

### Step 6: Create the Provider Module

**File:** `backend/open_webui/services/{provider}/provider.py`

```python
from open_webui.services.sync.provider import SyncProvider, TokenManager

class ProviderTokenManager(TokenManager):
    async def get_valid_access_token(self, user_id, knowledge_id):
        from .token_refresh import get_valid_access_token
        return await get_valid_access_token(user_id, knowledge_id)

    def has_stored_token(self, user_id, knowledge_id):
        from open_webui.models.oauth import OAuthSessions
        session = OAuthSessions.get_session_by_provider_and_user_id(
            "provider_name", user_id
        )
        return session is not None

    def delete_token(self, user_id, knowledge_id):
        from open_webui.models.oauth import OAuthSessions
        session = OAuthSessions.get_session_by_provider_and_user_id(
            "provider_name", user_id
        )
        if session:
            OAuthSessions.delete_session_by_id(session.id)
            return True
        return False


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

**Reference:** `services/google_drive/provider.py`

### Step 7: Create Thin Wrappers (Events + Scheduler)

**File:** `backend/open_webui/services/{provider}/sync_events.py`

```python
from open_webui.services.sync.events import (
    emit_file_processing as _emit_file_processing,
    emit_file_added as _emit_file_added,
    emit_sync_progress as _emit_sync_progress,
)

_PREFIX = "provider"

async def emit_sync_progress(user_id, knowledge_id, status, **kwargs):
    await _emit_sync_progress(_PREFIX, user_id, knowledge_id, status, **kwargs)

async def emit_file_processing(user_id, knowledge_id, filename, **kwargs):
    await _emit_file_processing(_PREFIX, user_id, knowledge_id, filename, **kwargs)

async def emit_file_added(user_id, knowledge_id, file_data, **kwargs):
    await _emit_file_added(_PREFIX, user_id, knowledge_id, file_data, **kwargs)
```

**File:** `backend/open_webui/services/{provider}/scheduler.py`

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

**Reference:** `services/google_drive/scheduler.py`

### Step 8: Register in Factory Functions

**File:** `backend/open_webui/services/sync/provider.py`

Add to `get_sync_provider()` (~line 150):
```python
elif provider_type == "provider_name":
    from open_webui.services.provider.provider import ProviderSyncProvider
    return ProviderSyncProvider()
```

Add to `get_token_manager()` (~line 165):
```python
elif provider_type == "provider_name":
    from open_webui.services.provider.provider import ProviderTokenManager
    return ProviderTokenManager()
```

### Step 9: Create the Router

**File:** `backend/open_webui/routers/{provider}_sync.py`

The router is thin — it defines provider constants, a `SyncItem` model, and delegates to shared handler functions.

```python
from fastapi import APIRouter, Depends, Request
from open_webui.utils.auth import get_verified_user
from open_webui.services.sync.router import (
    get_knowledge_or_raise,
    handle_sync_items_request,
    handle_get_sync_status,
    handle_cancel_sync,
    handle_remove_source,
    handle_list_synced_collections,
    handle_get_token_status,
    handle_revoke_token,
    complete_auth_callback,
    auth_callback_html,
    remove_files_for_source_generic,
)

router = APIRouter()

_META_KEY = "provider_sync"
_PROVIDER_TYPE = "provider_name"
_FILE_ID_PREFIX = "provider-"
_CLEAR_DELTA_KEYS = ["delta_link", "page_token"]

# --- Pydantic models ---
class SyncItem(BaseModel):
    item_id: str
    name: str
    type: str  # "folder" or "file"
    # Add provider-specific fields as needed

class SyncItemsRequest(BaseModel):
    items: List[SyncItem]

# --- Endpoints ---
@router.post("/{knowledge_id}/sync")
async def sync_items(knowledge_id: str, form_data: SyncItemsRequest, ...):
    # Delegate to shared handler
    return await handle_sync_items_request(
        knowledge_id=knowledge_id,
        items=[item.model_dump() for item in form_data.items],
        meta_key=_META_KEY,
        provider_type=_PROVIDER_TYPE,
        clear_delta_keys=_CLEAR_DELTA_KEYS,
        user=user,
        request=request,
    )

@router.get("/{knowledge_id}/sync/status")
async def get_sync_status(knowledge_id: str, ...):
    return handle_get_sync_status(knowledge_id, _META_KEY, user)

# ... same delegation pattern for all other endpoints

@router.get("/auth/initiate/{knowledge_id}")
async def initiate_auth(knowledge_id: str, request: Request, ...):
    """Start OAuth flow — generate auth URL and return it."""
    from .auth import get_authorization_url
    redirect_uri = str(request.base_url).rstrip("/") + "/oauth/{provider}/callback"
    auth_url = get_authorization_url(user.id, knowledge_id, redirect_uri)
    return {"auth_url": auth_url}
```

**Reference:** `routers/google_drive_sync.py` (full working example)

### Step 10: Register in main.py

**File:** `backend/open_webui/main.py`

Three registration points:

```python
# 1. Import (near line 100, with other router imports)
from open_webui.routers import provider_sync

# 2. Mount router (near line 1794, with other conditional routers)
if app.state.config.ENABLE_PROVIDER_SYNC:
    app.include_router(
        provider_sync.router,
        prefix="/api/v1/{provider}",
        tags=["{provider}"],
    )

# 3. Start scheduler (in lifespan startup, near line 766)
from open_webui.services.{provider}.scheduler import (
    start_scheduler as start_provider_scheduler,
)
start_provider_scheduler(app)

# 4. Stop scheduler (in lifespan shutdown, near line 867)
from open_webui.services.{provider}.scheduler import (
    stop_scheduler as stop_provider_scheduler,
)
stop_provider_scheduler()

# 5. Expose config to frontend (in /api/config endpoint, near line 2455)
# Add enable flag and client credentials to the config response

# 6. OAuth callback routing (near line 2875)
# If your provider shares a callback URL pattern, add routing logic
# to dispatch to your auth handler based on the state parameter
```

### Step 11: Add Knowledge Type

**File:** `backend/open_webui/routers/knowledge.py`

Add your provider type to the allowed types set (~line 285):

```python
allowed_kb_types = {"local", "onedrive", "google_drive", "provider_name"} | set(
    (request.app.state.config.INTEGRATION_PROVIDERS or {}).keys()
)
```

**Note:** Non-local KB types are automatically forced private (`access_grants = []`) at creation time (line 295-296). Write operations (file upload, delete) are blocked for non-local types throughout the router.

### Step 12: Frontend

#### 12a. API Client

**File:** `src/lib/apis/{provider}/index.ts`

```typescript
import { createSyncApi, type SyncItem } from '$lib/apis/sync';

// Re-export the shared sync API configured for this provider
const syncApi = createSyncApi('{provider}');

export const startProviderSyncItems = syncApi.startSyncItems;
export const getProviderSyncStatus = syncApi.getSyncStatus;
export const cancelProviderSync = syncApi.cancelSync;
export const getProviderSyncedCollections = syncApi.getSyncedCollections;
export const getProviderTokenStatus = syncApi.getTokenStatus;
export const removeProviderSource = syncApi.removeSource;
export const revokeProviderToken = syncApi.revokeToken;

export type { SyncItem };
```

**Reference:** `src/lib/apis/googledrive/index.ts`

#### 12b. File Picker

**File:** `src/lib/utils/{provider}-picker.ts`

This is the most provider-specific frontend component. It handles launching the provider's file/folder selection UI and returning selected items.

Options:
- **Provider SDK picker** (like Google Picker API) — load the SDK script, configure, and handle selection
- **Custom modal** — build your own folder browser using the provider's list API
- **OAuth redirect flow** — some providers (like OneDrive) use a redirect-based picker

**Reference:** `src/lib/utils/google-drive-picker.ts`

#### 12c. KnowledgeBase.svelte

**File:** `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Add your provider to the `CLOUD_PROVIDERS` configuration array (near top of `<script>`):

```typescript
// In the CLOUD_PROVIDERS array, add:
{
    type: "provider_name",
    metaKey: "provider_sync",
    eventPrefix: "provider",      // Must match sync_worker.event_prefix
    fileIdPrefix: "provider-",    // Must match sync_worker.file_id_prefix
    sourceMetaField: "provider_name",
    label: "Provider Name",
    icon: ProviderIcon,           // Svelte icon component
    startSyncFn: startProviderSyncItems,
    getSyncStatusFn: getProviderSyncStatus,
    cancelSyncFn: cancelProviderSync,
    getTokenStatusFn: getProviderTokenStatus,
    revokeTokenFn: revokeProviderToken,
    removeSourceFn: removeProviderSource,
    initPickerFn: initializeProviderPicker,
    getAuthTokenFn: getProviderAuthToken,
    clearTokenFn: clearProviderToken,
}
```

The generic cloud provider loop in `KnowledgeBase.svelte` automatically handles sync status polling, Socket.IO event listening, and UI rendering for all providers in this array.

#### 12d. Icon Component

**File:** `src/lib/components/icons/{Provider}.svelte`

Create an SVG icon component following the pattern of `GoogleDrive.svelte` or `OneDrive.svelte`.

#### 12e. CreateKnowledgeBase.svelte and TypeSelector

Add the provider type option to the KB creation flow type selector.

## Provider-Specific Considerations

| Aspect | Google Drive | OneDrive | Notes for New Providers |
|--------|-------------|----------|------------------------|
| Change detection | `modifiedTime` comparison | Delta tokens (`delta_link`) | Delta tokens are more efficient but require tracking state |
| File export | Google Workspace files need export (Docs→docx, Sheets→xlsx) | Native file download | Some providers have proprietary formats needing conversion |
| Folder recursion | Manual recursive listing | Delta API returns flat list with parent refs | Flat listing is simpler; recursive needs depth tracking |
| Permissions | Google Drive permissions API | Microsoft Graph permissions | Map provider users to Open WebUI users by email |
| Rate limits | Per-user quotas, 429 with Retry-After | Per-app throttling, 429 with Retry-After | Always implement backoff in your API client |
| OAuth | Standard OAuth 2.0 + PKCE optional | OAuth 2.0 with tenant-specific endpoints | Some providers require PKCE or have unique token flows |
| Max file size | Configurable (default 100MB) | Configurable (default 100MB) | Large files may need chunked download |

## Testing Checklist

### Per-Provider:
- [ ] Create KB with provider type → verify creation flow
- [ ] Authenticate via OAuth popup → verify token stored
- [ ] Pick folder/files → verify sources saved to metadata
- [ ] Trigger sync → verify files download, process, and appear in KB
- [ ] Cancel sync mid-progress → verify cancellation
- [ ] Re-sync → verify incremental (only changed files)
- [ ] Remove source → verify files cleaned up
- [ ] Revoke token → verify re-auth prompt
- [ ] Background sync → verify scheduler triggers after interval
- [ ] Permission sync → verify `access_grants` updated on KB

### Cross-Provider:
- [ ] Create one KB per provider simultaneously → verify no event/state collision
- [ ] Socket events correctly prefixed (e.g., `provider:sync:progress`)
- [ ] `npm run build` compiles successfully
- [ ] Backend starts without import errors

## Reference Implementations

| Provider | Complexity | Best For |
|----------|-----------|----------|
| **Google Drive** (`services/google_drive/`) | Simpler | Starting point — straightforward OAuth, no delta tokens, clean API |
| **OneDrive** (`services/onedrive/`) | More complex | Delta sync reference, legacy migration patterns, Microsoft Graph API |

## Files Created (New Provider)

```
backend/open_webui/services/{provider}/
  __init__.py
  api_client.py          # Provider API client
  auth.py                # OAuth flow
  token_refresh.py       # Token refresh HTTP call
  sync_worker.py         # BaseSyncWorker subclass
  provider.py            # SyncProvider + TokenManager
  scheduler.py           # Thin wrapper → SyncScheduler
  sync_events.py         # Thin wrapper → shared events

backend/open_webui/routers/
  {provider}_sync.py     # FastAPI router

src/lib/apis/{provider}/
  index.ts               # API client

src/lib/utils/
  {provider}-picker.ts   # File/folder picker

src/lib/components/icons/
  {Provider}.svelte      # SVG icon
```

## Files Modified (Registration)

```
backend/open_webui/config.py              # Config variables
backend/open_webui/main.py                # Router mount, scheduler, config exposure
backend/open_webui/services/sync/provider.py  # Factory functions
backend/open_webui/routers/knowledge.py   # Allowed types set
src/lib/components/workspace/Knowledge/KnowledgeBase.svelte  # CLOUD_PROVIDERS array
src/lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte  # Type selector
src/lib/stores/index.ts                   # Feature flag stores (optional)
helm/*/values.yaml                        # Deployment config (if using Helm)
```

## Estimated Effort

| Component | Files | Effort |
|-----------|-------|--------|
| API client | 1 | Medium (depends on API complexity) |
| Auth module | 1 | Low-Medium (OAuth 2.0 is mostly boilerplate) |
| Token refresh | 1 | Low (boilerplate) |
| Sync worker | 1 | Medium (file collection + download logic) |
| Provider + wrappers | 3 | Low (boilerplate) |
| Router | 1 | Low (delegates to shared helpers) |
| Config + registration | 3 files modified | Low |
| Frontend | 3-4 | Medium (picker is the hard part) |
| **Total** | ~10 new + ~5 modified | **1-3 days** depending on API complexity |
