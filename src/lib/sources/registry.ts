import type { ComponentType } from 'svelte';
import type { ScheduleForm } from '$lib/apis/cloudSync';
import OneDrive from '$lib/components/icons/OneDrive.svelte';
import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
import Folder from '$lib/components/icons/Folder.svelte';
import { openOneDriveItemPicker } from '$lib/utils/onedrive-file-picker';
import {
	createKnowledgePicker,
	initialize as initializeGooglePicker
} from '$lib/utils/google-drive-picker';

export type PickedScope = Pick<ScheduleForm, 'scope' | 'label' | 'path'>;

export interface SourceProvider {
	kind: string;
	label: string;
	icon: ComponentType;
	startParam: string;
	pick(): Promise<PickedScope[] | null>;
	warmUp?(): Promise<void>;
	needsReconnectOn: string[];
}

// Vendor adapters keep browser consent inside their existing pickers. A future
// soevBrowser adapter can implement the same pick() contract using a soev-api
// listing endpoint, returning scopes without changing any knowledge components.
export const DEFAULT_RECONNECT_CODES = [
	'suspended:reauth',
	'pending',
	'revoked',
	'access_revoked',
	'credential_unusable',
	'owner_mismatch'
];

export const providers: Record<string, SourceProvider> = {
	onedrive: {
		kind: 'onedrive',
		label: 'OneDrive',
		icon: OneDrive,
		startParam: 'start_onedrive_sync',
		needsReconnectOn: DEFAULT_RECONNECT_CODES,
		async pick() {
			const items = await openOneDriveItemPicker('organizations');
			return items?.map(oneDriveScope) ?? null;
		}
	},
	google_drive: {
		kind: 'google_drive',
		label: 'Google Drive',
		icon: GoogleDrive,
		startParam: 'start_google_drive_sync',
		warmUp: () => initializeGooglePicker().catch(() => {}),
		needsReconnectOn: DEFAULT_RECONNECT_CODES,
		async pick() {
			const result = await createKnowledgePicker();
			return result?.items.map(googleDriveScope) ?? null;
		}
	}
};

export const localSource: { kind: 'local'; label: string; icon: ComponentType } = {
	kind: 'local',
	label: 'Local',
	icon: Folder
};

export function providerFor(kind: string | null | undefined): SourceProvider | null {
	return kind && Object.hasOwn(providers, kind) ? providers[kind] : null;
}

export function reconnectCodes(kind: string | null | undefined): string[] {
	return providerFor(kind)?.needsReconnectOn ?? DEFAULT_RECONNECT_CODES;
}

export function providerIcon(kind: string | null | undefined): ComponentType {
	return providerFor(kind)?.icon ?? localSource.icon;
}

export function oneDriveScope(item: {
	id: string;
	driveId: string;
	type: 'file' | 'folder';
	name: string;
	path: string;
}): Pick<ScheduleForm, 'scope' | 'label' | 'path'> {
	return {
		label: item.name,
		path: item.path,
		scope: {
			drive_id: item.driveId,
			item_id: item.id,
			include_descendants: item.type === 'folder',
			single_file: item.type === 'file'
		}
	};
}

export function googleDriveScope(item: {
	id: string;
	type: 'file' | 'folder';
	name: string;
	path: string;
}): Pick<ScheduleForm, 'scope' | 'label' | 'path'> {
	return {
		label: item.name,
		path: item.path,
		scope: { file_id: item.id, drive_id: null, include_descendants: item.type === 'folder' }
	};
}
