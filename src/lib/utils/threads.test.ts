import { describe, expect, it, vi } from 'vitest';
import websearch from './fixtures/projection-websearch.json';
import { buildFullSourceList } from './citations';
import {
	ThreadError,
	stateText,
	type IndexItem,
	type MessageView,
	type ProjectionPage,
	type ThreadAPI,
	type SidebarItem
} from '$lib/apis/threads';
import {
	buildThreadHistory,
	extendProjectionToTurn,
	filterSidebar,
	mergeProjection,
	ThreadSidebar,
	ThreadWindow
} from './threads';
import en from '$lib/i18n/locales/en-US/translation.json';
import nl from '$lib/i18n/locales/nl-NL/translation.json';
import windowSource from '$lib/components/chat/ThreadWindow.svelte?raw';
import sidebarSource from '$lib/components/layout/ThreadSidebar.svelte?raw';
import stateSource from '$lib/components/common/ThreadState.svelte?raw';
import shareSource from '$lib/components/chat/ThreadShareModal.svelte?raw';
import sharedSource from '$lib/components/chat/SharedThread.svelte?raw';
import mainSidebarSource from '$lib/components/layout/Sidebar.svelte?raw';
import folderSource from '$lib/components/layout/Sidebar/RecursiveFolder.svelte?raw';
import sharedFolderSource from '$lib/components/layout/Sidebar/SharedFolderItem.svelte?raw';
import searchSource from '$lib/components/layout/SearchModal.svelte?raw';
import archivedSource from '$lib/components/chat/Settings/ArchivedChats.svelte?raw';
import { sharingAPI, shareStateText } from './threadSharing';

it('provides English keys and Dutch translations for every thread UI label', () => {
	const keys = [
		...(windowSource + sidebarSource + stateSource + shareSource + sharedSource).matchAll(
			/\$i18n\.t\('([^']+)'/g
		)
	].map((match) => match[1]);
	for (const key of keys) {
		expect(Object.hasOwn(en, key), key).toBe(true);
		expect((nl as Record<string, string>)[key], key).toBeTruthy();
	}
});

const message = (position: number, text = `Fictional ${position}`): MessageView => ({
	position,
	content: { role: position % 2 ? 'user' : 'assistant', text },
	finished: true,
	version: `opaque-${position}`
});
const page = (
	positions: number[],
	cursor: string | null = null,
	thread = 'fictional-thread'
): ProjectionPage => ({
	thread_id: thread,
	generation: 'fictional-generation',
	title: 'Fictional title',
	tags: [],
	preferences: {},
	messages: positions.map((p) => message(p)),
	next_cursor: cursor
});
const fork = (id = 'fictional-fork', at = 2): IndexItem => ({
	thread_id: id,
	owned: true,
	title: 'Fictional fork',
	tags: [],
	updated_at: '2026-10-25T10:00:00Z',
	parent_thread: 'fictional-thread',
	parent_position: at
});
const api = (): ThreadAPI => ({
	authorize: vi.fn(),
	projection: vi.fn(async () => ({
		page: page([3, 4], 'opaque-older'),
		etag: 'opaque-etag',
		notModified: false
	})),
	forks: vi.fn(async () => ({ items: [fork()], next_cursor: null })),
	versions: vi.fn(async () => ({ items: [] })),
	fork: vi.fn(async () => ({ thread_id: 'fictional-fork' })),
	index: vi.fn(async () => ({ items: [], next_cursor: null }))
});
const deferred = <T>() => {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
};

