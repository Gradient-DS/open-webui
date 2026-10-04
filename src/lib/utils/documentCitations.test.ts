// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import {
	bindDocumentCitationClicks,
	documentSourceLabel,
	getExportMarkdown,
	transformDocumentCitations
} from './documentCitations';
import { sanitizeDocumentHtml, DOCUMENT_CSP } from './htmlDocument';

const sources = ['First.pdf', 'https://www.example.com/article', 'Third.pdf'].map(
	(name, index) => ({
		source: { id: String(index), name, ...(index === 1 ? { url: name } : {}) },
		metadata: [{ source: String(index) }],
		document: ['Excerpt']
	})
);
const transform = (html: string, mode: 'preview' | 'print' = 'preview', heading = 'Sources') =>
	new DOMParser().parseFromString(
		transformDocumentCitations(sanitizeDocumentHtml(html, 'Report'), sources, mode, heading),
		'text/html'
	);

describe('HTML citations', () => {
	it('replaces text markers with source-labelled pills, including multiple citations', () => {
		const doc = transform('<p>Claim [2, 1] and <b>[3]</b>, again [2]. Unknown [99].</p>');
		expect([...doc.querySelectorAll('button')].map((pill) => pill.textContent)).toEqual([
			'example.com',
			'First.pdf',
			'Third.pdf',
			'example.com'
		]);
		expect(doc.body.textContent).toContain('Unknown [99]');
		expect(doc.head.querySelector('style:last-child')?.textContent).toContain(
			'button[data-document-citation]'
		);
	});
	it('leaves style text, attributes and the CSP untouched', () => {
		const doc = transform('<style>p::after { content: "[1]" }</style><p title="[2]">[1]</p>');
		expect(doc.querySelector('style')?.textContent).toContain('"[1]"');
		expect(doc.querySelector('p')?.title).toBe('[2]');
		expect(doc.querySelector('meta')?.content).toBe(DOCUMENT_CSP);
		expect(doc.querySelectorAll('button')).toHaveLength(1);
	});
	it('escapes source names, URLs and translated headings in preview and print', () => {
		const hostile = [
			{ source: { name: '<img onerror=x>', url: '</p><script>x</script>' }, document: ['x'] }
		];
		for (const mode of ['preview', 'print'] as const) {
			const html = transformDocumentCitations(
				sanitizeDocumentHtml('<p>[1]</p>', 'T'),
				hostile,
				mode,
				'<b>Sources</b>'
			);
			const doc = new DOMParser().parseFromString(html, 'text/html');
			expect(doc.querySelector('img,script,b')).toBeNull();
			expect(doc.body.textContent).toContain('<img onerror=x>');
		}
	});
	it('renumbers print citations by first use across text nodes and lists only cited sources', () => {
		const doc = transform('<p>[3]</p><div title="[1]">[2, 3] [99]</div>', 'print', 'Bronnen');
		expect(doc.querySelector('p')?.textContent).toBe('[1]');
		expect(doc.querySelector('div')?.textContent).toBe('[2, 1] [99]');
		expect(doc.querySelector('h2')?.textContent).toBe('Bronnen');
		expect(doc.querySelector('section')?.textContent).toBe(
			'Bronnen[1] Third.pdf, Third.pdf[2] example.com, https://www.example.com/article'
		);
		expect(doc.querySelector('button')).toBeNull();
		expect(doc.querySelector('section')?.textContent).not.toContain('First.pdf');
	});
	it('does not append an uncited source list', () => {
		expect(transform('<p>No citations</p>', 'print').querySelector('section')).toBeNull();
	});
	it('lets the parent handle pill clicks and remove its listener', () => {
		const frame = document.createElement('iframe');
		document.body.append(frame);
		const doc = frame.contentDocument!;
		doc.body.innerHTML = transform('<p>[2]</p>').body.innerHTML;
		const clicked = vi.fn();
		const cleanup = bindDocumentCitationClicks(doc, clicked);
		doc.querySelector('button')!.click();
		expect(clicked).toHaveBeenCalledWith(2);
		cleanup();
		doc.querySelector('button')!.click();
		expect(clicked).toHaveBeenCalledTimes(1);
		frame.remove();
	});
	it('uses the markdown pill label rules for metadata, URL domains and long names', () => {
		expect(documentSourceLabel({ ...sources[0], metadata: [{ name: 'A%20B.pdf' }] })).toBe(
			'A B.pdf'
		);
		expect(
			documentSourceLabel({ ...sources[0], metadata: [{ source: 'https://www.example.com/path' }] })
		).toBe('example.com');
		expect(documentSourceLabel({ source: { name: 'abcdefghijklmnopqrstuvwxyz0123456789' } })).toBe(
			'abcdefghijklmno...0123456789'
		);
	});
});

describe('markdown exports', () => {
	it('renumbers by first use and includes a numbered appendix in the UI language', () => {
		const result = getExportMarkdown('Claim [3] then [1, 3].', sources, 'Sources');
		expect(result).toBe(
			'Claim [1] then [2, 1].\n\n---\n\nSources:\n[1] Third.pdf\n[2] First.pdf\n'
		);
	});
	it('preserves the legacy full-list fallback when there are no inline citations', () => {
		expect(getExportMarkdown('Text', sources, 'Bronnen')).toContain('[3] Third.pdf');
	});
});
