import { describe, expect, it } from 'vitest';
import { fitZoom, PAGE_HEIGHT, PAGE_WIDTH } from './documentZoom';

const page = { width: PAGE_WIDTH, height: PAGE_HEIGHT };

describe('fitting a page to the panel', () => {
	it('shows a single page whole in a panel shorter than the page', () => {
		expect(fitZoom({ width: 900, height: PAGE_HEIGHT / 2 }, page)).toBe(0.5);
	});

	it('shrinks a single page to a narrower panel', () => {
		expect(fitZoom({ width: PAGE_WIDTH / 2, height: 5000 }, page)).toBe(0.5);
	});

	it('fits a longer document to the width and lets it scroll', () => {
		const long = { width: PAGE_WIDTH, height: PAGE_HEIGHT * 3 };
		expect(fitZoom({ width: PAGE_WIDTH / 2, height: 400 }, long)).toBe(0.5);
	});

	it('shows a page at 100% in a larger panel instead of enlarging it', () => {
		expect(fitZoom({ width: 2000, height: 2000 }, page)).toBe(1);
	});

	it('fits a page wider than A4 by its own width', () => {
		expect(fitZoom({ width: 600, height: 5000 }, { width: 1200, height: PAGE_HEIGHT })).toBe(0.5);
	});

	it('shows 100% before the panel is measured', () => {
		expect(fitZoom({ width: 0, height: 0 }, page)).toBe(1);
	});
});
