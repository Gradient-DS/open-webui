import { WEBUI_API_BASE_URL } from '$lib/constants';
import { ThreadError, type ProjectionPage, type ThreadState, stateText } from '$lib/apis/threads';

export type ShareAudience = { kind: 'person' | 'group' | 'folder'; id: string };
export type ThreadShare = { audience: ShareAudience; at: number; name?: string };

const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const personRef = new RegExp(`^platform:user:${uuid}$`);
const groupId = new RegExp(`^${uuid}$`);

export const personAudience = (user: { person_id?: string | null }): ShareAudience | null =>
	typeof user?.person_id === 'string' && personRef.test(user.person_id)
		? { kind: 'person', id: user.person_id }
		: null;

export const groupAudience = (group: { id: string }): ShareAudience | null =>
	groupId.test(group.id) ? { kind: 'group', id: `platform:group:${group.id}` } : null;

export const shareStateText = (error: unknown): string =>
	error instanceof ThreadError ? stateText[error.state] : stateText.reconnect;

export const sharingAPI = (token: string) => {
	const request = async (path: string, method = 'GET', body?: unknown) => {
		const response = await fetch(`${WEBUI_API_BASE_URL}/chats${path}`, {
			method,
			cache: 'no-store',
			headers: {
				Authorization: `Bearer ${token}`,
				'Content-Type': 'application/json'
			},
			body: body === undefined ? undefined : JSON.stringify(body)
		}).catch(() => {
			throw new ThreadError('reconnect');
		});
		if (!response.ok) {
			const payload = await response.json().catch(() => ({}));
			const state: ThreadState =
				response.status === 401
					? 'sign_in'
					: Object.hasOwn(stateText, payload?.state ?? '')
						? payload.state
						: 'reconnect';
			throw new ThreadError(state);
		}
		return response.json();
	};
	return {
		read: (id: string, before = ''): Promise<ProjectionPage> =>
			request(`/share/${encodeURIComponent(id)}?${new URLSearchParams({ before, window: '200' })}`),
		list: (id: string): Promise<{ items: ThreadShare[]; next_cursor: null }> =>
			request(`/${encodeURIComponent(id)}/share`),
		share: (id: string, share: ThreadShare): Promise<{ share_id: string }> =>
			request(`/${encodeURIComponent(id)}/share`, 'POST', share),
		unshare: (id: string, audience: ShareAudience): Promise<boolean> =>
			request(`/${encodeURIComponent(id)}/share`, 'DELETE', { audience }),
		clone: (id: string): Promise<{ id: string }> =>
			request(`/${encodeURIComponent(id)}/clone/shared`, 'POST')
	};
};
