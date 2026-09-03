import { describe, it, expect } from 'vitest';
import { composeInterviewContent, interpretOnboardingEvent, renderReasoningBlock } from './index';

describe('interpretOnboardingEvent', () => {
	it('extracts content from an OpenAI delta', () => {
		const result = interpretOnboardingEvent({
			event: 'message',
			data: JSON.stringify({ choices: [{ delta: { content: 'Who will use it?' } }] })
		});
		expect(result).toEqual({ type: 'content', text: 'Who will use it?' });
	});

	it('signals done on [DONE]', () => {
		expect(interpretOnboardingEvent({ event: 'message', data: '[DONE]' })).toEqual({
			type: 'done'
		});
	});

	it('parses an assistant_draft event', () => {
		const draft = { name: 'HR Helper', capabilities: { vision: true } };
		const result = interpretOnboardingEvent({
			event: 'assistant_draft',
			data: JSON.stringify(draft)
		});
		expect(result).toEqual({ type: 'draft', draft });
	});

	it('parses a status event', () => {
		const status = { description: 'Reading {{doc_title}}...', doc_title: 'a.pdf', done: false };
		const result = interpretOnboardingEvent({
			event: 'status',
			data: JSON.stringify(status)
		});
		expect(result).toEqual({ type: 'status', status });
	});

	it('parses a present_ui event as a ui_block', () => {
		const props = { id: 'q1', options: ['A', 'B', 'C'], question: 'Pick?' };
		const result = interpretOnboardingEvent({
			event: 'present_ui',
			data: JSON.stringify({ name: 'choice', props })
		});
		expect(result).toEqual({ type: 'ui_block', name: 'choice', props });
	});

	it('returns null for a present_ui event missing name or props', () => {
		expect(
			interpretOnboardingEvent({ event: 'present_ui', data: JSON.stringify({ name: 'choice' }) })
		).toBeNull();
		expect(
			interpretOnboardingEvent({
				event: 'present_ui',
				data: JSON.stringify({ props: { id: 'x' } })
			})
		).toBeNull();
	});

	it('returns null for an unparseable or empty event', () => {
		expect(interpretOnboardingEvent({ event: 'message', data: 'not json' })).toBeNull();
		expect(
			interpretOnboardingEvent({
				event: 'message',
				data: JSON.stringify({ choices: [{ delta: {} }] })
			})
		).toBeNull();
	});
});

describe('interpretOnboardingEvent — reasoning deltas', () => {
	it('extracts reasoning_content from an OpenAI delta', () => {
		const result = interpretOnboardingEvent({
			event: 'message',
			data: JSON.stringify({ choices: [{ delta: { reasoning_content: 'Let me think.' } }] })
		});
		expect(result).toEqual({ type: 'reasoning', text: 'Let me think.' });
	});

	it('falls back to the bare reasoning field', () => {
		const result = interpretOnboardingEvent({
			event: 'message',
			data: JSON.stringify({ choices: [{ delta: { reasoning: 'Hmm.' } }] })
		});
		expect(result).toEqual({ type: 'reasoning', text: 'Hmm.' });
	});

	it('ignores an empty reasoning delta', () => {
		expect(
			interpretOnboardingEvent({
				event: 'message',
				data: JSON.stringify({ choices: [{ delta: { reasoning_content: '', content: '' } }] })
			})
		).toBeNull();
	});
});

describe('renderReasoningBlock', () => {
	it('renders an in-progress block the way the chat middleware does', () => {
		const block = renderReasoningBlock('Let me think.\nAsk about scope.', {
			startedAt: 1700000000000
		});
		expect(block).toBe(
			'<details type="reasoning" done="false" started_at="1700000000000">\n' +
				'<summary>Thinking…</summary>\n' +
				'&gt; Let me think.\n&gt; Ask about scope.\n' +
				'</details>'
		);
	});

	it('renders a finished block with a whole-second duration', () => {
		const block = renderReasoningBlock('Done.', {
			startedAt: 1700000000000,
			endedAt: 1700000002900
		});
		expect(block).toBe(
			'<details type="reasoning" done="true" duration="2" started_at="1700000000000">\n' +
				'<summary>Thought for 2 seconds</summary>\n' +
				'&gt; Done.\n' +
				'</details>'
		);
	});

	it('escapes markup inside the reasoning and keeps existing quote markers', () => {
		const block = renderReasoningBlock('> quoted <b>bold</b> & "x"', {
			startedAt: 1,
			endedAt: 1
		});
		expect(block).toContain('&gt; quoted &lt;b&gt;bold&lt;/b&gt; &amp; &quot;x&quot;');
		expect(block).not.toContain('&gt; &gt; quoted');
	});
});

describe('composeInterviewContent', () => {
	it('returns the answer alone when there is no reasoning', () => {
		expect(composeInterviewContent('', 'Wat wil je?')).toBe('Wat wil je?');
	});

	it('puts the reasoning block before the answer', () => {
		expect(composeInterviewContent('<details type="reasoning"></details>', 'Wat wil je?')).toBe(
			'<details type="reasoning"></details>\nWat wil je?'
		);
	});
});
