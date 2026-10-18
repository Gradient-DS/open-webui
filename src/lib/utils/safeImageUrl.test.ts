import { describe, expect, it } from 'vitest';

import { isSafeImageUrl, safeImageUrl } from './safeImageUrl';

describe('isSafeImageUrl', () => {
	it('rejects empty and relative figure paths from parsed documents', () => {
		expect(isSafeImageUrl('')).toBe(false);
		expect(isSafeImageUrl('figure-1.png')).toBe(false);
		expect(isSafeImageUrl('image_000000_abc.png', true)).toBe(false);
	});

	it('accepts same-origin paths and data URIs', () => {
		expect(isSafeImageUrl('/api/v1/files/abc/content')).toBe(true);
		expect(isSafeImageUrl('data:image/png;base64,AAAA')).toBe(true);
	});

	it('accepts external http(s) URLs only when allowed', () => {
		expect(isSafeImageUrl('https://example.com/a.png')).toBe(false);
		expect(isSafeImageUrl('https://example.com/a.png', true)).toBe(true);
	});
});

describe('safeImageUrl', () => {
	it('substitutes the placeholder exactly when isSafeImageUrl rejects', () => {
		expect(safeImageUrl('figure-1.png', true)).toBe('/favicon.png');
		expect(safeImageUrl('/api/v1/files/abc/content')).toBe('/api/v1/files/abc/content');
	});
});
