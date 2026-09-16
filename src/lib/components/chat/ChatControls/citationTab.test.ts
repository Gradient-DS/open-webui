import { describe, expect, it } from 'vitest';
import { latestMessageWithSources, type CitationHistory } from './citationTab';

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
