import type { PopupRequest, PublicClientApplication } from '@azure/msal-browser';
import { v4 as uuidv4 } from 'uuid';
import { WEBUI_BASE_URL } from '$lib/constants';

class OneDriveConfig {
	private static instance: OneDriveConfig;
	private clientIdPersonal: string = '';
	private clientIdBusiness: string = '';
	private sharepointUrl: string = '';
	private sharepointTenantId: string = '';
	private msalInstance: PublicClientApplication | null = null;
	private currentAuthorityType: 'personal' | 'organizations' = 'personal';

	private constructor() {}

	public static getInstance(): OneDriveConfig {
		if (!OneDriveConfig.instance) {
			OneDriveConfig.instance = new OneDriveConfig();
		}
		return OneDriveConfig.instance;
	}

	public async initialize(authorityType?: 'personal' | 'organizations'): Promise<void> {
		if (authorityType && this.currentAuthorityType !== authorityType) {
			this.currentAuthorityType = authorityType;
			this.msalInstance = null;
		}
		await this.getCredentials();
	}

	public async ensureInitialized(authorityType?: 'personal' | 'organizations'): Promise<void> {
		await this.initialize(authorityType);
	}

	private async getCredentials(): Promise<void> {
		const response = await fetch(`${WEBUI_BASE_URL}/api/config`, {
			headers: {
				'Content-Type': 'application/json'
			},
			credentials: 'include'
		});

		if (!response.ok) {
			throw new Error('Failed to fetch OneDrive credentials');
		}

		const config = await response.json();

		this.clientIdPersonal = config.onedrive?.client_id_personal;
		this.clientIdBusiness = config.onedrive?.client_id_business;
		this.sharepointUrl = config.onedrive?.sharepoint_url;
		this.sharepointTenantId = config.onedrive?.sharepoint_tenant_id;

		if (!this.clientIdPersonal && !this.clientIdBusiness) {
			throw new Error('OneDrive personal or business client ID not configured');
		}
	}

	public async getMsalInstance(
		authorityType?: 'personal' | 'organizations'
	): Promise<PublicClientApplication> {
		await this.ensureInitialized(authorityType);

		if (!this.msalInstance) {
			const authorityEndpoint =
				this.currentAuthorityType === 'organizations'
					? this.sharepointTenantId || 'common'
					: 'consumers';

			const clientId =
				this.currentAuthorityType === 'organizations'
					? this.clientIdBusiness
					: this.clientIdPersonal;

			if (!clientId) {
				throw new Error('OneDrive client ID not configured');
			}

			const msalParams = {
				auth: {
					authority: `https://login.microsoftonline.com/${authorityEndpoint}`,
					clientId: clientId,
					redirectUri: window.location.origin
				}
			};

			const { PublicClientApplication } = await import('@azure/msal-browser');
			this.msalInstance = new PublicClientApplication(msalParams);
			if (this.msalInstance.initialize) {
				await this.msalInstance.initialize();
			}
		}

		return this.msalInstance;
	}

	public getAuthorityType(): 'personal' | 'organizations' {
		return this.currentAuthorityType;
	}

	public getSharepointUrl(): string {
		return this.sharepointUrl;
	}

	public getSharepointTenantId(): string {
		return this.sharepointTenantId;
	}

