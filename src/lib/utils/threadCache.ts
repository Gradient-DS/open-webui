import { openDB, deleteDB, type IDBPDatabase } from 'idb';
import {
	ThreadError,
	type CacheAuthorization,
	type CachedThread,
	type ProjectionPage,
	type ProjectionResult,
	type ThreadAPI,
	type ThreadState
} from '$lib/apis/threads';

const prefix = 'soev-thread-cache:';
const registryKey = `${prefix}databases`;
const revisionKey = `${prefix}revision`;
const accountKey = `${prefix}account`;
const sessionKey = `${prefix}session`;

type StoredWindow = {
	thread: string;
	before: string;
	generation: string;
	versions: string[];
	cursor: string | null;
	etag: string | null;
	fallback: boolean;
};
export type CacheRecord = { page: ProjectionPage; before: string; etag: string | null };
export interface ThreadCacheStorage {
	read(partition: string, thread: string, before: string): Promise<CacheRecord | null>;
	write(partition: string, record: CacheRecord, valid: () => boolean): Promise<void>;
	drop(partition: string, thread: string): Promise<void>;
	clear(): Promise<void>;
}

const fallback = (page: ProjectionPage) =>
	(page.preferences as { fallback?: boolean } | null)?.fallback === true;

// Metadata contains window references and the owner-only marker, never titles or unfinished content.
export class IndexedThreadStorage implements ThreadCacheStorage {
	private databases = new Map<string, Promise<IDBPDatabase>>();
	private database(partition: string) {
		const name = prefix + encodeURIComponent(partition);
		let pending = this.databases.get(name);
		if (!pending) {
			const names: string[] = JSON.parse(localStorage.getItem(registryKey) ?? '[]');
			localStorage.setItem(registryKey, JSON.stringify([...new Set([...names, name])]));
			pending = openDB(name, 1, {
				upgrade(db) {
					db.createObjectStore('messages');
					db.createObjectStore('windows');
				},
				blocking: () => void pending?.then((db) => db.close())
			});
			this.databases.set(name, pending);
		}
		return pending;
	}
	async read(partition: string, thread: string, before: string): Promise<CacheRecord | null> {
		const db = await this.database(partition);
		const tx = db.transaction(['messages', 'windows']);
		const window: StoredWindow | undefined = await tx.objectStore('windows').get([thread, before]);
		if (!window) return null;
		const messages = await Promise.all(
			window.versions.map((version) => tx.objectStore('messages').get([thread, version]))
		);
		await tx.done;
		if (messages.some((message) => !message?.finished || !message.version)) return null;
		return {
			before,
			etag: window.etag,
			page: {
				thread_id: thread,
				generation: window.generation,
				title: null,
				preferences: { fallback: window.fallback === true },
				messages,
				next_cursor: window.cursor
			}
		};
	}
	async write(partition: string, record: CacheRecord, valid: () => boolean) {
		const db = await this.database(partition);
		if (!valid()) return;
		const tx = db.transaction(['messages', 'windows'], 'readwrite');
		const windows = tx.objectStore('windows');
		const messages = tx.objectStore('messages');
		const previous: StoredWindow[] = await windows.getAll();
		if (!valid()) {
			tx.abort();
			await tx.done.catch(() => {});
			return;
		}
		const { page, before, etag } = record;
		if (
			previous.some((item) => item.thread === page.thread_id && item.generation !== page.generation)
		) {
			for (const key of await messages.getAllKeys()) {
				if (Array.isArray(key) && key[0] === page.thread_id) await messages.delete(key);
			}
			for (const item of previous) {
				if (item.thread === page.thread_id) await windows.delete([item.thread, item.before]);
			}
		}
		if (!valid()) {
			tx.abort();
			await tx.done.catch(() => {});
			return;
		}
		const finished = page.messages.filter((message) => message.finished && message.version);
		for (const message of finished) messages.put(message, [page.thread_id, message.version!]);
		// A partial window must be fetched again, even when its HTTP ETag is unchanged.
		windows.put(
			{
				thread: page.thread_id,
				before,
				generation: page.generation,
				fallback: fallback(page),
				versions: finished.map((message) => message.version!),
				cursor: page.next_cursor,
				etag: finished.length === page.messages.length ? etag : null
			} satisfies StoredWindow,
			[page.thread_id, before]
		);
		await tx.done;
	}
	async drop(partition: string, thread: string) {
		const db = await this.database(partition);
		const tx = db.transaction(['messages', 'windows'], 'readwrite');
		for (const name of ['messages', 'windows']) {
			const store = tx.objectStore(name);
			for (const key of await store.getAllKeys()) {
				if (Array.isArray(key) && key[0] === thread) await store.delete(key);
			}
		}
		await tx.done;
	}
	async clear() {
		const registered: string[] = JSON.parse(localStorage.getItem(registryKey) ?? '[]');
		const discovered = indexedDB.databases ? await indexedDB.databases() : [];
		const names = new Set([
			...registered,
			...this.databases.keys(),
			...discovered.map((db) => db.name ?? '').filter((name) => name.startsWith(prefix))
		]);
		const pending = [...this.databases.values()];
		this.databases.clear();
		await Promise.all(pending.map(async (db) => (await db).close()));
		await Promise.all([...names].map((name) => deleteDB(name)));
		localStorage.removeItem(registryKey);
	}
}

