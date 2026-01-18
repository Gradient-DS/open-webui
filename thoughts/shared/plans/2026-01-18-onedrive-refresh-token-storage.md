# OneDrive Refresh Token Storage Implementation Plan

## Overview

Implement automatic OneDrive sync by storing encrypted refresh tokens server-side. This enables the scheduler to refresh access tokens and execute syncs without user interaction.

**Approach**: Store raw encrypted refresh tokens (like Airweave does) rather than relying on MSAL cache. This provides full control over token lifecycle and is the recommended pattern for background sync operations.

## Current State Analysis

### What Exists
- **Scheduler infrastructure** (`services/onedrive/scheduler.py`) - exists but doesn't execute syncs
- **OAuthSession model** (`models/oauth_sessions.py`) - Fernet-encrypted token storage for other OAuth providers
- **Sync worker** (`services/onedrive/sync_worker.py`) - fully functional, needs valid access token
- **Frontend MSAL** (`utils/onedrive-file-picker.ts`) - acquires delegated tokens

### What's Missing
1. `offline_access` scope not requested - no refresh tokens returned
2. Refresh tokens not sent to backend
3. No backend token refresh mechanism
4. Scheduler doesn't execute syncs

### Key Discoveries
- `OAuthSession` model at `models/oauth_sessions.py:24-42` already uses Fernet encryption
- Encryption key: `OAUTH_SESSION_TOKEN_ENCRYPTION_KEY` (defaults to `WEBUI_SECRET_KEY`)
- Airweave uses same pattern: Fernet encryption + proactive refresh every 25 minutes
- Microsoft issues **rotating refresh tokens** - must store new token after each refresh
- Microsoft refresh tokens last 90 days (rolling expiration)

## Desired End State

After implementation:
1. Users authorize OneDrive sync once with `offline_access` consent
2. Refresh token stored encrypted in database linked to knowledge base
3. Scheduler automatically refreshes access tokens (every 25 min) and executes syncs
4. Tokens remain valid for 90 days with automatic renewal on each refresh
5. Manual re-auth only needed if refresh token is explicitly revoked

### Verification
- Backend logs show "Refreshed OneDrive token for knowledge base X"
- Backend logs show "Executing scheduled sync for knowledge base X"
- Files sync automatically at configured interval without user action
- Tokens remain valid across backend restarts

## What We're NOT Doing

- **MSAL Python library for token refresh** - Direct HTTP is simpler and more reliable for background jobs
- **Application-only auth (client credentials flow)** - Requires admin consent, limited to organizational data
- **Storing access tokens long-term** - These expire in ~1 hour; only refresh tokens are stored
- **Custom encryption** - Reuse existing `OAuthSession` Fernet encryption pattern
- **Personal OneDrive auto-sync** - Focus on organizational/SharePoint first (can extend later)

## Implementation Approach

Based on Airweave's proven pattern:
1. Store raw encrypted refresh tokens in `OAuthSession` table with `provider="onedrive:{knowledge_id}"`
2. Use direct HTTP calls to Microsoft's token endpoint for refresh (not MSAL)
3. Handle rotating tokens by storing the new refresh token after each refresh
4. Implement `TokenManager` pattern for proactive refresh (every 25 minutes)

## Phase 1: Add `offline_access` Scope and Capture Refresh Token

### Overview
Request `offline_access` scope during MSAL authentication to receive refresh tokens from Microsoft, then capture the full token response including refresh_token.

### Changes Required:

#### 1. Update MSAL to request offline_access and expose refresh token
**File**: `src/lib/utils/onedrive-file-picker.ts`
**Changes**: Add `offline_access` scope and create function to get full token response

Add after existing `getGraphApiToken` function (around line 1320):

