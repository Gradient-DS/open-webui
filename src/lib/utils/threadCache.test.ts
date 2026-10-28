import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
	ThreadError,
	type CacheAuthorization,
	type ProjectionPage,
	type ThreadAPI
} from '$lib/apis/threads';
import {
	ThreadCache,
	ThreadCacheOrigin,
	IndexedThreadStorage,
	observeThreadAccount,
	threadCacheOrigin,
	clearThreadDrafts,
	watchThreadVisibility,
	type CacheRecord,
	type ThreadCacheStorage
} from './threadCache';
import { ThreadSidebar, ThreadWindow } from './threads';
import { openDB } from 'idb';

vi.mock('idb', () => ({ openDB: vi.fn(), deleteDB: vi.fn() }));

class MemoryStorage implements ThreadCacheStorage {
	partitions = new Map<string, Map<string, CacheRecord>>();
	pendingWrite?: Promise<void>;
	key(thread: string, before: string) {
		return JSON.stringify([thread, before]);
	}
	async read(partition: string, thread: string, before: string) {
		return structuredClone(this.partitions.get(partition)?.get(this.key(thread, before)) ?? null);
	}
	async write(partition: string, record: CacheRecord, valid: () => boolean) {
		await this.pendingWrite;
		if (!valid()) return;
		const records = this.partitions.get(partition) ?? new Map<string, CacheRecord>();
		for (const [key, existing] of records) {
			if (
				existing.page.thread_id === record.page.thread_id &&
				existing.page.generation !== record.page.generation
			)
				records.delete(key);
		}
		records.set(this.key(record.page.thread_id, record.before), structuredClone(record));
		this.partitions.set(partition, records);
	}
	async drop(partition: string, thread: string) {
		const records = this.partitions.get(partition);
		for (const [key, record] of records ?? []) {
			if (record.page.thread_id === thread) records!.delete(key);
		}
	}
	async clear() {
		this.partitions.clear();
	}
	all() {
		return [...this.partitions.values()].flatMap((records) => [...records.values()]);
	}
}
const deferred = <T>() => {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
};
const page = (): ProjectionPage => ({
	thread_id: 'fictional-thread',
	generation: 'fictional-generation',
	title: null,
	tags: [],
	preferences: null,
	next_cursor: null,
	messages: [
		{
			position: 1,
			content: 'Fictional finished answer',
			finished: true,
			version: 'fictional-version'
		}
	]
});
const setup = (storage = new MemoryStorage(), context = 'fictional-door') => {
	const origin = new ThreadCacheOrigin(storage);
	const grant = (body: Parameters<ThreadAPI['authorize']>[0]): CacheAuthorization => ({
		principal: 'platform:user:fictional-fern',
		session: 'fictional-session',
		context_epoch: body.context_epoch,
		authorization_context: context,
		valid_until: new Date(Date.now() + 30_000).toISOString(),
		items: body.threads.map((thread) => ({ ...thread, status: 'ok' }))
	});
	const api: ThreadAPI = {
		authorize: vi.fn(async (body) => grant(body)),
		projection: vi.fn(async () => ({ page: page(), etag: '"fictional-etag"', notModified: false })),
		forks: vi.fn(async () => ({ items: [], next_cursor: null })),
		fork: vi.fn(),
		index: vi.fn()
	};
	const cache = new ThreadCache(api, origin, 'fictional-tenant', 'fictional-epoch');
	return { storage, origin, api, cache, grant };
};
beforeEach(() => {
	vi.useFakeTimers();
	vi.setSystemTime(new Date('2026-10-25T10:00:00Z'));
});
afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

