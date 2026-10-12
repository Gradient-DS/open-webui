import { describe, expect, it } from 'vitest';
import { answerText, citedCitations } from './citedSources';
import { reduceSources } from './reduceSources';

const stored = (count: number) =>
	reduceSources(
		Array.from({ length: count }, (_, index) => ({
			source: { id: `doc-${index + 1}` },
			document: [`Passage ${index + 1}`]
		}))
	);
const ids = (text: string, count = 15) =>
	citedCitations(stored(count), text).map((citation) => citation.id);

// [Gradient] A message lists exactly the sources its own `[N]` markers cite.
describe('citedCitations', () => {
	it('lists only the cited sources of an old message carrying the whole accumulated list', () => {
		expect(ids('Het antwoord [3] staat ook hier [12].')).toEqual(['doc-3', 'doc-12']);
	});

	it('lists nothing for an answer that cites nothing', () => {
		expect(ids('Geen bronnen nodig.')).toEqual([]);
		expect(ids('')).toEqual([]);
	});

	it('lists a repeated marker once and keeps stored order', () => {
		expect(ids('B [2], A [1], B again [2].')).toEqual(['doc-1', 'doc-2']);
	});

	it('reads grouped and adjacent markers as the renderer does', () => {
		expect(ids('One [1][2], two [4, 5], hash [6#p3], fullwidth 【7】 and 【8†L1-L4】.')).toEqual([
			'doc-1',
			'doc-2',
			'doc-4',
			'doc-5',
			'doc-6',
			'doc-7',
			'doc-8'
		]);
	});

	it('ignores markers past the stored list, zero and footnotes', () => {
		expect(ids('Real [2], past the end [16], zero [0], footnote [^1].')).toEqual(['doc-2']);
	});

	it('ignores markers inside code', () => {
		expect(ids('Index `a[1]` and\n```\nb[2]\n```\nbut cites [3].')).toEqual(['doc-3']);
	});

	it('follows the text as it streams', () => {
		expect(ids('Partial [1')).toEqual([]);
		expect(ids('Partial [1]')).toEqual(['doc-1']);
	});
});

describe('answerText', () => {
	it('prefers output items over content and drops details blocks from content', () => {
		const output = [{ type: 'message', content: [{ type: 'output_text', text: 'Output [1]' }] }];
		expect(answerText({ content: 'Content [2]', output })).toBe('Output [1]');
		expect(
			answerText({ content: '<details type="tool_calls">\n[2]\n</details>\nAnswer [1]' }).trim()
		).toBe('Answer [1]');
	});
});
