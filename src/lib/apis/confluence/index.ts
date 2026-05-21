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

// ─────────────────────────────────────────────────────────────────────
// Basic-auth connection test (admin)
// ─────────────────────────────────────────────────────────────────────

export interface ConfluenceTestConnectionPayload {
	site_url?: string;
	username?: string;
	api_token?: string;
}

export interface ConfluenceTestConnectionResult {
	ok: boolean;
	detail: string;
	space_count?: number;
}

// Probe a basic-auth Confluence credential. Blank fields fall back to the
// stored config server-side, so an admin can test before or after saving.
// The endpoint always returns 200 — `ok` carries the result.
export function testConfluenceConnection(
	token: string,
	payload: ConfluenceTestConnectionPayload = {}
): Promise<ConfluenceTestConnectionResult> {
	return apiFetch(`${base}/auth/test`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(payload)
	});
}

// ─────────────────────────────────────────────────────────────────────
// Shared full-content KB (admin)
// ─────────────────────────────────────────────────────────────────────

// A Confluence space the admin can opt into the shared knowledge base.
export interface ConfluenceSharedKbSpace {
	id: string;
	key?: string | null;
	name?: string | null;
	type?: string | null;
	cloud_id?: string | null;
}

export interface ConfluenceSharedKbStatus {
	kb_mode: string;
	auth_mode: string;
	configured_owner_id: string;
	provisioned: boolean;
	knowledge_id: string | null;
	owner_id?: string;
	status?: string;
	last_sync_at?: number | null;
	last_result?: Record<string, unknown> | null;
	suspended_at?: number | null;
	file_count?: number;
	// Live sync progress — files done / total for the current run.
	progress_current?: number;
	progress_total?: number;
	// The spaces currently opted into the shared KB — pre-fills the picker.
	spaces?: ConfluenceSharedKbSpace[];
}

// Report shared-KB provisioning state and the last sync result.
export function getConfluenceSharedKbStatus(token: string): Promise<ConfluenceSharedKbStatus> {
	return apiFetch(`${base}/shared/status`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}

// List the Confluence spaces available for the shared KB (company-wide mode).
// Enumerates every space the basic-auth service account can see.
export function getConfluenceSharedKbSpaces(
	token: string
): Promise<{ spaces: ConfluenceSharedKbSpace[] }> {
	return apiFetch(`${base}/shared/spaces`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}

// Create (or update) the single shared, public-read Confluence KB. Reads the
// saved owner / auth_mode config — save the form before calling — and stamps
// the passed-in space selection (opt-in) into the KB.
export function provisionConfluenceSharedKb(
	token: string,
	spaces: ConfluenceSharedKbSpace[] = []
): Promise<ConfluenceSharedKbStatus> {
	return apiFetch(`${base}/shared/provision`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ spaces })
	});
}

// Trigger an immediate full sync of the shared Confluence KB.
export function syncConfluenceSharedKb(
	token: string
): Promise<{ message: string; knowledge_id: string }> {
	return apiFetch(`${base}/shared/sync`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${token}` }
	});
}

// Soft-delete the shared Confluence KB. Admin-only — the workspace Knowledge
// UI cannot delete it; this is the only managed removal path.
export function deleteConfluenceSharedKb(
	token: string
): Promise<{ message: string; knowledge_id: string }> {
	return apiFetch(`${base}/shared`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${token}` }
	});
}

// ─────────────────────────────────────────────────────────────────────
// Ad-hoc page content (chat + menu)
// ─────────────────────────────────────────────────────────────────────

export interface ConfluencePageContent {
	page_id: string;
	title: string;
	content: string;
}

// Fetch one Confluence page rendered as Markdown — for attaching a page as
// one-off chat context via the + menu picker.
export function getConfluencePageContent(
	token: string,
	cloudId: string,
	pageId: string
): Promise<ConfluencePageContent> {
	return apiFetch(
		`${base}/page/${encodeURIComponent(cloudId)}/${encodeURIComponent(pageId)}/content`,
		{ headers: { Authorization: `Bearer ${token}` } }
	);
}