it('revalidates exact versions before an unchanged ETag is rendered', async () => {
	const { api, cache } = setup();
	await cache.read('fictional-thread');
	vi.mocked(api.projection).mockResolvedValueOnce({
		page: null,
		etag: '"fictional-etag"',
		notModified: true
	});
	const reopened = await cache.read('fictional-thread');
	expect(reopened.page?.messages).toEqual(page().messages);
	expect(reopened.valid()).toBe(true);
	expect(api.projection).toHaveBeenLastCalledWith('fictional-thread', '', '"fictional-etag"');
	expect(api.authorize).toHaveBeenLastCalledWith({
		context_epoch: 'fictional-epoch',
		threads: [
			{
				thread_id: 'fictional-thread',
				generation: 'fictional-generation',
				versions: ['fictional-version']
			}
		]
	});
});

it('drops revoked entries without logout, even with an unchanged validator', async () => {
	const { api, cache, storage, grant } = setup();
	await cache.read('fictional-thread');
	vi.mocked(api.authorize).mockImplementation(async (body) => ({
		...grant(body),
		items: body.threads.map((thread) => ({ ...thread, status: 'gone' }))
	}));
	const window = new ThreadWindow(api, undefined, cache);
	await window.open('fictional-thread');
	expect(window.snapshot.history.messages).toEqual({});
	expect(window.snapshot.state).toBe('unavailable');
	expect(storage.all()).toEqual([]);
	expect(api.projection).toHaveBeenCalledTimes(1);
});

it.each(['authorize', 'projection'] as const)(
	'renders nothing on offline reopen during %s',
	async (operation) => {
		const { api, cache } = setup();
		await cache.read('fictional-thread');
		vi.mocked(api[operation]).mockRejectedValueOnce(
			new TypeError('Fictional disconnected network')
		);
		const window = new ThreadWindow(api, undefined, cache);
		await window.open('fictional-thread');
		expect(window.snapshot.page).toBeNull();
		expect(window.snapshot.history.messages).toEqual({});
		expect(window.snapshot.state).toBe('reconnect');
	}
);

it('partitions by tenant, session and door/scope; a narrower door sends no cached ETag', async () => {
	const { storage, cache } = setup();
	await cache.read('fictional-thread');
	const narrow = setup(storage, 'fictional-narrow-scope');
	await narrow.cache.read('fictional-thread');
	expect(narrow.api.projection).toHaveBeenCalledWith('fictional-thread', '', undefined);
	const otherTenant = new ThreadCache(
		narrow.api,
		narrow.origin,
		'fictional-other-tenant',
		'fictional-epoch'
	);
	await otherTenant.read('fictional-thread');
	expect(narrow.api.projection).toHaveBeenLastCalledWith('fictional-thread', '', undefined);
	const otherSession = setup(storage);
	vi.mocked(otherSession.api.authorize).mockImplementation(async (body) => ({
		...otherSession.grant(body),
		session: 'fictional-other-session'
	}));
	await otherSession.cache.read('fictional-thread');
	expect(otherSession.api.projection).toHaveBeenLastCalledWith('fictional-thread', '', undefined);
	expect(storage.partitions.size).toBe(4);
});

it.each([
	'principal',
	'session',
	'context_epoch',
	'authorization_context',
	'versions',
	'generation',
	'thread_id',
	'valid_until'
] as const)('refuses a mismatched %s binding before a write or render', async (field) => {
	const { api, cache, storage, grant } = setup();
	// Bind the session/door first, then corrupt one part of the content authorization.
	vi.mocked(api.authorize).mockImplementation(async (body) => {
		const result = grant(body);
		if (!body.threads.length) return result;
		if (['versions', 'generation', 'thread_id'].includes(field)) {
			Object.assign(result.items[0], {
				[field]: field === 'versions' ? ['fictional-other-version'] : 'fictional-other'
			});
		} else
			Object.assign(result, {
				[field]:
					field === 'valid_until' ? new Date(Date.now() - 1).toISOString() : 'fictional-other'
			});
		return result;
	});
	await expect(cache.read('fictional-thread')).rejects.toBeInstanceOf(ThreadError);
	expect(storage.all()).toEqual([]);
});

