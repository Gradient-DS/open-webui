import { describe, expect, it } from 'vitest';
import { sheetRelease } from './sourcesSheet';

describe('sheetRelease', () => {
	it('expands when dragged past halfway to full height', () => {
		expect(sheetRelease(800, 900)).toBe('expanded');
	});

	it('returns to the normal height in between', () => {
		expect(sheetRelease(650, 900)).toBe('normal');
		expect(sheetRelease(500, 900)).toBe('normal');
	});

	it('closes when dragged below half the screen', () => {
		expect(sheetRelease(400, 900)).toBe('close');
	});
});