describe('projection history', () => {
	it('folds the live web-search projection into one assistant per turn with reasoning, tools and citations', () => {
		const history = buildThreadHistory(websearch, []);
		const messages = Object.values(history.messages);
		expect(messages.map((m) => m.role)).toEqual(['assistant', 'user', 'assistant']);
		const [first, , last] = messages;
		expect(first.content).toContain('<details type="reasoning"');
		expect(first.content).toContain('I should search the web for this.');
		expect(first.content).toContain('<details type="tool_calls"');
		expect(first.content).toContain('Python 3.14.0 is uitgebracht');
		expect(first.content).toContain('Python [1][2].');
		expect(first.statusHistory).toEqual(
			expect.arrayContaining([expect.objectContaining({ action: 'web_search', done: true })])
		);
		expect(first.sources).toEqual(
			expect.arrayContaining([
				expect.objectContaining({
					n: 1,
					source: expect.objectContaining({
						url: 'https://www.python.org/downloads/release/python-3140/'
					})
				})
			])
		);
		expect(buildFullSourceList(first.sources as unknown[])).toHaveLength(5);
		expect(last.content).toContain('Template strings');
		expect(last.content).toContain('[15]');
		expect(last.position).toBe(23);
		expect(last.version).toBe(websearch.messages.at(-1)!.version);
		expect(first.id).toBe(`${websearch.thread_id}:${websearch.generation}:3`);
	});

	it('keeps a turn id as tool steps arrive and forks at their original platform positions', () => {
		const projected = { ...websearch, messages: websearch.messages.slice(0, 1) };
		const initial = Object.values(buildThreadHistory(projected, []).messages)[0];
		const history = buildThreadHistory(websearch, [
			{ ...fork('child', 4), parent_thread: websearch.thread_id }
		]);
		const finished = Object.values(history.messages)[0];
		expect(finished.id).toBe(initial.id);
		expect(finished.position).toBe(5);
		expect(finished.positions).toEqual([3, 4, 5]);
		expect(history.versions[5]).toContainEqual({ threadId: 'child', at: 4 });
	});

	it('inserts markers at code point offsets and ignores unknown optional fields', () => {
		const projected = page([2]);
		projected.messages[0].content = {
			role: 'assistant',
			text: '😀 Hi',
			future: true,
			markers: [
				null,
				{ at: 1, text: ' [7]', cited: [{ written: 7, source: 'https://example.com' }] }
			],
			tool_calls: [null, { future: true }]
		};
		const [answer] = Object.values(buildThreadHistory(projected, []).messages);
		expect(answer.content).toBe('😀 [7] Hi');
		expect(buildFullSourceList(answer.sources as unknown[])).toHaveLength(1);
		expect(answer).not.toHaveProperty('future');
	});

	it('keeps texts in order, ignores unknown keys and preserves enriched sources', () => {
		const projected = page([2, 4, 6]);
		projected.messages.forEach((m, i) => {
			m.content = { role: 'assistant', text: ['First', '', 'Last'][i], future: { anything: true } };
		});
		(projected.messages[2].content as Record<string, unknown>).sources = [
			{ n: 7, source: { id: 'url', name: 'Title', url: 'url' }, document: ['Snippet'] }
		];
		const [answer] = Object.values(buildThreadHistory(projected, []).messages);
		expect(answer.content).toBe('First\n\nLast');
		expect(answer.sources).toEqual(
			(projected.messages[2].content as Record<string, unknown>).sources
		);
	});

	it('builds a linked active branch with immutable versions and no orphan parents at a page boundary', () => {
		const history = buildThreadHistory(page([5, 6]), []);
		const [first, last] = Object.values(history.messages);
		expect(first.parentId).toBeNull();
		expect(first.childrenIds).toEqual([last.id]);
		expect(last.parentId).toBe(first.id);
		expect(history.currentId).toBe(last.id);
		expect(last.version).toBe('opaque-6');
		expect(last.done).toBe(true);
	});

	it('arrows contain only returned forks at the recorded parent positions, without invented message bodies', () => {
		const history = buildThreadHistory(page([1, 2, 3]), [
			fork(),
			fork(),
			fork('fictional-second', 2),
			{ ...fork('fictional-unrelated'), parent_thread: 'fictional-other' }
		]);
		expect(history.versions[2]).toEqual([
			{ threadId: 'fictional-thread', at: 2 },
			{ threadId: 'fictional-fork', at: 2 },
			{ threadId: 'fictional-second', at: 2 }
		]);
		expect(Object.keys(history.messages)).toHaveLength(3);
	});

	it('preserves presentation fields while ignoring structural fields in projected content', () => {
		const projected = page([2]);
		projected.messages[0].content = {
			role: 'assistant',
			text: 'Fictional answer [1]',
			id: 'spoofed',
			parentId: 'spoofed',
			sources: [{ source: { name: 'Fictional source' } }],
			statusHistory: [{ action: 'search', done: true }],
			output: [],
			reasoning: ['Fictional reasoning']
		};
		const result = Object.values(buildThreadHistory(projected, []).messages)[0];
		expect(result.id).not.toBe('spoofed');
		expect(result.parentId).toBeNull();
		expect(result.sources).toEqual(
			(projected.messages[0].content as Record<string, unknown>).sources
		);
		expect(result.reasoning).toEqual(['Fictional reasoning']);
		expect(result.statusHistory).toEqual([{ action: 'search', done: true }]);
	});

	it('does not serialize unsupported content or accept duplicate positions', () => {
		expect(() =>
			buildThreadHistory({ ...page([]), messages: [{ ...message(1), content: [] }] }, [])
		).toThrow(ThreadError);
		expect(() => buildThreadHistory(page([1, 1]), [])).toThrow(ThreadError);
	});

	it('merges overlapping windows while preserving the latest streamed message', () => {
		const current = page([3, 4]);
		current.messages[0] = {
			...message(3, 'Fictional live answer'),
			finished: false,
			version: null
		};
		const merged = mergeProjection(current, page([1, 2, 3]));
		expect(merged.messages.map((m) => m.position)).toEqual([1, 2, 3, 4]);
		expect(merged.messages[2].content).toEqual(current.messages[0].content);
		expect(() => mergeProjection(current, { ...page([1]), generation: 'restored' })).toThrow(
			ThreadError
		);
	});
});

