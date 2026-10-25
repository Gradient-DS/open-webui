import { afterEach, describe, expect, it, vi } from 'vitest';
import { submitThreadRating } from './threadFeedback';

afterEach(() => vi.unstubAllGlobals());

describe('thread ratings', () => {
	it('sends only explicit rating fields, discarding a supplied snapshot and attribution', async () => {
		const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', fetcher);
		const input = {
			thread_id: 'fictional-thread',
			position: 2,
			rating: 1 as const,
			model: 'fictional-model',
			snapshot: { text: 'Fictional private text' }
		};
		await submitThreadRating('fictional-token', input);
		expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
			thread_id: 'fictional-thread',
			position: 2,
			rating: 1
		});
		expect(fetcher.mock.calls[0][1].cache).toBe('no-store');
	});
	it('marks only an explicitly written comment as authored', async () => {
		const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', fetcher);
		await submitThreadRating('fictional-token', {
			thread_id: 'fictional-thread',
			position: 2,
			rating: -1,
			comment: 'Fictional comment'
		});
		expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
			thread_id: 'fictional-thread',
			position: 2,
			rating: -1,
			comment: 'Fictional comment',
			comment_authored: true
		});
	});
	it('refuses a failed submission without displaying upstream content', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response('Fictional private detail', { status: 404 }))
		);
		await expect(
			submitThreadRating('fictional-token', {
				thread_id: 'fictional-thread',
				position: 2,
				rating: 1
			})
		).rejects.toThrow('This chat is unavailable');
	});
});

it('reads per-thread feedback with the current credential and no persistent HTTP cache', async () => {
	const fetcher = vi
		.fn()
		.mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null })));
	vi.stubGlobal('fetch', fetcher);
	const { readThreadFeedback } = await import('./threadFeedback');
	await readThreadFeedback('fictional-token', {
		threadId: 'fictional-thread',
		cursor: 'fictional-cursor'
	});
	expect(fetcher.mock.calls[0][0]).toContain('/feedback/conversation/fictional-thread?');
	expect(fetcher.mock.calls[0][0]).toContain('cursor=fictional-cursor');
	expect(fetcher.mock.calls[0][1]).toMatchObject({
		cache: 'no-store',
		headers: { Authorization: 'Bearer fictional-token' }
	});
});
