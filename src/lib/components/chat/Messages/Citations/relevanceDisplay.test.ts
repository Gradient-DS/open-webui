import { describe, it, expect } from 'vitest';
import { calculateShowRelevance, shouldShowPercentage } from './relevanceDisplay';

describe('shouldShowPercentage', () => {
	it('shows percentages when every score is in [-1, 1]', () => {
		expect(shouldShowPercentage([{ distances: [0.7873, 0.5855, 0.4353] }])).toBe(true);
	});

	it('falls back to raw scores when any score is out of range', () => {
		expect(shouldShowPercentage([{ distances: [0.78, 12.4] }])).toBe(false);
	});

	it('ignores undefined holes left by a source that carried no distances', () => {
		// Regression: a whole-document read (read_document / fetch_url) emits no
		// `distances`, so reduceSources leaves undefined holes to keep the
		// parallel arrays aligned. Those holes used to disqualify the whole
		// message, downgrading every scored chunk to a grey raw score.
		const citations = [
			{ distances: [0.7873, 0.5855, 0.4353] },
			{ distances: [undefined, undefined] }
		];
		expect(shouldShowPercentage(citations)).toBe(true);
	});

	it('ignores holes interleaved inside one citation', () => {
		expect(shouldShowPercentage([{ distances: [0.87, undefined, 0.44] }])).toBe(true);
	});

	it('still falls back when a real out-of-range score sits beside holes', () => {
		expect(shouldShowPercentage([{ distances: [0.87, undefined, 42] }])).toBe(false);
	});

	it('is false when there are no scores at all', () => {
		expect(shouldShowPercentage([{ distances: [undefined] }, {}])).toBe(false);
	});
});

describe('calculateShowRelevance', () => {
	it('shows relevance for a uniform set of scores', () => {
		expect(calculateShowRelevance([{ distances: [0.87, 0.7, 0.44] }])).toBe(true);
	});

	it('hides relevance when the scores are not on one scale', () => {
		expect(calculateShowRelevance([{ distances: [0.87, 0.7, 42] }])).toBe(false);
	});

	it('hides relevance when no source carried a score', () => {
		expect(calculateShowRelevance([{ distances: [undefined, undefined] }, {}])).toBe(false);
	});

	it('does not let holes inflate the outlier count', () => {
		// Holes are not scores: with them counted, the single out-of-scale 42
		// no longer looked like "one outlier out of three" and relevance was
		// wrongly shown.
		const citations = [{ distances: [0.87, 0.7, 42] }, { distances: [undefined, undefined] }];
		expect(calculateShowRelevance(citations)).toBe(false);
	});
});
