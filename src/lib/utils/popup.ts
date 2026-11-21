import { get } from 'svelte/store';
import i18n from '$lib/i18n';

/** window.open features for a width x height window centered over the current browser window. */
export function centeredPopupFeatures(width: number, height: number): string {
	const left = Math.round(window.screenX + (window.outerWidth - width) / 2);
	const top = Math.round(window.screenY + (window.outerHeight - height) / 2);
	return `width=${width},height=${height},left=${left},top=${top}`;
}

const escapeHtml = (value: string) =>
	value.replace(
		/[&<>"']/g,
		(char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!
	);

// Keep in step with the relay page in backend/open_webui/soev/connect_page.py.
const STYLE = `
:root{color-scheme:light dark;--bg:#fff;--fg:#171717;--muted:#6b6b6b;--track:#e5e5e5}
@media (prefers-color-scheme:dark){:root:not(.light){--bg:#171717;--fg:#ececec;--muted:#9b9b9b;--track:#333}}
:root.dark{--bg:#171717;--fg:#ececec;--muted:#9b9b9b;--track:#333}
html,body{height:100%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;text-align:center}
main{padding:24px;max-width:320px}
.spinner{width:28px;height:28px;margin:0 auto 20px;border-radius:50%;border:3px solid var(--track);
border-top-color:var(--fg);animation:spin .9s linear infinite}
h1{font-size:16px;font-weight:500;margin:0 0 4px}
p{margin:0;font-size:13px;color:var(--muted)}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spinner{animation:none;border-color:var(--track);border-top-color:var(--fg)}}
`;

/** The loading page shown while the authorize URL is fetched; fixed text only, no external resources. */
export function connectingDocument(provider: string, theme: 'dark' | 'light' | null): string {
	const { t, language } = get(i18n);
	const heading = escapeHtml(t('Connecting to {{provider}}…', { provider }));
	return (
		`<!DOCTYPE html><html lang="${escapeHtml(language || 'en')}"${theme ? ` class="${theme}"` : ''}>` +
		`<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
		`<title>${heading}</title><style>${STYLE}</style></head>` +
		`<body><main role="status" aria-live="polite"><div class="spinner" aria-hidden="true"></div>` +
		`<h1>${heading}</h1><p>${escapeHtml(t('The sign-in page opens in a moment.'))}</p></main></body></html>`
	);
}

/**
 * Open the consent popup in the click handler (keeping the user gesture) and show a loading page
 * until the caller navigates it to the authorize URL; null when the browser blocked it.
 */
export function openConnectPopup(name: string, provider: string): Window | null {
	const popup = window.open('about:blank', name, centeredPopupFeatures(600, 720));
	if (!popup) return null;
	const classes = window.document.documentElement.classList;
	const theme = classes.contains('dark') ? 'dark' : classes.contains('light') ? 'light' : null;
	try {
		popup.document.open();
		popup.document.write(connectingDocument(provider, theme));
		popup.document.close();
	} catch {
		// A reused window still on another origin: it navigates to the authorize URL regardless.
	}
	return popup;
}
