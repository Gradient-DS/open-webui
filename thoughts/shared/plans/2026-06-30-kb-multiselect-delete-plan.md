# KB Multiselect Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users select multiple rows in a Knowledge Base (local + cloud) and remove them in one confirmed action, batching each row's existing removal (file-remove or remove-source).

**Architecture:** A reusable, store-based selection model (`selection.ts`) holds the selected items keyed `file:<id>` / `source:<itemId>`, each carrying the exact payload its row's `✕` already uses. Three list views (`Files`, `SourceGroupedFiles`, `LazyKnowledgeTree`/`LazyTreeNode`) render a shared `SelectCheckbox` on selectable rows and route clicks into the model. The parent `KnowledgeBase.svelte` owns the model instance, renders a shared `KbBulkActionBar` + one confirm dialog, and a single `bulkRemoveHandler` replays each item's removal (mirroring `deleteFileHandler` / `removeCloudSourceHandler`) without per-item toast/init, then refreshes once.

**Tech Stack:** SvelteKit 5 (legacy `export let` + `$:` + `svelte/store` `writable`, matching this repo — NO runes/`.svelte.ts`), TypeScript, Vitest (node env, no jsdom), Tailwind.

## Global Constraints

- **Spec:** `thoughts/shared/plans/2026-06-30-kb-multiselect-delete-design.md`. Read it before starting.
- **Additive only.** Do not change existing `onClick` / `onDelete` / `onRemoveSource` props or handlers. New behavior is gated on a new optional `selection` prop (default `null`) so every view is behavior-neutral until the parent passes it.
- **Selectable rows = rows that already have a `✕` today:** plain files (→ file-remove) and synced source roots (→ remove-source). Files nested inside a synced folder and mid-upload rows (`status === 'uploading'` / no `id`) are NOT selectable.
- **No new backend endpoint.** Bulk delete loops the existing client APIs: `removeFileFromKnowledgeById(token, kbId, fileId)` (from `$lib/apis/knowledge`) and `provider.api.removeSource(token, kbId, itemId)` (a `createSyncApi` instance from `$lib/apis/sync`).
- **State pattern:** plain factory returning `svelte/store` `writable`s. There is NO `.svelte.ts` rune precedent in this repo — do not introduce one.
- **Tests run in node env** (`vite.config.ts` sets no `environment`; no jsdom). Unit-test only the pure/store model. Verify `.svelte` changes via `npm run build` (compiles in ~60s) — do NOT rely on `npm run check` for a clean exit (there are ~8000 pre-existing svelte-check errors; only ensure you add no NEW errors in files you touch).
- **i18n:** every new user-facing string must exist in BOTH `src/lib/i18n/locales/en-US/translation.json` and `src/lib/i18n/locales/nl-NL/translation.json`, inserted in alphabetical key order. Empty string in en-US means "use the key itself".
- **Commit message footer:** end every commit body with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## Task 0: Branch setup

**Files:** none (git only).

- [ ] **Step 1: Create the feature branch off `dev`**

Run:
```bash
cd /Users/lexlubbers/Code/soev/open-webui
git checkout dev && git pull --ff-only
git checkout -b feat/kb-multiselect-delete
```
Expected: `Switched to a new branch 'feat/kb-multiselect-delete'`.

- [x] **Step 2: Commit the design + plan docs (already on disk)**

```bash
git add thoughts/shared/plans/2026-06-30-kb-multiselect-delete-design.md thoughts/shared/plans/2026-06-30-kb-multiselect-delete-plan.md
git commit -m "docs(kb): multiselect delete design + implementation plan"
```

---

## Task 1: Selection model (`selection.ts`) + unit tests

The reusable, testable core. Keyed by `file:<id>` / `source:<itemId>` so it works through `VirtualList` (which exposes no row index) and so file/source payloads never collide.

**Files:**
- Create: `src/lib/components/workspace/Knowledge/KnowledgeBase/selection.ts`
- Test: `src/lib/components/workspace/Knowledge/KnowledgeBase/selection.test.ts`

**Interfaces:**
- Produces:
  - `type SelectableItem` (discriminated union on `kind`)
  - `fileItem(fileId: string, label: string): SelectableItem`
  - `sourceItem(itemId: string, label: string): SelectableItem`
  - `createKbSelection(): KbSelection` where `KbSelection` exposes stores `selected: Readable<Map<string, SelectableItem>>`, `count: Readable<number>`, `breakdown: Readable<{ files: number; sources: number }>`, `selectionMode: Readable<boolean>`, and methods `toggle(item)`, `select(item, orderedItems, e)`, `selectAll(items)`, `clear()`, `enterSelectionMode()`.

- [x] **Step 1: Write the failing test**

