import { describe, it, expect } from 'vitest';
import { rectsFromMetadata } from './citationRects';

const RECT = { page: 2, x0: 10, y0: 20, x1: 300, y1: 40 };

describe('rectsFromMetadata', () => {
	it('parses an array of rects and converts pages 0-based -> 1-based', () => {
		const rects = rectsFromMetadata({ bboxes: [RECT] });
		expect(rects).toEqual([{ page: 3, x0: 10, y0: 20, x1: 300, y1: 40 }]);
	});

	it('parses the JSON-string form (Weaviate TEXT property)', () => {
		const rects = rectsFromMetadata({ bboxes: JSON.stringify([RECT]) });
		expect(rects).toEqual([{ page: 3, x0: 10, y0: 20, x1: 300, y1: 40 }]);
	});

	it('falls back to metadata.page when a rect has no page of its own', () => {
		const rects = rectsFromMetadata({
			page: 4,
			bboxes: [{ x0: 1, y0: 2, x1: 3, y1: 4 }]
		});
		expect(rects).toEqual([{ page: 5, x0: 1, y0: 2, x1: 3, y1: 4 }]);
	});

	it('defaults the fallback page to 0 (-> page 1) without metadata.page', () => {
		const rects = rectsFromMetadata({ bboxes: [{ x0: 1, y0: 2, x1: 3, y1: 4 }] });
		expect(rects?.[0]?.page).toBe(1);
	});

	it('returns null for missing, malformed-JSON, or non-array bboxes', () => {
		expect(rectsFromMetadata(undefined)).toBeNull();
		expect(rectsFromMetadata({})).toBeNull();
		expect(rectsFromMetadata({ bboxes: 'not json {' })).toBeNull();
		expect(rectsFromMetadata({ bboxes: '"a string"' })).toBeNull();
		expect(rectsFromMetadata({ bboxes: 42 as unknown })).toBeNull();
	});

	it('drops malformed entries but keeps valid ones', () => {
		const rects = rectsFromMetadata({
			bboxes: [
				null,
				'nonsense',
				{ x0: 'a', y0: 0, x1: 1, y1: 1 },
				{ x0: 0, y0: 0, x1: Number.NaN, y1: 1 },
				RECT
			]
		});
		expect(rects).toHaveLength(1);
		expect(rects?.[0]?.page).toBe(3);
	});

	it('drops degenerate rects (zero or negative extent)', () => {
		expect(
			rectsFromMetadata({
				bboxes: [
					{ page: 0, x0: 10, y0: 10, x1: 10, y1: 20 },
					{ page: 0, x0: 10, y0: 30, x1: 20, y1: 20 }
				]
			})
		).toBeNull();
	});

	it('returns null when nothing valid remains (fallback chain can proceed)', () => {
		expect(rectsFromMetadata({ bboxes: [] })).toBeNull();
		expect(rectsFromMetadata({ bboxes: [{ x0: 'x' }] })).toBeNull();
	});
});
