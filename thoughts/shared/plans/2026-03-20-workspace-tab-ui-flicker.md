# Workspace Tab UI Flicker Fix — Implementation Plan

## Overview

Fix the UI flicker (spinner flash / double-loading) in the Knowledge and Prompts workspace tabs. The root cause is competing Svelte `$:` reactive blocks that independently trigger data fetches on mount, causing `init()` to fire twice — the second call resets items to `null`, producing a visible spinner flash.

## Current State Analysis

### Knowledge.svelte (3 competing reactive blocks)
- **Line 56-61**: Debounced query watcher (300ms) — fires on mount even though query hasn't changed
- **Line 68-70**: Immediate `viewOption` watcher — fires when `onMount` sets viewOption from localStorage
- **Line 72-74**: Immediate `typeFilter` watcher (our custom code) — fires on mount
- `init()` calls `reset()` which sets `items = null` → spinner shown → API call → items repopulate

### Prompts.svelte (2 competing reactive blocks)
- **Line 63-69**: Debounced query watcher (300ms) — sets `loading = true` immediately on mount, schedules fetch at +300ms
- **Line 72-74**: Immediate page/tag/view watcher — fires on mount after `loaded = true`
- Result: content appears → 300ms later spinner flash → content reappears

### Models.svelte (reference — no flicker)
- **Line 75-77**: Single reactive block guarded by `loaded` — only fires once `loaded` flips to `true`
- `onMount` sets viewOption and calls `tick()` before setting `loaded = true`
- Clean single-load pattern we should follow

## Desired End State

- Knowledge, Prompts, and Models tabs all load content exactly once on mount with no spinner flash
- Filter/search changes still trigger re-fetches with appropriate debouncing
- During re-fetches, stale data remains visible instead of showing a spinner (except on initial load)
- No regression in existing functionality (pagination, search, filtering, view options)

### How to verify:
1. Navigate between workspace tabs — no flicker or double-load on any tab
2. Use search, filter, and view options — results update correctly
3. Check browser Network tab — only one API call per mount, one per filter change

## What We're NOT Doing

- Migrating to Svelte 5 runes (too much upstream merge risk)
- Fixing the redundant `getModels()` call in `workspace/models/+page.svelte` (no visible impact)
- Changing the Tools tab (already clean, client-side filtering)

## Implementation Approach

Follow the Models.svelte pattern: single reactive block guarded by `loaded`, all state initialized before `loaded = true`, no item nulling during re-fetches.

---

## Phase 1: Fix Knowledge.svelte

### Overview
Consolidate three reactive blocks into one, prevent item nulling during re-fetches, and ensure mount produces exactly one API call.

### Changes Required:

#### 1. Knowledge.svelte — Reactive blocks and init logic
**File**: `src/lib/components/workspace/Knowledge.svelte`

Replace the three separate reactive blocks (lines 56-74) with a single consolidated block:

```svelte
// Replace lines 56-74 with:

// Track whether the user has interacted (typed a query) vs initial mount
let queryDebounceActive = false;

$: if (loaded) {
	// Track all dependencies explicitly
	void viewOption, typeFilter;

	if (queryDebounceActive) {
		// User is typing — debounce
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			init();
		}, 300);
	} else {
		// Filter/view change or initial load — fetch immediately
		init();
	}
}
```

Add a query input handler to set `queryDebounceActive` instead of relying on the reactive block for debouncing. In the search input's `on:input` handler:

```svelte
on:input={(e) => {
	queryDebounceActive = true;
}}
```

And reset it after fetch completes. In `getItemsPage`, after the API call resolves:

```js
queryDebounceActive = false;
```

**Modify `init()` to not null items on re-fetch** (line 76-82, 90-95):

```js
const init = async () => {
	if (!loaded) return;

	page = 1;
	allItemsLoaded = false;
	// Don't null items — keep showing stale data during re-fetch
	// items = null; total = null; — REMOVED
	await getItemsPage(true); // true = replace mode
};
```

**Modify `getItemsPage` to support replace mode** — when called from `init()`, replace items entirely instead of appending:

```js
const getItemsPage = async (replace = false) => {
	itemsLoading = true;
	const res = await searchKnowledgeBases(localStorage.token, query, viewOption, page, typeFilter || null).catch(
		() => { /* existing error handling */ }
	);

	if (res) {
		if (replace || items === null) {
			items = res.items;
		} else {
			items = [...items, ...res.items];
		}
		total = res.total;
		allItemsLoaded = items.length >= total;
	}
	itemsLoading = false;
	queryDebounceActive = false;
};
```