describe('whole turn paging', () => {
	const toolPage = (positions: number[], cursor: string | null = null) => ({
		...page(positions, cursor),
		messages: positions.map((position) => ({
			...message(position),
			content: { role: position === 1 ? 'user' : position % 2 ? 'tool' : 'assistant', text: '' }
		}))
	});

	it('extends a single tool-heavy turn to its user message and removes the older button cursor', async () => {
		const older = vi.fn(async () => toolPage(Array.from({ length: 20 }, (_, i) => i + 1)));
		const result = await extendProjectionToTurn(
			toolPage(
				Array.from({ length: 20 }, (_, i) => i + 21),
				'before-21'
			),
			older
		);
		expect(older).toHaveBeenCalledExactlyOnceWith('before-21');
		expect(result.messages).toHaveLength(40);
		expect(result.messages[0].content).toMatchObject({ role: 'user' });
		expect(Boolean(result.next_cursor)).toBe(false);
		expect(Object.values(buildThreadHistory(result, []).messages).map((m) => m.role)).toEqual([
			'user',
			'assistant'
		]);
	});

	it('extends multiple pages, keeping a cursor only for older turns', async () => {
		const older = vi
			.fn()
			.mockResolvedValueOnce(toolPage([6, 7], 'before-6'))
			.mockResolvedValueOnce(page([3, 4, 5], 'before-3'));
		const result = await extendProjectionToTurn(toolPage([8, 9], 'before-8'), older);
		expect(older.mock.calls).toEqual([['before-8'], ['before-6']]);
		expect(result.messages.map((m) => m.position)).toEqual([3, 4, 5, 6, 7, 8, 9]);
		expect(result.next_cursor).toBe('before-3');
	});

	it('leaves a page starting at a user message alone', async () => {
		const older = vi.fn();
		const initial = page([3, 4], 'before-3');
		expect(await extendProjectionToTurn(initial, older)).toBe(initial);
		expect(older).not.toHaveBeenCalled();
	});

	it('stops at the beginning even if no user message exists', async () => {
		const older = vi.fn(async () => toolPage([2]));
		const result = await extendProjectionToTurn(toolPage([3, 4], 'before-3'), older);
		expect(result.messages.map((m) => m.position)).toEqual([2, 3, 4]);
		expect(result.next_cursor).toBeNull();
	});

	it('bounds a load to ten transport pages without publishing an incomplete turn', async () => {
		let position = 100;
		const older = vi.fn(async () => toolPage([--position], `before-${position}`));
		await expect(extendProjectionToTurn(toolPage([100], 'before-100'), older)).rejects.toThrow(
			ThreadError
		);
		expect(older).toHaveBeenCalledTimes(9);
	});

	it('rejects repeated cursors and generation changes', async () => {
		await expect(
			extendProjectionToTurn(toolPage([4], 'same'), async () => toolPage([2], 'same'))
		).rejects.toThrow(ThreadError);
		await expect(
			extendProjectionToTurn(toolPage([4], 'older'), async () => ({
				...toolPage([1, 2]),
				generation: 'changed'
			}))
		).rejects.toThrow(ThreadError);
	});
});

