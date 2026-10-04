import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import * as api from '$lib/apis/cloudSync';
import { connectLiveSource, prefetchLiveConnections } from './live-connections';
vi.mock('$lib/apis/cloudSync', async (original) => ({
	...(await original<typeof import('$lib/apis/cloudSync')>()),
	listConnections: vi.fn(),
	listLiveGrants: vi.fn(),
	enableLiveFamily: vi.fn(),
	createConnection: vi.fn(),
	authorizeConnection: vi.fn(),
	getConnection: vi.fn()
}));
const popup = { close: vi.fn(), closed: false, location: { href: '' } };
let receive: (event: unknown) => void;
beforeEach(() => {
	vi.resetAllMocks();
	vi.useFakeTimers();
	popup.closed = false;
	popup.location.href = '';
	vi.stubGlobal('window', {
		open: vi.fn(() => popup),
		location: { origin: 'https://app.invalid' },
		addEventListener: vi.fn((_, listener) => {
			receive = listener;
		}),
		removeEventListener: vi.fn()
	});
});
afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});
it('reuses an enabled grant without starting authorization', async () => {
	vi.mocked(api.listConnections).mockResolvedValue([
		{ id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' }
	]);
	vi.mocked(api.listLiveGrants).mockResolvedValue([
		{ id: 'g', lifecycle: 'enabled', families: ['live_documents'] }
	]);
	await prefetchLiveConnections('session');
	expect(await connectLiveSource('session')).toBe('g');
	expect(api.authorizeConnection).not.toHaveBeenCalled();
	expect(window.open).not.toHaveBeenCalled();
});
it('ignores forged completion and grants only after the owned connection is enabled', async () => {
	vi.mocked(api.listConnections).mockResolvedValue([]);
	vi.mocked(api.createConnection).mockResolvedValue({
		connection_id: 'c',
		authorize_url: 'https://provider.invalid/auth'
	});
	vi.mocked(api.getConnection).mockResolvedValue({
		id: 'c',
		source_kind: 'onedrive',
		lifecycle: 'enabled'
	});
	vi.mocked(api.enableLiveFamily).mockResolvedValue({
		id: 'g',
		lifecycle: 'enabled',
		families: ['live_documents']
	});
	await prefetchLiveConnections('session');
	const pending = connectLiveSource('session');
	await vi.advanceTimersByTimeAsync(0);
	receive({
		origin: 'https://evil.invalid',
		source: popup,
		data: { type: 'soev_connect', connection: 'c', result: 'pending' }
	});
	expect(api.enableLiveFamily).not.toHaveBeenCalled();
	receive({
		origin: 'https://app.invalid',
		source: popup,
		data: { type: 'soev_connect', connection: 'c', result: 'pending' }
	});
	expect(await pending).toBe('g');
	expect(api.enableLiveFamily).toHaveBeenCalledWith('session', 'c', 'live_documents');
});
it('propagates policy denial without authorizing a disabled feature', async () => {
	vi.mocked(api.listConnections).mockResolvedValue([
		{ id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' }
	]);
	vi.mocked(api.listLiveGrants).mockResolvedValue([]);
	vi.mocked(api.enableLiveFamily).mockRejectedValue(
		new api.CloudSyncError(403, 'policy_forbids', 'Live documents are disabled')
	);
	await prefetchLiveConnections('session');
	await expect(connectLiveSource('session')).rejects.toThrow('Live documents are disabled');
	expect(api.authorizeConnection).not.toHaveBeenCalled();
});

it('sends a picker reference to the consumer with session authorization and an operation id', async () => {
	const { attachPickedDocument } = await import('./live-connections');
	const fetchSpy = vi.fn().mockResolvedValue({
		ok: true,
		json: async () => ({ id: 'source', type: 'file', status: 'processing' })
	});
	vi.stubGlobal('fetch', fetchSpy);
	const ref = {
		drive_id: 'd',
		item_id: 'i',
		etag: 'v1',
		name: 'Plan.pdf',
		web_url: 'https://tenant.sharepoint.com/plan.pdf',
		size: 123
	};
	expect((await attachPickedDocument('session', 'grant', ref, 'operation')).id).toBe('source');
	const [url, request] = fetchSpy.mock.calls[0];
	expect(url).toMatch(/\/files\/onedrive\/attach$/);
	expect(request.headers).toEqual({
		Authorization: 'Bearer session',
		'Content-Type': 'application/json',
		'Idempotency-Key': 'operation'
	});
	expect(JSON.parse(request.body)).toEqual({ grant_id: 'grant', ...ref });
});
it.each(['suspended:reauth', 'enabled'])(
	'reauthorizes %s connections with a reauth error',
	async (lifecycle) => {
		vi.mocked(api.listConnections).mockResolvedValue([
			{ id: 'c', source_kind: 'onedrive', lifecycle, last_error: 'reauth_required' }
		]);
		vi.mocked(api.authorizeConnection).mockResolvedValue({
			authorize_url: 'https://provider.invalid/auth'
		});
		await prefetchLiveConnections('session');
		const pending = connectLiveSource('session');
		expect(window.open).toHaveBeenCalledOnce();
		await vi.advanceTimersByTimeAsync(0);
		expect(api.authorizeConnection).toHaveBeenCalledWith('session', 'c');
		receive({
			origin: 'https://app.invalid',
			source: popup,
			data: { type: 'soev_connect', connection: 'c', result: 'error' }
		});
		await expect(pending).rejects.toThrow('Provider connection failed');
	}
);

it('selects only the picker tenant and object identity, regardless of connection order', async () => {
	const { matchingPickerConnection } = await import('./live-connections');
	const rows = ['other', 'chosen'].map((id) => ({
		connection: {
			id,
			source_kind: 'onedrive',
			lifecycle: 'enabled',
			provider_tenant_id: 'tenant',
			provider_identity: `entra:user:${id}`
		},
		grantId: `grant-${id}`
	}));
	const account = { tenantId: 'tenant', localAccountId: 'chosen', username: 'user@example.test' };
	expect(matchingPickerConnection(rows, account).grantId).toBe('grant-chosen');
	expect(() => matchingPickerConnection(rows, { ...account, tenantId: 'other-tenant' })).toThrow(
		'does not match'
	);
	expect(() => matchingPickerConnection(rows, { ...account, localAccountId: 'missing' })).toThrow(
		'does not match'
	);
	expect(() =>
		matchingPickerConnection(
			[{ ...rows[1], connection: { ...rows[1].connection, lifecycle: 'suspended:reauth' } }],
			account
		)
	).toThrow('does not match');
});

it('keeps mail consent separate from document consent', async () => {
	vi.mocked(api.listConnections).mockResolvedValue([
		{ id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' },
		{ id: 'm', source_kind: 'outlook_mail', lifecycle: 'enabled' }
	]);
	vi.mocked(api.listLiveGrants).mockImplementation(async (_token, id, family) => [
		{ id: `${id}-grant`, lifecycle: 'enabled', families: [family!] }
	]);
	await Promise.all([
		prefetchLiveConnections('mail-session', 'onedrive', 'live_documents'),
		prefetchLiveConnections('mail-session', 'outlook_mail', 'mail')
	]);
	expect(await connectLiveSource('mail-session', 'outlook_mail', 'mail')).toBe('m-grant');
	expect(await connectLiveSource('mail-session', 'onedrive', 'live_documents')).toBe('c-grant');
	expect(window.open).not.toHaveBeenCalled();
});
