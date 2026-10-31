import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { config } from '$lib/stores';
import { generateThreadCompletion } from './threadCompletion';

vi.mock('$lib/utils', () => ({ getTimeRange: vi.fn() }));
vi.mock('$lib/stores', async () => ({ config: (await import('svelte/store')).writable(null) }));
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const answer = () =>
	new Response('data: {"choices":[{"delta":{"content":"First answer"}}]}\n\ndata: [DONE]\n\n', {
		headers: { 'Content-Type': 'text/event-stream' }
	});
beforeEach(() =>
	config.set({ features: { enable_v2_threads: true } } as NonNullable<
		Parameters<typeof config.set>[0]
	>)
);
afterEach(() => vi.unstubAllGlobals());

it('prepares the selected turn, creates once, moves to the folder, then follows without repeating input', async () => {
	const form = {
		agent: 'test',
		input: { text: 'First input', tools: { web_search: 'off' } },
		model: 'provider/model'
	};
	const fetch = vi
		.fn()
		.mockResolvedValueOnce(json(form))
		.mockResolvedValueOnce(json({ id: 'thread' }))
		.mockResolvedValueOnce(json({ id: 'thread', folder_id: 'folder' }))
		.mockResolvedValueOnce(answer());
	vi.stubGlobal('fetch', fetch);
	const created = vi.fn();
	const chunk = vi.fn();
	await generateThreadCompletion(
		'token',
		{ model: 'provider/model', user_message: { content: 'First input' }, folder_id: 'folder' },
		{ created, chunk, signal: new AbortController().signal }
	);
	expect(fetch.mock.calls.map(([url]) => url)).toEqual([
		'/api/chat/completions',
		'/api/v1/chats/new',
		'/api/v1/chats/thread/folder',
		'/api/chat/completions'
	]);
	expect(JSON.parse(fetch.mock.calls[0][1].body).prepare_thread).toBe(true);
	expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(form);
	expect(JSON.parse(fetch.mock.calls[3][1].body)).toMatchObject({
		chat_id: 'thread',
		follow_thread: true,
		stream: true
	});
	expect(created).toHaveBeenCalledWith('thread');
	expect(chunk.mock.calls).toEqual([
		[{ choices: [{ delta: { content: 'First answer' } }] }],
		[{ done: true }]
	]);
});

it('appends subsequent turns without creating or following the previous turn', async () => {
	const fetch = vi.fn().mockResolvedValueOnce(answer());
	vi.stubGlobal('fetch', fetch);
	const created = vi.fn();
	await generateThreadCompletion(
		'token',
		{ chat_id: 'thread', model: 'provider/model', user_message: { content: 'Next input' } },
		{ created, chunk: vi.fn(), signal: new AbortController().signal }
	);
	expect(fetch).toHaveBeenCalledTimes(1);
	expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
		chat_id: 'thread',
		follow_thread: false
	});
	expect(created).not.toHaveBeenCalled();
});

it('does not create a thread when preparation fails', async () => {
	const fetch = vi.fn().mockResolvedValueOnce(json({ detail: 'Access prohibited' }, 403));
	vi.stubGlobal('fetch', fetch);
	await expect(
		generateThreadCompletion(
			'token',
			{ model: 'model' },
			{ created: vi.fn(), chunk: vi.fn(), signal: new AbortController().signal }
		)
	).rejects.toBe('Access prohibited');
	expect(fetch).toHaveBeenCalledTimes(1);
});

it('surfaces truncated streams and runtime errors and releases the loading state', async () => {
	for (const content of [
		'data: {"choices":[]}\n\n',
		'data: {"error":{"message":"Unavailable"}}\n\n'
	]) {
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValueOnce(
					new Response(content, { headers: { 'Content-Type': 'text/event-stream' } })
				)
		);
		const chunk = vi.fn();
		await expect(
			generateThreadCompletion(
				'token',
				{ chat_id: 'thread' },
				{ created: vi.fn(), chunk, signal: new AbortController().signal }
			)
		).rejects.toBeTruthy();
		expect(chunk).not.toHaveBeenCalledWith({ done: true });
	}
});

it('does not create a thread after preparation is cancelled', async () => {
	const controller = new AbortController();
	controller.abort();
	const fetch = vi
		.fn()
		.mockResolvedValueOnce(json({ agent: 'test', input: { text: 'Input' }, model: 'model' }));
	vi.stubGlobal('fetch', fetch);
	await expect(
		generateThreadCompletion(
			'token',
			{ model: 'model' },
			{ created: vi.fn(), chunk: vi.fn(), signal: controller.signal }
		)
	).rejects.toMatchObject({ name: 'AbortError' });
	expect(fetch).toHaveBeenCalledTimes(1);
});
