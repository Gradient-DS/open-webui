import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import * as api from '$lib/apis/cloudSync';
import { connectLiveDocuments, prefetchLiveDocuments } from './live-documents';
vi.mock('$lib/apis/cloudSync', async (original) => ({
	...(await original<typeof import('$lib/apis/cloudSync')>()),
	listConnections: vi.fn(),
	listLiveDocumentGrants: vi.fn(),
	enableLiveDocuments: vi.fn(),
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
	vi.mocked(api.listLiveDocumentGrants).mockResolvedValue([
		{ id: 'g', lifecycle: 'enabled', families: ['live_documents'] }
	]);
	await prefetchLiveDocuments('session');
	expect(await connectLiveDocuments('session')).toBe('g');
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
	vi.mocked(api.enableLiveDocuments).mockResolvedValue({
		id: 'g',
		lifecycle: 'enabled',
		families: ['live_documents']
	});
	await prefetchLiveDocuments('session');
	const pending = connectLiveDocuments('session');
	await vi.advanceTimersByTimeAsync(0);
	receive({
		origin: 'https://evil.invalid',
		source: popup,
		data: { type: 'soev_connect', connection: 'c', result: 'pending' }
	});
	expect(api.enableLiveDocuments).not.toHaveBeenCalled();
	receive({
		origin: 'https://app.invalid',
		source: popup,
		data: { type: 'soev_connect', connection: 'c', result: 'pending' }
	});
	expect(await pending).toBe('g');
	expect(api.enableLiveDocuments).toHaveBeenCalledWith('session', 'c');
});
it('propagates policy denial without authorizing a disabled feature', async () => {
	vi.mocked(api.listConnections).mockResolvedValue([
		{ id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' }
	]);
	vi.mocked(api.listLiveDocumentGrants).mockResolvedValue([]);
	vi.mocked(api.enableLiveDocuments).mockRejectedValue(
		new api.CloudSyncError(403, 'policy_forbids', 'Live documents are disabled')
	);
	await prefetchLiveDocuments('session');
	await expect(connectLiveDocuments('session')).rejects.toThrow('Live documents are disabled');
	expect(api.authorizeConnection).not.toHaveBeenCalled();
});

<<<<<<< HEAD
it('sends a picker reference to the consumer with session authorization and an operation id', async () => {
	const { attachPickedDocument } = await import('./live-documents');
	const fetchSpy = vi
		.fn()
		.mockResolvedValue({
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
=======
it.each(['suspended:reauth', 'enabled'])(
	'reauthorizes %s connections with a reauth error',
	async (lifecycle) => {
		vi.mocked(api.listConnections).mockResolvedValue([
			{ id: 'c', source_kind: 'onedrive', lifecycle, last_error: 'reauth_required' }
		]);
		vi.mocked(api.authorizeConnection).mockResolvedValue({
			authorize_url: 'https://provider.invalid/auth'
		});
		await prefetchLiveDocuments('session');
		const pending = connectLiveDocuments('session');
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
>>>>>>> feat/live-documents-p6
