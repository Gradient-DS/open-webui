---
date: 2026-03-20T14:00:00+01:00
researcher: Claude
git_commit: c26ae48d6be9d43682b1944f683170a9b7e780a6
branch: merge/upstream-260320
repository: open-webui
topic: 'UI flicker and double-loading in workspace tabs (knowledge, agents, prompts)'
tags:
  [
    research,
    codebase,
    workspace,
    knowledge,
    prompts,
    models,
    tools,
    flicker,
    reactive-statements,
    svelte
  ]
status: complete
last_updated: 2026-03-20
last_updated_by: Claude
---

# Research: UI Flicker and Double-Loading in Workspace Tabs

**Date**: 2026-03-20T14:00:00+01:00
**Researcher**: Claude
**Git Commit**: c26ae48d6be9d43682b1944f683170a9b7e780a6
**Branch**: merge/upstream-260320
**Repository**: open-webui

## Research Question

The workspace tabs (knowledge/kennisbanken, agents, prompts) exhibit UI flicker — content loads, briefly disappears behind a spinner, then reappears. Knowledge appears to always load twice. Is this caused by our custom changes or an upstream Open WebUI bug?

## Summary

**Root cause: Competing reactive statements on mount.** The Knowledge and Prompts components each have multiple `$:` reactive blocks that independently trigger data fetching. On mount, a debounced query watcher (300ms) races with immediate filter/option watchers, causing `init()` to fire twice — the second call resets the item list to `null`, producing a visible spinner flash.

**This is primarily an upstream pattern**, present in both Knowledge and Prompts before our customizations. Our `typeFilter` reactive block in Knowledge adds a third potential trigger but follows the same pattern. The Models and Tools tabs are not affected — Models has a single guarded reactive block, and Tools uses synchronous client-side filtering.

## Detailed Findings

### Knowledge (Kennisbanken) — Double Load

**File:** `src/lib/components/workspace/Knowledge.svelte`

Three reactive `$:` blocks each call `init()`:

| Reactive block | Trigger              | Debounced | Origin            |
| -------------- | -------------------- | --------- | ----------------- |
| Line 56-61     | `query` changes      | 300ms     | Upstream          |
| Line 68-70     | `viewOption` changes | No        | Upstream          |
| Line 72-74     | `typeFilter` changes | No        | **Custom (ours)** |

**Mount sequence:**

1. Variables initialize: `query = ''`, `viewOption = ''`, `typeFilter = ''`, `loaded = false`
2. All three reactive blocks evaluate but `init()` returns early (`loaded === false`)
3. The query block starts a 300ms debounce timer
4. `onMount` sets `viewOption` from localStorage, then sets `loaded = true`
5. If `viewOption` changed → reactive block fires `init()` immediately → **API call #1**
6. 300ms later, debounce timer fires `init()` → calls `reset()` (sets `items = null`) → **API call #2**

**Visible effect:** List appears → spinner flash → list reappears

Even when `viewOption` didn't change (localStorage empty), the 300ms debounce is the sole trigger, adding a noticeable delay before any content appears.

### Prompts — Double Load

**File:** `src/lib/components/workspace/Prompts.svelte`

Two reactive blocks:

| Reactive block | Trigger                             | Debounced                                | Origin   |
| -------------- | ----------------------------------- | ---------------------------------------- | -------- |
| Line 63-69     | `query` changes                     | 300ms, sets `loading = true` immediately | Upstream |
| Line 72-74     | `page`, `selectedTag`, `viewOption` | No                                       | Upstream |

**Mount sequence:**

1. `loaded = true` set synchronously in `onMount` (no async work before it)
2. Block 2 fires immediately → `getPromptList()` → **API call #1** → content renders
3. Block 1 had set `loading = true` synchronously and scheduled `getPromptList()` at +300ms
4. At 300ms, `getPromptList()` fires again → sets `loading = true` → **spinner shown** → API call #2 → content reappears

**Visible effect:** Content appears → 300ms later spinner flash → content reappears

### Models (Agents) — Minor Issue

**File:** `src/lib/components/workspace/Models.svelte`

Single reactive block at line 75-77 fires when `loaded` flips to `true`. No debounce race. However, the page route (`workspace/models/+page.svelte`) also calls `getModels()` in its own `onMount`, redundantly fetching models that were already loaded by the `(app)` layout. This is a wasted API call but doesn't cause visible flicker since it updates the global store asynchronously after the component is already rendering.

