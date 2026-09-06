/**
 * Client for the soev-solutions `assistant_onboarding` agent, reached
 * through Open WebUI's agent proxy. Streams interview questions as
 * OpenAI content deltas and the finished draft as an `assistant_draft`
 * SSE event.
 */
import { EventSourceParserStream } from 'eventsource-parser/stream';
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type OnboardingMessage = { role: 'user' | 'assistant'; content: string };

export type OnboardingEvent =
	| { type: 'content'; text: string }
	| { type: 'reasoning'; text: string }
	| { type: 'ui_block'; name: string; props: Record<string, unknown> }
	| { type: 'status'; status: Record<string, unknown> }
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
	if (parsed.event === 'status') {
		try {
			return { type: 'status', status: JSON.parse(parsed.data) };
		} catch {
			return null;
		}
	}
	if (parsed.data.startsWith('[DONE]')) {
		return { type: 'done' };
	}
	try {
		const delta = JSON.parse(parsed.data)?.choices?.[0]?.delta;
		// The agents API streams the model's thinking as `reasoning_content`
		// (vLLM / OpenAI-compatible); `reasoning` is the older vendor spelling.
		const reasoning = delta?.reasoning_content ?? delta?.reasoning;
		if (reasoning) {
			return { type: 'reasoning', text: reasoning };
		}
		const content = delta?.content;
		return content ? { type: 'content', text: content } : null;
	} catch {
		return null;
	}
}

const escapeHtml = (text: string): string =>
	text
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#x27;');

/**
 * Render streamed reasoning as the `<details type="reasoning">` block the
 * chat middleware emits (`backend/open_webui/utils/middleware.py`), so the
 * shared ResponseMessage/StatusHistory shows it as the same collapsed
 * "Thinking..." / "Thought for N seconds" bullet as in normal chat. The
 * builder bypasses that middleware (raw agent-proxy passthrough), hence
 * the client-side twin. Body lines are quoted and HTML-escaped exactly
 * like the middleware does; timestamps are epoch milliseconds.
 */
export function renderReasoningBlock(
	reasoning: string,
	{ startedAt, endedAt }: { startedAt: number; endedAt?: number }
): string {
	const display = escapeHtml(
		reasoning
			.split('\n')
			.map((line) => (line.startsWith('>') ? line : `> ${line}`))
			.join('\n')
	);
	if (endedAt === undefined) {
		return `<details type="reasoning" done="false" started_at="${startedAt}">\n<summary>Thinking…</summary>\n${display}\n</details>`;
	}
	const duration = Math.max(0, Math.floor((endedAt - startedAt) / 1000));
	return `<details type="reasoning" done="true" duration="${duration}" started_at="${startedAt}">\n<summary>Thought for ${duration} seconds</summary>\n${display}\n</details>`;
}

/** Message content for the interview bubble: reasoning block (if any) above the answer. */
export function composeInterviewContent(reasoningBlock: string, answer: string): string {
	return reasoningBlock ? `${reasoningBlock}\n${answer}` : answer;
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
	messages: OnboardingMessage[],
	files: Array<Record<string, unknown>> = []
): AsyncGenerator<OnboardingEvent> {
	// Keep every resolved file (has an id). The onboarding agent reads full
	// content via get_document_content, which is available the moment the
	// upload assigns an id (content is extracted during upload). We do NOT
	// gate on the 'uploading' status — that tracks background embedding only
	// and would drop a just-attached file in the race before the file:status
	// socket event flips it to 'uploaded'. The server's _build_attached_sources
	// expects resolved id/type entries and filters to file/collection itself.
	const payloadFiles = files.filter((f) => f && f['id']);
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
			messages,
			files: payloadFiles
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
