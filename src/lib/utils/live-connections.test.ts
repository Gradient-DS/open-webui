import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import * as api from '$lib/apis/cloudSync';
import { get } from 'svelte/store';
import {
	attachErrorMessage,
	connectLiveSource,
	grantedLiveState,
	liveConnections,
	liveDefaultState,
	liveGranted,
	liveGrants,
	prefetchLiveConnections,
	prefetchLiveFamilies,
	relinkPrompt
} from './live-connections';
vi.mock('$lib/apis/cloudSync', async (original) => ({
	...(await original<typeof import('$lib/apis/cloudSync')>()),
	listConnections: vi.fn(),
	listLiveGrants: vi.fn(),
	enableLiveFamily: vi.fn(),
	createConnection: vi.fn(),
	authorizeConnection: vi.fn(),
	getConnection: vi.fn(),
	grantAutoConnected: vi.fn()
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
		screenX: 100,
		screenY: 50,
		outerWidth: 1400,
		outerHeight: 900,
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
	const opened = vi.fn();
	expect(await connectLiveSource('session', 'onedrive', 'live_documents', opened)).toBe('g');
	expect(api.authorizeConnection).not.toHaveBeenCalled();
	expect(window.open).not.toHaveBeenCalled();
	expect(opened).not.toHaveBeenCalled();
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
		const opened = vi.fn();
		const pending = connectLiveSource('session', 'onedrive', 'live_documents', opened);
		expect(window.open).toHaveBeenCalledWith(
			'about:blank',
			'soev-live-live_documents',
			'width=600,height=720,left=500,top=140'
		);
		expect(opened).toHaveBeenCalledOnce();
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
	expect(api.listConnections).toHaveBeenCalledTimes(1);
	expect(await connectLiveSource('mail-session', 'outlook_mail', 'mail')).toBe('m-grant');
	expect(await connectLiveSource('mail-session', 'onedrive', 'live_documents')).toBe('c-grant');
	expect(window.open).not.toHaveBeenCalled();
});
it('maps subject_not_linked to the log-in-again prompt, not a generic error', () => {
	const unlinked = new api.CloudSyncError(403, 'subject_not_linked', 'Subject is not linked');
	expect(relinkPrompt(unlinked)).toBe('Log in again with Microsoft to connect');
	expect(relinkPrompt(new Error('subject_not_linked'))).toBe(
		'Log in again with Microsoft to connect'
	);
	expect(relinkPrompt(new api.CloudSyncError(403, 'policy_forbids', 'Disabled'))).toBeNull();
	expect(relinkPrompt('subject_not_linked')).toBeNull();
});
it('maps picker attach refusals to user messages without upstream detail', () => {
	expect(attachErrorMessage(new Error('too_large'))).toBe('This file is too large to attach.');
	expect(attachErrorMessage(new Error('soev_not_configured'))).toBe(
		'Attaching OneDrive files is unavailable right now.'
	);
	expect(attachErrorMessage(new Error('subject_not_linked'))).toBe(
		'Log in again with Microsoft to connect'
	);
	expect(attachErrorMessage(new Error('upstream_error'))).toBe('Could not attach OneDrive file');
	expect(attachErrorMessage('too_large')).toBe('Could not attach OneDrive file');
});

it('refreshes the snapshot after a cancelled consent, so a retry reuses the pending connection', async () => {
	vi.mocked(api.listConnections)
		.mockResolvedValueOnce([])
		.mockResolvedValue([{ id: 'c', source_kind: 'onedrive', lifecycle: 'pending' }]);
	vi.mocked(api.createConnection).mockResolvedValue({
		connection_id: 'c',
		authorize_url: 'https://provider.invalid/auth'
	});
	vi.mocked(api.getConnection).mockResolvedValue({
		id: 'c',
		source_kind: 'onedrive',
		lifecycle: 'pending'
	});
	await prefetchLiveConnections('cancel-session');
	const pending = connectLiveSource('cancel-session');
	const rejected = expect(pending).rejects.toThrow('Provider connection cancelled');
	await vi.advanceTimersByTimeAsync(0);
	popup.closed = true;
	await vi.advanceTimersByTimeAsync(500);
	await rejected;
	expect(api.getConnection).toHaveBeenCalledWith('cancel-session', 'c');
	expect(liveConnections('cancel-session')?.map((row) => row.connection.id)).toEqual(['c']);
	vi.mocked(api.authorizeConnection).mockResolvedValue({
		authorize_url: 'https://provider.invalid/auth'
	});
	void connectLiveSource('cancel-session').catch(() => {});
	await vi.advanceTimersByTimeAsync(0);
	expect(api.authorizeConnection).toHaveBeenCalledWith('cancel-session', 'c');
	expect(api.createConnection).toHaveBeenCalledOnce();
});
it('treats a popup that closed before its message as connected when the connection is enabled', async () => {
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
	vi.mocked(api.listLiveGrants).mockResolvedValue([]);
	vi.mocked(api.enableLiveFamily).mockResolvedValue({
		id: 'g',
		lifecycle: 'enabled',
		families: ['live_documents']
	});
	await prefetchLiveConnections('closed-session');
	const pending = connectLiveSource('closed-session');
	await vi.advanceTimersByTimeAsync(0);
	popup.closed = true;
	await vi.advanceTimersByTimeAsync(500);
	expect(await pending).toBe('g');
	expect(api.enableLiveFamily).toHaveBeenCalledWith('closed-session', 'c', 'live_documents');
});
it('enables the family on an existing healthy connection without waiting for consent', async () => {
	vi.mocked(api.listConnections).mockResolvedValue([]);
	vi.mocked(api.createConnection).mockResolvedValue({ connection_id: 'c', authorize_url: null });
	vi.mocked(api.getConnection).mockResolvedValue({
		id: 'c',
		source_kind: 'outlook_mail',
		lifecycle: 'enabled'
	});
	vi.mocked(api.enableLiveFamily).mockResolvedValue({
		id: 'g',
		lifecycle: 'enabled',
		families: ['mail']
	});
	await prefetchLiveConnections('exists-session', 'outlook_mail', 'mail');
	expect(await connectLiveSource('exists-session', 'outlook_mail', 'mail')).toBe('g');
	expect(popup.location.href).toBe('');
	expect(popup.close).toHaveBeenCalled();
});
it('publishes per family whether the snapshot holds a grant', async () => {
	vi.mocked(api.listConnections).mockResolvedValue([
		{ id: 'm', source_kind: 'outlook_mail', lifecycle: 'enabled' },
		{ id: 'c', source_kind: 'onedrive', lifecycle: 'pending' }
	]);
	vi.mocked(api.listLiveGrants).mockResolvedValue([
		{ id: 'g', lifecycle: 'enabled', families: ['mail'] }
	]);
	await Promise.all([
		prefetchLiveConnections('grant-session', 'outlook_mail', 'mail'),
		prefetchLiveConnections('grant-session', 'onedrive', 'live_documents')
	]);
	expect(liveGranted(get(liveGrants), 'mail')).toBe(true);
	expect(liveGranted(get(liveGrants), 'live_documents')).toBe(false);
});
it('starts a family on only with the feature and a grant, and never sends it on without one', () => {
	expect(liveDefaultState(true, true)).toBe('auto');
	expect(liveDefaultState(true, false)).toBe('off');
	expect(liveDefaultState(true, undefined)).toBe('off');
	expect(liveDefaultState(false, true)).toBe('off');
	expect(grantedLiveState('auto', false)).toBe('off');
	expect(grantedLiveState('auto', true)).toBe('auto');
	expect(grantedLiveState('auto', undefined)).toBe('auto');
	expect(grantedLiveState('off', true)).toBe('off');
});

it('grants what the Microsoft login connected once per session, before the first snapshot', async () => {
	let grantedNow = false;
	vi.mocked(api.grantAutoConnected).mockImplementation(async () => {
		grantedNow = true;
		return { families: ['mail'] };
	});
	vi.mocked(api.listConnections).mockImplementation(async () =>
		grantedNow ? [{ id: 'c', source_kind: 'outlook_mail', lifecycle: 'enabled' }] : []
	);
	vi.mocked(api.listLiveGrants).mockResolvedValue([
		{ id: 'g', lifecycle: 'enabled', families: ['mail'] }
	]);
	prefetchLiveFamilies('auto-session', ['mail']);
	prefetchLiveFamilies('auto-session', ['mail']);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.grantAutoConnected).toHaveBeenCalledTimes(1);
	expect(liveGranted(get(liveGrants), 'mail')).toBe(true);
});

