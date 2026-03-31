import { WEBUI_BASE_URL, WEBUI_API_BASE_URL } from '$lib/constants';

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

let pickerApiLoaded = false;
let initialized = false;

// ── Picker API loading (unchanged) ──────────────────────────────────

export const loadGoogleDriveApi = () => {
	return new Promise((resolve, reject) => {
		if (typeof gapi === 'undefined') {
			const script = document.createElement('script');
			script.src = 'https://apis.google.com/js/api.js';
			script.onload = () => {
				gapi.load('picker', () => {
					pickerApiLoaded = true;
					resolve(true);
				});
			};
			script.onerror = reject;
			document.body.appendChild(script);
		} else {
			gapi.load('picker', () => {
				pickerApiLoaded = true;
				resolve(true);
			});
		}
	});
};

export const initialize = async () => {
	if (!initialized) {
		await getCredentials();
		validateCredentials();
		await loadGoogleDriveApi();
		initialized = true;
	}
};

// ── Backend-proxied token management ────────────────────────────────

/**
 * Get a valid access token from the backend (auto-refreshed).
 * Returns null if no stored token exists (user must authorize).
 */
async function fetchBackendAccessToken(): Promise<string | null> {
	const res = await fetch(`${WEBUI_API_BASE_URL}/google-drive/auth/access-token`, {
		headers: { Authorization: `Bearer ${localStorage.token}` }
	});
	if (res.status === 401) return null;
	if (!res.ok) throw new Error('Failed to get Google Drive access token');
	const data = await res.json();
	return data.access_token;
}

/**
 * Open the backend OAuth popup and wait for it to complete.
 * Reuses the same popup/postMessage pattern as KnowledgeBase.svelte's authorizeBackgroundSync.
 */
function triggerAuthPopup(knowledgeId?: string): Promise<void> {
	const url = knowledgeId
		? `${WEBUI_API_BASE_URL}/google-drive/auth/initiate?knowledge_id=${knowledgeId}`
		: `${WEBUI_API_BASE_URL}/google-drive/auth/initiate`;

	return new Promise<void>((resolve, reject) => {
		const popup = window.open(url, 'google_drive_auth', 'width=600,height=700,scrollbars=yes');
		let messageReceived = false;

		const handleMessage = (event: MessageEvent) => {
			if (event.data?.type !== 'google_drive_auth_callback') return;
			messageReceived = true;
			window.removeEventListener('message', handleMessage);
			if (event.data.success) {
				resolve();
			} else {
				reject(new Error(event.data.error || 'Google Drive authorization failed'));
			}
		};

		window.addEventListener('message', handleMessage);

		// Fallback: check when popup closes without postMessage
		const check = setInterval(() => {
			if (popup?.closed) {
				clearInterval(check);
				if (!messageReceived) {
					window.removeEventListener('message', handleMessage);
					resolve(); // Will verify token after
				}
			}
		}, 500);
	});
}

/**
 * Ensure the user has authorized Google Drive.
 * If no stored token, opens an OAuth popup for consent.
 * Returns a valid access token.
 */
export const getAuthToken = async (knowledgeId?: string): Promise<string> => {
	let token = await fetchBackendAccessToken();
	if (token) return token;

	// No stored token — trigger OAuth popup
	await triggerAuthPopup(knowledgeId);

	// After popup closes, fetch the now-stored token
	token = await fetchBackendAccessToken();
	if (!token) throw new Error('Google Drive authorization failed or was cancelled');
	return token;
};

export const clearGoogleDriveToken = () => {
	// No-op: tokens are now managed server-side.
};

// ── Picker functions (auth replaced, picker UI unchanged) ───────────

export interface KnowledgePickerItem {
	type: 'file' | 'folder';
	id: string;
	name: string;
	path: string;
	mimeType: string;
}

export interface KnowledgePickerResult {
	items: KnowledgePickerItem[];
	accessToken: string;
}

export const createKnowledgePicker = (knowledgeId?: string): Promise<KnowledgePickerResult | null> => {
	return new Promise(async (resolve, reject) => {
		try {
			await initialize();
			const token = await getAuthToken(knowledgeId);

			const docsView = new google.picker.DocsView()
				.setIncludeFolders(true)
				.setSelectFolderEnabled(true)
				.setParent('root');

			const picker = new google.picker.PickerBuilder()
				.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
				.addView(docsView)
				.setOAuthToken(token)
				.setDeveloperKey(API_KEY)
				.setCallback((data: any) => {
					if (data[google.picker.Response.ACTION] === google.picker.Action.PICKED) {
						const docs = data[google.picker.Response.DOCUMENTS];
						const items: KnowledgePickerItem[] = docs.map((doc: any) => {
							const mimeType = doc[google.picker.Document.MIME_TYPE];
							return {
								type: mimeType === 'application/vnd.google-apps.folder' ? 'folder' : 'file',
								id: doc[google.picker.Document.ID],
								name: doc[google.picker.Document.NAME],
								path: doc[google.picker.Document.URL] || '',
								mimeType
							};
						});
						resolve({ items, accessToken: token });
					} else if (data[google.picker.Response.ACTION] === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			console.error('Google Drive Knowledge Picker error:', error);
			reject(error);
		}
	});
};

export const createPicker = () => {
	return new Promise(async (resolve, reject) => {
		try {
			await initialize();
			const token = await getAuthToken();

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
				.setCallback(async (data: any) => {
					if (data[google.picker.Response.ACTION] === google.picker.Action.PICKED) {
						try {
							const doc = data[google.picker.Response.DOCUMENTS][0];
							const fileId = doc[google.picker.Document.ID];
							const fileName = doc[google.picker.Document.NAME];
							const mimeType = doc[google.picker.Document.MIME_TYPE];

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

							const response = await fetch(downloadUrl, {
								headers: { Authorization: `Bearer ${token}`, Accept: '*/*' }
							});

							if (!response.ok) {
								const errorText = await response.text();
								throw new Error(`Failed to download file (${response.status}): ${errorText}`);
							}

							const blob = await response.blob();
							resolve({
								id: fileId,
								name: effectiveName,
								url: downloadUrl,
								blob: blob,
								headers: { Authorization: `Bearer ${token}`, Accept: '*/*' }
							});
						} catch (error) {
							reject(error);
						}
					} else if (data[google.picker.Response.ACTION] === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			console.error('Google Drive Picker error:', error);
			reject(error);
		}
	});
};
