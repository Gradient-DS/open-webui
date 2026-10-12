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

// [Gradient] Each answer's group lists exactly the sources its own `[N]` markers cite.
describe('sourceGroups', () => {
	it('groups a two-turn cumulative chat in branch order and scores only cited sources', () => {
		const shared = { source: { id: 'shared' }, document: ['Shared'], distances: [0.8] };
		const history: CitationHistory = {
			currentId: 'a2',
			messages: {
				q1: { id: 'q1', role: 'user', content: '  First\n question?  ' },
				a1: { id: 'a1', role: 'assistant', parentId: 'q1', content: 'One [1].', sources: [shared] },
				q2: { id: 'q2', role: 'user', parentId: 'a1', content: 'Second question?' },
				a2: {
					id: 'a2',
					role: 'assistant',
					parentId: 'q2',
					content: 'Again [1] and new [3].',
					sources: [
						shared,
						{ source: { id: 'retrieved' }, document: ['Unused'], distances: [12] },
						{ source: { id: 'new' }, document: ['New'], distances: [0.7] }
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

	it('scopes an old message with the whole accumulated list to what its text cites', () => {
		const accumulated = Array.from({ length: 15 }, (_, index) => ({
			source: { id: `doc-${index + 1}` },
			document: [`Passage ${index + 1}`]
		}));
		const [group] = sourceGroups({
			currentId: 'a',
			messages: {
				a: { id: 'a', role: 'assistant', content: 'See [2] and [9].', sources: accumulated }
			}
		});
		expect(group.citations).toHaveLength(15);
		expect(group.used.map((citation) => citation.id)).toEqual(['doc-2', 'doc-9']);
		expect(group.question).toBe('');
	});

	it('reads the cites from output items', () => {
		const [group] = sourceGroups({
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					content: '',
					output: [{ type: 'message', content: [{ type: 'output_text', text: 'Cited [1]' }] }],
					sources
				}
			}
		});
		expect(group.used).toHaveLength(1);
	});

	it('ignores sourced sibling answers and unsourced messages', () => {
		const groups = sourceGroups({
			currentId: 'a',
			messages: {
				q: { id: 'q', role: 'user', content: '[1]', sources },
				a: { id: 'a', role: 'assistant', parentId: 'q', content: '[1]', sources },
				sibling: { id: 'sibling', role: 'assistant', parentId: 'q', content: '[1]', sources }
			}
		});
		expect(groups.map((group) => group.messageId)).toEqual(['a']);
	});

	it('gives an answer that cites nothing no group', () => {
		expect(
			sourceGroups({
				currentId: 'a',
				messages: { a: { id: 'a', role: 'assistant', content: 'No cites.', sources } }
			})
		).toEqual([]);
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
						content: '[1]',
						sources
					}
				}
			})
		).toHaveLength(1);
	});
});
