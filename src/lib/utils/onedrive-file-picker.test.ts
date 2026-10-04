import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock MSAL so getMsalInstance()/getGraphApiToken() resolve a fake token
// without touching the network or a real browser.
const auth = vi.hoisted(() => ({ silent: vi.fn(), popup: vi.fn() }));

vi.mock('@azure/msal-browser', () => {
	class PublicClientApplication {
		async initialize() {}
		async acquireTokenSilent() {
			auth.silent();
			return { accessToken: 'graph-token', account: {} };
		}
		setActiveAccount() {}
		getActiveAccount() {
			return { homeAccountId: 'account', tenantId: 'tenant', localAccountId: 'user' };
		}
		getAllAccounts() {
			return [this.getActiveAccount()];
		}
		async loginPopup() {
			auth.popup();
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

describe('documentReference', () => {
	it('returns only versioned identity and display metadata without provider credentials or bytes', async () => {
		const { documentReference } = await import('./onedrive-file-picker');
		const fetchSpy = vi.fn();
		vi.stubGlobal('fetch', fetchSpy);
		const item = {
			id: 'i',
			name: 'Plan.pdf',
			parentReference: { driveId: 'd' },
			eTag: 'v1',
			webUrl: 'https://tenant.sharepoint.com/plan.pdf',
			size: 123,
			'@content.downloadUrl': 'https://never-fetch.invalid',
			access_token: 'never-forward'
		};
		expect(documentReference(item)).toEqual({
			drive_id: 'd',
			item_id: 'i',
			name: 'Plan.pdf',
			etag: 'v1',
			web_url: item.webUrl,
			size: 123
		});
		expect(fetchSpy).not.toHaveBeenCalled();
	});
	it.each(['eTag', 'id', 'parentReference', 'webUrl', 'size'])(
		'refuses a picker item missing %s',
		async (field) => {
			const { documentReference } = await import('./onedrive-file-picker');
			const item = {
				id: 'i',
				name: 'Plan.pdf',
				parentReference: { driveId: 'd' },
				eTag: 'v1',
				webUrl: 'https://tenant.sharepoint.com/plan.pdf',
				size: 123
			};
			expect(() => documentReference({ ...item, [field]: undefined })).toThrow(
				'versioned document reference'
			);
		}
	);
});

describe('business picker user gesture', () => {
	beforeEach(() => {
		vi.resetModules();
		auth.silent.mockReset();
		auth.popup.mockReset();
		vi.stubGlobal('window', { location: { origin: 'http://localhost' } });
		vi.stubGlobal('fetch', stubFetch('https://static.sharepoint.com'));
	});
	it('prepares silently and invokes loginPopup before yielding the click stack', async () => {
		const picker = await import('./onedrive-file-picker');
		auth.silent.mockImplementation(() => {
			throw new Error('interaction required');
		});
		await expect(picker.prepareBusinessDocumentPicker()).rejects.toThrow('Sign in');
		expect(auth.popup).not.toHaveBeenCalled();
		auth.popup.mockImplementation(() => {
			throw new Error('cancelled');
		});
		const pending = picker.beginBusinessDocumentPicker();
		expect(auth.popup).toHaveBeenCalledTimes(1);
		await expect(pending).rejects.toThrow('cancelled');
	});
	it('a connected cached account needs no interactive login', async () => {
		const picker = await import('./onedrive-file-picker');
		await picker.prepareBusinessDocumentPicker();
		const pending = picker.beginBusinessDocumentPicker();
		// There is no DOM in this unit test; opening the iframe fails after auth.
		await expect(pending).rejects.toThrow();
		expect(auth.popup).not.toHaveBeenCalled();
	});
});

it('business prefetch preserves personal authority and caches separate MSAL clients', async () => {
	vi.resetModules();
	auth.silent.mockReset();
	vi.stubGlobal('window', { location: { origin: 'http://localhost' } });
	vi.stubGlobal('fetch', stubFetch('https://static.sharepoint.com'));
	const picker = await import('./onedrive-file-picker');
	const config = picker.OneDriveConfig.getInstance();
	await config.initialize('personal');
	await picker.prepareBusinessDocumentPicker();
	expect(config.getAuthorityType()).toBe('personal');
	const [business, personal, businessAgain] = await Promise.all([
		config.getMsalInstance('organizations'),
		config.getMsalInstance('personal'),
		config.getMsalInstance('organizations')
	]);
	expect(business).not.toBe(personal);
	expect(business).toBe(businessAgain);
});