```typescript
export interface OneDriveFullTokenResponse {
    accessToken: string;
    refreshToken: string | null;
    expiresOn: Date | null;
    tenantId: string;
    accountUsername: string;
}

export async function getGraphApiTokenWithRefresh(
    authorityType?: 'personal' | 'organizations'
): Promise<OneDriveFullTokenResponse> {
    const config = OneDriveConfig.getInstance();
    await config.ensureInitialized(authorityType);

    const currentAuthorityType = config.getAuthorityType();

    // Include offline_access to get refresh token
    const scopes =
        currentAuthorityType === 'organizations'
            ? ['https://graph.microsoft.com/Files.Read.All', 'offline_access']
            : ['Files.Read.All', 'offline_access'];

    const authParams: PopupRequest = { scopes };

    const msalInstance = await config.getMsalInstance(authorityType);

    let response;
    try {
        response = await msalInstance.acquireTokenSilent(authParams);
    } catch {
        const loginResponse = await msalInstance.loginPopup(authParams);
        msalInstance.setActiveAccount(loginResponse.account);
        response = await msalInstance.acquireTokenSilent(authParams);
    }

    // MSAL browser doesn't directly expose refresh_token in the response
    // We need to get it from the token cache
    const tokenCache = msalInstance.getTokenCache();
    let refreshToken: string | null = null;

    // Access the internal cache storage to get refresh token
    // Note: This uses MSAL's internal API - may need adjustment for MSAL updates
    const cacheStorage = (msalInstance as any).browserStorage;
    if (cacheStorage && response.account) {
        const refreshTokenKey = `${response.account.homeAccountId}-${response.account.environment}-refreshtoken-${config.getClientId()}--`;
        const cachedRefreshToken = cacheStorage.getRefreshTokenCredential(refreshTokenKey);
        if (cachedRefreshToken) {
            refreshToken = cachedRefreshToken.secret;
        }
    }

    return {
        accessToken: response.accessToken,
        refreshToken: refreshToken,
        expiresOn: response.expiresOn,
        tenantId: response.account?.tenantId || config.getSharepointTenantId(),
        accountUsername: response.account?.username || ''
    };
}
```

**Alternative approach** - If MSAL cache access is unreliable, we can use a custom token endpoint. Add to `OneDriveConfig`:

```typescript
public getClientId(): string {
    return this.currentAuthorityType === 'organizations'
        ? this.clientIdBusiness
        : this.clientIdPersonal;
}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Linting passes: `npm run lint:frontend`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] OneDrive folder picker still works
- [ ] Browser dev tools show `offline_access` in token scopes
- [ ] `getGraphApiTokenWithRefresh()` returns a refresh token (check console.log)

**Implementation Note**: After completing this phase, pause for manual verification. If MSAL doesn't expose refresh_token reliably, we'll need to implement a backend callback flow instead.

---

## Phase 2: Backend Token Storage with Encryption

### Overview
Create endpoint to receive and store encrypted refresh tokens from frontend, plus token refresh service.

### Changes Required:

#### 1. Add request/response models for token storage
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Changes**: Add models for token storage

```python
class StoreRefreshTokenRequest(BaseModel):
    """Request to store OneDrive refresh token for a knowledge base."""
    knowledge_id: str
    refresh_token: str
    tenant_id: str
    username: str  # For display/audit purposes
    expires_in: Optional[int] = None  # Access token expiry in seconds


class TokenStorageResponse(BaseModel):
    """Response confirming token storage."""
    success: bool
    message: str
    has_stored_token: bool
```

#### 2. Add token storage and revocation endpoints
**File**: `backend/open_webui/routers/onedrive_sync.py`
**Changes**: Add POST endpoint to store token and DELETE to revoke

```python
from open_webui.models.oauth_sessions import OAuthSessions
import time

