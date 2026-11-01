import { expect, it } from 'vitest';
import { reasoningBlock, retainReasoningTiming, updateThreadReasoning } from './threadReasoning';
import { buildThreadHistory } from './threads';

const chunk = (delta: { content?: string; reasoning_content?: string }) => ({
	choices: [{ delta }]
});
it('appends live reasoning, escapes it, and measures until the first text', () => {
	const message = { content: '' };
	updateThreadReasoning(message, chunk({ reasoning_content: '<first>' }), 1000);
	expect(message.content).toContain('done="false"');
	expect(message.content).toContain('<summary>Thinking…</summary>');
	expect(message.content).toContain('&lt;first&gt;');
	updateThreadReasoning(message, chunk({ reasoning_content: '\nnext' }), 1500);
	expect(message.content.match(/<details/g)).toHaveLength(1);
	updateThreadReasoning(message, chunk({ content: 'Answer' }), 4000);
	expect(message.content).toContain('done="true" duration="3"');
	expect(message.content).toContain('next');
});

it('finalizes reasoning-only and failed turns', () => {
	for (const ending of [{ done: true }, { error: 'failed' }]) {
		const message = { content: '' };
		updateThreadReasoning(message, chunk({ reasoning_content: 'Thought' }), 1000);
		updateThreadReasoning(message, ending, 3000);
		expect(message.content).toContain('done="true" duration="2"');
	}
});

it('retains separate segments around tools and replaces them without duplication', () => {
	const live = { content: '' };
	const marker =
		'<details type="tool_calls" done="true" name="web_search">\n<summary>Searching the web…</summary>\n</details>';
	updateThreadReasoning(live, chunk({ reasoning_content: 'First' }), 1000);
	updateThreadReasoning(live, chunk({ content: marker }), 3000);
	live.content += marker;
	updateThreadReasoning(live, chunk({ reasoning_content: 'Second' }), 4000);
	updateThreadReasoning(live, chunk({ content: 'Answer' }), 7000);
	live.content += 'Answer';
	expect(live.content.indexOf('First')).toBeLessThan(live.content.indexOf(marker));
	expect(live.content.indexOf(marker)).toBeLessThan(live.content.indexOf('Second'));
	const history = buildThreadHistory(
		{
			thread_id: 'thread',
			generation: 'gen',
			title: null,
			tags: [],
			preferences: {},
			next_cursor: null,
			messages: [
				{ position: 2, content: { role: 'user', text: 'Question' }, finished: true, version: null },
				{
					position: 3,
					content: {
						role: 'assistant',
						text: '',
						reasoning: 'First',
						tool_calls: [{ id: 'tool', name: 'web_search' }]
					},
					finished: true,
					version: null
				},
				{ position: 4, content: { role: 'tool', text: '' }, finished: true, version: null },
				{
					position: 5,
					content: { role: 'assistant', text: 'Answer', reasoning: 'Second' },
					finished: true,
					version: null
				}
			]
		},
		[]
	);
	const projected = history.messages[history.currentId!];
	projected.content = retainReasoningTiming(projected.content, live.content);
	expect(projected.content.match(/type="reasoning"/g)).toHaveLength(2);
	expect(projected.content).toContain('duration="2"');
	expect(projected.content).toContain('duration="3"');
	expect(projected.content.indexOf('First')).toBeLessThan(projected.content.indexOf('tool_calls'));
	expect(projected.content.indexOf('tool_calls')).toBeLessThan(projected.content.indexOf('Second'));
	expect(retainReasoningTiming(projected.content, projected.content)).toBe(projected.content);
});

it('never carries timing onto a different projected thought', () => {
	const projected = reasoningBlock('new', true, 0);
	expect(retainReasoningTiming(projected, reasoningBlock('old', true, 10))).toBe(projected);
});
