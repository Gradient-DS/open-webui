import { afterEach, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { config } from '$lib/stores';
import { userSignOut } from './index';

const { clear, clearParts } = vi.hoisted(() => ({ clear: vi.fn(), clearParts: vi.fn() }));
vi.mock('$lib/utils/threadCache', () => ({ threadCacheOrigin: () => ({ clear }) }));
vi.mock('$lib/components/meetings/parts', () => ({ partStore: () => ({ clear: clearParts }) }));
vi.mock('$lib/stores', async () => ({
	config: (await import('svelte/store')).writable({ features: { enable_v2_threads: true } })
}));
afterEach(() => {
	vi.clearAllMocks();
	vi.unstubAllGlobals();
});

it.each(['sync', 'async'] as const)(
	'always signs out on the server and surfaces a %s cache clear failure',
	async (mode) => {
		const failure = new Error('Fictional storage failure');
		if (mode === 'sync')
			clear.mockImplementationOnce(() => {
				throw failure;
			});
		else clear.mockRejectedValueOnce(failure);
		const fetcher = vi.fn(async () => new Response(JSON.stringify({ status: true })));
		vi.stubGlobal('fetch', fetcher);
		const clearSession = vi.fn();
		vi.stubGlobal('sessionStorage', { clear: clearSession });
		await expect(userSignOut()).rejects.toBe(failure);
		expect(fetcher).toHaveBeenCalledWith(
			expect.stringContaining('/auths/signout'),
			expect.objectContaining({ method: 'POST', credentials: 'include' })
		);
		expect(clearSession).toHaveBeenCalledOnce();
		expect(clearParts).toHaveBeenCalledOnce();
	}
);

it('keeps the legacy signout path when v2 threads are disabled', async () => {
	const previous = get(config)!;
	config.set({ ...previous, features: { ...previous.features, enable_v2_threads: false } });
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify({ status: true })))
	);
	vi.stubGlobal('sessionStorage', { clear: vi.fn() });
	try {
		expect(await userSignOut()).toEqual({ status: true });
		expect(clear).not.toHaveBeenCalled();
	} finally {
		config.set(previous);
	}
});
