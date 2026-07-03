import { describe, it, expect } from 'vitest';
import { normalizeForMatch, matchItemsToNeedle, type PageTextItem } from './citationMatch';

const items = (...strings: string[]): PageTextItem[] =>
	strings.map((str, index) => ({ str, index }));

describe('normalizeForMatch', () => {
	it('lowercases and collapses whitespace runs to single spaces', () => {
		expect(normalizeForMatch('The   Quick\n\tBrown   Fox')).toBe('the quick brown fox');
	});

	it('trims leading and trailing whitespace', () => {
		expect(normalizeForMatch('  hello world  ')).toBe('hello world');
	});

	it('folds ligatures via NFKC normalization (ﬁ -> fi)', () => {
		expect(normalizeForMatch('ﬁle')).toBe('file');
	});

	it('strips markdown syntax noise (#, *, _, `, ~, |) and link brackets', () => {
		expect(normalizeForMatch('## The **Quarterly** _Report_')).toBe('the quarterly report');
		expect(normalizeForMatch('`code` ~~strike~~')).toBe('code strike');
		expect(normalizeForMatch('| col a | col b |')).toBe('col a col b');
		expect(normalizeForMatch('see [the docs](https://example.com)')).toBe(
			'see the docs https://example.com'
		);
	});

	it('strips HTML tags', () => {
		expect(normalizeForMatch('<b>bold</b> and <span class="x">spanned</span>')).toBe(
			'bold and spanned'
		);
	});

	it('drops soft hyphens (U+00AD)', () => {
		expect(normalizeForMatch('exam­ple')).toBe('example');
	});

	it('joins hyphenated line breaks (hyphen + whitespace between word chars)', () => {
		expect(normalizeForMatch('exam- ple')).toBe('example');
		expect(normalizeForMatch('exam-\nple')).toBe('example');
	});

	it('keeps a normal intra-word hyphen when no whitespace follows', () => {
		expect(normalizeForMatch('well-known')).toBe('well-known');
	});
});

describe('matchItemsToNeedle', () => {
	it('returns indices of an exact match spanning multiple consecutive items', () => {
		const page = items('The quick ', 'brown fox ', 'jumps over');
		const needle = 'the quick brown fox jumps over';
		expect(matchItemsToNeedle(page, needle)).toEqual([0, 1, 2]);
	});

	it('matches a markdown-formatted needle against plain page text', () => {
		const page = items('the quarterly report ', 'shows strong growth');
		const needle = '## The **Quarterly** Report shows strong growth';
		expect(matchItemsToNeedle(page, needle)).toEqual([0, 1]);
	});

	it('joins a hyphenated line break across two items to match the needle', () => {
		// pdf.js often splits a hyphenated line-break word across items.
		const page = items('A detailed exam-', 'ple of the matching behaviour');
		const needle = 'a detailed example of the matching behaviour';
		expect(matchItemsToNeedle(page, needle)).toEqual([0, 1]);
	});

	it('matches across ligature and whitespace/newline noise', () => {
		const page = items('The conﬁguration ', 'ﬁle was loaded');
		const needle = 'the   configuration\nfile was loaded';
		expect(matchItemsToNeedle(page, needle)).toEqual([0, 1]);
	});

	it('does not highlight a short common word that is below minRunChars', () => {
		const page = items('totally unrelated heading', 'the', 'more unrelated content');
		const needle = 'something about the weather today';
		expect(matchItemsToNeedle(page, needle)).toEqual([]);
	});

	it('returns an empty array when nothing matches', () => {
		const page = items('completely different ', 'page content here');
		const needle = 'the cited passage that does not appear on this page at all';
		expect(matchItemsToNeedle(page, needle)).toEqual([]);
	});

	it('returns only the matching items when the needle spans part of the page', () => {
		const page = items(
			'Unrelated header line. ',
			'The cited passage of interest ',
			'continues here. ',
			'Then unrelated footer.'
		);
		const needle = 'the cited passage of interest continues here';
		expect(matchItemsToNeedle(page, needle)).toEqual([1, 2]);
	});

	it('treats whitespace-only items as transparent within a run', () => {
		const page = items('the cited passage ', '   ', 'of interest here');
		const needle = 'the cited passage of interest here';
		expect(matchItemsToNeedle(page, needle)).toEqual([0, 1, 2]);
	});

	it('is pure: repeated calls return the same result and inputs are not mutated', () => {
		const page = items('the cited passage ', 'of interest here');
		const snapshot = JSON.parse(JSON.stringify(page));
		const needle = 'the cited passage of interest here';
		const first = matchItemsToNeedle(page, needle);
		const second = matchItemsToNeedle(page, needle);
		expect(first).toEqual([0, 1]);
		expect(second).toEqual(first);
		expect(page).toEqual(snapshot);
	});

	it('respects a custom minRunChars threshold', () => {
		const page = items('alpha beta');
		const needle = 'alpha beta gamma delta epsilon';
		// "alpha beta" is 10 normalized chars: below 20 (default) but above 5.
		expect(matchItemsToNeedle(page, needle)).toEqual([]);
		expect(matchItemsToNeedle(page, needle, { minRunChars: 5 })).toEqual([0]);
	});

	it('collects multiple separate runs across the page', () => {
		const page = items(
			'the first cited segment here ',
			' UNRELATED INTERRUPTION ',
			'and the second cited segment here'
		);
		const needle =
			'the first cited segment here ... and the second cited segment here';
		expect(matchItemsToNeedle(page, needle)).toEqual([0, 2]);
	});

	it('ignores empty and whitespace-only items as standalone runs', () => {
		const page = items('', '   ', 'unmatched');
		const needle = 'a totally different needle string entirely';
		expect(matchItemsToNeedle(page, needle)).toEqual([]);
	});
});
