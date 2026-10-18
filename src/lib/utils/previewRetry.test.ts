import { describe, expect, it, vi } from 'vitest';

import {
	PREVIEW_PENDING_DETAIL,
	PreviewPendingError,
	isPendingFileStatus,
	isPreviewPending,
	loadWithPreviewRetry,
	previewRetryDelay
} from './previewRetry';

function clock() {
	let time = 0;
	return {
		now: () => time,
		sleep: vi.fn(async (ms: number) => {
			time += ms;
		})
	};
}

describe('previewRetryDelay', () => {
	it('grows from 2s and caps at 10s', () => {
		expect([0, 1, 2, 3, 4, 5, 10].map(previewRetryDelay)).toEqual([
			2000, 3000, 4500, 6750, 10000, 10000, 10000
		]);
	});
});

describe('isPreviewPending', () => {
	it('recognises the processing detail, a 409 and the pending error', () => {
		expect(isPreviewPending(PREVIEW_PENDING_DETAIL)).toBe(true);
		expect(isPreviewPending({ status: 409 })).toBe(true);
		expect(isPreviewPending({ detail: PREVIEW_PENDING_DETAIL })).toBe(true);
		expect(isPreviewPending(new PreviewPendingError())).toBe(true);
		expect(isPreviewPending('Not found')).toBe(false);
		expect(isPreviewPending({ status: 404 })).toBe(false);
		expect(isPreviewPending(null)).toBe(false);
	});
});

describe('isPendingFileStatus', () => {
	it('treats pending, processing and uploading as pending', () => {
		expect(['pending', 'processing', 'uploading'].every(isPendingFileStatus)).toBe(true);
		expect(['completed', 'uploaded', 'failed', undefined].some(isPendingFileStatus)).toBe(false);
	});
});

describe('loadWithPreviewRetry', () => {
	it('retries while pending and returns the value once available', async () => {
		const { now, sleep } = clock();
		const onPending = vi.fn();
		const load = vi
			.fn()
			.mockRejectedValueOnce(PREVIEW_PENDING_DETAIL)
			.mockRejectedValueOnce(PREVIEW_PENDING_DETAIL)
			.mockResolvedValue('bytes');
		const result = await loadWithPreviewRetry(load, {
			isCurrent: () => true,
			onPending,
			now,
			sleep
		});
		expect(result).toEqual({ status: 'ok', value: 'bytes' });
		expect(load).toHaveBeenCalledTimes(3);
		expect(onPending).toHaveBeenCalledTimes(2);
		expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([2000, 3000]);
	});

	it('fails at once on an error that is not pending', async () => {
		const { now, sleep } = clock();
		const result = await loadWithPreviewRetry(() => Promise.reject('Not found'), {
			isCurrent: () => true,
			now,
			sleep
		});
		expect(result).toEqual({ status: 'failed', error: 'Not found' });
		expect(sleep).not.toHaveBeenCalled();
	});

	it('gives up once the wait budget would be exceeded', async () => {
		const { now, sleep } = clock();
		const load = vi.fn().mockRejectedValue(PREVIEW_PENDING_DETAIL);
		const result = await loadWithPreviewRetry(load, { isCurrent: () => true, now, sleep });
		expect(result).toEqual({ status: 'failed', error: PREVIEW_PENDING_DETAIL });
		expect(now()).toBeLessThanOrEqual(180_000);
		expect(now()).toBeGreaterThan(170_000);
	});

	it('stops without reporting once the caller moved on', async () => {
		let current = true;
		const sleep = vi.fn(async () => {
			current = false;
		});
		const load = vi.fn().mockRejectedValue(PREVIEW_PENDING_DETAIL);
		const result = await loadWithPreviewRetry(load, { isCurrent: () => current, sleep });
		expect(result).toEqual({ status: 'cancelled' });
		expect(load).toHaveBeenCalledTimes(1);
	});

	it('discards a late success for a replaced file', async () => {
		const result = await loadWithPreviewRetry(() => Promise.resolve('bytes'), {
			isCurrent: () => false
		});
		expect(result).toEqual({ status: 'cancelled' });
	});

	it('uses a custom pending check', async () => {
		const { now, sleep } = clock();
		const load = vi.fn().mockRejectedValueOnce('Not found').mockResolvedValue('bytes');
		const result = await loadWithPreviewRetry(load, {
			isCurrent: () => true,
			isPending: () => true,
			now,
			sleep
		});
		expect(result).toEqual({ status: 'ok', value: 'bytes' });
	});
});
