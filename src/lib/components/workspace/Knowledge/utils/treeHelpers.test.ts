import { describe, it, expect } from 'vitest';
import { LARGE_TREE_THRESHOLD, isLargeTree, computeInitialExpansion } from './treeHelpers';

describe('isLargeTree', () => {
	it('returns false when totalFiles is null (unknown)', () => {
		expect(isLargeTree(null)).toBe(false);
	});

	it('returns false for an empty KB (0 files)', () => {
		expect(isLargeTree(0)).toBe(false);
	});

	it('returns false for a small KB just below the threshold', () => {
		expect(isLargeTree(LARGE_TREE_THRESHOLD - 1)).toBe(false);
	});

	it('returns true at the threshold boundary', () => {
		expect(isLargeTree(LARGE_TREE_THRESHOLD)).toBe(true);
	});

	it('returns true for a large KB well above the threshold', () => {
		expect(isLargeTree(5950)).toBe(true);
	});

	it('respects a custom threshold', () => {
		expect(isLargeTree(10, 10)).toBe(true);
		expect(isLargeTree(9, 10)).toBe(false);
	});
});

describe('computeInitialExpansion', () => {
	it('auto-expands all sources for a small KB', () => {
		const result = computeInitialExpansion(['id-a', 'id-b'], 10);
		expect(result['id-a']).toBe(true);
		expect(result['id-b']).toBe(true);
	});

	it('collapses all sources for a large KB', () => {
		const result = computeInitialExpansion(['id-a', 'id-b'], 200);
		expect(result['id-a']).toBe(false);
		expect(result['id-b']).toBe(false);
	});

	it('collapses when totalFiles is null (unknown size)', () => {
		// unknown → not large → auto-expand
		const result = computeInitialExpansion(['id-a'], null);
		expect(result['id-a']).toBe(true);
	});

	it('returns an empty map for no sources', () => {
		expect(computeInitialExpansion([], 100)).toEqual({});
	});

	it('respects a custom threshold', () => {
		const result = computeInitialExpansion(['x'], 5, 10);
		expect(result['x']).toBe(true); // 5 < 10 → small → expanded
	});
});
