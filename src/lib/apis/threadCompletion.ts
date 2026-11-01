import { EventSourceParserStream } from 'eventsource-parser/stream';
import { WEBUI_BASE_URL } from '$lib/constants';
import { createNewChat, updateChatFolderIdById } from '$lib/apis/chats';
import { generateOpenAIChatCompletion, getErrorMessage } from '$lib/apis/chat';

type Completion = {
	chat_id?: string;
	folder_id?: string;
	[key: string]: unknown;
};

type Callbacks = {
	created: (id: string) => Promise<void>;
	chunk: (data: Record<string, unknown>) => Promise<void>;
	signal: AbortSignal;
};

export const generateThreadCompletion = async (
	token: string,
	body: Completion,
	{ created, chunk, signal }: Callbacks,
	url = `${WEBUI_BASE_URL}/api`
) => {
	let id = body.chat_id;
	let follow = false;
	if (!id) {
		// Use the same permission checks and input builder as subsequent turns.
		const prepared = await generateOpenAIChatCompletion(
			token,
			{
				...body,
				prepare_thread: true,
				stream: false
			},
			url
		);
		if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
		const record = await createNewChat(token, prepared);
		id = record.id;
		if (body.folder_id) await updateChatFolderIdById(token, id!, body.folder_id);
		await created(id!);
		follow = true;
	}
	const response = await fetch(`${url}/chat/completions`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...body, chat_id: id, follow_thread: follow, stream: true }),
		signal
	});
	if (!response.ok) throw getErrorMessage(await response.json());
	if (!response.headers.get('Content-Type')?.includes('text/event-stream')) {
		const result = await response.json();
		await chunk(result);
		if (result.error) throw getErrorMessage(result);
		await chunk({ done: true });
		return { ...result, chat_id: id };
	}
	if (!response.body) throw new Error('Server connection failed');
	const reader = response.body
		.pipeThrough(new TextDecoderStream())
		.pipeThrough(new EventSourceParserStream())
		.getReader();
	let completed = false;
	try {
		while (!signal.aborted) {
			const { value, done } = await reader.read();
			if (done) break;
			if (value.data.trim() === '[DONE]') {
				completed = true;
				break;
			}
			const data = JSON.parse(value.data);
			await chunk(data);
			if (data.error) throw getErrorMessage(data);
		}
	} finally {
		await reader.cancel();
		reader.releaseLock();
	}
	if (!completed) throw new Error('Server connection failed');
	await chunk({ done: true });
	return { chat_id: id };
};
