---
date: 2026-02-04T22:15:00+01:00
researcher: claude
git_commit: 0977c3485bcfea9af544093ec695798fad13c57d
branch: feat/simple-kb
repository: open-webui
topic: "Replacing WEBUI_URL in OneDrive Sync Worker with Direct Function Calls"
tags: [research, codebase, onedrive, sync-worker, webui-url, process-file, retrieval, architecture]
status: complete
last_updated: 2026-02-04
last_updated_by: claude
---

# Research: Replacing WEBUI_URL in OneDrive Sync Worker

**Date**: 2026-02-04T22:15:00+01:00
**Researcher**: claude
**Git Commit**: 0977c3485bcfea9af544093ec695798fad13c57d
**Branch**: feat/simple-kb
**Repository**: open-webui

## Research Question

The sync worker uses `WEBUI_URL` for internal HTTP self-calls to `POST /api/v1/retrieval/process/file`. This is fragile (wrong port in dev, unnecessary external round-trip in production). What's the best replacement approach: (1) use an internal URL like `WEBUI_INNER_URL`, or (2) call the processing functions directly as Python calls?

## Summary

**Recommendation: Option 2 — Direct function calls**, passing the `app` reference from the router to the sync worker at construction time.

This is the cleanest approach because:
- The sync worker already runs in the same Python process as the FastAPI app
- `knowledge.py` already calls `process_file` directly as a Python function (proven pattern)
- `main.py:690-709` already demonstrates constructing a mock `Request` from the `app` object
- Eliminates all HTTP overhead, auth token management, timeout handling, and URL fragility
- `WEBUI_INNER_URL` doesn't exist in the codebase and would be a new concept just for this one use case

The refactoring effort is moderate: pass `request.app` through from the router, construct mock `Request` objects, and replace 3 `httpx` call sites with direct `process_file()` calls.

---

## Option 1: Internal URL (WEBUI_INNER_URL or similar)

### What exists today

`WEBUI_INNER_URL` does **not** exist anywhere in the codebase. There is only `WEBUI_URL`, which is intended as the **public-facing URL** — the admin UI (`General.svelte:753-757`) describes it as:

> "Enter the public URL of your WebUI. This URL will be used to generate links in the notifications."

The server port (`8080` default) is only configured at startup time via `PORT` env var in `start.sh:23` or CLI args in `__init__.py:34-37`. It's not available as a config value to import.

### What it would look like

```python
# New config in env.py or config.py
WEBUI_INNER_URL = os.environ.get("WEBUI_INNER_URL", "http://localhost:8080")

# In sync_worker.py (3 call sites)
from open_webui.config import WEBUI_INNER_URL
base_url = WEBUI_INNER_URL
```

### Pros

- Minimal code change (3 lines changed + 1 config line added)
- No risk of circular imports
- No change to the processing logic
- Easy to understand

### Cons

- **Still makes HTTP round-trips** — network overhead, serialization/deserialization, timeout handling
- **Still needs JWT auth token** — the sync worker must carry `self.user_token` for the `Authorization` header
- **Still fragile** — if the backend port changes or binds to a non-localhost address, the internal URL breaks
- **Adds a new config concept** that only one component uses
- **Error handling remains complex** — parsing HTTP status codes, handling timeouts, connection refused
- In production with multiple uvicorn workers, the HTTP call might hit a different worker (though default is 1 worker)

---

## Option 2: Direct Python Function Calls (Recommended)

### Existing precedent: `knowledge.py` already does this

`knowledge.py:17-22` imports `process_file` directly:

```python
from open_webui.routers.retrieval import (
    process_file,
    ProcessFileForm,
    process_files_batch,
    BatchProcessFilesForm,
)
```

And calls it at `knowledge.py:492-496`:

```python
process_file(
    request,
    ProcessFileForm(file_id=form_data.file_id, collection_name=id),
    user=user,
)
```

The `request` parameter comes from FastAPI's route handler injection.

### Existing precedent: mock Request in `main.py`

`main.py:690-709` already constructs a synthetic `Request` from the `app` object:

```python
Request(
    {
        "type": "http",
        "asgi.version": "3.0",
        "asgi.spec_version": "2.0",
        "method": "GET",
        "path": "/internal",
        "query_string": b"",
        "headers": Headers({}).raw,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
        "scheme": "http",
        "app": app,  # <-- This is the key: makes request.app.state work
    }
)
```

