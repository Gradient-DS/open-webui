# Background Sync Implementation Plan

## Overview

Implement background (scheduled) sync for OneDrive knowledge bases, replacing the current frontend-token-only model with server-side OAuth token storage and refresh. Design the system with light abstraction interfaces so that future datasources (Google Drive, SharePoint, etc.) can be added by implementing the same ABCs, following the codebase's factory-singleton pattern.

## Current State Analysis

The OneDrive sync system is fully functional for **manual, user-triggered syncs** but completely inert for **background sync**:

- **Token flow**: Frontend MSAL acquires 1-hour Graph API tokens → sends to backend → sync worker uses them until they expire
- **No server-side tokens**: `OAuthSessions` table exists with Fernet encryption (used by OIDC login and MCP tool OAuth) but OneDrive code doesn't use it
- **Scheduler is stub-only**: `start_scheduler()` exists (`services/onedrive/scheduler.py:97`) but is never called from `main.py`, and `_check_and_report_due_syncs()` only logs
- **GraphClient has no 401/410 handling**: Token expiry mid-sync causes unhandled exceptions (`graph_client.py:69`)
- **No client secret configured**: `ONEDRIVE_CLIENT_SECRET_BUSINESS` doesn't exist — needed for confidential client auth code flow with 90-day refresh tokens

### Key Discoveries:
- `OAuthSessions` model is production-ready with Fernet encryption, CRUD operations, and `(user_id, provider)` index (`models/oauth_sessions.py:38-42`)
- `OAuthClientManager` already implements token refresh via OpenID discovery (`utils/oauth.py:647-674`) — we can follow this pattern
- The sync worker requires the `app` instance for mock `Request` construction (`sync_worker.py:106-122`) — scheduler needs app access
- Knowledge `type` column exists with `"local"` and `"onedrive"` values (`models/knowledge.py:47`)
- Codebase patterns use factory-singleton (StorageProvider, VectorDBBase), not registries

## Desired End State

After implementation:
1. **Admin configures** `ONEDRIVE_CLIENT_SECRET_BUSINESS` env var alongside existing client ID
2. **Users authorize** OneDrive access via a popup that triggers backend OAuth auth code flow with PKCE
3. **Backend stores** encrypted 90-day refresh tokens in `oauth_session` table, per-KB isolation
4. **Scheduler runs** every N minutes (configurable), finds OneDrive KBs due for sync, refreshes tokens, and executes syncs automatically
5. **GraphClient auto-retries** on 401 (token expired) by calling a token provider callback
6. **Frontend shows** token status (stored/expired/revoked) and allows re-authorization
7. **Abstraction interfaces** (`SyncProvider`, `TokenManager`) define clear extension points for future datasources

### Verification:
- An OneDrive KB that has been authorized shows "Background sync enabled" in the UI
- After the configured interval, the KB automatically re-syncs without user interaction
- If a token is revoked in Azure AD, the system detects this and shows "Re-authorization needed"
- Manual sync still works (frontend token takes precedence over stored token)
- The scheduler handles concurrent sync prevention, error recovery, and token refresh

## What We're NOT Doing

- **Google Drive implementation** — only designing interfaces for it; no concrete code
- **Multi-tenant OAuth** — single Azure AD app registration per deployment
- **Admin UI for token management** — admins use Azure AD portal for revocation
- **Token migration** — existing OneDrive KBs require manual re-authorization
- **Cross-node scheduler coordination** — single scheduler instance per process (no distributed locking)
- **Automatic retry of failed syncs** — failed syncs wait for next scheduled interval
- **Changing the existing manual sync flow** — frontend MSAL tokens continue to work for user-triggered syncs

## Implementation Approach

Five phases, each independently testable:
1. **Backend OAuth flow** — confidential client auth code + PKCE, token storage
2. **Token refresh + GraphClient hardening** — automatic refresh, 401/410 handling
3. **Light abstraction interfaces** — SyncProvider + TokenManager ABCs, factory function
4. **Scheduler implementation** — wire into lifespan, execute syncs with stored tokens
5. **Frontend integration** — auth popup, token status UI, re-auth flow

---

## Phase 1: Backend OAuth Auth Code Flow

### Overview
Add confidential client OAuth auth code flow with PKCE for obtaining 90-day Microsoft refresh tokens. Tokens are stored encrypted in the existing `oauth_session` table, isolated per knowledge base.

### Security Design

- **Confidential client**: Uses `client_secret` for token exchange (not public client)
- **PKCE**: Code challenge/verifier even with confidential client (defense in depth, required by Microsoft for new registrations)
- **State parameter**: Random UUID stored in memory with 10-minute TTL, maps to `(user_id, knowledge_id, code_verifier)` — prevents CSRF
- **Minimal scopes**: `Files.Read.All offline_access` — read-only file access + refresh token
- **Per-KB token isolation**: `provider = "onedrive:{knowledge_id}"` — revoking one KB doesn't affect others
- **Client secret**: Env var only, never in PersistentConfig, never exposed to frontend

### Changes Required:

#### 1. Configuration
**File**: `backend/open_webui/config.py`
**Changes**: Add `ONEDRIVE_CLIENT_SECRET_BUSINESS` env var

```python
# After ONEDRIVE_CLIENT_ID_BUSINESS (line 2524)
ONEDRIVE_CLIENT_SECRET_BUSINESS = os.environ.get(
    "ONEDRIVE_CLIENT_SECRET_BUSINESS", ""
)
```

Also add to `main.py` config exposure (around line 2101-2105) — expose a boolean `has_client_secret` to frontend (never the secret itself):

```python
"onedrive": {
    # ... existing keys ...
    "has_client_secret": bool(ONEDRIVE_CLIENT_SECRET_BUSINESS),
}
```

#### 2. Auth Service
**File**: `backend/open_webui/services/onedrive/auth.py` (new file)
**Changes**: OAuth auth code flow with PKCE

