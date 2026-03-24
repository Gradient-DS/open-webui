import { createSyncApi } from '$lib/apis/sync';

export type {
	SyncStatusResponse,
	FailedFile,
	TokenStatusResponse,
	SyncErrorType
} from '$lib/apis/sync';

// Provider-specific types (Google Drive has no drive_id)
export interface SyncItem {
	type: 'file' | 'folder';
	item_id: string;
	item_path: string;
	name: string;
}

export interface SyncItemsRequest {
	knowledge_id: string;
	items: SyncItem[];
	access_token: string;
}

// Create API instance with Google Drive base path
const api = createSyncApi('google-drive');

// Re-export with original function names for backward compatibility
export const startGoogleDriveSyncItems = api.startSyncItems;
export const getSyncStatus = api.getSyncStatus;
export const cancelSync = api.cancelSync;
export const getSyncedCollections = api.getSyncedCollections;
export const getTokenStatus = api.getTokenStatus;
export const removeSource = api.removeSource;
export const revokeToken = api.revokeToken;