Create `src/lib/components/workspace/Knowledge/KnowledgeBase/selection.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import { createKbSelection, fileItem, sourceItem } from './selection';

const f = (id: string) => fileItem(id, `file-${id}`);
const s = (id: string) => sourceItem(id, `src-${id}`);

describe('createKbSelection', () => {
	it('toggle adds then removes an item and updates count', () => {
		const sel = createKbSelection();
		sel.toggle(f('a'));
		expect(get(sel.count)).toBe(1);
		expect(get(sel.selected).has('file:a')).toBe(true);
		sel.toggle(f('a'));
		expect(get(sel.count)).toBe(0);
	});

	it('select with ctrl/meta toggles a single item', () => {
		const sel = createKbSelection();
		const items = [f('a'), f('b'), f('c')];
		sel.select(f('b'), items, { ctrlKey: true });
		expect([...get(sel.selected).keys()]).toEqual(['file:b']);
		sel.select(f('b'), items, { metaKey: true });
		expect(get(sel.count)).toBe(0);
	});

	it('select with shift selects a contiguous range over orderedItems', () => {
		const sel = createKbSelection();
		const items = [f('a'), f('b'), f('c'), f('d')];
		sel.select(f('a'), items, {}); // anchor (plain toggle)
		sel.select(f('c'), items, { shiftKey: true });
		expect([...get(sel.selected).keys()]).toEqual(['file:a', 'file:b', 'file:c']);
	});

	it('shift with no prior anchor falls back to single toggle', () => {
		const sel = createKbSelection();
		const items = [f('a'), f('b')];
		sel.select(f('b'), items, { shiftKey: true });
		expect([...get(sel.selected).keys()]).toEqual(['file:b']);
	});

	it('selectAll selects every provided item', () => {
		const sel = createKbSelection();
		sel.selectAll([f('a'), s('x'), f('b')]);
		expect(get(sel.count)).toBe(3);
	});

	it('breakdown counts files vs sources', () => {
		const sel = createKbSelection();
		sel.selectAll([f('a'), s('x'), s('y')]);
		expect(get(sel.breakdown)).toEqual({ files: 1, sources: 2 });
	});

	it('clear empties selection and resets selection mode', () => {
		const sel = createKbSelection();
		sel.selectAll([f('a')]);
		sel.enterSelectionMode();
		expect(get(sel.selectionMode)).toBe(true);
		sel.clear();
		expect(get(sel.count)).toBe(0);
		expect(get(sel.selectionMode)).toBe(false);
	});

	it('file and source helpers build collision-free keys + payloads', () => {
		expect(fileItem('1', 'A')).toEqual({ key: 'file:1', label: 'A', kind: 'file', fileId: '1' });
		expect(sourceItem('1', 'A')).toEqual({
			key: 'source:1',
			label: 'A',
			kind: 'source',
			itemId: '1',
			sourceName: 'A'
		});
	});
});
```

- [x] **Step 2: Run the test to verify it fails**

Run: `npm run test:frontend -- src/lib/components/workspace/Knowledge/KnowledgeBase/selection.test.ts`
Expected: FAIL — cannot resolve `./selection` (module does not exist yet).

- [x] **Step 3: Write the implementation**

Create `src/lib/components/workspace/Knowledge/KnowledgeBase/selection.ts`:

```ts
import { writable, derived, type Readable } from 'svelte/store';

export type SelectableItem =
	| { key: string; label: string; kind: 'file'; fileId: string }
	| { key: string; label: string; kind: 'source'; itemId: string; sourceName: string };

export const fileItem = (fileId: string, label: string): SelectableItem => ({
	key: `file:${fileId}`,
	label,
	kind: 'file',
	fileId
});

export const sourceItem = (itemId: string, label: string): SelectableItem => ({
	key: `source:${itemId}`,
	label,
	kind: 'source',
	itemId,
	sourceName: label
});

type ClickModifiers = { shiftKey?: boolean; metaKey?: boolean; ctrlKey?: boolean };

export interface KbSelection {
	selected: Readable<Map<string, SelectableItem>>;
	count: Readable<number>;
	breakdown: Readable<{ files: number; sources: number }>;
	selectionMode: Readable<boolean>;
	toggle: (item: SelectableItem) => void;
	select: (item: SelectableItem, orderedItems: SelectableItem[], e: ClickModifiers) => void;
	selectAll: (items: SelectableItem[]) => void;
	clear: () => void;
	enterSelectionMode: () => void;
}

const rangeBetween = (
	orderedItems: SelectableItem[],
	fromKey: string,
	toKey: string
): SelectableItem[] => {
	const fromIdx = orderedItems.findIndex((i) => i.key === fromKey);
	const toIdx = orderedItems.findIndex((i) => i.key === toKey);
	if (fromIdx === -1 || toIdx === -1) return [];
	const [start, end] = fromIdx <= toIdx ? [fromIdx, toIdx] : [toIdx, fromIdx];
	return orderedItems.slice(start, end + 1);
};

export function createKbSelection(): KbSelection {
	const _selected = writable<Map<string, SelectableItem>>(new Map());
	const _mode = writable(false);
	let lastKey: string | null = null;

	const toggle = (item: SelectableItem) => {
		_selected.update((map) => {
			const m = new Map(map);
			if (m.has(item.key)) m.delete(item.key);
			else m.set(item.key, item);
			return m;
		});
		lastKey = item.key;
	};

	const select = (item: SelectableItem, orderedItems: SelectableItem[], e: ClickModifiers) => {
		if (e.shiftKey && lastKey) {
			const range = rangeBetween(orderedItems, lastKey, item.key);
			if (range.length) {
				_selected.set(new Map(range.map((it) => [it.key, it])));
				lastKey = item.key;
				return;
			}
		}
		// ctrl/meta or plain (selection-mode) → toggle one
		toggle(item);
	};

	const selectAll = (items: SelectableItem[]) => {
		_selected.set(new Map(items.map((it) => [it.key, it])));
		lastKey = items.length ? items[items.length - 1].key : null;
	};

	const clear = () => {
		_selected.set(new Map());
		lastKey = null;
		_mode.set(false);
	};

	const enterSelectionMode = () => _mode.set(true);

	const count = derived(_selected, (m) => m.size);
	const breakdown = derived(_selected, (m) => {
		let files = 0;
		let sources = 0;
		for (const it of m.values()) {
			if (it.kind === 'file') files++;
			else sources++;
		}
		return { files, sources };
	});

	return {
		selected: { subscribe: _selected.subscribe },
		count,
		breakdown,
		selectionMode: { subscribe: _mode.subscribe },
		toggle,
		select,
		selectAll,
		clear,
		enterSelectionMode
	};
}
```

