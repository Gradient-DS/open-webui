# Background Sync & Multi-Datasource Architecture Implementation Plan

## Overview

Enable automatic background OneDrive sync and establish the foundation for multi-datasource support. The primary blocker today is the "token gap" — the scheduler exists but cannot execute syncs because it has no way to obtain fresh Microsoft Graph API tokens. We solve this by implementing a backend OAuth authorization code flow that stores encrypted 90-day refresh tokens, then upgrade the scheduler to use them for unattended sync execution. Finally, we extract the generic patterns into a multi-datasource abstraction layer (SyncProvider + TokenManager + Registry) that follows the existing PermissionProvider pattern.

## Current State Analysis

### What Works Today
- **Manual sync via button** — User picks OneDrive items, frontend MSAL gets a delegated access token, backend downloads and processes files into a knowledge base (`sync_worker.py`)
- **Delta queries** — `GraphClient.get_drive_delta()` at `services/onedrive/graph_client.py:129-159` tracks changes efficiently
- **Encrypted token storage** — `OAuthSession` model at `models/oauth_sessions.py:24-42` uses Fernet encryption
- **Permission Provider pattern** — `PermissionProvider(ABC)` at `services/permissions/provider.py:48-135` with registry at `services/permissions/registry.py:16-70`
- **Scheduler infrastructure** — `services/onedrive/scheduler.py` exists but only logs which KBs are due (lines 87-92), never executes syncs
- **Scheduler not wired** — `start_scheduler()` exported from `__init__.py` but never called from `main.py`

### What's Missing
| Gap | Location | Impact |
|-----|----------|--------|
| No `offline_access` scope, no refresh tokens | Frontend MSAL | SPA refresh tokens have **24-hour hard limit** — useless for background sync |
| No backend auth code flow | Not implemented | Cannot get 90-day refresh tokens (requires confidential client with Web redirect URI) |
| No `ONEDRIVE_CLIENT_SECRET` config | `config.py` | Backend cannot exchange auth codes as confidential client |
| No token refresh mechanism | Not implemented | Scheduler cannot get fresh access tokens |
| Scheduler never started | `main.py` lifespan (lines 627-717) | Background sync is completely inert |
| No `410 Gone` handling | `graph_client.py:30-76` | Stale delta tokens cause unrecoverable errors |

### Key Discoveries
- **SPA refresh token limit**: Tokens obtained via SPA redirect URIs expire in **24 hours** (non-configurable). Tokens from Web redirect URIs (confidential client) last **90 days** rolling. This is the core reason for the backend auth code flow.
- **Same Azure AD app registration** can have both SPA and Web platforms. The redirect URIs must be different per platform type. Microsoft determines flow behavior by redirect URI type.
- **MSAL Python is synchronous** — would require `asyncio.to_thread()` in FastAPI. Direct `httpx` calls are simpler and consistent with the existing `GraphClient` pattern.
- **Existing OAuth patterns**: Login OAuth uses Authlib (`utils/oauth.py:1339-1644`), MCP client OAuth uses similar pattern. OneDrive backend auth can follow `OAuthClientManager` pattern but simpler.
- **Internal API calls**: `sync_worker.py:970-976` uses `user_token` (Open WebUI JWT) for internal HTTP calls to `/api/v1/retrieval/process/file`. For scheduled syncs, we generate an internal JWT via `create_token()` at `utils/auth.py:191-202`.

## Desired End State

After implementation:
1. Users connect OneDrive to a KB via a **one-time backend OAuth flow** that stores a 90-day refresh token
2. The scheduler **automatically refreshes tokens and executes syncs** at the configured interval
3. Manual re-authentication is only needed if the refresh token is explicitly revoked (password change, admin action)
4. The sync system is built on a **multi-datasource abstraction** (`SyncProvider` + `TokenManager` + `SyncProviderRegistry`) that mirrors the `PermissionProvider` pattern
5. Adding a new datasource (Google Drive, SharePoint, Slack) requires implementing two interfaces and registering them

### Verification
- Backend logs show `"Refreshed OneDrive token for knowledge base X"`
- Backend logs show `"Executing scheduled sync for knowledge base X"`
- Files sync automatically at the configured interval without user interaction
- Tokens remain valid across backend restarts
- UI shows "Auto-sync enabled" or "Re-authentication required" per KB
- `SyncProviderRegistry.get_all_providers()` returns the OneDrive provider

## What We're NOT Doing

- **MSAL Python library** — Direct `httpx` calls are simpler for our async backend and avoid the sync→async bridging
- **Application-only auth (client credentials)** — Requires admin consent, changes access model to org-wide instead of per-user
- **Webhook subscriptions** — Deferred to a future plan; polling-based sync is sufficient for the configured interval (default 60 min)
- **`$select` query optimization** — Nice-to-have but not blocking
- **Proactive `RateLimit-Remaining` header monitoring** — The existing retry logic handles 429s adequately
- **Token migration tooling** — Existing KBs will need a one-time re-sync; no automated migration

## Implementation Approach

1. **Backend auth code flow** with confidential client (Web platform + `client_secret`) to get 90-day refresh tokens
2. **Direct `httpx` calls** for both token exchange and refresh (no MSAL Python dependency)
3. **OAuthSessions table** for encrypted token storage (existing Fernet encryption)
4. **Option 3 from research**: Build OneDrive first with clean boundaries, then extract into multi-datasource abstractions
5. **Internal JWT generation** for scheduled syncs that need to call retrieval API

---

## Phase 1: Backend OAuth Configuration & Auth Endpoints

### Overview
Add a confidential client configuration (client secret) and create backend OAuth endpoints that handle the authorization code flow with PKCE. This stores 90-day refresh tokens for unattended sync.

### Prerequisites: Azure AD Configuration

Before implementing, the Azure AD app registration needs these changes (document for ops/admin):

1. Open **Microsoft Entra admin center > App registrations > [OneDrive Business app]**
2. Under **Authentication > Platform configurations**, click **Add a platform > Web**
3. Enter redirect URI: `https://{WEBUI_URL}/api/v1/onedrive/auth/callback`
4. For local dev: also add `http://localhost:8080/api/v1/onedrive/auth/callback`
5. Do NOT check "Implicit grant" boxes
6. Under **Certificates & secrets > Client secrets > New client secret** — copy the value
7. Under **API permissions**, ensure `Files.Read.All` (delegated) is added

The existing SPA platform configuration remains untouched.

### Changes Required:

#### 1. Add configuration variables
**File**: `backend/open_webui/config.py`
**Location**: After line 2534 (after `ONEDRIVE_CLIENT_ID_BUSINESS`)

Add:
```python
ONEDRIVE_CLIENT_SECRET_BUSINESS = os.environ.get("ONEDRIVE_CLIENT_SECRET_BUSINESS", "")
```

No `PersistentConfig` needed — client secrets should only come from environment variables, not the admin UI.

#### 2. Create backend auth service
**File**: `backend/open_webui/services/onedrive/auth.py` (new file)

