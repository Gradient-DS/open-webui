import { afterEach, expect, it, vi } from 'vitest';
import { getFolders } from './index';

vi.mock('$lib/utils', () => ({ getTimeRange: vi.fn() }));
vi.mock('$lib/stores', async () => ({
	config: (await import('svelte/store')).writable(undefined)
}));
afterEach(() => vi.unstubAllGlobals());

it('keeps the supported folder list shape and rejects v2 state failures', async () => {
	const folders = [{ id: 'folder', name: 'Grove', unread_count: 0 }];
	const fetch = vi
		.fn()
		.mockResolvedValueOnce(new Response(JSON.stringify(folders)))
		.mockResolvedValueOnce(new Response(JSON.stringify({ state: 'reconnect' }), { status: 503 }));
	vi.stubGlobal('fetch', fetch);
	expect(await getFolders('token')).toEqual(folders);
	expect(fetch.mock.calls[0][0]).toBe('/api/v1/folders/');
	await expect(getFolders('token')).rejects.toBe('Reconnect to view this chat');
});