- [x] **Step 4: Run the test to verify it passes**

Run: `npm run test:frontend -- src/lib/components/workspace/Knowledge/KnowledgeBase/selection.test.ts`
Expected: PASS (8 tests).

- [x] **Step 5: Commit**

```bash
git add src/lib/components/workspace/Knowledge/KnowledgeBase/selection.ts src/lib/components/workspace/Knowledge/KnowledgeBase/selection.test.ts
git commit -m "feat(kb): reusable selection model for multiselect delete"
```

---

## Task 2: Shared presentational components (`SelectCheckbox`, `KbBulkActionBar`)

Two small, dependency-light components reused by every view and the parent. No unit tests (node test env can't render Svelte) — verified by `npm run build`.

**Files:**
- Create: `src/lib/components/workspace/Knowledge/KnowledgeBase/SelectCheckbox.svelte`
- Create: `src/lib/components/workspace/Knowledge/KnowledgeBase/KbBulkActionBar.svelte`

**Interfaces:**
- Produces:
  - `SelectCheckbox` props: `selected: boolean`, `visible: boolean` (force-visible in selection mode), `onToggle: () => void`.
  - `KbBulkActionBar` props: `count: number`, `onDelete: () => void`, `onClear: () => void`, `onSelectAll: (() => void) | null` (Select-All button rendered only when non-null).

- [x] **Step 1: Create `SelectCheckbox.svelte`**

The checkbox cell. Hover-reveal via `group-hover` (the row container must have the `group` class — added in later tasks); always visible when selected or in selection mode. Check glyph copied from `FileNav/FileEntryRow.svelte`.

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	export let selected = false;
	export let visible = false; // selection-mode (touch) forces the box visible
	export let onToggle: () => void = () => {};
</script>

<div
	class="flex items-center transition-opacity {selected || visible
		? 'opacity-100'
		: 'opacity-0 group-hover:opacity-100'}"
