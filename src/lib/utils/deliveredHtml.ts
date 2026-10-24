// [Gradient] A workspace HTML page is written by a model that may have read attacker text, so the panel treats it as
// hostile: an opaque origin (no allow-same-origin), no network (the policy below), no forms, popups, downloads or top
// navigation (the sandbox), and scripts only after the user asks for them. What no browser offers is a way to stop a
// frame navigating itself (CSP navigate-to never shipped), so a running page can still send one request by leaving;
// the guard and the frame catch that and blank the panel. docs/fork-delta.md has the per-browser account.
export const DELIVERED_HTML_CSP = [
	"default-src 'none'",
	"script-src 'unsafe-inline' 'unsafe-eval'",
	"style-src 'unsafe-inline'",
	'img-src data: blob:',
	'font-src data:',
	'media-src data: blob:',
	"connect-src 'none'",
	"frame-src 'none'",
	"child-src 'none'",
	"worker-src 'none'",
	"manifest-src 'none'",
	"object-src 'none'",
	"form-action 'none'",
	"base-uri 'none'",
	"webrtc 'block'"
].join('; ');

/** Without scripts: the page as a picture of itself. */
export const STATIC_SANDBOX = '';
/** Scripts only: no same-origin, forms, popups, modals, downloads or top navigation. */
export const SCRIPTED_SANDBOX = 'allow-scripts';

/** The message the guard posts to the panel when the page starts to leave. */
export const LEFT = 'soev:delivered-html:left';

const NAVIGATING = 'a, area';
const FRAMES = 'iframe, frame, frameset, object, embed, portal, fencedframe';
const DROPPED = [
	FRAMES,
	'base',
	'meta[http-equiv]',
	'meta[name="referrer" i]',
	...['dns-prefetch', 'preconnect', 'prefetch', 'prerender', 'preload', 'modulepreload'].map(
		(rel) => `link[rel~="${rel}" i]`
	)
].join(', ');

/** The first script of a running page. It cannot stop `location = …` (nothing can), but it tells the panel the
 * moment the page begins to leave, keeps link clicks inside the page, and removes WebRTC, the one network path the
 * policy does not cover, together with the nested frames that could hand out a fresh copy of it. */
export const GUARD = `(() => {
	// The panel is cross-origin to the page, so its postMessage cannot be replaced from in here.
	const panel = parent, prevent = Event.prototype.preventDefault, closest = Element.prototype.closest;
	const attribute = Element.prototype.getAttribute, byId = Document.prototype.getElementById;
	const left = () => panel.postMessage(${JSON.stringify(LEFT)}, '*');
	addEventListener('beforeunload', left);
	addEventListener('pagehide', left);
	// A link never follows: about:srcdoc#part is a new document too, so in-page links scroll here instead.
	const follow = (event) => {
		const link = event.target instanceof Element ? closest.call(event.target, 'a, area') : null;
		const href = link && (attribute.call(link, 'href') ?? attribute.call(link, 'xlink:href'));
		if (typeof href !== 'string') return;
		if (event.type === 'keydown' && event.key !== 'Enter') return;
		prevent.call(event);
		const part = href.trim().startsWith('#') ? byId.call(document, decodeURIComponent(href.trim().slice(1))) : null;
		if (event.type !== 'auxclick' && part) part.scrollIntoView();
	};
	for (const type of ['click', 'auxclick', 'keydown']) addEventListener(type, follow, true);
	addEventListener('submit', (event) => prevent.call(event), true);
	for (const name of Object.getOwnPropertyNames(window)) {
		if (/^(webkit|moz)?RTC/.test(name)) delete window[name];
	}
	window.open = () => null;
	for (const name of ['HTMLIFrameElement', 'HTMLFrameElement', 'HTMLObjectElement', 'HTMLEmbedElement']) {
		const proto = window[name] && window[name].prototype;
		if (!proto) continue;
		for (const key of ['contentWindow', 'contentDocument']) {
			if (key in proto) Object.defineProperty(proto, key, { get: () => null });
		}
		if ('getSVGDocument' in proto) Object.defineProperty(proto, 'getSVGDocument', { value: () => null });
	}
	const dropped = ${JSON.stringify(DROPPED)};
	new MutationObserver((records) => {
		for (const record of records) for (const node of record.addedNodes) {
			if (!(node instanceof Element)) continue;
			if (node.matches(dropped)) node.remove();
			for (const inner of node.querySelectorAll(dropped)) inner.remove();
		}
	}).observe(document, { childList: true, subtree: true });
	document.currentScript && document.currentScript.remove();
})();`;

const meta = (doc: Document, attribute: 'http-equiv' | 'name', key: string, content: string) => {
	const element = doc.createElement('meta');
	element.setAttribute(attribute, key);
	element.setAttribute('content', content);
	return element;
};

/** The page as the panel shows it: the policy first in its head, no referrer, no DNS prefetching, nothing that loads
 * or navigates (its own refresh or policy metas, frames, prefetch links, link targets; a running page keeps its
 * in-page ones for the guard), and, when it may run scripts, the guard as its first script. */
export function deliveredHtmlDocument(content: string, scripts: boolean): string {
	const doc = new DOMParser().parseFromString(content, 'text/html');
	for (const element of doc.querySelectorAll(DROPPED)) element.remove();
	// Even an in-page link loads a new document in a srcdoc frame; only the guard can turn it into a scroll.
	for (const element of doc.querySelectorAll(NAVIGATING)) {
		for (const name of ['href', 'xlink:href']) {
			const href = element.getAttribute(name);
			if (href !== null && !(scripts && href.trim().startsWith('#'))) element.removeAttribute(name);
		}
	}
	const head: Element[] = [
		meta(doc, 'http-equiv', 'Content-Security-Policy', DELIVERED_HTML_CSP),
		meta(doc, 'name', 'referrer', 'no-referrer'),
		meta(doc, 'http-equiv', 'x-dns-prefetch-control', 'off')
	];
	if (scripts) {
		const guard = doc.createElement('script');
		guard.textContent = GUARD;
		head.push(guard);
	}
	doc.head.prepend(...head);
	return `<!doctype html>\n${doc.documentElement.outerHTML}`;
}

/** Whether the page has anything to run: a script, an inline handler or a javascript: URL. */
export function hasScripts(content: string): boolean {
	const doc = new DOMParser().parseFromString(content, 'text/html');
	if (doc.querySelector('script')) return true;
	for (const element of doc.querySelectorAll('*')) {
		for (const { name, value } of element.attributes) {
			if (name.toLowerCase().startsWith('on') || /^\s*javascript:/i.test(value)) return true;
		}
	}
	return false;
}

/** Whether a message is the shown page saying it starts to leave. Any document in the frame may send it, which is
 * fine: it only ever blanks the panel. */
export const isLeaving = (event: MessageEvent, frame: Window | null | undefined): boolean =>
	!!frame && event.source === frame && event.data === LEFT;

/** Counts the frame's loads: a srcdoc frame loads once, so any later load is a document it navigated to. */
export const loadWatcher = () => {
	let loads = 0;
	return { navigated: (): boolean => ++loads > 1 };
};

// Pages the user chose to run, by delivered element, until the app reloads.
const running = new Set<string>();
export const runsScripts = (elementId: string): boolean => running.has(elementId);
export const runScripts = (elementId: string): void => {
	running.add(elementId);
};