	public getBaseUrl(): string {
		if (this.currentAuthorityType === 'organizations') {
			if (!this.sharepointUrl || this.sharepointUrl === '') {
				throw new Error('Sharepoint URL not configured');
			}

			let sharePointBaseUrl = this.sharepointUrl.replace(/^https?:\/\//, '');
			sharePointBaseUrl = sharePointBaseUrl.replace(/\/$/, '');

			return `https://${sharePointBaseUrl}`;
		} else {
			return 'https://onedrive.live.com/picker';
		}
	}
}

// Retrieve OneDrive access token
async function getToken(
	resource?: string,
	authorityType?: 'personal' | 'organizations'
): Promise<string> {
	const config = OneDriveConfig.getInstance();
	await config.ensureInitialized(authorityType);

	const currentAuthorityType = config.getAuthorityType();

	const scopes =
		currentAuthorityType === 'organizations'
			? [`${resource || config.getBaseUrl()}/.default`]
			: ['OneDrive.ReadWrite'];

	const authParams: PopupRequest = { scopes };
	let accessToken = '';

	try {
		const msalInstance = await config.getMsalInstance(authorityType);
		const resp = await msalInstance.acquireTokenSilent(authParams);
		accessToken = resp.accessToken;
	} catch {
		const msalInstance = await config.getMsalInstance(authorityType);
		try {
			const resp = await msalInstance.loginPopup(authParams);
			msalInstance.setActiveAccount(resp.account);
			if (resp.idToken) {
				const resp2 = await msalInstance.acquireTokenSilent(authParams);
				accessToken = resp2.accessToken;
			}
		} catch (popupError) {
			throw new Error(
				'Failed to login: ' +
					(popupError instanceof Error ? popupError.message : String(popupError))
			);
		}
	}

	if (!accessToken) {
		throw new Error('Failed to acquire access token');
	}

	return accessToken;
}

// Silent-only token acquisition (for use within iframe contexts where popups are blocked)
async function getTokenSilent(
	resource?: string,
	authorityType?: 'personal' | 'organizations'
): Promise<string | null> {
	const config = OneDriveConfig.getInstance();
	await config.ensureInitialized(authorityType);

	const currentAuthorityType = config.getAuthorityType();

	const scopes =
		currentAuthorityType === 'organizations'
			? [`${resource || config.getBaseUrl()}/.default`]
			: ['OneDrive.ReadWrite'];

	const authParams: PopupRequest = { scopes };

	try {
		const msalInstance = await config.getMsalInstance(authorityType);
		const resp = await msalInstance.acquireTokenSilent(authParams);
		return resp.accessToken;
	} catch {
		// Silent acquisition failed - don't try popup in iframe context
		return null;
	}
}

interface PickerParams {
	sdk: string;
	entry: {
		oneDrive: Record<string, unknown>;
	};
	authentication: Record<string, unknown>;
	messaging: {
		origin: string;
		channelId: string;
	};
	search: {
		enabled: boolean;
	};
	selection?: {
		mode: 'single' | 'multiple' | 'pick';
		enablePersistence?: boolean;
		maximumCount?: number;
	};
	typesAndSources: {
		mode: string;
		pivots: Record<string, boolean>;
	};
}

interface PickerResult {
	command?: string;
	items?: OneDriveFileInfo[];
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	[key: string]: any;
}

export interface FolderPickerResult {
	id: string;
	name: string;
	driveId: string;
	path: string;
	webUrl: string;
}

export interface ItemPickerResult {
	type: 'file' | 'folder';
	id: string;
	name: string;
	driveId: string;
	path: string;
	webUrl: string;
}

export type MultiItemPickerResult = ItemPickerResult[];

// Get picker parameters based on account type
function getPickerParams(): PickerParams {
	const channelId = uuidv4();
	const config = OneDriveConfig.getInstance();

	const params: PickerParams = {
		sdk: '8.0',
		entry: {
			oneDrive: {}
		},
		authentication: {},
		messaging: {
			origin: window?.location?.origin || '',
			channelId
		},
		search: {
			enabled: true
		},
		typesAndSources: {
			mode: 'files',
			pivots: {
				oneDrive: true,
				recent: true,
				myOrganization: config.getAuthorityType() === 'organizations'
			}
		}
	};

	// For personal accounts, set files object in oneDrive
	if (config.getAuthorityType() !== 'organizations') {
		params.entry.oneDrive = { files: {} };
	}

	return params;
}

// Get folder picker parameters for folder selection mode
function getFolderPickerParams(channelId: string): PickerParams {
	const config = OneDriveConfig.getInstance();

	const params: PickerParams = {
		sdk: '8.0',
		entry: {
			oneDrive: {}
		},
		authentication: {},
		messaging: {
			origin: window?.location?.origin || '',
			channelId
		},
		search: {
			enabled: true
		},
		typesAndSources: {
			mode: 'folders', // Changed from 'files'
			pivots: {
				oneDrive: true,
				recent: false, // Folders don't have recent
				myOrganization: config.getAuthorityType() === 'organizations'
			}
		}
	};

	// For personal accounts, set folders object in oneDrive
	if (config.getAuthorityType() === 'personal') {
		params.entry.oneDrive = { folders: {} };
	}

	return params;
}

// Get item picker parameters for multi-select files and folders
function getItemPickerParams(channelId: string): PickerParams {
	const config = OneDriveConfig.getInstance();

	const params: PickerParams = {
		sdk: '8.0',
		entry: {
			oneDrive: {}
		},
		authentication: {},
		messaging: {
			origin: window?.location?.origin || '',
			channelId
		},
		search: {
			enabled: true
		},
		selection: {
			mode: 'multiple',
			enablePersistence: true
		},
		typesAndSources: {
			mode: 'all',
			pivots: {
				oneDrive: true,
				recent: true,
				myOrganization: config.getAuthorityType() === 'organizations'
			}
		}
	};

	if (config.getAuthorityType() === 'personal') {
		params.entry.oneDrive = {};
	}

	return params;
}

interface OneDriveFileInfo {
	id: string;
	name: string;
	parentReference?: {
		driveId?: string;
		path?: string;
	};
	folder?: object;
	webUrl?: string;
	'@sharePoint.endpoint'?: string;
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	[key: string]: any;
}

// Download file from OneDrive
async function downloadOneDriveFile(
	fileInfo: OneDriveFileInfo,
	authorityType?: 'personal' | 'organizations'
): Promise<Blob> {
	// Extract the base URL from the endpoint to use as the resource for token acquisition
	// The endpoint might be different from the configured SharePoint URL (e.g., user's personal OneDrive)
	const endpoint = fileInfo['@sharePoint.endpoint'];
	let resource: string | undefined;
	if (endpoint && authorityType === 'organizations') {
		// Extract the base URL (e.g., https://tenant-my.sharepoint.com from https://tenant-my.sharepoint.com/_api/v2.0)
		try {
			const url = new URL(endpoint);
			resource = `${url.protocol}//${url.host}`;
		} catch {
			// Fall back to using the endpoint as-is if URL parsing fails
			resource = endpoint.split('/_api')[0];
		}
	}

	const accessToken = await getToken(resource, authorityType);
	if (!accessToken) {
		throw new Error('Unable to retrieve OneDrive access token.');
	}

	// The endpoint URL is provided in the file info
	if (!fileInfo.parentReference?.driveId) {
		throw new Error('File info missing parentReference.driveId');
	}
	const fileInfoUrl = `${endpoint}/drives/${fileInfo.parentReference.driveId}/items/${fileInfo.id}`;

	const response = await fetch(fileInfoUrl, {
		headers: {
			Authorization: `Bearer ${accessToken}`
		}
	});

	if (!response.ok) {
		throw new Error(`Failed to fetch file information: ${response.status} ${response.statusText}`);
	}

	const fileData = await response.json();
	const downloadUrl = fileData['@content.downloadUrl'];

	if (!downloadUrl) {
		throw new Error('Download URL not found in file data');
	}

	const downloadResponse = await fetch(downloadUrl);

	if (!downloadResponse.ok) {
		throw new Error(
			`Failed to download file: ${downloadResponse.status} ${downloadResponse.statusText}`
		);
	}

	const blob = await downloadResponse.blob();

	// Verify the blob has content - empty blobs indicate download failure
	if (blob.size === 0) {
		console.error('OneDrive download returned empty blob', {
			fileId: fileInfo.id,
			fileName: fileInfo.name,
			endpoint: endpoint
		});
		throw new Error('Downloaded file is empty. This may be due to permission issues.');
	}

	return blob;
}

// Open OneDrive file picker and return selected file metadata
export async function openOneDrivePicker(
	authorityType?: 'personal' | 'organizations'
): Promise<PickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize OneDrive config with the specified authority type
	const config = OneDriveConfig.getInstance();
	await config.initialize(authorityType);