### What `process_file` actually needs from `request`

Both `process_file` and `save_docs_to_vector_db` access **only** two things from `request`:

| Access | Purpose |
|--------|---------|
| `request.app.state.config.*` | ~47 config attributes (Loader params, splitter params, embedding params) |
| `request.app.state.ef` | SentenceTransformer embedding function instance |

They do **not** access `request.url`, `request.headers`, `request.client`, `request.method`, or any other HTTP-specific properties.

### Proposed implementation

**Step 1**: Pass `request.app` from the router to the sync worker at construction time.

The sync is started from `onedrive_sync.py:114` (`sync_items_to_knowledge()`), which is a background task kicked off from a FastAPI route handler that already has `request: Request`. Pass `request.app` through:

```python
# onedrive_sync.py - sync start endpoint already has request: Request
worker = OneDriveSyncWorker(
    ...,
    app=request.app,  # NEW: pass the app reference
)
```

**Step 2**: In the sync worker, construct a mock `Request` and call `process_file` directly.

```python
# sync_worker.py
from starlette.requests import Request
from starlette.datastructures import Headers
from open_webui.routers.retrieval import process_file, ProcessFileForm

class OneDriveSyncWorker:
    def __init__(self, ..., app):
        self.app = app

    def _make_request(self) -> Request:
        """Construct a minimal Request for calling retrieval functions."""
        return Request({
            "type": "http",
            "method": "POST",
            "path": "/internal/sync",
            "query_string": b"",
            "headers": Headers({}).raw,
            "app": self.app,
        })

    def _call_process_file(self, file_id: str, collection_name: str = None):
        form = ProcessFileForm(file_id=file_id, collection_name=collection_name)
        user = UserModel(id=self.user_id, role="admin", ...)  # or fetch from DB
        return process_file(self._make_request(), form, user=user)
```

**Step 3**: Replace the 3 HTTP call sites with direct calls.

| Call site | Lines | Current | Replacement |
|-----------|-------|---------|-------------|
| Vector propagation to other KBs | 990-1015 | `httpx.post(...)` | `process_file(request, ProcessFileForm(file_id=file_id, collection_name=kf.knowledge_id), user=user)` |
| `_ensure_vectors_in_kb()` | 1046-1099 | `httpx.post(...)` | `process_file(request, ProcessFileForm(file_id=file_id, collection_name=self.knowledge_id), user=user)` |
| `_process_file_via_api()` Step 1+2 | 1101-1235 | Two `httpx.post(...)` calls | Two `process_file(...)` calls |

### User object handling

`process_file` uses the `user` parameter for file access control (line 1577-1580):
- Admin role: fetches file by ID only
- Non-admin: fetches file by ID + user_id