it('drops a delayed authorization after a context switch', async () => {
	const { api, cache, storage, grant } = setup();
	const delayed = deferred<CacheAuthorization>();
	vi.mocked(api.authorize).mockReturnValueOnce(delayed.promise);
	const reading = cache.read('fictional-thread');
	await vi.waitFor(() => expect(api.authorize).toHaveBeenCalled());
	cache.dispose();
	delayed.resolve(grant({ context_epoch: 'fictional-epoch', threads: [] }));
	await expect(reading).rejects.toEqual(new ThreadError('reconnect'));
	expect(storage.all()).toEqual([]);
	expect(api.projection).not.toHaveBeenCalled();
});

it('reopens an expired page after a suspended tab wakes up', async () => {
	const { api, cache, origin } = setup();
	const window = new ThreadWindow(api, undefined, cache);
	await window.open('fictional-thread');
	const documentEvents = Object.assign(new EventTarget(), { visibilityState: 'visible' });
	const windowEvents = new EventTarget();
	vi.stubGlobal('document', documentEvents);
	vi.stubGlobal('window', windowEvents);
	const reopen = vi.fn(() => void window.open('fictional-thread'));
	const unwatch = watchThreadVisibility(
		() => window.clear(),
		() => window.resume(reopen),
		origin
	);
	documentEvents.visibilityState = 'hidden';
	documentEvents.dispatchEvent(new Event('visibilitychange'));
	expect(window.snapshot.page).not.toBeNull();
	// A suspended browser does not run the renewal or expiry timers.
	vi.setSystemTime(Date.now() + 60_000);
	vi.mocked(api.authorize).mockRejectedValueOnce(new TypeError('Fictional offline tab'));
	documentEvents.visibilityState = 'visible';
	documentEvents.dispatchEvent(new Event('visibilitychange'));
	expect(window.snapshot.page).toBeNull();
	await vi.waitFor(() => expect(window.snapshot.state).toBe('reconnect'));
	expect(reopen).toHaveBeenCalledOnce();
	unwatch();
	window.clear();
});

it('refuses an expired read proof after a slow fork listing before rendering', async () => {
	const { api, cache, grant } = setup();
	vi.mocked(api.forks).mockImplementationOnce(async () => {
		vi.setSystemTime(Date.now() + 60_000);
		vi.mocked(api.authorize).mockImplementation(async (body) => ({
			...grant(body),
			items: body.threads.map((thread) => ({ ...thread, status: 'gone' }))
		}));
		return { items: [], next_cursor: null };
	});
	const window = new ThreadWindow(api, undefined, cache);
	await window.open('fictional-thread');
	expect(window.snapshot.page).toBeNull();
	expect(window.snapshot.state).toBe('reconnect');
});

it('invalidates every old-generation window after a restore', async () => {
	const { api, cache, storage } = setup();
	await cache.read('fictional-thread', 'fictional-older');
	await cache.read('fictional-thread');
	vi.mocked(api.projection).mockResolvedValueOnce({
		page: {
			...page(),
			generation: 'fictional-restored',
			messages: [{ ...page().messages[0], version: 'fictional-restored-version' }]
		},
		etag: '"fictional-restored"',
		notModified: false
	});
	await cache.read('fictional-thread');
	expect(storage.all()).toHaveLength(1);
	expect(storage.all()[0].page.generation).toBe('fictional-restored');
});

it('stores only finished messages and never reuses the validator of a streaming window', async () => {
	const { api, cache, storage } = setup();
	vi.mocked(api.projection).mockResolvedValueOnce({
		page: {
			...page(),
			messages: [
				...page().messages,
				{ position: 2, content: 'Fictional streaming text', version: null, finished: false }
			]
		},
		etag: '"fictional-stream"',
		notModified: false
	});
	await cache.read('fictional-thread');
	expect(storage.all()[0].page.messages).toEqual(page().messages);
	expect(storage.all()[0].etag).toBeNull();
	await cache.read('fictional-thread');
	expect(api.projection).toHaveBeenLastCalledWith('fictional-thread', '', undefined);
});

