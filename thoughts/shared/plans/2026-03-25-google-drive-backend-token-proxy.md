# Google Drive Backend Token Proxy Implementation Plan

## Overview

Replace the Google Drive frontend GIS (Google Identity Services) implicit token flow with a backend-proxied OAuth authorization code flow. This ensures Google Drive tokens are long-lived and auto-refresh server-side, matching OneDrive's behavior where users authorize once and it works until the app restarts (or the refresh token is revoked).

## Current State Analysis

**The problem:** Google Drive uses GIS `initTokenClient()` on the frontend to get short-lived access tokens (~1 hour). These are stored in a JavaScript module variable, lost on page refresh, and have **no silent refresh mechanism**. Users must re-consent every time the token expires.

**OneDrive comparison:** OneDrive uses MSAL's `acquireTokenSilent()` which automatically manages refresh tokens in the browser cache, giving seamless token renewal without user interaction.

**Existing infrastructure:** The backend already has a complete OAuth code flow for Google Drive (used for background sync scheduling):

- `GET /api/v1/google-drive/auth/initiate` — redirects to Google consent screen
- OAuth callback stores tokens (with refresh token) encrypted in `oauth_session` table
- `services/google_drive/token_refresh.py` — auto-refreshes expired tokens using refresh grants
- Background scheduler uses this infrastructure successfully

The fix: make the frontend use the backend's stored token instead of getting its own via GIS.

### Key Discoveries:

- Backend token storage is per-user per-provider (`oauth_sessions.py:36`), not per-KB
- `get_valid_access_token` in `sync/token_refresh.py:18` takes `knowledge_id` but only uses it for logging — the actual token lookup is by `(provider, user_id)`
- `auth/initiate` currently requires `knowledge_id` (`google_drive_sync.py:181`) — needs to be optional for the chat picker flow (no KB context)
- Manual sync (`_sync_items_background` at `google_drive_sync.py:104`) creates a worker without `token_provider`, meaning manual syncs have no mid-sync token refresh — this should be fixed
- Google Picker API still needs to be loaded via script tag for the picker UI — only the **auth** changes, not the picker

## Desired End State

- Users authorize Google Drive **once** via an OAuth popup (same as the existing background sync auth)
- The backend stores refresh tokens and auto-refreshes access tokens transparently
- Frontend obtains valid access tokens from the backend via a new API endpoint
- Both the **chat file picker** and **knowledge base sync** flows use backend-managed tokens
- The GIS `initTokenClient` code is removed entirely
- Manual syncs pass the token through the backend (no frontend token in request body)

### Verification:

1. User authorizes Google Drive → token stored in backend
2. Open Google Drive picker in chat → works without re-auth
3. Wait >1 hour → picker still works (backend auto-refreshed the token)
4. Refresh the page → picker still works (token is server-side, not in JS variable)
5. Start a knowledge base sync → works without requiring `access_token` in request body
6. Background scheduled sync → continues working as before

## What We're NOT Doing

- Changing the OneDrive flow (it already works well with MSAL)
- Changing how the Google Picker UI itself works (still uses `google.picker.PickerBuilder`)
- Adding new Google OAuth scopes (keeping `drive.readonly`)
- Changing the chat picker to download files server-side (still downloads in browser)
- Fixing the `scope` mismatch (frontend currently requests `drive.file` too — this is unnecessary with `drive.readonly` and will be cleaned up)

## Implementation Approach

The backend already does 90% of what we need. The main work is:

1. Expose a simple endpoint to get a valid (auto-refreshed) access token
2. Make `auth/initiate` work without a `knowledge_id` (for the chat flow)
3. Rewrite the frontend token management to use the backend instead of GIS
4. Make the sync endpoint use the backend's stored token when no frontend token is provided

---

## Phase 1: Backend — Access Token Endpoint + Optional Knowledge ID

### Overview

Add a `GET /auth/access-token` endpoint that returns a valid access token (refreshing if expired), and make `auth/initiate` work without a `knowledge_id`.

### Changes Required:

#### 1. New access-token endpoint

**File**: `backend/open_webui/routers/google_drive_sync.py`
**Changes**: Add endpoint after the existing auth endpoints (~line 279)

