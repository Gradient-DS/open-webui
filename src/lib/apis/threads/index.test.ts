import { afterEach, expect, it, vi } from 'vitest';
import { ThreadError, threadAPI } from './index';

afterEach(() => vi.unstubAllGlobals());

it('relays cache versions and context through the private browser boundary', async () => {
	const fetcher = vi.fn(async () => new Response(JSON.stringify({ items: [] })));
	vi.stubGlobal('fetch', fetcher);
	const body = {
		context_epoch: 'fictional-epoch',
		threads: [
			{
				thread_id: 'fictional-thread',
				generation: 'fictional-generation',
				versions: ['fictional-version']
			}
		]
	};
	await threadAPI('fictional-token').authorize(body);
	const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
	expect(url).toBe('/api/v1/threads:authorize');
	expect(init.method).toBe('POST');
	expect(init.cache).toBe('no-store');
	expect(JSON.parse(String(init.body))).toEqual(body);
	expect(init.headers).toMatchObject({ Authorization: 'Bearer fictional-token' });
});

it('requests private pages with only the OWUI credential and preserves opaque cursors and validators', async () => {
	const fetch = vi.fn(
		async () => new Response(null, { status: 304, headers: { ETag: '"fictional"' } })
	);
	vi.stubGlobal('fetch', fetch);
	const result = await threadAPI('fictional-owui-token', 10).projection(
		'fictional-thread',
		'opaque+/=',
		'"fictional"'
	);
	expect(result).toEqual({ page: null, etag: '"fictional"', notModified: true });
	const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
	expect(new URL(url, 'https://fictional.invalid').searchParams.get('before')).toBe('opaque+/=');
	expect(init.cache).toBe('no-store');
	expect(init.headers).toMatchObject({
		Authorization: 'Bearer fictional-owui-token',
		'If-None-Match': '"fictional"'
	});
	expect(init.headers).not.toHaveProperty('X-Soev-User-Token');
	expect(init.headers).not.toHaveProperty('X-Soev-' + 'Subject');
});

it.each([
	[401, 'sign_in'],
	[404, 'unavailable'],
	[422, 'invalid_request'],
	[503, 'reconnect']
] as const)('maps HTTP %i to %s without exposing an upstream error body', async (status, state) => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response('Fictional private detail', { status }))
	);
	await expect(threadAPI('fictional-token').index()).rejects.toEqual(new ThreadError(state));
});

it('maps network loss and an unsolicited 304 to reconnect', async () => {
	const fetch = vi
		.fn()
		.mockRejectedValueOnce(new TypeError('Fictional network failure'))
		.mockResolvedValueOnce(new Response(null, { status: 304 }));
	vi.stubGlobal('fetch', fetch);
	const client = threadAPI('fictional-token');
	await expect(client.projection('fictional-thread')).rejects.toEqual(new ThreadError('reconnect'));
	await expect(client.projection('fictional-thread')).rejects.toEqual(new ThreadError('reconnect'));
});