it('logout in another tab clears every partition, hides content, reloads, and defeats a pending write', async () => {
	const { api, cache, storage } = setup();
	await cache.read('fictional-thread');
	const other = setup(storage, 'fictional-other-door');
	await other.cache.read('fictional-thread');
	const channelA = { onmessage: null as BroadcastChannel['onmessage'], postMessage: vi.fn() };
	const channelB = { onmessage: null as BroadcastChannel['onmessage'], postMessage: vi.fn() };
	const reload = vi.fn();
	const remote = new ThreadCacheOrigin(storage, channelB, reload);
	channelA.postMessage.mockImplementation(() =>
		channelB.onmessage?.call({} as BroadcastChannel, new MessageEvent('message'))
	);
	const local = new ThreadCacheOrigin(storage, channelA);
	const pendingCache = new ThreadCache(api, remote, 'fictional-tenant', 'fictional-epoch');
	const window = new ThreadWindow(api, undefined, pendingCache);
	const unsubscribe = remote.subscribe(() => window.clear());
	await window.open('fictional-thread');
	const pending = deferred<void>();
	storage.pendingWrite = pending.promise;
	const writing = vi.spyOn(storage, 'write');
	const write = pendingCache.save(page());
	await vi.waitFor(() => expect(writing).toHaveBeenCalledOnce());
	await local.clear();
	expect(window.snapshot.page).toBeNull();
	pending.resolve();
	await expect(write).rejects.toEqual(new ThreadError('reconnect'));
	await remote.ready();
	expect(storage.all()).toEqual([]);
	expect(reload).toHaveBeenCalled();
	expect(channelA.postMessage).toHaveBeenCalledOnce();
	unsubscribe();
});

it('logout with a subscribed sidebar deletes storage and broadcasts once', async () => {
	const { api, cache, storage } = setup();
	await cache.read('fictional-thread');
	const deleted = vi.spyOn(storage, 'clear');
	const channel = { onmessage: null as BroadcastChannel['onmessage'], postMessage: vi.fn() };
	const drafts = vi.fn();
	const origin = new ThreadCacheOrigin(storage, channel, undefined, undefined, undefined, drafts);
	const authenticationFailed = vi.fn(() => void origin.clear());
	const sidebar = new ThreadSidebar(api, undefined, authenticationFailed);
	const unsubscribe = origin.subscribe(() => sidebar.clear('sign_in'));
	await origin.clear();
	expect(sidebar.snapshot).toMatchObject({ state: 'sign_in', items: [] });
	expect(authenticationFailed).not.toHaveBeenCalled();
	expect(storage.all()).toEqual([]);
	expect(deleted).toHaveBeenCalledOnce();
	expect(channel.postMessage).toHaveBeenCalledOnce();
	expect(drafts).toHaveBeenCalledOnce();
	unsubscribe();
});

it('coalesces recursive and concurrent clears, and permits clearing again after completion', async () => {
	const { storage } = setup();
	const pending = deferred<void>();
	const deleted = vi.spyOn(storage, 'clear').mockReturnValueOnce(pending.promise);
	const channel = { onmessage: null as BroadcastChannel['onmessage'], postMessage: vi.fn() };
	const origin = new ThreadCacheOrigin(storage, channel);
	let nested: Promise<void> | undefined;
	const unsubscribe = origin.subscribe(() => {
		nested = origin.clear();
	});
	const clearing = origin.clear();
	expect(nested).toBe(clearing);
	expect(origin.clear()).toBe(clearing);
	expect(origin.ready()).toBe(clearing);
	await Promise.resolve();
	expect(deleted).toHaveBeenCalledOnce();
	expect(channel.postMessage).toHaveBeenCalledOnce();
	pending.resolve();
	await clearing;
	unsubscribe();
	await origin.clear();
	expect(deleted).toHaveBeenCalledTimes(2);
	expect(channel.postMessage).toHaveBeenCalledTimes(2);
});