**Visible effect:** Single spinner → content (clean transition, no flicker)

### Tools — No Issue

**File:** `src/lib/components/workspace/Tools.svelte`

All filtering is client-side (synchronous). The debounced query reactive block calls `setFilteredItems()` which operates on already-fetched local data. No API calls from reactive statements.

**Visible effect:** Single spinner → content (clean, no flicker)

### Layout Cascade

Three nested layouts (`+layout.svelte` → `(app)/+layout.svelte` → `workspace/+layout.svelte`) each have independent `loaded` flags gating their `<slot />`. This creates a sequential loading chain on initial page load, but layouts persist across SvelteKit client-side navigation, so navigating between workspace tabs does NOT re-trigger layout loading.

## Proposed Fixes

### Fix 1: Consolidate reactive triggers (Knowledge)

Replace the three separate reactive blocks with a single one that watches all dependencies:

```svelte
$: if (loaded) {
    // Reset debounce on any filter change
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
        init();
    }, query ? 300 : 0); // Only debounce when typing
    // Track dependencies explicitly
    void query, viewOption, typeFilter;
}
```

Or better: move all initialization into `onMount` after `loaded = true`, and only use reactive blocks for user-initiated changes (not initial mount).

### Fix 2: Don't reset items on re-fetch (Knowledge + Prompts)

Instead of calling `reset()` (which sets `items = null` and shows the spinner), keep the existing items visible while the new data loads:

```js
const init = async () => {
	if (!loaded) return;
	// Don't reset items — keep showing stale data during fetch
	page = 1;
	await getItemsPage(true); // true = replace items instead of append
};
```

### Fix 3: Add fetch deduplication

Use a simple counter or AbortController to discard stale responses:

```js
let fetchId = 0;
const init = async () => {
    if (!loaded) return;
    const currentFetchId = ++fetchId;
    page = 1;
    const result = await searchKnowledgeBases(...);
    if (currentFetchId !== fetchId) return; // Stale response
    items = result.items;
    total = result.total;
};
```

### Fix 4: Skip debounce on mount (Prompts)

Don't start the debounce timer during initial reactive evaluation:

```js
let mounted = false;
$: if (query !== undefined && mounted) {
	loading = true;
	clearTimeout(searchDebounceTimer);
	searchDebounceTimer = setTimeout(() => {
		getPromptList();
	}, 300);
}
onMount(() => {
	mounted = true; /* ... */
});
```

## Code References

- `src/lib/components/workspace/Knowledge.svelte:56-74` — Three competing reactive blocks
- `src/lib/components/workspace/Knowledge.svelte:90-95` — `init()` with `loaded` guard
- `src/lib/components/workspace/Knowledge.svelte:178-183` — `onMount` setting viewOption + loaded
- `src/lib/components/workspace/Prompts.svelte:63-74` — Two competing reactive blocks
- `src/lib/components/workspace/Prompts.svelte:175-177` — `onMount` with immediate `loaded = true`
- `src/lib/components/workspace/Models.svelte:75-77` — Single guarded reactive block (no flicker)
- `src/lib/components/workspace/Tools.svelte:66-75` — Client-side filtering (no API calls)
- `src/routes/(app)/workspace/models/+page.svelte:9-24` — Redundant `getModels()` call

## Architecture Insights

The workspace components use Svelte 4's `$:` reactive statements rather than Svelte 5 runes. The `$:` semantics of running on first evaluation _and_ on dependency changes create subtle initialization races when multiple blocks watch overlapping state. This is a well-known Svelte 4 footgun that Svelte 5's explicit `$effect` with cleanup/untrack would handle better.

The pattern of "restore from localStorage in onMount → trigger reactive blocks → race with debounce timer" appears in both upstream and custom code. A consistent fix would be to initialize all state (including localStorage reads) _before_ setting `loaded = true`, and have a single reactive block that only fires after `loaded`.

## Open Questions

- Should we fix this only for Knowledge (our custom code) or also patch the upstream Prompts flicker?
- Would migrating these components to Svelte 5 runes be worthwhile given the upstream merge complexity?
- Is the redundant `getModels()` call in the Models page route intentional (to refresh after navigation)?
