import { describe, it, expect, vi } from 'vitest';
import { parseSharepointHostFromWebUrl, fetchOneDriveHost } from './onedrive-host';

describe('parseSharepointHostFromWebUrl', () => {
	it('derives the -my host from a personal OneDrive webUrl', () => {
		expect(
			parseSharepointHostFromWebUrl(
				'https://contoso-my.sharepoint.com/personal/u_contoso_com/Documents'
			)
		).toBe('https://contoso-my.sharepoint.com');
	});

	it('strips query strings', () => {
		expect(parseSharepointHostFromWebUrl('https://x-my.sharepoint.com/a?b=c')).toBe(
			'https://x-my.sharepoint.com'
		);
	});

	it('upgrades http to https', () => {
		expect(parseSharepointHostFromWebUrl('http://x-my.sharepoint.com/a')).toBe(
			'https://x-my.sharepoint.com'
		);
	});

	it('throws on missing or unparseable webUrl', () => {
		expect(() => parseSharepointHostFromWebUrl(undefined)).toThrow();
		expect(() => parseSharepointHostFromWebUrl('')).toThrow();
		expect(() => parseSharepointHostFromWebUrl('not a url')).toThrow();
	});
});

describe('fetchOneDriveHost', () => {
	const ok = (webUrl: unknown) =>
		({ ok: true, status: 200, json: async () => ({ webUrl }) }) as unknown as Response;

	it('returns the host origin on 200 and calls /me/drive with the bearer token', async () => {
		const f = vi
			.fn()
			.mockResolvedValue(ok('https://contoso-my.sharepoint.com/personal/u/Documents'));
		await expect(fetchOneDriveHost('tok', f)).resolves.toBe('https://contoso-my.sharepoint.com');
		expect(f).toHaveBeenCalledWith(
			'https://graph.microsoft.com/v1.0/me/drive',
			expect.objectContaining({ headers: { Authorization: 'Bearer tok' } })
		);
	});

	it('throws a clear error on 404 (no OneDrive provisioned)', async () => {
		const f = vi.fn().mockResolvedValue({ ok: false, status: 404 } as Response);
		await expect(fetchOneDriveHost('tok', f)).rejects.toThrow(
			'No OneDrive found for your account.'
		);
	});

	it('throws on other non-ok responses', async () => {
		const f = vi.fn().mockResolvedValue({ ok: false, status: 500 } as Response);
		await expect(fetchOneDriveHost('tok', f)).rejects.toThrow(
			'Could not connect to OneDrive to determine your location.'
		);
	});

	it('throws when webUrl is missing in the payload', async () => {
		const f = vi.fn().mockResolvedValue(ok(undefined));
		await expect(fetchOneDriveHost('tok', f)).rejects.toThrow();
	});
});
