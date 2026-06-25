import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock MSAL so getMsalInstance()/getGraphApiToken() resolve a fake token
// without touching the network or a real browser.
vi.mock('@azure/msal-browser', () => {
	class PublicClientApplication {
		async initialize() {}
		async acquireTokenSilent() {
			return { accessToken: 'graph-token', account: {} };
		}
		setActiveAccount() {}
		async loginPopup() {
			return { accessToken: 'graph-token', account: {}, idToken: 'id' };
		}
	}
	return { PublicClientApplication };
});

// Build a fetch stub whose /api/config payload toggles static vs derive mode.
function stubFetch(sharepointUrl: string) {
	return vi.fn(async (url: string) => {
		if (typeof url === 'string' && url.endsWith('/api/config')) {
			return {
				ok: true,
				json: async () => ({
					onedrive: {
						client_id_business: 'biz-client',
						client_id_personal: 'personal-client',
						sharepoint_url: sharepointUrl,
						sharepoint_tenant_id: 'common'
					}
				})
			} as unknown as Response;
		}
		if (typeof url === 'string' && url.endsWith('/me/drive')) {
			return {
				ok: true,
				status: 200,
				json: async () => ({ webUrl: 'https://derived-my.sharepoint.com/personal/u/Documents' })
			} as unknown as Response;
		}
		throw new Error(`unexpected fetch: ${url}`);
	});
}

const meDriveCalls = (spy: ReturnType<typeof vi.fn>) =>
	spy.mock.calls.filter(([url]) => typeof url === 'string' && url.endsWith('/me/drive')).length;

describe('OneDriveConfig.resolveHost', () => {
	beforeEach(() => {
		vi.resetModules();
		// getMsalInstance() reads window.location.origin for the redirectUri;
		// provide a minimal browser-like global for the node test env.
		vi.stubGlobal('window', { location: { origin: 'http://localhost' } });
	});

	it('uses the static host when sharepoint_url is set and makes no /me/drive call', async () => {
		const fetchSpy = stubFetch('https://static.sharepoint.com');
		vi.stubGlobal('fetch', fetchSpy);

		const { OneDriveConfig } = await import('./onedrive-file-picker');
		const config = OneDriveConfig.getInstance();
		await config.resolveHost('organizations');

		expect(config.getBaseUrl()).toBe('https://static.sharepoint.com');
		expect(meDriveCalls(fetchSpy)).toBe(0);
	});

	it('derives the host from /me/drive when sharepoint_url is blank', async () => {
		const fetchSpy = stubFetch('');
		vi.stubGlobal('fetch', fetchSpy);

		const { OneDriveConfig } = await import('./onedrive-file-picker');
		const config = OneDriveConfig.getInstance();
		await config.resolveHost('organizations');

		expect(config.getBaseUrl()).toBe('https://derived-my.sharepoint.com');
		expect(meDriveCalls(fetchSpy)).toBe(1);
	});

	it('memoizes the derived host (single /me/drive call across repeated resolves)', async () => {
		const fetchSpy = stubFetch('');
		vi.stubGlobal('fetch', fetchSpy);

		const { OneDriveConfig } = await import('./onedrive-file-picker');
		const config = OneDriveConfig.getInstance();
		await config.resolveHost('organizations');
		await config.resolveHost('organizations');

		expect(config.getBaseUrl()).toBe('https://derived-my.sharepoint.com');
		expect(meDriveCalls(fetchSpy)).toBe(1);
	});
});
