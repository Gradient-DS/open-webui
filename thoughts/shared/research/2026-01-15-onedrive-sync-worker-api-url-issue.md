---
date: 2026-01-15T22:55:00+01:00
researcher: Claude
git_commit: 4248118b6d93f135979ca7094edf5f44a3f53494
branch: feat/onedrive
repository: open-webui
topic: "OneDrive sync worker 404 error - API URL misconfiguration"
tags: [research, onedrive, sync, api, debugging]
status: complete
last_updated: 2026-01-15
last_updated_by: Claude
---

# Research: OneDrive Sync Worker 404 Error

**Date**: 2026-01-15T22:55:00+01:00
**Researcher**: Claude
**Git Commit**: 4248118b6d93f135979ca7094edf5f44a3f53494
**Branch**: feat/onedrive
**Repository**: open-webui

## Research Question

The OneDrive file picker shows success, but files fail to process with 404 errors. The backend logs show:
```
HTTP Request: POST http://localhost:5173/api/v1/retrieval/process/file "HTTP/1.1 404 Not Found"
```

## Summary

**Root Cause**: The `WEBUI_URL` configuration is set to `http://localhost:5173` (Vite dev server) instead of `http://localhost:8080` (FastAPI backend). The OneDrive sync worker uses this URL for internal API calls, but the Vite dev server doesn't proxy POST requests to the backend, resulting in a 404 HTML page response.

**Solution**: Change `WEBUI_URL` in Admin Settings > General to `http://localhost:8080`, or leave it empty to use the default.

## Detailed Findings

### 1. The Sync Worker API Call

Location: `backend/open_webui/services/onedrive/sync_worker.py:468-497`

```python
async def _process_file_via_api(self, file_id: str):
    """Process file by calling the internal retrieval API."""
    import httpx
    from open_webui.config import WEBUI_URL

    base_url = WEBUI_URL.value if WEBUI_URL.value else "http://localhost:8080"

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            f"{base_url}/api/v1/retrieval/process/file",
            ...
        )
```

The sync worker:
1. Imports `WEBUI_URL` from config (line 473)
2. Uses `WEBUI_URL.value` if set, otherwise defaults to `http://localhost:8080` (line 475)
3. POSTs to `/api/v1/retrieval/process/file` endpoint (line 479)

### 2. WEBUI_URL Configuration

Location: `backend/open_webui/config.py:1138`

```python
WEBUI_URL = PersistentConfig("WEBUI_URL", "webui.url", os.environ.get("WEBUI_URL", ""))
```

`PersistentConfig` is a special configuration class that:
- First checks the database for a stored value
- Falls back to environment variable if not in database
- Defaults to empty string

The value can be set via:
- Admin Settings UI: `src/lib/components/admin/Settings/General.svelte:749`
- Environment variable: `WEBUI_URL`
- Database: `webui.url` config key

### 3. The Retrieval Endpoint

Location: `backend/open_webui/routers/retrieval.py:1565-1570`

```python
@router.post("/process/file")
def process_file(
    request: Request,
    form_data: ProcessFileForm,
    user=Depends(get_verified_user),
):
```

The endpoint exists and is mounted at `/api/v1/retrieval/process/file`. It requires:
- Authentication via JWT token in `Authorization` header
- `file_id` in request body
- Optional `collection_name` for knowledge base processing

### 4. Development Setup Architecture

| Service | Port | Purpose |
|---------|------|---------|
| Vite Dev Server | 5173 | SvelteKit frontend with hot-reload |
| FastAPI Backend | 8080 | Python API server |

The frontend at `src/lib/constants.ts:6-7` correctly targets port 8080 in dev mode:
```typescript
export const WEBUI_HOSTNAME = browser ? (dev ? `${location.hostname}:8080` : ``) : '';
export const WEBUI_BASE_URL = browser ? (dev ? `http://${WEBUI_HOSTNAME}` : ``) : ``;
```

### 5. Why the 404 Occurs

1. User sets `WEBUI_URL = http://localhost:5173` in admin settings (stored in database)
2. Sync worker reads this value: `WEBUI_URL.value` returns `http://localhost:5173`
3. Sync worker POSTs to `http://localhost:5173/api/v1/retrieval/process/file`
4. Vite dev server doesn't have this route (no API proxy configured in `vite.config.ts`)
5. SvelteKit returns its fallback HTML page with 404 status
6. Sync worker receives HTML instead of JSON, fails to process

### 6. Console Errors (SharePoint File Picker)

The console errors from `spserviceworker.js` and `spofilepickerwebpack.js` are **internal Microsoft SharePoint errors** and are not related to the 404 issue. These are common warnings/errors from the Microsoft Office File Picker SDK that don't affect functionality:

- "Knockout is now deprecated" - Internal MS dependency warning
- "Event handler of 'message' event must be added" - Service worker timing warning
- "Refused to get unsafe header" - CORS headers on SharePoint API responses

These are expected and can be safely ignored.

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:468-497` - API call implementation
- `backend/open_webui/config.py:1138` - WEBUI_URL configuration
- `backend/open_webui/routers/retrieval.py:1565` - Process file endpoint
- `src/lib/constants.ts:6-7` - Frontend API URL configuration
- `src/lib/components/admin/Settings/General.svelte:749` - Admin UI binding

## Architecture Insights

The `WEBUI_URL` config is designed for **external-facing URLs** (OAuth redirects, share links, etc.), not internal backend API calls. The sync worker incorrectly uses it for internal communication.

**Better approaches would be:**
1. Use `request.base_url` when available (like OAuth handlers do)
2. Use a separate `INTERNAL_API_URL` config for backend-to-backend calls
3. Call the endpoint functions directly instead of via HTTP (same Python process)

## Recommended Fix

**Immediate (config change):**
1. Go to Admin Settings > General
2. Set `WEBUI_URL` to `http://localhost:8080` (or leave empty)
3. Save and retry OneDrive sync

**Long-term (code change):**
Consider modifying the sync worker to either:
- Call the processing functions directly (avoiding HTTP)
- Use a separate internal API URL configuration
- Always use port 8080 for internal calls in development

## Open Questions

1. Should the sync worker use HTTP for internal processing, or call functions directly?
2. Should there be a separate `INTERNAL_API_URL` config for backend-to-backend calls?
3. Can the file processing be moved earlier in the sync pipeline to avoid needing the API call?
