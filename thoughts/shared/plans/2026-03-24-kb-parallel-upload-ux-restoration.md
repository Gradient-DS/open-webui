# KB Parallel Upload UX Restoration — Implementation Plan

## Overview

The upstream merge (c26ae48d6 — "merge: upstream version 0.8.9 260320") replaced our custom parallel upload UX with a sequential flow. This plan restores the original design: parallel file uploads via `Promise.all`, socket-based completion notification via `file:status` events, and batched success toasts.

## Current State Analysis

- **Backend**: `emit_file_status` is defined in `services/files/events.py:7-38` and imported at `files.py:46` but never called. `process_uploaded_file` silently succeeds or writes `status: "failed"` to the DB.
- **Frontend** (`KnowledgeBase.svelte`):
  - `uploadFileHandler` (line 344) calls `addFileHandler` directly after upload
  - `addFileHandler` (lines 985-1002) shows an individual toast and calls `init()` per file
  - File input handler (lines 1362-1365) uses sequential `for...of await`
  - Drag-and-drop (lines 1177-1179) fires uploads without `await` but shows premature toasts
  - No `file:status` socket listener exists
  - No batched toast logic exists

### Key Discoveries:

- `emit_file_status` import already at `files.py:46` — no new import needed
- Drag-and-drop is already fire-and-forget (parallel) — just needs premature toast removal
- `uploadWeb` (line 258) calls `addFileHandler` directly — this is correct since web URLs don't use background processing
- Warning support (`res.warning` at line 992) must be preserved in the new flow
- `AddTextContentModal` (line 1351) calls `uploadFileHandler` for single files — will benefit from socket path too

## Desired End State

Uploading multiple files to a KB:

1. All uploads fire in parallel via `Promise.all`
2. Each file appears in the UI immediately with `status: 'uploading'`
3. Backend processes each file in the background and emits `file:status` via Socket.IO
4. Frontend socket handler receives completion/failure events
5. On completion: `addFileHandler` is called (without individual toast or `init()`)
6. When all uploads finish: a single batched toast ("N file(s) added successfully") and one `init()` call
7. On failure: individual error toast, file removed from UI

### Verification:

- Upload 3+ files via file picker → all upload simultaneously, single success toast at end
- Upload via drag-and-drop → same parallel behavior, no premature toasts
- Upload a file that fails processing → error toast for that file, others succeed normally
- Upload an empty PDF → warning toast (existing fix preserved)
- Upload via URL → still works with direct `addFileHandler` call (unchanged)

## What We're NOT Doing

- Changing the `uploadWeb` flow — it doesn't use background processing
- Modifying `process_file` in `retrieval.py` — already fixed for race conditions
- Adding retry logic for failed socket events
- Changing the upload API endpoint itself

## Implementation Approach

Two phases: backend first (add socket emissions), then frontend (consume them and switch to parallel). This ordering means the backend change is independently deployable and harmless without the frontend change.

---

## Phase 1: Backend — Re-add `emit_file_status` Calls

### Overview

Add `emit_file_status` calls in `process_uploaded_file` so the frontend can be notified when background file processing completes or fails.

### Changes Required:

#### 1. `backend/open_webui/routers/files.py` — `process_uploaded_file` function

**File**: `backend/open_webui/routers/files.py`
**Location**: Inside `_process_handler`, lines 98-159

**Success path** — after the `process_file` calls succeed (end of try block, before the except), add:

```python
            # After all process_file calls (line ~148), before except:
            file_data = Files.get_file_by_id(file_item.id)
            collection_name = (
                file_data.meta.get("collection_name")
                if file_data and file_data.meta
                else None
            )
            asyncio.run(
                emit_file_status(
                    user_id=user.id,
                    file_id=file_item.id,
                    status="completed",
                    collection_name=collection_name,
                )
            )
```

**Failure path** — in the `except Exception as e` block (after line 159), add:

```python
            # After Files.update_file_data_by_id (line ~159):
            error_msg = str(e.detail) if hasattr(e, "detail") else str(e)
            asyncio.run(
                emit_file_status(
                    user_id=user.id,
                    file_id=file_item.id,
                    status="failed",
                    error=error_msg,
                )
            )
```

The full `_process_handler` after changes:

