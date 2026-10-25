import {
	ThreadError,
	type IndexItem,
	type MessageView,
	type ProjectionPage,
	type ThreadAPI,
	type ThreadState,
	type SidebarItem
} from '$lib/apis/threads';

export type HistoryMessage = {
	id: string;
	parentId: string | null;
	childrenIds: string[];
	role: 'user' | 'assistant';
	content: string;
	done: boolean;
	timestamp: number;
	position: number;
	version: string | null;
	[key: string]: unknown;
};

export type VersionTarget = { threadId: string; at: number };
export type ThreadHistory = {
	messages: Record<string, HistoryMessage>;
	currentId: string | null;
	versions: Record<number, VersionTarget[]>;
};

const emptyHistory = (): ThreadHistory => ({ messages: {}, currentId: null, versions: {} });
const messageId = (page: ProjectionPage, position: number) =>
	`${page.thread_id}:${page.generation}:${position}`;

// 7a leaves content as JsonValue. Accept text and OWUI presentation fields;
// reject other shapes instead of displaying raw runtime state as JSON.
const presentation = (content: unknown): Record<string, unknown> => {
	if (typeof content === 'string') return { role: 'assistant', content };
	if (!content || typeof content !== 'object' || Array.isArray(content)) {
		throw new ThreadError('reconnect');
	}
	const value = content as Record<string, unknown>;
	if (!['content', 'text', 'output'].some((key) => key in value))
		throw new ThreadError('reconnect');
	const text = value.content ?? value.text ?? '';
	if (
		typeof text !== 'string' ||
		(value.role !== undefined && !['user', 'assistant'].includes(String(value.role)))
	) {
		throw new ThreadError('reconnect');
	}
	const result: Record<string, unknown> = { role: value.role ?? 'assistant', content: text };
	for (const key of [
		'output',
		'sources',
		'files',
		'statusHistory',
		'reasoning',
		'model',
		'timestamp',
		'notices'
	]) {
		if (value[key] !== undefined) result[key] = value[key];
	}
	return result;
};

export const buildThreadHistory = (page: ProjectionPage, forks: IndexItem[]): ThreadHistory => {
	const history = emptyHistory();
	let previous: HistoryMessage | undefined;
	for (const view of [...page.messages].sort((a, b) => a.position - b.position)) {
		const id = messageId(page, view.position);
		if (history.messages[id]) throw new ThreadError('reconnect');
		const fields = presentation(view.content);
		const message: HistoryMessage = {
			...fields,
			id,
			parentId: previous?.id ?? null,
			childrenIds: [],
			role: fields.role as HistoryMessage['role'],
			content: fields.content as string,
			timestamp: typeof fields.timestamp === 'number' ? fields.timestamp : 0,
			done: view.finished,
			position: view.position,
			version: view.version
		};
		if (previous) previous.childrenIds.push(id);
		history.messages[id] = message;
		previous = message;
	}
	history.currentId = previous?.id ?? null;
	// Fork index rows carry no message bodies. Arrows load their projection on demand.
	for (const fork of forks) {
		if (fork.parent_thread !== page.thread_id || fork.parent_position === null) continue;
		const at = fork.parent_position;
		const targets = history.versions[at] ?? [{ threadId: page.thread_id, at }];
		if (!targets.some((target) => target.threadId === fork.thread_id)) {
			targets.push({ threadId: fork.thread_id, at });
		}
		history.versions[at] = targets;
	}
	return history;
};

export const mergeProjection = (current: ProjectionPage, older: ProjectionPage): ProjectionPage => {
	if (older.thread_id !== current.thread_id || older.generation !== current.generation) {
		throw new ThreadError('reconnect');
	}
	const messages = new Map(older.messages.map((message) => [message.position, message]));
	// The newer window (including an in-flight stream) wins on overlapping pages.
	for (const message of current.messages) messages.set(message.position, message);
	return {
		...current,
		messages: [...messages.values()].sort((a, b) => a.position - b.position),
		next_cursor: older.next_cursor
	};
};

export type WindowSnapshot = {
	page: ProjectionPage | null;
	history: ThreadHistory;
	loading: boolean;
	state: ThreadState | null;
	family?: VersionTarget[];
};

