import { describe, expect, it } from 'vitest';
import { scopePanelCitations, usedCitations } from './panelScope';
import { reduceSources, type RawSource } from './reduceSources';

const citationsWith = (...flags: Partial<RawSource>[]) =>
	reduceSources(
		flags.map((flag, index) => ({
			source: { id: String(index) },
			document: ['Passage'],
			...flag
		}))
	);

// [Gradient] The older union helper retains its documented fallback for pre-change agents.
describe('scopePanelCitations', () => {
	it('retains all sources without cited flags, including current-turn-only payloads', () => {
		for (const citations of [
			citationsWith({}, {}),
			citationsWith({ current_turn: false }, { current_turn: true })
		]) {
			expect(scopePanelCitations(citations)).toBe(citations);
		}
	});
	it('includes retrieved and cited sources when cited flags exist', () => {
		const citations = citationsWith(
			{ current_turn: true, cited_this_turn: false },
			{ current_turn: false, cited_this_turn: true },
			{ current_turn: false, cited_this_turn: false }
		);
		expect(scopePanelCitations(citations)).toEqual(citations.slice(0, 2));
	});
});

describe('usedCitations', () => {
	it('prefers explicit cites, including cross-turn cites, over retrievals', () => {
		const citations = citationsWith(
			{ current_turn: true, cited_this_turn: false },
			{ current_turn: false, cited_this_turn: true },
			{ current_turn: true },
			{}
		);
		expect(usedCitations(citations)).toEqual([citations[1]]);
	});
	it('does not fall back to retrievals when every explicit cited flag is false', () => {
		expect(usedCitations(citationsWith({ current_turn: true, cited_this_turn: false }))).toEqual(
			[]
		);
	});
	it('falls back to current-turn retrievals when no cited flag exists', () => {
		const citations = citationsWith({ current_turn: false }, { current_turn: true }, {});
		expect(usedCitations(citations)).toEqual([citations[1]]);
	});
	it('retains every source in unflagged legacy payloads', () => {
		const citations = citationsWith({}, {});
		expect(usedCitations(citations)).toBe(citations);
	});
	it('handles empty sources and explicit current-turn false', () => {
		expect(usedCitations([])).toEqual([]);
		expect(usedCitations(citationsWith({ current_turn: false }))).toEqual([]);
	});
});
