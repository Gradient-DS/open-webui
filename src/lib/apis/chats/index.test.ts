import { afterEach, expect, it, vi } from 'vitest';
import { addTagById, getTagsById } from './index';

vi.mock('$lib/stores', async () => ({
	config: (await import('svelte/store')).writable(undefined)
}));
vi.mock('$lib/utils', () => ({ getTimeRange: vi.fn() }));
afterEach(() => vi.unstubAllGlobals());

it('adapts canonical public tags for the existing tag component', async () => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify(['fictional_grove'])))
	);
	expect(await getTagsById('fictional-token', 'fictional-thread')).toEqual([
		{ name: 'fictional_grove' }
	]);
});

it('keeps legacy tag objects readable', async () => {
	const tags = [{ id: 'fictional-tag', name: 'Fictional Grove' }];
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify(tags)))
	);
	expect(await getTagsById('fictional-token', 'fictional-chat')).toEqual(tags);
});

it('reports a non-owner tag refusal instead of swallowing its state', async () => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify({ state: 'unavailable' }), { status: 404 }))
	);
	await expect(addTagById('fictional-token', 'fictional-thread', 'Fictional Grove')).rejects.toBe(
		'This chat is unavailable'
	);
});

it('normalizes the global v2 tag list and surfaces sidebar state errors', async () => {
	const { getAllTags, getChatList } = await import('./index');
	vi.stubGlobal(
		'fetch',
		vi
			.fn()
			.mockResolvedValueOnce(new Response(JSON.stringify(['fictional_grove'])))
			.mockResolvedValueOnce(new Response(JSON.stringify({ state: 'reconnect' }), { status: 503 }))
	);
	expect(await getAllTags('token')).toEqual([{ name: 'fictional_grove' }]);
	await expect(getChatList('token', 1)).rejects.toBe('Reconnect to view this chat');
});

it('uses exact runtime creation and metadata bodies in v2 and preserves legacy bodies', async () => {
	const { config } = await import('$lib/stores');
	const { createNewChat, updateChatById, updateChatFolderIdById, forkChatById } =
		await import('./index');
	const fetch = vi.fn<typeof globalThis.fetch>(
		async () => new Response(JSON.stringify({ id: 'thread' }))
	);
	vi.stubGlobal('fetch', fetch);
	const { get } = await import('svelte/store');
	const previous = get(config);
	try {
		config.set({
			...previous,
			features: { ...previous?.features, enable_v2_threads: true }
		} as NonNullable<Parameters<typeof config.set>[0]>);
		const form = { agent: 'test', input: { text: 'First input' }, model: 'provider/model' };
		await createNewChat('token', form, 'folder', { private: true });
		expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual(form);
		await expect(createNewChat('token', { history: {} })).rejects.toBe(
			'Reload this chat to continue'
		);
		const features = { web_search: false, document_writer: true, tool_ids: [] };
		await updateChatById('token', 'thread', { title: 'Changed', tags: ['grove'], features });
		expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({
			title: 'Changed',
			tags: ['grove'],
			features
		});
		await expect(updateChatById('token', 'thread', { history: {} })).rejects.toBe(
			'Reload this chat to continue'
		);
		await updateChatFolderIdById('token', 'thread');
		expect(JSON.parse(String(fetch.mock.calls[2][1]?.body))).toEqual({ folder_id: null });
		await forkChatById('token', 'thread', 12);
		expect(JSON.parse(String(fetch.mock.calls[3][1]?.body))).toEqual({ at: 12 });
		config.set({
			...previous,
			features: { ...previous?.features, enable_v2_threads: false }
		} as NonNullable<Parameters<typeof config.set>[0]>);
		await createNewChat('token', { history: {} }, 'folder', { name: 'value' });
		expect(JSON.parse(String(fetch.mock.calls[4][1]?.body))).toEqual({
			chat: { history: {} },
			folder_id: 'folder',
			variables: { name: 'value' }
		});
		await updateChatById('token', 'chat', { params: {} });
		expect(JSON.parse(String(fetch.mock.calls[5][1]?.body))).toEqual({ chat: { params: {} } });
	} finally {
		config.set(previous);
	}
});

it('keeps search available in v2 while refusing unsupported bulk export', async () => {
	const { config } = await import('$lib/stores');
	const { getChatListBySearchText, getAllChats, getChatById } = await import('./index');
	const { get } = await import('svelte/store');
	const previous = get(config);
	const fetch = vi
		.fn()
		.mockResolvedValueOnce(new Response(JSON.stringify([])))
		.mockResolvedValueOnce(new Response(JSON.stringify({ thread_id: 'thread', messages: [] })));
	vi.stubGlobal('fetch', fetch);
	try {
		config.set({ features: { enable_v2_threads: true } } as NonNullable<
			Parameters<typeof config.set>[0]
		>);
		expect(await getChatListBySearchText('token', 'Grove', 2)).toEqual([]);
		expect(fetch.mock.calls[0][0]).toBe('/api/v1/chats/search?text=Grove&page=2');
		await expect(getAllChats('token')).rejects.toBe('This chat is unavailable');
		await getChatById('token', 'thread', 'opaque cursor');
		expect(fetch.mock.calls[1][0]).toBe('/api/v1/chats/thread?limit=20&before=opaque+cursor');
		expect(fetch).toHaveBeenCalledTimes(2);
	} finally {
		config.set(previous);
	}
});
