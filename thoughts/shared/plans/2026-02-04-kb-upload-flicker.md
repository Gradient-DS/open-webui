# KB Upload UI Flicker Fixes - Implementation Plan

## Overview

Apply 5 targeted state-management fixes to `KnowledgeBase.svelte` to eliminate UI flickering during OneDrive file uploads, file deletions, and content saves. All fixes are isolated to one file and independent of other feature work.

## Current State Analysis

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

The UI flickers because:
1. `getItemsPage()` sets `fileItems = null` before every API call (line 140-141), causing the file list to vanish and show a spinner until the response arrives.
2. Two reactive `$:` blocks (lines 117-135) both trigger `getItemsPage()` during `init()` — once from `reset()` changing `currentPage`, once from the reactive block tracking `currentPage`.
3. Both HTTP polling (`pollOneDriveSyncStatus`) and Socket.IO (`handleOneDriveSyncProgress`) call `init()` on sync completion, causing double full-refresh.
4. `deleteFileHandler` (line 917) calls `init()` after deletion — full list reset with spinner instead of removing the single item.
5. `updateFileContentHandler` (line 958) calls `init()` after saving — resets to page 1 unnecessarily.

### Key Discoveries:
- Template at line 1484 branches on `fileItems !== null` — when null, shows `<Spinner />`
- `init()` calls `reset()` which sets `currentPage = 1`, then calls `getItemsPage()` — but the reactive block at line 117 also fires from the `currentPage` change
- Both sync handlers run concurrently and independently detect completion

## Desired End State

After implementation:
- File list stays visible during background refreshes (no spinner flash)
- `getItemsPage()` fires exactly once per `init()` call
- Only one sync handler refreshes the list on completion (not both)
- Deleting a file instantly removes it from the list with rollback on failure
- Saving file content refreshes the current page without resetting to page 1

### Verification:
- Upload OneDrive files: no spinner flicker during sync
- Delete a file: instant removal from list, no full-page flash
- Save file content: stays on current page, no flicker
- Build succeeds: `npm run build`

## What We're NOT Doing

- Refactoring the reactive blocks to `$effect` (Svelte 5 migration)
- Changing the polling/Socket.IO architecture
- Modifying any other files or components
- Adding tests (no existing test coverage for this component)

## Implementation Approach

Apply all 5 fixes in a single phase. They are independent changes within the same file, but together they form one cohesive "fix flicker" commit.

## Phase 1: Apply All Flicker Fixes

### Overview
Five changes to `KnowledgeBase.svelte` that eliminate all identified flicker sources.

### Changes Required:

#### 1. Add `_skipReactiveRefresh` guard to prevent double `getItemsPage()` from reactive blocks

**Lines affected**: 108-135 (the `reset`, `init`, and reactive `$:` blocks)

Replace the current `reset()`, `init()`, and reactive blocks with:

```svelte
let _skipReactiveRefresh = false;

const reset = () => {
    currentPage = 1;
};

const init = async () => {
    _skipReactiveRefresh = true;
    reset();
    _skipReactiveRefresh = false;
    await getItemsPage();
};

$: if (
    knowledgeId !== null &&
    query !== undefined &&
    viewOption !== undefined &&
    sortKey !== undefined &&
    direction !== undefined &&
    currentPage !== undefined
) {
    if (!_skipReactiveRefresh) {
        getItemsPage();
    }
}

$: if (
    query !== undefined &&
    viewOption !== undefined &&
    sortKey !== undefined &&
    direction !== undefined
) {
    reset();
}
```

**Why**: When `init()` calls `reset()`, it changes `currentPage`, which triggers the first reactive block to call `getItemsPage()`. Then `init()` itself also calls `getItemsPage()`. The `_skipReactiveRefresh` flag suppresses the reactive block during `init()`.

#### 2. Remove `fileItems = null` from `getItemsPage()`

**Lines affected**: 140-141

Remove these two lines from `getItemsPage()`:

```diff
 const getItemsPage = async () => {
     if (knowledgeId === null) return;
-
-    fileItems = null;
-    fileItemsTotal = null;

     if (sortKey === null) {
```

**Why**: Old data stays visible until new data replaces it. The template's `{#if fileItems !== null}` check at line 1484 no longer triggers a spinner flash.

#### 3. Add `_syncRefreshDone` flag to deduplicate sync completion handlers

**Lines affected**: Add variable declaration near other state vars, modify `pollOneDriveSyncStatus` and `handleOneDriveSyncProgress`.

Add a new flag:
```javascript
let _syncRefreshDone = false;
```

In `pollOneDriveSyncStatus` (line 588), guard each `init()` call:

```diff
-            } else if (oneDriveSyncStatus.status === 'completed' || oneDriveSyncStatus.status === 'completed_with_errors') {
-                // Toast is handled by Socket.IO handler, just refresh
-                isSyncingOneDrive = false;
-                await init();
+            } else if (oneDriveSyncStatus.status === 'completed' || oneDriveSyncStatus.status === 'completed_with_errors') {
+                isSyncingOneDrive = false;
+                if (!_syncRefreshDone) {
+                    _syncRefreshDone = true;
+                    await init();
+                }
```

Apply the same guard to `file_limit_exceeded` and `cancelled` branches in polling.

In `handleOneDriveSyncProgress` (line 807), set the flag before calling `init()`:

```diff
         if (data.status === 'completed' || data.status === 'completed_with_errors') {
             // ... toast logic stays the same ...
             isSyncingOneDrive = false;
+            _syncRefreshDone = true;
             // ... getKnowledgeById stays ...
             await init();
```

Apply the same to the `cancelled` and `file_limit_exceeded` branches in the Socket.IO handler.

Reset the flag at the start of new syncs (in `syncOneDriveHandler` / `resyncOneDriveHandler`):

```diff
+        _syncRefreshDone = false;
         await startOneDriveSyncItems(localStorage.token, { ... });
```

**Why**: Both polling and Socket.IO detect completion independently. Whichever fires first sets `_syncRefreshDone = true`, preventing the other from also calling `init()`.

#### 4. Optimistic delete with rollback in `deleteFileHandler`

**Lines affected**: 907-923

Replace the current `deleteFileHandler`:

```javascript
const deleteFileHandler = async (fileId) => {
    const previousItems = fileItems;
    const previousTotal = fileItemsTotal;
    fileItems = (fileItems ?? []).filter((file) => file.id !== fileId);
    fileItemsTotal = Math.max(0, (fileItemsTotal ?? 1) - 1);

    try {
        const res = await removeFileFromKnowledgeById(localStorage.token, id, fileId);
        if (res) {
            knowledge = res;
            toast.success($i18n.t('File removed successfully.'));
            await getItemsPage(); // background refresh, no spinner
        } else {
            fileItems = previousItems;
            fileItemsTotal = previousTotal;
        }
    } catch (e) {
        console.error('Error in deleteFileHandler:', e);
        fileItems = previousItems;
        fileItemsTotal = previousTotal;
        toast.error(`${e}`);
    }
};
```

**Why**: The file disappears instantly from the list. If the API call fails, the list reverts. No spinner flash.

#### 5. Use `getItemsPage()` instead of `init()` after content save

**Lines affected**: 958

```diff
-                await init();
+                await getItemsPage();
```

**Why**: `init()` calls `reset()` which sets `currentPage = 1`. After saving file content, the user should stay on their current page. `getItemsPage()` refreshes without resetting pagination.

### Success Criteria:

#### Automated Verification:
- [ ] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] Upload OneDrive files: file list stays visible during sync, no spinner flash between refreshes
- [ ] Delete a file: item disappears instantly, no full-list flicker
- [ ] Save file content: stays on current page, no page-1 reset or flicker
- [ ] Navigate between pages: pagination still works correctly
- [ ] Sync completion: only one refresh happens (check browser console for duplicate `init()` calls)

## References

- Research document: `thoughts/shared/research/2026-02-04-kb-upload-flicker.md`
- Target file: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