```python
"""
OneDrive OAuth Auth Code Flow for Background Sync.

Uses confidential client (client_secret) with PKCE for obtaining
90-day refresh tokens. Tokens are stored encrypted in OAuthSessions.
"""

import hashlib
import base64
import secrets
import time
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlencode

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.config import (
    ONEDRIVE_CLIENT_ID_BUSINESS,
    ONEDRIVE_CLIENT_SECRET_BUSINESS,
    ONEDRIVE_SHAREPOINT_TENANT_ID,
)

log = logging.getLogger(__name__)

# In-memory pending flows with TTL (10 minutes)
_pending_flows: Dict[str, Dict[str, Any]] = {}
_FLOW_TTL_SECONDS = 600

# Microsoft OAuth endpoints
_AUTHORITY_BASE = "https://login.microsoftonline.com"
_GRAPH_SCOPE = "https://graph.microsoft.com/Files.Read.All offline_access"


def _cleanup_expired_flows():
    """Remove expired pending flows."""
    now = time.time()
    expired = [k for k, v in _pending_flows.items() if now - v["created_at"] > _FLOW_TTL_SECONDS]
    for k in expired:
        del _pending_flows[k]


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def get_authorization_url(
    user_id: str,
    knowledge_id: str,
    redirect_uri: str,
) -> str:
    """
    Build the Microsoft OAuth authorization URL.

    Returns the URL to redirect the user to for authorization.
    Stores the pending flow in memory for callback validation.
    """
    _cleanup_expired_flows()

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    _pending_flows[state] = {
        "user_id": user_id,
        "knowledge_id": knowledge_id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    }

    params = {
        "client_id": ONEDRIVE_CLIENT_ID_BUSINESS,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _GRAPH_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "response_mode": "query",
        "prompt": "consent",  # Force consent to ensure refresh token
    }

    return f"{_AUTHORITY_BASE}/{tenant_id}/oauth2/v2.0/authorize?{urlencode(params)}"


async def exchange_code_for_tokens(
    code: str,
    state: str,
    user_id: str,
) -> Dict[str, Any]:
    """
    Exchange authorization code for tokens and store in OAuthSessions.

    Validates the state parameter against pending flows,
    exchanges the code with Microsoft's token endpoint,
    and stores the encrypted token data.

    Returns dict with 'success', 'knowledge_id', and optionally 'error'.
    """
    import httpx

    _cleanup_expired_flows()

    # Validate state
    flow = _pending_flows.pop(state, None)
    if not flow:
        return {"success": False, "error": "Invalid or expired state parameter"}

    if flow["user_id"] != user_id:
        log.warning(
            "OAuth callback user mismatch: expected %s, got %s",
            flow["user_id"], user_id,
        )
        return {"success": False, "error": "User mismatch"}

    # Check TTL
    if time.time() - flow["created_at"] > _FLOW_TTL_SECONDS:
        return {"success": False, "error": "Authorization flow expired"}

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    token_url = f"{_AUTHORITY_BASE}/{tenant_id}/oauth2/v2.0/token"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": ONEDRIVE_CLIENT_ID_BUSINESS,
                    "client_secret": ONEDRIVE_CLIENT_SECRET_BUSINESS,
                    "code": code,
                    "redirect_uri": flow["redirect_uri"],
                    "grant_type": "authorization_code",
                    "code_verifier": flow["code_verifier"],
                },
            )
            response.raise_for_status()
            token_data = response.json()
    except httpx.HTTPStatusError as e:
        error_body = e.response.json() if e.response.headers.get("content-type", "").startswith("application/json") else {}
        log.error("Token exchange failed: %s %s", e.response.status_code, error_body.get("error_description", ""))
        return {"success": False, "error": error_body.get("error_description", "Token exchange failed")}
    except Exception as e:
        log.error("Token exchange error: %s", e)
        return {"success": False, "error": "Token exchange failed"}

    # Calculate expires_at from expires_in
    if "expires_in" in token_data and "expires_at" not in token_data:
        token_data["expires_at"] = int(time.time()) + int(token_data["expires_in"])
    token_data["issued_at"] = int(time.time())

    # Store in OAuthSessions with per-KB provider key
    knowledge_id = flow["knowledge_id"]
    provider = f"onedrive:{knowledge_id}"

    # Delete any existing session for this KB
    existing = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if existing:
        OAuthSessions.delete_session_by_id(existing.id)

    session = OAuthSessions.create_session(
        user_id=user_id,
        provider=provider,
        token=token_data,
    )

    if not session:
        return {"success": False, "error": "Failed to store token"}

    log.info("Stored OAuth token for user %s, KB %s", user_id, knowledge_id)
    return {"success": True, "knowledge_id": knowledge_id}


def get_stored_token(user_id: str, knowledge_id: str) -> Optional[Dict[str, Any]]:
    """Get the stored token data for a knowledge base, or None."""
    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return None
    return session.token


def delete_stored_token(user_id: str, knowledge_id: str) -> bool:
    """Delete the stored token for a knowledge base."""
    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return False
    return OAuthSessions.delete_session_by_id(session.id)
```

#### 3. Router Endpoints
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Changes**: Add auth endpoints

Add these endpoints to the existing router:

```python
from open_webui.services.onedrive.auth import (
    get_authorization_url,
    exchange_code_for_tokens,
    get_stored_token,
    delete_stored_token,
)

@router.get("/auth/initiate")
async def initiate_auth(
    knowledge_id: str,
    request: Request,
    user=Depends(get_verified_user),
):
    """Initiate OAuth auth code flow for background sync."""
    # Validate client secret is configured
    if not ONEDRIVE_CLIENT_SECRET_BUSINESS:
        raise HTTPException(400, "OneDrive client secret not configured")

    # Validate KB ownership
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    # Build redirect URI from the current request
    redirect_uri = str(request.url_for("auth_callback"))

    auth_url = get_authorization_url(
        user_id=user.id,
        knowledge_id=knowledge_id,
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(auth_url)


@router.get("/auth/callback")
async def auth_callback(
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
    user=Depends(get_verified_user),
):
    """
    Handle OAuth callback from Microsoft.
    Returns HTML that posts result to the opener window and closes.
    """
    if error:
        return _auth_callback_html(
            success=False,
            error=error_description or error,
        )

    if not code or not state:
        return _auth_callback_html(
            success=False,
            error="Missing authorization code or state",
        )

    result = await exchange_code_for_tokens(
        code=code,
        state=state,
        user_id=user.id,
    )

    if result["success"]:
        # Update knowledge meta to reflect stored token
        knowledge_id = result["knowledge_id"]
        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get("onedrive_sync", {})
            sync_info["has_stored_token"] = True
            sync_info["token_stored_at"] = int(time.time())
            sync_info["needs_reauth"] = False
            meta["onedrive_sync"] = sync_info
            Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    return _auth_callback_html(
        success=result["success"],
        error=result.get("error"),
        knowledge_id=result.get("knowledge_id"),
    )


def _auth_callback_html(success: bool, error: str = None, knowledge_id: str = None):
    """Return HTML that communicates result to opener and closes."""
    from starlette.responses import HTMLResponse

    data = {
        "type": "onedrive_auth_callback",
        "success": success,
    }
    if error:
        data["error"] = error
    if knowledge_id:
        data["knowledge_id"] = knowledge_id

    import json
    html = f"""<!DOCTYPE html>
<html><body><script>
    if (window.opener) {{
        window.opener.postMessage({json.dumps(data)}, window.location.origin);
    }}
    window.close();
</script></body></html>"""
    return HTMLResponse(html)


@router.get("/auth/token-status/{knowledge_id}")
async def get_token_status(
    knowledge_id: str,
    user=Depends(get_verified_user),
):
    """Check if a stored token exists and is valid for a KB."""
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    token_data = get_stored_token(user.id, knowledge_id)
    if not token_data:
        return {"has_token": False}

    expires_at = token_data.get("expires_at", 0)
    is_expired = expires_at < time.time()

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    return {
        "has_token": True,
        "is_expired": is_expired,
        "needs_reauth": sync_info.get("needs_reauth", False),
        "token_stored_at": sync_info.get("token_stored_at"),
    }


@router.post("/auth/revoke/{knowledge_id}")
async def revoke_token(
    knowledge_id: str,
    user=Depends(get_verified_user),
):
    """Revoke and delete stored token for a KB."""
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    deleted = delete_stored_token(user.id, knowledge_id)

    # Update meta
    if knowledge:
        meta = knowledge.meta or {}
        sync_info = meta.get("onedrive_sync", {})
        sync_info["has_stored_token"] = False
        sync_info.pop("token_stored_at", None)
        sync_info["needs_reauth"] = False
        meta["onedrive_sync"] = sync_info
        Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    return {"revoked": deleted}
```

### Success Criteria:

#### Automated Verification:
- [x] No import errors: `python -c "from open_webui.services.onedrive.auth import get_authorization_url, exchange_code_for_tokens"`
- [x] Config variable loads: `python -c "from open_webui.config import ONEDRIVE_CLIENT_SECRET_BUSINESS; print(type(ONEDRIVE_CLIENT_SECRET_BUSINESS))"`
- [x] Backend builds without errors: `cd backend && pip install -e . && python -c 'from open_webui.main import app'`
- [ ] Frontend build succeeds: `npm run build`

#### Manual Verification:
- [ ] With `ONEDRIVE_CLIENT_SECRET_BUSINESS` set, `/api/v1/onedrive/auth/initiate?knowledge_id=...` redirects to Microsoft login
- [ ] After authorization, the callback stores a token in `oauth_session` table
- [ ] The popup window closes and posts a message to the opener
- [ ] `/api/v1/onedrive/auth/token-status/{kb_id}` returns `has_token: true`
- [ ] `/api/v1/onedrive/auth/revoke/{kb_id}` deletes the token

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Token Refresh & GraphClient Hardening

### Overview
Add automatic token refresh using stored refresh tokens, and harden the GraphClient with 401 retry logic and 410 Gone handling. This makes background sync viable by ensuring tokens stay fresh and transient Graph API errors are handled gracefully.

### Changes Required:

#### 1. Token Refresh Service
**File**: `backend/open_webui/services/onedrive/token_refresh.py` (new file)
**Changes**: Token refresh via httpx to Microsoft token endpoint