This module handles the OAuth authorization code flow with PKCE:

```python
"""Backend OAuth authorization code flow for OneDrive.

Uses confidential client (Web platform + client_secret) to obtain
90-day refresh tokens for background sync.
"""

import hashlib
import base64
import secrets
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from urllib.parse import urlencode

import httpx

from open_webui.config import (
    ONEDRIVE_CLIENT_ID_BUSINESS,
    ONEDRIVE_CLIENT_SECRET_BUSINESS,
    ONEDRIVE_SHAREPOINT_TENANT_ID,
)
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)

# In-memory store for pending auth flows (state → flow data)
# Entries expire after 10 minutes
_pending_flows: Dict[str, Dict[str, Any]] = {}
_FLOW_TTL_SECONDS = 600


@dataclass
class AuthFlowResult:
    success: bool
    knowledge_id: Optional[str] = None
    error: Optional[str] = None


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def _cleanup_expired_flows():
    """Remove expired pending flows."""
    now = time.time()
    expired = [k for k, v in _pending_flows.items() if now - v["created_at"] > _FLOW_TTL_SECONDS]
    for k in expired:
        del _pending_flows[k]


def initiate_auth_flow(
    user_id: str,
    knowledge_id: str,
    redirect_uri: str,
) -> str:
    """Generate Microsoft OAuth authorization URL.

    Args:
        user_id: The authenticated user's ID
        knowledge_id: The KB to store the token for
        redirect_uri: The backend callback URL

    Returns:
        The authorization URL to redirect the user to
    """
    _cleanup_expired_flows()

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    client_id = ONEDRIVE_CLIENT_ID_BUSINESS

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    # Store flow data for callback verification
    _pending_flows[state] = {
        "user_id": user_id,
        "knowledge_id": knowledge_id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    }

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "https://graph.microsoft.com/Files.Read.All offline_access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",  # Ensure offline_access consent is shown
    }

    auth_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        + urlencode(params)
    )
    return auth_url


async def handle_auth_callback(
    code: str,
    state: str,
) -> AuthFlowResult:
    """Exchange authorization code for tokens and store refresh token.

    Args:
        code: The authorization code from Microsoft
        state: The state parameter for flow lookup

    Returns:
        AuthFlowResult with success/failure
    """
    # Look up and validate the pending flow
    flow = _pending_flows.pop(state, None)
    if not flow:
        return AuthFlowResult(success=False, error="Invalid or expired state")

    if time.time() - flow["created_at"] > _FLOW_TTL_SECONDS:
        return AuthFlowResult(success=False, error="Auth flow expired")

    user_id = flow["user_id"]
    knowledge_id = flow["knowledge_id"]
    code_verifier = flow["code_verifier"]
    redirect_uri = flow["redirect_uri"]

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    client_id = ONEDRIVE_CLIENT_ID_BUSINESS
    client_secret = ONEDRIVE_CLIENT_SECRET_BUSINESS

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )

        if response.status_code != 200:
            error_data = response.json()
            error_msg = error_data.get("error_description", error_data.get("error", "Unknown"))
            log.error(f"Token exchange failed for KB {knowledge_id}: {error_msg}")
            return AuthFlowResult(success=False, knowledge_id=knowledge_id, error=error_msg)

        tokens = response.json()
        refresh_token = tokens.get("refresh_token")
        access_token = tokens.get("access_token")

        if not refresh_token:
            log.error(f"No refresh token in response for KB {knowledge_id}")
            return AuthFlowResult(
                success=False, knowledge_id=knowledge_id,
                error="No refresh token received"
            )

        # Store encrypted refresh token in OAuthSessions
        provider = f"onedrive:{knowledge_id}"
        token_data = {
            "refresh_token": refresh_token,
            "tenant_id": tenant_id,
            "knowledge_id": knowledge_id,
            "stored_at": int(time.time()),
            "expires_at": int(time.time()) + (90 * 24 * 60 * 60),
        }

        existing = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
        if existing:
            OAuthSessions.update_session_by_id(existing.id, token_data)
        else:
            OAuthSessions.create_session(user_id=user_id, provider=provider, token=token_data)

        # Update KB metadata
        knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            if "onedrive_sync" not in meta:
                meta["onedrive_sync"] = {}
            meta["onedrive_sync"]["has_stored_token"] = True
            meta["onedrive_sync"]["token_stored_at"] = int(time.time())
            meta["onedrive_sync"]["needs_reauth"] = False
            Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

        log.info(f"Stored OneDrive refresh token for KB {knowledge_id}")

        return AuthFlowResult(success=True, knowledge_id=knowledge_id)

    except httpx.TimeoutException:
        log.error(f"Token exchange timeout for KB {knowledge_id}")
        return AuthFlowResult(success=False, knowledge_id=knowledge_id, error="Request timeout")
    except Exception as e:
        log.exception(f"Token exchange error for KB {knowledge_id}: {e}")
        return AuthFlowResult(success=False, knowledge_id=knowledge_id, error=str(e))
```

#### 3. Add auth endpoints to the router
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Location**: Add after existing endpoints (after line 236)

```python
from open_webui.services.onedrive.auth import initiate_auth_flow, handle_auth_callback
from open_webui.config import ONEDRIVE_CLIENT_SECRET_BUSINESS
from fastapi.responses import HTMLResponse

@router.get("/auth/initiate")
async def initiate_onedrive_auth(
    knowledge_id: str,
    request: Request,
    user: UserModel = Depends(get_verified_user),
):
    """Initiate backend OAuth flow for OneDrive background sync.

    Returns the Microsoft authorization URL for the frontend to open in a popup.
    """
    if not ONEDRIVE_CLIENT_SECRET_BUSINESS:
        raise HTTPException(
            status_code=501,
            detail="ONEDRIVE_CLIENT_SECRET_BUSINESS not configured. "
            "Backend auth flow requires a client secret.",
        )

    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Build callback URL from the request's base URL
    redirect_uri = str(request.base_url).rstrip("/") + "/api/v1/onedrive/auth/callback"

    auth_url = initiate_auth_flow(
        user_id=user.id,
        knowledge_id=knowledge_id,
        redirect_uri=redirect_uri,
    )

    return {"auth_url": auth_url}


@router.get("/auth/callback")
async def onedrive_auth_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """Handle Microsoft OAuth callback.

    This endpoint is called by Microsoft after user consent.
    Returns HTML that closes the popup and signals the opener.
    """
    if error:
        log.warning(f"OneDrive auth error: {error} - {error_description}")
        return HTMLResponse(f"""<!DOCTYPE html>
<html><body><script>
window.opener.postMessage({{
    type: 'onedrive-auth-error',
    error: {repr(error_description or error)}
}}, window.location.origin);
window.close();
</script></body></html>""")

    result = await handle_auth_callback(code=code, state=state)

    if result.success:
        return HTMLResponse(f"""<!DOCTYPE html>
<html><body><script>
window.opener.postMessage({{
    type: 'onedrive-auth-success',
    knowledge_id: {repr(result.knowledge_id)}
}}, window.location.origin);
window.close();
</script></body></html>""")
    else:
        return HTMLResponse(f"""<!DOCTYPE html>
<html><body><script>
window.opener.postMessage({{
    type: 'onedrive-auth-error',
    error: {repr(result.error or 'Unknown error')}
}}, window.location.origin);
window.close();
</script></body></html>""")


@router.get("/token-status/{knowledge_id}")
async def get_token_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
) -> dict:
    """Check if a valid refresh token is stored for a knowledge base."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    return {
        "knowledge_id": knowledge_id,
        "has_stored_token": sync_info.get("has_stored_token", False),
        "token_stored_at": sync_info.get("token_stored_at"),
        "needs_reauth": sync_info.get("needs_reauth", False),
    }


@router.delete("/revoke-token/{knowledge_id}")
async def revoke_onedrive_token(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Revoke stored OneDrive refresh token for a knowledge base."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user.id)
    if session:
        OAuthSessions.delete_session_by_id(session.id)

    meta = knowledge.meta or {}
    if "onedrive_sync" in meta:
        meta["onedrive_sync"]["has_stored_token"] = False
        meta["onedrive_sync"].pop("token_stored_at", None)
        Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    return {"success": True, "message": "Token revoked"}
```

