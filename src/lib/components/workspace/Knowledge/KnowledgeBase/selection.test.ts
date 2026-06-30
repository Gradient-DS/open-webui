import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import { createKbSelection, fileItem, sourceItem } from './selection';

const f = (id: string) => fileItem(id, `file-${id}`);
const s = (id: string, fc = 1) => sourceItem(id, `src-${id}`, fc);

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

	it('breakdown counts files, sources, and total affected files', () => {
		const sel = createKbSelection();
		sel.selectAll([f('a'), s('x', 11), s('y', 3)]);
		// 1 plain file + sources holding 11 and 3 files = 15 total files removed
		expect(get(sel.breakdown)).toEqual({ files: 1, sources: 2, totalFiles: 15 });
	});

	it('selectionMode reflects whether anything is selected', () => {
		const sel = createKbSelection();
		expect(get(sel.selectionMode)).toBe(false);
		sel.toggle(f('a'));
		expect(get(sel.selectionMode)).toBe(true);
		sel.clear();
		expect(get(sel.selectionMode)).toBe(false);
	});

	it('clear empties the selection', () => {
		const sel = createKbSelection();
		sel.selectAll([f('a'), f('b')]);
		sel.clear();
		expect(get(sel.count)).toBe(0);
	});

	it('available drives allSelected / indeterminate / toggleSelectAll', () => {
		const sel = createKbSelection();
		const av = [f('a'), f('b'), s('x', 2)];
		sel.setAvailable(av);
		expect(get(sel.allSelected)).toBe(false);
		expect(get(sel.indeterminate)).toBe(false);

		sel.toggle(f('a'));
		expect(get(sel.allSelected)).toBe(false);
		expect(get(sel.indeterminate)).toBe(true);

		sel.toggleSelectAll(); // not all → select all
		expect(get(sel.count)).toBe(3);
		expect(get(sel.allSelected)).toBe(true);
		expect(get(sel.indeterminate)).toBe(false);

		sel.toggleSelectAll(); // all → clear
		expect(get(sel.count)).toBe(0);
	});

	it('drag paints an additive contiguous range from anchor to hovered row', () => {
		const sel = createKbSelection();
		const ordered = [f('a'), f('b'), f('c'), f('d')];
		sel.toggle(f('d')); // pre-existing selection (baseline)
		sel.pointerDown(f('a'), ordered);
		sel.pointerEnter(f('c')); // drag a..c
		expect([...get(sel.selected).keys()].sort()).toEqual(['file:a', 'file:b', 'file:c', 'file:d']);
		expect(sel.consumeDidDrag()).toBe(true);
		// consumed once, then resets
		expect(sel.consumeDidDrag()).toBe(false);
		sel.endDrag();
	});

	it('press without moving to another row is not a drag', () => {
		const sel = createKbSelection();
		const ordered = [f('a'), f('b')];
		sel.pointerDown(f('a'), ordered);
		sel.endDrag();
		expect(sel.consumeDidDrag()).toBe(false);
		expect(get(sel.count)).toBe(0);
	});

	it('file and source helpers build collision-free keys + payloads', () => {
		expect(fileItem('1', 'A')).toEqual({ key: 'file:1', label: 'A', kind: 'file', fileId: '1' });
		expect(sourceItem('1', 'A', 7)).toEqual({
			key: 'source:1',
			label: 'A',
			kind: 'source',
			itemId: '1',
			sourceName: 'A',
			fileCount: 7
		});
		expect(sourceItem('2', 'B').fileCount).toBe(1); // defaults to 1
	});
});
