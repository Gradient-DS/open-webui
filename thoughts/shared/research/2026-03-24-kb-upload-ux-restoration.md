---
date: 2026-03-24T16:00:00+01:00
researcher: Claude
git_commit: 2c0b32ae7
branch: fix/test-bugs-daan-260323
repository: open-webui
topic: 'Restoring pre-merge parallel upload UX for Knowledge Base file uploads'
tags: [research, codebase, knowledge-base, file-upload, socket-io, upstream-merge]
status: complete
last_updated: 2026-03-24
last_updated_by: Claude
---

# Research: Restoring Pre-Merge KB File Upload UX

**Date**: 2026-03-24
**Researcher**: Claude
**Git Commit**: 2c0b32ae7
**Branch**: fix/test-bugs-daan-260323
**Repository**: open-webui

## Research Question

The upstream merge (c26ae48d6 — "merge: upstream version 0.8.9 260320") replaced our custom parallel upload UX with the upstream sequential flow. What exactly needs to change to restore the original design?

## Summary

The pre-merge flow (`c26ae48d6^`) used Socket.IO events for asynchronous file processing notification, enabling parallel uploads with batched success toasts. The upstream merge replaced this with a synchronous sequential flow. Three bug fixes (race condition, duplicate content, empty PDF) are already applied in the working tree. The remaining work is restoring the parallel upload + socket-based UX in both frontend and backend.

## Pre-Merge Flow (c26ae48d6^)

```
Frontend                              Backend
────────                              ───────
Promise.all(files.map(upload))         POST /api/v1/files/
  └─ uploadFileHandler(file)             └─ stores file, returns immediately
     └─ does NOT call addFileHandler     └─ background: process_uploaded_file()
                                              └─ emit_file_status("completed")
$socket.on('file:status')                        via Socket.IO
  └─ handleFileStatus(data)
     └─ addFileHandler(file_id)
     └─ successfulFileCount++
     └─ when all done: showBatchedSuccessToast()
```

## Post-Merge Flow (current)

```
Frontend                              Backend
────────                              ───────
for...of await (sequential)           POST /api/v1/files/
  └─ uploadFileHandler(file)             └─ stores file, returns immediately
     └─ immediately calls addFileHandler └─ background: process_uploaded_file()
        └─ individual toast.success()       └─ (no emit_file_status call)
        └─ init() refresh after EACH file
```

## Detailed Delta: What Was Lost

### Frontend (KnowledgeBase.svelte)

#### 1. Socket handler + batched toasts (existed at lines 709-754)

```javascript
let successfulFileCount = 0;

const showBatchedSuccessToast = () => {
  if (successfulFileCount > 0) {
    const count = successfulFileCount;
    successfulFileCount = 0;
    toast.success(count === 1 ? ... : ...);
  }
};

const handleFileStatus = async (data: {
  file_id: string;
  status: string;
  error?: string;
  collection_name?: string;
}) => {
  if (!fileItems) return;
  const idx = fileItems.findIndex((f) => f.id === data.file_id);
  if (idx >= 0) {
    if (data.status === 'completed') {
      fileItems[idx].status = 'uploaded';
      await addFileHandler(data.file_id);
      successfulFileCount++;
    } else if (data.status === 'failed') {
      fileItems[idx].status = 'error';
      fileItems[idx].error = data.error || 'Processing failed';
      toast.error(`File processing failed: ${data.error || 'Unknown error'}`);
      fileItems = fileItems.filter((file) => file.id !== data.file_id);
    }
    fileItems = fileItems;
    const stillUploading = fileItems.some((f) => f.status === 'uploading');
    if (!stillUploading) {
      showBatchedSuccessToast();
    }
  }
};
```

#### 2. Socket listener registration (lines 1325, 1343)

```javascript
// onMount:
$socket?.on('file:status', handleFileStatus);

// onDestroy:
$socket?.off('file:status', handleFileStatus);
```

#### 3. uploadFileHandler — no direct addFileHandler call (line 350)

```javascript
// Don't call addFileHandler here - Socket.IO 'file:status' event will trigger it
// when processing completes
```

