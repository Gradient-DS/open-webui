import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getKnowledgeById, searchKnowledgeFilesById } from '$lib/apis/knowledge';
import {
	createKnowledgePrefetchCache,
	initialKnowledgeItemsArgs,
	invalidateKnowledgePrefetch,
	knowledgeRequestKey,
	prefetchKnowledge,
	takeKnowledgeDetail,
	takeKnowledgeItems
} from './prefetch';

vi.mock('$lib/apis/knowledge', () => ({
	getKnowledgeById: vi.fn(),
	searchKnowledgeFilesById: vi.fn()
}));

beforeEach(() => {
	vi.useFakeTimers();
	vi.clearAllMocks();
});
afterEach(() => {
	invalidateKnowledgePrefetch();
	vi.useRealTimers();
});

describe('knowledge prefetch', () => {
	it('starts the exact detail and root-items requests together and deduplicates hover/focus', async () => {
		let resolve!: (value: object) => void;
		vi.mocked(getKnowledgeById).mockReturnValue(
			new Promise((done) => {
				resolve = done;
			})
		);
		vi.mocked(searchKnowledgeFilesById).mockResolvedValue({ items: [], total: 0 });
		prefetchKnowledge('user', 'token', 'kb');
		prefetchKnowledge('user', 'token', 'kb');
		expect(getKnowledgeById).toHaveBeenCalledExactlyOnceWith('token', 'kb');
		expect(searchKnowledgeFilesById).toHaveBeenCalledExactlyOnceWith(
			'token',
			'kb',
			'',
			null,
			null,
			null,
			1,
			null,
			true,
			null
		);
		const detail = takeKnowledgeDetail('user', 'token', 'kb');
		const items = takeKnowledgeItems('user', initialKnowledgeItemsArgs('token', 'kb'));
		resolve({ id: 'kb' });
		expect(await detail.promise).toEqual({ id: 'kb' });
		expect(await items.promise).toEqual({ items: [], total: 0 });
		expect(detail.reused).toBe(true);
		expect(items.reused).toBe(true);
		expect(getKnowledgeById).toHaveBeenCalledTimes(1);
		expect(searchKnowledgeFilesById).toHaveBeenCalledTimes(1);
	});

	it('consumes settled entries once and fetches normally on a second navigation', async () => {
		const cache = createKnowledgePrefetchCache();
		const load = vi.fn(async () => ({ id: 'kb' }));
		cache.prefetch('key', load);
		await Promise.resolve();
		vi.advanceTimersByTime(29_999);
		const first = cache.take('key', load);
		await first.promise;
		expect(first.reused).toBe(true);
		const second = cache.take('key', load);
		await second.promise;
		expect(second.reused).toBe(false);
		expect(load).toHaveBeenCalledTimes(2);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('expires pending entries at 30 seconds without extending the TTL on hover', async () => {
		const cache = createKnowledgePrefetchCache();
		const load = vi
			.fn<() => Promise<string>>()
			.mockImplementationOnce(() => new Promise(() => {}))
			.mockResolvedValue('fresh');
		cache.prefetch('key', load);
		vi.advanceTimersByTime(20_000);
		cache.prefetch('key', load);
		vi.advanceTimersByTime(10_000);
		const request = cache.take('key', load);
		expect(await request.promise).toBe('fresh');
		expect(request.reused).toBe(false);
		expect(load).toHaveBeenCalledTimes(2);
		expect(vi.getTimerCount()).toBe(0);
	});

	it.each([undefined, null, new Error('offline')])(
		'falls back after an unsuccessful prefetch: %s',
		async (result) => {
			const cache = createKnowledgePrefetchCache();
			const load = vi
				.fn()
				.mockImplementationOnce(() =>
					result instanceof Error ? Promise.reject(result) : Promise.resolve(result)
				)
				.mockResolvedValue('fresh');
			cache.prefetch('key', load);
			const request = cache.take('key', load);
			expect(await request.promise).toBe('fresh');
			expect(request.reused).toBe(false);
			expect(load).toHaveBeenCalledTimes(2);
		}
	);

	it('preserves normal fetch errors for the caller', async () => {
		const cache = createKnowledgePrefetchCache();
		const load = vi.fn().mockRejectedValue(new Error('denied'));
		cache.prefetch('key', load);
		await expect(cache.take('key', load).promise).rejects.toThrow('denied');
	});

	it('invalidates pending entries and releases their timers', async () => {
		const cache = createKnowledgePrefetchCache();
		let resolve!: (value: string) => void;
		const load = vi
			.fn<() => Promise<string>>()
			.mockImplementationOnce(
				() =>
					new Promise((done) => {
						resolve = done;
					})
			)
			.mockResolvedValue('fresh');
		cache.prefetch('key', load);
		cache.invalidate();
		resolve('deleted');
		expect(await cache.take('key', load).promise).toBe('fresh');
		expect(vi.getTimerCount()).toBe(0);
	});

	it('isolates user, credentials, KB and every items parameter including directory scope', () => {
		const args = initialKnowledgeItemsArgs('token', 'kb');
		const key = knowledgeRequestKey('user', 'items', args);
		expect(knowledgeRequestKey('other', 'items', args)).not.toBe(key);
		expect(knowledgeRequestKey('user', 'detail', args)).not.toBe(key);
		for (let index = 0; index < args.length; index++) {
			const different: unknown[] = [...args];
			different[index] = 'different';
			expect(knowledgeRequestKey('user', 'items', different)).not.toBe(key);
		}
		expect(knowledgeRequestKey('user', 'items', [...args.slice(0, -1), undefined])).not.toBe(key);
	});
});