describe('window paging and streams', () => {
	it('refreshes parent forks when opening a version and preserves its family while paging', async () => {
		const backend = api();
		vi.mocked(backend.projection).mockResolvedValueOnce({
			page: page([3, 4], 'opaque-older', 'fictional-fork'),
			etag: null,
			notModified: false
		});
		vi.mocked(backend.forks).mockImplementation(async (id) => ({
			items: id === 'fictional-thread' ? [fork()] : [],
			next_cursor: null
		}));
		const controller = new ThreadWindow(backend);
		await controller.open('fictional-fork', { threadId: 'fictional-thread', at: 2 });
		expect(controller.snapshot.family).toEqual([
			{ threadId: 'fictional-thread', at: 2 },
			{ threadId: 'fictional-fork', at: 2 }
		]);
		expect(backend.forks).toHaveBeenCalledWith('fictional-thread', '');
		vi.mocked(backend.projection).mockResolvedValueOnce({
			page: page([1, 2], null, 'fictional-fork'),
			etag: null,
			notModified: false
		});
		await controller.older();
		expect(controller.snapshot.family).toHaveLength(2);
	});

	it('keeps a readable clone without exposing inaccessible parent versions', async () => {
		const backend = api();
		vi.mocked(backend.projection).mockResolvedValueOnce({
			page: page([3], null, 'fictional-fork'),
			etag: null,
			notModified: false
		});
		vi.mocked(backend.forks)
			.mockResolvedValueOnce({ items: [], next_cursor: null })
			.mockRejectedValueOnce(new ThreadError('unavailable'));
		const controller = new ThreadWindow(backend);
		await controller.open('fictional-fork', { threadId: 'fictional-thread', at: 2 });
		expect(controller.snapshot.page?.thread_id).toBe('fictional-fork');
		expect(controller.snapshot.family).toBeUndefined();
		expect(controller.snapshot.state).toBeNull();
	});

	it('follows fork cursors, and uses opaque before cursors for scroll paging', async () => {
		const backend = api();
		vi.mocked(backend.forks)
			.mockResolvedValueOnce({ items: [fork()], next_cursor: 'opaque-forks' })
			.mockResolvedValueOnce({ items: [fork('fictional-second', 2)], next_cursor: null });
		const controller = new ThreadWindow(backend);
		await controller.open('fictional-thread');
		expect(backend.forks).toHaveBeenLastCalledWith('fictional-thread', 'opaque-forks');
		vi.mocked(backend.projection).mockResolvedValueOnce({
			page: page([1, 2]),
			etag: null,
			notModified: false
		});
		await controller.older();
		expect(backend.projection).toHaveBeenLastCalledWith('fictional-thread', 'opaque-older');
		expect(Object.values(controller.snapshot.history.messages).map((m) => m.position)).toEqual([
			1, 2, 3, 4
		]);
		expect(controller.snapshot.page?.next_cursor).toBeNull();
		await controller.older();
		expect(backend.projection).toHaveBeenCalledTimes(2);
	});

	it('coalesces simultaneous scroll requests', async () => {
		const backend = api();
		const controller = new ThreadWindow(backend);
		await controller.open('fictional-thread');
		const next = deferred<Awaited<ReturnType<ThreadAPI['projection']>>>();
		vi.mocked(backend.projection).mockReturnValueOnce(next.promise);
		const first = controller.older();
		const second = controller.older();
		expect(first).toBe(second);
		next.resolve({ page: page([1, 2]), etag: null, notModified: false });
		await first;
		expect(backend.projection).toHaveBeenCalledTimes(2);
	});

	it('drops delayed page replies after navigation or a session reset', async () => {
		const backend = api();
		const controller = new ThreadWindow(backend);
		await controller.open('fictional-thread');
		const next = deferred<Awaited<ReturnType<ThreadAPI['projection']>>>();
		vi.mocked(backend.projection).mockReturnValueOnce(next.promise);
		const pending = controller.older();
		vi.mocked(backend.projection).mockResolvedValueOnce({
			page: page([9], null, 'fictional-new'),
			etag: null,
			notModified: false
		});
		await controller.open('fictional-new');
		next.resolve({ page: page([1, 2]), etag: null, notModified: false });
		await pending;
		expect(controller.snapshot.page?.thread_id).toBe('fictional-new');
		controller.clear();
		expect(controller.snapshot.history.messages).toEqual({});
	});

	it('streams into the active window and ignores another branch, generation, and finished message edits', async () => {
		const controller = new ThreadWindow(api());
		await controller.open('fictional-thread');
		controller.stream('fictional-thread', 'fictional-generation', {
			...message(5, 'Fictional partial'),
			finished: false,
			version: null
		});
		controller.stream('fictional-thread', 'fictional-generation', message(5, 'Fictional final'));
		controller.stream(
			'fictional-thread',
			'fictional-generation',
			message(5, 'Fictional overwrite')
		);
		controller.stream('fictional-other', 'fictional-generation', message(6));
		controller.stream('fictional-thread', 'restored', message(7));
		expect(controller.snapshot.page?.messages.map((m) => m.position)).toEqual([3, 4, 5]);
		expect(controller.snapshot.page?.messages.at(-1)?.content).toEqual(
			message(5, 'Fictional final').content
		);
	});

	it.each(['reconnect', 'unavailable', 'sign_in', 'invalid_request'] as const)(
		'clears visible content on %s',
		async (state) => {
			const backend = api();
			const controller = new ThreadWindow(backend);
			await controller.open('fictional-thread');
			vi.mocked(backend.projection).mockRejectedValueOnce(new ThreadError(state));
			await controller.older();
			expect(controller.snapshot.page).toBeNull();
			expect(controller.snapshot.history.messages).toEqual({});
			expect(controller.snapshot.state).toBe(state);
			expect(Object.hasOwn(en, stateText[state])).toBe(true);
			expect((nl as Record<string, string>)[stateText[state]]).toBeTruthy();
		}
	);

	it('fails closed on unexpected 304, a restored generation, or a repeated cursor', async () => {
		for (const next of [
			{ page: null, etag: 'opaque', notModified: true },
			{ page: { ...page([1]), generation: 'restored' }, etag: null, notModified: false },
			{ page: page([1], 'opaque-older'), etag: null, notModified: false }
		]) {
			const backend = api();
			const controller = new ThreadWindow(backend);
			await controller.open('fictional-thread');
			vi.mocked(backend.projection).mockResolvedValueOnce(next);
			await controller.older();
			expect(controller.snapshot.state).toBe('reconnect');
			expect(controller.snapshot.page).toBeNull();
		}
	});
});

