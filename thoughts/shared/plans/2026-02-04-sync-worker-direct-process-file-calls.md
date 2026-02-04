# Replace WEBUI_URL HTTP Self-Calls with Direct process_file Calls

## Overview

The OneDrive sync worker currently makes HTTP requests to its own FastAPI server via `WEBUI_URL` to process files. This is fragile (wrong port in dev causes 404s, unnecessary round-trip in production). Since the worker runs in the same Python process, we replace these with direct `process_file()` function calls — the same pattern `knowledge.py` already uses.

## Current State Analysis

Three call sites in `sync_worker.py` use `WEBUI_URL` to call `POST /api/v1/retrieval/process/file`:

| Call site | Lines | Purpose |
|-----------|-------|---------|
| Vector propagation | 990-1015 | Copy updated vectors to other KBs referencing same file |
| `_ensure_vectors_in_kb()` | 1046-1099 | Copy existing vectors into KB (dedup path) |
| `_process_file_via_api()` | 1101-1235 | Two-step: extract content + add to KB |

All use the pattern:
```python
base_url = WEBUI_URL.value if WEBUI_URL.value else "http://localhost:8080"
```

The `user_token` field (JWT) exists solely for the `Authorization` headers on these HTTP calls.

### Key Discoveries:
- `knowledge.py:492-496` already calls `process_file` directly as a Python function (precedent)
- `main.py:690-709` already constructs mock `Request` objects from the `app` reference (precedent)
- The "Duplicate content" check in the sync worker is dead code — `process_file` never raises it (`ERROR_MESSAGES.DUPLICATE_CONTENT` in `constants.py:103` is unused by any router)
- `process_file` only needs `request.app.state.config` and `request.app.state.ef` from the request object — no HTTP-specific properties
- `process_file` is a sync function called from sync `knowledge.py` routes — same pattern applies here

## Desired End State

The sync worker calls `process_file()` directly as a Python function, with no HTTP self-calls. `WEBUI_URL` and `user_token` are no longer used by the sync worker.

### How to verify:
- `grep -r "WEBUI_URL\|httpx\|user_token" backend/open_webui/services/onedrive/sync_worker.py` returns no matches
- OneDrive sync processes files successfully in dev mode (port 5173 frontend, port 8080 backend) without any WEBUI_URL configuration
- Existing sync behavior is preserved: files are extracted, embedded, and added to KB collections

## What We're NOT Doing

- Not refactoring `process_file` itself to remove its `request` dependency (that would affect `knowledge.py`, `files.py`, and other callers)
- Not removing `user_token` from the frontend `SyncItemsRequest` type (breaking API change; backend just ignores it)
- Not changing the external RAG pipeline integration — `process_file` still calls the external pipeline internally when configured

## Implementation Approach

Thread the FastAPI `app` reference from the router to the worker, construct a minimal mock `Request` (same pattern as `main.py:690`), and call `process_file` directly. Map `HTTPException` to `FailedFile` instead of parsing HTTP status codes.

## Phase 1: Pass `app` to Sync Worker and Add Helper

### Overview
Thread the FastAPI app reference through the call chain and add a `_make_request()` helper method to construct minimal Request objects.

### Changes Required:

#### 1. Update router to pass `app`
**File**: `backend/open_webui/routers/onedrive_sync.py`

The `sync_items` endpoint (line 57) already has `request` available via the Pydantic model name collision — but actually it doesn't have `Request` injected. We need to add it.

Change the endpoint signature to inject `Request` and pass `req.app` to the background task:

```python
# Line 57-59: Add fastapi_request parameter
@router.post("/sync/items")
async def sync_items(
    request: SyncItemsRequest,
    fastapi_request: Request,  # NEW: inject the actual HTTP request
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_verified_user),
):
```

Add the import at the top of the file:
```python
from starlette.requests import Request
```

Update the `background_tasks.add_task` call (line 102-109) to pass the app:
```python
background_tasks.add_task(
    sync_items_to_knowledge,
    knowledge_id=request.knowledge_id,
    sources=all_sources,
    access_token=request.access_token,
    user_id=user.id,
    app=fastapi_request.app,  # NEW: pass app reference
)
```

Update `sync_items_to_knowledge` (line 114-131) to accept and pass `app`:
```python
async def sync_items_to_knowledge(
    knowledge_id: str,
    sources: List[dict],
    access_token: str,
    user_id: str,
    app,  # NEW: FastAPI app reference
):
    from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker

    worker = OneDriveSyncWorker(
        knowledge_id=knowledge_id,
        sources=sources,
        access_token=access_token,
        user_id=user_id,
        app=app,  # NEW: pass to worker
    )
    await worker.sync()
```

#### 2. Update worker constructor and add helper
**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Update `__init__` (lines 89-104) to accept `app` instead of `user_token`:

```python
def __init__(
    self,
    knowledge_id: str,
    sources: List[Dict[str, Any]],
    access_token: str,
    user_id: str,
    app,  # NEW: FastAPI app reference for direct process_file calls
    event_emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
):
    self.knowledge_id = knowledge_id
    self.sources = sources
    self.access_token = access_token  # OneDrive Graph API token
    self.user_id = user_id
    self.app = app
    self.event_emitter = event_emitter
    self._client: Optional[GraphClient] = None
```