it('renews Outlook silently and waits for the token exchange', async () => {
	const { reauthorizeMail } = await import('./live-connections');
	vi.mocked(api.authorizeConnection).mockResolvedValue({
		authorize_url: 'https://provider.invalid/silent'
	});
	vi.mocked(api.getConnection)
		.mockResolvedValueOnce({ id: 'c', source_kind: 'outlook_mail', lifecycle: 'suspended:reauth' })
		.mockResolvedValue({ id: 'c', source_kind: 'outlook_mail', lifecycle: 'enabled' });
	const pending = reauthorizeMail('session', 'c');
	await vi.advanceTimersByTimeAsync(0);
	expect(api.authorizeConnection).toHaveBeenCalledWith('session', 'c', true);
	receive({
		origin: 'https://app.invalid',
		source: popup,
		data: { type: 'soev_connect', connection: 'c', result: 'pending' }
	});
	await vi.advanceTimersByTimeAsync(1000);
	await pending;
	expect(api.authorizeConnection).toHaveBeenCalledOnce();
	expect(api.getConnection).toHaveBeenCalledTimes(2);
	expect(popup.close).toHaveBeenCalled();
});

it('continues interactive Outlook consent in the silent popup only after a trusted callback', async () => {
	const { reauthorizeMail } = await import('./live-connections');
	vi.mocked(api.authorizeConnection)
		.mockResolvedValueOnce({ authorize_url: 'https://provider.invalid/silent' })
		.mockResolvedValueOnce({ authorize_url: 'https://provider.invalid/interactive' });
	vi.mocked(api.getConnection).mockResolvedValue({
		id: 'c',
		source_kind: 'outlook_mail',
		lifecycle: 'enabled'
	});
	const pending = reauthorizeMail('session', 'c');
	await vi.advanceTimersByTimeAsync(0);
	const callback = {
		origin: 'https://app.invalid',
		source: popup,
		data: { type: 'soev_connect', connection: 'c', result: 'interaction_required' }
	};
	for (const forged of [
		{ ...callback, origin: 'https://other.invalid' },
		{ ...callback, source: {} },
		{ ...callback, data: { ...callback.data, connection: 'other' } }
	])
		receive(forged);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.authorizeConnection).toHaveBeenCalledOnce();
	receive(callback);
	await vi.advanceTimersByTimeAsync(0);
	expect(api.authorizeConnection).toHaveBeenLastCalledWith('session', 'c');
	expect(popup.location.href).toBe('https://provider.invalid/interactive');
	expect(window.open).toHaveBeenCalledOnce();
	receive({ ...callback, data: { ...callback.data, result: 'pending' } });
	await pending;
	expect(popup.close).toHaveBeenCalled();
});

it('does not begin reauthorization if the consent popup is blocked', async () => {
	const { reauthorizeMail } = await import('./live-connections');
	vi.mocked(window.open).mockReturnValue(null);
	await expect(reauthorizeMail('session', 'c')).rejects.toThrow(
		'Allow popups to connect your account'
	);
	expect(api.authorizeConnection).not.toHaveBeenCalled();
});
