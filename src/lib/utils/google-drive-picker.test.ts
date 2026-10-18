import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_BASE_URL: '' }));

let callback: (response: { access_token?: string; error?: string }) => void;
let rejectPopup: () => void;
const requestAccessToken = vi.fn();
const hasGrantedAllScopes = vi.fn();
const fetch = vi.fn();

beforeEach(() => {
	vi.resetModules();
	vi.useFakeTimers();
	requestAccessToken.mockReset();
	hasGrantedAllScopes.mockReturnValue(true);
	fetch.mockReset().mockImplementation(
		async () =>
			new Response(
				JSON.stringify({
					google_drive: { api_key: 'invalid-test-api-key', client_id: 'invalid-test-client-id' }
				})
			)
	);
	vi.stubGlobal('fetch', fetch);
	vi.stubGlobal('gapi', { load: (_name: string, ready: () => void) => ready() });
	vi.stubGlobal('google', {
		accounts: {
			oauth2: {
				initTokenClient: (options: {
					callback: typeof callback;
					error_callback: typeof rejectPopup;
				}) => {
					callback = options.callback;
					rejectPopup = options.error_callback;
					return { requestAccessToken };
				},
				hasGrantedAllScopes
			}
		}
	});
});
afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

it('requests browser consent with no backend token route or token storage', async () => {
	const picker = await import('./google-drive-picker');
	await picker.initialize();
	const token = picker.getAuthToken();
	expect(requestAccessToken).toHaveBeenCalledOnce();
	callback({ access_token: 'invalid-test-google-token' });
	await expect(token).resolves.toBe('invalid-test-google-token');
	expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/config']);
	expect(vi.getTimerCount()).toBe(0);
});

it.each(['denied', 'scope', 'closed'])(
	'rejects %s consent without exposing provider response details',
	async (failure) => {
		const picker = await import('./google-drive-picker');
		await picker.initialize();
		const token = picker.getAuthToken();
		const rejected = expect(token).rejects.toThrow(
			'Google Drive authorization failed or was cancelled'
		);
		if (failure === 'closed') rejectPopup();
		else {
			hasGrantedAllScopes.mockReturnValue(failure !== 'scope');
			callback(
				failure === 'denied'
					? { error: 'invalid-test-error' }
					: { access_token: 'invalid-test-google-token' }
			);
		}
		await rejected;
		expect(vi.getTimerCount()).toBe(0);
	}
);

it('times out consent when the provider never calls back', async () => {
	const picker = await import('./google-drive-picker');
	await picker.initialize();
	const rejected = expect(picker.getAuthToken()).rejects.toThrow(
		'Google Drive authorization timed out'
	);
	await vi.advanceTimersByTimeAsync(120000);
	await rejected;
});
