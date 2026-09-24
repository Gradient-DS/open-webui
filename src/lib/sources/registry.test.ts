import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
	providers,
	DEFAULT_RECONNECT_CODES,
	reconnectCodes,
	providerFor,
	providerIcon,
	localSource,
	oneDriveScope,
	googleDriveScope
} from './registry';
import { openOneDriveItemPicker } from '$lib/utils/onedrive-file-picker';
import {
	createKnowledgePicker,
	initialize as initializeGooglePicker
} from '$lib/utils/google-drive-picker';

vi.mock('$lib/utils/onedrive-file-picker', () => ({ openOneDriveItemPicker: vi.fn() }));
vi.mock('$lib/utils/google-drive-picker', () => ({
	createKnowledgePicker: vi.fn(),
	initialize: vi.fn()
}));
beforeEach(() => vi.resetAllMocks());

it('registers OneDrive folders recursively and files as single-file scopes', () => {
	const folder = {
		id: 'folder',
		driveId: 'drive',
		type: 'folder' as const,
		name: 'Reports',
		path: '/Team/Reports'
	};
	expect(oneDriveScope(folder)).toEqual({
		label: 'Reports',
		path: '/Team/Reports',
		scope: { drive_id: 'drive', item_id: 'folder', include_descendants: true, single_file: false }
	});
	expect(
		oneDriveScope({
			...folder,
			id: 'file',
			type: 'file',
			name: 'Report.pdf',
			path: '/Team/Report.pdf'
		})
	).toEqual({
		label: 'Report.pdf',
		path: '/Team/Report.pdf',
		scope: { drive_id: 'drive', item_id: 'file', include_descendants: false, single_file: true }
	});
});

it('registers Google folders and files without carrying picker credentials', () => {
	const folder = {
		id: 'folder',
		type: 'folder' as const,
		name: 'Reports',
		path: '/Reports',
		token: 'not-forwarded'
	};
	expect(googleDriveScope(folder)).toEqual({
		label: 'Reports',
		path: '/Reports',
		scope: { file_id: 'folder', drive_id: null, include_descendants: true }
	});
	expect(googleDriveScope({ ...folder, type: 'file' }).scope.include_descendants).toBe(false);
});

describe('provider registry', () => {
	it.each(Object.entries(providers))('defines the complete %s adapter', (kind, provider) => {
		expect(provider.kind).toBe(kind);
		expect(provider.label.trim()).not.toBe('');
		expect(provider.icon).toBeTruthy();
		expect(provider.startParam).toBe(`start_${kind}_sync`);
		expect(provider.pick).toBeTypeOf('function');
		expect(provider.warmUp === undefined || typeof provider.warmUp === 'function').toBe(true);
		expect(provider.needsReconnectOn.length).toBeGreaterThan(0);
		expect(reconnectCodes(kind)).toBe(provider.needsReconnectOn);
		expect(providerFor(kind)).toBe(provider);
		expect(providerIcon(kind)).toBe(provider.icon);
	});
	it.each([null, undefined, 'local', 'unknown', 'toString'])('falls back for %s', (kind) => {
		expect(reconnectCodes(kind)).toBe(DEFAULT_RECONNECT_CODES);
		expect(providerFor(kind)).toBeNull();
		expect(providerIcon(kind)).toBe(localSource.icon);
	});
});

describe('vendor adapters', () => {
	it('invokes the OneDrive picker without an import delay', async () => {
		vi.mocked(openOneDriveItemPicker).mockResolvedValue([]);
		const picked = providers.onedrive.pick();
		expect(openOneDriveItemPicker).toHaveBeenCalledExactlyOnceWith('organizations');
		await expect(picked).resolves.toEqual([]);
	});
	it('invokes the Google picker without an import delay', async () => {
		vi.mocked(createKnowledgePicker).mockResolvedValue(null);
		const picked = providers.google_drive.pick();
		expect(createKnowledgePicker).toHaveBeenCalledExactlyOnceWith();
		await expect(picked).resolves.toBeNull();
	});
	it('preloads Google without opening a picker', async () => {
		vi.mocked(initializeGooglePicker).mockResolvedValue(undefined);
		await expect(providers.google_drive.warmUp!()).resolves.toBeUndefined();
		expect(initializeGooglePicker).toHaveBeenCalledExactlyOnceWith();
		expect(createKnowledgePicker).not.toHaveBeenCalled();
	});
	it('ignores Google preload failures', async () => {
		vi.mocked(initializeGooglePicker).mockRejectedValue(new Error('offline'));
		await expect(providers.google_drive.warmUp!()).resolves.toBeUndefined();
	});
});
