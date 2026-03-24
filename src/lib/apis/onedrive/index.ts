import { createSyncApi } from '$lib/apis/sync';

export type {
	SyncStatusResponse,
	FailedFile,
	TokenStatusResponse,
	SyncErrorType
} from '$lib/apis/sync';

// Provider-specific types (OneDrive includes drive_id)
export interface SyncFolderRequest {
	knowledge_id: string;
	drive_id: string;
	folder_id: string;
	folder_path: string;
	access_token: string;
	user_token: string;
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

// Create API instance with OneDrive base path
const api = createSyncApi('onedrive');

// Re-export with original function names for backward compatibility
export const startOneDriveSyncItems = api.startSyncItems;
export const getSyncStatus = api.getSyncStatus;
export const cancelSync = api.cancelSync;
export const getSyncedCollections = api.getSyncedCollections;
export const getTokenStatus = api.getTokenStatus;
export const removeSource = api.removeSource;
export const revokeToken = api.revokeToken;

// Legacy single-folder sync (OneDrive-specific, kept for backward compatibility)
export async function startOneDriveSync(
	token: string,
	request: SyncFolderRequest
): Promise<{ message: string; knowledge_id: string }> {
	const { WEBUI_API_BASE_URL } = await import('$lib/constants');
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