```python
"""
OneDrive Token Refresh Service.

Refreshes stored OAuth tokens using the Microsoft v2.0 token endpoint.
Handles rotating refresh tokens and revocation detection.
"""

import time
import logging
from typing import Optional

import httpx

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.config import (
    ONEDRIVE_CLIENT_ID_BUSINESS,
    ONEDRIVE_CLIENT_SECRET_BUSINESS,
    ONEDRIVE_SHAREPOINT_TENANT_ID,
)

log = logging.getLogger(__name__)

_AUTHORITY_BASE = "https://login.microsoftonline.com"
_GRAPH_SCOPE = "https://graph.microsoft.com/Files.Read.All offline_access"

# Refresh if token expires within this many seconds
_REFRESH_BUFFER_SECONDS = 300  # 5 minutes


async def get_valid_access_token(
    user_id: str,
    knowledge_id: str,
) -> Optional[str]:
    """
    Get a valid access token for a knowledge base.

    If the stored token is expired or near-expiry, automatically refreshes it.
    Returns None if no token exists or refresh fails (token revoked).
    """
    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return None

    token_data = session.token
    expires_at = token_data.get("expires_at", 0)

    # Check if token needs refresh
    if time.time() + _REFRESH_BUFFER_SECONDS < expires_at:
        return token_data.get("access_token")

    # Token expired or near-expiry — refresh
    log.info("Refreshing token for user %s, KB %s", user_id, knowledge_id)
    new_token_data = await _refresh_token(token_data)

    if new_token_data is None:
        # Refresh failed — token likely revoked
        log.warning("Token refresh failed for user %s, KB %s — marking as needs_reauth", user_id, knowledge_id)
        _mark_needs_reauth(knowledge_id)
        return None

    # Update stored token
    OAuthSessions.update_session_by_id(session.id, new_token_data)
    return new_token_data.get("access_token")


async def _refresh_token(token_data: dict) -> Optional[dict]:
    """
    Refresh an OAuth token using the refresh_token grant.

    Returns updated token dict on success, None on failure (revocation).
    """
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        log.error("No refresh_token in stored token data")
        return None

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    token_url = f"{_AUTHORITY_BASE}/{tenant_id}/oauth2/v2.0/token"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": ONEDRIVE_CLIENT_ID_BUSINESS,
                    "client_secret": ONEDRIVE_CLIENT_SECRET_BUSINESS,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": _GRAPH_SCOPE,
                },
            )

            if response.status_code == 400:
                error_data = response.json()
                error_code = error_data.get("error", "")
                if error_code in ("invalid_grant", "interaction_required"):
                    log.warning("Token revoked or requires interaction: %s", error_code)
                    return None
                log.error("Token refresh error: %s", error_data)
                return None

            response.raise_for_status()
            new_token_data = response.json()

    except httpx.HTTPStatusError as e:
        log.error("Token refresh HTTP error: %s", e.response.status_code)
        return None
    except Exception as e:
        log.error("Token refresh error: %s", e)
        return None

    # Microsoft may rotate refresh tokens — preserve old one if new not provided
    if "refresh_token" not in new_token_data:
        new_token_data["refresh_token"] = refresh_token

    # Calculate expires_at
    if "expires_in" in new_token_data:
        new_token_data["expires_at"] = int(time.time()) + int(new_token_data["expires_in"])
    new_token_data["issued_at"] = int(time.time())

    return new_token_data


def _mark_needs_reauth(knowledge_id: str):
    """Mark a knowledge base as needing re-authorization."""
    from open_webui.models.knowledge import Knowledges

    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge:
        return

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})
    sync_info["needs_reauth"] = True
    sync_info["has_stored_token"] = False
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
```

#### 2. GraphClient Callback-Based Token Provider
**File**: `backend/open_webui/services/onedrive/graph_client.py`
**Changes**: Add token_provider callback, 401 retry, 410 Gone handling

Modify the constructor:
```python
def __init__(
    self,
    access_token: str,
    token_provider: Optional[Callable[[], Awaitable[str]]] = None,
):
    self._access_token = access_token
    self._token_provider = token_provider
    self._client: Optional[httpx.AsyncClient] = None
```

Add type import at top:
```python
from typing import Optional, Callable, Awaitable
```

Modify `_request_with_retry` to handle 401 and 410:

```python
async def _request_with_retry(
    self, method: str, url: str, params=None, max_retries: int = 3,
    follow_redirects: bool = False,
) -> httpx.Response:
    client = await self._get_client()
    last_exception = None
    token_refreshed = False

    for attempt in range(max_retries):
        try:
            response = await client.request(
                method,
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
                follow_redirects=follow_redirects,
            )

            if response.status_code == 401 and not token_refreshed and self._token_provider:
                # Token expired — try refresh once
                log.info("Received 401, attempting token refresh")
                try:
                    new_token = await self._token_provider()
                    if new_token:
                        self._access_token = new_token
                        token_refreshed = True
                        continue  # Retry with new token
                except Exception as e:
                    log.warning("Token refresh failed: %s", e)
                return response  # Return 401 if refresh failed or already tried

            if response.status_code == 410:
                # Delta token expired — caller should reset delta link and retry
                log.info("Received 410 Gone — delta token expired")
                return response  # Let caller handle by clearing delta_link

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                log.warning("Rate limited, waiting %d seconds", retry_after)
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500:
                wait = 2 ** attempt
                log.warning("Server error %d, retrying in %d seconds", response.status_code, wait)
                await asyncio.sleep(wait)
                continue

            return response

        except httpx.HTTPStatusError as e:
            last_exception = e
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise

    raise RuntimeError(f"Failed after {max_retries} retries: {last_exception}")
```

#### 3. Sync Worker 410 Gone Handling
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Handle 410 in `_collect_folder_files`, accept token_provider

In the constructor, add optional `token_provider`:
```python
def __init__(
    self,
    knowledge_id: str,
    sources: List[Dict[str, Any]],
    access_token: str,
    user_id: str,
    app,
    event_emitter: Optional[Callable] = None,
    token_provider: Optional[Callable[[], Awaitable[str]]] = None,
):
    # ... existing assignments ...
    self._token_provider = token_provider
```

In `sync()`, pass token_provider to GraphClient:
```python
self._client = GraphClient(self.access_token, token_provider=self._token_provider)
```

In `_collect_folder_files`, handle 410 by clearing delta_link and retrying:
```python
async def _collect_folder_files(self, source, ...):
    try:
        items, new_delta_link = await self._client.get_drive_delta(
            source["drive_id"], source.get("delta_link")
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 410:
            log.info("Delta token expired for source %s, performing full sync", source["name"])
            source["delta_link"] = None
            items, new_delta_link = await self._client.get_drive_delta(
                source["drive_id"], None
            )
        else:
            raise
    # ... rest of method unchanged ...
```

### Success Criteria:

#### Automated Verification:
- [x] No import errors: `python -c "from open_webui.services.onedrive.token_refresh import get_valid_access_token"`
- [x] GraphClient accepts token_provider: `python -c "from open_webui.services.onedrive.graph_client import GraphClient; GraphClient('test', token_provider=None)"`
- [x] Backend builds: `cd backend && pip install -e .`

#### Manual Verification:
- [ ] When a stored token expires, `get_valid_access_token()` successfully refreshes it
- [ ] When making a Graph API call with an expired token, the 401 triggers a refresh and retry
- [ ] When a delta token expires (410), the sync falls back to full sync instead of crashing
- [ ] When a refresh token is revoked in Azure AD, the system detects `invalid_grant` and marks `needs_reauth`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Light Sync Provider Abstraction

### Overview
Create lightweight `SyncProvider` and `TokenManager` ABCs following the codebase's factory-singleton pattern (like `StorageProvider` at `storage/provider.py`). OneDrive is the only implementation; Google Drive is a placeholder in comments. This provides clear extension points without premature abstraction.

