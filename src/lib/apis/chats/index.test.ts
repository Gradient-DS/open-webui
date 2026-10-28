import { afterEach, expect, it, vi } from 'vitest';
import { addTagById, getTagsById } from './index';

vi.mock('$lib/utils', () => ({ getTimeRange: vi.fn() }));
afterEach(() => vi.unstubAllGlobals());

it('adapts canonical public tags for the existing tag component', async () => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify(['fictional_grove'])))
	);
	expect(await getTagsById('fictional-token', 'fictional-thread')).toEqual([
		{ name: 'fictional_grove' }
	]);
});

it('keeps legacy tag objects readable', async () => {
	const tags = [{ id: 'fictional-tag', name: 'Fictional Grove' }];
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify(tags)))
	);
	expect(await getTagsById('fictional-token', 'fictional-chat')).toEqual(tags);
});

it('reports a non-owner tag refusal instead of swallowing its state', async () => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify({ state: 'unavailable' }), { status: 404 }))
	);
	await expect(addTagById('fictional-token', 'fictional-thread', 'Fictional Grove')).rejects.toBe(
		'This chat is unavailable'
	);
});
