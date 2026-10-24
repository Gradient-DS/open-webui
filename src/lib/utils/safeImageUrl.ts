import { WEBUI_BASE_URL } from '$lib/constants';

// LICENSE covers this Open WebUI fallback logo.
// Do not alter, remove, obscure, or replace it except as LICENSE permits:
// https://docs.openwebui.com/license.
const PLACEHOLDER_IMAGE = '/favicon.png';

/**
 * Validates an image URL against an allowlist of safe patterns and returns
 * the URL if trusted, or a placeholder otherwise.
 *
 * Allowed patterns:
 *   - Relative paths (starting with '/', but not '//' or '/\\')
 *   - data:image/* URIs
 *   - Same-origin URLs (starting with WEBUI_BASE_URL)
 *   - Gravatar URLs (https://www.gravatar.com/avatar/)
 *   - External HTTP(S) URLs when allowExternal is true
 *
 * All other URLs (including arbitrary http(s):// origins by default) are
 * rejected to prevent client-side IP/UA/Referer leaks to attacker-controlled servers.
 */
export function safeImageUrl(url: string, allowExternal = false): string {
	if (isSafeImageUrl(url, allowExternal)) {
		return url;
	}

	return `${WEBUI_BASE_URL}${PLACEHOLDER_IMAGE}`;
}

/**
 * [Gradient] True when safeImageUrl would pass the URL through rather than
 * substitute the placeholder, so content images can tell "rejected" apart
 * from a real picture.
 */
export function isSafeImageUrl(url: string, allowExternal = false): boolean {
	if (!url) {
		return false;
	}

	return (
		(!!WEBUI_BASE_URL && url.startsWith(`${WEBUI_BASE_URL}/`)) ||
		url.startsWith('https://www.gravatar.com/avatar/') ||
		(allowExternal && /^https?:\/\//i.test(url)) ||
		url.startsWith('data:') ||
		isOwnPath(url)
	);
}

/**
 * [Gradient] A path on this origin. Browsers drop tabs and newlines from URLs and read `//host` and `/\host`
 * as another host, so those are not paths.
 */
const isOwnPath = (url: string): boolean => /^\/(?![\/\\])/.test(url) && !/[\t\n\r]/.test(url);

/**
 * [Gradient] The host an external image would be fetched from, or null for our own and inline images. Model
 * output (markdown images, embedded media) shows such an image only after a click, since loading it would hand
 * whatever the URL carries to that host.
 */
export function externalHost(url: string): string | null {
	if (
		!url ||
		url.startsWith('data:') ||
		(!!WEBUI_BASE_URL && url.startsWith(`${WEBUI_BASE_URL}/`)) ||
		isOwnPath(url)
	) {
		return null;
	}
	try {
		const parsed = new URL(url, window.location.href);
		if (!['http:', 'https:'].includes(parsed.protocol) || parsed.origin === window.location.origin)
			return null;
		return parsed.host;
	} catch {
		return null;
	}
}
