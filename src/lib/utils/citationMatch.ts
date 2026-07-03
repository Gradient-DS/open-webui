/**
 * Pure, framework-free text-match engine for citation highlighting.
 *
 * A citation modal shows a cited snippet (the "needle") alongside a rendered
 * document. pdf.js exposes each page's text as `textContent.items[]`, where
 * every item maps 1:1 to a `<span>` in the page's TextLayer. To highlight the
 * cited passage we must know which items (spans) to mark. This module answers
 * that: given a page's text items and the cited snippet, it returns the indices
 * of items to highlight.
 *
 * It is intentionally free of Svelte, the DOM, and pdf.js — just plain data in,
 * indices out — so it can be unit-tested directly and reused by the DOCX path.
 */

const DEFAULT_MIN_RUN_CHARS = 20;
const SOFT_HYPHEN = /­/g;
const HTML_TAG = /<[^>]*>/g;
const MARKDOWN_NOISE = /[#*_`~|[\]()]/g;
const HYPHEN_LINE_BREAK = /(\w)-\s+(\w)/g;
const WHITESPACE_RUN = /\s+/g;
// Trailing punctuation / line-break hyphen on a run that the cited needle
// usually omits at a clause boundary (e.g. "...here." vs "...here").
const TRAILING_NOISE = /[-.,;:!?]+$/;

export interface PageTextItem {
	/** One pdf.js text item's text (== one TextLayer span's text). */
	str: string;
	/** The item's index within the page (this is what gets returned). */
	index: number;
}

/**
 * Produce a canonical comparable form of a string so a page's items and the
 * cited needle line up despite formatting, ligatures, and layout artefacts.
 *
 * Applies, in order: NFKC unicode normalization (folds ligatures like "ﬁ"),
 * lowercasing, soft-hyphen removal, HTML-tag stripping, markdown-noise
 * stripping, line-break-hyphen joining ("exam- ple" -> "example"), and
 * whitespace-run collapsing with trim.
 */
export const normalizeForMatch = (s: string): string =>
	s
		.normalize('NFKC')
		.toLowerCase()
		.replace(SOFT_HYPHEN, '')
		.replace(HTML_TAG, ' ')
		.replace(MARKDOWN_NOISE, ' ')
		.replace(HYPHEN_LINE_BREAK, '$1$2')
		.replace(WHITESPACE_RUN, ' ')
		.trim();

/**
 * Return the indices of page items whose text falls inside the cited needle.
 *
 * Greedy run-matching: walk items left to right, extending a run of consecutive
 * items while the normalized concatenation of the run stays a substring of the
 * normalized needle. A finished run is kept only if it covers at least
 * `minRunChars` normalized characters, which suppresses short common-word false
 * positives. Returns the union of kept-run indices in ascending order.
 *
 * @param items page text items (not mutated)
 * @param needle the cited passage (the longer text each page shows part of)
 * @param opts.minRunChars minimum normalized chars a run must cover to be kept
 */
export const matchItemsToNeedle = (
	items: PageTextItem[],
	needle: string,
	opts?: { minRunChars?: number }
): number[] => {
	const minRunChars = opts?.minRunChars ?? DEFAULT_MIN_RUN_CHARS;
	const normalizedNeedle = normalizeForMatch(needle);
	if (normalizedNeedle.length === 0) {
		return [];
	}

	const kept: number[] = [];
	let start = 0;
	while (start < items.length) {
		const run = _extendRun(items, start, normalizedNeedle);
		if (run.matchedChars >= minRunChars) {
			for (let i = start; i < run.end; i += 1) {
				kept.push(items[i].index);
			}
		}
		// Always advance past the consumed run; never re-scan the same start.
		start = Math.max(run.end, start + 1);
	}
	return kept;
};

/**
 * Extend a run of consecutive items starting at `start` for as long as the
 * combined normalized run text stays a substring of the needle.
 *
 * Item strings are joined with a single space before normalization. Each item
 * is a distinct TextLayer span, so the boundary is at least a soft word break;
 * the space lets the line-break-hyphen rule fire across items ("exam-" + "ple"
 * -> "example") and keeps adjacent fragments from fusing into a non-word.
 * Whitespace-only / empty items are transparent: they extend the run without
 * contributing matched chars (their normalized form is empty).
 */
const _extendRun = (
	items: PageTextItem[],
	start: number,
	normalizedNeedle: string
): { end: number; matchedChars: number } => {
	const run: string[] = [];
	let end = start;
	let matchedChars = 0;
	while (end < items.length) {
		const candidate = normalizeForMatch([...run, items[end].str].join(' '));
		if (!_isSubstringOfNeedle(candidate, normalizedNeedle)) {
			break;
		}
		run.push(items[end].str);
		matchedChars = candidate.length;
		end += 1;
	}
	return { end, matchedChars };
};

/**
 * Whether the candidate run text appears in the needle, tolerating a single
 * trailing punctuation / line-break hyphen that the cited needle commonly drops
 * at a clause boundary. Empty candidates (whitespace-only items) always pass.
 */
const _isSubstringOfNeedle = (candidate: string, normalizedNeedle: string): boolean => {
	if (candidate.length === 0) {
		return true;
	}
	if (normalizedNeedle.includes(candidate)) {
		return true;
	}
	const trimmed = candidate.replace(TRAILING_NOISE, '');
	return trimmed.length > 0 && normalizedNeedle.includes(trimmed);
};
