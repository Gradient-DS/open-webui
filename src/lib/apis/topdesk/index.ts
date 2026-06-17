import { WEBUI_API_BASE_URL } from '$lib/constants';

// TOPdesk frontend API client.
//
// Modelled on `$lib/apis/confluence` (same `apiFetch` helper + throw
// conventions). TOPdesk is simpler than Confluence: one service-account auth
// mode (no OAuth), so there is no token/site browsing — only a connection
// probe, a knowledge-item tree-picker proxy, and the shared full-content KB
// lifecycle (status / provision / sync / delete). Every route is admin-gated
// server-side. Paths match `routers/topdesk_sync.py` mounted at
// `/api/v1/topdesk`.

const base = `${WEBUI_API_BASE_URL}/topdesk`;

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
	const res = await fetch(url, init);
	if (!res.ok) {
		const error = await res.json().catch(() => ({ detail: res.statusText }));
		throw new Error(error.detail || `Request failed: ${res.status}`);
	}
	return res.json();
}

// ─────────────────────────────────────────────────────────────────────
// Connection test (admin)
// ─────────────────────────────────────────────────────────────────────

export interface TopdeskTestConnectionPayload {
	url?: string;
	username?: string;
	app_password?: string;
}

export interface TopdeskTestConnectionResult {
	ok: boolean;
	// Stable machine code (auth_failed, unreachable, …) the UI localizes; see
	// `CloudSync/errors.ts`. `detail` is an English debug fallback.
	reason?: string;
	detail: string;
}

// Probe a TOPdesk service credential. Blank fields fall back to the stored
// config server-side, so an admin can test before or after saving. The
// endpoint always returns 200 — `ok` carries the result.
export function testTopdeskConnection(
	token: string,
	payload: TopdeskTestConnectionPayload = {}
): Promise<TopdeskTestConnectionResult> {
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
// Tree-picker proxy (admin)
// ─────────────────────────────────────────────────────────────────────

// One TOPdesk knowledge item as returned by the tree-picker proxy. `has_children`
// is a best-effort flag (the picker offers an expand affordance and the
// on-expand child query reveals whether there are real children).
export interface TopdeskBrowseItem {
	id: string;
	name: string;
	number?: string | null;
	has_children: boolean;
	status?: string | null;
}

// List TOPdesk knowledge items for the tree picker. Without `parentId` returns
// the root items; with `parentId` returns that item's direct children.
export function browseTopdeskItems(
	token: string,
	parentId?: string
): Promise<{ items: TopdeskBrowseItem[] }> {
	const params = new URLSearchParams();
	if (parentId) params.set('parent_id', parentId);
	const query = params.toString();
	return apiFetch(`${base}/browse/items${query ? `?${query}` : ''}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}

// ─────────────────────────────────────────────────────────────────────
// Shared full-content KB (admin)
// ─────────────────────────────────────────────────────────────────────

// One TOPdesk knowledge item (or subtree) opted into the shared KB. Shape
// matches the picker output and the backend `TopdeskKbItem` model.
export interface TopdeskKbItem {
	type?: 'folder' | 'file' | null;
	item_id: string;
	name?: string | null;
	include_descendants?: boolean | null;
}

export interface TopdeskSharedKbStatus {
	// True when the global service credential (URL + app password) is configured.
	credential_configured?: boolean;
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
	// The items currently opted into the shared KB — pre-fills the picker.
	items?: TopdeskKbItem[];
}

// Report shared-KB provisioning state and the last sync result.
export function getTopdeskSharedKbStatus(token: string): Promise<TopdeskSharedKbStatus> {
	return apiFetch(`${base}/shared/status`, {
		headers: { Authorization: `Bearer ${token}` }
	});
}

// Create (or update) the single shared, public-read TOPdesk KB. Stamps the
// passed-in item selection (opt-in) into the KB. `ownerUserId` carries the
// admin's owner pick ('' = system-owned KB synced with the global service
// credential).
export function provisionTopdeskSharedKb(
	token: string,
	items: TopdeskKbItem[] = [],
	ownerUserId: string | null = null
): Promise<TopdeskSharedKbStatus> {
	return apiFetch(`${base}/shared/provision`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ items, owner_user_id: ownerUserId })
	});
}

// Trigger an immediate full sync of the shared TOPdesk KB.
export function syncTopdeskSharedKb(
	token: string
): Promise<{ message: string; knowledge_id: string }> {
	return apiFetch(`${base}/shared/sync`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${token}` }
	});
}

// Soft-delete the shared TOPdesk KB. Admin-only — the workspace Knowledge UI
// cannot delete it; this is the only managed removal path.
export function deleteTopdeskSharedKb(
	token: string
): Promise<{ message: string; knowledge_id: string }> {
	return apiFetch(`${base}/shared`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${token}` }
	});
}
