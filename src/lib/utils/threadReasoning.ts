// Keep transport state off persisted messages; projection replacement owns the final content.
type Message = { content: string };
type Segment = { offset: number; text: string; started: number; length: number };
const active = new WeakMap<Message, Segment>();
const escapeHtml = (text: string) =>
	text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export const reasoningBlock = (
	text: string,
	done: boolean,
	duration?: number,
	started?: number
) => {
	const body = text
		.trim()
		.split('\n')
		.map((line) => (line.startsWith('>') ? line : `> ${line}`))
		.join('\n');
	return `<details type="reasoning" done="${done}"${duration === undefined ? '' : ` duration="${duration}"`}${started === undefined ? '' : ` started_at="${started / 1000}"`}>\n<summary>${done ? `Thought for ${duration ?? 0} seconds` : 'Thinking…'}</summary>\n${escapeHtml(body)}\n</details>`;
};

export const updateThreadReasoning = (
	message: Message,
	data: {
		choices?: {
			delta?: { reasoning_content?: string; content?: string };
			message?: { reasoning_content?: string; content?: string };
		}[];
		done?: boolean;
		error?: unknown;
	},
	now = Date.now()
) => {
	const delta = data.choices?.[0]?.delta ?? data.choices?.[0]?.message;
	let segment = active.get(message);
	if (delta?.reasoning_content) {
		if (!segment) {
			const separator = message.content ? '\n\n' : '';
			message.content += separator;
			segment = { offset: message.content.length, text: '', started: now, length: 0 };
			active.set(message, segment);
		}
		segment.text += delta.reasoning_content;
	}
	if (!segment) return;
	const done = Boolean(delta?.content || data.done || data.error);
	const block =
		reasoningBlock(
			segment.text,
			done,
			done ? Math.max(0, Math.floor((now - segment.started) / 1000)) : undefined,
			segment.started
		) + (done ? '\n\n' : '');
	message.content =
		message.content.slice(0, segment.offset) +
		block +
		message.content.slice(segment.offset + segment.length);
	segment.length = block.length;
	if (done) active.delete(message);
};

// The durable projection has no timing. Retain measured durations when replacing live blocks.
export const retainReasoningTiming = (content: string, previous: string) => {
	const blocks = [...previous.matchAll(/<details type="reasoning"[^>]*>[\s\S]*?<\/details>/g)];
	let index = 0;
	const body = (block: string) =>
		block
			.replace(/^<details[^>]*>\s*<summary>[\s\S]*?<\/summary>\s*/, '')
			.replace(/\s*<\/details>$/, '');
	return content.replace(/<details type="reasoning"[^>]*>[\s\S]*?<\/details>/g, (block) => {
		const old = blocks[index++]?.[0];
		return old && old.includes('done="true"') && body(old) === body(block) ? old : block;
	});
};