it('deletes cached messages and drafts despite a throwing subscriber, then surfaces the failure', async () => {
	const { cache, storage } = setup();
	await cache.read('fictional-thread');
	const deleted = vi.spyOn(storage, 'clear');
	const drafts = vi.fn();
	const origin = new ThreadCacheOrigin(storage, undefined, undefined, undefined, undefined, drafts);
	const failure = new Error('Fictional subscriber failure');
	origin.subscribe(() => {
		throw failure;
	});
	const notified = vi.fn();
	origin.subscribe(notified);
	await expect(origin.clear()).rejects.toMatchObject({ errors: [failure] });
	expect(storage.all()).toEqual([]);
	expect(deleted).toHaveBeenCalledOnce();
	expect(drafts).toHaveBeenCalledOnce();
	expect(notified).toHaveBeenCalledOnce();
});

it('clears the origin only when the sidebar API refuses authentication', async () => {
	const { api, cache, storage } = setup();
	await cache.read('fictional-thread');
	const origin = new ThreadCacheOrigin(storage);
	const authenticationFailed = vi.fn(() => void origin.clear());
	const sidebar = new ThreadSidebar(api, undefined, authenticationFailed);
	origin.subscribe(() => sidebar.clear('sign_in'));
	vi.mocked(api.index).mockRejectedValueOnce(new ThreadError('sign_in'));
	await sidebar.search();
	await origin.ready();
	expect(authenticationFailed).toHaveBeenCalledOnce();
	expect(sidebar.snapshot).toMatchObject({ state: 'sign_in', items: [] });
	expect(storage.all()).toEqual([]);
});

it('a suspended tab detects an origin clear even before its broadcast is delivered', async () => {
	let revision = 'fictional-before-logout';
	const { storage, api } = setup();
	const origin = new ThreadCacheOrigin(storage, undefined, undefined, () => revision);
	const cache = new ThreadCache(api, origin, 'fictional-tenant', 'fictional-epoch');
	const proof = await cache.read('fictional-thread');
	revision = 'fictional-after-logout';
	expect(proof.valid()).toBe(false);
});

it('clears legacy drafts from both browser storage areas', () => {
	const storage = () => ({
		'chat-input': 'Fictional draft',
		'chat-input-fictional-thread': 'Fictional draft',
		theme: 'dark',
		removeItem(key: string) {
			delete this[key as keyof typeof this];
		}
	});
	const local = storage();
	const session = storage();
	vi.stubGlobal('localStorage', local);
	vi.stubGlobal('sessionStorage', session);
	clearThreadDrafts();
	for (const area of [local, session]) {
		expect(area).not.toHaveProperty('chat-input');
		expect(area).not.toHaveProperty('chat-input-fictional-thread');
		expect(area.theme).toBe('dark');
	}
});

it('a login expiry clears all origin partitions and permanently invalidates old cache instances', async () => {
	const { api, cache, storage, origin } = setup();
	await cache.read('fictional-thread');
	await setup(storage, 'fictional-other-door').cache.read('fictional-thread');
	vi.mocked(api.authorize).mockRejectedValueOnce(new ThreadError('sign_in'));
	await expect(cache.read('fictional-thread')).rejects.toEqual(new ThreadError('sign_in'));
	await origin.ready();
	expect(storage.all()).toEqual([]);
	await expect(cache.read('fictional-thread')).rejects.toEqual(new ThreadError('reconnect'));
});

it('refuses a write whose authorization expires while storage is pending', async () => {
	const { cache, storage } = setup();
	const pending = deferred<void>();
	storage.pendingWrite = pending.promise;
	const writing = vi.spyOn(storage, 'write');
	const write = cache.save(page());
	await vi.waitFor(() => expect(writing).toHaveBeenCalledOnce());
	vi.setSystemTime(Date.now() + 30_001);
	pending.resolve();
	await expect(write).rejects.toEqual(new ThreadError('reconnect'));
	expect(storage.all()).toEqual([]);
});