@router.post("/store-token")
async def store_onedrive_token(
    request: StoreRefreshTokenRequest,
    user: UserModel = Depends(get_verified_user),
) -> TokenStorageResponse:
    """Store OneDrive refresh token for scheduled sync.

    The refresh token is encrypted at rest using Fernet encryption.
    """
    # Verify user owns the knowledge base
    knowledge = Knowledges.get_knowledge_by_id(request.knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Provider format: onedrive:{knowledge_id} for unique lookup
    provider = f"onedrive:{request.knowledge_id}"

    # Token data to encrypt and store
    token_data = {
        "refresh_token": request.refresh_token,
        "tenant_id": request.tenant_id,
        "username": request.username,
        "knowledge_id": request.knowledge_id,
        "stored_at": int(time.time()),
        "expires_at": int(time.time()) + (90 * 24 * 60 * 60),  # 90 days from now
    }

    # Check if session exists, update or create
    existing = OAuthSessions.get_session_by_provider_and_user_id(provider, user.id)
    if existing:
        OAuthSessions.update_session_by_id(existing.id, token_data)
        log.info(f"Updated OneDrive token for knowledge base {request.knowledge_id}")
    else:
        OAuthSessions.create_session(
            user_id=user.id,
            provider=provider,
            token=token_data,
        )
        log.info(f"Stored new OneDrive token for knowledge base {request.knowledge_id}")

    # Update knowledge meta to indicate token is stored
    meta = knowledge.meta or {}
    if "onedrive_sync" not in meta:
        meta["onedrive_sync"] = {}
    meta["onedrive_sync"]["has_stored_token"] = True
    meta["onedrive_sync"]["token_stored_at"] = int(time.time())
    meta["onedrive_sync"]["token_username"] = request.username
    Knowledges.update_knowledge_meta_by_id(request.knowledge_id, meta)

    return TokenStorageResponse(
        success=True,
        message="Token stored successfully",
        has_stored_token=True
    )


@router.delete("/revoke-token/{knowledge_id}")
async def revoke_onedrive_token(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
) -> TokenStorageResponse:
    """Revoke stored OneDrive token for a knowledge base."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user.id)

    if session:
        OAuthSessions.delete_session_by_id(session.id)
        log.info(f"Revoked OneDrive token for knowledge base {knowledge_id}")

    # Update knowledge meta
    meta = knowledge.meta or {}
    if "onedrive_sync" in meta:
        meta["onedrive_sync"]["has_stored_token"] = False
        meta["onedrive_sync"].pop("token_stored_at", None)
        meta["onedrive_sync"].pop("token_username", None)
        Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    return TokenStorageResponse(
        success=True,
        message="Token revoked",
        has_stored_token=False
    )


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
        "token_username": sync_info.get("token_username"),
        "needs_reauth": sync_info.get("needs_reauth", False),
    }
```

#### 3. Add frontend API client functions
**File**: `src/lib/apis/onedrive/index.ts`
**Changes**: Add functions to call token endpoints

```typescript
export interface StoreTokenRequest {
    knowledge_id: string;
    refresh_token: string;
    tenant_id: string;
    username: string;
    expires_in?: number;
}

export interface TokenStorageResponse {
    success: boolean;
    message: string;
    has_stored_token: boolean;
}

export async function storeOneDriveToken(
    token: string,
    request: StoreTokenRequest
): Promise<TokenStorageResponse> {
    const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/store-token`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`
        },
        body: JSON.stringify(request)
    });

    if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to store token');
    }

    return res.json();
}

export async function revokeOneDriveToken(
    token: string,
    knowledgeId: string
): Promise<TokenStorageResponse> {
    const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/revoke-token/${knowledgeId}`, {
        method: 'DELETE',
        headers: {
            Authorization: `Bearer ${token}`
        }
    });

    if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to revoke token');
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
    token_username?: string;
    needs_reauth: boolean;
}> {
    const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/token-status/${knowledgeId}`, {
        headers: {
            Authorization: `Bearer ${token}`
        }
    });

    if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to get token status');
    }

    return res.json();
}
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend linting passes: `npm run lint:backend`
- [ ] Backend formatting: `npm run format:backend`
- [ ] TypeScript compiles: `npm run check`
- [ ] API endpoint responds: `curl -X POST localhost:8080/api/v1/onedrive/store-token` (returns 401 without auth)

#### Manual Verification:
- [ ] Token storage endpoint accepts valid request
- [ ] OAuthSession record created with encrypted token (verify in DB that token field is encrypted gibberish)
- [ ] Knowledge meta shows `has_stored_token: true`
- [ ] Token status endpoint returns correct status

**Implementation Note**: Pause for manual verification before proceeding.

---

## Phase 3: Backend Token Refresh Service

### Overview
Implement service to refresh access tokens using stored refresh tokens via direct HTTP calls to Microsoft's token endpoint.

### Changes Required:

#### 1. Add httpx dependency for async HTTP
**File**: `backend/requirements.txt`
**Changes**: Add httpx for async HTTP client (if not already present)

```
httpx>=0.24.0
```

#### 2. Create token refresh service
**File**: `backend/open_webui/services/onedrive/token_refresh.py` (new file)
**Changes**: Service to refresh OneDrive access tokens

```python
"""OneDrive token refresh service.

Refreshes access tokens for scheduled OneDrive sync using stored refresh tokens.
Based on Airweave's proven pattern for background sync.
"""

import logging
import time
from typing import Optional, Tuple
from dataclasses import dataclass

import httpx

from open_webui.config import (
    ONEDRIVE_CLIENT_ID_BUSINESS,
    ONEDRIVE_SHAREPOINT_TENANT_ID,
)
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)

# Refresh tokens 5 minutes before access token expiry
ACCESS_TOKEN_BUFFER_SECONDS = 5 * 60

# Proactive refresh interval (like Airweave's 25 minutes)
PROACTIVE_REFRESH_INTERVAL_SECONDS = 25 * 60


@dataclass
class TokenRefreshResult:
    """Result of a token refresh operation."""
    success: bool
    access_token: Optional[str] = None
    new_refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    error: Optional[str] = None
    needs_reauth: bool = False


class TokenRefreshError(Exception):
    """Raised when token refresh fails."""
    def __init__(self, message: str, needs_reauth: bool = False):
        super().__init__(message)
        self.needs_reauth = needs_reauth


async def refresh_access_token(
    knowledge_id: str,
    user_id: str
) -> TokenRefreshResult:
    """Refresh access token for a knowledge base using stored refresh token.

    Uses direct HTTP call to Microsoft's token endpoint.
    Handles rotating refresh tokens by storing the new token.

    Args:
        knowledge_id: Knowledge base ID
        user_id: User ID who owns the knowledge base

    Returns:
        TokenRefreshResult with access token or error details
    """
    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)

    if not session:
        log.warning(f"No stored token for knowledge base {knowledge_id}")
        return TokenRefreshResult(
            success=False,
            error="No stored token",
            needs_reauth=True
        )

    token_data = session.token
    refresh_token = token_data.get("refresh_token")
    tenant_id = token_data.get("tenant_id")

    if not refresh_token:
        log.error(f"No refresh_token in stored data for {knowledge_id}")
        return TokenRefreshResult(
            success=False,
            error="No refresh token stored",
            needs_reauth=True
        )

    # Get client credentials
    client_id = ONEDRIVE_CLIENT_ID_BUSINESS.value
    if not client_id:
        log.error("ONEDRIVE_CLIENT_ID_BUSINESS not configured")
        return TokenRefreshResult(
            success=False,
            error="OneDrive client ID not configured"
        )

    # Build token endpoint URL
    authority = tenant_id or ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    token_url = f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/token"

    # Make refresh request
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "https://graph.microsoft.com/Files.Read.All offline_access"
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=30.0
            )

        if response.status_code == 200:
            tokens = response.json()
            new_access_token = tokens.get("access_token")
            new_refresh_token = tokens.get("refresh_token")  # Microsoft rotates refresh tokens
            expires_in = tokens.get("expires_in", 3600)

            # Store the new refresh token (Microsoft rotates them)
            if new_refresh_token:
                token_data["refresh_token"] = new_refresh_token
                token_data["last_refreshed_at"] = int(time.time())
                token_data["expires_at"] = int(time.time()) + (90 * 24 * 60 * 60)  # 90 days
                OAuthSessions.update_session_by_id(session.id, token_data)
                log.debug(f"Stored rotated refresh token for {knowledge_id}")

            log.info(f"Successfully refreshed token for knowledge base {knowledge_id}")

            return TokenRefreshResult(
                success=True,
                access_token=new_access_token,
                new_refresh_token=new_refresh_token,
                expires_in=expires_in
            )

        elif response.status_code == 400:
            error_data = response.json()
            error_code = error_data.get("error", "unknown")
            error_desc = error_data.get("error_description", "Unknown error")

            if error_code == "invalid_grant":
                # Token was revoked - user must re-authenticate
                log.warning(f"Refresh token revoked for {knowledge_id}: {error_desc}")
                await _mark_needs_reauth(knowledge_id)
                return TokenRefreshResult(
                    success=False,
                    error=f"Token revoked: {error_desc}",
                    needs_reauth=True
                )
            else:
                log.error(f"Token refresh error for {knowledge_id}: {error_code} - {error_desc}")
                return TokenRefreshResult(
                    success=False,
                    error=f"{error_code}: {error_desc}"
                )

        else:
            log.error(f"Unexpected response {response.status_code} for {knowledge_id}")
            return TokenRefreshResult(
                success=False,
                error=f"HTTP {response.status_code}"
            )

    except httpx.TimeoutException:
        log.error(f"Token refresh timeout for {knowledge_id}")
        return TokenRefreshResult(
            success=False,
            error="Request timeout"
        )
    except Exception as e:
        log.exception(f"Token refresh error for {knowledge_id}: {e}")
        return TokenRefreshResult(
            success=False,
            error=str(e)
        )


async def _mark_needs_reauth(knowledge_id: str):
    """Mark a knowledge base as needing re-authentication."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if knowledge:
        meta = knowledge.meta or {}
        if "onedrive_sync" in meta:
            meta["onedrive_sync"]["needs_reauth"] = True
            meta["onedrive_sync"]["status"] = "needs_reauth"
            meta["onedrive_sync"]["error"] = "Token expired or revoked. Please re-authenticate."
            Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)