#### 4. Add frontend API functions for auth flow
**File**: `src/lib/apis/onedrive/index.ts`
**Location**: Add after existing exports

```typescript
export async function initiateOneDriveAuth(
    token: string,
    knowledgeId: string
): Promise<{ auth_url: string }> {
    const res = await fetch(
        `${WEBUI_API_BASE_URL}/onedrive/auth/initiate?knowledge_id=${encodeURIComponent(knowledgeId)}`,
        {
            headers: { Authorization: `Bearer ${token}` }
        }
    );
    if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to initiate auth');
    }
    return res.json();
}

export async function getTokenStatus(
    token: string,
    knowledgeId: string
): Promise<{
    knowledge_id: string;
    has_stored_token: boolean;
    token_stored_at?: number;
    needs_reauth: boolean;
}> {
    const res = await fetch(
        `${WEBUI_API_BASE_URL}/onedrive/token-status/${encodeURIComponent(knowledgeId)}`,
        {
            headers: { Authorization: `Bearer ${token}` }
        }
    );
    if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to get token status');
    }
    return res.json();
}

export async function revokeOneDriveToken(
    token: string,
    knowledgeId: string
): Promise<{ success: boolean; message: string }> {
    const res = await fetch(
        `${WEBUI_API_BASE_URL}/onedrive/revoke-token/${encodeURIComponent(knowledgeId)}`,
        {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${token}` }
        }
    );
    if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to revoke token');
    }
    return res.json();
}
```

#### 5. Update `__init__.py` to export new auth module
**File**: `backend/open_webui/services/onedrive/__init__.py`

Add the auth module imports and exports.

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] TypeScript compiles: `npm run check`
- [ ] Backend linting: `npm run lint:backend`
- [ ] Frontend linting: `npm run lint:frontend`
- [ ] `GET /api/v1/onedrive/auth/initiate?knowledge_id=test` returns 501 when `ONEDRIVE_CLIENT_SECRET_BUSINESS` is not set
- [ ] `GET /api/v1/onedrive/auth/initiate?knowledge_id=test` returns 401 without auth header

#### Manual Verification:
- [ ] With `ONEDRIVE_CLIENT_SECRET_BUSINESS` configured and Web platform added to Azure AD app:
  - [ ] `/auth/initiate` returns a valid Microsoft authorization URL
  - [ ] Opening the URL in a browser shows Microsoft consent screen with `offline_access`
  - [ ] After consent, callback stores encrypted token in `oauth_session` table
  - [ ] KB metadata shows `has_stored_token: true`
  - [ ] `/token-status/{kb_id}` returns correct status
  - [ ] `/revoke-token/{kb_id}` deletes the stored token

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to the next phase.

---

## Phase 2: Token Refresh Service

### Overview
Create a service to refresh access tokens using stored refresh tokens via direct HTTP calls to Microsoft's token endpoint. Handle rotating refresh tokens and detect revocation.

### Changes Required:

#### 1. Create token refresh service
**File**: `backend/open_webui/services/onedrive/token_refresh.py` (new file)

```python
"""OneDrive token refresh service.

Refreshes access tokens using stored refresh tokens via direct HTTP calls
to Microsoft's token endpoint. Handles rotating refresh tokens.
"""

import logging
import time
from typing import Optional, Tuple
from dataclasses import dataclass

import httpx

from open_webui.config import (
    ONEDRIVE_CLIENT_ID_BUSINESS,
    ONEDRIVE_CLIENT_SECRET_BUSINESS,
    ONEDRIVE_SHAREPOINT_TENANT_ID,
)
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)

ACCESS_TOKEN_BUFFER_SECONDS = 5 * 60


@dataclass
class TokenRefreshResult:
    success: bool
    access_token: Optional[str] = None
    expires_in: Optional[int] = None
    error: Optional[str] = None
    needs_reauth: bool = False


async def refresh_access_token(
    knowledge_id: str,
    user_id: str,
) -> TokenRefreshResult:
    """Refresh access token using stored refresh token.

    Handles Microsoft's rotating refresh tokens by storing the new token
    after each refresh. Detects revocation via invalid_grant error.
    """
    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)

    if not session:
        return TokenRefreshResult(success=False, error="No stored token", needs_reauth=True)

    token_data = session.token
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return TokenRefreshResult(success=False, error="No refresh token", needs_reauth=True)

    client_id = ONEDRIVE_CLIENT_ID_BUSINESS
    client_secret = ONEDRIVE_CLIENT_SECRET_BUSINESS
    tenant_id = token_data.get("tenant_id") or ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"

    if not client_id or not client_secret:
        return TokenRefreshResult(success=False, error="OneDrive client credentials not configured")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "https://graph.microsoft.com/Files.Read.All offline_access",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )

        if response.status_code == 200:
            tokens = response.json()
            new_refresh_token = tokens.get("refresh_token")

            # Microsoft rotates refresh tokens — store the new one
            if new_refresh_token:
                token_data["refresh_token"] = new_refresh_token
                token_data["last_refreshed_at"] = int(time.time())
                token_data["expires_at"] = int(time.time()) + (90 * 24 * 60 * 60)
                OAuthSessions.update_session_by_id(session.id, token_data)

            log.info(f"Refreshed token for KB {knowledge_id}")
            return TokenRefreshResult(
                success=True,
                access_token=tokens.get("access_token"),
                expires_in=tokens.get("expires_in", 3600),
            )

        elif response.status_code == 400:
            error_data = response.json()
            error_code = error_data.get("error", "unknown")
            error_desc = error_data.get("error_description", "")

            if error_code == "invalid_grant":
                log.warning(f"Refresh token revoked for KB {knowledge_id}: {error_desc}")
                await _mark_needs_reauth(knowledge_id)
                return TokenRefreshResult(
                    success=False, error=f"Token revoked: {error_desc}", needs_reauth=True
                )

            log.error(f"Token refresh error for KB {knowledge_id}: {error_code}")
            return TokenRefreshResult(success=False, error=f"{error_code}: {error_desc}")

        else:
            log.error(f"Unexpected HTTP {response.status_code} for KB {knowledge_id}")
            return TokenRefreshResult(success=False, error=f"HTTP {response.status_code}")

    except httpx.TimeoutException:
        return TokenRefreshResult(success=False, error="Request timeout")
    except Exception as e:
        log.exception(f"Token refresh error for KB {knowledge_id}: {e}")
        return TokenRefreshResult(success=False, error=str(e))