```python
@router.get("/auth/access-token")
async def get_access_token(
    user: UserModel = Depends(get_verified_user),
):
    """Get a valid Google Drive access token, refreshing if needed.

    Used by the frontend for the Google Drive picker and file downloads.
    Returns 401 if no stored token exists (user must authorize first).
    """
    from open_webui.services.google_drive.token_refresh import get_valid_access_token

    if not GOOGLE_CLIENT_SECRET.value:
        raise HTTPException(400, "Google client secret not configured")

    token = await get_valid_access_token(user.id, knowledge_id="__picker__")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="No stored Google Drive token. Authorization required.",
        )
    return {"access_token": token}
```

#### 2. Make knowledge_id optional in auth/initiate

**File**: `backend/open_webui/routers/google_drive_sync.py`
**Changes**: Modify `initiate_auth` (~line 180)

```python
from typing import Optional

@router.get("/auth/initiate")
async def initiate_auth(
    request: Request,
    user: UserModel = Depends(get_verified_user),
    knowledge_id: Optional[str] = None,
):
    """Initiate OAuth auth code flow for Google Drive.

    knowledge_id is optional — if provided, validates KB ownership.
    Used for both knowledge base background sync auth and general picker auth.
    """
    from open_webui.services.google_drive.auth import get_authorization_url

    if not GOOGLE_CLIENT_SECRET.value:
        raise HTTPException(400, "Google client secret not configured")

    if knowledge_id:
        get_knowledge_or_raise(knowledge_id, user)

    redirect_uri = str(request.base_url).rstrip("/") + "/oauth/google/callback"
    log.info(
        "OAuth initiate: base_url=%s, redirect_uri=%s", request.base_url, redirect_uri
    )

    auth_url = get_authorization_url(
        user_id=user.id,
        knowledge_id=knowledge_id or "__general__",
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(auth_url)
```

#### 3. Make access_token optional in SyncItemsRequest

**File**: `backend/open_webui/routers/google_drive_sync.py`
**Changes**: Modify `SyncItemsRequest` and `sync_items`

```python
class SyncItemsRequest(BaseModel):
    """Request to sync multiple Google Drive items to a Knowledge base."""

    knowledge_id: str
    items: List[SyncItem]
    access_token: Optional[str] = None  # Optional — backend uses stored token if omitted


@router.post("/sync/items")
async def sync_items(
    request: SyncItemsRequest,
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_verified_user),
):
    """Start Google Drive sync for multiple items (files and folders)."""
    # If no access_token provided, get one from the stored session
    access_token = request.access_token
    if not access_token:
        from open_webui.services.google_drive.token_refresh import (
            get_valid_access_token,
        )
        access_token = await get_valid_access_token(user.id, request.knowledge_id)
        if not access_token:
            raise HTTPException(
                401, "No valid Google Drive token. Please re-authorize."
            )

    new_sources = [
        {
            "type": item.type,
            "item_id": item.item_id,
            "item_path": item.item_path,
            "name": item.name,
        }
        for item in request.items
    ]

    result = handle_sync_items_request(
        knowledge_id=request.knowledge_id,
        meta_key=_META_KEY,
        new_sources=new_sources,
        access_token=access_token,
        user=user,
        clear_delta_keys=_CLEAR_DELTA_KEYS,
    )

    background_tasks.add_task(
        _sync_items_background,
        knowledge_id=request.knowledge_id,
        sources=result["all_sources"],
        access_token=access_token,
        user_id=user.id,
        app=fastapi_request.app,
    )

    return {"message": "Sync started", "knowledge_id": request.knowledge_id}
```

#### 4. Add access-token function to the frontend API client

**File**: `src/lib/apis/sync/index.ts`
**Changes**: Add `getAccessToken` to the `createSyncApi` factory

```typescript
getAccessToken(token: string): Promise<{ access_token: string }> {
    return apiFetch(`${base}/auth/access-token`, {
        headers: { Authorization: `Bearer ${token}` }
    });
},
```

#### 5. Re-export from googledrive API

**File**: `src/lib/apis/googledrive/index.ts`
**Changes**: Add export

