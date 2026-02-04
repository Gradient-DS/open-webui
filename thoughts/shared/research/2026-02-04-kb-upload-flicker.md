---
date: 2026-02-04T12:00:00+01:00
researcher: claude
git_commit: 0977c3485bcfea9af544093ec695798fad13c57d
branch: feat/simple-kb
repository: open-webui
topic: "OneDrive KB file upload UI flickering"
tags: [research, codebase, knowledge-base, onedrive, flickering, ui-performance]
status: complete
last_updated: 2026-02-04
last_updated_by: claude
---

# Research: OneDrive KB File Upload UI Flickering

**Date**: 2026-02-04
**Researcher**: claude
**Git Commit**: 0977c34
**Branch**: feat/simple-kb
**Repository**: open-webui

## Research Question

Uploading OneDrive files in Knowledge Bases makes the UI flicker. Can we prevent that? Are we reloading the page unnecessarily or doing unoptimized things?

## Summary

The flickering is caused by three compounding issues in `KnowledgeBase.svelte`:

1. **`getItemsPage()` nulls `fileItems` before the API call** (line 140-141), causing the file list to vanish and show a spinner until the response arrives.
2. **Dual reactive `$:` blocks** (lines 117-135) cause `getItemsPage()` to fire twice per `init()` call -- once from `reset()` changing `currentPage`, once from the `init()` body.
3. **Dual event systems for sync completion** -- both HTTP polling (`pollOneDriveSyncStatus`) and Socket.IO (`handleOneDriveSyncProgress`) call `init()`, leading to double full-refresh on sync completion.

The `feat/data-control` branch already contains five targeted fixes for all of these issues. They are independent of the permissions/data-control features and can be cleanly cherry-picked.

## Detailed Findings

### Root Cause 1: `getItemsPage()` Nulls Display Data

**File:** `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:137-164`

```javascript
const getItemsPage = async () => {
    if (knowledgeId === null) return;
    fileItems = null;       // line 140 -- causes spinner
    fileItemsTotal = null;  // line 141 -- causes spinner
    // ... async API call ...
    const res = await searchKnowledgeFilesById(/*...*/);
    if (res) {
        fileItems = res.items;
        fileItemsTotal = res.total;
    }
};
```

The template at line 1484 branches on `fileItems !== null`:
```svelte
{#if fileItems !== null && fileItemsTotal !== null}
    <!-- file list -->
{:else}
    <Spinner />
{/if}
```

Every `getItemsPage()` call: list disappears -> spinner -> list reappears.

### Root Cause 2: Double `getItemsPage()` From Reactive Blocks

**File:** `KnowledgeBase.svelte:117-135`

Two `$:` blocks with overlapping dependencies:
- Block 1 (line 117): Tracks `knowledgeId, query, viewOption, sortKey, direction, currentPage` -> calls `getItemsPage()`
- Block 2 (line 128): Tracks `query, viewOption, sortKey, direction` -> calls `reset()` which sets `currentPage = 1`

When `init()` calls `reset()`, it changes `currentPage`, which triggers Block 1 to call `getItemsPage()`. Then `init()` itself calls `getItemsPage()` again. Result: two null-then-repopulate cycles.

### Root Cause 3: Dual Sync Completion Handlers

**Polling** (`KnowledgeBase.svelte:588-618`): `pollOneDriveSyncStatus()` polls every 2s, calls `init()` on completion.

**Socket.IO** (`KnowledgeBase.svelte:807-887`): `handleOneDriveSyncProgress` listens for `onedrive:sync:progress`, also calls `init()` on completion.

Both run concurrently. If both detect completion, `init()` fires twice in quick succession -- two flicker cycles back to back.

### Additional Minor Issue: Delete and Content Save

- `deleteFileHandler` (line 917): Calls `init()` after deletion -- full list reset instead of removing the single item.
- `updateFileContentHandler` (line 958): Calls `init()` after saving -- resets to page 1 unnecessarily.

## Fixes on `feat/data-control`

The `feat/data-control` branch has five fixes that address all these issues. The relevant commit is `bb16bd595` and later commits. All changes are in `KnowledgeBase.svelte`:

### Fix 1: `_skipReactiveRefresh` Guard

```javascript
let _skipReactiveRefresh = false;

const init = async () => {
    _skipReactiveRefresh = true;
    currentPage = 1;
    _skipReactiveRefresh = false;
    await getItemsPage();
};

// Reactive block now guarded:
$: if (...) {
    if (!_skipReactiveRefresh) {
        getItemsPage();
    }
}
```

Prevents the reactive block from firing during `init()`, eliminating the double `getItemsPage()` call.

### Fix 2: Remove `fileItems = null` From `getItemsPage()`

```diff
 const getItemsPage = async () => {
     if (knowledgeId === null) return;
-    fileItems = null;
-    fileItemsTotal = null;
```

Old data stays visible until new data replaces it. No more spinner flash.

### Fix 3: `_syncRefreshDone` Flag

```javascript
let _syncRefreshDone = false;
```

- Socket.IO handler sets `_syncRefreshDone = true` before calling `init()`
- Polling handler checks `if (!_syncRefreshDone)` before calling `init()`
- Both handlers reset `_syncRefreshDone = false` at the start of a new sync

Ensures only one of the two systems refreshes the list.

### Fix 4: Optimistic Delete With Rollback

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
            fileItems = previousItems;    // revert
            fileItemsTotal = previousTotal;
        }
    } catch (e) {
        fileItems = previousItems;    // revert
        fileItemsTotal = previousTotal;
    }
};
```

### Fix 5: `getItemsPage()` Instead of `init()` After Content Save

```diff
-               await init();
+               await getItemsPage();
```

No page reset to page 1 after saving file content.

## Code References

- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:137-164` - `getItemsPage()` with null-reset
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:117-135` - Dual reactive blocks
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:588-618` - HTTP polling handler
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:807-887` - Socket.IO sync handler
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:912-920` - Delete handler calling `init()`
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:955-958` - Content save calling `init()`
- `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte:29` - Keyed `{#each}` loop

## Recommendation

Cherry-pick the five flicker fixes from `feat/data-control`. They are isolated state-management changes in `KnowledgeBase.svelte` that are independent of the permissions/data-control features. The changes involve:

1. Adding `_skipReactiveRefresh` flag + guarding the reactive block
2. Removing `fileItems = null; fileItemsTotal = null;` from `getItemsPage()`
3. Adding `_syncRefreshDone` flag for OneDrive sync dedup
4. Optimistic delete with rollback in `deleteFileHandler()`
5. Using `getItemsPage()` instead of `init()` after content saves

These five changes eliminate all identified sources of UI flickering during file uploads and general KB operations.

## Open Questions

- None. The fixes are well-understood and already implemented on `feat/data-control`.
