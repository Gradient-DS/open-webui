import { WEBUI_API_BASE_URL } from '$lib/constants';

export type MessageView = {
	position: number;
	content: unknown;
	finished: boolean;
	version: string | null;
};

export type ProjectionPage = {
	thread_id: string;
	generation: string;
	title: string | null;
	preferences: unknown;
	messages: MessageView[];
	next_cursor: string | null;
};

export type IndexItem = {
	thread_id: string;
	title: string | null;
	updated_at: string;
	parent_thread: string | null;
	parent_position: number | null;
};

export type Organization = {
	id: string;
	thread_id: string;
	folder_id: string | null;
	pinned: boolean;
	archived: boolean;
	last_read_at: number | null;
};

export type SidebarItem = IndexItem & { organization: Organization | null };
export type Page<T> = { items: T[]; next_cursor: string | null };
export type ThreadState = 'reconnect' | 'unavailable' | 'sign_in' | 'invalid_request';

export class ThreadError extends Error {
	constructor(public state: ThreadState) {
		super(state);
	}
}

export const stateText: Record<ThreadState, string> = {
	reconnect: 'Reconnect to view this chat',
	unavailable: 'This chat is unavailable',
	sign_in: 'Sign in again to view this chat',
	invalid_request: 'Reload this chat to continue'
};

export type ProjectionResult = {
	page: ProjectionPage | null;
	etag: string | null;
	notModified: boolean;
};

export interface ThreadAPI {
	projection(threadId: string, before?: string, etag?: string): Promise<ProjectionResult>;
	forks(threadId: string, cursor?: string): Promise<Page<IndexItem>>;
	fork(threadId: string, at: number): Promise<{ thread_id: string }>;
	index(q?: string, cursor?: string): Promise<Page<SidebarItem>>;
}

// The login token stays on the server. This boundary takes only the OWUI session credential.
export const threadAPI = (token: string, window = 20): ThreadAPI => {
	const request = async (path: string, init: RequestInit = {}) => {
		let response: Response;
		try {
			response = await fetch(`${WEBUI_API_BASE_URL}/threads${path}`, {
				...init,
				cache: 'no-store',
				headers: {
					Accept: 'application/json',
					'Content-Type': 'application/json',
					Authorization: `Bearer ${token}`,
					...init.headers
				}
			});
		} catch {
			throw new ThreadError('reconnect');
		}
		if (!response.ok && response.status !== 304) {
			const state: ThreadState =
				response.status === 401
					? 'sign_in'
					: [403, 404].includes(response.status)
						? 'unavailable'
						: response.status === 422
							? 'invalid_request'
							: 'reconnect';
			throw new ThreadError(state);
		}
		return response;
	};
	return {
		async projection(threadId, before = '', etag) {
			const query = new URLSearchParams({ window: String(window), before });
			const response = await request(`/${encodeURIComponent(threadId)}?${query}`, {
				headers: etag ? { 'If-None-Match': etag } : {}
			});
			if (response.status === 304 && !etag) throw new ThreadError('reconnect');
			return {
				page: response.status === 304 ? null : await response.json(),
				etag: response.headers.get('ETag'),
				notModified: response.status === 304
			};
		},
		async forks(threadId, cursor = '') {
			const query = new URLSearchParams({ cursor });
			return (await request(`/${encodeURIComponent(threadId)}/forks?${query}`)).json();
		},
		async fork(threadId, at) {
			return (
				await request(`/${encodeURIComponent(threadId)}/fork`, {
					method: 'POST',
					body: JSON.stringify({ at })
				})
			).json();
		},
		async index(q = '', cursor = '') {
			return (await request(`?${new URLSearchParams({ q, cursor })}`)).json();
		}
	};
};
