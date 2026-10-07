import { describe, expect, it } from 'vitest';

import { INPUT_ITEMS, barItemIds, inputItemOrder, itemState, normalizePins } from './inputItems';

describe('input items', () => {
	it('lists every menu item as pinnable, meetings included, in menu order', () => {
		expect(INPUT_ITEMS.every((item) => item.pinnable)).toBe(true);
		const order = inputItemOrder(['a', 'b']);
		expect(order.indexOf('attach_meetings')).toBe(order.indexOf('attach_notes') + 1);
		expect(order.slice(order.indexOf('document_writer') + 1, order.indexOf('tools'))).toEqual([
			'filter:a',
			'filter:b'
		]);
	});

	it('maps tool states and booleans to off / auto / on', () => {
		expect(itemState('auto')).toBe('auto');
		expect(itemState('required')).toBe('on');
		expect(itemState('off')).toBe('off');
		expect(itemState(true)).toBe('on');
		expect(itemState(false)).toBe('off');
		expect(itemState(0)).toBe('off');
		expect(itemState(2)).toBe('on');
		expect(itemState(undefined)).toBe('off');
	});
});

describe('composer bar', () => {
	const order = inputItemOrder(['f1']);

	it('shows pinned items and active items, in menu order, each once', () => {
		const ids = barItemIds({
			order,
			pinned: ['tools', 'attach_meetings', 'web_search'],
			states: { web_search: 'auto', live_mail: 'auto', image_generation: 'on', 'filter:f1': 'on' }
		});
		expect(ids).toEqual([
			'attach_meetings',
			'live_mail',
			'web_search',
			'image_generation',
			'filter:f1',
			'tools'
		]);
	});

	it('leaves out items that are off and not pinned, and stale pins', () => {
		expect(
			barItemIds({ order, pinned: ['gone', 'filter:old'], states: { code_interpreter: 'off' } })
		).toEqual([]);
	});

	it('respects what is allowed here (restrictions, data separation)', () => {
		const ids = barItemIds({
			order,
			pinned: ['knowledge'],
			states: { web_search: 'auto' },
			allowed: (id) => id !== 'web_search'
		});
		expect(ids).toEqual(['knowledge']);
	});
});

describe('pin set', () => {
	it('keeps stored pins, dropping junk and duplicates', () => {
		expect(normalizePins(['a', 'b', 'a', 3, '', null])).toEqual(['a', 'b']);
		expect(normalizePins(undefined)).toEqual([]);
		expect(normalizePins('a')).toEqual([]);
	});
});
