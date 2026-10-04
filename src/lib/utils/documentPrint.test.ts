// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildPrintDocument, printDocument } from './documentPrint';
import { DOCUMENT_SANDBOX } from './htmlDocument';

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
		const pending = printDocument('Report', '# Title', 'markdown');
		const frame = document.querySelector('iframe')!;
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
		const frame = document.querySelector('iframe')!;
		vi.spyOn(frame.contentWindow!, 'print').mockImplementation(() => {
			throw new Error('Print blocked');
		});
		frame.dispatchEvent(new Event('load'));
		await expect(pending).rejects.toThrow('Print blocked');
		expect(frame.isConnected).toBe(false);
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