async def get_valid_access_token(
    knowledge_id: str,
    user_id: str,
    cached_token: Optional[str] = None,
    cached_expires_at: Optional[int] = None
) -> Tuple[Optional[str], Optional[int]]:
    """Get a valid access token, refreshing if necessary.

    Implements proactive refresh pattern (refresh before expiry).

    Args:
        knowledge_id: Knowledge base ID
        user_id: User ID
        cached_token: Previously cached access token
        cached_expires_at: Expiry timestamp of cached token

    Returns:
        Tuple of (access_token, expires_at) or (None, None) if refresh fails
    """
    current_time = int(time.time())

    # Check if cached token is still valid (with buffer)
    if cached_token and cached_expires_at:
        if cached_expires_at > current_time + ACCESS_TOKEN_BUFFER_SECONDS:
            return cached_token, cached_expires_at

    # Need to refresh
    result = await refresh_access_token(knowledge_id, user_id)

    if result.success and result.access_token:
        expires_at = current_time + (result.expires_in or 3600)
        return result.access_token, expires_at

    return None, None
```

### Success Criteria:

#### Automated Verification:
- [ ] `pip install httpx` succeeds (or already installed)
- [ ] Backend starts without errors: `open-webui dev`
- [ ] Import works: `python -c "from open_webui.services.onedrive.token_refresh import refresh_access_token"`

#### Manual Verification:
- [ ] Store a refresh token via the API
- [ ] Call `refresh_access_token()` directly and verify it returns a new access token
- [ ] Check that the new refresh token is stored (rotating tokens)

**Implementation Note**: Pause for manual verification before proceeding.

---

## Phase 4: Frontend Integration for Token Storage

### Overview
After successful OneDrive sync setup, store the refresh token for scheduled sync.

### Changes Required:

#### 1. Update sync handler to store refresh token
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Call store-token endpoint after successful sync start

Find the `oneDriveSyncHandler` function and update:

```typescript
import { storeOneDriveToken } from '$lib/apis/onedrive';
import { getGraphApiTokenWithRefresh } from '$lib/utils/onedrive-file-picker';