export class ThreadWindow {
	private epoch = 0;
	private forks: IndexItem[] = [];
	private pending: Promise<void> | null = null;
	snapshot: WindowSnapshot = { page: null, history: emptyHistory(), loading: false, state: null };
	constructor(
		private api: ThreadAPI,
		private changed: (value: WindowSnapshot) => void = () => {}
	) {}
	private publish() {
		this.changed({ ...this.snapshot });
	}
	private fail(error: unknown) {
		this.snapshot = {
			page: null,
			history: emptyHistory(),
			loading: false,
			state: error instanceof ThreadError ? error.state : 'reconnect'
		};
		this.forks = [];
		this.publish();
	}
	clear() {
		this.epoch++;
		this.pending = null;
		this.forks = [];
		this.snapshot = { page: null, history: emptyHistory(), loading: false, state: null };
		this.publish();
	}
	private async readForks(threadId: string, epoch: number): Promise<IndexItem[]> {
		const forks: IndexItem[] = [];
		let cursor: string | null = '';
		const seen = new Set<string>();
		do {
			if (seen.has(cursor)) throw new ThreadError('reconnect');
			seen.add(cursor);
			const next = await this.api.forks(threadId, cursor);
			if (epoch !== this.epoch) return [];
			forks.push(...next.items);
			cursor = next.next_cursor;
		} while (cursor !== null);
		return forks;
	}
	async open(threadId: string, parent?: VersionTarget) {
		this.clear();
		const epoch = this.epoch;
		this.snapshot.loading = true;
		this.publish();
		try {
			const result = await this.api.projection(threadId);
			if (epoch !== this.epoch) return;
			if (!result.page || result.page.thread_id !== threadId) throw new ThreadError('reconnect');
			const forks = await this.readForks(threadId, epoch);
			let family: VersionTarget[] | undefined;
			if (parent && parent.threadId !== threadId) {
				try {
					const siblings = await this.readForks(parent.threadId, epoch);
					family = [
						{ threadId: parent.threadId, at: parent.at },
						...siblings
							.filter(
								(item) =>
									item.parent_thread === parent.threadId && item.parent_position === parent.at
							)
							.map((item) => ({ threadId: item.thread_id, at: parent.at }))
					];
					if (!family.some((target) => target.threadId === threadId)) family = undefined;
				} catch (error) {
					// A readable clone can outlive access to its parent. Omit its parent arrows.
					if (!(error instanceof ThreadError) || error.state !== 'unavailable') throw error;
				}
			}
			if (epoch !== this.epoch) return;
			this.forks = forks;
			this.snapshot = {
				page: result.page,
				history: buildThreadHistory(result.page, forks),
				loading: false,
				state: null,
				family
			};
			this.publish();
		} catch (error) {
			if (epoch === this.epoch) this.fail(error);
		}
	}
	older(): Promise<void> {
		if (this.pending) return this.pending;
		const page = this.snapshot.page;
		if (!page?.next_cursor || this.snapshot.loading) return Promise.resolve();
		const epoch = this.epoch;
		this.snapshot.loading = true;
		this.publish();
		const pending = (async () => {
			try {
				const result = await this.api.projection(page.thread_id, page.next_cursor!);
				if (epoch !== this.epoch) return;
				if (!result.page || result.page.next_cursor === page.next_cursor)
					throw new ThreadError('reconnect');
				const merged = mergeProjection(this.snapshot.page!, result.page);
				this.snapshot = {
					...this.snapshot,
					page: merged,
					history: buildThreadHistory(merged, this.forks),
					loading: false,
					state: null
				};
				this.publish();
			} catch (error) {
				if (epoch === this.epoch) this.fail(error);
			}
		})();
		this.pending = pending;
		void pending.finally(() => {
			if (epoch === this.epoch) this.pending = null;
		});
		return pending;
	}
	// 7f connects the authenticated platform stream's projected updates here.
	stream(threadId: string, generation: string, message: MessageView) {
		const page = this.snapshot.page;
		if (!page || page.thread_id !== threadId || page.generation !== generation) return;
		const previous = page.messages.find((view) => view.position === message.position);
		if (previous?.finished) return;
		try {
			const messages = page.messages.filter((view) => view.position !== message.position);
			messages.push(message);
			const next = { ...page, messages: messages.sort((a, b) => a.position - b.position) };
			this.snapshot = {
				...this.snapshot,
				page: next,
				history: buildThreadHistory(next, this.forks)
			};
			this.publish();
		} catch (error) {
			this.fail(error);
		}
	}
}

export type SidebarFilter = { folderId?: string | null; pinned?: boolean; archived?: boolean };
export const filterSidebar = (items: SidebarItem[], filter: SidebarFilter): SidebarItem[] =>
	items.filter(
		({ organization: record }) =>
			(filter.folderId === undefined || (record?.folder_id ?? null) === filter.folderId) &&
			(filter.pinned === undefined || (record?.pinned ?? false) === filter.pinned) &&
			(filter.archived === undefined || (record?.archived ?? false) === filter.archived)
	);

export type SidebarSnapshot = {
	items: SidebarItem[];
	cursor: string | null;
	loading: boolean;
	state: ThreadState | null;
};

export class ThreadSidebar {
	private epoch = 0;
	private query = '';
	snapshot: SidebarSnapshot = { items: [], cursor: null, loading: false, state: null };
	constructor(
		private api: ThreadAPI,
		private changed: (value: SidebarSnapshot) => void = () => {}
	) {}
	clear() {
		this.epoch++;
		this.snapshot = { items: [], cursor: null, loading: false, state: null };
		this.changed(this.snapshot);
	}
	async search(query = '') {
		this.clear();
		this.query = query;
		await this.load('');
	}
	async more() {
		if (this.snapshot.cursor && !this.snapshot.loading) await this.load(this.snapshot.cursor);
	}
	private async load(cursor: string) {
		const epoch = this.epoch;
		this.snapshot = { ...this.snapshot, loading: true };
		this.changed(this.snapshot);
		try {
			const page = await this.api.index(this.query, cursor);
			if (epoch !== this.epoch) return;
			if (page.next_cursor !== null && page.next_cursor === cursor)
				throw new ThreadError('reconnect');
			const items = new Map(this.snapshot.items.map((item) => [item.thread_id, item]));
			for (const item of page.items) items.set(item.thread_id, item);
			this.snapshot = {
				items: [...items.values()],
				cursor: page.next_cursor,
				loading: false,
				state: null
			};
		} catch (error) {
			if (epoch !== this.epoch) return;
			this.snapshot = {
				items: [],
				cursor: null,
				loading: false,
				state: error instanceof ThreadError ? error.state : 'reconnect'
			};
		}
		this.changed(this.snapshot);
	}
}
