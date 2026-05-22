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