it('chunks authorization of long scroll windows without omitting any versions', async () => {
	const { cache, api } = setup();
	const long = {
		...page(),
		messages: Array.from({ length: 401 }, (_, index) => ({
			...page().messages[0],
			position: index + 1,
			version: `fictional-version-${index}`
		}))
	};
	await cache.approve(long);
	const requested = vi
		.mocked(api.authorize)
		.mock.calls.flatMap(([body]) => body.threads.flatMap((thread) => thread.versions));
	expect(requested).toEqual(long.messages.map((message) => message.version));
	expect(
		vi
			.mocked(api.authorize)
			.mock.calls.every(([body]) => body.threads.every((thread) => thread.versions.length <= 200))
	).toBe(true);
});

it('ignores a late login refusal from a disposed context without clearing the new session', async () => {
	const { api, cache, origin, storage } = setup();
	await cache.read('fictional-thread');
	let reject!: (reason: unknown) => void;
	vi.mocked(api.authorize)
		.mockClear()
		.mockReturnValueOnce(
			new Promise((_, no) => {
				reject = no;
			})
		);
	const reading = cache.read('fictional-thread');
	await vi.waitFor(() => expect(api.authorize).toHaveBeenCalledOnce());
	cache.dispose();
	const clear = vi.spyOn(origin, 'clear');
	reject(new ThreadError('sign_in'));
	await expect(reading).rejects.toEqual(new ThreadError('sign_in'));
	expect(clear).not.toHaveBeenCalled();
	expect(storage.all()).toHaveLength(1);
});

it('keeps only the fallback marker through storage and a 304 reopen', async () => {
	const { api, cache, storage } = setup();
	vi.mocked(api.projection).mockResolvedValueOnce({
		page: {
			...page(),
			title: 'Fictional private title',
			preferences: { fallback: true, secret: 'Fictional' }
		},
		etag: '"fictional-etag"',
		notModified: false
	});
	await cache.read('fictional-thread');
	expect(storage.all()[0].page.preferences).toEqual({ fallback: true });
	expect(storage.all()[0].page.title).toBeNull();
	vi.mocked(api.projection).mockResolvedValueOnce({ page: null, etag: null, notModified: true });
	expect((await cache.read('fictional-thread')).page?.preferences).toEqual({ fallback: true });
});

it('round trips the non-content fallback flag in IndexedDB window metadata', async () => {
	const data = new Map<string, Map<string, unknown>>();
	const objectStore = (name: string) => {
		const items = data.get(name) ?? new Map<string, unknown>();
		data.set(name, items);
		return {
			get: async (key: unknown) => items.get(JSON.stringify(key)),
			getAll: async () => [...items.values()],
			put: (value: unknown, key: unknown) => items.set(JSON.stringify(key), structuredClone(value))
		};
	};
	vi.mocked(openDB).mockResolvedValue({
		transaction: () => ({ objectStore, done: Promise.resolve() })
	} as unknown as Awaited<ReturnType<typeof openDB>>);
	const local = new Map<string, string>();
	vi.stubGlobal('localStorage', {
		getItem: (key: string) => local.get(key) ?? null,
		setItem: (key: string, value: string) => local.set(key, value)
	});
	const storage = new IndexedThreadStorage();
	await storage.write(
		'fictional-partition',
		{
			page: { ...page(), preferences: { fallback: true } },
			before: '',
			etag: 'fictional-etag'
		},
		() => true
	);
	const record = await storage.read('fictional-partition', 'fictional-thread', '');
	expect(record?.page.preferences).toEqual({ fallback: true });
	expect(record?.page.messages).toEqual(page().messages);
});

it('uses the read proof without a second window approval', async () => {
	const { api, cache } = setup();
	const window = new ThreadWindow(api, undefined, cache);
	await window.open('fictional-thread');
	expect(api.authorize).toHaveBeenCalledTimes(2); // Session binding and the displayed page.
	expect(window.snapshot.page).not.toBeNull();
	window.clear();
});

