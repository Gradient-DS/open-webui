import { describe, expect, it } from 'vitest';

import { audioCapabilities } from './audio';
import { dayjsLocale } from './meeting';

const display = { getDisplayMedia: () => undefined };
const CHROME_MAC =
	'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36';
const EDGE_WIN =
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 Edg/129.0';
const FIREFOX_WIN =
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0';
const SAFARI_MAC =
	'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15';
const IPHONE =
	'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const CHROME_ANDROID =
	'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36';

describe('audio capabilities', () => {
	it('offers tab audio on desktop Chromium only', () => {
		expect(audioCapabilities({ userAgent: CHROME_MAC, mediaDevices: display })).toEqual({
			tabAudio: true,
			mobile: false,
			windows: false
		});
		expect(audioCapabilities({ userAgent: EDGE_WIN, mediaDevices: display })).toEqual({
			tabAudio: true,
			mobile: false,
			windows: true
		});
		expect(audioCapabilities({ userAgent: FIREFOX_WIN, mediaDevices: display }).tabAudio).toBe(
			false
		);
		expect(audioCapabilities({ userAgent: SAFARI_MAC, mediaDevices: display }).tabAudio).toBe(
			false
		);
	});

	it('treats phones and iPads as mobile without tab audio', () => {
		expect(audioCapabilities({ userAgent: IPHONE, mediaDevices: {} })).toMatchObject({
			tabAudio: false,
			mobile: true
		});
		expect(audioCapabilities({ userAgent: CHROME_ANDROID, mediaDevices: display })).toMatchObject({
			tabAudio: false,
			mobile: true
		});
		const iPad = { userAgent: SAFARI_MAC, maxTouchPoints: 5, mediaDevices: display };
		expect(audioCapabilities(iPad)).toMatchObject({ tabAudio: false, mobile: true });
	});

	it('prefers client hints over the user agent', () => {
		const hinted = {
			userAgent: FIREFOX_WIN,
			mediaDevices: display,
			userAgentData: { mobile: false, platform: 'macOS', brands: [{ brand: 'Chromium' }] }
		};
		expect(audioCapabilities(hinted)).toEqual({ tabAudio: true, mobile: false, windows: false });
		const phone = { ...hinted, userAgentData: { ...hinted.userAgentData, mobile: true } };
		expect(audioCapabilities(phone).tabAudio).toBe(false);
	});

	it('needs getDisplayMedia, and is safe without a navigator', () => {
		expect(audioCapabilities({ userAgent: CHROME_MAC, mediaDevices: {} }).tabAudio).toBe(false);
		expect(audioCapabilities(undefined)).toEqual({
			tabAudio: false,
			mobile: false,
			windows: false
		});
	});
});

describe('dayjs locale', () => {
	it('picks the first UI language dayjs has, falling back to its base and to English', () => {
		const loaded = { en: {}, nl: {}, 'en-gb': {} };
		expect(dayjsLocale(['nl-NL', 'nl', 'en'], loaded)).toBe('nl');
		expect(dayjsLocale(['en-GB'], loaded)).toBe('en-gb');
		expect(dayjsLocale(['xx-YY'], loaded)).toBe('en');
		expect(dayjsLocale(undefined, loaded)).toBe('en');
	});
});
