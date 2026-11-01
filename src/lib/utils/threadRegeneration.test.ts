import { expect, it } from 'vitest';
import { threadRegenerationInput } from './threadRegeneration';

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
