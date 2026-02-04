import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface SyncFolderRequest {
	knowledge_id: string;
	drive_id: string;
	folder_id: string;
	folder_path: string;
	access_token: string;
	user_token: string;
}

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

export interface SyncItem {
	type: 'file' | 'folder';
	drive_id: string;
	item_id: string;
	item_path: string;
	name: string;
}

export interface SyncItemsRequest {
	knowledge_id: string;
	items: SyncItem[];
	access_token: string;
	user_token: string;
}

export async function startOneDriveSync(
	token: string,
	request: SyncFolderRequest
): Promise<{ message: string; knowledge_id: string }> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/sync`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(request)
	});

	if (!res.ok) {
		const error = await res.json();
		throw new Error(error.detail || 'Failed to start sync');
	}

	return res.json();
}

export async function startOneDriveSyncItems(
	token: string,
	request: SyncItemsRequest
): Promise<{ message: string; knowledge_id: string }> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/sync/items`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(request)
	});

	if (!res.ok) {
		const error = await res.json();
		throw new Error(error.detail || 'Failed to start OneDrive sync');
	}

	return res.json();
}

export async function getSyncStatus(
	token: string,
	knowledgeId: string
): Promise<SyncStatusResponse> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/sync/${knowledgeId}`, {
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const error = await res.json();
		throw new Error(error.detail || 'Failed to get sync status');
	}

	return res.json();
}

export async function cancelSync(
	token: string,
	knowledgeId: string
): Promise<{ message: string; knowledge_id: string }> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/sync/${knowledgeId}/cancel`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const error = await res.json();
		throw new Error(error.detail || 'Failed to cancel sync');
	}

	return res.json();
}

export async function getSyncedCollections(
	token: string
): Promise<Array<{ id: string; name: string; sync_info: Record<string, unknown> }>> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/synced-collections`, {
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const error = await res.json();
		throw new Error(error.detail || 'Failed to get synced collections');
	}

	return res.json();
}

// ──────────────────────────────────────────────────────────────────────
// Background Sync OAuth API
// ──────────────────────────────────────────────────────────────────────

export interface TokenStatusResponse {
	has_token: boolean;
	is_expired?: boolean;
	needs_reauth?: boolean;
	token_stored_at?: number;
}

export async function getTokenStatus(
	token: string,
	knowledgeId: string
): Promise<TokenStatusResponse> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/auth/token-status/${knowledgeId}`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});
	if (!res.ok) throw new Error(await res.text());
	return res.json();
}

export async function revokeToken(
	token: string,
	knowledgeId: string
): Promise<{ revoked: boolean }> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/onedrive/auth/revoke/${knowledgeId}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});
	if (!res.ok) throw new Error(await res.text());
	return res.json();
}
