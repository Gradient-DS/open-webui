# KB Detail View Double-Load Flicker Fix

## Overview

Fix the visible flicker in the Knowledge Base detail view (`KnowledgeBase.svelte`) caused by multiple reactive blocks firing during initialization, each calling `getItemsPage()` which aggressively nulls `fileItems`/`fileItemsTotal`. Apply the same three-fix pattern already proven in the list view (`Knowledge.svelte`).

## Current State Analysis

**Problem:** When opening any KB, the detail view UI loads twice causing visible flicker (spinner → files → spinner → files).

**Root cause:** Three competing reactive `$:` blocks (lines 128-154) all fire during mount:
1. Query watcher (line 128): fires on `query` change with 300ms debounce — but starts immediately since `query = ''`
2. Filter/pagination watcher (line 137): fires when `knowledgeId` is set in `onMount`
3. Reset watcher (line 147): fires on any filter change, mutates `currentPage` which re-triggers block #2

**Amplifier:** `getItemsPage()` (line 156) sets `fileItems = null` and `fileItemsTotal = null` on every call, causing the template (line 1694) to flash the spinner between fetches.

### Key Discoveries:
- `src/lib/components/workspace/Knowledge.svelte:59-73` — List view already has consolidated reactive block pattern
- `src/lib/components/workspace/Knowledge.svelte:91` — List view preserves stale data during re-fetch
- `src/lib/components/workspace/Knowledge.svelte:96-104` — List view uses `fetchId` for deduplication
- None of these patterns exist in the detail view yet

## Desired End State

Opening a KB shows the file list once without any flicker. Re-filtering/searching shows stale data while fetching. Concurrent/stale fetches are discarded.

### Verification:
- Open any KB → file list appears once, no spinner flash
- Type in search → stale results stay visible until new results arrive
- Rapidly change filters → only the last filter's results are shown

## What We're NOT Doing

- Adding a loading overlay/opacity indicator (potential future enhancement)
- Changing the list view (`Knowledge.svelte`) — already fixed
- Modifying the API or backend

## Implementation Approach

All three fixes are applied in a single phase since they're tightly coupled changes in one file (~30 lines changed). The pattern is directly copied from the working list view.

## Phase 1: Apply All Three Fixes

### Overview
Consolidate reactive blocks, preserve stale data, and add fetch deduplication in `KnowledgeBase.svelte`.

### Changes Required:

#### 1. Add state variables
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: After `let fileItemsTotal = null;` (line 116)

Add:
```js
let loaded = false;
let queryDebounceActive = false;
let fetchId = 0;
```

#### 2. Replace three reactive blocks with one consolidated block
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Lines**: 127-154 (replace the query watcher, filter watcher, and reset watcher)

Replace:
```js
// Debounce only query changes
$: if (query !== undefined) {
    clearTimeout(searchDebounceTimer);

    searchDebounceTimer = setTimeout(() => {
        getItemsPage();
    }, 300);
}

// Immediate response to filter/pagination changes
$: if (
    knowledgeId !== null &&
    viewOption !== undefined &&
    sortKey !== undefined &&
    direction !== undefined &&
    currentPage !== undefined
) {
    getItemsPage();
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

With:
```js
// Consolidated reactive block — mirrors Knowledge.svelte list view pattern
$: if (loaded && knowledgeId !== null) {
    // Track all dependencies explicitly
    void query, viewOption, sortKey, direction, currentPage;

    if (queryDebounceActive) {
        // User is typing — debounce
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => {
            reset();
            getItemsPage();
        }, 300);
    } else {
        // Filter/view/pagination change or initial load — fetch immediately
        getItemsPage();
    }
}
```

#### 3. Fix `getItemsPage()` — remove aggressive null reset, add fetch deduplication
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Lines**: 156-185

Replace:
```js
const getItemsPage = async () => {
    if (knowledgeId === null) return;

    fileItems = null;
    fileItemsTotal = null;

    if (sortKey === null) {
        direction = null;
    }

    const isOneDrive = knowledge?.type === 'onedrive';
    const res = await searchKnowledgeFilesById(
        localStorage.token,
        knowledge.id,
        query,
        viewOption,
        sortKey,
        direction,
        currentPage,
        isOneDrive ? 250 : null
    ).catch(() => {
        return null;
    });

    if (res) {
        fileItems = res.items;
        fileItemsTotal = res.total;
    }
    return res;
};
```

With:
```js
const getItemsPage = async () => {
    if (knowledgeId === null) return;

    // Don't null items — keep showing stale data during re-fetch
    const currentFetchId = ++fetchId;

    if (sortKey === null) {
        direction = null;
    }

    const isOneDrive = knowledge?.type === 'onedrive';
    const res = await searchKnowledgeFilesById(
        localStorage.token,
        knowledge.id,
        query,
        viewOption,
        sortKey,
        direction,
        currentPage,
        isOneDrive ? 250 : null
    ).catch(() => {
        return null;
    });

    if (currentFetchId !== fetchId) return; // Stale response, discard

    if (res) {
        fileItems = res.items;
        fileItemsTotal = res.total;
    }
    return res;
};
```

#### 4. Set `loaded = true` at end of `onMount`
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: After the OneDrive auto-sync block, before the drag/drop setup (~line 1278)

Add after line 1278 (after the `} else { goto(...) }` block):
```js
loaded = true;
```

#### 5. Clean up `onDestroy`
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Ensure `searchDebounceTimer` is cleared on destroy (check if already present, add if not):
```js
onDestroy(() => {
    clearTimeout(searchDebounceTimer);
});
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript check passes: `npm run check` (may have pre-existing errors — verify no new errors)
- [x] Build succeeds: `npm run build`
- [x] Frontend lint passes: `npm run lint:frontend`

#### Manual Verification:
- [ ] Open a local KB → file list appears once, no spinner flash
- [ ] Open a OneDrive KB → file list appears once, no spinner flash
- [ ] Type in search box → stale results stay visible, new results replace them after debounce
- [ ] Change sort/filter → results update without flicker
- [ ] Change pagination → results update without flicker
- [ ] Rapidly type then change filter → only final state's results shown (no race conditions)

**Implementation Note**: Single phase — all changes are in one file and interdependent.

---

## Testing Strategy

### Manual Testing Steps:
1. Open any KB and watch for spinner flashes — should show file list exactly once
2. Open browser DevTools Network tab, open a KB — should see exactly 1 search API call on mount (not 2-3)
3. Type "test" in search — should see debounced single API call, stale results visible during fetch
4. Change sort key rapidly 3 times — should see exactly 1 API response applied (last one)

### Edge Cases:
- KB with 0 files — should show empty state once, no flicker
- KB where API returns error — should keep stale data or show error, not flash spinner
- Opening KB while query param `start_onedrive_sync=true` is present — sync should still auto-start

## References

- Research: `thoughts/shared/research/2026-03-24-kb-detail-view-double-load-flicker.md`
- Related plan: `thoughts/shared/plans/2026-03-20-workspace-tab-ui-flicker.md`
- List view reference implementation: `src/lib/components/workspace/Knowledge.svelte:59-120`
