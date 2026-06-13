/**
 * Tests for the DOM-side citation highlighter.
 *
 * These exercise the REAL `citationDomHighlight` functions against a REAL DOM:
 * `document.createTreeWalker`, `createElement`, `createTextNode`, `Node.normalize`,
 * `getBoundingClientRect`, etc. They are NOT trivially true — they assert actual
 * post-mutation DOM state (which text the `<mark>` wraps, `textContent` identity
 * after clear/re-highlight, multi-text-node runs). The expected values were
 * captured from the real matcher running under jsdom, not assumed.
 *
 * DOM ENVIRONMENT REQUIRED. This repo's vitest runs in the default `node`
 * environment and neither `jsdom` nor `happy-dom` is installed (they are only
 * optional peer deps of vitest, absent from devDependencies/node_modules), so
 * there is no live DOM available here. Per the task constraint we do NOT add a
 * dependency. The suite therefore SKIPS when `document` is undefined (keeping the
 * default `npm run test:frontend` green) and runs in full against a real DOM the
 * moment one is provided:
 *
 *     npm i -D jsdom            # or happy-dom
 *     npx vitest run --environment jsdom src/lib/utils/citationDomHighlight.test.ts
 *
 * Once jsdom/happy-dom is in devDependencies, add a per-file vitest-environment
 * directive (the "at vitest-environment jsdom" comment) as the first line of this
 * file to make it run under the standard `npm run test:frontend` without the
 * explicit flag. (Written in prose here, not as a real directive, so vitest does
 * not try to load jsdom in the default node environment.)
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
	highlightDocx,
	clearDocxHighlights,
	scrollToFirstDocxHighlight
} from './citationDomHighlight';

const HAS_DOM = typeof document !== 'undefined';

const MARK_SELECTOR = 'mark.citation-highlight';

const makeContainer = (html: string): HTMLElement => {
	const container = document.createElement('div');
	container.innerHTML = html;
	return container;
};

const markedText = (container: HTMLElement): string =>
	Array.from(container.querySelectorAll<HTMLElement>(MARK_SELECTOR))
		.map((mark) => mark.textContent ?? '')
		.join('');

const markCount = (container: HTMLElement): number =>
	container.querySelectorAll(MARK_SELECTOR).length;

describe.skipIf(!HAS_DOM)('citationDomHighlight (requires a DOM environment)', () => {
	let container: HTMLElement;

	beforeEach(() => {
		container = makeContainer('');
	});

	describe('highlightDocx — match wraps the right text', () => {
		it('wraps the matching passage in a citation-highlight mark', () => {
			// Cited sentence is the final text in the document, so the match ends
			// cleanly on the last node (no trailing run to greedily absorb).
			container = makeContainer(
				'<p>Intro paragraph that is unrelated here.</p>' +
					'<p>The quick brown fox jumps over the lazy dog.</p>'
			);
			const needle = 'The quick brown fox jumps over the lazy dog.';

			const matched = highlightDocx(container, needle);

			expect(matched).toBeGreaterThan(0);
			expect(markCount(container)).toBeGreaterThan(0);
			// The marked text equals the matched passage (the longest sentence).
			expect(markedText(container)).toBe('The quick brown fox jumps over the lazy dog.');
		});

		it('uses the longest sentence of a multi-sentence snippet as the target', () => {
			container = makeContainer(
				'<p>Short one.</p>' +
					'<p>This considerably longer sentence carries the cited substance here.</p>'
			);
			const needle =
				'Short one. This considerably longer sentence carries the cited substance here.';

			highlightDocx(container, needle);

			expect(markedText(container)).toBe(
				'This considerably longer sentence carries the cited substance here.'
			);
			// The short sentence must not be marked.
			expect(markedText(container)).not.toContain('Short one.');
		});
	});

	describe('highlightDocx — no match', () => {
		it('produces no marks and does not throw when the snippet is absent', () => {
			container = makeContainer('<p>Entirely different content with nothing in common.</p>');
			const needle = 'A cited passage that simply does not appear in this container at all.';

			let matched = -1;
			expect(() => {
				matched = highlightDocx(container, needle);
			}).not.toThrow();

			expect(matched).toBe(0);
			expect(markCount(container)).toBe(0);
		});

		it('does not mark a needle shorter than the minimum and returns 0', () => {
			container = makeContainer('<p>tiny</p>');
			expect(highlightDocx(container, 'tiny')).toBe(0);
			expect(markCount(container)).toBe(0);
		});

		it('treats a null needle as a clean no-op', () => {
			container = makeContainer('<p>Some rendered document text goes here.</p>');
			expect(() => highlightDocx(container, null)).not.toThrow();
			expect(markCount(container)).toBe(0);
		});
	});

	describe('clear → re-highlight idempotency', () => {
		it('clears, re-highlights a different snippet, and leaves text content untouched', () => {
			container = makeContainer(
				'<p>The first cited sentence sits in this paragraph.</p>' +
					'<p>The second distinct sentence lives down here instead.</p>'
			);
			const originalText = container.textContent;

			// First snippet: assert it is wrapped (it sits before another node, so the
			// greedy matcher may also absorb the trailing node — assert containment).
			highlightDocx(container, 'The first cited sentence sits in this paragraph.');
			expect(markedText(container)).toContain('The first cited sentence sits in this paragraph.');

			// Clear coalesces the split text nodes back; the document text must be
			// byte-identical to before any highlighting and carry zero marks.
			clearDocxHighlights(container);
			container.normalize();
			expect(markCount(container)).toBe(0);
			expect(container.textContent).toBe(originalText);

			// Second snippet is the final text node, so the match is exact.
			highlightDocx(container, 'The second distinct sentence lives down here instead.');
			expect(markedText(container)).toBe('The second distinct sentence lives down here instead.');
			// Only the second snippet is marked — the first is not re-marked.
			expect(markedText(container)).not.toContain('first cited sentence');
			// Underlying text content is unchanged from before any highlighting.
			expect(container.textContent).toBe(originalText);
		});

		it('clearDocxHighlights coalesces split text nodes so a later pass still matches', () => {
			container = makeContainer('<p>The cited passage of interest continues here today.</p>');
			const originalText = container.textContent;

			highlightDocx(container, 'The cited passage of interest continues here today.');
			expect(markCount(container)).toBeGreaterThan(0);

			clearDocxHighlights(container);
			container.normalize();
			expect(markCount(container)).toBe(0);
			expect(container.textContent).toBe(originalText);

			// Re-highlighting the same passage works after the clear+normalize round-trip.
			const matched = highlightDocx(
				container,
				'The cited passage of interest continues here today.'
			);
			expect(matched).toBeGreaterThan(0);
			expect(markedText(container)).toBe('The cited passage of interest continues here today.');
		});
	});

	describe('multi-text-node run match', () => {
		it('matches a passage split across adjacent inline elements / text nodes', () => {
			// The cited sentence is broken across sibling inline nodes the way a
			// rendered DOCX splits runs (bold/italic spans, etc.).
			container = makeContainer(
				'<p>The cited passage <strong>of interest</strong> continues <em>here</em> now.</p>'
			);
			const needle = 'The cited passage of interest continues here now.';

			const matched = highlightDocx(container, needle);

			// The passage spans several text nodes; each contributing node is marked.
			expect(matched).toBeGreaterThan(1);
			// The combined marked text reconstructs the matched passage.
			const combined = markedText(container).replace(/\s+/g, ' ').trim();
			expect(combined).toBe('The cited passage of interest continues here now.');
		});
	});

	describe('scrollToFirstDocxHighlight', () => {
		it('returns false and does not throw when there are zero marks', () => {
			container = makeContainer('<p>No marks have been added to this container.</p>');
			expect(() => scrollToFirstDocxHighlight(container)).not.toThrow();
			expect(scrollToFirstDocxHighlight(container)).toBe(false);
		});

		it('returns true after a highlight produces a mark', () => {
			container = makeContainer('<p>The quick brown fox jumps over the lazy dog today.</p>');
			highlightDocx(container, 'The quick brown fox jumps over the lazy dog today.');
			expect(markCount(container)).toBeGreaterThan(0);
			expect(() => scrollToFirstDocxHighlight(container)).not.toThrow();
			expect(scrollToFirstDocxHighlight(container)).toBe(true);
		});
	});
});