```typescript
export const getAccessToken = api.getAccessToken;
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts without errors: `open-webui dev`
- [x] `npm run build` succeeds
- [ ] `curl -H "Authorization: Bearer <jwt>" localhost:8080/api/v1/google-drive/auth/access-token` returns 401 (no token) or 200 with access_token

#### Manual Verification:

- [ ] `GET /auth/initiate` works without `knowledge_id` parameter
- [ ] `GET /auth/access-token` returns a valid token after authorization
- [ ] `POST /sync/items` works without `access_token` in body (uses stored token)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Frontend — Replace GIS with Backend Token Management

### Overview

Rewrite `google-drive-picker.ts` to get tokens from the backend instead of GIS. Add an `ensureAuthorized` flow that checks for a stored token and triggers the OAuth popup if needed.

### Changes Required:

#### 1. Rewrite google-drive-picker.ts

**File**: `src/lib/utils/google-drive-picker.ts`
**Changes**: Replace GIS auth with backend-proxied auth. Keep Picker API loading.

```typescript
import { WEBUI_BASE_URL, WEBUI_API_BASE_URL } from '$lib/constants';

// Google Drive Picker API configuration
let API_KEY = '';
let CLIENT_ID = '';

// Function to fetch credentials from backend config (still needed for Picker API key)
async function getCredentials() {
	const response = await fetch(`${WEBUI_BASE_URL}/api/config`, {
		headers: { 'Content-Type': 'application/json' },
		credentials: 'include'
	});
	if (!response.ok) throw new Error('Failed to fetch Google Drive credentials');
	const config = await response.json();
	API_KEY = config.google_drive?.api_key;
	CLIENT_ID = config.google_drive?.client_id;
	if (!API_KEY || !CLIENT_ID) throw new Error('Google Drive API credentials not configured');
}

const validateCredentials = () => {
	if (!API_KEY || !CLIENT_ID || API_KEY === '' || CLIENT_ID === '') {
		throw new Error('Google Drive API credentials not configured');
	}
};

let pickerApiLoaded = false;
let initialized = false;

// ── Picker API loading (unchanged) ──────────────────────────────────

export const loadGoogleDriveApi = () => {
	return new Promise((resolve, reject) => {
		if (typeof gapi === 'undefined') {
			const script = document.createElement('script');
			script.src = 'https://apis.google.com/js/api.js';
			script.onload = () => {
				gapi.load('picker', () => {
					pickerApiLoaded = true;
					resolve(true);
				});
			};
			script.onerror = reject;
			document.body.appendChild(script);
		} else {
			gapi.load('picker', () => {
				pickerApiLoaded = true;
				resolve(true);
			});
		}
	});
};

export const initialize = async () => {
	if (!initialized) {
		await getCredentials();
		validateCredentials();
		await loadGoogleDriveApi();
		initialized = true;
	}
};

// ── Backend-proxied token management ────────────────────────────────

/**
 * Get a valid access token from the backend (auto-refreshed).
 * Returns null if no stored token exists (user must authorize).
 */