const oneDriveSyncHandler = async () => {
    // ... existing folder picker code ...
    const folder = await openOneDriveFolderPicker('organizations');

    if (!folder) return;

    // Get token with refresh token
    const tokenResponse = await getGraphApiTokenWithRefresh('organizations');

    // Start sync with access token
    await startOneDriveSync(localStorage.token, {
        knowledge_id: knowledge.id,
        drive_id: folder.driveId,
        folder_id: folder.id,
        folder_path: folder.path,
        access_token: tokenResponse.accessToken,
        user_token: localStorage.token
    });

    // Store refresh token for scheduled sync (if available)
    if (tokenResponse.refreshToken) {
        try {
            await storeOneDriveToken(localStorage.token, {
                knowledge_id: knowledge.id,
                refresh_token: tokenResponse.refreshToken,
                tenant_id: tokenResponse.tenantId,
                username: tokenResponse.accountUsername
            });
            console.log('OneDrive refresh token stored for scheduled sync');
        } catch (err) {
            console.warn('Failed to store refresh token for scheduled sync:', err);
            // Non-fatal - manual sync still works, just no auto-sync
        }
    } else {
        console.warn('No refresh token received - scheduled sync will not be available');
    }
};
```

#### 2. Add UI indicator for auto-sync status (optional enhancement)
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Show whether auto-sync is enabled

In the sync status display area, add:

```svelte
{#if knowledge.meta?.onedrive_sync?.has_stored_token}
    <span class="text-xs text-green-600">Auto-sync enabled</span>
{:else if knowledge.meta?.onedrive_sync?.needs_reauth}
    <span class="text-xs text-orange-600">Re-authentication required</span>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Linting passes: `npm run lint:frontend`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] Sync OneDrive folder to knowledge base