it('renews visible authorization before the deadline without blanking or reloading', async () => {
	const { api, cache } = setup();
	const changed = vi.fn();
	const window = new ThreadWindow(api, changed, cache);
	await window.open('fictional-thread');
	const visible = window.snapshot.page;
	changed.mockClear();
	await vi.advanceTimersByTimeAsync(60_000);
	expect(window.snapshot.page).toBe(visible);
	expect(api.authorize).toHaveBeenCalledTimes(4);
	expect(api.projection).toHaveBeenCalledOnce();
	expect(changed).not.toHaveBeenCalled();
	window.clear();
});

it.each(['reconnect', 'unavailable'] as const)(
	'fails closed when proof renewal returns %s',
	async (state) => {
		const { api, cache, grant, storage } = setup();
		const window = new ThreadWindow(api, undefined, cache);
		await window.open('fictional-thread');
		vi.mocked(api.authorize).mockImplementation(async (body) => {
			if (state === 'reconnect') throw new TypeError('Fictional offline network');
			return {
				...grant(body),
				items: body.threads.map((thread) => ({ ...thread, status: 'gone' }))
			};
		});
		await vi.advanceTimersByTimeAsync(29_000);
		expect(window.snapshot.page).toBeNull();
		expect(window.snapshot.state).toBe(state);
		if (state === 'unavailable') expect(storage.all()).toEqual([]);
	}
);

it('clears at the hard deadline when renewal is still pending and ignores its late reply', async () => {
	const { api, cache, grant } = setup();
	const window = new ThreadWindow(api, undefined, cache);
	await window.open('fictional-thread');
	const delayed = deferred<CacheAuthorization>();
	vi.mocked(api.authorize).mockReturnValueOnce(delayed.promise);
	await vi.advanceTimersByTimeAsync(30_001);
	expect(window.snapshot.page).toBeNull();
	expect(window.snapshot.state).toBe('reconnect');
	const body = vi.mocked(api.authorize).mock.calls.at(-1)![0];
	delayed.resolve(grant(body));
	await vi.advanceTimersByTimeAsync(0);
	expect(window.snapshot.page).toBeNull();
});

it('reapproves a valid resumed page without clearing and ignores plain focus events', async () => {
	const { api, cache, origin } = setup();
	const window = new ThreadWindow(api, undefined, cache);
	await window.open('fictional-thread');
	const visible = window.snapshot.page;
	const doc = Object.assign(new EventTarget(), { visibilityState: 'visible' });
	const win = new EventTarget();
	vi.stubGlobal('document', doc);
	vi.stubGlobal('window', win);
	const clear = vi.spyOn(window, 'clear');
	const reopen = vi.fn();
	const unwatch = watchThreadVisibility(
		(state) => window.clear(state),
		() => window.resume(reopen),
		origin
	);
	vi.mocked(api.authorize).mockClear();
	win.dispatchEvent(new Event('focus'));
	expect(api.authorize).not.toHaveBeenCalled();
	win.dispatchEvent(new Event('pageshow'));
	await vi.waitFor(() => expect(api.authorize).toHaveBeenCalledOnce());
	expect(window.snapshot.page).toBe(visible);
	expect(clear).not.toHaveBeenCalled();
	expect(reopen).not.toHaveBeenCalled();
	unwatch();
	window.clear();
});

it('reopens on resume when the origin revision changed before a broadcast arrives', async () => {
	const { api, storage } = setup();
	let revision = 'fictional-before';
	const origin = new ThreadCacheOrigin(storage, undefined, undefined, () => revision);
	const cache = new ThreadCache(api, origin, 'fictional-tenant', 'fictional-epoch');
	const window = new ThreadWindow(api, undefined, cache);
	await window.open('fictional-thread');
	revision = 'fictional-after';
	const reopen = vi.fn();
	window.resume(reopen);
	expect(window.snapshot.page).toBeNull();
	expect(reopen).toHaveBeenCalledOnce();
});