```python
    def _process_handler(db_session):
        try:
            content_type = file.content_type

            # Detect mis-labeled text files (e.g. .ts → video/mp2t)
            if content_type and content_type.startswith(("image/", "video/")):
                if _is_text_file(file_path):
                    content_type = "text/plain"

            if content_type:
                stt_supported_content_types = getattr(
                    request.app.state.config, "STT_SUPPORTED_CONTENT_TYPES", []
                )

                if strict_match_mime_type(stt_supported_content_types, content_type):
                    file_path_processed = Storage.get_file(file_path)
                    result = transcribe(
                        request, file_path_processed, file_metadata, user
                    )

                    process_file(
                        request,
                        ProcessFileForm(
                            file_id=file_item.id, content=result.get("text", "")
                        ),
                        user=user,
                        db=db_session,
                    )
                elif (not content_type.startswith(("image/", "video/"))) or (
                    request.app.state.config.CONTENT_EXTRACTION_ENGINE == "external"
                ):
                    process_file(
                        request,
                        ProcessFileForm(file_id=file_item.id),
                        user=user,
                        db=db_session,
                    )
                else:
                    raise Exception(
                        f"File type {content_type} is not supported for processing"
                    )
            else:
                log.info(
                    f"File type {file.content_type} is not provided, but trying to process anyway"
                )
                process_file(
                    request,
                    ProcessFileForm(file_id=file_item.id),
                    user=user,
                    db=db_session,
                )

            # Notify frontend via Socket.IO that processing completed
            file_data = Files.get_file_by_id(file_item.id)
            collection_name = (
                file_data.meta.get("collection_name")
                if file_data and file_data.meta
                else None
            )
            asyncio.run(
                emit_file_status(
                    user_id=user.id,
                    file_id=file_item.id,
                    status="completed",
                    collection_name=collection_name,
                )
            )

        except Exception as e:
            log.error(f"Error processing file: {file_item.id}")
            error_msg = str(e.detail) if hasattr(e, "detail") else str(e)
            Files.update_file_data_by_id(
                file_item.id,
                {
                    "status": "failed",
                    "error": error_msg,
                },
                db=db_session,
            )
            asyncio.run(
                emit_file_status(
                    user_id=user.id,
                    file_id=file_item.id,
                    status="failed",
                    error=error_msg,
                )
            )
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts without errors: `open-webui dev`
- [x] Backend linting passes: `npm run format:backend` (no diff)
- [x] No import errors — `emit_file_status` import already at line 46

#### Manual Verification:

- [ ] Upload a file to a KB → check browser devtools Socket.IO messages for `file:status` event with `status: "completed"`
- [ ] Upload a file that will fail processing → check for `file:status` event with `status: "failed"`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Frontend — Socket Handler + Parallel Uploads

### Overview

Add `file:status` socket handler, batched toast logic, switch to parallel uploads, and update `addFileHandler` to not show individual toasts or call `init()` per file.

### Changes Required:

#### 1. Add state variable and batched toast function

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: Near other state variables (before `uploadFileHandler`)

```javascript
let successfulFileCount = 0;

const showBatchedSuccessToast = () => {
	if (successfulFileCount > 0) {
		const count = successfulFileCount;
		successfulFileCount = 0;
		toast.success(
			count === 1
				? $i18n.t('File added successfully.')
				: $i18n.t('{{count}} files added successfully.', { count: count.toString() })
		);
		init();
	}
};
```

#### 2. Add `handleFileStatus` socket handler

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: After the batched toast function

```javascript
const handleFileStatus = async (data: {
    file_id: string;
    status: string;
    error?: string;
    collection_name?: string;
}) => {
    if (!fileItems) return;

    const idx = fileItems.findIndex((f) => f.id === data.file_id);
    if (idx < 0) return;

    if (data.status === 'completed') {
        fileItems[idx].status = 'uploaded';
        await addFileHandler(data.file_id);
        successfulFileCount++;
    } else if (data.status === 'failed') {
        fileItems[idx].status = 'error';
        fileItems[idx].error = data.error || 'Processing failed';
        toast.error($i18n.t('File processing failed: {{error}}', { error: data.error || 'Unknown error' }));
        fileItems = fileItems.filter((file) => file.id !== data.file_id);
    }

    fileItems = fileItems;

    const stillUploading = fileItems.some((f) => f.status === 'uploading');
    if (!stillUploading) {
        showBatchedSuccessToast();
    }
};
```

#### 3. Register socket listener in `onMount` (line ~1291)

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Add before the closing `});` of onMount:

```javascript
$socket?.on('file:status', handleFileStatus);
```

#### 4. Unregister socket listener in `onDestroy` (line ~1307)

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Add before the closing `});` of onDestroy:

```javascript
$socket?.off('file:status', handleFileStatus);
```

#### 5. Remove direct `addFileHandler` call from `uploadFileHandler`

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: Line 344

Change:

```javascript
            } else {
                await addFileHandler(uploadedFile.id);
            }