	return new Promise((resolve, reject) => {
		let pickerWindow: Window | null = null;
		let channelPort: MessagePort | null = null;
		const params = getPickerParams();
		const baseUrl = config.getBaseUrl();

		const handleWindowMessage = (event: MessageEvent) => {
			if (event.source !== pickerWindow) return;
			const message = event.data;
			if (message?.type === 'initialize' && message?.channelId === params.messaging.channelId) {
				channelPort = event.ports?.[0];
				if (!channelPort) return;
				channelPort.addEventListener('message', handlePortMessage);
				channelPort.start();
				channelPort.postMessage({ type: 'activate' });
			}
		};

		const handlePortMessage = async (portEvent: MessageEvent) => {
			const portData = portEvent.data;
			switch (portData.type) {
				case 'notification':
					break;
				case 'command': {
					channelPort?.postMessage({ type: 'acknowledge', id: portData.id });
					const command = portData.data;
					switch (command.command) {
						case 'authenticate': {
							try {
								// Pass the resource from the command for org accounts
								const resource =
									config.getAuthorityType() === 'organizations' ? command.resource : undefined;
								const newToken = await getToken(resource, authorityType);
								if (newToken) {
									channelPort?.postMessage({
										type: 'result',
										id: portData.id,
										data: { result: 'token', token: newToken }
									});
								} else {
									throw new Error('Could not retrieve auth token');
								}
							} catch {
								channelPort?.postMessage({
									type: 'result',
									id: portData.id,
									data: {
										result: 'error',
										error: { code: 'tokenError', message: 'Failed to get token' }
									}
								});
							}
							break;
						}
						case 'close': {
							cleanup();
							resolve(null);
							break;
						}
						case 'pick': {
							channelPort?.postMessage({
								type: 'result',
								id: portData.id,
								data: { result: 'success' }
							});
							cleanup();
							resolve(command);
							break;
						}
						default: {
							channelPort?.postMessage({
								result: 'error',
								error: { code: 'unsupportedCommand', message: command.command },
								isExpected: true
							});
							break;
						}
					}
					break;
				}
			}
		};

		function cleanup() {
			window.removeEventListener('message', handleWindowMessage);
			if (channelPort) {
				channelPort.removeEventListener('message', handlePortMessage);
			}
			if (pickerWindow) {
				pickerWindow.close();
				pickerWindow = null;
			}
		}

		const initializePicker = async () => {
			try {
				const authToken = await getToken(undefined, authorityType);
				if (!authToken) {
					return reject(new Error('Failed to acquire access token'));
				}

				pickerWindow = window.open('', 'OneDrivePicker', 'width=800,height=600');
				if (!pickerWindow) {
					return reject(new Error('Failed to open OneDrive picker window'));
				}

				const queryString = new URLSearchParams({
					filePicker: JSON.stringify(params)
				});

				let url = '';
				if (config.getAuthorityType() === 'organizations') {
					url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;
				} else {
					url = baseUrl + `?${queryString}`;
				}

				const form = pickerWindow.document.createElement('form');
				form.setAttribute('action', url);
				form.setAttribute('method', 'POST');
				const input = pickerWindow.document.createElement('input');
				input.setAttribute('type', 'hidden');
				input.setAttribute('name', 'access_token');
				input.setAttribute('value', authToken);
				form.appendChild(input);

				pickerWindow.document.body.appendChild(form);
				form.submit();

				window.addEventListener('message', handleWindowMessage);
			} catch (err) {
				if (pickerWindow) {
					pickerWindow.close();
				}
				reject(err);
			}
		};

		initializePicker();
	});
}

// Pick and download file from OneDrive (popup version)
export async function pickAndDownloadFile(
	authorityType?: 'personal' | 'organizations'
): Promise<{ blob: Blob; name: string } | null> {
	const pickerResult = await openOneDrivePicker(authorityType);

	if (!pickerResult || !pickerResult.items || pickerResult.items.length === 0) {
		return null;
	}

	const selectedFile = pickerResult.items[0];
	const blob = await downloadOneDriveFile(selectedFile, authorityType);

	return { blob, name: selectedFile.name };
}

