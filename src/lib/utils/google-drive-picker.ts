import { WEBUI_BASE_URL } from '$lib/constants';

// Google Drive Picker API configuration
let API_KEY = '';
let CLIENT_ID = '';

// Function to fetch credentials from backend config (still needed for Picker API key)
async function getCredentials() {
	const response = await fetch(`${WEBUI_BASE_URL}/api/config`, {
		headers: { 'Content-Type': 'application/json' },
		credentials: 'include'
	});
	if (!response.ok) throw new Error('Failed to fetch Google Drive credentials');
	const config = await response.json();
	API_KEY = config.google_drive?.api_key;
	CLIENT_ID = config.google_drive?.client_id;
	if (!API_KEY || !CLIENT_ID) throw new Error('Google Drive API credentials not configured');
}

const validateCredentials = () => {
	if (!API_KEY || !CLIENT_ID || API_KEY === '' || CLIENT_ID === '') {
		throw new Error('Google Drive API credentials not configured');
	}
};

interface PickerDocument {
	id: string;
	name: string;
	url?: string;
	mimeType: string;
}
interface PickerData {
	action: string;
	docs?: PickerDocument[];
}
interface DocsView {
	setIncludeFolders(value: boolean): DocsView;
	setSelectFolderEnabled(value: boolean): DocsView;
	setParent(value: string): DocsView;
}
interface PickerBuilder {
	enableFeature(value: string): PickerBuilder;
	addView(view: DocsView): PickerBuilder;
	setOAuthToken(token: string): PickerBuilder;
	setDeveloperKey(key: string): PickerBuilder;
	setCallback(callback: (data: PickerData) => void): PickerBuilder;
	build(): { setVisible(visible: boolean): void };
}
interface TokenResponse {
	access_token?: string;
	error?: string;
	scope?: string;
}
declare const google: {
	picker: {
		DocsView: new () => DocsView;
		PickerBuilder: new () => PickerBuilder;
		Feature: { MULTISELECT_ENABLED: string };
		Action: { PICKED: string; CANCEL: string };
	};
	accounts: {
		oauth2: {
			initTokenClient(options: {
				client_id: string;
				scope: string;
				callback: (response: TokenResponse) => void;
				error_callback: () => void;
			}): { requestAccessToken(): void };
			hasGrantedAllScopes(response: TokenResponse, scope: string): boolean;
		};
	};
};
declare const gapi: { load(name: string, callback: () => void): void };

let initialized = false;
let initialization: Promise<void> | undefined;

function loadScript(src: string): Promise<void> {
	return new Promise((resolve, reject) => {
		const script = document.createElement('script');
		script.src = src;
		script.onload = () => resolve();
		script.onerror = () => {
			script.remove();
			reject(new Error('Google Drive picker could not load'));
		};
		document.body.appendChild(script);
	});
}

export const loadGoogleDriveApi = async () => {
	if (typeof gapi === 'undefined') await loadScript('https://apis.google.com/js/api.js');
	await new Promise<void>((resolve) => gapi.load('picker', resolve));
};

export const initialize = (): Promise<void> => {
	if (!initialization) {
		initialization = (async () => {
			await getCredentials();
			validateCredentials();
			await Promise.all([
				loadGoogleDriveApi(),
				typeof google !== 'undefined' && google.accounts?.oauth2
					? Promise.resolve()
					: loadScript('https://accounts.google.com/gsi/client')
			]);
			initialized = true;
		})().catch((error) => {
			initialization = undefined;
			throw error;
		});
	}
	return initialization;
};

// Picker consent is browser-only; sync credentials remain with soev-sync.
export const getAuthToken = (): Promise<string> => {
	if (!initialized) return initialize().then(() => getAuthToken());
	return new Promise((resolve, reject) => {
		const scope = 'https://www.googleapis.com/auth/drive.readonly';
		const timeout = setTimeout(
			() => reject(new Error('Google Drive authorization timed out')),
			120000
		);
		const fail = () => {
			clearTimeout(timeout);
			reject(new Error('Google Drive authorization failed or was cancelled'));
		};
		const client = google.accounts.oauth2.initTokenClient({
			client_id: CLIENT_ID,
			scope,
			callback: (response) => {
				clearTimeout(timeout);
				if (
					response.error ||
					!response.access_token ||
					!google.accounts.oauth2.hasGrantedAllScopes(response, scope)
				)
					return fail();
				resolve(response.access_token);
			},
			error_callback: fail
		});
		client.requestAccessToken();
	});
};

