import { writable, derived, get, type Readable } from 'svelte/store';

export type SelectableItem =
	| { key: string; label: string; kind: 'file'; fileId: string }
	| {
			key: string;
			label: string;
			kind: 'source';
			itemId: string;
			sourceName: string;
			fileCount: number;
	  }
	| { key: string; label: string; kind: 'directory'; dirId: string; fileCount: number };

export const fileItem = (
	fileId: string,
	label: string
): Extract<SelectableItem, { kind: 'file' }> => ({
	key: `file:${fileId}`,
	label,
	kind: 'file',
	fileId
});

export const directoryItem = (
	dirId: string,
	label: string,
	fileCount = 0
): Extract<SelectableItem, { kind: 'directory' }> => ({
	key: `dir:${dirId}`,
	label,
	kind: 'directory',
	dirId,
	fileCount
});

export const sourceItem = (
	itemId: string,
	label: string,
	fileCount = 1
): Extract<SelectableItem, { kind: 'source' }> => ({
	key: `source:${itemId}`,
	label,
	kind: 'source',
	itemId,
	sourceName: label,
	fileCount
});

type ClickModifiers = { shiftKey?: boolean; metaKey?: boolean; ctrlKey?: boolean };

export interface KbSelection {
	selected: Readable<Map<string, SelectableItem>>;
	count: Readable<number>;
	breakdown: Readable<{ files: number; sources: number; directories: number; totalFiles: number }>;
	available: Readable<SelectableItem[]>;
	allSelected: Readable<boolean>;
	indeterminate: Readable<boolean>;
	selectionMode: Readable<boolean>;
	toggle: (item: SelectableItem) => void;
	select: (item: SelectableItem, orderedItems: SelectableItem[], e: ClickModifiers) => void;
	selectAll: (items: SelectableItem[]) => void;
	setAvailable: (items: SelectableItem[]) => void;
	toggleSelectAll: () => void;
	clear: () => void;
	pointerDown: (item: SelectableItem, orderedItems: SelectableItem[]) => void;
	pointerEnter: (item: SelectableItem) => void;
	endDrag: () => void;
	consumeDidDrag: () => boolean;
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
	const _available = writable<SelectableItem[]>([]);
	let lastKey: string | null = null;

	// Drag-paint state (plain locals — not reactive).
	let dragging = false;
	let dragAnchorKey: string | null = null;
	let dragOrdered: SelectableItem[] = [];
	let dragBaseline = new Map<string, SelectableItem>();
	let didDrag = false;

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

	const setAvailable = (items: SelectableItem[]) => _available.set(items);

	const clear = () => {
		_selected.set(new Map());
		lastKey = null;
	};

	// Header checkbox: anything selected → clear (one-click deselect-all, since the
	// explicit deselect button was removed); nothing selected → select all available.
	const toggleSelectAll = () => {
		if (get(_selected).size > 0) clear();
		else selectAll(get(_available));
	};

	// ── Drag-paint ────────────────────────────────────────────────────────
	const pointerDown = (item: SelectableItem, orderedItems: SelectableItem[]) => {
		dragging = true;
		dragAnchorKey = item.key;
		dragOrdered = orderedItems;
		dragBaseline = new Map(get(_selected));
		didDrag = false;
	};

	const pointerEnter = (item: SelectableItem) => {
		if (!dragging || dragAnchorKey === null) return;
		if (item.key !== dragAnchorKey) didDrag = true;
		const range = rangeBetween(dragOrdered, dragAnchorKey, item.key);
		const m = new Map(dragBaseline);
		for (const it of range) m.set(it.key, it);
		_selected.set(m);
		lastKey = item.key;
	};

	const endDrag = () => {
		dragging = false;
		dragAnchorKey = null;
	};

	const consumeDidDrag = () => {
		const d = didDrag;
		didDrag = false;
		return d;
	};

	const count = derived(_selected, (m) => m.size);
	const selectionMode = derived(_selected, (m) => m.size > 0);
	const breakdown = derived(_selected, (m) => {
		let files = 0;
		let sources = 0;
		let directories = 0;
		let totalFiles = 0;
		for (const it of m.values()) {
			if (it.kind === 'file') {
				files++;
				totalFiles++;
			} else if (it.kind === 'directory') {
				directories++;
				totalFiles += it.fileCount ?? 0;
			} else {
				sources++;
				totalFiles += it.fileCount ?? 0;
			}
		}
		return { files, sources, directories, totalFiles };
	});
	const allSelected = derived([_selected, _available], ([m, av]) => {
		return av.length > 0 && av.every((it) => m.has(it.key));
	});
	const indeterminate = derived([_selected, allSelected], ([m, all]) => m.size > 0 && !all);

	return {
		selected: { subscribe: _selected.subscribe },
		count,
		breakdown,
		available: { subscribe: _available.subscribe },
		allSelected,
		indeterminate,
		selectionMode,
		toggle,
		select,
		selectAll,
		setAvailable,
		toggleSelectAll,
		clear,
		pointerDown,
		pointerEnter,
		endDrag,
		consumeDidDrag
	};
}
