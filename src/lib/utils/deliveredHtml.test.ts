// @vitest-environment jsdom
import { JSDOM } from 'jsdom';
import { describe, expect, it, vi } from 'vitest';

import {
	DELIVERED_HTML_CSP,
	GUARD,
	LEFT,
	deliveredHtmlDocument,
	hasScripts,
	isLeaving,
	loadWatcher,
	runScripts,
	runsScripts
} from './deliveredHtml';

const parse = (html: string) => new DOMParser().parseFromString(html, 'text/html');

describe('deliveredHtmlDocument', () => {
	it('puts the policy, no-referrer and no DNS prefetching first in the head and keeps the scripts', () => {
		const doc = parse(
			deliveredHtmlDocument(
				'<html><head><title>Chart</title><script>window.x = 1</script></head><body><div id="c"></div></body></html>',
				false
			)
		);
		const [policy, referrer, prefetch] = doc.head.children as unknown as HTMLMetaElement[];
		expect(policy.httpEquiv).toBe('Content-Security-Policy');
		expect(policy.content).toBe(DELIVERED_HTML_CSP);
		expect([referrer.name, referrer.content]).toEqual(['referrer', 'no-referrer']);
		expect([prefetch.httpEquiv, prefetch.content]).toEqual(['x-dns-prefetch-control', 'off']);
		expect(doc.querySelector('script')?.textContent).toBe('window.x = 1');
		expect(doc.getElementById('c')).not.toBeNull();
	});

	it('adds the guard as the first script only for a page that may run', () => {
		const html = '<head><script>window.x = 1</script></head><body></body>';
		expect(parse(deliveredHtmlDocument(html, false)).scripts).toHaveLength(1);
		const scripts = parse(deliveredHtmlDocument(html, true)).scripts;
		expect(scripts).toHaveLength(2);
		expect(scripts[0].textContent).toBe(GUARD);
		expect(scripts[0].previousElementSibling?.getAttribute('http-equiv')).toBe('x-dns-prefetch-control');
	});

	it("drops the page's own refresh, policy, referrer, base, frames and prefetching", () => {
		const doc = parse(
			deliveredHtmlDocument(
				'<head><meta http-equiv="refresh" content="0;url=https://example.com/?q=1"><meta http-equiv="Content-Security-Policy" content="script-src *"><meta name="referrer" content="unsafe-url"><base href="https://example.com/"><link rel="dns-prefetch" href="//leak.example.com"><link rel="preconnect" href="https://example.com"><link rel="Prefetch" href="https://example.com/a"><link rel="stylesheet" href="https://example.com/s.css"></head><body><iframe src="about:blank"></iframe><object data="x"></object><embed src="x"></body>',
				true
			)
		);
		expect([...doc.querySelectorAll('meta[http-equiv]')].map((m) => m.getAttribute('http-equiv'))).toEqual([
			'Content-Security-Policy',
			'x-dns-prefetch-control'
		]);
		expect(doc.querySelectorAll('meta[name="referrer"]')).toHaveLength(1);
		expect(doc.querySelector('base, iframe, object, embed')).toBeNull();
		expect([...doc.querySelectorAll('link')].map((l) => l.rel)).toEqual(['stylesheet']);
	});

	it('removes every link target from a page without scripts, and all but in-page ones from a running page', () => {
		const html =
			'<body><a id="out" href="https://example.com/?d=secret">out</a><a id="in" href="#total">in</a><map><area id="area" href="https://example.com/"></map><svg><a id="svg" xlink:href="https://example.com/"><text>s</text></a></svg></body>';
		const still = parse(deliveredHtmlDocument(html, false));
		expect(still.querySelectorAll('[href]')).toHaveLength(0);
		expect(still.getElementById('svg')?.getAttribute('xlink:href')).toBeNull();
		const running = parse(deliveredHtmlDocument(html, true));
		expect([...running.querySelectorAll('[href]')].map((a) => a.id)).toEqual(['in']);
		expect(running.getElementById('svg')?.getAttribute('xlink:href')).toBeNull();
	});

	it('gives a fragment a head with the policy', () => {
		const doc = parse(deliveredHtmlDocument('<p>Only a paragraph</p>', false));
		expect(doc.head.querySelector('meta')).not.toBeNull();
		expect(doc.body.textContent).toBe('Only a paragraph');
	});

	it('never lets the page reach the network or nest a frame', () => {
		for (const directive of [
			"default-src 'none'",
			"connect-src 'none'",
			"frame-src 'none'",
			"child-src 'none'",
			"worker-src 'none'",
			"form-action 'none'",
			"base-uri 'none'"
		]) {
			expect(DELIVERED_HTML_CSP).toContain(directive);
		}
		expect(DELIVERED_HTML_CSP).not.toMatch(/https?:|\*/);
	});
});

