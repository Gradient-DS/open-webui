// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import { DELIVERED_HTML_CSP, deliveredHtmlDocument } from './deliveredHtml';

const parse = (html: string) => new DOMParser().parseFromString(html, 'text/html');

describe('deliveredHtmlDocument', () => {
	it('puts the policy first in the head and keeps the scripts', () => {
		const doc = parse(
			deliveredHtmlDocument(
				'<html><head><title>Chart</title><script>window.x = 1</script></head><body><div id="c"></div></body></html>'
			)
		);
		const first = doc.head.firstElementChild as HTMLMetaElement;
		expect(first.httpEquiv).toBe('Content-Security-Policy');
		expect(first.content).toBe(DELIVERED_HTML_CSP);
		expect(doc.querySelector('script')?.textContent).toBe('window.x = 1');
		expect(doc.getElementById('c')).not.toBeNull();
	});

	it("drops the page's own refresh and policy", () => {
		const doc = parse(
			deliveredHtmlDocument(
				'<head><meta http-equiv="refresh" content="0;url=https://example.com/?q=1"><meta http-equiv="Content-Security-Policy" content="script-src *"></head><body>x</body>'
			)
		);
		const metas = doc.querySelectorAll('meta[http-equiv]');
		expect(metas).toHaveLength(1);
		expect((metas[0] as HTMLMetaElement).content).toBe(DELIVERED_HTML_CSP);
	});

	it('gives a fragment a head with the policy', () => {
		const doc = parse(deliveredHtmlDocument('<p>Only a paragraph</p>'));
		expect(doc.head.querySelector('meta')).not.toBeNull();
		expect(doc.body.textContent).toBe('Only a paragraph');
	});

	it('never lets the page reach the network', () => {
		expect(DELIVERED_HTML_CSP).toContain("default-src 'none'");
		expect(DELIVERED_HTML_CSP).toContain("connect-src 'none'");
		expect(DELIVERED_HTML_CSP).not.toMatch(/https?:|\*/);
	});
});
