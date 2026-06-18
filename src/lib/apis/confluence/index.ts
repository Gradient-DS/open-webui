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

export class ConfluenceApiError extends Error {
	status: number;
	constructor(message: string, status: number) {
		super(message);
		this.name = 'ConfluenceApiError';
		this.status = status;
	}
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
	const res = await fetch(url, init);
	if (!res.ok) {
		const error = await res.json().catch(() => ({ detail: res.statusText }));
		throw new ConfluenceApiError(error.detail || `Request failed: ${res.status}`, res.status);
	}
	return res.json();
}

// 401 from the picker proxy = the user has no valid Confluence token yet.
export function isConfluenceAuthError(e: unknown): boolean {
	return e instanceof ConfluenceApiError && e.status === 401;
}

export type ConfluenceAuthResult = 'authorized' | 'cancelled' | 'blocked';

// Open the Atlassian OAuth consent popup for per-user Confluence access and
// resolve once it closes. `knowledge_id` is deliberately omitted: the backend
// defaults it to '__general__', and the token is stored per-user
// (oauth_session keyed by user_id+provider), so it is immediately usable by the
// picker's /browse/sites lookup. Passing knowledge_id=__picker__ would 404 —
// /auth/initiate validates real KB ids and __picker__ is a pseudo-id.
export function authorizeConfluencePopup(): Promise<ConfluenceAuthResult> {
	return new Promise((resolve) => {
		const popup = window.open(
			`${base}/auth/initiate`,
			'confluence_auth',
			'width=600,height=700,scrollbars=yes'
		);
		if (!popup) {
			resolve('blocked');
			return;
		}
		let result: ConfluenceAuthResult = 'cancelled';
		const onMessage = (event: MessageEvent) => {
			if (event.data?.type !== 'confluence_auth_callback') return;
			result = event.data.success ? 'authorized' : 'cancelled';
		};
		window.addEventListener('message', onMessage);
		// Source of truth is whether the popup closed; postMessage can be missed
		// on origin mismatch, so the caller re-checks by retrying listSites().
		const timer = setInterval(() => {
			if (!popup.closed) return;
			clearInterval(timer);
			window.removeEventListener('message', onMessage);
			resolve(result);
		}, 500);
	});
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
	// 'basic' (classic token → site) or 'scoped' (scoped token → gateway).
	// Omitted defaults to 'basic' server-side.
	mode?: 'basic' | 'scoped';
	site_url?: string;
	username?: string;
	api_token?: string;
	// Scoped mode only: optional manual cloudId override (blank → auto-resolve
	// from the site URL server-side).
	cloud_id?: string;
}

export interface ConfluenceTestConnectionResult {
	ok: boolean;
	// Stable machine code (auth_failed, not_found, …) the UI localizes; see
	// `CloudSync/errors.ts`. `detail` is an English debug fallback.
	reason?: string;
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

// One Confluence space, page, or page-subtree opted into the shared knowledge
// base. The type name is kept for back-compat — entries can be spaces or pages,
// discriminated by ``type`` (defaults to ``'space'`` for legacy payloads).
// Shape matches ``SyncItem`` (per-user picker output) so the same picker feeds
// both flows.
export interface ConfluenceSharedKbSpace {
	type?: 'space' | 'page' | null;
	// Legacy alias for space items — older payloads sent {id, key, name, cloud_id}.
	id?: string | null;
	key?: string | null;
	name?: string | null;
	cloud_id?: string | null;
	space_id?: string | null;
	space_key?: string | null;
	site_url?: string | null;
	item_id?: string | null;
	item_path?: string | null;
	include_descendants?: boolean | null;
}

export interface ConfluenceSharedKbStatus {
	kb_mode: string;
	auth_mode: string;
	// Whether the account the shared sync will run with has connected — basic
	// auth: the service credential is configured; oauth: the effective owner
	// (``kb.user_id`` if provisioned, else the calling admin) has a stored
	// token.
	owner_connected?: boolean;
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

// List the Confluence spaces available for the shared KB (pre-synced mode).
// Basic auth: every space the service account can see. OAuth: every space the
// configured owner's token can reach.
export function getConfluenceSharedKbSpaces(
	token: string
): Promise<{ spaces: ConfluenceSharedKbSpace[] }> {
	return apiFetch(`${base}/shared/spaces`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}

// Create (or update) the single shared, public-read Confluence KB. Reads the
// saved auth_mode config — save the form before calling — and stamps the
// passed-in item selection (opt-in) into the KB. ``ownerUserId`` carries the
// basic-mode owner pick ('' = system-owned); ignored in OAuth mode where the
// calling admin is implicitly the owner (only their stored token can run the
// sync).
export function provisionConfluenceSharedKb(
	token: string,
	spaces: ConfluenceSharedKbSpace[] = [],
	ownerUserId: string | null = null
): Promise<ConfluenceSharedKbStatus> {
	return apiFetch(`${base}/shared/provision`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ spaces, owner_user_id: ownerUserId })
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