const sidebarItem = (id: string, pinned = false): SidebarItem => ({
	...fork(id),
	parent_thread: null,
	parent_position: null,
	organization: {
		id: `local-${id}`,
		thread_id: id,
		pinned,
		archived: false,
		folder_id: 'fictional-folder',
		last_read_at: 0
	}
});

describe('platform sidebar', () => {
	it('passes readonly state to every chat row in the sidebar and folders', () => {
		const mainRows = [...mainSidebarSource.matchAll(/<ChatItem\s[\s\S]*?\/>/g)].map(([row]) => row);
		expect(mainRows).toHaveLength(3);
		expect(
			mainRows.filter((row) => row.includes('readonly={chat.readonly ?? false}'))
		).toHaveLength(2);
		expect(mainRows.filter((row) => row.includes('readonly={true}'))).toHaveLength(1);
		for (const source of [folderSource, sharedFolderSource]) {
			const rows = [...source.matchAll(/<ChatItem\s[\s\S]*?\/>/g)];
			expect(rows).toHaveLength(1);
			expect(rows[0][0]).toContain('readonly={chat.readonly ??');
		}
	});

	it('hides search menus and Shift actions for readonly rows', () => {
		const actions = searchSource.match(
			/\{#if !chat\.readonly && editingChatId !== chat\.id\}([\s\S]*?)\n\t{8}\{\/if\}/
		)?.[1];
		expect(actions).toBeDefined();
		expect(actions).toContain('{#if shiftKey}');
		expect(actions).toContain('archiveChatHandler(chat.id)');
		expect(actions).toContain('deleteChatHandler(chat.id)');
		expect(actions).toContain('<ChatMenu');
		expect(searchSource.match(/<ChatMenu\s/g)).toHaveLength(1);
	});

	it('routes readonly archived rows to shared threads under v2', () => {
		expect(archivedSource).toMatch(
			/\{@const chatUrl =\s*\$config\?\.features\?\.enable_v2_threads && chat\.readonly\s*\? `\/s\/\$\{chat\.id\}`\s*: `\/c\/\$\{chat\.id\}`\}/
		);
		expect(archivedSource).toContain('href={chatUrl}');
		expect(archivedSource).toContain('goto(chatUrl)');
		expect(archivedSource).not.toContain('goto(`/c/${chat.id}`)');
	});

	it('hides archived owner actions while keeping reader unarchive on its organization', () => {
		const actions = archivedSource.match(/\{#if !chat\.readonly &&[\s\S]*?\{\/if\}/)?.[0];
		expect(actions).toBeDefined();
		expect(actions).toContain("$user?.role === 'admin'");
		expect(actions).toContain('$user?.permissions?.chat?.delete');
		expect(actions).toContain('selectedChatId = chat.id;');
		expect(actions).toContain("aria-label={$i18n.t('Delete Chat')}");
		expect(archivedSource.match(/aria-label=\{\$i18n\.t\('Delete Chat'\)\}/g)).toHaveLength(1);
		expect(actions).not.toContain('unarchiveHandler');
		expect(archivedSource).toContain('unarchiveHandler(chat.id, chat.organization?.id ?? chat.id)');
		expect(archivedSource).toContain('archiveChatById(localStorage.token, organizationId)');
		const exportAction = archivedSource.match(
			/\{#if !\$config\?\.features\?\.enable_v2_threads\}[\s\S]*?\{\/if\}/
		)?.[0];
		expect(exportAction).toContain('on:click={exportChatsHandler}');
	});

	it('preserves platform ownership independently of local records', async () => {
		const backend = api();
		const shared = { ...sidebarItem('fictional-shared', true), owned: false };
		const owned = { ...sidebarItem('fictional-owned'), organization: null };
		vi.mocked(backend.index).mockResolvedValueOnce({ items: [shared, owned], next_cursor: null });
		const controller = new ThreadSidebar(backend);
		await controller.search();
		expect(controller.snapshot.items).toEqual([shared, owned]);
		expect(filterSidebar(controller.snapshot.items, { pinned: true })).toEqual([shared]);
	});

	it('keeps platform order and local filters after search and opaque paging', async () => {
		const backend = api();
		vi.mocked(backend.index)
			.mockResolvedValueOnce({
				items: [sidebarItem('fictional-one', true)],
				next_cursor: 'opaque-index'
			})
			.mockResolvedValueOnce({ items: [sidebarItem('fictional-two')], next_cursor: null });
		const controller = new ThreadSidebar(backend);
		await controller.search('Fictional query');
		await controller.more();
		expect(backend.index).toHaveBeenLastCalledWith('Fictional query', 'opaque-index');
		expect(controller.snapshot.items.map((item) => item.thread_id)).toEqual([
			'fictional-one',
			'fictional-two'
		]);
		expect(
			filterSidebar(controller.snapshot.items, { pinned: true, folderId: 'fictional-folder' })
		).toHaveLength(1);
		expect(filterSidebar(controller.snapshot.items, { archived: true })).toEqual([]);
	});

	it('drops delayed search results and clears titles on a failed page', async () => {
		const backend = api();
		const pending = deferred<Awaited<ReturnType<ThreadAPI['index']>>>();
		vi.mocked(backend.index)
			.mockReturnValueOnce(pending.promise)
			.mockResolvedValueOnce({
				items: [sidebarItem('fictional-new')],
				next_cursor: 'opaque-index'
			});
		const controller = new ThreadSidebar(backend);
		const first = controller.search('Fictional old query');
		await controller.search('Fictional new query');
		pending.resolve({ items: [sidebarItem('fictional-old')], next_cursor: null });
		await first;
		expect(controller.snapshot.items[0].thread_id).toBe('fictional-new');
		vi.mocked(backend.index).mockRejectedValueOnce(new ThreadError('unavailable'));
		await controller.more();
		expect(controller.snapshot.items).toEqual([]);
		expect(controller.snapshot.state).toBe('unavailable');
	});
});

describe('live thread sharing', () => {
	it('sends session credentials and bounded share positions without snapshots', async () => {
		const fetcher = vi.fn(
			async () => new Response(JSON.stringify({ share_id: 'fictional-thread' }))
		);
		vi.stubGlobal('fetch', fetcher);
		try {
			const api = sharingAPI('fictional-session');
			const audience = { kind: 'person' as const, id: 'fictional-person' };
			await api.share('fictional-chat', { audience, at: 7 });
			await api.unshare('fictional-chat', audience);
			await api.clone('fictional-thread');
			await api.read('fictional-thread', 'fictional-older');
			const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
			expect(JSON.parse(String(calls[0][1].body))).toEqual({ audience, at: 7 });
			expect(JSON.parse(String(calls[1][1].body))).toEqual({ audience });
			expect(calls[2][0]).toContain('/fictional-thread/clone/shared');
			expect(calls[3][0]).toContain('before=fictional-older');
			for (const [, init] of calls) {
				expect(init.cache).toBe('no-store');
				expect(init.headers).toEqual({
					Authorization: 'Bearer fictional-session',
					'Content-Type': 'application/json'
				});
			}
		} finally {
			vi.unstubAllGlobals();
		}
	});
	it('renders only fixed translated refusals, including legacy and tokenless states', async () => {
		for (const state of ['share_refused', 'legacy_share', 'sign_in'] as const) {
			vi.stubGlobal(
				'fetch',
				vi.fn(
					async () =>
						new Response(JSON.stringify({ state, detail: 'Fictional private title' }), {
							status: state === 'sign_in' ? 401 : 404
						})
				)
			);
			try {
				await expect(sharingAPI('fictional-session').read('fictional-thread')).rejects.toEqual(
					new ThreadError(state)
				);
				expect(shareStateText(new ThreadError(state))).toBe(stateText[state]);
				expect(Object.hasOwn(en, stateText[state])).toBe(true);
				expect((nl as Record<string, string>)[stateText[state]]).toBeTruthy();
			} finally {
				vi.unstubAllGlobals();
			}
		}
	});
});

it('refreshes sidebar authority on resume without blanking the current rows', async () => {
	const backend = api();
	vi.mocked(backend.index).mockResolvedValueOnce({
		items: [sidebarItem('fictional-old')],
		next_cursor: null
	});
	const controller = new ThreadSidebar(backend);
	await controller.search('Fictional query');
	const pending = deferred<Awaited<ReturnType<ThreadAPI['index']>>>();
	vi.mocked(backend.index).mockReturnValueOnce(pending.promise);
	const refreshing = controller.refresh();
	expect(controller.snapshot.items.map((item) => item.thread_id)).toEqual(['fictional-old']);
	pending.resolve({ items: [sidebarItem('fictional-current')], next_cursor: null });
	await refreshing;
	expect(backend.index).toHaveBeenLastCalledWith('Fictional query', '');
	expect(controller.snapshot.items.map((item) => item.thread_id)).toEqual(['fictional-current']);
	vi.mocked(backend.index).mockRejectedValueOnce(new ThreadError('sign_in'));
	await controller.refresh();
	expect(controller.snapshot.items).toEqual([]);
	expect(controller.snapshot.state).toBe('sign_in');
});

it('wires retry to a fresh cache and loads shared older messages on click', () => {
	expect(windowSource).not.toContain('location.reload()');
	expect(windowSource).toContain('retry = reopen');
	expect(windowSource).toMatch(
		/const reopen = \(\) => \{\s*cache.dispose\(\);[\s\S]*?cache = browserThreadCache/
	);
	expect(sharedSource).toMatch(/onclick=\{\(\) => \{\s*older = true;\s*void refresh\(\);/);
});

it('filters by the public index tags independently of ownership and title search', () => {
	const owned = { ...sidebarItem('fictional-owned'), tags: ['fictional_grove'] };
	const shared = { ...sidebarItem('fictional-shared'), owned: false, tags: ['fictional_grove'] };
	const other = { ...sidebarItem('fictional-other'), tags: ['fictional_other'] };
	expect(filterSidebar([owned, shared, other], { tag: 'fictional_grove' })).toEqual([
		owned,
		shared
	]);
});

describe('conversation version lineage', () => {
	const versions = [
		{ thread_id: 'root', parent_thread: null, parent_position: null },
		{ thread_id: 'v2', parent_thread: 'root', parent_position: 2 },
		{ thread_id: 'v3', parent_thread: 'v2', parent_position: 2 },
		{ thread_id: 'edit', parent_thread: 'v3', parent_position: 4 }
	];
	it.each(['root', 'v2', 'v3'])(
		'shows all regenerations from %s and preserves earlier branch choices',
		(id) => {
			const history = buildThreadHistory(page([1, 2, 3, 4, 5, 6], null, id), versions);
			const input = Object.values(history.messages).find((message) => message.positions[0] === 3)!;
			expect(input.threadVersions).toEqual(
				['root', 'v2', 'v3'].map((threadId) => ({ threadId, at: 2 }))
			);
		}
	);
	it('keeps earlier alternatives reachable from a later edit', () => {
		const history = buildThreadHistory(page([1, 2, 3, 4, 5, 6], null, 'edit'), versions);
		expect(
			Object.values(history.messages).find((message) => message.positions[0] === 3)?.threadVersions
		).toEqual([
			{ threadId: 'root', at: 2 },
			{ threadId: 'v2', at: 2 },
			{ threadId: 'edit', at: 2 }
		]);
	});
	it('does not reveal forks into another conversation or any reader arrows', () => {
		const source = page([1, 2, 3, 4], null, 'root');
		const owned = buildThreadHistory(source, [
			{ thread_id: 'root', parent_thread: 'external', parent_position: 2 }
		]);
		expect(Object.values(owned.messages).every((message) => !message.threadVersions)).toBe(true);
		const reader = buildThreadHistory({ ...source, preferences: null }, []);
		expect(Object.values(reader.messages).every((message) => !message.threadVersions)).toBe(true);
	});
});

it('keeps one sidebar row per conversation across changing current versions and search pages', async () => {
	const backend = api();
	const first = { ...sidebarItem('v1', true), conversation_id: 'root', version_count: 1 };
	const latest = { ...first, thread_id: 'v2', version_count: 2 };
	const reader = { ...sidebarItem('shared'), owned: false };
	vi.mocked(backend.index)
		.mockResolvedValueOnce({ items: [first, reader], next_cursor: 'next' })
		.mockResolvedValueOnce({ items: [latest], next_cursor: null });
	const sidebar = new ThreadSidebar(backend);
	await sidebar.search('question');
	await sidebar.more();
	expect(sidebar.snapshot.items).toEqual([latest, reader]);
	expect(filterSidebar(sidebar.snapshot.items, { pinned: true })).toEqual([latest]);
	expect(backend.index).toHaveBeenCalledTimes(2);
	expect(backend.projection).not.toHaveBeenCalled();
});

it('waits for the branch point to be paged in before showing its arrows', () => {
	const versions = [
		{ thread_id: 'root', parent_thread: null, parent_position: null },
		{ thread_id: 'v2', parent_thread: 'root', parent_position: 2 }
	];
	const history = buildThreadHistory(page([25, 26], 'older', 'v2'), versions);
	expect(Object.values(history.messages).every((message) => !message.threadVersions)).toBe(true);
});
