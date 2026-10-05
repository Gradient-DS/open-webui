import { describe, expect, it } from 'vitest';
import { getExportMarkdown } from './documentCitations';

const sources = ['First.pdf', 'https://www.example.com/article', 'Third.pdf'].map(
	(name, index) => ({
		source: { id: String(index), name, ...(index === 1 ? { url: name } : {}) },
		metadata: [{ source: String(index) }],
		document: ['Excerpt']
	})
);

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

describe('superscript export', () => {
	it('raises body citations for print and keeps the source list labels plain', () => {
		const out = getExportMarkdown('Fact [3].', sources, 'Bronnen', true);
		expect(out).toContain('Fact <sup>[1]</sup>.');
		expect(out).toContain('[1] Third.pdf');
	});
});