>
	<button
		type="button"
		class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
		on:click|stopPropagation={onToggle}
		aria-label={$i18n.t('Select')}
	>
		<div
			class="size-3.5 shrink-0 rounded border flex items-center justify-center transition-colors {selected
				? 'bg-blue-500 dark:bg-blue-600 border-blue-500 dark:border-blue-600 text-white'
				: 'border-gray-300 dark:border-gray-600'}"
		>
			{#if selected}
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-2.5">
					<path
						fill-rule="evenodd"
						d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
						clip-rule="evenodd"
					/>
				</svg>
			{/if}
		</div>
	</button>
</div>
```

- [x] **Step 2: Create `KbBulkActionBar.svelte`**

Copy of `chat/FileNav/BulkActionBar.svelte` with the Download button removed and Select-All made optional. Reuses existing i18n keys `'{{count}} selected'`, `'Select All'`, `'Delete'`, `'Deselect'`.

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	export let count: number = 0;
	export let onDelete: () => void = () => {};
	export let onClear: () => void = () => {};
	export let onSelectAll: (() => void) | null = null;
</script>

<div class="flex items-center gap-2 px-3 py-1.5 bg-gray-50 dark:bg-gray-800/50 rounded-lg shrink-0">
	<span class="text-xs font-medium text-gray-600 dark:text-gray-400 flex-1 truncate">
		{$i18n.t('{{count}} selected', { count })}
	</span>

	{#if onSelectAll}
		<Tooltip content={$i18n.t('Select All')}>
			<button
				class="p-1 rounded transition text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-400"
				on:click={onSelectAll}
				aria-label={$i18n.t('Select All')}
			>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
					<path
						fill-rule="evenodd"
						d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
						clip-rule="evenodd"
					/>
				</svg>
			</button>
		</Tooltip>
	{/if}

	<Tooltip content={$i18n.t('Delete')}>
		<button
			class="p-1 rounded transition text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-400"
			on:click={onDelete}
			aria-label={$i18n.t('Delete')}
		>
			<GarbageBin className="size-3.5" />
		</button>
	</Tooltip>

	<Tooltip content={$i18n.t('Deselect')}>
		<button
			class="p-1 rounded transition text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-400"
			on:click={onClear}
			aria-label={$i18n.t('Deselect')}
		>
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
				<path
					d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"
				/>
			</svg>
		</button>
	</Tooltip>
</div>
```

- [x] **Step 3: Verify it builds**

Run: `npm run build`
Expected: build completes (exit 0). The two new components compile.

- [x] **Step 4: Commit**

```bash
git add src/lib/components/workspace/Knowledge/KnowledgeBase/SelectCheckbox.svelte src/lib/components/workspace/Knowledge/KnowledgeBase/KbBulkActionBar.svelte
git commit -m "feat(kb): SelectCheckbox + KbBulkActionBar presentational components"
```

---

## Task 3: Multiselect in `Files.svelte` (local KB flat list)

Add an optional `selection` prop. When set, render a `SelectCheckbox` on each real-file row, intercept modifier-clicks on the row body, and tint selected rows. Behavior-neutral when `selection` is `null`.

**Files:**
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte`

**Interfaces:**
- Consumes: `KbSelection`, `fileItem` from `./selection`; `SelectCheckbox` from `./SelectCheckbox.svelte`.

- [x] **Step 1: Extend the `<script>` block**

In `Files.svelte`, after the existing imports/exports (current lines 20–27), add the new import lines and selection props/helpers. Replace the block:

```svelte
	import VirtualList from '@sveltejs/svelte-virtual-list';

	export let knowledge = null;
	export let selectedFileId = null;
	export let files = [];

	export let onClick = (fileId) => {};
	export let onDelete = (fileId) => {};
</script>
```

with:

```svelte
	import VirtualList from '@sveltejs/svelte-virtual-list';
	import SelectCheckbox from './SelectCheckbox.svelte';
	import { fileItem, type KbSelection, type SelectableItem } from './selection';

	export let knowledge = null;
	export let selectedFileId = null;
	export let files = [];

	export let onClick = (fileId) => {};
	export let onDelete = (fileId) => {};

	// Optional multiselect model injected by KnowledgeBase. Null = no selection UI.
	export let selection: KbSelection | null = null;

	$: selectedStore = selection?.selected;
	$: selectionModeStore = selection?.selectionMode;

	const isSelectable = (file: any) => !!file?.id && file?.status !== 'uploading';
	const buildItem = (file: any): SelectableItem =>
		fileItem(file.id, file?.name ?? file?.meta?.name ?? '');

	$: orderedItems = (files ?? []).filter(isSelectable).map(buildItem);

	const onRowClick = (file: any, e: MouseEvent) => {
		if (selection && isSelectable(file) && (e.metaKey || e.ctrlKey || e.shiftKey)) {
			e.preventDefault();
			selection.select(buildItem(file), orderedItems, e);
			return;
		}
		if (selection && isSelectable(file) && selectionModeStore && $selectionModeStore) {
			selection.select(buildItem(file), orderedItems, e);
			return;
		}
		onClick(file?.id ?? file?.tempId);
	};
</script>
```

- [x] **Step 2: Add the `group` class + selected tint to the row container, and insert the checkbox**

Replace the row container opening + the "Open file" block start (current lines 38–43):

```svelte
		<div
			class=" flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selectedFileId
				? ''
				: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
		>
			<div class="flex items-center">
```

with (adds `group`, a selected-row tint, and the `SelectCheckbox` as the first child):

```svelte
		{@const selKey = `file:${file?.id}`}
		{@const isSel = (selection && isSelectable(file) && $selectedStore?.has(selKey)) ?? false}
		<div
			class=" group flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {isSel
				? 'bg-blue-50 dark:bg-blue-900/20'
				: selectedFileId
					? ''
					: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
		>
			{#if selection && isSelectable(file)}
				<SelectCheckbox
					selected={isSel}
					visible={!!$selectionModeStore}
					onToggle={() => selection.toggle(buildItem(file))}
				/>
			{/if}
			<div class="flex items-center">
```

- [x] **Step 3: Route the row-body click through `onRowClick`**

Replace the row-body button's handler (current lines 62–69):

```svelte
			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={async () => {
					console.log(file);
					onClick(file?.id ?? file?.tempId);
				}}
			>
```

with:

```svelte
			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={(e) => onRowClick(file, e)}
			>
```

- [x] **Step 4: Verify it builds**

Run: `npm run build`
Expected: build completes (exit 0).

- [x] **Step 5: Commit**

```bash
git add src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte
git commit -m "feat(kb): multiselect checkboxes in local file list (Files.svelte)"
```

---

## Task 4: Multiselect in `SourceGroupedFiles.svelte` (cloud grouped view)

Selectable rows here: **loose files** (which route to file-remove OR source-remove exactly like their `✕`) and **source-folder header rows** (→ source-remove). Files nested inside folders stay non-selectable.

**Files:**
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/SourceGroupedFiles.svelte`

**Interfaces:**
- Consumes: `KbSelection`, `fileItem`, `sourceItem` from `./selection`; `SelectCheckbox`.

- [x] **Step 1: Extend the `<script>` block**

After the existing imports (current line 25 `import FolderTreeNode ...`) add:

```svelte
	import FolderTreeNode from './FolderTreeNode.svelte';
	import SelectCheckbox from './SelectCheckbox.svelte';
	import { fileItem, sourceItem, type KbSelection, type SelectableItem } from './selection';
```

After the existing `export let onDelete` (current line 39), add the selection prop + helpers:

```svelte
	export let onDelete: (fileId: string) => void = () => {};

	// Optional multiselect model injected by KnowledgeBase. Null = no selection UI.
	export let selection: KbSelection | null = null;

	$: selectedStore = selection?.selected;
	$: selectionModeStore = selection?.selectionMode;

	// Mirror the per-row ✕ routing (lines ~339-351): cloud-provider loose files
	// remove via their source; everything else is a plain file delete.
	const looseItem = (file: any): SelectableItem => {
		const cloudSource =
			file?.meta?.source === 'onedrive' ||
			file?.meta?.source === 'google_drive' ||
			file?.meta?.source === 'confluence';
		if (cloudSource && file?.meta?.source_item_id) {
			return sourceItem(file.meta.source_item_id, file?.name ?? file?.meta?.name ?? '');
		}
		return fileItem(file?.id ?? file?.tempId, file?.name ?? file?.meta?.name ?? '');
	};
	const isLooseSelectable = (file: any) =>
		(!!file?.id || !!file?.meta?.source_item_id) && file?.status !== 'uploading';

	$: looseOrdered = (looseFiles ?? []).filter(isLooseSelectable).map(looseItem);
	$: sourceOrdered = (sources ?? []).filter(isFolderLikeSource).map((s: any) =>
		sourceItem(s.item_id, s.name)
	);

	const onLooseClick = (file: any, e: MouseEvent) => {
		if (selection && isLooseSelectable(file) && (e.metaKey || e.ctrlKey || e.shiftKey)) {
			e.preventDefault();
			selection.select(looseItem(file), looseOrdered, e);
			return;
		}
		if (selection && isLooseSelectable(file) && $selectionModeStore) {
			selection.select(looseItem(file), looseOrdered, e);
			return;
		}
		onClick(file?.id ?? file?.tempId);
	};
```

> Note: `looseFiles`, `sources`, and `isFolderLikeSource` already exist in this component (see the `isFolderLikeSource` helper at ~lines 94-98 and the `looseFiles` `{#each}` at ~line 273). If `looseFiles` is a derived `$:` already, reuse it; do not redeclare.

- [x] **Step 2: Source-header rows — add `group`, checkbox, selected tint**

Open the file and find the source-header row block (the `{#each sources ...}` header that renders the Remove-Source `✕` calling `onRemoveSource(source.item_id, source.name)`, around lines 163-204). On that header row's outer container `<div>`: add the `group` class, and as its first child insert:

```svelte
				{@const srcKey = `source:${source.item_id}`}
				{@const srcSel = (selection && $selectedStore?.has(srcKey)) ?? false}
				{#if selection && knowledge?.write_access && isFolderLikeSource(source)}
					<SelectCheckbox
						selected={srcSel}
						visible={!!$selectionModeStore}
						onToggle={() => selection.toggle(sourceItem(source.item_id, source.name))}
					/>
				{/if}
```

Add the selected tint to that header container by appending `{srcSel ? 'bg-blue-50 dark:bg-blue-900/20' : ''}` to its class list, and ensure the container has `group`. (The header's expand/collapse click handler is unchanged — selection of a source is via its checkbox or, optionally, modifier-click; for v1 the header checkbox is sufficient and we do NOT intercept the header's expand click.)

- [x] **Step 3: Loose-file rows — add `group`, checkbox, modifier click, tint**

Replace the loose-file row container open + body button (current lines 275–283):

```svelte
	<div
		class="flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selectedFileId
			? ''
			: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
	>
		<button
			class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
			type="button"
			on:click={() => onClick(file?.id ?? file?.tempId)}
		>
```

with:

```svelte
	{@const looseKey = looseItem(file).key}
	{@const looseSel = (selection && isLooseSelectable(file) && $selectedStore?.has(looseKey)) ?? false}
	<div
		class="group flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {looseSel
			? 'bg-blue-50 dark:bg-blue-900/20'
			: selectedFileId
				? ''
				: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
	>
		{#if selection && isLooseSelectable(file)}
			<SelectCheckbox
				selected={looseSel}
				visible={!!$selectionModeStore}
				onToggle={() => selection.toggle(looseItem(file))}
			/>
		{/if}
		<button
			class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
			type="button"
			on:click={(e) => onLooseClick(file, e)}
		>
```

- [x] **Step 4: Verify it builds**

Run: `npm run build`
Expected: build completes (exit 0).

- [x] **Step 5: Commit**

```bash
git add src/lib/components/workspace/Knowledge/KnowledgeBase/SourceGroupedFiles.svelte
git commit -m "feat(kb): multiselect for loose files + source headers (SourceGroupedFiles.svelte)"
```

---

## Task 5: Multiselect in the lazy tree (`LazyKnowledgeTree.svelte` + `LazyTreeNode.svelte`)

Selectable rows: **root-level loose files** (in `LazyKnowledgeTree`, → file-remove) and **top-level source nodes** (`LazyTreeNode` with `isSource === true`, → source-remove via `node.path` as the item id). Nested folders/files are not selectable. Source rows support checkbox + Cmd/Ctrl-toggle (no Shift-range across the tree).

**Files:**
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/LazyKnowledgeTree.svelte`
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/LazyTreeNode.svelte`

**Interfaces:**
- Consumes: `KbSelection`, `fileItem`, `sourceItem`, `SelectCheckbox`.

- [x] **Step 1: `LazyKnowledgeTree.svelte` — add `selection` prop + helpers**

After the existing `export let onRemoveSource` (current line 33) add:

```svelte
	export let onRemoveSource: (itemId: string, name: string) => void = () => {};

	import SelectCheckbox from './SelectCheckbox.svelte';
	import { fileItem, type KbSelection, type SelectableItem } from './selection';

	export let selection: KbSelection | null = null;

	$: selectedStore = selection?.selected;
	$: selectionModeStore = selection?.selectionMode;

	const isFileSelectable = (file: any) => !!file?.id && file?.status !== 'uploading';
	const buildFileItem = (file: any): SelectableItem => fileItem(file.id, file?.name ?? '');
	$: rootFileOrdered = (rootFiles ?? []).filter(isFileSelectable).map(buildFileItem);

	const onRootFileClick = (file: any, e: MouseEvent) => {
		if (selection && isFileSelectable(file) && (e.metaKey || e.ctrlKey || e.shiftKey)) {
			e.preventDefault();
			selection.select(buildFileItem(file), rootFileOrdered, e);
			return;
		}
		if (selection && isFileSelectable(file) && $selectionModeStore) {
			selection.select(buildFileItem(file), rootFileOrdered, e);
			return;
		}
		onClick(file);
	};
```

> Place the `import` lines with the component's other imports at the top of the `<script>` if the linter prefers; functionally Svelte hoists imports. `rootFiles` already exists in this component (the root-files `{#each rootFiles ...}`).

- [x] **Step 2: `LazyKnowledgeTree.svelte` — root file rows: `group`, checkbox, click, tint**

Replace the root-file row container + body button (current lines 207–216):

```svelte
		<div
			class="flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selectedFileId
				? ''
				: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
		>
			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={() => onClick(file)}
			>
```

with:

```svelte
		{@const rfKey = `file:${file?.id}`}
		{@const rfSel = (selection && isFileSelectable(file) && $selectedStore?.has(rfKey)) ?? false}
		<div
			class="group flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {rfSel
				? 'bg-blue-50 dark:bg-blue-900/20'
				: selectedFileId
					? ''
					: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
		>
			{#if selection && isFileSelectable(file)}
				<SelectCheckbox
					selected={rfSel}
					visible={!!$selectionModeStore}
					onToggle={() => selection.toggle(buildFileItem(file))}
				/>
			{/if}
			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={(e) => onRootFileClick(file, e)}
			>
```

- [x] **Step 3: `LazyKnowledgeTree.svelte` — thread `selection` into `LazyTreeNode`**

Replace the `LazyTreeNode` mount (current lines 189–201) to also pass `{selection}`:

```svelte
		<LazyTreeNode
			node={source}
			isSource={true}
			{isSyncing}
			{knowledge}
			{expanded}
			{nodeCache}
			{loadingPaths}
			{toggle}
			{loadMore}
			{onClick}
			{onRemoveSource}
			{selection}
		/>
```

- [x] **Step 4: `LazyTreeNode.svelte` — add `selection` prop + source-node helper**

After the existing `export let onRemoveSource` (current line 42) add:

```svelte
	export let onRemoveSource: (itemId: string, name: string) => void = () => {};

	import SelectCheckbox from './SelectCheckbox.svelte';
	import { sourceItem, type KbSelection } from './selection';

	export let selection: KbSelection | null = null;

	$: selectedStore = selection?.selected;
	$: selectionModeStore = selection?.selectionMode;
```

> Keep the `import` near the file's other imports if the linter complains about import placement.

- [x] **Step 5: `LazyTreeNode.svelte` — source-node checkbox**

The source/folder header row is gated by `isSource`. Add a checkbox for source nodes. Immediately inside the source row's flex container (the same row that holds the Remove-Source `✕` at lines 92-104), as the FIRST child, insert:

```svelte
				{@const nodeKey = `source:${node.path}`}
				{@const nodeSel = (selection && isSource && $selectedStore?.has(nodeKey)) ?? false}
				{#if selection && isSource && knowledge?.write_access}
					<SelectCheckbox
						selected={nodeSel}
						visible={!!$selectionModeStore}
						onToggle={() => selection.toggle(sourceItem(node.path, node.name))}
					/>
				{/if}
```

Ensure the source row's container `<div>` has the `group` class (so the checkbox hover-reveal works) and append the selected tint `{nodeSel ? 'bg-blue-50 dark:bg-blue-900/20' : ''}` to its class list. Do NOT intercept the folder's expand/collapse click — source selection is via the checkbox.

- [x] **Step 6: `LazyTreeNode.svelte` — forward `selection` in the recursive `<svelte:self>`**

Replace the recursive mount (current lines 118–129) to pass `{selection}`:

```svelte
					<svelte:self
						node={child}
						isSource={false}
						{knowledge}
						{expanded}
						{nodeCache}
						{loadingPaths}
						{toggle}
						{loadMore}
						{onClick}
						{onRemoveSource}
						{selection}
					/>
```

(Nested nodes have `isSource={false}`, so they render no checkbox — forwarding `selection` is harmless and keeps the recursion uniform.)

- [x] **Step 7: Verify it builds**

Run: `npm run build`
Expected: build completes (exit 0).

- [x] **Step 8: Commit**

```bash
git add src/lib/components/workspace/Knowledge/KnowledgeBase/LazyKnowledgeTree.svelte src/lib/components/workspace/Knowledge/KnowledgeBase/LazyTreeNode.svelte
git commit -m "feat(kb): multiselect for root files + source nodes (lazy tree)"
```

---

## Task 6: Parent wiring in `KnowledgeBase.svelte` — light up the feature

Instantiate the model, pass it to the three editable views, render the bulk bar + confirm dialog, implement the dispatch handler, and add i18n.

**Files:**
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- Modify: `src/lib/i18n/locales/en-US/translation.json`
- Modify: `src/lib/i18n/locales/nl-NL/translation.json`

**Interfaces:**
- Consumes: `createKbSelection` from `./KnowledgeBase/selection`; `KbBulkActionBar` from `./KnowledgeBase/KbBulkActionBar.svelte`; existing `removeFileFromKnowledgeById` (already imported, line 35), `getKnowledgeById` (already imported, line 34), `activeProvider.api.removeSource`, `init()`.

- [x] **Step 1: Imports + model instance**

Add imports next to the existing list-view imports (after line 69):

```svelte
	import LazyKnowledgeSearch from './KnowledgeBase/LazyKnowledgeSearch.svelte';
	import KbBulkActionBar from './KnowledgeBase/KbBulkActionBar.svelte';
	import { createKbSelection, fileItem } from './KnowledgeBase/selection';
```

Near the other state declarations (after line 290 `let treeRefresh = 0;`), add:

```svelte
	const selection = createKbSelection();
	const { count: bulkCount, breakdown: bulkBreakdown } = selection;
	let showBulkRemoveConfirm = false;
```

- [x] **Step 2: The dispatch handler `bulkRemoveHandler`**

Add next to `deleteFileHandler` (after line 1589). It reads the selected items, replays each removal without per-item toast/init, refreshes `knowledge` meta once if any source was removed, then one `init()`:

```svelte
	const bulkRemoveHandler = async () => {
		const { get } = await import('svelte/store');
		const items = [...get(selection.selected).values()];
		if (items.length === 0) return;

		let ok = 0;
		let removedSource = false;
		for (const item of items) {
			try {
				if (item.kind === 'file') {
					await removeFileFromKnowledgeById(localStorage.token, id, item.fileId);
					ok++;
				} else if (activeProvider) {
					await activeProvider.api.removeSource(localStorage.token, knowledge.id, item.itemId);
					removedSource = true;
					ok++;
				}
			} catch (e) {
				console.error('Bulk remove failed for', item.key, e);
			}
		}

		toast[ok > 0 ? 'success' : 'error'](
			$i18n.t('Removed {{ok}} of {{total}} items', { ok, total: items.length })
		);

		selection.clear();

		if (removedSource) {
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
		}
		await init();
	};
```

> Prefer a top-of-file `import { get } from 'svelte/store';` if one is not already present, and use `get(...)` directly instead of the dynamic `await import`. Check the existing imports first; many Svelte files here already import `get`. If not present, add it to the `svelte/store` import line.

- [x] **Step 3: Pass `selection` to the three editable views**

In the template, add `{selection}` to each of these mounts (do NOT add it to `LazyKnowledgeSearch`, which is read-only):
- `LazyKnowledgeTree` mount (after `onDelete={...}`, before the closing `/>` at line 2347)
- `SourceGroupedFiles` mount (after `onDelete={...}`, before `/>` at line 2397)
- `Files` mount (after `onDelete={...}`, before `/>` at line 2421)

Each becomes, e.g. for `Files`:

```svelte
						<Files
							files={fileItems}
							{knowledge}
							{selectedFileId}
							onClick={(fileId) => { /* unchanged */ }}
							onDelete={(fileId) => { /* unchanged */ }}
							{selection}
						/>
```

- [x] **Step 4: Render the bulk bar above the list**

Insert directly after `<div class="w-full h-full flex flex-col min-h-0">` (line 2318), before the `{#if lazyTreeActive && !query}` branch:

```svelte
							<div class="w-full h-full flex flex-col min-h-0">
								{#if $bulkCount > 0}
									<div class="px-1 pb-1.5">
										<KbBulkActionBar
											count={$bulkCount}
											onDelete={() => (showBulkRemoveConfirm = true)}
											onClear={() => selection.clear()}
											onSelectAll={!activeProvider && fileItems
												? () =>
														selection.selectAll(
															(fileItems ?? [])
																.filter((f) => f?.id && f?.status !== 'uploading')
																.map((f) => fileItem(f.id, f?.name ?? f?.meta?.name ?? ''))
														)
												: null}
										/>
									</div>
								{/if}
```

> `onSelectAll` is non-null only when the flat `Files` view is active (`!activeProvider`), where `fileItems` is the complete selectable set. In cloud/lazy views it is `null`, so the Select-All button is hidden (selection there is via checkbox + Shift-range + Cmd/Ctrl-click). This is the documented v1 scope line.

- [x] **Step 5: Add the confirm dialog**

Next to the existing `SyncConfirmDialog` usages (after line 1885), add a bulk-remove confirm. Its copy adapts to whether sources are included:

```svelte
<SyncConfirmDialog
	bind:show={showBulkRemoveConfirm}
	title={$bulkBreakdown.sources > 0
		? $i18n.t('Delete {{fileCount}} file(s) and {{sourceCount}} source(s)?', {
				fileCount: $bulkBreakdown.files,
				sourceCount: $bulkBreakdown.sources
			})
		: $i18n.t('Delete {{count}} files?', { count: $bulkBreakdown.files })}
	message={$bulkBreakdown.sources > 0
		? $i18n.t('Removing a source stops its sync and deletes all of its files.')
		: $i18n.t('This will remove the selected files from this knowledge base.')}
	confirmLabel={$i18n.t('Delete')}
	on:confirm={() => {
		bulkRemoveHandler();
	}}
/>
```

- [x] **Step 6: Add i18n keys (en-US + nl-NL)**

First check which keys already exist:

Run: `grep -nE '"(\{\{count\}\} selected|Select All|Deselect|Removed \{\{ok\}\} of \{\{total\}\} items|Delete \{\{count\}\} files\?|Delete \{\{fileCount\}\} file\(s\) and \{\{sourceCount\}\} source\(s\)\?|Removing a source stops its sync|This will remove the selected files|Select)":' src/lib/i18n/locales/en-US/translation.json src/lib/i18n/locales/nl-NL/translation.json`

For every key below that is MISSING from a file, add it in alphabetical position. en-US values may be the empty string `""` (meaning "use the key itself"); nl-NL must have the Dutch text.

en-US (`src/lib/i18n/locales/en-US/translation.json`) — add any missing as `"<key>": ""`:
- `"{{count}} selected"`
- `"Select"`
- `"Select All"`
- `"Deselect"`
- `"Removed {{ok}} of {{total}} items"`
- `"Delete {{count}} files?"`
- `"Delete {{fileCount}} file(s) and {{sourceCount}} source(s)?"`
- `"Removing a source stops its sync and deletes all of its files."`
- `"This will remove the selected files from this knowledge base."`

nl-NL (`src/lib/i18n/locales/nl-NL/translation.json`) — add any missing with these values:
- `"{{count}} selected": "{{count}} geselecteerd"`
- `"Select": "Selecteren"`
- `"Select All": "Alles selecteren"`
- `"Deselect": "Deselecteren"`
- `"Removed {{ok}} of {{total}} items": "{{ok}} van {{total}} items verwijderd"`
- `"Delete {{count}} files?": "{{count}} bestanden verwijderen?"`
- `"Delete {{fileCount}} file(s) and {{sourceCount}} source(s)?": "{{fileCount}} bestand(en) en {{sourceCount}} bron(nen) verwijderen?"`
- `"Removing a source stops its sync and deletes all of its files.": "Een bron verwijderen stopt de synchronisatie en verwijdert al zijn bestanden."`
- `"This will remove the selected files from this knowledge base.": "Hiermee worden de geselecteerde bestanden uit deze kennisbank verwijderd."`

- [x] **Step 7: Verify it builds and i18n is valid JSON**

Run: `npm run build`
Expected: build completes (exit 0).

Run: `node -e "JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/nl-NL/translation.json','utf8')); JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/en-US/translation.json','utf8')); console.log('JSON OK')"`
Expected: `JSON OK`.

- [x] **Step 8: Commit**

```bash
git add src/lib/components/workspace/Knowledge/KnowledgeBase.svelte src/lib/i18n/locales/en-US/translation.json src/lib/i18n/locales/nl-NL/translation.json
git commit -m "feat(kb): wire bulk multiselect delete + confirm + i18n in KnowledgeBase"
```

---

## Task 7: Verification & manual smoke test

**Files:** none (verification only).

- [x] **Step 1: Full unit test + build**

Run: `npm run test:frontend -- src/lib/components/workspace/Knowledge/KnowledgeBase/selection.test.ts`
Expected: PASS (8 tests).

Run: `npm run build`
Expected: exit 0.

- [x] **Step 2: Type-check the touched files only (no NEW errors)**

Run: `npx svelte-check --tsconfig ./tsconfig.json 2>&1 | grep -E 'KnowledgeBase/(selection|SelectCheckbox|KbBulkActionBar|Files|SourceGroupedFiles|LazyKnowledgeTree|LazyTreeNode)|KnowledgeBase.svelte' | head -50`
Expected: no errors referencing the new selection logic (pre-existing implicit-`any` warnings in these large files are acceptable; a NEW error you introduced is not).

- [ ] **Step 3: Manual smoke (run the stack)**

Start backend (`open-webui dev`) + frontend (`npm run dev`), open a Knowledge Base, and verify:
1. **Local KB:** hover a file row → checkbox fades in. Check 2 files → bulk bar shows "2 geselecteerd" with Select-All, Delete, Deselect. Shift-click selects a range; Cmd/Ctrl-click toggles. Select-All selects all. Delete → confirm "… bestanden verwijderen?" → confirm → files gone, one toast "x van y items verwijderd", list refreshed once. Plain click still opens the edit drawer. The per-row `✕` still deletes one immediately.
2. **Cloud KB (grouped):** loose files selectable; a synced source header selectable (checkbox). Mixed selection → confirm shows the file+source breakdown and the "stops its sync" warning → confirm removes both kinds. Files inside folders have no checkbox.
3. **Cloud KB (lazy tree):** root loose files + top-level source nodes selectable; nested files have no checkbox; Select-All button absent.
4. **Esc** clears selection. **No selection** → UI looks exactly as before.

- [ ] **Step 4: (Optional) E2E**

If a stack is running, invoke `/validate-owui` (owui-e2e) with a scenario that creates a KB, uploads 3 files, multi-selects 2, bulk-deletes, and asserts 1 remains.

- [ ] **Step 5: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill (or open a PR) once manual verification passes.

---

## Self-Review (completed by plan author)

- **Spec coverage:** all three views (Task 3/4/5) ✓; shared selection model (Task 1) ✓; shared bulk bar (Task 2) ✓; both removal actions batched (Task 6 `bulkRemoveHandler`) ✓; confirm with mixed breakdown + source-stops-sync note (Task 6 Step 5) ✓; single-`✕` unchanged (additive prop, no edits to existing handlers) ✓; en-US + nl-NL i18n (Task 6 Step 6) ✓; nested-file/upload exclusion (`isSelectable`/`isLooseSelectable`/`isFileSelectable` guards) ✓.
- **Deviations from spec (documented):** (a) Select-All is offered only in the flat `Files` view; cloud/lazy views select via checkbox + Shift-range + Cmd/Ctrl-click (lazy "select all" is ill-defined over unloaded rows). (b) Shift-range applies within a single flat list (local files, loose files); source rows support toggle + checkbox only. Both are noted inline and are reasonable v1 lines.
- **Type consistency:** `fileItem`/`sourceItem`/`SelectableItem`/`KbSelection` names are identical across Tasks 1, 3, 4, 5, 6. `selection` prop name is identical across all views and the parent. `removeSource(token, kbId, itemId)` and `removeFileFromKnowledgeById(token, id, fileId)` signatures match the extracted API.
- **Placeholder scan:** none — every code step shows complete code.
