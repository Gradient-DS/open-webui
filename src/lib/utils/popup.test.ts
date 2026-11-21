import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { openConnectPopup } from './popup';

vi.mock('$lib/i18n', async () => ({
	default: (await import('svelte/store')).readable({
		language: 'nl-NL',
		t: (key: string, options?: { provider?: string }) =>
			({
				'Connecting to {{provider}}…': `Verbinden met ${options?.provider}…`,
				'The sign-in page opens in a moment.': 'De aanmeldpagina opent zo.'
			})[key] ?? key
	})
}));

let written: string;
let classes: string[];
const popup = {
	location: { href: '' },
	document: {
		open: vi.fn(),
		write: vi.fn((html: string) => (written = html)),
		close: vi.fn()
	}
};
beforeEach(() => {
	written = '';
	classes = [];
	popup.document.write.mockImplementation((html: string) => (written = html));
	vi.stubGlobal('window', {
		open: vi.fn(() => popup),
		screenX: 100,
		screenY: 50,
		outerWidth: 1400,
		outerHeight: 900,
		document: {
			documentElement: { classList: { contains: (name: string) => classes.includes(name) } }
		}
	});
});
afterEach(() => vi.unstubAllGlobals());

it('opens a centred popup and writes a translated loading page into it', () => {
	expect(openConnectPopup('soev_connect', 'OneDrive')).toBe(popup);
	expect(window.open).toHaveBeenCalledWith(
		'about:blank',
		'soev_connect',
		'width=600,height=720,left=500,top=140'
	);
	expect(popup.document.close).toHaveBeenCalled();
	expect(written).toContain('<html lang="nl-NL">');
	expect(written).toContain('<title>Verbinden met OneDrive…</title>');
	expect(written).toContain('<h1>Verbinden met OneDrive…</h1>');
	expect(written).toContain('De aanmeldpagina opent zo.');
	expect(written).toContain('prefers-color-scheme:dark');
	expect(written).toContain('prefers-reduced-motion:reduce');
	expect(popup.location.href).toBe('');
});

it('loads nothing from elsewhere', () => {
	openConnectPopup('soev_connect', 'OneDrive');
	expect(written).not.toMatch(/https?:|\/\/|<script|<link|<img|src=|href=|url\(|@import/i);
});

it('follows the app theme and escapes the provider name', () => {
	classes = ['dark'];
	openConnectPopup('soev_connect', '<b>"Drive"</b>');
	expect(written).toContain('<html lang="nl-NL" class="dark">');
	expect(written).toContain('Verbinden met &lt;b&gt;&quot;Drive&quot;&lt;/b&gt;…');
	expect(written).not.toContain('<b>');
});

it('returns null without writing when the popup is blocked', () => {
	vi.mocked(window.open).mockReturnValue(null);
	expect(openConnectPopup('soev_connect', 'OneDrive')).toBeNull();
	expect(written).toBe('');
});

it('still returns the popup when its document cannot be written', () => {
	popup.document.write.mockImplementation(() => {
		throw new DOMException('Blocked', 'SecurityError');
	});
	expect(openConnectPopup('soev_connect', 'OneDrive')).toBe(popup);
});
