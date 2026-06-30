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