#### 4. Parallel uploads via Promise.all (line 1391)

```javascript
await Promise.all(Array.from(inputFiles).map((file) => uploadFileHandler(file)));
```

#### 5. addFileHandler — no individual toast, no init() per file

```javascript
const addFileHandler = async (fileId) => {
  const res = await addFileToKnowledgeById(localStorage.token, id, fileId).catch(...);
  if (res) {
    // Success toast is batched in handleFileStatus
    if (res.knowledge) {
      knowledge = res.knowledge;
    }
  } else {
    toast.error($i18n.t('Failed to add file.'));
    fileItems = fileItems.filter((file) => file.id !== fileId);
  }
};
```

### Backend (files.py)

#### emit_file_status calls in process_uploaded_file

**On success (lines 148-162 at c26ae48d6^):**

```python
file_data = Files.get_file_by_id(file_item.id)
collection_name = file_data.meta.get("collection_name") if file_data and file_data.meta else None
asyncio.run(
    emit_file_status(
        user_id=user.id,
        file_id=file_item.id,
        status="completed",
        collection_name=collection_name,
    )
)
```

**On failure (lines 181-188 at c26ae48d6^):**

```python
asyncio.run(
    emit_file_status(
        user_id=user.id,
        file_id=file_item.id,
        status="failed",
        error=error_msg,
    )
)
```

The import already exists in the current code (files.py:46) but the calls were removed by the merge.

## What's Already Fixed in Working Tree

| Bug                                | File                      | Status                                |
| ---------------------------------- | ------------------------- | ------------------------------------- |
| Race condition (no content yet)    | retrieval.py:1835-1891    | Fixed — falls back to Loader          |
| Duplicate content blocks re-upload | retrieval.py:1574-1585    | Fixed — deletes old entries, re-adds  |
| Empty PDF hard error               | retrieval.py:2038-2065    | Fixed — returns warning instead       |
| Warning propagation                | knowledge.py:739-769      | Fixed — injects `warning` in response |
| Warning toast in frontend          | KnowledgeBase.svelte:~989 | Fixed — shows toast.warning()         |
| Double-load flicker                | KnowledgeBase.svelte      | Fixed — fetchId guard, no null-out    |

## Restoration Checklist

### Backend: `files.py`

1. Re-add `emit_file_status(status="completed")` call after successful processing in `process_uploaded_file`
2. Re-add `emit_file_status(status="failed")` call in the error handler
3. Import is already present (line 46)

### Frontend: `KnowledgeBase.svelte`

1. Add `successfulFileCount` state variable
2. Add `showBatchedSuccessToast()` function
3. Add `handleFileStatus()` socket handler (must integrate with warning support from current fixes)
4. Register `$socket?.on('file:status', handleFileStatus)` in onMount
5. Register `$socket?.off('file:status', handleFileStatus)` in onDestroy
6. Remove `await addFileHandler(uploadedFile.id)` from `uploadFileHandler` — let socket handle it
7. Change file input handler from `for...of await` to `Promise.all(Array.from(inputFiles).map(...))`
8. Update `addFileHandler` to not show individual toasts or call `init()` per file — batch via `handleFileStatus`
9. Keep warning toast support: when `handleFileStatus` calls `addFileHandler`, check for `res.warning`

### Integration Considerations

- The `addFileHandler` in the pre-merge code updated `knowledge` from the response but didn't call `init()`. This avoids N refreshes for N files. The batched toast + final refresh should happen when `stillUploading` becomes false.
- The drag-and-drop path was already fire-and-forget (no await) in the current code — this is compatible with the parallel pattern.
- URL uploads should continue to call `addFileHandler` directly (they don't go through background processing).
- The `uploadWeb` function at line ~200 has its own flow and should be left as-is.

## Code References

- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte` — main component
- `backend/open_webui/routers/files.py:46` — existing unused import
- `backend/open_webui/routers/files.py:89-165` — `process_uploaded_file` (needs emit calls)
- `backend/open_webui/services/files/events.py:7-38` — `emit_file_status` definition (exists, functional)
- `src/lib/apis/files/index.ts:4-47` — `uploadFile` API function
