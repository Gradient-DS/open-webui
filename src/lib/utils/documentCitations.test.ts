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

describe('print export', () => {
	it('keeps body citations as plain [n] markers matching the source list', () => {
		const out = getExportMarkdown('Fact [3].', sources, 'Bronnen');
		expect(out).toContain('Fact [1].');
		expect(out).not.toContain('<sup>');
		expect(out).toContain('[1] Third.pdf');
	});
});