### Changes Required:

#### 1. Abstraction Interfaces
**File**: `backend/open_webui/services/sync/provider.py` (new file)
**Changes**: Define SyncProvider and TokenManager ABCs

```python
"""
Sync Provider Abstraction Layer.

Defines interfaces for external datasource sync providers.
Follows the factory-singleton pattern used by StorageProvider and VectorDBBase.

To add a new datasource:
1. Create a new directory under services/ (e.g., services/google_drive/)
2. Implement SyncProvider and TokenManager ABCs
3. Add a case to get_sync_provider() and get_token_manager()
4. Add the provider type to the Knowledge model's type validation
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List


class TokenManager(ABC):
    """Manages OAuth token lifecycle for a sync provider."""

    @abstractmethod
    async def get_valid_access_token(
        self, user_id: str, knowledge_id: str
    ) -> Optional[str]:
        """
        Get a valid access token, refreshing if needed.
        Returns None if no token exists or refresh failed.
        """
        ...

    @abstractmethod
    def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        """Check if a stored token exists (may be expired)."""
        ...

    @abstractmethod
    def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        """Delete stored token. Returns True if deleted."""
        ...


class SyncProvider(ABC):
    """Executes sync operations for an external datasource."""

    @abstractmethod
    async def execute_sync(
        self,
        knowledge_id: str,
        user_id: str,
        app,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a sync for a knowledge base.

        Args:
            knowledge_id: The knowledge base ID
            user_id: The user who owns the KB
            app: FastAPI app instance (for process_file mock requests)
            access_token: Optional frontend-provided token (manual sync).
                         If None, the provider should obtain a token from
                         its TokenManager.

        Returns:
            Dict with sync results (files_processed, files_failed, etc.)
        """
        ...

    @abstractmethod
    def get_provider_type(self) -> str:
        """Return the provider type string (e.g., 'onedrive')."""
        ...

    @abstractmethod
    def get_token_manager(self) -> TokenManager:
        """Return the token manager for this provider."""
        ...


def get_sync_provider(provider_type: str) -> SyncProvider:
    """
    Factory function for sync providers.

    Follows the same pattern as get_storage_provider() in storage/provider.py.
    """
    if provider_type == "onedrive":
        from open_webui.services.onedrive.provider import OneDriveSyncProvider
        return OneDriveSyncProvider()
    # elif provider_type == "google_drive":
    #     from open_webui.services.google_drive.provider import GoogleDriveSyncProvider
    #     return GoogleDriveSyncProvider()
    else:
        raise ValueError(f"Unsupported sync provider: {provider_type}")


def get_token_manager(provider_type: str) -> TokenManager:
    """Factory function for token managers."""
    if provider_type == "onedrive":
        from open_webui.services.onedrive.provider import OneDriveTokenManager
        return OneDriveTokenManager()
    else:
        raise ValueError(f"Unsupported token manager: {provider_type}")
```

#### 2. OneDrive Provider Implementation
**File**: `backend/open_webui/services/onedrive/provider.py` (new file)
**Changes**: Concrete implementations wrapping existing code

```python
"""
OneDrive Sync Provider implementation.

Wraps the existing OneDriveSyncWorker and token refresh service
behind the SyncProvider and TokenManager interfaces.
"""

import logging
from typing import Optional, Dict, Any

from open_webui.services.sync.provider import SyncProvider, TokenManager
from open_webui.services.onedrive.token_refresh import (
    get_valid_access_token as _get_valid_access_token,
)
from open_webui.services.onedrive.auth import (
    get_stored_token,
    delete_stored_token,
)
from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)


class OneDriveTokenManager(TokenManager):
    """Token manager for OneDrive using Microsoft OAuth v2.0."""

    async def get_valid_access_token(
        self, user_id: str, knowledge_id: str
    ) -> Optional[str]:
        return await _get_valid_access_token(user_id, knowledge_id)

    def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        return get_stored_token(user_id, knowledge_id) is not None

    def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        return delete_stored_token(user_id, knowledge_id)


class OneDriveSyncProvider(SyncProvider):
    """Sync provider for OneDrive / SharePoint."""

    def __init__(self):
        self._token_manager = OneDriveTokenManager()

    def get_provider_type(self) -> str:
        return "onedrive"

    def get_token_manager(self) -> TokenManager:
        return self._token_manager

    async def execute_sync(
        self,
        knowledge_id: str,
        user_id: str,
        app,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute OneDrive sync.

        If access_token is provided (manual sync), uses it directly.
        Otherwise, obtains a token from the token manager (background sync).
        """
        from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker

        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
        if not knowledge:
            return {"error": "Knowledge base not found"}

        meta = knowledge.meta or {}
        sync_info = meta.get("onedrive_sync", {})
        sources = sync_info.get("sources", [])

        if not sources:
            return {"error": "No sync sources configured"}

        # Determine token source
        token_provider = None
        if access_token:
            # Manual sync — use frontend-provided token
            effective_token = access_token
        else:
            # Background sync — get token from store
            effective_token = await self._token_manager.get_valid_access_token(
                user_id, knowledge_id
            )
            if not effective_token:
                return {"error": "No valid token available", "needs_reauth": True}

            # Create a token provider callback for mid-sync refresh
            async def _refresh():
                return await self._token_manager.get_valid_access_token(
                    user_id, knowledge_id
                )
            token_provider = _refresh

        worker = OneDriveSyncWorker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=effective_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
        )

        return await worker.sync()
```

#### 3. Package Init Files
**File**: `backend/open_webui/services/sync/__init__.py` (new file)

```python
from open_webui.services.sync.provider import (
    SyncProvider,
    TokenManager,
    get_sync_provider,
    get_token_manager,
)
```

### Success Criteria:

#### Automated Verification:
- [x] Interfaces import cleanly: `python -c "from open_webui.services.sync import SyncProvider, TokenManager, get_sync_provider"`
- [x] OneDrive provider instantiates: `python -c "from open_webui.services.sync import get_sync_provider; p = get_sync_provider('onedrive'); print(p.get_provider_type())"`
- [x] Invalid provider raises ValueError: `python -c "from open_webui.services.sync import get_sync_provider; get_sync_provider('invalid')" 2>&1 | grep ValueError`
- [x] Backend builds: `cd backend && pip install -e .`

#### Manual Verification:
- [ ] `OneDriveSyncProvider.execute_sync()` works with a frontend-provided access_token (manual sync path)
- [ ] `OneDriveSyncProvider.execute_sync()` works without access_token when a stored token exists (background sync path)
- [ ] Token refresh callback is invoked by GraphClient on 401 during background sync

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Scheduler Implementation

### Overview
Wire the scheduler into the application lifecycle and implement actual sync execution using stored tokens and the SyncProvider abstraction. The scheduler finds OneDrive KBs due for sync, obtains tokens, and runs syncs in sequence (one at a time to limit resource usage).

### Changes Required:

#### 1. Knowledge Query Method
**File**: `backend/open_webui/models/knowledge.py`
**Changes**: Add method to get all KBs by type (no limit)

```python
def get_knowledge_bases_by_type(self, type: str) -> list[KnowledgeModel]:
    """Get all knowledge bases of a specific type (no pagination limit)."""
    with get_db() as db:
        return [
            KnowledgeModel.model_validate(kb)
            for kb in db.query(Knowledge)
            .filter_by(type=type)
            .order_by(Knowledge.updated_at.desc())
            .all()
        ]
```

#### 2. Scheduler Rewrite
**File**: `backend/open_webui/services/onedrive/scheduler.py`
**Changes**: Implement actual sync execution

```python
"""
OneDrive Background Sync Scheduler.

Periodically checks OneDrive knowledge bases for sync eligibility
and executes syncs using stored OAuth refresh tokens.

Architecture:
- Runs as an asyncio.Task started from main.py lifespan
- Receives the FastAPI app instance for sync worker mock requests
- Syncs one KB at a time to limit resource usage
- Skips KBs that are already syncing, need re-auth, or not due
"""

import asyncio
import time
import logging
from typing import Optional

from open_webui.config import (
    ENABLE_ONEDRIVE_SYNC,
    ONEDRIVE_SYNC_INTERVAL_MINUTES,
)
from open_webui.models.knowledge import Knowledges, KnowledgeModel

log = logging.getLogger(__name__)

_scheduler_task: Optional[asyncio.Task] = None
_app = None  # FastAPI app instance, set during startup


def start_scheduler(app):
    """
    Start the background sync scheduler.

    Called from main.py lifespan function. Stores the app reference
    and creates the scheduler asyncio task.
    """
    global _scheduler_task, _app

    if not ENABLE_ONEDRIVE_SYNC.value:
        log.info("OneDrive sync disabled, scheduler not started")
        return

    _app = app
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_run_scheduler())
        log.info("OneDrive background sync scheduler started")


def stop_scheduler():
    """Stop the background sync scheduler."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        log.info("OneDrive background sync scheduler stopped")
    _scheduler_task = None


async def _run_scheduler():
    """Main scheduler loop."""
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60

    # Wait one interval before first run (don't sync immediately on startup)
    await asyncio.sleep(interval_seconds)

    while True:
        try:
            await _execute_due_syncs()
        except asyncio.CancelledError:
            log.info("Scheduler cancelled")
            return
        except Exception:
            log.exception("Error in scheduler loop")

        await asyncio.sleep(interval_seconds)


async def _execute_due_syncs():
    """Find and execute syncs for all due OneDrive knowledge bases."""
    from open_webui.services.sync.provider import get_sync_provider

    kbs = Knowledges.get_knowledge_bases_by_type("onedrive")
    if not kbs:
        return

    now = time.time()
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60
    provider = get_sync_provider("onedrive")

    for kb in kbs:
        if not _is_sync_due(kb, now, interval_seconds):
            continue

        log.info("Starting scheduled sync for KB %s (%s)", kb.id, kb.name)

        try:
            # Mark as syncing before starting
            _update_sync_status(kb.id, "syncing")

            result = await provider.execute_sync(
                knowledge_id=kb.id,
                user_id=kb.user_id,
                app=_app,
            )

            if result.get("error"):
                if result.get("needs_reauth"):
                    log.warning("KB %s needs re-authorization", kb.id)
                    # needs_reauth is already set by token_refresh._mark_needs_reauth
                else:
                    log.error("Scheduled sync failed for KB %s: %s", kb.id, result["error"])
                    _update_sync_status(kb.id, "failed", error=result["error"])
            else:
                log.info(
                    "Scheduled sync completed for KB %s: %d files processed",
                    kb.id, result.get("files_processed", 0),
                )

        except Exception:
            log.exception("Unexpected error during scheduled sync of KB %s", kb.id)
            _update_sync_status(kb.id, "failed", error="Unexpected scheduler error")


def _is_sync_due(kb: KnowledgeModel, now: float, interval_seconds: float) -> bool:
    """Check if a knowledge base is due for scheduled sync."""
    meta = kb.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    # Skip if no sources configured
    if not sync_info.get("sources"):
        return False

    # Skip if no stored token
    if not sync_info.get("has_stored_token"):
        return False

    # Skip if needs re-authorization
    if sync_info.get("needs_reauth"):
        return False

    # Skip if currently syncing
    status = sync_info.get("status", "idle")
    if status == "syncing":
        return False

    # Check if enough time has passed since last sync
    last_sync = sync_info.get("last_sync_at", 0)
    return (now - last_sync) >= interval_seconds


def _update_sync_status(knowledge_id: str, status: str, error: str = None):
    """Update the sync status in knowledge meta."""
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge:
        return

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})
    sync_info["status"] = status
    if error:
        sync_info["error"] = error
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
```

#### 3. Wire Scheduler into Lifespan
**File**: `backend/open_webui/main.py`
**Changes**: Start scheduler in lifespan function, stop on shutdown

In the lifespan function, after the existing periodic tasks (around line 667):