type Channel = Pick<BroadcastChannel, 'postMessage' | 'onmessage'>;
export class ThreadCacheOrigin {
	private epoch = 0;
	private listeners = new Set<() => void>();
	private clearing: Promise<void> = Promise.resolve();
	constructor(
		readonly storage: ThreadCacheStorage,
		private channel?: Channel,
		private reload: () => void = () => {},
		private revision: () => string | null = () => null,
		private bumpRevision: () => void = () => {},
		private clearDrafts: () => void = () => {},
		private identityChanged: (principal: string, session: string) => boolean = () => false
	) {
		if (channel) channel.onmessage = () => void this.clear(false, true);
	}
	stamp() {
		const epoch = this.epoch;
		const revision = this.revision();
		return () => epoch === this.epoch && revision === this.revision();
	}
	subscribe(listener: () => void) {
		this.listeners.add(listener);
		return () => this.listeners.delete(listener);
	}
	ready() {
		return this.clearing;
	}
	bind(principal: string, session: string) {
		if (this.identityChanged(principal, session)) {
			void this.clear(true, true);
			throw new ThreadError('reconnect');
		}
	}
	clear(broadcast = true, reload = false) {
		this.epoch++;
		if (broadcast) {
			this.bumpRevision();
			this.channel?.postMessage('clear');
		}
		for (const listener of this.listeners) listener();
		this.clearDrafts();
		this.clearing = this.clearing.catch(() => {}).then(() => this.storage.clear());
		if (reload) void this.clearing.then(this.reload).catch(() => {});
		return this.clearing;
	}
}

let browserOrigin: ThreadCacheOrigin | undefined;
export const threadCacheOrigin = () => {
	if (!browserOrigin) {
		browserOrigin = new ThreadCacheOrigin(
			new IndexedThreadStorage(),
			new BroadcastChannel('soev-thread-cache'),
			() => location.reload(),
			() => localStorage.getItem(revisionKey),
			() => localStorage.setItem(revisionKey, crypto.randomUUID()),
			() => {
				clearThreadDrafts();
				localStorage.removeItem(sessionKey);
			},
			(principal, session) => {
				const identity = JSON.stringify([principal, session]);
				const previous = localStorage.getItem(sessionKey);
				if (previous && previous !== identity) return true;
				localStorage.setItem(sessionKey, identity);
				return false;
			}
		);
		window.addEventListener('storage', (event) => {
			if (event.key === revisionKey) void browserOrigin!.clear(false, true);
		});
	}
	return browserOrigin;
};
export function clearThreadDrafts() {
	for (const storage of [localStorage, sessionStorage]) {
		for (const key of Object.keys(storage)) {
			if (key.startsWith('chat-input') || key.startsWith('embedded-chat-input'))
				storage.removeItem(key);
		}
	}
}
export const observeThreadAccount = (account: string | null) => {
	const origin = threadCacheOrigin();
	const previous = localStorage.getItem(accountKey);
	if (previous && previous !== account) void origin.clear(true, account !== null);
	if (account) localStorage.setItem(accountKey, account);
	else localStorage.removeItem(accountKey);
};