it.each(['reconnect', 'unavailable'] as const)(
	'renders completed stream text immediately when background caching returns %s',
	async (state) => {
		const { api, cache } = setup();
		const window = new ThreadWindow(api, undefined, cache);
		await window.open('fictional-thread');
		const pending = deferred<CacheAuthorization>();
		vi.mocked(api.authorize).mockReturnValueOnce(pending.promise);
		const final = {
			position: 2,
			content: 'Fictional final answer',
			finished: true,
			version: 'fictional-final'
		};
		window.stream('fictional-thread', 'fictional-generation', final);
		expect(window.snapshot.page?.messages.at(-1)).toEqual(final);
		await vi.waitFor(() => expect(api.authorize).toHaveBeenCalledTimes(3));
		// Resolve the background request as a transient error or an explicit revocation.
		if (state === 'reconnect') pending.reject(new TypeError('Fictional network interruption'));
		else
			pending.resolve({
				principal: 'platform:user:fictional-fern',
				session: 'fictional-session',
				context_epoch: 'fictional-epoch',
				authorization_context: 'fictional-door',
				valid_until: new Date(Date.now() + 30_000).toISOString(),
				items: [
					{
						thread_id: 'fictional-thread',
						generation: 'fictional-generation',
						versions: ['fictional-version', 'fictional-final'],
						status: state === 'unavailable' ? 'gone' : 'ok'
					}
				]
			});
		await vi.advanceTimersByTimeAsync(0);
		if (state === 'unavailable') expect(window.snapshot.page).toBeNull();
		else expect(window.snapshot.page?.messages.at(-1)).toEqual(final);
		window.clear();
	}
);

it('clears and broadcasts an observed account switch, but keeps the same account', async () => {
	const values = new Map<string, string>();
	vi.stubGlobal('localStorage', {
		getItem: (key: string) => values.get(key) ?? null,
		setItem: (key: string, value: string) => values.set(key, value),
		removeItem: (key: string) => values.delete(key)
	});
	vi.stubGlobal('sessionStorage', {});
	vi.stubGlobal('window', new EventTarget());
	const reload = vi.fn();
	vi.stubGlobal('location', { reload });
	const postMessage = vi.fn();
	vi.stubGlobal(
		'BroadcastChannel',
		class {
			onmessage = null;
			postMessage = postMessage;
		}
	);
	const origin = threadCacheOrigin();
	const clearStorage = vi.spyOn(origin.storage, 'clear').mockResolvedValue();
	observeThreadAccount('fictional-fern');
	const valid = origin.stamp();
	observeThreadAccount('fictional-fern');
	expect(valid()).toBe(true);
	expect(postMessage).not.toHaveBeenCalled();
	observeThreadAccount('fictional-moss');
	expect(valid()).toBe(false);
	await origin.ready();
	expect(clearStorage).toHaveBeenCalledOnce();
	expect(postMessage).toHaveBeenCalledWith('clear');
	expect(reload).toHaveBeenCalledOnce();
});

it('broadcasts and invalidates visible proofs even if origin storage clearing fails', async () => {
	const { api, storage } = setup();
	const channel = { onmessage: null as BroadcastChannel['onmessage'], postMessage: vi.fn() };
	const origin = new ThreadCacheOrigin(storage, channel);
	const cache = new ThreadCache(api, origin, 'fictional-tenant', 'fictional-epoch');
	const proof = await cache.read('fictional-thread');
	const failure = new Error('Fictional storage clear failure');
	vi.spyOn(storage, 'clear').mockRejectedValueOnce(failure);
	await expect(origin.clear()).rejects.toBe(failure);
	expect(channel.postMessage).toHaveBeenCalledWith('clear');
	expect(proof.valid()).toBe(false);
});
