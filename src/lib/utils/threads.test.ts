import { describe, expect, it, vi } from 'vitest';
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

it('provides English keys and Dutch translations for every thread UI label', () => {
	const keys = [
		...(windowSource + sidebarSource + stateSource).matchAll(/\$i18n\.t\('([^']+)'/g)
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
	preferences: {},
	messages: positions.map((p) => message(p)),
	next_cursor: cursor
});
const fork = (id = 'fictional-fork', at = 2): IndexItem => ({
	thread_id: id,
	title: 'Fictional fork',
	updated_at: '2026-10-25T10:00:00Z',
	parent_thread: 'fictional-thread',
	parent_position: at
});
const api = (): ThreadAPI => ({
	projection: vi.fn(async () => ({
		page: page([3, 4], 'opaque-older'),
		etag: 'opaque-etag',
		notModified: false
	})),
	forks: vi.fn(async () => ({ items: [fork()], next_cursor: null })),
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