```python
# Start OneDrive background sync scheduler
from open_webui.services.onedrive.scheduler import start_scheduler as start_onedrive_scheduler
start_onedrive_scheduler(app)
```

Before the `yield` (around line 711), or in the shutdown section after `yield`:

```python
# Stop OneDrive scheduler
from open_webui.services.onedrive.scheduler import stop_scheduler as stop_onedrive_scheduler
stop_onedrive_scheduler()
```

### Success Criteria:

#### Automated Verification:
- [x] `get_knowledge_bases_by_type` works: `python -c "from open_webui.models.knowledge import Knowledges; print(Knowledges.get_knowledge_bases_by_type('onedrive'))"`
- [x] Scheduler imports cleanly: `python -c "from open_webui.services.onedrive.scheduler import start_scheduler, stop_scheduler"`
- [x] Backend builds: `cd backend && pip install -e .`

#### Manual Verification:
- [ ] With `ENABLE_ONEDRIVE_SYNC=true`, the scheduler starts on app boot (check logs for "scheduler started")
- [ ] After the configured interval, an authorized OneDrive KB automatically syncs
- [ ] KBs without stored tokens are skipped (no error, just skipped)
- [ ] KBs already syncing are skipped
- [ ] KBs with `needs_reauth` are skipped
- [ ] Failed syncs are logged and status updated, but don't crash the scheduler
- [ ] On app shutdown, the scheduler stops cleanly

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 5: Frontend Integration

### Overview
Add the authorization popup flow, token status indicators, and re-authorization UX to the Knowledge Base frontend. Manual sync continues to work with frontend MSAL tokens; the new auth flow is additive for enabling background sync.

### Changes Required:

#### 1. API Client Functions
**File**: `src/lib/apis/onedrive/index.ts`
**Changes**: Add auth-related API functions

```typescript
export async function getTokenStatus(
    token: string,
    knowledgeId: string
): Promise<{
    has_token: boolean;
    is_expired?: boolean;
    needs_reauth?: boolean;
    token_stored_at?: number;
}> {
    const res = await fetch(
        `${WEBUI_BASE_URL}/api/v1/onedrive/auth/token-status/${knowledgeId}`,
        {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
        }
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function revokeToken(
    token: string,
    knowledgeId: string
): Promise<{ revoked: boolean }> {
    const res = await fetch(
        `${WEBUI_BASE_URL}/api/v1/onedrive/auth/revoke/${knowledgeId}`,
        {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
        }
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}
```

#### 2. Auth Popup Flow
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Add authorize for background sync button and popup handler

Add state variables:
```typescript
let backgroundSyncAuthorized = $state(false);
let backgroundSyncNeedsReauth = $state(false);
```

Add auth popup handler:
```typescript
async function authorizeBackgroundSync() {
    const authUrl = `${WEBUI_BASE_URL}/api/v1/onedrive/auth/initiate?knowledge_id=${$page.params.id}`;

    // Open popup
    const popup = window.open(
        authUrl,
        'onedrive_auth',
        'width=600,height=700,scrollbars=yes'
    );

    // Listen for postMessage from callback
    const handleMessage = (event: MessageEvent) => {
        if (event.origin !== window.location.origin) return;
        if (event.data?.type !== 'onedrive_auth_callback') return;

        window.removeEventListener('message', handleMessage);

        if (event.data.success) {
            backgroundSyncAuthorized = true;
            backgroundSyncNeedsReauth = false;
            toast.success($i18n.t('Background sync authorized'));
        } else {
            toast.error($i18n.t('Authorization failed: {{error}}', { error: event.data.error }));
        }
    };

    window.addEventListener('message', handleMessage);

    // Cleanup if popup is closed without completing
    const checkClosed = setInterval(() => {
        if (popup?.closed) {
            clearInterval(checkClosed);
            window.removeEventListener('message', handleMessage);
        }
    }, 500);
}
```

Add token status check on mount (inside `onMount`):
```typescript
// Check background sync token status
if (knowledge?.type === 'onedrive' && knowledge?.meta?.onedrive_sync?.sources?.length) {
    try {
        const status = await getTokenStatus(localStorage.token, knowledge.id);
        backgroundSyncAuthorized = status.has_token && !status.is_expired;
        backgroundSyncNeedsReauth = status.needs_reauth ?? false;
    } catch {
        // Silently fail — token status is informational
    }
}
```

#### 3. Token Status UI
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Show background sync status and authorize/re-auth button

In the KB header area (near the existing OneDrive sync button), add a background sync indicator:

```svelte
{#if knowledge?.type === 'onedrive' && $config?.onedrive?.has_client_secret}
    {#if backgroundSyncNeedsReauth}
        <button
            class="text-xs text-red-500 hover:text-red-600 flex items-center gap-1"
            on:click={authorizeBackgroundSync}
        >
            <svg ...warning-icon... />
            {$i18n.t('Re-authorize background sync')}
        </button>
    {:else if backgroundSyncAuthorized}
        <span class="text-xs text-green-600 flex items-center gap-1">
            <svg ...check-icon... />
            {$i18n.t('Background sync enabled')}
        </span>
    {:else if knowledge?.meta?.onedrive_sync?.sources?.length}
        <button
            class="text-xs text-blue-500 hover:text-blue-600 flex items-center gap-1"
            on:click={authorizeBackgroundSync}
        >
            <svg ...key-icon... />
            {$i18n.t('Enable background sync')}
        </button>
    {/if}
{/if}
```

#### 4. Translation Keys
**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add new translation keys (alphabetically sorted)

```json
"Authorization failed: {{error}}": "",
"Background sync authorized": "",
"Background sync enabled": "",
"Enable background sync": "",
"Re-authorize background sync": ""
```

#### 5. Config Type Update
**File**: `src/lib/stores/index.ts`
**Changes**: Add `has_client_secret` to onedrive config type

In the Config type definition, update the `onedrive` section:

```typescript
onedrive: {
    client_id_personal: string;
    client_id_business: string;
    sharepoint_url: string;
    sharepoint_tenant_id: string;
    has_client_secret: boolean;  // NEW
};
```

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds: `npm run build`
- [x] Translation keys are valid JSON: `python -c "import json; json.load(open('src/lib/i18n/locales/en-US/translation.json'))"`

#### Manual Verification:
- [ ] When `has_client_secret` is true, the "Enable background sync" button appears for OneDrive KBs
- [ ] Clicking the button opens a Microsoft login popup
- [ ] After authorization, the button changes to "Background sync enabled"
- [ ] When a token is revoked, the button changes to "Re-authorize background sync" (red)
- [ ] Manual sync (via the sync icon button) still works independently of background sync authorization
- [ ] The authorization flow works correctly with PKCE (check network requests for code_challenge)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Security Considerations

### Token Security
- **Encryption at rest**: All tokens encrypted with Fernet (AES-128-CBC + HMAC-SHA256) via `OAuthSessions` — key derived from `WEBUI_SECRET_KEY` or `OAUTH_SESSION_TOKEN_ENCRYPTION_KEY`
- **Per-KB isolation**: Each KB gets its own OAuth session (`provider = "onedrive:{kb_id}"`). Revoking one doesn't affect others
- **Client secret protection**: `ONEDRIVE_CLIENT_SECRET_BUSINESS` is an env var only, never stored in PersistentConfig, never exposed via API or frontend
- **Token never exposed to frontend**: After initial storage, tokens are only used server-side. The frontend only sees `has_token: boolean`

### OAuth Flow Security
- **PKCE**: Code verifier/challenge prevents authorization code interception, even though we're a confidential client
- **State parameter**: Random UUID stored in memory with 10-minute TTL. Prevents CSRF by binding the callback to a specific (user, KB) pair
- **User validation**: The callback validates that the authenticated user matches the one who initiated the flow
- **Forced consent**: `prompt=consent` ensures the user explicitly grants access each time
- **Minimal scopes**: `Files.Read.All offline_access` — read-only access, no write/delete permissions

### Revocation Handling
- **`invalid_grant` detection**: Token refresh detects revoked tokens and marks KBs as `needs_reauth`
- **No silent degradation**: When a token is revoked, the system explicitly surfaces this to the user rather than silently failing syncs
- **Manual revocation**: Users can revoke tokens via the UI (`/auth/revoke/{kb_id}`) and admins via Azure AD

### Scheduler Security
- **Ownership preserved**: Syncs run as the KB owner (using their stored token), not as an admin
- **No privilege escalation**: The sync worker uses the same `process_file` pipeline as manual syncs
- **Concurrent sync prevention**: The scheduler skips KBs that are already syncing

## Testing Strategy

### Unit Tests:
- PKCE generation produces valid code_verifier/code_challenge pairs
- State parameter TTL expiry correctly rejects expired flows
- User mismatch in callback is rejected
- Token refresh handles rotating refresh tokens (new refresh_token in response)
- Token refresh detects `invalid_grant` as revocation
- `_is_sync_due()` correctly evaluates all skip conditions
- Factory function returns correct provider for each type

### Integration Tests:
- Full OAuth flow with mock Microsoft endpoints
- Token refresh with mock token endpoint
- GraphClient 401 → token refresh → retry
- GraphClient 410 → delta link reset → full sync
- Scheduler finds due KBs and executes syncs

### Manual Testing Steps:
1. Configure `ONEDRIVE_CLIENT_SECRET_BUSINESS` and verify the "Enable background sync" button appears
2. Complete the authorization flow and verify the token is stored (check `oauth_session` table)
3. Wait for one scheduler interval and verify automatic sync occurs (check logs)
4. Revoke the app's permission in Azure AD > My Account > App permissions
5. Verify the next sync attempt detects revocation and shows "Re-authorize background sync"
6. Re-authorize and verify syncs resume

## Performance Considerations

- **Sequential KB syncs**: The scheduler syncs one KB at a time to limit memory and CPU usage from parallel file processing
- **Token caching**: `get_valid_access_token` only calls the token endpoint when the token is within 5 minutes of expiry
- **Delta queries**: Incremental sync via delta tokens minimizes Graph API calls for unchanged files
- **Scheduler sleep**: The scheduler sleeps for the full interval between runs, not polling frequently

## Migration Notes

- **Existing OneDrive KBs**: Will NOT have stored tokens. They continue to work with manual sync. Users must explicitly authorize background sync via the new UI button
- **No database migration needed**: Uses the existing `oauth_session` table and the existing `meta` JSON field on `knowledge`
- **New config var**: `ONEDRIVE_CLIENT_SECRET_BUSINESS` must be set in the Azure AD app registration as a "Web" platform (not SPA). The redirect URI must be added to the app registration

## Future Extensibility

To add a new datasource (e.g., Google Drive):

1. Create `backend/open_webui/services/google_drive/` with:
   - `auth.py` — Google OAuth flow
   - `token_refresh.py` — Google token refresh
   - `sync_worker.py` — Google Drive sync logic
   - `provider.py` — `GoogleDriveSyncProvider` and `GoogleDriveTokenManager`
2. Add `"google_drive"` case to factory functions in `services/sync/provider.py`
3. Add `"google_drive"` to the knowledge type validation
4. Add scheduler logic (the existing `get_knowledge_bases_by_type()` already supports any type)
5. Add frontend auth flow (same popup pattern, different OAuth URL)

## References

- Research: `thoughts/shared/research/2026-02-04-background-sync-current-state.md`
- Original plan: `thoughts/shared/plans/2026-02-04-background-sync-multi-datasource.md`
- Knowledge type plan: `thoughts/shared/plans/2026-02-04-typed-knowledge-bases.md`
- OAuth sessions model: `backend/open_webui/models/oauth_sessions.py`
- Existing sync worker: `backend/open_webui/services/onedrive/sync_worker.py`
- StorageProvider pattern: `backend/open_webui/storage/provider.py`
- OAuthClientManager (reference for token refresh): `backend/open_webui/utils/oauth.py:403-864`
