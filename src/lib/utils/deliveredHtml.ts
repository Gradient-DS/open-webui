// [Gradient] A workspace HTML page runs its own scripts in the document panel, cut off from the network and
// from this origin: the frame has an opaque origin and the policy below allows only what the page carries inline.
export const DELIVERED_HTML_CSP = [
	"default-src 'none'",
	"script-src 'unsafe-inline' 'unsafe-eval'",
	"style-src 'unsafe-inline'",
	'img-src data: blob:',
	'font-src data:',
	'media-src data: blob:',
	"connect-src 'none'",
	"form-action 'none'",
	"base-uri 'none'"
].join('; ');

/** Scripts only: no same-origin, forms, popups, modals or top navigation. */
export const DELIVERED_HTML_SANDBOX = 'allow-scripts';

/** The page with the policy as the first element of its head; its own http-equiv metas (a refresh that would
 * navigate away, a policy of its own) are removed. */
export function deliveredHtmlDocument(content: string): string {
	const doc = new DOMParser().parseFromString(content, 'text/html');
	for (const meta of doc.querySelectorAll('meta[http-equiv]')) meta.remove();
	const csp = doc.createElement('meta');
	csp.httpEquiv = 'Content-Security-Policy';
	csp.content = DELIVERED_HTML_CSP;
	doc.head.prepend(csp);
	return `<!doctype html>\n${doc.documentElement.outerHTML}`;
}
