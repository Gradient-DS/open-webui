import { describe, it, expect } from 'vitest';
import {
	buildTaggedSourceNames,
	deriveVisibleSources,
	extractCitedNs,
	hasTaggedSources
} from './citations';

const tagged = (n: number, currentTurn: boolean, name = `Doc ${n}`) => ({
	n,
	current_turn: currentTurn,
	source: { name, type: 'file', id: `doc-${n}` },
	document: [`chunk of ${name}`],
	metadata: [{ source: name }]
});

const untagged = (name: string) => ({
	source: { name, type: 'file', id: name },
	document: [`chunk of ${name}`],
	metadata: [{ source: name }]
});

describe('extractCitedNs', () => {
	it('parses single bracket citations', () => {
		expect(extractCitedNs('Answer with [1] inline.')).toEqual(new Set([1]));
	});

	it('parses grouped citations', () => {
		expect(extractCitedNs('See [1, 2] and later [7].')).toEqual(new Set([1, 2, 7]));
	});

	it('parses hash-suffixed citations', () => {
		expect(extractCitedNs('Targeted cite [4#section-2].')).toEqual(new Set([4]));
	});

	it('parses fullwidth bracket citations', () => {
		expect(extractCitedNs('Japanese style 【3】 and 【5†L1-L4】.')).toEqual(new Set([3, 5]));
	});

	it('returns empty set for content without citations', () => {
		expect(extractCitedNs('No citations here.')).toEqual(new Set());
		expect(extractCitedNs('')).toEqual(new Set());
	});

	it('ignores footnote markers', () => {
		expect(extractCitedNs('A footnote[^1] is not a citation.')).toEqual(new Set());
	});
});

describe('hasTaggedSources', () => {
	it('detects tagged sources', () => {
		expect(hasTaggedSources([tagged(1, true)])).toBe(true);
		expect(hasTaggedSources([untagged('a.pdf'), tagged(2, false)])).toBe(true);
	});

	it('is false for untagged-only and empty lists', () => {
		expect(hasTaggedSources([untagged('a.pdf')])).toBe(false);
		expect(hasTaggedSources([])).toBe(false);
	});
});

describe('deriveVisibleSources', () => {
	it('shows tagged sources retrieved this turn', () => {
		const sources = [tagged(1, true)];
		expect(deriveVisibleSources(sources, new Set())).toEqual(sources);
	});

	it('shows prior-turn tagged sources when cited inline', () => {
		const crossTurn = tagged(1, false);
		expect(deriveVisibleSources([crossTurn], new Set([1]))).toEqual([crossTurn]);
	});

	it('hides prior-turn tagged sources that are not cited', () => {
		expect(deriveVisibleSources([tagged(1, false)], new Set())).toEqual([]);
	});

	it('combines retrieved and cross-turn cited (the panel semantics)', () => {
		const citedCrossTurn = tagged(1, false);
		const uncitedCrossTurn = tagged(2, false);
		const retrieved = tagged(3, true);
		const visible = deriveVisibleSources(
			[citedCrossTurn, uncitedCrossTurn, retrieved],
			new Set([1, 3])
		);
		expect(visible).toEqual([citedCrossTurn, retrieved]);
	});

	it('keeps all untagged sources (vanilla show-all path)', () => {
		const sources = [untagged('a.pdf'), untagged('b.pdf')];
		expect(deriveVisibleSources(sources, new Set())).toEqual(sources);
	});

	it('keeps untagged sources in a mixed message', () => {
		const vanilla = untagged('a.pdf');
		const hiddenTagged = tagged(1, false);
		expect(deriveVisibleSources([vanilla, hiddenTagged], new Set())).toEqual([vanilla]);
	});
});

describe('buildTaggedSourceNames', () => {
	it('indexes display names by cumulative id (n - 1)', () => {
		const names = buildTaggedSourceNames([tagged(2, false, 'Second'), tagged(1, true, 'First')]);
		expect(names[0]).toBe('First');
		expect(names[1]).toBe('Second');
	});

	it('prefers chunk metadata name, then url id, then source name', () => {
		const metadataNamed = {
			n: 1,
			current_turn: true,
			source: { name: 'fallback', id: 'doc-1' },
			document: ['chunk'],
			metadata: [{ source: 'doc-1', name: 'meta-name.pdf' }]
		};
		const urlSource = {
			n: 2,
			current_turn: true,
			source: { name: 'fallback', id: 'https://example.com/page' },
			document: ['chunk'],
			metadata: [{ source: 'https://example.com/page' }]
		};
		const names = buildTaggedSourceNames([metadataNamed, urlSource]);
		expect(names[0]).toBe('meta-name.pdf');
		expect(names[1]).toBe('https://example.com/page');
	});

	it('skips untagged sources', () => {
		const names = buildTaggedSourceNames([untagged('a.pdf'), tagged(2, true, 'Second')]);
		expect(names[0]).toBeUndefined();
		expect(names[1]).toBe('Second');
	});
});
