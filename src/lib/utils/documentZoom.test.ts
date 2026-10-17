import { describe, expect, it } from 'vitest';
import { fitZoom } from './documentZoom';

const page = { width: 794, height: 1123 };

describe('fitting a document to the panel', () => {
	it('shows a single page whole in a panel shorter than the page', () => {
		expect(fitZoom({ width: 900, height: 1123 / 2 }, page, true)).toBe(0.5);
	});

	it('shrinks a single page to a narrower panel', () => {
		expect(fitZoom({ width: 397, height: 5000 }, page, true)).toBe(0.5);
	});

	it('fits more pages to the width and lets them scroll', () => {
		expect(fitZoom({ width: 397, height: 400 }, { width: 794, height: 3400 }, false)).toBe(0.5);
	});

	it('shows a page at 100% in a larger panel instead of enlarging it', () => {
		expect(fitZoom({ width: 2000, height: 2000 }, page, true)).toBe(1);
	});

	it('fits a page wider than its paper by its own width', () => {
		expect(fitZoom({ width: 600, height: 5000 }, { width: 1200, height: 1123 }, true)).toBe(0.5);
	});

	it('shows 100% before the panel is measured', () => {
		expect(fitZoom({ width: 0, height: 0 }, page, true)).toBe(1);
	});
});
