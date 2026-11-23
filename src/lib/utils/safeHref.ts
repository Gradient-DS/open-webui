// [Gradient] Markdown links come from model answers, web results, files and outside sources such as council
// documents. marked keeps any scheme, so `[x](javascript:...)` would become a link that runs script on a click.
// A link keeps only a scheme that navigates: anything else renders as its text.

const SCHEMES = new Set(['http:', 'https:', 'mailto:']);

// Only resolves relative links; never shown or fetched.
const BASE = 'https://relative.invalid/';

/** The href when it is a web or mail link or a relative one, else `null`. Parsed as the browser parses it, so tabs,
 * newlines, leading control characters and case cannot hide a scheme. */
export function safeHref(href: string | null | undefined): string | null {
	if (typeof href !== 'string' || !href.trim()) return null;
	try {
		return SCHEMES.has(new URL(href, BASE).protocol) ? href : null;
	} catch {
		return null;
	}
}