describe('hasScripts', () => {
	it.each([
		['<script>1</script>', true],
		['<img src="data:," onerror="1">', true],
		['<svg><a href="javascript:void 0"><text>x</text></a></svg>', true],
		['<svg onload="1"></svg>', true],
		['<p>Plain <b>chart</b></p><svg><rect width="1" height="1"/></svg>', false]
	])('%s runs: %s', (html, expected) => {
		expect(hasScripts(html)).toBe(expected);
	});
});

describe('leaving the panel', () => {
	it('knows only the shown frame saying it leaves', () => {
		const frame = {} as Window;
		const message = (source: unknown, data: unknown) => ({ source, data }) as MessageEvent;
		expect(isLeaving(message(frame, LEFT), frame)).toBe(true);
		expect(isLeaving(message({}, LEFT), frame)).toBe(false);
		expect(isLeaving(message(frame, 'other'), frame)).toBe(false);
		expect(isLeaving(message(null, LEFT), null)).toBe(false);
	});

	it('takes every load after the first as a navigation', () => {
		const watcher = loadWatcher();
		expect(watcher.navigated()).toBe(false);
		expect(watcher.navigated()).toBe(true);
		expect(watcher.navigated()).toBe(true);
	});

	it('remembers per delivered file that the user started it', () => {
		expect(runsScripts('element-a')).toBe(false);
		runScripts('element-a');
		expect(runsScripts('element-a')).toBe(true);
		expect(runsScripts('element-b')).toBe(false);
	});
});

describe('the guard', () => {
	const page = (body: string) => {
		const dom = new JSDOM(deliveredHtmlDocument(`<body>${body}</body>`, true), {
			runScripts: 'dangerously',
			beforeParse(window) {
				Object.assign(window, {
					RTCPeerConnection: class {},
					webkitRTCPeerConnection: class {},
					RTCDataChannel: class {}
				});
				window.Element.prototype.scrollIntoView = vi.fn();
			}
		});
		return dom.window;
	};
	const click = (window: Window, id: string) => {
		const event = new window.MouseEvent('click', { bubbles: true, cancelable: true });
		window.document.getElementById(id)?.dispatchEvent(event);
		return event;
	};

	it('removes itself and WebRTC before the page runs', () => {
		const window = page('<script>window.seen = [typeof RTCPeerConnection, document.scripts.length]</script>');
		expect((window as unknown as { seen: unknown }).seen).toEqual(['undefined', 1]);
		expect('webkitRTCPeerConnection' in window || 'RTCDataChannel' in window).toBe(false);
	});

	it('keeps clicks on links inside the page and scrolls to in-page targets', () => {
		const window = page(
			'<a id="in" href="#total">in</a><p id="total">42</p><span id="plain">x</span><script>const a = document.createElement("a"); a.id = "made"; a.href = "https://example.com/?d=1"; document.body.append(a)</script>'
		);
		expect(click(window, 'made').defaultPrevented).toBe(true);
		expect(click(window, 'in').defaultPrevented).toBe(true);
		expect(window.document.getElementById('total')?.scrollIntoView).toHaveBeenCalled();
		expect(click(window, 'plain').defaultPrevented).toBe(false);
	});

	it('hands out no nested window and drops frames the page adds', async () => {
		const window = page(
			'<script>const f = document.createElement("iframe"); document.body.append(f); window.nested = f.contentWindow; window.frame = f</script>'
		);
		const seen = window as unknown as { nested: unknown; frame: HTMLIFrameElement };
		expect(seen.nested).toBeNull();
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(seen.frame.isConnected).toBe(false);
	});

	it('tells the panel when the page starts to leave', async () => {
		const window = page('');
		const messages: unknown[] = [];
		window.addEventListener('message', (event) => messages.push(event.data));
		window.dispatchEvent(new window.Event('beforeunload'));
		window.dispatchEvent(new window.Event('pagehide'));
		await new Promise((resolve) => setTimeout(resolve, 10));
		expect(messages).toEqual([LEFT, LEFT]);
	});
});
