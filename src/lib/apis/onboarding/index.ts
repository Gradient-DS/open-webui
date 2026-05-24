/**
 * Client for the genai-utils `assistant_onboarding` agent, reached
 * through Open WebUI's agent proxy. Streams interview questions as
 * OpenAI content deltas and the finished draft as an `assistant_draft`
 * SSE event.
 */
import { EventSourceParserStream } from 'eventsource-parser/stream';
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type OnboardingMessage = { role: 'user' | 'assistant'; content: string };

export type OnboardingEvent =
	| { type: 'content'; text: string }
	| { type: 'ui_block'; name: string; props: Record<string, any> }
	| { type: 'draft'; draft: any }
	| { type: 'done' };

/** Interpret one parsed SSE event. Returns null for noise. */
export function interpretOnboardingEvent(parsed: {
	event?: string;
	data: string;
}): OnboardingEvent | null {
	if (parsed.event === 'assistant_draft') {
		try {
			return { type: 'draft', draft: JSON.parse(parsed.data) };
		} catch {
			return null;
		}
	}
	if (parsed.event === 'present_ui') {
		try {
			const payload = JSON.parse(parsed.data);
			if (payload?.name && payload?.props) {
				return { type: 'ui_block', name: payload.name, props: payload.props };
			}
			return null;
		} catch {
			return null;
		}
	}
	if (parsed.data.startsWith('[DONE]')) {
		return { type: 'done' };
	}
	try {
		const content = JSON.parse(parsed.data)?.choices?.[0]?.delta?.content;
		return content ? { type: 'content', text: content } : null;
	} catch {
		return null;
	}
}

/**
 * Stream one onboarding turn. Yields `OnboardingEvent`s until the
 * agent finishes (a `draft` or `done` event).
 *
 * @throws Error if the agent proxy is unreachable or returns non-OK.
 */
export async function* streamOnboarding(
	token: string,
	chatId: string,
	messages: OnboardingMessage[]
): AsyncGenerator<OnboardingEvent> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/agent/chat/completions`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			agent: 'assistant_onboarding',
			chat_id: chatId,
			stream: true,
			messages
		})
	});

	if (!res.ok || !res.body) {
		throw new Error(`Onboarding agent unavailable (status ${res.status})`);
	}

	const reader = res.body
		.pipeThrough(new TextDecoderStream())
		.pipeThrough(new EventSourceParserStream())
		.getReader();

	while (true) {
		const { value, done } = await reader.read();
		if (done) {
			yield { type: 'done' };
			break;
		}
		if (!value) continue;
		const interpreted = interpretOnboardingEvent({ event: value.event, data: value.data });
		if (interpreted) {
			yield interpreted;
			if (interpreted.type === 'draft' || interpreted.type === 'done') break;
		}
	}
}
