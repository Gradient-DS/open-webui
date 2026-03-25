import { WEBUI_API_BASE_URL } from '$lib/constants';

// ──────────────────────────────────────────────────────────────────────
// Shared types
// ──────────────────────────────────────────────────────────────────────

export type SyncErrorType = 'timeout' | 'empty_content' | 'processing_error' | 'download_error';

export interface FailedFile {
	filename: string;
	error_type: SyncErrorType;
	error_message: string;
}

export interface SyncStatusResponse {
	knowledge_id: string;
	status: 'idle' | 'syncing' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled';
	progress_current?: number;
	progress_total?: number;
	last_sync_at?: number;
	error?: string;
	source_count?: number;
	failed_files?: FailedFile[];
}

export interface TokenStatusResponse {
	has_token: boolean;
	is_expired?: boolean;
	needs_reauth?: boolean;
	token_stored_at?: number;
}

// ──────────────────────────────────────────────────────────────────────
// Generic fetch helper
// ──────────────────────────────────────────────────────────────────────

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
	const res = await fetch(url, init);
	if (!res.ok) {
		const error = await res.json().catch(() => ({ detail: res.statusText }));
		throw new Error(error.detail || `Request failed: ${res.status}`);
	}
	return res.json();
}

// ──────────────────────────────────────────────────────────────────────
// Sync API factory
// ──────────────────────────────────────────────────────────────────────

export function createSyncApi(basePath: string) {
	const base = `${WEBUI_API_BASE_URL}/${basePath}`;

	return {
		startSyncItems(
			token: string,
			request: Record<string, unknown>
		): Promise<{ message: string; knowledge_id: string }> {
			return apiFetch(`${base}/sync/items`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${token}`
				},
				body: JSON.stringify(request)
			});
		},

		getSyncStatus(token: string, knowledgeId: string): Promise<SyncStatusResponse> {
			return apiFetch(`${base}/sync/${knowledgeId}`, {
				headers: { Authorization: `Bearer ${token}` }
			});
		},

		cancelSync(
			token: string,
			knowledgeId: string
		): Promise<{ message: string; knowledge_id: string }> {
			return apiFetch(`${base}/sync/${knowledgeId}/cancel`, {
				method: 'POST',
				headers: { Authorization: `Bearer ${token}` }
			});
		},

		getSyncedCollections(
			token: string
		): Promise<Array<{ id: string; name: string; sync_info: Record<string, unknown> }>> {
			return apiFetch(`${base}/synced-collections`, {
				headers: { Authorization: `Bearer ${token}` }
			});
		},

		getTokenStatus(token: string, knowledgeId: string): Promise<TokenStatusResponse> {
			return apiFetch(`${base}/auth/token-status/${knowledgeId}`, {
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${token}`
				}
			});
		},

		removeSource(
			token: string,
			knowledgeId: string,
			itemId: string
		): Promise<{ message: string; source_name: string; files_removed: number }> {
			return apiFetch(`${base}/sync/${knowledgeId}/sources/remove`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${token}`
				},
				body: JSON.stringify({ item_id: itemId })
			});
		},

		getAccessToken(token: string): Promise<{ access_token: string }> {
			return apiFetch(`${base}/auth/access-token`, {
				headers: { Authorization: `Bearer ${token}` }
			});
		},

		revokeToken(token: string, knowledgeId: string): Promise<{ revoked: boolean }> {
			return apiFetch(`${base}/auth/revoke/${knowledgeId}`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${token}`
				}
			});
		}
	};
}