// Get file picker params with channelId parameter (for modal use)
function getFilePickerParams(channelId: string): PickerParams {
	const config = OneDriveConfig.getInstance();

	const params: PickerParams = {
		sdk: '8.0',
		entry: {
			oneDrive: {}
		},
		authentication: {},
		messaging: {
			origin: window?.location?.origin || '',
			channelId
		},
		search: {
			enabled: true
		},
		selection: {
			mode: 'multiple',
			enablePersistence: true
		},
		typesAndSources: {
			mode: 'files',
			pivots: {
				oneDrive: true,
				recent: true,
				myOrganization: config.getAuthorityType() === 'organizations'
			}
		}
	};

	// For personal accounts, set files object in oneDrive
	if (config.getAuthorityType() !== 'organizations') {
		params.entry.oneDrive = { files: {} };
	}

	return params;
}

// Open OneDrive file picker in an embedded modal (iframe)
export async function openOneDriveFilePickerModal(
	authorityType?: 'personal' | 'organizations'
): Promise<PickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize OneDrive config with the specified authority type
	const config = OneDriveConfig.getInstance();
	await config.initialize(authorityType);

	const channelId = uuidv4();
	const params = getFilePickerParams(channelId);
	const baseUrl = config.getBaseUrl();

	// Get auth token first (before creating UI)
	const authToken = await getToken(undefined, authorityType);
	if (!authToken) {
		throw new Error('Failed to acquire access token');
	}

	return new Promise((resolve) => {
		let channelPort: MessagePort | null = null;
		let pickerIframe: HTMLIFrameElement | null = null;

		// Create modal overlay
		const modalOverlay = document.createElement('div');
		modalOverlay.id = 'onedrive-file-picker-modal';
		modalOverlay.style.cssText = `
			position: fixed;
			top: 0;
			left: 0;
			right: 0;
			bottom: 0;
			background-color: rgba(0, 0, 0, 0.5);
			z-index: 10000;
			display: flex;
			justify-content: center;
			align-items: center;
			backdrop-filter: blur(2px);
		`;

		// Create modal container
		const modalContainer = document.createElement('div');
		modalContainer.style.cssText = `
			width: 90%;
			max-width: 1000px;
			height: 85%;
			max-height: 700px;
			background: white;
			border-radius: 12px;
			overflow: hidden;
			box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
			display: flex;
			flex-direction: column;
		`;

		// Create header with close button
		const header = document.createElement('div');
		header.style.cssText = `
			display: flex;
			justify-content: space-between;
			align-items: center;
			padding: 12px 16px;
			background: #f5f5f5;
			border-bottom: 1px solid #e0e0e0;
		`;

		const title = document.createElement('span');
		title.textContent = 'Select OneDrive File';
		title.style.cssText = `
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
			font-weight: 600;
			font-size: 16px;
			color: #333;
		`;

		const closeButton = document.createElement('button');
		closeButton.innerHTML = '✕';
		closeButton.style.cssText = `
			background: none;
			border: none;
			font-size: 20px;
			cursor: pointer;
			color: #666;
			padding: 4px 8px;
			border-radius: 4px;
			transition: background-color 0.2s;
		`;
		closeButton.onmouseover = () => {
			closeButton.style.backgroundColor = '#e0e0e0';
		};
		closeButton.onmouseout = () => {
			closeButton.style.backgroundColor = 'transparent';
		};
		closeButton.onclick = () => {
			cleanup();
			resolve(null);
		};

		header.appendChild(title);
		header.appendChild(closeButton);

		// Create iframe container
		const iframeContainer = document.createElement('div');
		iframeContainer.style.cssText = `
			flex: 1;
			position: relative;
		`;

		// Create loading indicator
		const loadingDiv = document.createElement('div');
		loadingDiv.style.cssText = `
			position: absolute;
			top: 0;
			left: 0;
			right: 0;
			bottom: 0;
			display: flex;
			justify-content: center;
			align-items: center;
			background: white;
		`;
		loadingDiv.innerHTML = `
			<div style="text-align: center; color: #666;">
				<div style="
					width: 40px;
					height: 40px;
					border: 4px solid #e0e0e0;
					border-top-color: #0078d4;
					border-radius: 50%;
					animation: spin 1s linear infinite;
					margin: 0 auto 16px;
				"></div>
				<p style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
					Connecting to OneDrive...
				</p>
			</div>
			<style>
				@keyframes spin { to { transform: rotate(360deg); } }
			</style>
		`;

		// Create iframe
		pickerIframe = document.createElement('iframe');
		const iframeName = `onedrive-file-picker-${channelId}`;
		pickerIframe.name = iframeName;
		pickerIframe.style.cssText = `
			width: 100%;
			height: 100%;
			border: none;
		`;
		pickerIframe.onload = () => {
			// Hide loading indicator once iframe loads
			loadingDiv.style.display = 'none';
		};

		iframeContainer.appendChild(loadingDiv);
		iframeContainer.appendChild(pickerIframe);

		modalContainer.appendChild(header);
		modalContainer.appendChild(iframeContainer);
		modalOverlay.appendChild(modalContainer);

		// Handle click outside to close
		modalOverlay.onclick = (e) => {
			if (e.target === modalOverlay) {
				cleanup();
				resolve(null);
			}
		};

		// Handle escape key
		const handleEscape = (e: KeyboardEvent) => {
			if (e.key === 'Escape') {
				cleanup();
				resolve(null);
			}
		};
		document.addEventListener('keydown', handleEscape);

		// Add modal to document
		document.body.appendChild(modalOverlay);

		// Create hidden form to submit to iframe
		const form = document.createElement('form');
		form.style.display = 'none';
		form.target = iframeName;
		form.method = 'POST';

		const queryString = new URLSearchParams({
			filePicker: JSON.stringify(params)
		});

		let url = '';
		if (config.getAuthorityType() === 'organizations') {
			url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;
		} else {
			url = baseUrl + `?${queryString}`;
		}

		form.action = url;

		const input = document.createElement('input');
		input.type = 'hidden';
		input.name = 'access_token';
		input.value = authToken;
		form.appendChild(input);

		document.body.appendChild(form);
		form.submit();
		document.body.removeChild(form);

		const handleWindowMessage = (event: MessageEvent) => {
			// Check if message is from our iframe
			if (
				pickerIframe?.contentWindow &&
				event.source === pickerIframe.contentWindow &&
				event.data?.type === 'initialize' &&
				event.data?.channelId === params.messaging.channelId
			) {
				channelPort = event.ports?.[0];
				if (!channelPort) return;
				channelPort.addEventListener('message', handlePortMessage);
				channelPort.start();
				channelPort.postMessage({ type: 'activate' });
			}
		};

		const handlePortMessage = async (portEvent: MessageEvent) => {
			const portData = portEvent.data;
			switch (portData.type) {
				case 'notification':
					break;
				case 'command': {
					channelPort?.postMessage({ type: 'acknowledge', id: portData.id });
					const command = portData.data;
					switch (command.command) {
						case 'authenticate': {
							// Use silent-only token acquisition in iframe context
							// Popup auth doesn't work from within iframes
							const resource =
								config.getAuthorityType() === 'organizations' ? command.resource : undefined;
							const newToken = await getTokenSilent(resource, authorityType);
							if (newToken) {
								channelPort?.postMessage({
									type: 'result',
									id: portData.id,
									data: { result: 'token', token: newToken }
								});
							} else {
								// Silent acquisition failed - this is expected for some resources
								// The picker will handle this gracefully
								channelPort?.postMessage({
									type: 'result',
									id: portData.id,
									data: {
										result: 'error',
										error: { code: 'tokenError', message: 'Silent token acquisition failed' }
									}
								});
							}
							break;
						}
						case 'close': {
							cleanup();
							resolve(null);
							break;
						}
						case 'pick': {
							channelPort?.postMessage({
								type: 'result',
								id: portData.id,
								data: { result: 'success' }
							});
							cleanup();
							resolve(command);
							break;
						}
						default: {
							channelPort?.postMessage({
								result: 'error',
								error: { code: 'unsupportedCommand', message: command.command },
								isExpected: true
							});
							break;
						}
					}
					break;
				}
			}
		};

		function cleanup() {
			window.removeEventListener('message', handleWindowMessage);
			document.removeEventListener('keydown', handleEscape);
			if (channelPort) {
				channelPort.removeEventListener('message', handlePortMessage);
			}
			if (modalOverlay && modalOverlay.parentNode) {
				modalOverlay.parentNode.removeChild(modalOverlay);
			}
			pickerIframe = null;
		}

		window.addEventListener('message', handleWindowMessage);
	});
}