async function fetchBackendAccessToken(): Promise<string | null> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/google-drive/auth/access-token`, {
		headers: { Authorization: `Bearer ${localStorage.token}` }
	});
	if (res.status === 401) return null;
	if (!res.ok) throw new Error('Failed to get Google Drive access token');
	const data = await res.json();
	return data.access_token;
}

/**
 * Open the backend OAuth popup and wait for it to complete.
 * Reuses the same popup/postMessage pattern as KnowledgeBase.svelte's authorizeBackgroundSync.
 */
function triggerAuthPopup(knowledgeId?: string): Promise<void> {
	const url = knowledgeId
		? `${WEBUI_API_BASE_URL}/google-drive/auth/initiate?knowledge_id=${knowledgeId}`
		: `${WEBUI_API_BASE_URL}/google-drive/auth/initiate`;

	return new Promise<void>((resolve, reject) => {
		const popup = window.open(url, 'google_drive_auth', 'width=600,height=700,scrollbars=yes');
		let messageReceived = false;

		const handleMessage = (event: MessageEvent) => {
			if (event.data?.type !== 'google_drive_auth_callback') return;
			messageReceived = true;
			window.removeEventListener('message', handleMessage);
			if (event.data.success) {
				resolve();
			} else {
				reject(new Error(event.data.error || 'Google Drive authorization failed'));
			}
		};

		window.addEventListener('message', handleMessage);

		// Fallback: check when popup closes without postMessage
		const check = setInterval(() => {
			if (popup?.closed) {
				clearInterval(check);
				if (!messageReceived) {
					window.removeEventListener('message', handleMessage);
					resolve(); // Will verify token after
				}
			}
		}, 500);
	});
}

/**
 * Ensure the user has authorized Google Drive.
 * If no stored token, opens an OAuth popup for consent.
 * Returns a valid access token.
 */
export const getAuthToken = async (knowledgeId?: string): Promise<string> => {
	let token = await fetchBackendAccessToken();
	if (token) return token;

	// No stored token — trigger OAuth popup
	await triggerAuthPopup(knowledgeId);

	// After popup closes, fetch the now-stored token
	token = await fetchBackendAccessToken();
	if (!token) throw new Error('Google Drive authorization failed or was cancelled');
	return token;
};

export const clearGoogleDriveToken = () => {
	// No-op: tokens are now managed server-side.
	// Kept for backward compatibility with existing callers.
};

// ── Picker functions (auth replaced, picker UI unchanged) ───────────

export interface KnowledgePickerItem {
	type: 'file' | 'folder';
	id: string;
	name: string;
	path: string;
	mimeType: string;
}

export interface KnowledgePickerResult {
	items: KnowledgePickerItem[];
	accessToken: string;
}

export const createKnowledgePicker = (
	knowledgeId?: string
): Promise<KnowledgePickerResult | null> => {
	return new Promise(async (resolve, reject) => {
		try {
			await initialize();
			const token = await getAuthToken(knowledgeId);

			const SUPPORTED_MIME_TYPES = [
				'application/pdf',
				'text/plain',
				'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
				'application/vnd.google-apps.document',
				'application/vnd.google-apps.spreadsheet',
				'application/vnd.google-apps.presentation',
				'application/vnd.google-apps.folder'
			].join(',');

			const filesView = new google.picker.DocsView()
				.setIncludeFolders(true)
				.setSelectFolderEnabled(false)
				.setMimeTypes(SUPPORTED_MIME_TYPES);

			const folderView = new google.picker.DocsView()
				.setIncludeFolders(true)
				.setSelectFolderEnabled(true)
				.setMimeTypes('application/vnd.google-apps.folder');

			const picker = new google.picker.PickerBuilder()
				.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
				.addView(filesView)
				.addView(folderView)
				.setOAuthToken(token)
				.setDeveloperKey(API_KEY)
				.setCallback((data: any) => {
					if (data[google.picker.Response.ACTION] === google.picker.Action.PICKED) {
						const docs = data[google.picker.Response.DOCUMENTS];
						const items: KnowledgePickerItem[] = docs.map((doc: any) => {
							const mimeType = doc[google.picker.Document.MIME_TYPE];
							return {
								type: mimeType === 'application/vnd.google-apps.folder' ? 'folder' : 'file',
								id: doc[google.picker.Document.ID],
								name: doc[google.picker.Document.NAME],
								path: doc[google.picker.Document.URL] || '',
								mimeType
							};
						});
						resolve({ items, accessToken: token });
					} else if (data[google.picker.Response.ACTION] === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			console.error('Google Drive Knowledge Picker error:', error);
			reject(error);
		}
	});
};

export const createPicker = () => {
	return new Promise(async (resolve, reject) => {
		try {
			await initialize();
			const token = await getAuthToken();

			const picker = new google.picker.PickerBuilder()
				.enableFeature(google.picker.Feature.NAV_HIDDEN)
				.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
				.addView(
					new google.picker.DocsView()
						.setIncludeFolders(false)
						.setSelectFolderEnabled(false)
						.setMimeTypes(
							'application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.google-apps.document,application/vnd.google-apps.spreadsheet,application/vnd.google-apps.presentation'
						)
				)
				.setOAuthToken(token)
				.setDeveloperKey(API_KEY)
				.setCallback(async (data: any) => {
					if (data[google.picker.Response.ACTION] === google.picker.Action.PICKED) {
						try {
							const doc = data[google.picker.Response.DOCUMENTS][0];
							const fileId = doc[google.picker.Document.ID];
							const fileName = doc[google.picker.Document.NAME];
							const mimeType = doc[google.picker.Document.MIME_TYPE];

							if (!fileId || !fileName) throw new Error('Required file details missing');

							let downloadUrl;
							if (mimeType.includes('google-apps')) {
								let exportFormat;
								if (mimeType.includes('document')) exportFormat = 'text/plain';
								else if (mimeType.includes('spreadsheet')) exportFormat = 'text/csv';
								else if (mimeType.includes('presentation')) exportFormat = 'text/plain';
								else exportFormat = 'application/pdf';
								downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=${encodeURIComponent(exportFormat)}`;
							} else {
								downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`;
							}

							const response = await fetch(downloadUrl, {
								headers: { Authorization: `Bearer ${token}`, Accept: '*/*' }
							});

							if (!response.ok) {
								const errorText = await response.text();
								throw new Error(`Failed to download file (${response.status}): ${errorText}`);
							}

							const blob = await response.blob();
							resolve({
								id: fileId,
								name: fileName,
								url: downloadUrl,
								blob: blob,
								headers: { Authorization: `Bearer ${token}`, Accept: '*/*' }
							});
						} catch (error) {
							reject(error);
						}
					} else if (data[google.picker.Response.ACTION] === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			console.error('Google Drive Picker error:', error);
			reject(error);
		}
	});
};
```

Key changes:

- Removed: `loadGoogleAuthApi()` (GIS script loading), `oauthToken` module variable, GIS `initTokenClient` usage
- Added: `fetchBackendAccessToken()`, `triggerAuthPopup()`, `ensureAuthorized()` flow
- `getAuthToken()` now accepts optional `knowledgeId` parameter
- `clearGoogleDriveToken()` becomes a no-op (kept for backward compat)
- `createKnowledgePicker()` accepts optional `knowledgeId` to pass to auth
- `initialize()` no longer loads GIS auth script (only Picker API)

#### 2. Update KnowledgeBase.svelte — Google Drive sync flows

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

**Change imports** (~line 46-50): Remove unused imports

```typescript
// Remove: clearGoogleDriveToken, getAuthToken as getGoogleDriveAuthToken, initialize as initializeGoogleDrive
// Keep:
import {
	startGoogleDriveSyncItems,
	type SyncItem as GoogleDriveSyncItem
} from '$lib/apis/googledrive';
import { createKnowledgePicker } from '$lib/utils/google-drive-picker';
```

**Change `cloudSyncHandler` Google Drive branch** (~line 656-678):

```typescript
} else if (provider.type === 'google_drive') {
    const result = await createKnowledgePicker(knowledge.id);
    if (!result) {
        state.isSyncing = false;
        cloudSyncState = cloudSyncState;
        return;
    }

    syncItems = result.items.map(item => ({
        type: item.type,
        item_id: item.id,
        item_path: item.path,
        name: item.name
    }));

    state.refreshDone = false;
    cloudSyncState = cloudSyncState;
    await startGoogleDriveSyncItems(localStorage.token, {
        knowledge_id: knowledge.id,
        items: syncItems as GoogleDriveSyncItem[],
        // access_token omitted — backend uses its stored token
    });
}
```

**Change `cloudResyncHandler` Google Drive branch** (~line 724-742):

```typescript
} else if (provider.type === 'google_drive') {
    const syncItems: GoogleDriveSyncItem[] = sources.map((source: any) => ({
        type: source.type,
        item_id: source.item_id,
        item_path: source.item_path,
        name: source.name
    }));

    state.refreshDone = false;
    cloudSyncState = cloudSyncState;
    await startGoogleDriveSyncItems(localStorage.token, {
        knowledge_id: knowledge.id,
        items: syncItems,
        // access_token omitted — backend uses its stored token
    });
}
```

The resync no longer needs `clearGoogleDriveToken()`, `initializeGoogleDrive()`, or `getGoogleDriveAuthToken()` — the backend handles all token management.

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] No TypeScript errors related to changed imports

