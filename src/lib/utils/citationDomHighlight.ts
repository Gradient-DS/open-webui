/**
 * DOM-side citation highlighting for rendered DOCX HTML.
 *
 * The pure matcher in `citationMatch.ts` answers "which pdf.js text items fall
 * inside the cited needle". DOCX has no pdf.js text layer — it is sanitized HTML
 * injected with `{@html}`. So this module does the equivalent job against a live
 * DOM container: it locates the needle's text inside the rendered document and
 * wraps the matching run in `<mark class="citation-highlight">` elements.
 *
 * It is intentionally separate from `citationMatch.ts` because it is impure: it
 * reads and mutates the DOM. It reuses `normalizeForMatch` so DOCX and PDF share
 * the same canonicalization rules. No match is a no-op (never throws): callers
 * degrade to showing the document without a highlight.
 */

import { normalizeForMatch } from '$lib/utils/citationMatch';

const HIGHLIGHT_CLASS = 'citation-highlight';
const MIN_NEEDLE_CHARS = 12;

/**
 * Highlight the cited `needle` inside `container`, returning the number of
 * matched text nodes. Clears any previous highlight first. A needle that does
 * not appear (or is too short / empty) leaves the container unmarked.
 *
 * The longest sentence of the needle is used as the search target: cited
 * snippets often span layout boundaries (line breaks, inline tags) that the
 * rendered DOCX collapses differently, so matching one clean sentence is far
 * more robust than matching the whole multi-paragraph snippet verbatim.
 */
export const highlightDocx = (container: HTMLElement, needle: string | null): number => {
	clearDocxHighlights(container);
	const target = _longestSentence(needle ?? '');
	if (target.length < MIN_NEEDLE_CHARS) {
		return 0;
	}
	const textNodes = _collectTextNodes(container);
	const matchedNodes = _matchTextNodes(textNodes, target);
	for (const node of matchedNodes) {
		_wrapNode(node);
	}
	return matchedNodes.length;
};

/** Remove every citation `<mark>`, restoring the original text nodes. */
export const clearDocxHighlights = (container: HTMLElement) => {
	const marks = container.querySelectorAll<HTMLElement>(`mark.${HIGHLIGHT_CLASS}`);
	for (const mark of marks) {
		const parent = mark.parentNode;
		if (!parent) continue;
		parent.replaceChild(document.createTextNode(mark.textContent ?? ''), mark);
		parent.normalize();
	}
};

/**
 * Scroll the first citation `<mark>` into view within `container` (not the
 * window), centering it. Returns whether a mark was found.
 */
export const scrollToFirstDocxHighlight = (container: HTMLElement): boolean => {
	const first = container.querySelector<HTMLElement>(`mark.${HIGHLIGHT_CLASS}`);
	if (!first) {
		return false;
	}
	const containerRect = container.getBoundingClientRect();
	const targetRect = first.getBoundingClientRect();
	const delta =
		targetRect.top - containerRect.top - (container.clientHeight - targetRect.height) / 2;
	container.scrollTop += delta;
	return true;
};

/** Pick the longest sentence of a snippet by normalized length. */
const _longestSentence = (snippet: string): string => {
	const sentences = snippet.split(/(?<=[.!?])\s+/);
	let best = '';
	let bestLen = -1;
	for (const sentence of sentences) {
		const len = normalizeForMatch(sentence).length;
		if (len > bestLen) {
			best = sentence;
			bestLen = len;
		}
	}
	return best.trim();
};

/** Collect non-empty text nodes under `container` in document order. */
const _collectTextNodes = (container: HTMLElement): Text[] => {
	const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
		acceptNode: (node) =>
			(node.textContent ?? '').trim().length > 0
				? NodeFilter.FILTER_ACCEPT
				: NodeFilter.FILTER_REJECT
	});
	const nodes: Text[] = [];
	let current = walker.nextNode();
	while (current) {
		nodes.push(current as Text);
		current = walker.nextNode();
	}
	return nodes;
};

/**
 * Return the run of consecutive text nodes whose combined normalized text is a
 * substring of (or contains) the normalized target. Greedy: extend a run from
 * each start while it stays consistent with the target, keep the longest run.
 */
const _matchTextNodes = (nodes: Text[], target: string): Text[] => {
	const normalizedTarget = normalizeForMatch(target);
	if (normalizedTarget.length === 0) {
		return [];
	}
	let bestRun: Text[] = [];
	let start = 0;
	while (start < nodes.length) {
		const run = _extendRun(nodes, start, normalizedTarget);
		if (run.length > bestRun.length) {
			bestRun = run;
		}
		start += 1;
	}
	return bestRun;
};

/**
 * Extend a run of consecutive nodes from `start` while the joined-normalized run
 * text remains a prefix-consistent match against the target — i.e. the run text
 * is inside the target, or the target is inside the run text (the run already
 * covers the whole sentence). Stops as soon as adding a node breaks consistency.
 */
const _extendRun = (nodes: Text[], start: number, normalizedTarget: string): Text[] => {
	const run: Text[] = [];
	const parts: string[] = [];
	let end = start;
	while (end < nodes.length) {
		parts.push(nodes[end].textContent ?? '');
		const candidate = normalizeForMatch(parts.join(' '));
		if (!_isConsistent(candidate, normalizedTarget)) {
			break;
		}
		run.push(nodes[end]);
		if (normalizedTarget.includes(candidate) === false && candidate.includes(normalizedTarget)) {
			break;
		}
		end += 1;
	}
	return run;
};

/** A run is consistent if either string contains the other. */
const _isConsistent = (candidate: string, normalizedTarget: string): boolean => {
	if (candidate.length === 0) {
		return true;
	}
	return normalizedTarget.includes(candidate) || candidate.includes(normalizedTarget);
};

/** Replace a text node with a `<mark>` wrapping the same text. */
const _wrapNode = (node: Text) => {
	const parent = node.parentNode;
	if (!parent) {
		return;
	}
	const mark = document.createElement('mark');
	mark.className = HIGHLIGHT_CLASS;
	mark.textContent = node.textContent ?? '';
	parent.replaceChild(mark, node);
};