// Pick and download file from OneDrive using modal (iframe version)
export async function pickAndDownloadFileModal(
	authorityType?: 'personal' | 'organizations'
): Promise<{ blob: Blob; name: string } | null> {
	const pickerResult = await openOneDriveFilePickerModal(authorityType);

	if (!pickerResult || !pickerResult.items || pickerResult.items.length === 0) {
		return null;
	}

	const selectedFile = pickerResult.items[0];
	const blob = await downloadOneDriveFile(selectedFile, authorityType);

	return { blob, name: selectedFile.name };
}

// Pick and download multiple files from OneDrive using modal (iframe version)
export async function pickAndDownloadFilesModal(
	authorityType?: 'personal' | 'organizations'
): Promise<Array<{ blob: Blob; name: string }>> {
	const pickerResult = await openOneDriveFilePickerModal(authorityType);

	if (!pickerResult || !pickerResult.items || pickerResult.items.length === 0) {
		return [];
	}

	// Download all selected files in parallel
	const downloadPromises = pickerResult.items.map(async (item) => {
		try {
			const blob = await downloadOneDriveFile(item, authorityType);
			return { blob, name: item.name };
		} catch (error) {
			console.error(`Failed to download file ${item.name}:`, error);
			return null;
		}
	});

	const results = await Promise.all(downloadPromises);

	// Filter out failed downloads
	return results.filter((result): result is { blob: Blob; name: string } => result !== null);
}