Add a `_make_request()` helper and a `_get_user()` helper after `__init__`:

```python
def _make_request(self):
    """Construct a minimal Request for calling retrieval functions directly.

    Same pattern as main.py:690-709 (lifespan mock request).
    process_file only accesses request.app.state.config and request.app.state.ef.
    """
    from starlette.requests import Request
    from starlette.datastructures import Headers

    return Request({
        "type": "http",
        "method": "POST",
        "path": "/internal/onedrive-sync",
        "query_string": b"",
        "headers": Headers({}).raw,
        "app": self.app,
    })

def _get_user(self):
    """Fetch the user object for process_file access control."""
    from open_webui.models.users import Users

    user = Users.get_user_by_id(self.user_id)
    if not user:
        raise RuntimeError(f"User {self.user_id} not found")
    return user
```

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` completes successfully
- [x] No new lint errors in changed files: `grep -c "user_token" backend/open_webui/routers/onedrive_sync.py` returns 0

#### Manual Verification:
- [x] N/A — no behavior change yet, this is plumbing only

**Implementation Note**: This phase only changes the constructor/plumbing. No behavior change. Proceed to Phase 2 immediately.

---

## Phase 2: Replace HTTP Call Sites with Direct Calls

### Overview
Replace all 3 `httpx` HTTP call sites with direct `process_file()` calls, mapping `HTTPException` to `FailedFile` objects.

### Changes Required:

#### 1. Replace vector propagation block (lines 990-1015)
**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Replace the `httpx` block after `VECTOR_DB_CLIENT.delete` (lines 990-1015) with:

```python
                        # Copy new vectors via direct function call
                        try:
                            from open_webui.routers.retrieval import process_file, ProcessFileForm
                            process_file(
                                self._make_request(),
                                ProcessFileForm(
                                    file_id=file_id,
                                    collection_name=kf.knowledge_id,
                                ),
                                user=self._get_user(),
                            )
                        except Exception as e:
                            log.warning(
                                f"Failed to propagate vectors to KB {kf.knowledge_id}: {e}"
                            )
```

This preserves the existing best-effort semantics — propagation failures are logged as warnings and don't fail the sync.

#### 2. Replace `_ensure_vectors_in_kb()` (lines 1046-1099)
**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Replace the entire method body:

```python
    async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
        """Copy vectors from the per-file collection into this KB's collection.

        Used for cross-user dedup: when a file already exists and doesn't need
        re-processing, we still need to copy its vectors into the current KB.
        """
        try:
            from open_webui.routers.retrieval import process_file, ProcessFileForm
            from fastapi import HTTPException

            process_file(
                self._make_request(),
                ProcessFileForm(
                    file_id=file_id,
                    collection_name=self.knowledge_id,
                ),
                user=self._get_user(),
            )
            return None  # Success
        except HTTPException as e:
            detail = str(e.detail) if e.detail else ""
            if e.status_code == 400 and "Duplicate content" in detail:
                return None  # Already in KB, treat as success
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Failed to copy vectors to KB: {detail}"[:100],
            )
        except Exception as e:
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Error copying vectors: {str(e)}"[:80],
            )
```

Note: The `HTTPException` "Duplicate content" check is kept for defensive safety even though `process_file` doesn't currently raise it. The timeout handling is removed since there's no HTTP timeout with direct calls.

#### 3. Replace `_process_file_via_api()` (lines 1101-1235)
**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Replace the entire method. The two-step logic (extract → add to KB) remains, but via direct calls:

```python
    async def _process_file_via_api(self, file_id: str, filename: str) -> Optional[FailedFile]:
        """Process file by calling the retrieval processing function directly.

        Two-step process:
        1. First call WITHOUT collection_name to extract and process file content
        2. Second call WITH collection_name to add the processed content to knowledge base

        This is needed because when collection_name is provided, the retrieval function
        assumes the file has already been processed and tries to use existing vectors
        or file.data.content, which are empty for newly downloaded OneDrive files.

        Returns:
            None on success, FailedFile on error
        """
        from open_webui.routers.retrieval import process_file, ProcessFileForm
        from fastapi import HTTPException

        request = self._make_request()
        user = self._get_user()

        try:
            # Step 1: Process file content (extract text, create embeddings in file-{id} collection)
            try:
                process_file(
                    request,
                    ProcessFileForm(file_id=file_id),
                    user=user,
                )
                log.info(f"Successfully extracted content from file {file_id}")
            except HTTPException as e:
                detail = str(e.detail) if e.detail else ""
                if e.status_code == 400 and "Duplicate content" in detail:
                    log.debug(
                        f"File {file_id} already has embeddings, skipping to knowledge base addition"
                    )
                elif e.status_code == 400 and ("No content extracted" in detail or "empty" in detail.lower()):
                    log.debug(f"File {file_id} has no extractable content")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.EMPTY_CONTENT.value,
                        error_message="File has no extractable content",
                    )
                else:
                    log.debug(f"Failed to process file content {file_id}: {e.status_code} - {detail}")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=detail[:100] if detail else "Processing failed",
                    )

            # Step 2: Add processed content to knowledge base collection
            try:
                process_file(
                    request,
                    ProcessFileForm(
                        file_id=file_id,
                        collection_name=self.knowledge_id,
                    ),
                    user=user,
                )
                log.info(f"Successfully added file {file_id} to knowledge base {self.knowledge_id}")
            except HTTPException as e:
                detail = str(e.detail) if e.detail else ""
                if e.status_code == 400 and "Duplicate content" in detail:
                    log.debug(
                        f"File {file_id} already exists in knowledge base {self.knowledge_id}"
                    )
                    return None  # Success - file is already in the knowledge base
                else:
                    log.debug(f"Failed to add file {file_id} to knowledge base: {e.status_code} - {detail}")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=detail[:100] if detail else "Failed to add to knowledge base",
                    )

            return None  # Success
        except Exception as e:
            log.warning(f"Error processing file {file_id} ({filename}): {e}")
            return FailedFile(
                filename=filename,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )
```

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` completes successfully
- [x] `grep -c "httpx\|WEBUI_URL" backend/open_webui/services/onedrive/sync_worker.py` returns 0
- [x] `grep -c "user_token" backend/open_webui/services/onedrive/sync_worker.py` returns 0

#### Manual Verification:
- [ ] OneDrive sync processes files successfully with `WEBUI_URL` unset (or set to the wrong port)
- [ ] Files appear in the knowledge base with correct content and embeddings
- [ ] Syncing a KB that shares files with another KB propagates vectors correctly
- [ ] Re-syncing unchanged files (dedup path) works without errors
- [ ] External RAG pipeline is used when `EXTERNAL_PIPELINE_URL` is configured

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that OneDrive sync works end-to-end before proceeding to Phase 3.

---

## Phase 3: Clean Up Dead Code

### Overview
Remove `user_token` from the backend chain since it's no longer used. Keep the frontend field to avoid a breaking API change (backend simply ignores it).

### Changes Required:

#### 1. Remove `user_token` from router
**File**: `backend/open_webui/routers/onedrive_sync.py`

Remove `user_token` from `SyncItemsRequest` model (line 33):
```python
# Before
class SyncItemsRequest(BaseModel):
    knowledge_id: str
    items: List[SyncItem]
    access_token: str
    user_token: str

# After
class SyncItemsRequest(BaseModel):
    knowledge_id: str
    items: List[SyncItem]
    access_token: str
```

Remove `user_token=request.user_token` from the `background_tasks.add_task` call (already done in Phase 1).

Remove `user_token: str` parameter from `sync_items_to_knowledge` function signature (already done in Phase 1).

#### 2. Remove `httpx` from sync worker imports
**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Verify no remaining `import httpx` statements exist. The lazy imports inside the removed methods are already gone from Phase 2.

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` completes successfully
- [x] `grep -rn "user_token" backend/open_webui/routers/onedrive_sync.py` returns 0
- [x] `grep -rn "user_token" backend/open_webui/services/onedrive/sync_worker.py` returns 0
- [x] `grep -rn "httpx" backend/open_webui/services/onedrive/sync_worker.py` returns 0

#### Manual Verification:
- [ ] OneDrive sync still starts successfully from the UI (frontend sends `user_token` but backend ignores it via Pydantic's default behavior)

---

## Testing Strategy

### Manual Testing Steps:
1. Set `WEBUI_URL` to an incorrect value (e.g., `http://localhost:9999`) or unset it entirely
2. Start a OneDrive sync — files should process successfully since HTTP self-calls are gone
3. Verify files appear in the KB with searchable content
4. Test the dedup path: re-sync the same KB without changes — should complete with 0 processed, 0 failed
5. Test cross-KB propagation: sync a file that exists in another KB — both KBs should have vectors
6. If external pipeline is configured: verify it's still used during sync

### Edge Cases:
- User deleted between sync start and file processing — `_get_user()` raises `RuntimeError`, file fails gracefully
- File deleted between download and processing — `process_file` raises `HTTPException(404)`, mapped to `FailedFile`
- Concurrent syncs on same KB — unrelated to this change, existing behavior preserved

## Performance Considerations

Direct function calls eliminate per-file overhead of:
- HTTP connection setup/teardown (httpx.AsyncClient)
- Request serialization + response deserialization
- JWT token validation on each request
- Starlette middleware chain execution

For a sync of 200 files with 2-step processing (400 HTTP calls previously), this removes ~400 HTTP round-trips.

## References

- Research document: `thoughts/shared/research/2026-02-04-sync-worker-webui-url-replacement.md`
- Precedent (direct call): `backend/open_webui/routers/knowledge.py:492-496`
- Precedent (mock Request): `backend/open_webui/main.py:690-709`
- Prior 404 analysis: `thoughts/shared/research/2026-02-04-sync-cancellation-and-404-errors.md`
