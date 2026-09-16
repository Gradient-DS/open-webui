import { describe, expect, it } from 'vitest';
import { latestMessageWithSources, sourceGroups, type CitationHistory } from './citationTab';

const sources = [{ source: { id: 'file', name: 'Report' }, document: ['A passage'] }];

describe('latestMessageWithSources', () => {
	it('finds the latest assistant sources on the selected branch', () => {
		const history: CitationHistory = {
			currentId: 'question',
			messages: {
				sibling: { id: 'sibling', role: 'assistant', parentId: 'older', sources },
				latest: { id: 'latest', role: 'assistant', parentId: 'older', sources },
				older: { id: 'older', role: 'assistant', sources },
				question: { id: 'question', role: 'user', parentId: 'empty', sources },
				empty: { id: 'empty', role: 'assistant', parentId: 'latest', sources: [] }
			}
		};
		expect(latestMessageWithSources(history)).toBe(history.messages?.latest);
		history.currentId = 'sibling';
		expect(latestMessageWithSources(history)).toBe(history.messages?.sibling);
	});

	it('includes the current answer as soon as sources arrive', () => {
		const history: CitationHistory = {
			currentId: 'answer',
			messages: { answer: { id: 'answer', role: 'assistant' } }
		};
		expect(latestMessageWithSources(history)).toBeNull();
		history.messages!.answer.sources = sources;
		expect(latestMessageWithSources(history)).toBe(history.messages?.answer);
	});

	it('does not use sources from an unselected sibling', () => {
		expect(
			latestMessageWithSources({
				currentId: 'answer',
				messages: {
					answer: { id: 'answer', role: 'assistant' },
					sibling: { id: 'sibling', role: 'assistant', sources }
				}
			})
		).toBeNull();
	});

	it.each([
		undefined,
		null,
		{},
		{ currentId: null, messages: {} },
		{ currentId: 'missing', messages: {} }
	])('returns null for absent or incomplete history: %j', (history) =>
		expect(latestMessageWithSources(history)).toBeNull()
	);

	it('stops at a missing parent or a cycle', () => {
		const history: CitationHistory = {
			currentId: 'answer',
			messages: { answer: { id: 'answer', role: 'assistant', parentId: 'missing' } }
		};
		expect(latestMessageWithSources(history)).toBeNull();
		history.messages!.answer.parentId = 'answer';
		expect(latestMessageWithSources(history)).toBeNull();
	});
});

// [Gradient] Groups retain per-answer provenance even when the same file is reused.
describe('sourceGroups', () => {
	it('groups a two-turn cumulative chat in branch order and scores only used sources', () => {
		const shared = {
			source: { id: 'shared' },
			document: ['Shared'],
			distances: [0.8],
			cited_this_turn: true
		};
		const history: CitationHistory = {
			currentId: 'a2',
			messages: {
				q1: { id: 'q1', role: 'user', content: '  First\n question?  ' },
				a1: { id: 'a1', role: 'assistant', parentId: 'q1', sources: [shared] },
				q2: { id: 'q2', role: 'user', parentId: 'a1', content: 'Second question?' },
				a2: {
					id: 'a2',
					role: 'assistant',
					parentId: 'q2',
					sources: [
						{ ...shared, current_turn: false },
						{
							source: { id: 'retrieved' },
							document: ['Unused'],
							distances: [12],
							current_turn: true,
							cited_this_turn: false
						},
						{
							source: { id: 'new' },
							document: ['New'],
							distances: [0.7],
							current_turn: true,
							cited_this_turn: true
						}
					]
				}
			}
		};
		const groups = sourceGroups(history);
		expect(groups.map((group) => [group.messageId, group.question])).toEqual([
			['a1', 'First question?'],
			['a2', 'Second question?']
		]);
		expect(groups.map((group) => group.used.map((citation) => citation.id))).toEqual([
			['shared'],
			['shared', 'new']
		]);
		expect(groups[1].citations).toHaveLength(3);
		expect(groups[1].showPercentage).toBe(true);
		expect(groups[1].showRelevance).toBe(true);
	});

	it('keeps all legacy sources and an empty question when the parent is absent', () => {
		const [group] = sourceGroups({
			currentId: 'a',
			messages: { a: { id: 'a', role: 'assistant', sources } }
		});
		expect(group.used).toBe(group.citations);
		expect(group.used).toHaveLength(1);
		expect(group.question).toBe('');
	});

	it('ignores sourced sibling answers and unsourced messages', () => {
		const groups = sourceGroups({
			currentId: 'a',
			messages: {
				q: { id: 'q', role: 'user', sources },
				a: { id: 'a', role: 'assistant', parentId: 'q', sources },
				sibling: { id: 'sibling', role: 'assistant', parentId: 'q', sources }
			}
		});
		expect(groups.map((group) => group.messageId)).toEqual(['a']);
	});

	it('retains a group with no used sources even if sources were retrieved', () => {
		const [group] = sourceGroups({
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					sources: [{ ...sources[0], current_turn: true, cited_this_turn: false }]
				}
			}
		});
		expect(group.citations).toHaveLength(1);
		expect(group.used).toEqual([]);
		expect(group.showPercentage).toBe(false);
		expect(group.showRelevance).toBe(false);
	});

	it('uses current-turn flags when cited flags are absent', () => {
		const [group] = sourceGroups({
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					sources: [{ ...sources[0], current_turn: false }]
				}
			}
		});
		expect(group.used).toEqual([]);
	});

	it.each([undefined, null, {}, { currentId: 'missing', messages: {} }])(
		'handles incomplete history: %j',
		(history) => {
			expect(sourceGroups(history)).toEqual([]);
		}
	);

	it('stops at cycles without duplicating groups', () => {
		expect(
			sourceGroups({
				currentId: 'a',
				messages: {
					a: {
						id: 'a',
						role: 'assistant',
						parentId: 'a',
						sources
					}
				}
			})
		).toHaveLength(1);
	});
});