#### Manual Verification:

- [ ] **First-time auth**: Click Google Drive in chat → OAuth popup appears → consent → picker opens → file downloads correctly
- [ ] **Subsequent use**: Click Google Drive again → picker opens immediately (no popup)
- [ ] **Page refresh**: Refresh page → click Google Drive → picker opens immediately (token persists server-side)
- [ ] **Token expiry**: Wait >1 hour (or manually expire the access_token in DB) → picker still works (backend auto-refreshes)
- [ ] **Knowledge sync**: Create Google Drive KB → pick files → sync starts and completes
- [ ] **Knowledge resync**: Click resync on existing KB → sync starts without re-auth
- [ ] **Background sync**: Verify scheduled background syncs still work (if `ENABLE_GOOGLE_DRIVE_SYNC=true`)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Cleanup

### Overview

Remove unused GIS-related code and update the frontend `SyncItemsRequest` type to make `access_token` optional.

### Changes Required:

#### 1. Update frontend SyncItemsRequest type

**File**: `src/lib/apis/googledrive/index.ts`
**Changes**: Make `access_token` optional

```typescript
export interface SyncItemsRequest {
	knowledge_id: string;
	items: SyncItem[];
	access_token?: string; // Optional — backend uses stored token if omitted
}
```

#### 2. Remove GIS script reference if it exists elsewhere