async def get_valid_access_token(
    knowledge_id: str,
    user_id: str,
    cached_token: Optional[str] = None,
    cached_expires_at: Optional[int] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """Get a valid access token, refreshing if necessary.

    Returns (access_token, expires_at) or (None, None) if unavailable.
    """
    now = int(time.time())
    if cached_token and cached_expires_at and cached_expires_at > now + ACCESS_TOKEN_BUFFER_SECONDS:
        return cached_token, cached_expires_at

    result = await refresh_access_token(knowledge_id, user_id)
    if result.success and result.access_token:
        expires_at = now + (result.expires_in or 3600)
        return result.access_token, expires_at

    return None, None


async def _mark_needs_reauth(knowledge_id: str):
    """Mark a knowledge base as needing re-authentication."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if knowledge:
        meta = knowledge.meta or {}
        if "onedrive_sync" not in meta:
            meta["onedrive_sync"] = {}
        meta["onedrive_sync"]["needs_reauth"] = True
        meta["onedrive_sync"]["has_stored_token"] = False
        meta["onedrive_sync"]["status"] = "needs_reauth"
        meta["onedrive_sync"]["error"] = "Token expired or revoked. Please re-authenticate."
        Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors
- [ ] Import works: `python -c "from open_webui.services.onedrive.token_refresh import refresh_access_token"`

#### Manual Verification:
- [ ] Complete Phase 1 auth flow to store a refresh token
- [ ] Call `refresh_access_token()` (e.g., via a test script or shell) and verify it returns a new access token
- [ ] Verify the new refresh token is stored (check DB: `token_data.last_refreshed_at` updated)
- [ ] Revoke the token in Azure AD portal, call refresh again, verify `needs_reauth` is set in KB metadata

**Implementation Note**: After completing this phase, pause for manual verification.

---

## Phase 3: Frontend Integration

### Overview
Integrate the backend auth flow into the sync UI. Before opening the file picker, check for a stored token and trigger the auth popup if needed. Modify the sync endpoint to use backend tokens when available.

### Changes Required:

#### 1. Make `access_token` and `user_token` optional in the sync endpoint
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Location**: `SyncItemsRequest` at line 28

Change `access_token` and `user_token` from required to optional:

```python
class SyncItemsRequest(BaseModel):
    """Request to sync multiple OneDrive items to a Knowledge base."""

    knowledge_id: str
    items: List[SyncItem]
    access_token: Optional[str] = None  # Optional: backend uses stored token if absent
    user_token: Optional[str] = None    # Optional: generated internally for scheduled syncs
    clear_exclusions: bool = False
```

#### 2. Update sync endpoint to resolve tokens from backend when not provided
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Location**: `sync_items` function (line 59) and `sync_items_to_knowledge` (line 145)

In `sync_items`, before queuing the background task, resolve tokens:

```python
    # Resolve access token: prefer provided, fall back to stored refresh token
    access_token = request.access_token
    user_token = request.user_token

    if not access_token:
        # Use stored refresh token
        from open_webui.services.onedrive.token_refresh import get_valid_access_token
        access_token, _ = await get_valid_access_token(
            knowledge_id=request.knowledge_id, user_id=user.id
        )
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No access token provided and no stored refresh token. "
                "Please authorize OneDrive access first.",
            )

    if not user_token:
        # Generate internal JWT for retrieval API calls
        from open_webui.utils.auth import create_token
        from datetime import timedelta
        user_token = create_token(data={"id": user.id}, expires_delta=timedelta(hours=2))
```

#### 3. Add auth flow trigger and message listener to the frontend
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Update the `oneDriveSyncHandler` function to check for stored token and trigger backend auth popup if needed:

```typescript
import { initiateOneDriveAuth, getTokenStatus } from '$lib/apis/onedrive';

// Add message listener for auth popup result
function waitForAuthPopup(popup: Window): Promise<boolean> {
    return new Promise((resolve) => {
        const timeout = setTimeout(() => {
            window.removeEventListener('message', handler);
            resolve(false);
        }, 300000); // 5 min timeout

        function handler(event: MessageEvent) {
            if (event.origin !== window.location.origin) return;
            if (event.data?.type === 'onedrive-auth-success') {
                clearTimeout(timeout);
                window.removeEventListener('message', handler);
                resolve(true);
            } else if (event.data?.type === 'onedrive-auth-error') {
                clearTimeout(timeout);
                window.removeEventListener('message', handler);
                resolve(false);
            }
        }

        window.addEventListener('message', handler);

        // Also detect popup closed without completing
        const checkClosed = setInterval(() => {
            if (popup.closed) {
                clearInterval(checkClosed);
                clearTimeout(timeout);
                window.removeEventListener('message', handler);
                resolve(false);
            }
        }, 500);
    });
}

// In the sync handler, before opening the file picker:
const oneDriveSyncHandler = async () => {
    // Check if backend has a stored token
    const tokenStatus = await getTokenStatus(localStorage.token, knowledge.id);

    if (!tokenStatus.has_stored_token || tokenStatus.needs_reauth) {
        // Trigger backend auth flow
        const { auth_url } = await initiateOneDriveAuth(localStorage.token, knowledge.id);
        const popup = window.open(auth_url, 'onedrive-auth', 'width=600,height=700');
        if (!popup) {
            toast.error('Please allow popups for OneDrive authorization');
            return;
        }
        const success = await waitForAuthPopup(popup);
        if (!success) {
            toast.error('OneDrive authorization failed or was cancelled');
            return;
        }
        toast.success('OneDrive connected for auto-sync');
    }

    // Proceed with file picker (unchanged)
    const items = await openOneDriveItemPicker('organizations');
    if (!items || items.length === 0) return;

    // Start sync WITHOUT sending access_token — backend uses stored token
    await startOneDriveSyncItems(localStorage.token, {
        knowledge_id: knowledge.id,
        items: items.map((item) => ({
            type: item.folder ? 'folder' : 'file',
            drive_id: item.parentReference.driveId,
            item_id: item.id,
            item_path: item.name,
            name: item.name,
        })),
        clear_exclusions: true,
        // access_token and user_token omitted — backend resolves them
    });

    // Start polling sync status
    pollOneDriveSyncStatus();
};
```

#### 4. Update the `startOneDriveSyncItems` API function to support optional tokens
**File**: `src/lib/apis/onedrive/index.ts`

Update the request type to make tokens optional:

```typescript
export interface SyncItemsRequest {
    knowledge_id: string;
    items: SyncItem[];
    access_token?: string;   // Optional: backend uses stored token if absent
    user_token?: string;     // Optional: generated internally if absent
    clear_exclusions?: boolean;
}
```

#### 5. Add auto-sync status indicator
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

In the sync status display area, show auto-sync status:

```svelte
{#if knowledge.meta?.onedrive_sync?.has_stored_token}
    <span class="text-xs text-green-600 dark:text-green-400">Auto-sync enabled</span>
{:else if knowledge.meta?.onedrive_sync?.needs_reauth}
    <button class="text-xs text-orange-600 dark:text-orange-400 underline"
        on:click={oneDriveSyncHandler}>
        Re-authorization required
    </button>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend linting: `npm run lint:frontend`
- [ ] Backend linting: `npm run lint:backend`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] Click "Sync from OneDrive" on a KB → Microsoft consent popup appears (first time)
- [ ] After consent, file picker opens
- [ ] Select items → sync starts without errors
- [ ] Backend logs show token refresh (not frontend-provided token)
- [ ] Second sync on same KB → no consent popup, goes straight to file picker
- [ ] "Auto-sync enabled" indicator visible on KB with stored token
- [ ] Re-sync (existing sources) works without access_token in request body

**Implementation Note**: After completing this phase, pause for manual verification.

---

## Phase 4: Scheduler Upgrade

### Overview
Wire the scheduler into application startup and upgrade it to refresh tokens and execute actual syncs instead of just logging.

### Changes Required:

#### 1. Rewrite scheduler to execute syncs
**File**: `backend/open_webui/services/onedrive/scheduler.py`

Replace the current placeholder implementation. Key changes:
- Use `token_refresh.get_valid_access_token()` to obtain tokens
- Construct `OneDriveSyncWorker` and call `worker.sync()`
- Generate internal JWT via `create_token()` for retrieval API calls
- Cache access tokens in memory to avoid unnecessary refreshes
- Handle `needs_reauth` gracefully

Core scheduling loop:

```python
async def _execute_scheduled_sync(kb, sync_info: dict):
    """Execute a scheduled sync for a knowledge base."""
    from open_webui.services.onedrive.token_refresh import get_valid_access_token
    from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker
    from open_webui.utils.auth import create_token
    from datetime import timedelta

    log.info(f"Executing scheduled sync for: {kb.name} ({kb.id})")

    # Get or refresh access token
    cached = _token_cache.get(kb.id, (None, None))
    access_token, expires_at = await get_valid_access_token(
        knowledge_id=kb.id,
        user_id=kb.user_id,
        cached_token=cached[0],
        cached_expires_at=cached[1],
    )

    if not access_token:
        log.warning(f"Cannot get token for {kb.name} — needs re-auth")
        return

    _token_cache[kb.id] = (access_token, expires_at)

    # Generate internal JWT for retrieval API calls
    user_token = create_token(data={"id": kb.user_id}, expires_delta=timedelta(hours=2))

    sources = sync_info.get("sources", [])
    if not sources:
        log.warning(f"No sources configured for {kb.name}")
        return

    try:
        meta = kb.meta or {}
        meta["onedrive_sync"]["status"] = "syncing"
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)

        worker = OneDriveSyncWorker(
            knowledge_id=kb.id,
            sources=sources,
            access_token=access_token,
            user_id=kb.user_id,
            user_token=user_token,
        )
        await worker.sync()

        log.info(f"Scheduled sync completed for {kb.name}")

    except Exception as e:
        log.exception(f"Scheduled sync failed for {kb.name}: {e}")
        meta = kb.meta or {}
        if "onedrive_sync" in meta:
            meta["onedrive_sync"]["status"] = "error"
            meta["onedrive_sync"]["error"] = str(e)[:500]
            Knowledges.update_knowledge_meta_by_id(kb.id, meta)
```

The `_process_knowledge_base` function checks:
1. `sync_info` exists with `"sources"` key
2. `has_stored_token` is true
3. `needs_reauth` is false
4. `status` is not `"syncing"`
5. Enough time has passed since `last_sync_at`

#### 2. Wire scheduler into app startup
**File**: `backend/open_webui/main.py`
**Location**: Inside the `lifespan` function, after other initialization (around line 711, before `yield`)

```python
    # Start OneDrive sync scheduler
    if app.state.config.ENABLE_ONEDRIVE_SYNC:
        from open_webui.services.onedrive.scheduler import start_scheduler, stop_scheduler
        start_scheduler()
        log.info("OneDrive sync scheduler started")
```

And in the shutdown section (after `yield`, around line 715):

```python
    # Stop OneDrive sync scheduler
    if app.state.config.ENABLE_ONEDRIVE_SYNC:
        from open_webui.services.onedrive.scheduler import stop_scheduler
        stop_scheduler()
```

#### 3. Handle `410 Gone` in GraphClient
**File**: `backend/open_webui/services/onedrive/graph_client.py`
**Location**: In `get_drive_delta()` at line 129

Add handling for `410 Gone` response, which means the delta token has expired:

```python
    # In _request_with_retry or in get_drive_delta:
    # If response is 410 Gone, the delta link is stale.
    # Clear the delta link and retry with a full sync.
    if response.status_code == 410:
        log.warning(f"Delta token expired (410 Gone) for drive {drive_id}, folder {folder_id}")
        raise DeltaTokenExpiredError(drive_id, folder_id)
```

In the sync_worker, catch `DeltaTokenExpiredError`, clear the stored `delta_link` from the source, and retry with a full delta query (no token).

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors
- [ ] Logs show `"OneDrive sync scheduler started"` when `ENABLE_ONEDRIVE_SYNC=true`
- [ ] Logs show `"OneDrive sync scheduler stopped"` on shutdown
- [ ] Backend linting: `npm run lint:backend`

#### Manual Verification:
- [ ] Set `ONEDRIVE_SYNC_INTERVAL_MINUTES=1` for testing
- [ ] Complete a manual sync (stores refresh token and sources)
- [ ] Wait 1-2 minutes
- [ ] Observe in logs: `"Knowledge base X is due for sync"`
- [ ] Observe in logs: `"Refreshed token for KB X"`
- [ ] Observe in logs: `"Scheduled sync completed for X"`
- [ ] Add a new file to the OneDrive folder
- [ ] Wait for next sync cycle
- [ ] Verify file appears in knowledge base
- [ ] Revoke token in Azure AD, wait for sync, verify `needs_reauth` status
- [ ] UI shows "Re-authorization required" for that KB

**Implementation Note**: This is the critical end-to-end phase. Thorough manual testing required before proceeding.

---

## Phase 5: Multi-Datasource Abstraction

### Overview
Extract the generic patterns from the OneDrive implementation into abstract interfaces, following the `PermissionProvider` pattern at `services/permissions/`. This establishes the architecture for future datasources (Google Drive, SharePoint, Slack).

### Changes Required:

#### 1. Create the sync package directory structure

```
backend/open_webui/services/sync/
├── __init__.py
├── provider.py              # SyncProvider ABC + data models
├── token_manager.py         # TokenManager ABC
├── registry.py              # SyncProviderRegistry
├── orchestrator.py          # Generic sync scheduler
└── providers/
    ├── __init__.py
    └── onedrive.py          # OneDriveSyncProvider + OneDriveTokenManager
```

#### 2. Define SyncProvider ABC
**File**: `backend/open_webui/services/sync/provider.py`

```python
"""Abstract base class for datasource sync providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

SUPPORTED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".html", ".htm", ".json", ".xml", ".csv",
}