- [ ] Check browser console for "OneDrive refresh token stored" message
- [ ] Verify in database: OAuthSession record exists with encrypted token
- [ ] Knowledge meta shows `has_stored_token: true`

**Implementation Note**: Pause for manual verification before proceeding.

---

## Phase 5: Update Scheduler to Execute Syncs

### Overview
Modify scheduler to refresh tokens and execute actual syncs.

### Changes Required:

#### 1. Replace scheduler with working implementation
**File**: `backend/open_webui/services/onedrive/scheduler.py`
**Changes**: Full rewrite to use token refresh and execute syncs

```python
"""Background scheduler for OneDrive sync jobs.

Executes scheduled syncs using stored refresh tokens.
Based on Airweave's TokenManager pattern for reliable background sync.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from open_webui.models.knowledge import Knowledges
from open_webui.config import (
    ONEDRIVE_SYNC_INTERVAL_MINUTES,
    ENABLE_ONEDRIVE_SYNC,
)
from open_webui.services.onedrive.token_refresh import (
    get_valid_access_token,
    refresh_access_token,
    TokenRefreshResult,
)
from open_webui.services.onedrive.sync_worker import sync_folder_to_knowledge

log = logging.getLogger(__name__)

_scheduler_task: Optional[asyncio.Task] = None

# Cache access tokens in memory to avoid unnecessary refreshes
# Key: knowledge_id, Value: (access_token, expires_at)
_token_cache: Dict[str, tuple] = {}


async def run_scheduled_syncs():
    """Run scheduled sync checks and execute syncs for due knowledge bases."""
    log.info("OneDrive sync scheduler started")

    while True:
        try:
            await _check_and_execute_due_syncs()
        except Exception as e:
            log.exception(f"Scheduler error: {e}")

        # Wait for next check interval (check every minute, sync based on configured interval)
        await asyncio.sleep(60)


async def _check_and_execute_due_syncs():
    """Check all knowledge bases and execute syncs for those due."""
    all_knowledge = Knowledges.get_knowledge_bases()

    current_time = int(datetime.utcnow().timestamp())
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60

    for kb in all_knowledge:
        try:
            await _process_knowledge_base(kb, current_time, interval_seconds)
        except Exception as e:
            log.exception(f"Error processing knowledge base {kb.id}: {e}")


async def _process_knowledge_base(kb, current_time: int, interval_seconds: int):
    """Process a single knowledge base for scheduled sync."""
    meta = kb.meta or {}
    sync_info = meta.get("onedrive_sync")

    if not sync_info:
        return

    # Skip if no stored token
    if not sync_info.get("has_stored_token"):
        return

    # Skip if needs re-auth
    if sync_info.get("needs_reauth"):
        return

    # Skip if currently syncing
    if sync_info.get("status") == "syncing":
        return

    # Check if enough time has passed since last sync
    last_sync = sync_info.get("last_sync_at", 0)
    time_since_sync = current_time - last_sync

    if time_since_sync < interval_seconds:
        return

    # Due for sync - execute it
    log.info(f"Knowledge base {kb.name} ({kb.id}) is due for sync")
    await _execute_scheduled_sync(kb, sync_info)


async def _execute_scheduled_sync(kb, sync_info: dict):
    """Execute a scheduled sync for a knowledge base."""
    log.info(f"Executing scheduled sync for: {kb.name} ({kb.id})")

    # Get cached or refresh access token
    cached = _token_cache.get(kb.id, (None, None))
    access_token, expires_at = await get_valid_access_token(
        knowledge_id=kb.id,
        user_id=kb.user_id,
        cached_token=cached[0],
        cached_expires_at=cached[1]
    )

    if not access_token:
        log.warning(f"Cannot get access token for {kb.name}. User needs to re-authenticate.")
        return

    # Cache the token
    _token_cache[kb.id] = (access_token, expires_at)

    # Get sync configuration
    drive_id = sync_info.get("drive_id")
    folder_id = sync_info.get("folder_id")

    if not drive_id or not folder_id:
        log.error(f"Missing drive_id or folder_id for {kb.name}")
        return

    try:
        # Update status to syncing
        meta = kb.meta or {}
        meta["onedrive_sync"]["status"] = "syncing"
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)

        # Execute sync (no user_token for scheduled syncs, no Socket.IO events)
        await sync_folder_to_knowledge(
            knowledge_id=kb.id,
            drive_id=drive_id,
            folder_id=folder_id,
            access_token=access_token,
            user_id=kb.user_id,
            user_token=None,  # No user token for scheduled syncs
            is_scheduled=True,
        )

        log.info(f"Scheduled sync completed for {kb.name}")

    except Exception as e:
        log.exception(f"Scheduled sync failed for {kb.name}: {e}")
        meta = kb.meta or {}
        meta["onedrive_sync"]["status"] = "error"
        meta["onedrive_sync"]["error"] = str(e)[:500]  # Truncate error message
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)


def start_scheduler():
    """Start the background scheduler.

    Should be called during app startup if ENABLE_ONEDRIVE_SYNC is True.
    """
    global _scheduler_task

    if not ENABLE_ONEDRIVE_SYNC.value:
        log.info("OneDrive sync is disabled, scheduler not started")
        return

    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(run_scheduled_syncs())
        log.info("OneDrive sync scheduler started")
    else:
        log.debug("OneDrive sync scheduler already running")


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler_task

    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        log.info("OneDrive sync scheduler stopped")
    _scheduler_task = None


def clear_token_cache():
    """Clear the in-memory token cache."""
    global _token_cache
    _token_cache = {}


async def trigger_manual_sync_check():
    """Manually trigger a sync check (for admin dashboard).

    Returns list of knowledge bases with sync status.
    """
    all_knowledge = Knowledges.get_knowledge_bases()

    current_time = int(datetime.utcnow().timestamp())
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60

    result = []

    for kb in all_knowledge:
        meta = kb.meta or {}
        sync_info = meta.get("onedrive_sync")

        if not sync_info:
            continue

        last_sync = sync_info.get("last_sync_at", 0)
        time_since_sync = current_time - last_sync
        is_due = time_since_sync >= interval_seconds

        result.append({
            "id": kb.id,
            "name": kb.name,
            "folder_path": sync_info.get("folder_path", ""),
            "last_sync_at": last_sync,
            "status": sync_info.get("status", "idle"),
            "has_stored_token": sync_info.get("has_stored_token", False),
            "needs_reauth": sync_info.get("needs_reauth", False),
            "is_due_for_sync": is_due,
            "next_sync_in_seconds": max(0, interval_seconds - time_since_sync),
        })

    return result
```

