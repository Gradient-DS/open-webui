import { expect, it } from 'vitest';
import {
	queueThreadRegeneration,
	takeThreadRegeneration,
	threadRegenerationInput
} from './threadRegeneration';

it('consumes a replay only once, scoped to its fork', () => {
	const input = {
		content: 'Question',
		model: 'model',
		features: {},
		regenerate_from: { thread_id: 'source', position: 2 }
	};
	queueThreadRegeneration('fork', input);
	expect(takeThreadRegeneration('other')).toBeUndefined();
	expect(takeThreadRegeneration('fork')).toEqual(input);
	expect(takeThreadRegeneration('fork')).toBeUndefined();
});
it('refuses an input without a prior event or an unfinished turn', () => {
	for (const [position, finished] of [
		[1, true],
		[2, false]
	] as const) {
		expect(() =>
			threadRegenerationInput(
				{
					thread_id: 'thread',
					generation: 'gen',
					title: null,
					preferences: {},
					tags: [],
					next_cursor: null,
					messages: [
						{ position, content: { role: 'user', text: 'Question' }, finished, version: null }
					]
				},
				'model',
				{}
			)
		).toThrow();
	}
});