@dataclass
class FileChange:
    """A file that changed in the source."""
    item_id: str
    name: str
    drive_id: str
    is_deleted: bool = False
    content_hash: Optional[str] = None
    web_url: Optional[str] = None
    size: Optional[int] = None
    relative_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncChangeset:
    """Result of collecting changes from a source."""
    changes: List[FileChange]
    new_cursor: Optional[str] = None  # Delta link, page token, etc.
    cursor_expired: bool = False       # True if full re-sync needed


@dataclass
class SyncResult:
    """Result of executing a sync operation."""
    files_processed: int = 0
    files_failed: int = 0
    files_deleted: int = 0
    total_found: int = 0
    error: Optional[str] = None


class SyncProvider(ABC):
    """Abstract interface for datasource sync operations."""

    source_type: str  # e.g., "onedrive", "google_drive", "sharepoint"

    @abstractmethod
    async def collect_changes(
        self,
        source_config: Dict[str, Any],
        access_token: str,
    ) -> SyncChangeset:
        """Discover what changed since last sync.

        Args:
            source_config: Provider-specific config (drive_id, folder_id, delta_link, etc.)
            access_token: Valid access token for the source API

        Returns:
            SyncChangeset with files to add/update/delete and new cursor
        """
        ...

    @abstractmethod
    async def download_file(
        self,
        file_ref: FileChange,
        access_token: str,
    ) -> bytes:
        """Download file content from the source."""
        ...

    @abstractmethod
    async def get_permissions(
        self,
        source_config: Dict[str, Any],
        access_token: str,
    ) -> Set[str]:
        """Get emails of users with access to the source resource."""
        ...

    def get_supported_extensions(self) -> Set[str]:
        """File extensions this provider can handle."""
        return SUPPORTED_EXTENSIONS
