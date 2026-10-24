// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import { externalHost, isSafeImageUrl, safeImageUrl } from './safeImageUrl';

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

describe('own paths', () => {
	it.each(['//attacker.example/a.png', '/\\attacker.example/a.png', '/\t/attacker.example/a.png'])(
		'never takes %j for a path on this origin',
		(url) => {
			expect(isSafeImageUrl(url)).toBe(false);
			expect(safeImageUrl(url)).toBe('/favicon.png');
		}
	);
});

describe('externalHost', () => {
	it.each([
		['https://attacker.example/p.png?d=secret', 'attacker.example'],
		['HTTP://Attacker.example:8443/p.png', 'attacker.example:8443'],
		['//attacker.example/p.png', 'attacker.example'],
		['/\\attacker.example/p.png', 'attacker.example'],
		['/\t/attacker.example/p.png', 'attacker.example']
	])('holds %j for %s', (url, host) => {
		expect(externalHost(url)).toBe(host);
	});

	it.each([
		'',
		'/api/v1/files/abc/content',
		'data:image/png;base64,AAAA',
		`${window.location.origin}/api/v1/files/abc/content`,
		'javascript:alert(1)',
		'figure-1.png'
	])('lets %j through without a host', (url) => {
		expect(externalHost(url)).toBeNull();
	});
});

describe('safeImageUrl', () => {
	it('substitutes the placeholder exactly when isSafeImageUrl rejects', () => {
		expect(safeImageUrl('figure-1.png', true)).toBe('/favicon.png');
		expect(safeImageUrl('/api/v1/files/abc/content')).toBe('/api/v1/files/abc/content');
	});
});
