import { describe, it, expect } from 'vitest';
import { interpretOnboardingEvent } from './index';

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