```

#### 3. Define TokenManager ABC
**File**: `backend/open_webui/services/sync/token_manager.py`

```python
"""Abstract base class for datasource token lifecycle management."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class TokenResult:
    """Result of a token operation."""
    success: bool
    access_token: Optional[str] = None
    expires_in: Optional[int] = None
    error: Optional[str] = None
    needs_reauth: bool = False


class TokenManager(ABC):
    """Abstract interface for datasource token lifecycle management."""

    source_type: str

    @abstractmethod
    async def get_valid_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> TokenResult:
        """Get a valid access token, refreshing if needed.

        Returns TokenResult with access_token or error.
        """
        ...

    @abstractmethod
    async def store_token(
        self,
        knowledge_id: str,
        user_id: str,
        token_data: dict,
    ) -> bool:
        """Store authentication credentials securely."""
        ...

    @abstractmethod
    async def revoke_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> bool:
        """Revoke/delete stored credentials."""
        ...

    @abstractmethod
    async def has_valid_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> bool:
        """Check if a valid token exists (without refreshing)."""
        ...
```

#### 4. Define SyncProviderRegistry
**File**: `backend/open_webui/services/sync/registry.py`

Following the `PermissionProviderRegistry` pattern at `services/permissions/registry.py`:

```python
"""Central registry for sync providers and token managers."""

import logging
from typing import Dict, List, Optional

from open_webui.services.sync.provider import SyncProvider
from open_webui.services.sync.token_manager import TokenManager

log = logging.getLogger(__name__)


class SyncProviderRegistry:
    """Central registry for sync providers."""

    _providers: Dict[str, SyncProvider] = {}
    _token_managers: Dict[str, TokenManager] = {}

    @classmethod
    def register(
        cls,
        provider: SyncProvider,
        token_manager: TokenManager,
    ) -> None:
        """Register a sync provider with its token manager."""
        cls._providers[provider.source_type] = provider
        cls._token_managers[provider.source_type] = token_manager
        log.info(f"Registered sync provider: {provider.source_type}")

    @classmethod
    def unregister(cls, source_type: str) -> None:
        cls._providers.pop(source_type, None)
        cls._token_managers.pop(source_type, None)

    @classmethod
    def get_provider(cls, source_type: str) -> Optional[SyncProvider]:
        return cls._providers.get(source_type)

    @classmethod
    def get_token_manager(cls, source_type: str) -> Optional[TokenManager]:
        return cls._token_managers.get(source_type)

    @classmethod
    def get_all_providers(cls) -> List[SyncProvider]:
        return list(cls._providers.values())

    @classmethod
    def has_provider(cls, source_type: str) -> bool:
        return source_type in cls._providers

    @classmethod
    def clear(cls) -> None:
        """Clear all registrations. Used for testing."""
        cls._providers.clear()
        cls._token_managers.clear()
```

#### 5. Implement OneDrive providers
**File**: `backend/open_webui/services/sync/providers/onedrive.py`

Wrap existing `GraphClient`, `token_refresh`, and sync logic:

```python
"""OneDrive implementations of SyncProvider and TokenManager."""

from typing import Any, Dict, Set

from open_webui.services.sync.provider import SyncProvider, SyncChangeset, FileChange
from open_webui.services.sync.token_manager import TokenManager, TokenResult
from open_webui.services.onedrive.graph_client import GraphClient
from open_webui.services.onedrive.token_refresh import (
    refresh_access_token,
    get_valid_access_token,
)
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.knowledge import Knowledges


class OneDriveSyncProvider(SyncProvider):
    source_type = "onedrive"

    async def collect_changes(
        self,
        source_config: Dict[str, Any],
        access_token: str,
    ) -> SyncChangeset:
        """Collect changes using Microsoft Graph delta queries."""
        client = GraphClient(access_token)
        drive_id = source_config["drive_id"]
        folder_id = source_config["item_id"]
        delta_link = source_config.get("delta_link")

        try:
            items, new_delta_link = await client.get_drive_delta(
                drive_id=drive_id,
                folder_id=folder_id,
                delta_link=delta_link,
            )
        except Exception as e:
            if "410" in str(e):
                return SyncChangeset(changes=[], cursor_expired=True)
            raise

        changes = []
        for item in items:
            if "@removed" in item or "deleted" in item:
                changes.append(FileChange(
                    item_id=item["id"],
                    name=item.get("name", ""),
                    drive_id=drive_id,
                    is_deleted=True,
                ))
            elif "file" in item:
                changes.append(FileChange(
                    item_id=item["id"],
                    name=item.get("name", ""),
                    drive_id=drive_id,
                    content_hash=item.get("file", {}).get("hashes", {}).get("sha256Hash"),
                    web_url=item.get("webUrl"),
                    size=item.get("size"),
                    metadata=item,
                ))

        return SyncChangeset(changes=changes, new_cursor=new_delta_link)

    async def download_file(
        self,
        file_ref: FileChange,
        access_token: str,
    ) -> bytes:
        client = GraphClient(access_token)
        return await client.download_file(file_ref.drive_id, file_ref.item_id)

    async def get_permissions(
        self,
        source_config: Dict[str, Any],
        access_token: str,
    ) -> Set[str]:
        client = GraphClient(access_token)
        drive_id = source_config["drive_id"]
        folder_id = source_config["item_id"]
        permissions = await client.get_folder_permissions(drive_id, folder_id)
        emails = set()
        for perm in permissions:
            for field_name in ["grantedTo", "grantedToIdentities", "grantedToIdentitiesV2"]:
                identities = perm.get(field_name, [])
                if isinstance(identities, dict):
                    identities = [identities]
                for identity in identities:
                    user = identity.get("user", {})
                    email = user.get("email") or user.get("displayName")
                    if email and "@" in email:
                        emails.add(email.lower())
        return emails


class OneDriveTokenManager(TokenManager):
    source_type = "onedrive"

    async def get_valid_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> TokenResult:
        access_token, expires_at = await get_valid_access_token(knowledge_id, user_id)
        if access_token:
            import time
            return TokenResult(
                success=True,
                access_token=access_token,
                expires_in=(expires_at - int(time.time())) if expires_at else 3600,
            )
        return TokenResult(success=False, error="Token unavailable", needs_reauth=True)

    async def store_token(
        self,
        knowledge_id: str,
        user_id: str,
        token_data: dict,
    ) -> bool:
        provider = f"onedrive:{knowledge_id}"
        existing = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
        if existing:
            OAuthSessions.update_session_by_id(existing.id, token_data)
        else:
            OAuthSessions.create_session(user_id=user_id, provider=provider, token=token_data)
        return True

    async def revoke_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> bool:
        provider = f"onedrive:{knowledge_id}"
        session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
        if session:
            OAuthSessions.delete_session_by_id(session.id)
        return True

    async def has_valid_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> bool:
        provider = f"onedrive:{knowledge_id}"
        session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
        return session is not None and bool(session.token.get("refresh_token"))
```

#### 6. Create generic SyncOrchestrator
**File**: `backend/open_webui/services/sync/orchestrator.py`

A generic scheduler that works with any registered provider:

```python
"""Generic sync orchestrator that coordinates across all registered providers."""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from open_webui.models.knowledge import Knowledges
from open_webui.config import ONEDRIVE_SYNC_INTERVAL_MINUTES
from open_webui.services.sync.registry import SyncProviderRegistry

log = logging.getLogger(__name__)

_orchestrator_task: Optional[asyncio.Task] = None
_token_cache: Dict[str, tuple] = {}  # knowledge_id → (access_token, expires_at)


async def run_sync_orchestrator():
    """Main orchestrator loop. Checks all providers for due syncs."""
    log.info("Sync orchestrator started")

    while True:
        try:
            await _check_all_providers()
        except Exception as e:
            log.exception(f"Orchestrator error: {e}")

        await asyncio.sleep(60)  # Check every minute


async def _check_all_providers():
    """Check all knowledge bases across all registered providers."""
    all_knowledge = Knowledges.get_knowledge_bases()
    now = int(datetime.utcnow().timestamp())

    for kb in all_knowledge:
        try:
            meta = kb.meta or {}
            # Check each registered source type
            for source_type_key in ["onedrive_sync"]:  # Extensible: add google_drive_sync, etc.
                sync_info = meta.get(source_type_key)
                if not sync_info:
                    continue

                source_type = source_type_key.replace("_sync", "")  # "onedrive"
                provider = SyncProviderRegistry.get_provider(source_type)
                token_mgr = SyncProviderRegistry.get_token_manager(source_type)

                if not provider or not token_mgr:
                    continue

                await _process_kb_for_provider(kb, sync_info, source_type_key, provider, token_mgr, now)

        except Exception as e:
            log.exception(f"Error processing KB {kb.id}: {e}")


async def _process_kb_for_provider(kb, sync_info, meta_key, provider, token_mgr, now):
    """Check if a KB is due for sync with a specific provider."""
    if not sync_info.get("has_stored_token"):
        return
    if sync_info.get("needs_reauth"):
        return
    if sync_info.get("status") == "syncing":
        return

    interval = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60  # TODO: per-provider interval
    last_sync = sync_info.get("last_sync_at", 0)
    if now - last_sync < interval:
        return

    log.info(f"KB {kb.name} ({kb.id}) due for {provider.source_type} sync")
    await _execute_sync(kb, sync_info, meta_key, provider, token_mgr)


async def _execute_sync(kb, sync_info, meta_key, provider, token_mgr):
    """Execute a sync using the abstraction layer.

    Note: For the initial implementation, this delegates to the existing
    OneDriveSyncWorker for full file processing (download, upload to storage,
    RAG processing). The SyncProvider abstraction handles change detection
    and token management. Full extraction of the processing loop into the
    orchestrator is a future enhancement.
    """
    # Get access token via the token manager
    token_result = await token_mgr.get_valid_token(kb.id, kb.user_id)
    if not token_result.success:
        log.warning(f"Cannot get token for {kb.name}: {token_result.error}")
        return

    # For OneDrive, delegate to existing sync_worker for now
    if provider.source_type == "onedrive":
        from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker
        from open_webui.utils.auth import create_token
        from datetime import timedelta

        user_token = create_token(data={"id": kb.user_id}, expires_delta=timedelta(hours=2))

        try:
            meta = kb.meta or {}
            meta[meta_key]["status"] = "syncing"
            Knowledges.update_knowledge_meta_by_id(kb.id, meta)

            worker = OneDriveSyncWorker(
                knowledge_id=kb.id,
                sources=sync_info.get("sources", []),
                access_token=token_result.access_token,
                user_id=kb.user_id,
                user_token=user_token,
            )
            await worker.sync()

            log.info(f"Scheduled sync completed for {kb.name}")

        except Exception as e:
            log.exception(f"Scheduled sync failed for {kb.name}: {e}")
            meta = kb.meta or {}
            if meta_key in meta:
                meta[meta_key]["status"] = "error"
                meta[meta_key]["error"] = str(e)[:500]
                Knowledges.update_knowledge_meta_by_id(kb.id, meta)


def start_orchestrator():
    global _orchestrator_task
    if _orchestrator_task is None or _orchestrator_task.done():
        _orchestrator_task = asyncio.create_task(run_sync_orchestrator())
        log.info("Sync orchestrator started")


def stop_orchestrator():
    global _orchestrator_task
    if _orchestrator_task and not _orchestrator_task.done():
        _orchestrator_task.cancel()
        log.info("Sync orchestrator stopped")
    _orchestrator_task = None
```

#### 7. Register providers and start orchestrator in main.py
**File**: `backend/open_webui/main.py`

Replace the scheduler startup (from Phase 4) with orchestrator startup, and add provider registration alongside permission provider registration:

At the permission provider registration site (line 1530):

```python
# Register permission providers for source access validation
from open_webui.services.permissions.registry import PermissionProviderRegistry
from open_webui.services.permissions.providers.onedrive import OneDrivePermissionProvider
PermissionProviderRegistry.register(OneDrivePermissionProvider())

# Register sync providers for background sync
from open_webui.services.sync.registry import SyncProviderRegistry
from open_webui.services.sync.providers.onedrive import OneDriveSyncProvider, OneDriveTokenManager
SyncProviderRegistry.register(OneDriveSyncProvider(), OneDriveTokenManager())
log.info("Registered OneDrive sync + permission providers")
```

In the `lifespan` function, replace the OneDrive-specific scheduler calls with the generic orchestrator:

```python
    # Start sync orchestrator (generic, handles all registered providers)
    if app.state.config.ENABLE_ONEDRIVE_SYNC:  # TODO: generalize to ENABLE_BACKGROUND_SYNC
        from open_webui.services.sync.orchestrator import start_orchestrator, stop_orchestrator
        start_orchestrator()
```

#### 8. Update `__init__.py` files

**File**: `backend/open_webui/services/sync/__init__.py`

```python
"""Multi-datasource sync abstraction layer."""

from open_webui.services.sync.provider import SyncProvider, SyncChangeset, FileChange, SyncResult
from open_webui.services.sync.token_manager import TokenManager, TokenResult
from open_webui.services.sync.registry import SyncProviderRegistry
from open_webui.services.sync.orchestrator import start_orchestrator, stop_orchestrator

__all__ = [
    "SyncProvider", "SyncChangeset", "FileChange", "SyncResult",
    "TokenManager", "TokenResult",
    "SyncProviderRegistry",
    "start_orchestrator", "stop_orchestrator",
]
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors
- [ ] Backend linting: `npm run lint:backend`
- [ ] Import works: `python -c "from open_webui.services.sync import SyncProviderRegistry; print(SyncProviderRegistry.get_all_providers())"`
- [ ] Logs show `"Registered sync provider: onedrive"` at startup
- [ ] Logs show `"Sync orchestrator started"` when `ENABLE_ONEDRIVE_SYNC=true`

#### Manual Verification:
- [ ] Full end-to-end test: connect OneDrive → manual sync → wait for scheduled sync → files appear
- [ ] Behavior is identical to Phase 4 (regression check)
- [ ] `SyncProviderRegistry.get_provider("onedrive")` returns the OneDrive provider
- [ ] `SyncProviderRegistry.get_token_manager("onedrive")` returns the OneDrive token manager

**Implementation Note**: This phase is a refactoring of Phase 4's scheduler into the abstraction layer. All existing sync functionality must continue working. Run the same manual tests from Phase 4 to verify no regressions.

---

## Testing Strategy

### Unit Tests
- `test_token_refresh.py`: Mock `httpx` responses to test refresh logic, rotating tokens, `invalid_grant` handling
- `test_auth_flow.py`: Test PKCE generation, state management, flow expiry
- `test_sync_registry.py`: Test provider registration, lookup, clear

### Integration Tests
- Full auth flow: initiate → callback → token stored → token status
- Full sync cycle: auth → select items → sync → scheduled re-sync
- Token revocation: revoke in Azure AD → next refresh fails → `needs_reauth` set
- Error recovery: network timeout during refresh → retries on next interval

### Manual Testing Steps
1. Configure `ONEDRIVE_CLIENT_SECRET_BUSINESS` and add Web platform to Azure AD app
2. Create a knowledge base
3. Click "Sync from OneDrive" → consent popup → file picker → select items → sync completes
4. Verify in DB: `oauth_session` record exists with encrypted token, provider = `onedrive:{kb_id}`
5. Verify KB metadata: `has_stored_token: true`
6. Set `ONEDRIVE_SYNC_INTERVAL_MINUTES=1`, restart backend
7. Wait and observe automatic sync in logs
8. Add a new file to the OneDrive folder
9. Wait for next sync cycle, verify file appears
10. Revoke refresh token in Azure AD portal
11. Wait for next sync attempt
12. Verify KB shows `needs_reauth` status in UI
13. Click "Re-authorization required" → re-authenticate → auto-sync resumes

## Security Considerations

| Concern | Mitigation |
|---------|-----------|
| **Tokens at rest** | Fernet encryption via `OAuthSession` model (existing) |
| **Client secret** | Environment variable only (`ONEDRIVE_CLIENT_SECRET_BUSINESS`), never in admin UI or DB |
| **PKCE** | Used for auth code exchange even with confidential client (Microsoft recommendation) |
| **State parameter** | Random token with server-side verification, 10-minute TTL |
| **Token rotation** | New refresh token stored after each Microsoft refresh (write-before-use) |
| **Revocation detection** | `invalid_grant` → KB marked `needs_reauth`, token deleted |
| **Token isolation** | Per-KB tokens (`provider = "onedrive:{kb_id}"`), not shared |
| **Internal JWT** | Short-lived (2 hours), only for internal retrieval API calls during scheduled sync |
| **Callback endpoint** | No auth required (handles Microsoft redirect), but validates state parameter |
| **Scope minimization** | Only `Files.Read.All` + `offline_access` (read-only + refresh) |

## Migration Notes

- **No database schema changes** — uses existing `oauth_session` table
- **No breaking changes** — manual sync with frontend MSAL tokens continues to work
- **One-time setup** per deployment: add Web platform + client secret to Azure AD app
- **One-time action** per KB: user must complete the backend auth flow to enable auto-sync
- **Existing synced KBs** need one click on "Sync from OneDrive" to trigger the auth flow

## References

- Research document: `thoughts/shared/research/2026-02-04-background-sync-multi-datasource-architecture.md`
- Existing OneDrive token plan: `thoughts/shared/plans/2026-01-18-onedrive-refresh-token-storage.md`
- PermissionProvider pattern: `backend/open_webui/services/permissions/provider.py:48-135`
- PermissionProviderRegistry: `backend/open_webui/services/permissions/registry.py:16-70`
- OAuthSession encryption: `backend/open_webui/models/oauth_sessions.py:69-105`
- OneDrive sync worker: `backend/open_webui/services/onedrive/sync_worker.py:84-103`
- Scheduler (current placeholder): `backend/open_webui/services/onedrive/scheduler.py:87-92`
- Microsoft refresh token docs: https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens
- Microsoft auth code flow: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
- Microsoft delta query API: https://learn.microsoft.com/en-us/graph/api/driveitem-delta
- Redirect URI platform types: https://learn.microsoft.com/en-us/entra/identity-platform/reply-url