// Open OneDrive folder picker in an embedded modal (iframe)
export async function openOneDriveFolderPicker(
	authorityType?: 'personal' | 'organizations'
): Promise<FolderPickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize OneDrive config with the specified authority type
	const config = OneDriveConfig.getInstance();
	await config.initialize(authorityType);

	const channelId = uuidv4();
	const params = getFolderPickerParams(channelId);
	const baseUrl = config.getBaseUrl();

	// Get auth token first (before creating UI)
	const authToken = await getToken(undefined, authorityType);
	if (!authToken) {
		throw new Error('Failed to acquire access token');
	}

	return new Promise((resolve) => {
		let channelPort: MessagePort | null = null;
		let pickerIframe: HTMLIFrameElement | null = null;

		// Create modal overlay
		const modalOverlay = document.createElement('div');
		modalOverlay.id = 'onedrive-picker-modal';
		modalOverlay.style.cssText = `
			position: fixed;
			top: 0;
			left: 0;
			right: 0;
			bottom: 0;
			background-color: rgba(0, 0, 0, 0.5);
			z-index: 10000;
			display: flex;
			justify-content: center;
			align-items: center;
			backdrop-filter: blur(2px);
		`;

		// Create modal container
		const modalContainer = document.createElement('div');
		modalContainer.style.cssText = `
			width: 90%;
			max-width: 1000px;
			height: 85%;
			max-height: 700px;
			background: white;
			border-radius: 12px;
			overflow: hidden;
			box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
			display: flex;
			flex-direction: column;
		`;

		// Create header with close button
		const header = document.createElement('div');
		header.style.cssText = `
			display: flex;
			justify-content: space-between;
			align-items: center;
			padding: 12px 16px;
			background: #f5f5f5;
			border-bottom: 1px solid #e0e0e0;
		`;

		const title = document.createElement('span');
		title.textContent = 'Select OneDrive Folder';
		title.style.cssText = `
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
			font-weight: 600;
			font-size: 16px;
			color: #333;
		`;

		const closeButton = document.createElement('button');
		closeButton.innerHTML = '✕';
		closeButton.style.cssText = `
			background: none;
			border: none;
			font-size: 20px;
			cursor: pointer;
			color: #666;
			padding: 4px 8px;
			border-radius: 4px;
			transition: background-color 0.2s;
		`;
		closeButton.onmouseover = () => {
			closeButton.style.backgroundColor = '#e0e0e0';
		};
		closeButton.onmouseout = () => {
			closeButton.style.backgroundColor = 'transparent';
		};
		closeButton.onclick = () => {
			cleanup();
			resolve(null);
		};

		header.appendChild(title);
		header.appendChild(closeButton);

		// Create iframe container
		const iframeContainer = document.createElement('div');
		iframeContainer.style.cssText = `
			flex: 1;
			position: relative;
		`;

		// Create loading indicator
		const loadingDiv = document.createElement('div');
		loadingDiv.style.cssText = `
			position: absolute;
			top: 0;
			left: 0;
			right: 0;
			bottom: 0;
			display: flex;
			justify-content: center;
			align-items: center;
			background: white;
		`;
		loadingDiv.innerHTML = `
			<div style="text-align: center; color: #666;">
				<div style="
					width: 40px;
					height: 40px;
					border: 4px solid #e0e0e0;
					border-top-color: #0078d4;
					border-radius: 50%;
					animation: spin 1s linear infinite;
					margin: 0 auto 16px;
				"></div>
				<p style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
					Connecting to OneDrive...
				</p>
			</div>
			<style>
				@keyframes spin { to { transform: rotate(360deg); } }
			</style>
		`;

		// Create iframe
		pickerIframe = document.createElement('iframe');
		const iframeName = `onedrive-picker-${channelId}`;
		pickerIframe.name = iframeName;
		pickerIframe.style.cssText = `
			width: 100%;
			height: 100%;
			border: none;
		`;
		pickerIframe.onload = () => {
			// Hide loading indicator once iframe loads
			loadingDiv.style.display = 'none';
		};

		iframeContainer.appendChild(loadingDiv);
		iframeContainer.appendChild(pickerIframe);

		modalContainer.appendChild(header);
		modalContainer.appendChild(iframeContainer);
		modalOverlay.appendChild(modalContainer);

		// Handle click outside to close
		modalOverlay.onclick = (e) => {
			if (e.target === modalOverlay) {
				cleanup();
				resolve(null);
			}
		};

		// Handle escape key
		const handleEscape = (e: KeyboardEvent) => {
			if (e.key === 'Escape') {
				cleanup();
				resolve(null);
			}
		};
		document.addEventListener('keydown', handleEscape);

		// Add modal to document
		document.body.appendChild(modalOverlay);

		// Create hidden form to submit to iframe
		const form = document.createElement('form');
		form.style.display = 'none';
		form.target = iframeName;
		form.method = 'POST';

		const queryString = new URLSearchParams({
			filePicker: JSON.stringify(params)
		});

		let url = '';
		if (config.getAuthorityType() === 'organizations') {
			url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;
		} else {
			url = baseUrl + `?${queryString}`;
		}

		form.action = url;

		const input = document.createElement('input');
		input.type = 'hidden';
		input.name = 'access_token';
		input.value = authToken;
		form.appendChild(input);

		document.body.appendChild(form);
		form.submit();
		document.body.removeChild(form);

		const handleWindowMessage = (event: MessageEvent) => {
			// Check if message is from our iframe
			if (
				pickerIframe?.contentWindow &&
				event.source === pickerIframe.contentWindow &&
				event.data?.type === 'initialize' &&
				event.data?.channelId === params.messaging.channelId
			) {
				channelPort = event.ports?.[0];
				if (!channelPort) return;
				channelPort.addEventListener('message', handlePortMessage);
				channelPort.start();
				channelPort.postMessage({ type: 'activate' });
			}
		};

		const handlePortMessage = async (portEvent: MessageEvent) => {
			const portData = portEvent.data;
			switch (portData.type) {
				case 'notification':
					break;
				case 'command': {
					channelPort?.postMessage({ type: 'acknowledge', id: portData.id });
					const command = portData.data;
					switch (command.command) {
						case 'authenticate': {
							// Use silent-only token acquisition in iframe context
							// Popup auth doesn't work from within iframes
							const resource =
								config.getAuthorityType() === 'organizations' ? command.resource : undefined;
							const newToken = await getTokenSilent(resource, authorityType);
							if (newToken) {
								channelPort?.postMessage({
									type: 'result',
									id: portData.id,
									data: { result: 'token', token: newToken }
								});
							} else {
								// Silent acquisition failed - this is expected for some resources
								// The picker will handle this gracefully
								channelPort?.postMessage({
									type: 'result',
									id: portData.id,
									data: {
										result: 'error',
										error: { code: 'tokenError', message: 'Silent token acquisition failed' }
									}
								});
							}
							break;
						}
						case 'close': {
							cleanup();
							resolve(null);
							break;
						}
						case 'pick': {
							channelPort?.postMessage({
								type: 'result',
								id: portData.id,
								data: { result: 'success' }
							});
							cleanup();

							const items = command.items;
							if (items && items.length > 0) {
								const folder = items[0];
								resolve({
									id: folder.id,
									name: folder.name,
									driveId: folder.parentReference?.driveId,
									path: folder.parentReference?.path || '',
									webUrl: folder.webUrl || ''
								});
							} else {
								resolve(null);
							}
							break;
						}
						default: {
							channelPort?.postMessage({
								result: 'error',
								error: { code: 'unsupportedCommand', message: command.command },
								isExpected: true
							});
							break;
						}
					}
					break;
				}
			}
		};

		function cleanup() {
			window.removeEventListener('message', handleWindowMessage);
			document.removeEventListener('keydown', handleEscape);
			if (channelPort) {
				channelPort.removeEventListener('message', handlePortMessage);
			}
			if (modalOverlay && modalOverlay.parentNode) {
				modalOverlay.parentNode.removeChild(modalOverlay);
			}
			pickerIframe = null;
		}

		window.addEventListener('message', handleWindowMessage);
	});
}

