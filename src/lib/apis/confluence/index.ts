import { createSyncApi } from '$lib/apis/sync';
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type {
	SyncStatusResponse,
	FailedFile,
	TokenStatusResponse,
	SyncErrorType
} from '$lib/apis/sync';

// Provider-specific types
export interface SyncItem {
	type: 'space' | 'page';
	cloud_id: string;
	space_id?: string;
	space_key?: string;
	site_url?: string;
	item_id: string;
	item_path: string;
	name: string;
	include_descendants?: boolean;
}

export interface SyncItemsRequest {
	knowledge_id: string;
	items: SyncItem[];
	access_token?: string;
}

export interface ConfluenceSite {
	cloud_id: string;
	url: string;
	name: string;
}

export interface ConfluenceSpaceSummary {
	id: string;
	key: string;
	name: string;
	type: string;
	status: string;
	homepage_id?: string;
}

export interface ConfluencePageSummary {
	id: string;
	title: string;
	status: string;
	space_id?: string;
	parent_id?: string;
}

// Create API instance with Confluence base path
const api = createSyncApi('confluence');

// Re-export with original function names for backward compatibility
export const startConfluenceSyncItems = api.startSyncItems;
export const getSyncStatus = api.getSyncStatus;
export const cancelSync = api.cancelSync;
export const getSyncedCollections = api.getSyncedCollections;
export const getTokenStatus = api.getTokenStatus;
export const removeSource = api.removeSource;
export const revokeToken = api.revokeToken;

// ─────────────────────────────────────────────────────────────────────
// Picker proxy helpers
// ─────────────────────────────────────────────────────────────────────

const base = `${WEBUI_API_BASE_URL}/confluence`;

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
	const res = await fetch(url, init);
	if (!res.ok) {
		const error = await res.json().catch(() => ({ detail: res.statusText }));
		throw new Error(error.detail || `Request failed: ${res.status}`);
	}
	return res.json();
}

export function listSites(token: string): Promise<{ sites: ConfluenceSite[] }> {
	return apiFetch(`${base}/browse/sites`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}

export function listSpaces(
	token: string,
	cloudId: string,
	cursor?: string
): Promise<{ site_url: string; spaces: ConfluenceSpaceSummary[]; next_cursor: string | null }> {
	const params = new URLSearchParams({ cloud_id: cloudId });
	if (cursor) params.set('cursor', cursor);
	return apiFetch(`${base}/browse/spaces?${params.toString()}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}

export function listPages(
	token: string,
	cloudId: string,
	opts: { spaceId?: string; parentId?: string; cursor?: string }
): Promise<{ site_url: string; pages: ConfluencePageSummary[]; next_cursor: string | null }> {
	const params = new URLSearchParams({ cloud_id: cloudId });
	if (opts.spaceId) params.set('space_id', opts.spaceId);
	if (opts.parentId) params.set('parent_id', opts.parentId);
	if (opts.cursor) params.set('cursor', opts.cursor);
	return apiFetch(`${base}/browse/pages?${params.toString()}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}
