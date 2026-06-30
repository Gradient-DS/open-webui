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