// Open OneDrive item picker for multi-select files and folders
export async function openOneDriveItemPicker(
	authorityType?: 'personal' | 'organizations'
): Promise<MultiItemPickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize OneDrive config with the specified authority type
	const config = OneDriveConfig.getInstance();
	await config.initialize(authorityType);

	const channelId = uuidv4();
	const params = getItemPickerParams(channelId);
	const baseUrl = config.getBaseUrl();

	// Get auth token first (before creating UI)
	const authToken = await getToken(undefined, authorityType);
	if (!authToken) {
		throw new Error('Failed to acquire access token');
	}

	return new Promise((resolve) => {
		let channelPort: MessagePort | null = null;
		let pickerIframe: HTMLIFrameElement | null = null;

		// Create modal overlay
		const modalOverlay = document.createElement('div');
		modalOverlay.id = 'onedrive-item-picker-modal';
		modalOverlay.style.cssText = `
			position: fixed;
			top: 0;
			left: 0;
			right: 0;
			bottom: 0;
			background-color: rgba(0, 0, 0, 0.5);
			z-index: 10000;
			display: flex;
			justify-content: center;
			align-items: center;
			backdrop-filter: blur(2px);
		`;

		// Create modal container
		const modalContainer = document.createElement('div');
		modalContainer.style.cssText = `
			width: 90%;
			max-width: 1000px;
			height: 85%;
			max-height: 700px;
			background: white;
			border-radius: 12px;
			overflow: hidden;
			box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
			display: flex;
			flex-direction: column;
		`;

		// Create header with close button
		const header = document.createElement('div');
		header.style.cssText = `
			display: flex;
			justify-content: space-between;
			align-items: center;
			padding: 12px 16px;
			background: #f5f5f5;
			border-bottom: 1px solid #e0e0e0;
		`;

		const title = document.createElement('span');
		title.textContent = 'Select OneDrive Files and Folders';
		title.style.cssText = `
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
			font-weight: 600;
			font-size: 16px;
			color: #333;
		`;

		const closeButton = document.createElement('button');
		closeButton.innerHTML = '✕';
		closeButton.style.cssText = `
			background: none;
			border: none;
			font-size: 20px;
			cursor: pointer;
			color: #666;
			padding: 4px 8px;
			border-radius: 4px;
			transition: background-color 0.2s;
		`;
		closeButton.onmouseover = () => {
			closeButton.style.backgroundColor = '#e0e0e0';
		};
		closeButton.onmouseout = () => {
			closeButton.style.backgroundColor = 'transparent';
		};
		closeButton.onclick = () => {
			cleanup();
			resolve(null);
		};

		header.appendChild(title);
		header.appendChild(closeButton);

		// Create iframe container
		const iframeContainer = document.createElement('div');
		iframeContainer.style.cssText = `
			flex: 1;
			position: relative;
		`;

		// Create loading indicator
		const loadingDiv = document.createElement('div');
		loadingDiv.style.cssText = `
			position: absolute;
			top: 0;
			left: 0;
			right: 0;
			bottom: 0;
			display: flex;
			justify-content: center;
			align-items: center;
			background: white;
		`;
		loadingDiv.innerHTML = `
			<div style="text-align: center; color: #666;">
				<div style="
					width: 40px;
					height: 40px;
					border: 4px solid #e0e0e0;
					border-top-color: #0078d4;
					border-radius: 50%;
					animation: spin 1s linear infinite;
					margin: 0 auto 16px;
				"></div>
				<p style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
					Connecting to OneDrive...
				</p>
			</div>
			<style>
				@keyframes spin { to { transform: rotate(360deg); } }
			</style>
		`;

		// Create iframe
		pickerIframe = document.createElement('iframe');
		const iframeName = `onedrive-item-picker-${channelId}`;
		pickerIframe.name = iframeName;
		pickerIframe.style.cssText = `
			width: 100%;
			height: 100%;
			border: none;
		`;
		pickerIframe.onload = () => {
			// Hide loading indicator once iframe loads
			loadingDiv.style.display = 'none';
		};

		iframeContainer.appendChild(loadingDiv);
		iframeContainer.appendChild(pickerIframe);

		modalContainer.appendChild(header);
		modalContainer.appendChild(iframeContainer);
		modalOverlay.appendChild(modalContainer);

		// Handle click outside to close
		modalOverlay.onclick = (e) => {
			if (e.target === modalOverlay) {
				cleanup();
				resolve(null);
			}
		};

		// Handle escape key
		const handleEscape = (e: KeyboardEvent) => {
			if (e.key === 'Escape') {
				cleanup();
				resolve(null);
			}
		};
		document.addEventListener('keydown', handleEscape);

		// Add modal to document
		document.body.appendChild(modalOverlay);

		// Create hidden form to submit to iframe
		const form = document.createElement('form');
		form.style.display = 'none';
		form.target = iframeName;
		form.method = 'POST';

		const queryString = new URLSearchParams({
			filePicker: JSON.stringify(params)
		});

		let url = '';
		if (config.getAuthorityType() === 'organizations') {
			url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;
		} else {
			url = baseUrl + `?${queryString}`;
		}

		form.action = url;

		const input = document.createElement('input');
		input.type = 'hidden';
		input.name = 'access_token';
		input.value = authToken;
		form.appendChild(input);

		document.body.appendChild(form);
		form.submit();
		document.body.removeChild(form);

		const handleWindowMessage = (event: MessageEvent) => {
			// Check if message is from our iframe
			if (
				pickerIframe?.contentWindow &&
				event.source === pickerIframe.contentWindow &&
				event.data?.type === 'initialize' &&
				event.data?.channelId === params.messaging.channelId
			) {
				channelPort = event.ports?.[0];
				if (!channelPort) return;
				channelPort.addEventListener('message', handlePortMessage);
				channelPort.start();
				channelPort.postMessage({ type: 'activate' });
			}
		};

		const handlePortMessage = async (portEvent: MessageEvent) => {
			const portData = portEvent.data;
			switch (portData.type) {
				case 'notification':
					break;
				case 'command': {
					channelPort?.postMessage({ type: 'acknowledge', id: portData.id });
					const command = portData.data;
					switch (command.command) {
						case 'authenticate': {
							// Use silent-only token acquisition in iframe context
							const resource =
								config.getAuthorityType() === 'organizations' ? command.resource : undefined;
							const newToken = await getTokenSilent(resource, authorityType);
							if (newToken) {
								channelPort?.postMessage({
									type: 'result',
									id: portData.id,
									data: { result: 'token', token: newToken }
								});
							} else {
								channelPort?.postMessage({
									type: 'result',
									id: portData.id,
									data: {
										result: 'error',
										error: { code: 'tokenError', message: 'Silent token acquisition failed' }
									}
								});
							}
							break;
						}
						case 'close': {
							cleanup();
							resolve(null);
							break;
						}
						case 'pick': {
							channelPort?.postMessage({
								type: 'result',
								id: portData.id,
								data: { result: 'success' }
							});
							cleanup();

							const items = command.items;
							if (items && items.length > 0) {
								const results: ItemPickerResult[] = items.map((item: OneDriveFileInfo) => ({
									type: item.folder ? 'folder' : 'file',
									id: item.id,
									name: item.name,
									driveId: item.parentReference?.driveId,
									path: item.parentReference?.path || '',
									webUrl: item.webUrl || ''
								}));
								resolve(results);
							} else {
								resolve(null);
							}
							break;
						}
						default: {
							channelPort?.postMessage({
								result: 'error',
								error: { code: 'unsupportedCommand', message: command.command },
								isExpected: true
							});
							break;
						}
					}
					break;
				}
			}
		};

		function cleanup() {
			window.removeEventListener('message', handleWindowMessage);
			document.removeEventListener('keydown', handleEscape);
			if (channelPort) {
				channelPort.removeEventListener('message', handlePortMessage);
			}
			if (modalOverlay && modalOverlay.parentNode) {
				modalOverlay.parentNode.removeChild(modalOverlay);
			}
			pickerIframe = null;
		}

		window.addEventListener('message', handleWindowMessage);
	});
}

