import {
	ThreadError,
	type IndexItem,
	type MessageView,
	type ProjectionPage,
	type ThreadAPI,
	type ThreadState,
	type SidebarItem
} from '$lib/apis/threads';
import type { CacheProof, ThreadCache } from './threadCache';

export type HistoryMessage = {
	id: string;
	parentId: string | null;
	childrenIds: string[];
	role: 'user' | 'assistant';
	content: string;
	done: boolean;
	timestamp: number;
	position: number;
	positions: number[];
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

const record = (value: unknown): Record<string, unknown> | null =>
	value !== null && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;

const escapeHtml = (text: string) =>
	text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const presentation = (content: unknown): Record<string, unknown> => {
	if (typeof content === 'string') return { role: 'assistant', content };
	const value = record(content);
	if (!value) throw new ThreadError('reconnect');
	const text = value.content ?? value.text ?? '';
	if (typeof text !== 'string') throw new ThreadError('reconnect');
	return { ...value, role: value.role ?? 'assistant', content: text };
};

const toolDescriptions: Record<string, string> = {
	web_search: 'Searching the web…',
	fetch: 'Fetching web pages...'
};

const assistantContent = (fields: Record<string, unknown>) => {
	const parts: string[] = [];
	if (typeof fields.reasoning === 'string' && fields.reasoning.trim()) {
		const reasoning = fields.reasoning
			.trim()
			.split('\n')
			.map((line) => (line.startsWith('>') ? line : `> ${line}`))
			.join('\n');
		parts.push(
			`<details type="reasoning" done="true" duration="0">\n<summary>Thought for 0 seconds</summary>\n${escapeHtml(reasoning)}\n</details>`
		);
	}
	const text = Array.from(fields.content as string);
	let at = 0;
	let marked = '';
	const sources: unknown[] = Array.isArray(fields.sources) ? [...fields.sources] : [];
	const markers = (Array.isArray(fields.markers) ? fields.markers : [])
		.map(record)
		.filter((marker): marker is Record<string, unknown> => marker !== null);
	for (const marker of markers.sort((a, b) => Number(a.at) - Number(b.at))) {
		if (
			!Number.isInteger(marker.at) ||
			Number(marker.at) < at ||
			Number(marker.at) > text.length ||
			typeof marker.text !== 'string'
		)
			continue;
		marked += text.slice(at, Number(marker.at)).join('') + marker.text;
		at = Number(marker.at);
		for (const cited of Array.isArray(marker.cited) ? marker.cited : []) {
			const citation = record(cited);
			if (!citation || typeof citation.source !== 'string' || !Number.isInteger(citation.written))
				continue;
			if (!sources.some((source) => record(source)?.n === citation.written)) {
				sources.push({
					n: citation.written,
					source: { id: citation.source, name: citation.source, url: citation.source },
					document: [''],
					metadata: [{ source: citation.source, name: citation.source }]
				});
			}
		}
	}
	marked += text.slice(at).join('');
	if (marked) parts.push(marked);
	const statuses: unknown[] = Array.isArray(fields.statusHistory) ? [...fields.statusHistory] : [];
	for (const entry of Array.isArray(fields.tool_calls) ? fields.tool_calls : []) {
		const call = record(entry);
		if (!call || typeof call.name !== 'string' || typeof call.id !== 'string') continue;
		const status = statuses.map(record).find((status) => status?.call_id === call.id) ?? {
			action: call.name,
			call_id: call.id,
			description: toolDescriptions[call.name] ?? 'Executing tool…',
			done: true
		};
		if (!statuses.includes(status)) statuses.push(status);
		parts.push(
			`<details type="tool_calls" done="true" name="${escapeHtml(call.name)}">\n<summary>${escapeHtml(String(status.description ?? call.name))}</summary>\n</details>`
		);
	}
	return { content: parts.join('\n\n'), sources, statusHistory: statuses };
};

export const buildThreadHistory = (page: ProjectionPage, forks: IndexItem[]): ThreadHistory => {
	const history = emptyHistory();
	const seen = new Set<number>();
	const anchors = new Map<number, number>();
	let previous: HistoryMessage | undefined;
	for (const view of [...page.messages].sort((a, b) => a.position - b.position)) {
		if (!Number.isInteger(view.position) || view.position < 1 || seen.has(view.position))
			throw new ThreadError('reconnect');
		seen.add(view.position);
		const fields = presentation(view.content);
		if (!['user', 'assistant', 'tool'].includes(String(fields.role))) continue;
		const role = fields.role === 'user' ? 'user' : 'assistant';
		const shown =
			fields.role === 'assistant'
				? assistantContent(fields)
				: {
						content: fields.role === 'tool' ? '' : (fields.content as string),
						sources: fields.sources,
						statusHistory: fields.statusHistory
					};
		if (role === 'assistant' && previous?.role === 'assistant') {
			previous.content = [previous.content, shown.content].filter(Boolean).join('\n\n');
			for (const key of ['sources', 'statusHistory', 'files', 'output']) {
				const added = key in shown ? shown[key as keyof typeof shown] : fields[key];
				if (Array.isArray(added) && added.length)
					previous[key] = [
						...(Array.isArray(previous[key]) ? (previous[key] as unknown[]) : []),
						...added
					];
			}
			if (fields.model) previous.model = fields.model;
			previous.done = previous.done && view.finished;
			previous.position = view.position;
			previous.version = view.version;
			previous.positions.push(view.position);
			continue;
		}
		const id = messageId(page, view.position);
		const message: HistoryMessage = {
			id,
			parentId: previous?.id ?? null,
			childrenIds: [],
			role,
			content: shown.content,
			timestamp: typeof fields.timestamp === 'number' ? fields.timestamp : 0,
			done: view.finished,
			position: view.position,
			positions: [view.position],
			version: view.version
		};
		for (const key of ['output', 'files', 'reasoning', 'model', 'notices']) {
			if (fields[key] !== undefined) message[key] = fields[key];
		}
		if (Array.isArray(shown.sources) && shown.sources.length) message.sources = shown.sources;
		if (Array.isArray(shown.statusHistory) && shown.statusHistory.length)
			message.statusHistory = shown.statusHistory;
		if (previous) previous.childrenIds.push(id);
		history.messages[id] = message;
		previous = message;
	}
	for (const message of Object.values(history.messages)) {
		for (const position of message.positions) anchors.set(position, message.position);
	}
	history.currentId = previous?.id ?? null;
	// A folded bubble anchors every underlying fork position; each target keeps its original cursor.
	for (const fork of forks) {
		if (fork.parent_thread !== page.thread_id || fork.parent_position === null) continue;
		const at = fork.parent_position;
		const anchor = anchors.get(at) ?? at;
		const targets = history.versions[anchor] ?? [{ threadId: page.thread_id, at }];
		if (!targets.some((target) => target.threadId === fork.thread_id)) {
			targets.push({ threadId: fork.thread_id, at });
		}
		history.versions[anchor] = targets;
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
	private proof?: CacheProof;
	private expiry?: ReturnType<typeof setTimeout>;
	private renewal?: ReturnType<typeof setTimeout>;
	private approving: Promise<void> | null = null;
	snapshot: WindowSnapshot = { page: null, history: emptyHistory(), loading: false, state: null };
	constructor(
		private api: ThreadAPI,
		private changed: (value: WindowSnapshot) => void = () => {},
		private cache?: ThreadCache
	) {}
	private publish() {
		if (this.cache && this.snapshot.page && !this.proof?.valid()) {
			this.fail(new ThreadError('reconnect'));
			return;
		}
		this.changed({ ...this.snapshot });
	}
	private arm(proof: CacheProof) {
		clearTimeout(this.expiry);
		clearTimeout(this.renewal);
		this.proof = proof;
		const remaining = proof.deadline - Date.now();
		this.expiry = setTimeout(
			() => {
				this.epoch++;
				this.fail(new ThreadError('reconnect'));
			},
			Math.min(2 ** 31 - 1, Math.max(0, remaining))
		);
		this.renewal = setTimeout(
			() => void this.reapprove(),
			Math.min(2 ** 31 - 1, Math.max(0, remaining - Math.min(1000, remaining / 4)))
		);
	}
	private reapprove(): Promise<void> {
		if (this.approving) return this.approving;
		const epoch = this.epoch;
		const pending = (async () => {
			try {
				let page = this.snapshot.page;
				while (this.cache && page) {
					const proof = await this.cache.approve(page);
					if (epoch !== this.epoch) return;
					if (page === this.snapshot.page) {
						this.arm(proof);
						return;
					}
					page = this.snapshot.page;
				}
			} catch (error) {
				if (epoch === this.epoch) {
					this.epoch++;
					this.fail(error);
				}
			}
		})();
		this.approving = pending;
		void pending.finally(() => {
			if (this.approving === pending) this.approving = null;
		});
		return pending;
	}
	resume(reopen: () => void) {
		if (!this.snapshot.page || (this.cache && !this.proof?.valid())) {
			this.clear();
			reopen();
		} else if (this.cache) {
			void this.reapprove();
		}
	}
	private fail(error: unknown) {
		clearTimeout(this.expiry);
		clearTimeout(this.renewal);
		this.proof = undefined;
		this.snapshot = {
			page: null,
			history: emptyHistory(),
			loading: false,
			state: error instanceof ThreadError ? error.state : 'reconnect'
		};
		this.forks = [];
		this.publish();
	}
	clear(state: ThreadState | null = null) {
		clearTimeout(this.expiry);
		clearTimeout(this.renewal);
		this.approving = null;
		this.proof = undefined;
		this.epoch++;
		this.pending = null;
		this.forks = [];
		this.snapshot = { page: null, history: emptyHistory(), loading: false, state };
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
			const cached = this.cache ? await this.cache.read(threadId) : null;
			const result = cached ?? (await this.api.projection(threadId));
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
			if (cached) {
				if (!cached.valid()) throw new ThreadError('reconnect');
				this.arm(cached);
			}
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
				const result = await (this.cache
					? this.cache.read(page.thread_id, page.next_cursor!)
					: this.api.projection(page.thread_id, page.next_cursor!));
				if (epoch !== this.epoch) return;
				if (!result.page || result.page.next_cursor === page.next_cursor)
					throw new ThreadError('reconnect');
				const merged = mergeProjection(this.snapshot.page!, result.page);
				if (this.cache) {
					const proof = await this.cache.approve(merged);
					if (epoch !== this.epoch) return;
					this.arm(proof);
				}
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
			if (this.cache && message.finished && this.snapshot.page === next) {
				// Stream delivery has live authority; caching gets its own version proof.
				const epoch = this.epoch;
				void this.cache
					.save(next)
					.then((proof) => {
						if (epoch !== this.epoch) return;
						if (this.snapshot.page === next && proof.valid()) this.arm(proof);
					})
					.catch((error) => {
						if (
							epoch === this.epoch &&
							error instanceof ThreadError &&
							error.state === 'unavailable'
						)
							this.fail(error);
					});
			}
		} catch (error) {
			this.fail(error);
		}
	}
}

export type SidebarFilter = {
	folderId?: string | null;
	pinned?: boolean;
	archived?: boolean;
	tag?: string;
};
export const filterSidebar = (items: SidebarItem[], filter: SidebarFilter): SidebarItem[] =>
	items.filter(
		({ organization: record, tags }) =>
			(filter.folderId === undefined || (record?.folder_id ?? null) === filter.folderId) &&
			(filter.pinned === undefined || (record?.pinned ?? false) === filter.pinned) &&
			(filter.archived === undefined || (record?.archived ?? false) === filter.archived) &&
			(filter.tag === undefined || tags.includes(filter.tag))
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
		private changed: (value: SidebarSnapshot) => void = () => {},
		private authenticationFailed: () => void = () => {}
	) {}
	clear(state: ThreadState | null = null) {
		this.epoch++;
		this.snapshot = { items: [], cursor: null, loading: false, state };
		this.changed(this.snapshot);
	}
	async search(query = '') {
		this.clear();
		this.query = query;
		await this.load('');
	}
	async refresh() {
		this.epoch++;
		await this.load('', true);
	}
	async more() {
		if (this.snapshot.cursor && !this.snapshot.loading) await this.load(this.snapshot.cursor);
	}
	private async load(cursor: string, replace = false) {
		const epoch = this.epoch;
		this.snapshot = { ...this.snapshot, loading: true };
		this.changed(this.snapshot);
		try {
			const page = await this.api.index(this.query, cursor);
			if (epoch !== this.epoch) return;
			if (page.next_cursor !== null && page.next_cursor === cursor)
				throw new ThreadError('reconnect');
			const items = new Map(
				replace ? [] : this.snapshot.items.map((item) => [item.thread_id, item] as const)
			);
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
			if (this.snapshot.state === 'sign_in') this.authenticationFailed();
		}
		this.changed(this.snapshot);
	}
}