#### 2. Update sync_worker to handle scheduled syncs
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Add `is_scheduled` parameter to handle missing user_token and skip Socket.IO

Find the function signature and update:

```python
async def sync_folder_to_knowledge(
    knowledge_id: str,
    drive_id: str,
    folder_id: str,
    access_token: str,
    user_id: str,
    user_token: Optional[str] = None,
    is_scheduled: bool = False,
):
    """Sync a OneDrive folder to a Knowledge base.

    Args:
        knowledge_id: Target knowledge base ID
        drive_id: OneDrive drive ID
        folder_id: OneDrive folder ID
        access_token: Microsoft Graph API access token
        user_id: Owner user ID
        user_token: Open WebUI JWT (optional for scheduled syncs)
        is_scheduled: If True, skip Socket.IO events
    """
```

Update the `OneDriveSyncWorker.__init__` similarly:

```python
def __init__(
    self,
    knowledge_id: str,
    drive_id: str,
    folder_id: str,
    access_token: str,
    user_id: str,
    user_token: Optional[str] = None,
    event_emitter: Optional[...] = None,
    is_scheduled: bool = False,
):
    # ...existing code...
    self.is_scheduled = is_scheduled

    # Skip event emitter for scheduled syncs
    if is_scheduled:
        self.event_emitter = None
```

