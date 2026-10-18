import { beforeEach, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { getPolicy, type SyncPolicy } from '$lib/apis/cloudSync';

vi.mock('$lib/apis/cloudSync', () => ({ getPolicy: vi.fn() }));
const policy: SyncPolicy = {
	providers_enabled: ['google_drive', 'unknown'],
	scope_shapes_allowed: { google_drive: ['folder'] },
	min_cadence_minutes: 15,
	default_cadence_minutes: 60
};

beforeEach(() => {
	vi.resetModules();
	vi.mocked(getPolicy).mockReset();
});

it('shares one pending request and caches the resolved policy', async () => {
	let resolve!: (policy: SyncPolicy) => void;
	vi.mocked(getPolicy).mockReturnValue(
		new Promise((done) => {
			resolve = done;
		})
	);
	const { sourcePolicy, loadSourcePolicy } = await import('./policy');
	expect(get(sourcePolicy)).toBeNull();
	const first = loadSourcePolicy('token');
	expect(loadSourcePolicy('token')).toBe(first);
	await Promise.resolve();
	expect(getPolicy).toHaveBeenCalledExactlyOnceWith('token');
	resolve(policy);
	await expect(first).resolves.toEqual(policy);
	expect(get(sourcePolicy)).toEqual(policy);
	expect(loadSourcePolicy('token')).toBe(first);
});

it('resolves failures to an empty policy', async () => {
	vi.mocked(getPolicy).mockRejectedValue(new Error('offline'));
	const { sourcePolicy, loadSourcePolicy, emptyPolicy, enabledProviders } =
		await import('./policy');
	await expect(loadSourcePolicy('token')).resolves.toEqual(emptyPolicy);
	expect(get(sourcePolicy)).toEqual(emptyPolicy);
	expect(get(enabledProviders)).toEqual([]);
});

it('shows only registered providers enabled by policy', async () => {
	const { sourcePolicy, enabledProviders } = await import('./policy');
	expect(get(enabledProviders)).toEqual([]);
	sourcePolicy.set(policy);
	expect(get(enabledProviders).map((provider) => provider.kind)).toEqual(['google_drive']);
	sourcePolicy.set({ ...policy, providers_enabled: [] });
	expect(get(enabledProviders)).toEqual([]);
});

it('does not reuse another signed-in session or publish its stale response', async () => {
	let resolve!: (policy: SyncPolicy) => void;
	vi.mocked(getPolicy)
		.mockReturnValueOnce(
			new Promise((done) => {
				resolve = done;
			})
		)
		.mockResolvedValueOnce(policy);
	const { loadSourcePolicy, sourcePolicy } = await import('./policy');
	const old = loadSourcePolicy('old-token');
	await loadSourcePolicy('new-token');
	resolve({ ...policy, providers_enabled: [] });
	await old;
	expect(get(sourcePolicy)).toEqual(policy);
	expect(getPolicy).toHaveBeenCalledTimes(2);
});
