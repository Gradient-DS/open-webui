import { describe, expect, it } from 'vitest';

import { turnNotices, withoutRemoved } from './turnNotices';

describe('turnNotices', () => {
	it('reads the notices and removed ids of the event', () => {
		expect(
			turnNotices({ notices: ["Kennisbank 'Oud' bestaat niet meer."], removed: ['kb-gone'] })
		).toEqual({ notices: ["Kennisbank 'Oud' bestaat niet meer."], removed: ['kb-gone'] });
	});

	it('keeps only non-empty strings and survives a malformed event', () => {
		expect(turnNotices({ notices: ['one', '', 3, null], removed: 'kb-gone' })).toEqual({
			notices: ['one'],
			removed: []
		});
		expect(turnNotices(null)).toEqual({ notices: [], removed: [] });
	});
});

describe('withoutRemoved', () => {
	const files = [
		{ type: 'collection', id: 'kb-gone' },
		{ type: 'collection', id: 'kb-secret' },
		{ type: 'url', url: 'https://example.org' }
	];

	it('drops the removed entries from the selection', () => {
		expect(withoutRemoved(files, ['kb-gone'])).toEqual(files.slice(1));
	});

	it('returns the same selection when nothing was removed', () => {
		expect(withoutRemoved(files, [])).toBe(files);
	});
});