export interface KnowledgePickerItem {
	type: 'file' | 'folder';
	id: string;
	name: string;
	path: string;
	mimeType: string;
}

export interface KnowledgePickerResult {
	items: KnowledgePickerItem[];
}

export const createKnowledgePicker = async (): Promise<KnowledgePickerResult | null> => {
	const token = await getAuthToken();
	return new Promise((resolve, reject) => {
		try {
			const docsView = new google.picker.DocsView()
				.setIncludeFolders(true)
				.setSelectFolderEnabled(true)
				.setParent('root');

			const picker = new google.picker.PickerBuilder()
				.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
				.addView(docsView)
				.setOAuthToken(token)
				.setDeveloperKey(API_KEY)
				.setCallback((data: PickerData) => {
					if (data.action === google.picker.Action.PICKED) {
						const docs = data.docs ?? [];
						const items: KnowledgePickerItem[] = docs.map((doc: PickerDocument) => {
							const mimeType = doc.mimeType;
							return {
								type: mimeType === 'application/vnd.google-apps.folder' ? 'folder' : 'file',
								id: doc.id,
								name: doc.name,
								path: doc.url || '',
								mimeType
							};
						});
						resolve({ items });
					} else if (data.action === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			reject(error);
		}
	});
};

interface PickerOptions {
	onFileSelected?: (metadata: { name: string }) => void;
}

export const createPicker = async (
	options?: PickerOptions
): Promise<{ id: string; name: string; url: string; blob: Blob } | null> => {
	const token = await getAuthToken();
	return new Promise((resolve, reject) => {
		try {
			const picker = new google.picker.PickerBuilder()
				.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
				.addView(
					new google.picker.DocsView()
						.setIncludeFolders(true)
						.setSelectFolderEnabled(false)
						.setParent('root')
				)
				.setOAuthToken(token)
				.setDeveloperKey(API_KEY)
				.setCallback(async (data: PickerData) => {
					if (data.action === google.picker.Action.PICKED) {
						try {
							const doc = (data.docs ?? [])[0];
							const fileId = doc.id;
							const fileName = doc.name;
							const mimeType = doc.mimeType;

							if (!fileId || !fileName) throw new Error('Required file details missing');

							let downloadUrl;
							let effectiveName = fileName;
							if (mimeType.includes('google-apps')) {
								let exportFormat: string;
								let exportExt: string;
								if (mimeType.includes('document')) {
									exportFormat = 'text/plain';
									exportExt = '.txt';
								} else if (mimeType.includes('spreadsheet')) {
									exportFormat = 'text/csv';
									exportExt = '.csv';
								} else if (mimeType.includes('presentation')) {
									exportFormat = 'text/plain';
									exportExt = '.txt';
								} else {
									exportFormat = 'application/pdf';
									exportExt = '.pdf';
								}
								if (!fileName.toLowerCase().endsWith(exportExt)) {
									effectiveName = `${fileName}${exportExt}`;
								}
								downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=${encodeURIComponent(exportFormat)}`;
							} else {
								downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`;
							}

							// Notify before download starts (for instant placeholder feedback)
							options?.onFileSelected?.({ name: effectiveName });

							const response = await fetch(downloadUrl, {
								headers: { Authorization: `Bearer ${token}`, Accept: '*/*' }
							});

							if (!response.ok) {
								throw new Error(`Failed to download file (${response.status})`);
							}

							const blob = await response.blob();
							resolve({
								id: fileId,
								name: effectiveName,
								url: downloadUrl,
								blob: blob
							});
						} catch (error) {
							reject(error);
						}
					} else if (data.action === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			reject(error);
		}
	});
};