Remove the now-unused `reset()` function (lines 76-82), or keep it only for explicit user actions if needed elsewhere.

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` completes without new errors
- [ ] `npm run check` produces no new errors (existing ~8000 errors are pre-existing)

#### Manual Verification:
- [ ] Navigate to Knowledge tab — content loads once, no spinner flash
- [ ] Type in search box — results update after 300ms debounce, no spinner flash (stale results visible during fetch)
- [ ] Change view option (list/grid) — results update immediately, no flash
- [ ] Change type filter — results update immediately, no flash
- [ ] Scroll down to load more items — pagination still works
- [ ] Browser Network tab shows exactly 1 API call on mount

---

## Phase 2: Fix Prompts.svelte

### Overview
Same pattern as Phase 1: consolidate reactive blocks, prevent loading flash on mount, keep stale data visible during re-fetches.

### Changes Required:

#### 1. Prompts.svelte — Reactive blocks and fetch logic
**File**: `src/lib/components/workspace/Prompts.svelte`

Replace the two reactive blocks (lines 63-74) with a single consolidated block:

```svelte
// Replace lines 63-74 with:

let queryDebounceActive = false;

$: if (loaded) {
	// Track all dependencies
	void page, selectedTag, viewOption;

	if (queryDebounceActive) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			getPromptList();
		}, 300);
	} else {
		getPromptList();
	}
}
```

**Remove the immediate `loading = true`** from the old query reactive block — this was the direct cause of the spinner flash. Instead, only set `loading = true` inside `getPromptList()` where it already is.

Add query input handler:
```svelte
on:input={() => {
	queryDebounceActive = true;
}}
```

Reset `queryDebounceActive = false` in the `finally` block of `getPromptList()` (around line 107).

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` completes without new errors
- [ ] `npm run check` produces no new errors beyond pre-existing ones

#### Manual Verification:
- [ ] Navigate to Prompts tab — content loads once, no spinner flash
- [ ] Type in search box — results update after 300ms debounce
- [ ] Change tag filter — results update immediately
- [ ] Change view option — results update immediately
- [ ] Pagination works correctly
- [ ] Browser Network tab shows exactly 1 API call on mount

---

## Phase 3: Fetch Deduplication (Optional Hardening)

### Overview
Add a simple fetch ID counter to both Knowledge and Prompts to discard stale responses when rapid filter changes cause overlapping API calls.

### Changes Required:

#### 1. Both Knowledge.svelte and Prompts.svelte

Add fetch deduplication to prevent stale responses from overwriting newer data:

```js
let fetchId = 0;

// In the fetch function (getItemsPage / getPromptList):
const currentFetchId = ++fetchId;
// ... await API call ...
if (currentFetchId !== fetchId) return; // Stale response, discard
// ... update state ...
```

This is a defensive measure — Phases 1 and 2 already eliminate the double-fetch on mount. This phase protects against rapid user interactions (e.g., quickly changing filters while a slow API call is in flight).

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` completes without new errors

#### Manual Verification:
- [ ] Rapidly change filters/search — no stale data shown, last filter state wins
- [ ] Slow network simulation (DevTools throttle) — no visual glitches

---

## Testing Strategy

### Manual Testing Steps:
1. Navigate to each workspace tab (Knowledge, Prompts, Models, Tools) — verify single clean load
2. Use search in Knowledge and Prompts — verify debounce works, no flash
3. Change view option in each tab — verify immediate update, no flash
4. Change type filter (Knowledge) and tag filter (Prompts) — verify immediate update
5. Test pagination / infinite scroll in Knowledge — verify load-more still works
6. Open browser DevTools Network tab, navigate between tabs — verify one API call per mount
7. Test with slow network (DevTools → Network → Slow 3G) — verify no spinner flash, stale data shown during refetch

### Regression Checks:
- Knowledge CRUD (create, edit, delete) still works
- Prompt CRUD still works
- OneDrive sync progress updates still appear in Knowledge
- localStorage viewOption persistence works across page refreshes

## References

- Research document: `thoughts/shared/research/2026-03-20-workspace-tab-ui-flicker.md`
- Reference implementation (clean pattern): `src/lib/components/workspace/Models.svelte:75-77`
- Knowledge reactive blocks: `src/lib/components/workspace/Knowledge.svelte:56-74`
- Prompts reactive blocks: `src/lib/components/workspace/Prompts.svelte:63-74`
