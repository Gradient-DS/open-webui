import DOMPurify, { type Config } from 'dompurify';

export const DOCUMENT_CSP =
	"default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:";
export const DOCUMENT_SANDBOX = 'allow-same-origin allow-modals';
export const DOCUMENT_PURIFY_CONFIG: Config = {
	WHOLE_DOCUMENT: true,
	RETURN_DOM: true,
	USE_PROFILES: { html: true },
	ADD_TAGS: ['style'],
	ADD_ATTR: ['style'],
	FORBID_TAGS: [
		'script',
		'iframe',
		'frame',
		'object',
		'embed',
		'link',
		'meta',
		'base',
		'form',
		'input',
		'button',
		'textarea',
		'select',
		'img',
		'picture',
		'source',
		'video',
		'audio',
		'use',
		'image',
		'foreignObject'
	],
	FORBID_ATTR: [
		'src',
		'srcset',
		'action',
		'formaction',
		'poster',
		'background',
		'ping',
		'target',
		'download',
		'xlink:href'
	]
};

const resourceCSS = (value: string): boolean => {
	const normalized = value
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/\\([0-9a-f]{1,6})\s?|\\([^\r\n])/gi, (_, hex, char) =>
			hex ? String.fromCodePoint(Math.min(parseInt(hex, 16), 0x10ffff)) : char
		);
	return /url\s*\(|@import/i.test(normalized);
};

// [Gradient] Drop resource-bearing CSS wholesale, including escaped/comment-obfuscated
// url() and @import; ordinary styles and @page survive. CSP also blocks other load syntaxes.
export function sanitizeDocumentHtml(content: string, title: string): string {
	const root = DOMPurify.sanitize(content, DOCUMENT_PURIFY_CONFIG) as unknown as HTMLElement;
	for (const element of [root, ...Array.from(root.querySelectorAll('*'))]) {
		for (const attribute of Array.from(element.attributes)) {
			if (
				/^on/i.test(attribute.name) ||
				resourceCSS(attribute.value) ||
				(attribute.name === 'href' && !attribute.value.startsWith('#'))
			)
				element.removeAttribute(attribute.name);
		}
		if (element.tagName === 'STYLE' && resourceCSS(element.textContent ?? '')) element.remove();
	}
	const doc = root.ownerDocument;
	const head = root.querySelector('head')!;
	for (const oldTitle of root.querySelectorAll('title')) oldTitle.remove();
	const csp = doc.createElement('meta');
	csp.httpEquiv = 'Content-Security-Policy';
	csp.content = DOCUMENT_CSP;
	head.prepend(csp);
	const titleElement = doc.createElement('title');
	titleElement.textContent = title;
	csp.after(titleElement);
	return `<!doctype html>\n${root.outerHTML}`;
}