The sync worker already has `self.user_id` (line 101). Two options:
1. Fetch the user from DB: `Users.get_user_by_id(self.user_id)` (cleanest)
2. Construct a minimal user object with admin role (matches the current implicit behavior since the sync worker's JWT was created by an authenticated user)

### Pros

- **Eliminates all HTTP overhead** — no network, no serialization, no connection management
- **No URL configuration needed** — no `WEBUI_URL`, no `WEBUI_INNER_URL`, no port matching
- **No JWT token needed** — user is passed directly, no auth header parsing
- **Simpler error handling** — Python exceptions instead of HTTP status code parsing
- **More reliable** — no 404s, connection refused, or timeout errors from self-calls
- **Follows existing pattern** — `knowledge.py` already does exactly this
- **Testable** — can unit test with a mock app/config without running a server

### Cons

- **Larger refactoring** — changing 3 call sites + constructor + user handling (~100 lines changed)
- **Mock Request pattern is unusual** — but already precedented in `main.py:690-709`
- **Potential circular import risk** — mitigated by importing `process_file` locally (inside method bodies), which the sync worker already does for `WEBUI_URL`
- **Error response mapping** — the current HTTP calls map status codes to `FailedFile` objects; direct calls will raise exceptions that need to be caught and mapped to the same `FailedFile` types

### Circular import analysis

The sync worker already uses lazy imports (inside method bodies) for `WEBUI_URL`. The same pattern would work for `process_file`:

```python
def _process_file_directly(self, file_id, collection_name=None):
    from open_webui.routers.retrieval import process_file, ProcessFileForm
    # ...
```

Import chain: `sync_worker.py` → `retrieval.py` → (config, models, vector DB). No path back to `sync_worker.py`, so no circular dependency.

---

## Error Handling Mapping

The current HTTP-based code maps responses to `FailedFile` objects. With direct calls, the mapping changes from status codes to exceptions:

| Current (HTTP) | Direct call equivalent |
|----------------|----------------------|
| HTTP 200 | Function returns normally |
| HTTP 400 + "Duplicate content" | Raises `HTTPException(400, "Duplicate content")` |
| HTTP 400 + "No content extracted" | Raises `HTTPException(400, ...)` |
| HTTP 400 + other | Raises `HTTPException(400, ...)` |
| Non-200 status | Raises `HTTPException(status_code, ...)` |
| `httpx.TimeoutException` | N/A (no HTTP timeout) |
| Other exception | Same — catch `Exception` |

Since `process_file` raises `HTTPException` for error cases (it's a FastAPI route handler), the sync worker would catch `HTTPException` and inspect `.status_code` and `.detail`:

```python
from fastapi import HTTPException

try:
    process_file(request, form, user=user)
except HTTPException as e:
    if e.status_code == 400 and "Duplicate content" in str(e.detail):
        return None  # Already present, treat as success
    return FailedFile(...)
except Exception as e:
    return FailedFile(error_type=ErrorType.PROCESSING_ERROR, error=str(e))
```

---

## Decision Matrix

| Criterion | Option 1 (Internal URL) | Option 2 (Direct calls) |
|-----------|------------------------|------------------------|
| Code change size | ~5 lines | ~100 lines |
| Eliminates HTTP overhead | No | **Yes** |
| Eliminates URL fragility | Partially (new URL to manage) | **Yes** |
| Eliminates auth token need | No | **Yes** |
| Follows existing patterns | No precedent | **`knowledge.py` precedent** |
| Error handling simplicity | Same as today | **Simpler** |
| Risk of regression | Low | Medium (error mapping) |
| Future-proof | No (still HTTP) | **Yes** |

---

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:990-1015` — HTTP call site 1 (vector propagation)
- `backend/open_webui/services/onedrive/sync_worker.py:1046-1099` — HTTP call site 2 (ensure vectors in KB)
- `backend/open_webui/services/onedrive/sync_worker.py:1101-1235` — HTTP call site 3 (two-step file processing)
- `backend/open_webui/routers/knowledge.py:17-22` — Direct import of `process_file` (precedent)
- `backend/open_webui/routers/knowledge.py:492-496` — Direct call to `process_file` (precedent)
- `backend/open_webui/main.py:690-709` — Mock `Request` construction from `app` (precedent)
- `backend/open_webui/routers/retrieval.py:1569-1840` — `process_file` function
- `backend/open_webui/routers/retrieval.py:1352-1559` — `save_docs_to_vector_db` function
- `backend/open_webui/config.py:1138` — `WEBUI_URL` definition
- `backend/open_webui/routers/onedrive_sync.py:114` — `sync_items_to_knowledge()` where worker is created

## Historical Context (from thoughts/)

- `thoughts/shared/research/2026-02-04-sync-cancellation-and-404-errors.md` — Identified WEBUI_URL as the root cause of 404 errors in dev mode
- `thoughts/shared/research/2026-02-04-background-sync-multi-datasource-architecture.md` — Broader sync architecture research (token management, multi-datasource abstraction)

## Related Research

- `thoughts/shared/research/2026-02-04-sync-cancellation-and-404-errors.md`
- `thoughts/shared/research/2026-02-04-background-sync-multi-datasource-architecture.md`

## Open Questions

1. **User object construction**: Should the sync worker fetch the full user from DB via `Users.get_user_by_id()`, or construct a minimal admin user object? Fetching from DB is safer but adds a DB call per process operation.
2. **Sync vs async**: `process_file` is a **sync** function (not `async def`). The sync worker's methods are `async`. Calling a sync function from async code is fine in Python (it just blocks the event loop briefly), but the existing `knowledge.py` call shows this is acceptable. Alternatively, could wrap in `run_in_threadpool()`.
3. **Should this refactoring also extract `process_file`'s config dependency?** A deeper refactoring could make `process_file` accept config as a parameter instead of reading from `request.app.state`, eliminating the mock Request need entirely. This would be a larger change affecting `knowledge.py`, `files.py`, and other callers.
