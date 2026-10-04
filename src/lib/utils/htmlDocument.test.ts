// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { DOCUMENT_CSP, sanitizeDocumentHtml } from './htmlDocument';

const parse = (content: string, title = 'Report') =>
	new DOMParser().parseFromString(sanitizeDocumentHtml(content, title), 'text/html');

describe('HTML document security boundary', () => {
	it.each([
		'<script>alert(1)</script>',
		'<img src="http://evil.test/leak" onerror="alert(1)">',
		'<link rel="stylesheet" href="http://evil.test/style">',
		'<meta http-equiv="refresh" content="0;url=http://evil.test">',
		'<base href="http://evil.test">',
		'<iframe src="http://evil.test"></iframe>',
		'<svg><image href="http://evil.test"/><use href="http://evil.test"/><foreignObject>bad</foreignObject></svg>',
		'<form action="http://evil.test"><input><button>Send</button><textarea></textarea><select></select></form>',
		'<object data="http://evil.test"></object><embed src="http://evil.test">',
		'<picture><source srcset="http://evil.test"><img src="http://evil.test"></picture><video poster="http://evil.test"></video><audio src="http://evil.test"></audio>'
	])('removes active/loading elements: %s', (hostile) => {
		const doc = parse(hostile);
		expect(
			doc.querySelector(
				'script,img,link,base,iframe,svg,form,input,button,textarea,select,object,embed,picture,source,video,audio'
			)
		).toBeNull();
		expect(doc.querySelectorAll('meta')).toHaveLength(1);
		expect(doc.documentElement.outerHTML).not.toContain('evil.test');
	});
	it.each([
		'@import url(http://evil.test/style); p { color: red }',
		'@import "http://evil.test/style";',
		'p { background: url(http://evil.test/leak) }',
		String.raw`p { background: u\72l(http://evil.test/leak) }`,
		'p { background: u/**/rl(http://evil.test/leak) }',
		String.raw`@\69mport "http://evil.test/style";`
	])('strips CSS loads: %s', (css) => {
		const doc = parse(`<style>${css}</style><p style='${css}' data-load='${css}'>Safe</p>`);
		expect(doc.querySelector('style:not([data-page-boxes])')).toBeNull();
		expect(doc.querySelector('p')?.attributes.length).toBe(0);
	});
	it('removes all event handlers and navigation links, retaining local anchors', () => {
		const doc = parse(
			'<p onerror="alert(1)" onclick="alert(1)">Safe</p><a href="javascript:alert(1)">Bad</a><a href="https://evil.test" ping="https://evil.test">External</a><a href="#ref">Local</a>'
		);
		expect(doc.querySelector('p')?.attributes.length).toBe(0);
		expect(Array.from(doc.querySelectorAll('a')).map((a) => a.getAttribute('href'))).toEqual([
			null,
			null,
			'#ref'
		]);
		expect(doc.documentElement.outerHTML).not.toMatch(/javascript:|onerror|onclick|ping=/);
	});
	it('preserves a styled document and page rules, placing CSP first and escaping the title', () => {
		const css = '@page { size: A4; margin: 20mm; } p { color: navy; font-size: 11pt; }';
		const title = '</title><script>bad</script> & "Report"';
		const doc = parse(
			`<html><head><title>Old</title><style>${css}</style></head><body><p style="color: red">Report</p></body></html>`,
			title
		);
		expect(doc.head.firstElementChild?.outerHTML).toBe(
			`<meta http-equiv="Content-Security-Policy" content="${DOCUMENT_CSP}">`
		);
		expect(doc.title).toBe(title);
		expect(doc.querySelectorAll('title')).toHaveLength(1);
		expect(doc.querySelector('script')).toBeNull();
		expect(doc.querySelector('style:not([data-page-boxes])')?.textContent).toBe(css);
		expect(doc.querySelector('p')?.getAttribute('style')).toBe('color: red');
	});
});

describe('print header and footer', () => {
	it('empties the page margin boxes so the browser prints no title, date or URL', () => {
		const html = sanitizeDocumentHtml('<html><head></head><body><p>x</p></body></html>', 'T');
		expect(html).toContain('@top-center { content: ""; }');
		expect(html).toContain('@bottom-left { content: ""; }');
	});
});

describe('HTML citation stripping', () => {
	const strip = (html: string, title = 'Report') =>
		new DOMParser().parseFromString(
			sanitizeDocumentHtml(html, title, { stripCitations: true }),
			'text/html'
		);

	it('removes single, grouped, repeated and unknown markers with their leading space', () => {
		const doc = strip('<p>Claim [2, 1] and <b>[3]</b>, again [2]. Unknown [99].</p>');
		expect(doc.body.innerHTML).toBe('<p>Claim and <b></b>, again. Unknown.</p>');
	});

	it('strips decoded text after sanitising without creating active markup', () => {
		const doc = strip(
			'<p onclick="bad()">Claim&#32;&#91;1&#93;. &lt;script&gt; [2]</p><script>bad()</script>'
		);
		expect(doc.querySelector('p')?.textContent).toBe('Claim. <script>');
		expect(doc.querySelector('p')?.attributes.length).toBe(0);
		expect(doc.querySelector('script')).toBeNull();
	});

	it('preserves styles, attributes and CSP while stripping visible text', () => {
		const css = 'p::after { content: " [1]" }';
		const doc = strip(
			`<style>${css}</style><p title="Label [2]" data-ref="[3, 4]" style="--label: ' [5]'">Text [6]</p><style>p::before { content: " [7]" }</style>`
		);
		expect(doc.querySelector('style')?.textContent).toBe(css);
		expect(doc.body.querySelector('style')?.textContent).toContain('" [7]"');
		expect(doc.querySelector('p')?.title).toBe('Label [2]');
		expect(doc.querySelector('p')?.dataset.ref).toBe('[3, 4]');
		expect(doc.querySelector('p')?.getAttribute('style')).toBe("--label: ' [5]'");
		expect(doc.querySelector('p')?.textContent).toBe('Text');
		expect(doc.querySelector('meta')?.content).toBe(DOCUMENT_CSP);
	});

	it('preserves other brackets and whitespace around the removed marker', () => {
		const doc = strip('<pre>[note] [1a] [1, x]\nLine [1]\nNext [2,3] word</pre>');
		expect(doc.querySelector('pre')?.textContent).toBe('[note] [1a] [1, x]\nLine\nNext word');
	});

	it('strips markers in the document title', () => {
		expect(strip('<p>Text</p>', 'Report [1, 2]').title).toBe('Report');
	});

	it('keeps citation markers when stripping is not requested for Markdown printing', () => {
		expect(parse('<p>Claim [2, 1]</p>').body.textContent).toBe('Claim [2, 1]');
	});
});