#### 3. Start scheduler in main.py
**File**: `backend/open_webui/main.py`
**Changes**: Call start_scheduler() during app startup

Find the lifespan or startup event handler and add:

```python
# In the startup section, after other initialization
if app.state.config.ENABLE_ONEDRIVE_SYNC:
    from open_webui.services.onedrive.scheduler import start_scheduler
    start_scheduler()
    log.info("OneDrive sync scheduler initialized")
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend linting passes: `npm run lint:backend`
- [ ] Backend starts without errors: `open-webui dev`
- [ ] Logs show "OneDrive sync scheduler started"

#### Manual Verification:
- [ ] Set `ONEDRIVE_SYNC_INTERVAL_MINUTES=1` for testing
- [ ] Sync a knowledge base manually (stores refresh token)
- [ ] Wait 1-2 minutes
- [ ] Observe logs: "Knowledge base X is due for sync"
- [ ] Observe logs: "Successfully refreshed token for knowledge base X"
- [ ] Observe logs: "Scheduled sync completed for X"
- [ ] Add a new file to the OneDrive folder
- [ ] Wait for next sync cycle
- [ ] Verify file appears in knowledge base

**Implementation Note**: This is the final phase. Full end-to-end testing required.

---

## Testing Strategy

### Unit Tests
- Test `refresh_access_token()` with mocked HTTP responses
- Test token caching logic in scheduler
- Test rotating token storage (new refresh token replaces old)

### Integration Tests
- Full flow: manual sync -> token stored -> scheduled sync executes
- Token expiration and refresh
- Token revocation handling (`invalid_grant` error)
- Error recovery (network errors, timeouts)

### Manual Testing Steps
1. Configure OneDrive integration with Azure AD app
2. Create knowledge base
3. Sync OneDrive folder manually
4. Verify refresh token stored in database (encrypted)
5. Set `ONEDRIVE_SYNC_INTERVAL_MINUTES=1`
6. Restart backend, observe scheduler start
7. Wait and observe automatic sync in logs
8. Add new file to OneDrive folder
9. Wait for next scheduled sync
10. Verify file appears in knowledge base
11. Revoke token in Azure AD portal
12. Wait for next sync attempt
13. Verify `needs_reauth` status is set

## Security Considerations

1. **Token encryption**: Uses existing Fernet encryption with `OAUTH_SESSION_TOKEN_ENCRYPTION_KEY`
2. **Token scope**: Only requests `Files.Read.All` (read-only access) + `offline_access`
3. **Token rotation**: Microsoft rotates refresh tokens - new token stored after each refresh
4. **Token revocation handling**: `invalid_grant` errors mark knowledge base for re-auth
5. **Token expiry**: Refresh tokens valid for 90 days (rolling), proactive refresh keeps them valid
6. **Audit logging**: All token operations logged
7. **Token isolation**: Each knowledge base has its own token (provider = `onedrive:{kb_id}`)

## UX Considerations

1. **Seamless initial setup**: User authorizes once, token stored automatically
2. **No action required**: Syncs happen in background without user interaction
3. **Clear status**: UI shows "Auto-sync enabled" or "Re-authentication required"
4. **Graceful degradation**: If refresh fails, manual sync still works
5. **Easy re-auth**: Just click sync again to store a new token

## Migration Notes

- No database schema changes required (uses existing `oauth_session` table)
- Existing manual syncs continue to work unchanged
- Users must perform one manual sync after upgrade to store refresh token
- Previously synced knowledge bases need one re-sync to enable auto-sync

## References

- Research document: `thoughts/shared/research/2026-01-18-onedrive-sync-interval-not-working.md`
- Airweave token pattern: `airweave/backend/airweave/platform/sync/token_manager.py`
- Existing OAuth pattern: `backend/open_webui/models/oauth_sessions.py:69-105`
- Microsoft token refresh: https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens
- RFC 9700 OAuth best practices: https://datatracker.ietf.org/doc/rfc9700/