/**
 * Get a token specifically for Microsoft Graph API calls.
 * This is different from the picker token which is scoped to SharePoint.
 */
export async function getGraphApiToken(
	authorityType?: 'personal' | 'organizations'
): Promise<string> {
	const config = OneDriveConfig.getInstance();
	await config.ensureInitialized(authorityType);

	const currentAuthorityType = config.getAuthorityType();

	// Graph API scopes - Files.Read.All covers delta, list, download
	const scopes =
		currentAuthorityType === 'organizations'
			? ['https://graph.microsoft.com/Files.Read.All']
			: ['Files.Read.All'];

	const authParams: PopupRequest = { scopes };
	let accessToken = '';

	try {
		const msalInstance = await config.getMsalInstance(authorityType);
		const resp = await msalInstance.acquireTokenSilent(authParams);
		accessToken = resp.accessToken;
	} catch {
		const msalInstance = await config.getMsalInstance(authorityType);
		try {
			const resp = await msalInstance.loginPopup(authParams);
			msalInstance.setActiveAccount(resp.account);
			if (resp.idToken) {
				const resp2 = await msalInstance.acquireTokenSilent(authParams);
				accessToken = resp2.accessToken;
			}
		} catch (popupError) {
			throw new Error(
				'Failed to acquire Graph API token: ' +
					(popupError instanceof Error ? popupError.message : String(popupError))
			);
		}
	}

	if (!accessToken) {
		throw new Error('Failed to acquire Graph API access token');
	}

	return accessToken;
}

export { downloadOneDriveFile, getToken };