export type CacheProof = { valid: () => boolean; deadline: number };
export class ThreadCache {
	private active = true;
	private lifetime: () => boolean;
	private binding?: CacheAuthorization;
	constructor(
		private api: ThreadAPI,
		private origin: ThreadCacheOrigin,
		private tenant: string,
		private contextEpoch: string,
		private now = Date.now
	) {
		this.lifetime = origin.stamp();
	}
	dispose() {
		this.active = false;
	}
	private partition() {
		const binding = this.binding!;
		return JSON.stringify([
			this.tenant,
			binding.principal,
			binding.session,
			binding.authorization_context
		]);
	}
	private async request<T>(send: () => Promise<T>): Promise<T> {
		try {
			return await send();
		} catch (error) {
			if (
				this.active &&
				this.lifetime() &&
				error instanceof ThreadError &&
				error.state === 'sign_in'
			) {
				void this.origin.clear();
			}
			throw error;
		}
	}
	private async authorize(threads: CachedThread[]): Promise<CacheProof> {
		const current = this.origin.stamp();
		const grant = await this.request(() =>
			this.api.authorize({ threads, context_epoch: this.contextEpoch })
		);
		const deadline = Date.parse(grant.valid_until);
		if (
			!this.active ||
			!this.lifetime() ||
			!current() ||
			grant.context_epoch !== this.contextEpoch ||
			!grant.principal ||
			!grant.session ||
			!grant.authorization_context ||
			!Number.isFinite(deadline) ||
			deadline <= this.now()
		)
			throw new ThreadError('reconnect');
		if (
			this.binding &&
			(grant.principal !== this.binding.principal ||
				grant.session !== this.binding.session ||
				grant.authorization_context !== this.binding.authorization_context)
		) {
			if (grant.principal !== this.binding.principal || grant.session !== this.binding.session) {
				void this.origin.clear(true, true);
			}
			this.dispose();
			throw new ThreadError('reconnect');
		}
		if (!Array.isArray(grant.items) || grant.items.length !== threads.length)
			throw new ThreadError('reconnect');
		this.origin.bind(grant.principal, grant.session);
		this.binding = grant;
		for (let index = 0; index < threads.length; index++) {
			const expected = threads[index];
			const item = grant.items[index];
			if (
				item.thread_id !== expected.thread_id ||
				item.generation !== expected.generation ||
				JSON.stringify(item.versions) !== JSON.stringify(expected.versions) ||
				!['ok', 'gone'].includes(item.status)
			)
				throw new ThreadError('reconnect');
			if (item.status === 'gone') {
				await this.origin.storage.drop(this.partition(), expected.thread_id);
				throw new ThreadError('unavailable');
			}
		}
		return {
			deadline,
			valid: () => this.active && this.lifetime() && current() && deadline > this.now()
		};
	}
	private async initialize() {
		await this.origin.ready();
		if (!this.active || !this.lifetime()) throw new ThreadError('reconnect');
		if (!this.binding) await this.authorize([]);
	}
	async approve(page: ProjectionPage): Promise<CacheProof> {
		await this.initialize();
		const versions = page.messages
			.filter((message) => message.finished && message.version)
			.map((message) => message.version!);
		const proofs: CacheProof[] = [];
		// Long scroll windows can contain more versions than a single contract request permits.
		for (let offset = 0; offset < Math.max(1, versions.length); offset += 200) {
			proofs.push(
				await this.authorize([
					{
						thread_id: page.thread_id,
						generation: page.generation,
						versions: versions.slice(offset, offset + 200)
					}
				])
			);
		}
		const proof = {
			deadline: Math.min(...proofs.map((proof) => proof.deadline)),
			valid: () => proofs.every((proof) => proof.valid())
		};
		if (!proof.valid()) throw new ThreadError('reconnect');
		return proof;
	}
	async read(thread: string, before = ''): Promise<ProjectionResult & CacheProof> {
		await this.initialize();
		const current = this.origin.stamp();
		const partition = this.partition();
		const cached = await this.origin.storage.read(partition, thread, before);
		if (!this.active || !current()) throw new ThreadError('reconnect');
		if (cached) await this.approve(cached.page);
		const result = await this.request(() =>
			this.api.projection(thread, before, cached?.etag ?? undefined)
		);
		if (!this.active || !current()) throw new ThreadError('reconnect');
		const page = result.notModified ? cached?.page : result.page;
		if (!page || page.thread_id !== thread || (result.notModified && !cached?.etag))
			throw new ThreadError('reconnect');
		const proof = await this.approve(page);
		await this.persist(
			{ page, before, etag: result.etag ?? (result.notModified ? cached!.etag : null) },
			proof
		);
		if (!proof.valid()) throw new ThreadError('reconnect');
		return { ...result, page, ...proof };
	}
	async save(page: ProjectionPage): Promise<CacheProof> {
		const proof = await this.approve(page);
		await this.persist({ page, before: '', etag: null }, proof);
		if (!proof.valid()) throw new ThreadError('reconnect');
		return proof;
	}
	private async persist(record: CacheRecord, proof: CacheProof) {
		const finished = record.page.messages.filter((message) => message.finished && message.version);
		await this.origin.storage.write(
			this.partition(),
			{
				...record,
				etag: finished.length === record.page.messages.length ? record.etag : null,
				page: {
					...record.page,
					title: null,
					preferences: { fallback: fallback(record.page) },
					messages: finished
				}
			},
			proof.valid
		);
	}
}

// Only the platform can bind its session and door/scope to a browser cache partition.
export const browserThreadCache = (api: ThreadAPI, contextEpoch: string) => {
	return new ThreadCache(api, threadCacheOrigin(), location.origin, contextEpoch);
};

export const watchThreadVisibility = (
	clear: (state?: ThreadState) => void,
	revalidate: () => void,
	origin = threadCacheOrigin()
) => {
	const resume = () => {
		if (document.visibilityState === 'visible') revalidate();
	};
	const unsubscribe = origin.subscribe(() => clear('sign_in'));
	document.addEventListener('visibilitychange', resume);
	window.addEventListener('pageshow', resume);
	return () => {
		unsubscribe();
		document.removeEventListener('visibilitychange', resume);
		window.removeEventListener('pageshow', resume);
	};
};
