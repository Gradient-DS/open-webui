// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildPrintDocument, printDocument, withTitle } from './documentPrint';
import { DOCUMENT_SANDBOX } from './htmlDocument';

const printFrame = () =>
	vi.waitFor(() => {
		const frame = document.querySelector('iframe');
		if (!frame) throw new Error('no print frame yet');
		return frame;
	});

afterEach(() => {
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

describe('browser PDF printing', () => {
	it('builds a standalone sanitized markdown document with A4 tables and page rules', () => {
		const doc = new DOMParser().parseFromString(
			buildPrintDocument(
				'Report <1>',
				'# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n<script>bad()</script>\n<img src="https://evil.test">',
				'markdown'
			),
			'text/html'
		);
		expect(doc.title).toBe('Report <1>');
		expect(doc.querySelector('h1')?.textContent).toBe('Title');
		expect(doc.querySelector('table')).not.toBeNull();
		expect(doc.querySelector('style')?.textContent).toMatch(/@page.*size: A4/);
		expect(doc.querySelector('style')?.textContent).toContain('break-inside: avoid');
		expect(doc.querySelector('script,img')).toBeNull();
	});
	it('keeps HTML styling through the same sanitiser', () => {
		const result = buildPrintDocument(
			'HTML report',
			'<style>@page { size: A4 }</style><h1>Title</h1><iframe src="https://evil.test"></iframe>',
			'html'
		);
		expect(result).toContain('@page { size: A4 }');
		expect(result).not.toContain('evil.test');
	});
	it('waits for the sandbox frame load, prints, then removes it after printing', async () => {
		vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
		const pending = printDocument('Report', '# Title', 'markdown');
		const frame = await printFrame();
		expect(frame.getAttribute('sandbox')).toBe(DOCUMENT_SANDBOX);
		expect(frame.srcdoc).toContain('<title>Report</title>');
		const print = vi.spyOn(frame.contentWindow!, 'print').mockImplementation(() => {});
		expect(print).not.toHaveBeenCalled();
		frame.dispatchEvent(new Event('load'));
		await pending;
		expect(print).toHaveBeenCalledOnce();
		expect(frame.isConnected).toBe(true);
		frame.contentWindow!.dispatchEvent(new Event('afterprint'));
		expect(frame.isConnected).toBe(false);
	});
	it('cleans up and reports print failures', async () => {
		const pending = printDocument('Report', '<p>Title</p>', 'html');
		const frame = await printFrame();
		vi.spyOn(frame.contentWindow!, 'print').mockImplementation(() => {
			throw new Error('Print blocked');
		});
		frame.dispatchEvent(new Event('load'));
		await expect(pending).rejects.toThrow('Print blocked');
		expect(frame.isConnected).toBe(false);
	});
});

describe('title block and page numbers', () => {
	it('opens with the document title unless the markdown starts with a heading', () => {
		expect(withTitle('Body', 'Memo')).toBe('# Memo\n\nBody');
		expect(withTitle('# Own title\n\nBody', 'Memo')).toBe('# Own title\n\nBody');
	});
	it('numbers pages and admits our inlined font only outside the sanitiser', () => {
		const font =
			"@font-face { font-family: Inter; src: url(data:font/ttf;base64,AA) format('truetype'); }";
		const doc = new DOMParser().parseFromString(
			buildPrintDocument('Memo', 'Body', 'markdown', font),
			'text/html'
		);
		const boxes = doc.querySelector('style[data-page-boxes]')?.textContent ?? '';
		expect(boxes).toContain('counter(page)');
		expect(boxes).toContain('font-family: Inter');
		expect(doc.querySelector('h1')?.textContent).toBe('Memo');
		const html = buildPrintDocument(
			'Memo',
			'<style>@font-face { src: url(https://evil.test/f) }</style>',
			'html',
			font
		);
		expect(html).not.toContain('evil.test');
		expect(html).not.toContain('font-family: Inter');
	});
});

describe('markdown line breaks', () => {
	it('keeps one source per line like the document panel', () => {
		const html = buildPrintDocument('Memo', 'Bronnen:\n[1] Een\n[2] Twee', 'markdown');
		expect(html).toContain('[1] Een<br>');
	});
});

it('strips HTML citation markers without adding pills or a source list', () => {
	const html = buildPrintDocument('Report', '<p>Claim [2] and [2, 99].</p>', 'html');
	const doc = new DOMParser().parseFromString(html, 'text/html');
	expect(doc.body.innerHTML).toBe('<p>Claim and.</p>');
	expect(doc.querySelector('button, section')).toBeNull();
	expect(doc.querySelector('style[data-page-boxes]')?.textContent).toContain('@bottom-left');
});
