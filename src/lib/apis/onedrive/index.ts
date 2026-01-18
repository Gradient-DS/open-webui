import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface SyncFolderRequest {
	knowledge_id: string;
	drive_id: string;
	folder_id: string;
	folder_path: string;
	access_token: string;
	user_token: string;
}

export interface SyncStatusResponse {
	knowledge_id: string;
	status: 'idle' | 'syncing' | 'completed' | 'failed';
	progress_current?: number;
	progress_total?: number;
	last_sync_at?: number;
	error?: string;
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
