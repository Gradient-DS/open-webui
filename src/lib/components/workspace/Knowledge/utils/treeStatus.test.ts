import { describe, it, expect } from 'vitest';
import { folderBadge, fileBadge, breadcrumbSegments } from './treeStatus';

describe('folderBadge', () => {
	it('returns failed when any descendant failed (precedence over pending)', () => {
		expect(folderBadge({ pending: 3, completed: 1, failed: 2, unknown: 0 })).toBe('failed');
	});

	it('returns pending when in-progress but none failed', () => {
		expect(folderBadge({ pending: 1, completed: 5, failed: 0, unknown: 0 })).toBe('pending');
	});

	it('returns none when all completed/unknown', () => {
		expect(folderBadge({ pending: 0, completed: 4, failed: 0, unknown: 2 })).toBe('none');
	});

	it('handles null/empty counts', () => {
		expect(folderBadge(null)).toBe('none');
		expect(folderBadge(undefined)).toBe('none');
		expect(folderBadge({})).toBe('none');
	});
});

describe('fileBadge', () => {
	it('spinner for in-progress states', () => {
		for (const s of ['uploading', 'pending', 'processing', 'downloading', 'parsing', 'ingesting']) {
			expect(fileBadge(s)).toBe('spinner');
		}
	});

	// Regression: the doc-pipeline's 'processing' was missing from IN_PROGRESS,
	// so a file still being parsed/embedded rendered the idle document icon.
	it('spinner for the doc-pipeline processing status', () => {
		expect(fileBadge('processing')).toBe('spinner');
	});

	it('error for error/cancelled/failed', () => {
		for (const s of ['error', 'cancelled', 'failed']) {
			expect(fileBadge(s)).toBe('error');
		}
	});

	it('idle for completed/unknown/null', () => {
		expect(fileBadge('completed')).toBe('idle');
		expect(fileBadge('uploaded')).toBe('idle');
		expect(fileBadge(null)).toBe('idle');
		expect(fileBadge(undefined)).toBe('idle');
	});
});

describe('breadcrumbSegments', () => {
	it('drops the filename, returns folder segments', () => {
		expect(breadcrumbSegments('docs/sub/file.pdf')).toEqual(['docs', 'sub']);
	});

	it('returns empty for a root-level file', () => {
		expect(breadcrumbSegments('file.pdf')).toEqual([]);
	});

	it('handles null/undefined/empty', () => {
		expect(breadcrumbSegments(null)).toEqual([]);
		expect(breadcrumbSegments(undefined)).toEqual([]);
		expect(breadcrumbSegments('')).toEqual([]);
	});
});