```

To:

```javascript
            }
            // Don't call addFileHandler here — Socket.IO 'file:status' event
            // will trigger it when background processing completes
```

#### 6. Change file input handler from sequential to parallel

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: Lines 1362-1365

Change:

```javascript
        if (inputFiles && inputFiles.length > 0) {
            for (const file of inputFiles) {
                await uploadFileHandler(file);
            }
```

To:

```javascript
        if (inputFiles && inputFiles.length > 0) {
            await Promise.all(Array.from(inputFiles).map((file) => uploadFileHandler(file)));
```

#### 7. Update `addFileHandler` — no individual toast, no `init()`

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: Lines 985-1002

Change to:

```javascript
const addFileHandler = async (fileId) => {
	const res = await addFileToKnowledgeById(localStorage.token, id, fileId).catch((e) => {
		toast.error(`${e}`);
		return null;
	});

	if (res) {
		if (res.warning) {
			toast.warning(res.warning);
		}
		// Success toast is batched in handleFileStatus/showBatchedSuccessToast
		if (res.knowledge) {
			knowledge = res.knowledge;
		}
	} else {
		toast.error($i18n.t('Failed to add file.'));
		fileItems = fileItems.filter((file) => file.id !== fileId);
	}
};
```

#### 8. Fix drag-and-drop premature toasts

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: Lines 1176-1179

Change:

```javascript
            } else {
                toast.info($i18n.t('Uploading file...'));
                uploadFileHandler(item.getAsFile());
                toast.success($i18n.t('File uploaded!'));
            }
```

To:

```javascript
            } else {
                uploadFileHandler(item.getAsFile());
            }
```

### Success Criteria:

#### Automated Verification:

- [x] Frontend builds: `npm run build`
- [ ] Frontend lints: `npm run lint:frontend`
- [ ] TypeScript checks: `npm run check` (no new errors beyond pre-existing ~8000)

#### Manual Verification:

- [ ] Upload 3+ files via file picker → all appear with 'uploading' status simultaneously, single batched toast when all complete
- [ ] Upload 1 file → single "File added successfully." toast (not "1 files added")
- [ ] Upload via drag-and-drop → parallel uploads, no premature "File uploaded!" toast, batched success toast
- [ ] Upload a file that fails processing → error toast for that file, other files still succeed and get batched toast
- [ ] Upload an empty PDF → warning toast appears (existing fix preserved)
- [ ] Upload via URL (webpage attach) → still works with individual toast (unchanged flow)
- [ ] Upload via "Add text content" → file processes via socket path, success toast appears
- [ ] Navigate away during upload → no errors (socket listener cleaned up in onDestroy)
- [ ] `knowledge` object updates correctly after uploads (from `res.knowledge` in addFileHandler)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Testing Strategy

### Manual Testing Steps:

1. Upload 5 files simultaneously via file picker — verify parallel upload + single toast
2. Upload 1 file — verify singular toast message
3. Mix of valid + invalid files — verify error toast for bad file, success toast for rest
4. Empty PDF upload — verify warning toast (not error)
5. Drag-and-drop folder with multiple files — verify parallel, no premature toasts
6. Upload via URL — verify unchanged behavior (direct `addFileHandler`, individual toast)
7. Navigate away mid-upload — verify no console errors

### Edge Cases:

- Upload while another upload is in progress (socket handler should handle interleaved events)
- Very large file that takes long to process (UI should show 'uploading' until socket event)
- Network interruption during upload (existing error handling in `uploadFileHandler` catches this)

## References

- Research document: `thoughts/shared/research/2026-03-24-kb-upload-ux-restoration.md`
- Pre-merge commit: `c26ae48d6^` (original parallel upload implementation)
- `emit_file_status` definition: `backend/open_webui/services/files/events.py:7-38`
- Socket room targeting: `f"user:{user_id}"` — ensures only the uploading user gets events