**File**: `src/app.html` or equivalent
**Changes**: Check for and remove any `<script src="https://accounts.google.com/gsi/client">` if present. (The old `loadGoogleAuthApi()` loaded it dynamically, so there shouldn't be a static reference, but verify.)

#### 3. Remove unused `google.accounts.oauth2` TypeScript types

If there are any type declarations for GIS in the project (e.g., in `src/app.d.ts` or a `types/` file), remove the `google.accounts.oauth2` types. Keep the `google.picker` types as those are still used.

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] No references to `google.accounts.oauth2` or `initTokenClient` in codebase

#### Manual Verification:

- [ ] All Phase 2 manual tests still pass

---

## Testing Strategy

### Manual Testing Steps:

1. **Clean slate**: Clear any existing Google Drive OAuth session from DB → verify picker prompts for auth
2. **Auth flow**: Complete auth popup → verify token appears in `oauth_session` table
3. **Picker with stored token**: Open picker → verify no popup, picker loads immediately
4. **Token refresh**: Manually set `expires_at` to past in DB → open picker → verify new `expires_at` (token was refreshed)
5. **Knowledge sync without frontend token**: Start sync → verify backend logs show it fetched its own token
6. **Background sync**: Enable `ENABLE_GOOGLE_DRIVE_SYNC=true` → verify scheduled syncs work
7. **Revoke token**: Call `POST /auth/revoke/{knowledge_id}` → verify next picker use triggers re-auth

### Edge Cases:

- User denies consent in popup → error should be shown, picker should not open
- Popup blocked by browser → should fail gracefully with error message
- Backend has no `GOOGLE_CLIENT_SECRET` configured → 400 error on access-token endpoint
- Refresh token revoked by Google (e.g., user removed app access in Google Account settings) → `needs_reauth` should be set, user prompted to re-authorize

## Performance Considerations

- The `GET /auth/access-token` endpoint adds one extra HTTP call before each picker use. This is a fast call (just a DB lookup + optional token refresh) and should add <100ms latency.
- The token refresh HTTP call to Google's token endpoint (~200ms) only happens when the token is within 5 minutes of expiry, not on every request.

## References

- Google Drive token refresh service: `backend/open_webui/services/google_drive/token_refresh.py`
- Google Drive auth (code flow): `backend/open_webui/services/google_drive/auth.py`
- Generic token refresh: `backend/open_webui/services/sync/token_refresh.py`
- OneDrive MSAL implementation (reference): `src/lib/utils/onedrive-file-picker.ts`
- Google Drive picker (current): `src/lib/utils/google-drive-picker.ts`
- Sync router: `backend/open_webui/routers/google_drive_sync.py`
- Shared sync router: `backend/open_webui/services/sync/router.py`
- KnowledgeBase component: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
