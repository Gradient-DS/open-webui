import type { AccountInfo, PopupRequest, PublicClientApplication } from '@azure/msal-browser';
import { v4 as uuidv4 } from 'uuid';
import { WEBUI_BASE_URL } from '$lib/constants';
import { fetchOneDriveHost } from './onedrive-host';

class OneDriveConfig {
	private static instance: OneDriveConfig;
	private clientIdBusiness: string = '';
	private sharepointUrl: string = '';
	private sharepointTenantId: string = '';
	private credentialsLoaded = false;
	private derivedHost: string | null = null;
	private derivedAccount: string | undefined;
	private msalInstance: Promise<PublicClientApplication> | undefined;

	private constructor() {}

	public static getInstance(): OneDriveConfig {
		if (!OneDriveConfig.instance) {
			OneDriveConfig.instance = new OneDriveConfig();
		}
		return OneDriveConfig.instance;
	}

	public async initialize(): Promise<void> {
		await this.getCredentials();
	}

	public async ensureInitialized(): Promise<void> {
		await this.initialize();
	}

	private async getCredentials(): Promise<void> {
		// The picker flow calls ensureInitialized() many times per session
		// (init, token acquire, per-operation) — without this cache every call
		// re-fetches /api/config, producing request bursts. The config values
		// are shared by every picker operation.
		if (this.credentialsLoaded) {
			return;
		}

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

		this.clientIdBusiness = config.onedrive?.client_id_business;
		this.sharepointUrl = config.onedrive?.sharepoint_url;
		this.sharepointTenantId = config.onedrive?.sharepoint_tenant_id;

		if (!this.clientIdBusiness) {
			throw new Error('OneDrive business client ID not configured');
		}

		this.credentialsLoaded = true;
	}

	public async getMsalInstance(): Promise<PublicClientApplication> {
		await this.getCredentials();
		this.msalInstance ??= this.createMsalInstance();
		try {
			return await this.msalInstance;
		} catch (error) {
			this.msalInstance = undefined;
			throw error;
		}
	}

	private async createMsalInstance(): Promise<PublicClientApplication> {
		const authorityEndpoint =
			!this.sharepointTenantId || this.sharepointTenantId === 'common'
				? 'organizations'
				: this.sharepointTenantId;
		const clientId = this.clientIdBusiness;
		if (!clientId) throw new Error('OneDrive client ID not configured');
		const { PublicClientApplication } = await import('@azure/msal-browser');
		const instance = new PublicClientApplication({
			auth: {
				authority: `https://login.microsoftonline.com/${authorityEndpoint}`,
				clientId,
				redirectUri: window.location.origin
			}
		});
		await instance.initialize();
		return instance;
	}

	public getSharepointUrl(): string {
		return this.sharepointUrl;
	}

	public getSharepointTenantId(): string {
		return this.sharepointTenantId;
	}

	/**
	 * Ensure an effective SharePoint host is available for `organizations` mode.
	 * - Static mode (sharepoint_url set): no-op, getBaseUrl() uses it.
	 * - Derive mode (sharepoint_url blank): derive once per session from the
	 *   signed-in user's Graph /me/drive webUrl (true per-user multi-tenant).
	 *
	 * Must be awaited after initialize() and before any getBaseUrl()/getToken()
	 * use. The derived value is memoized on `derivedHost`, which deliberately
	 * survives the per-call getCredentials() reset of `sharepointUrl`.
	 */
	public async resolveHost(allowPopup = true): Promise<void> {
		await this.ensureInitialized();

		if (this.sharepointUrl && this.sharepointUrl !== '') return; // static mode
		const msal = await this.getMsalInstance();
		const account = msal.getActiveAccount()?.homeAccountId;
		if (this.derivedHost && this.derivedAccount === account) return;

		const graphToken = await getGraphApiToken(allowPopup); // host-independent
		this.derivedHost = await fetchOneDriveHost(graphToken);
		this.derivedAccount = msal.getActiveAccount()?.homeAccountId;
	}

	public getBaseUrl(): string {
		const host = this.sharepointUrl || this.derivedHost;
		if (!host) throw new Error('Sharepoint URL not configured');
		return `https://${host.replace(/^https?:\/\//, '').replace(/\/$/, '')}`;
	}
}

// Retrieve OneDrive access token
async function getToken(resource?: string, allowPopup = true): Promise<string> {
	const config = OneDriveConfig.getInstance();
	await config.ensureInitialized();

	const scopes = [`${resource || config.getBaseUrl()}/.default`];

	const authParams: PopupRequest = { scopes };
	let accessToken = '';

	try {
		const msalInstance = await config.getMsalInstance();
		const resp = await msalInstance.acquireTokenSilent(authParams);
		accessToken = resp.accessToken;
	} catch {
		if (!allowPopup) {
			businessLogin = { scopes };
			throw new Error('Sign in to OneDrive again to finish opening the picker.');
		}
		const msalInstance = await config.getMsalInstance();
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
async function getTokenSilent(resource?: string): Promise<string | null> {
	const config = OneDriveConfig.getInstance();
	await config.ensureInitialized();

	const scopes = [`${resource || config.getBaseUrl()}/.default`];

	const authParams: PopupRequest = { scopes };

	try {
		const msalInstance = await config.getMsalInstance();
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

// Get picker parameters
function getPickerParams(): PickerParams {
	const channelId = uuidv4();

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
				myOrganization: true
			}
		}
	};

	return params;
}

// Get folder picker parameters for folder selection mode
function getFolderPickerParams(channelId: string): PickerParams {
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
				myOrganization: true
			}
		}
	};

	return params;
}

// Get item picker parameters for multi-select files and folders
function getItemPickerParams(channelId: string): PickerParams {
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
				myOrganization: true
			}
		}
	};

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

export interface DocumentReference {
	drive_id: string;
	item_id: string;
	name: string;
	etag: string;
	web_url: string;
	size: number;
}

export function documentReference(item: OneDriveFileInfo): DocumentReference {
	const drive = item.parentReference?.driveId;
	if (
		!drive ||
		!item.id ||
		!item.name ||
		typeof item.eTag !== 'string' ||
		!item.eTag ||
		typeof item.webUrl !== 'string' ||
		!item.webUrl.startsWith('https://') ||
		!Number.isSafeInteger(item.size) ||
		item.size < 0 ||
		item.folder
	) {
		throw new Error('The picker did not return a versioned document reference');
	}
	return {
		drive_id: drive,
		item_id: item.id,
		name: item.name,
		etag: item.eTag,
		web_url: item.webUrl,
		size: item.size
	};
}

// Open OneDrive file picker and return selected file metadata
export async function openOneDrivePicker(): Promise<PickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize the organisational picker
	const config = OneDriveConfig.getInstance();
	await config.initialize();
	await config.resolveHost();

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
								const resource = command.resource;
								const newToken = await getToken(resource);
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
				const authToken = await getToken(undefined);
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

				const url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;

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

// Get file picker params with channelId parameter (for modal use)
function getFilePickerParams(channelId: string): PickerParams {
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
				myOrganization: true
			}
		}
	};

	return params;
}

// Open OneDrive file picker in an embedded modal (iframe)
export async function openOneDriveFilePickerModal(): Promise<PickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize the organisational picker
	const config = OneDriveConfig.getInstance();
	await config.initialize();
	await config.resolveHost(false);

	const channelId = uuidv4();
	const params = getFilePickerParams(channelId);
	const baseUrl = config.getBaseUrl();

	// Get auth token first (before creating UI)
	const authToken = await getToken(undefined, false);
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

		const url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;

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
							const resource = command.resource;
							const newToken = await getTokenSilent(resource);
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

export async function pickDocumentReferencesModal(): Promise<DocumentReference[]> {
	const selected = await openOneDriveFilePickerModal();
	return (selected?.items ?? []).map(documentReference);
}

// Open OneDrive folder picker in an embedded modal (iframe)
export async function openOneDriveFolderPicker(): Promise<FolderPickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize the organisational picker
	const config = OneDriveConfig.getInstance();
	await config.initialize();
	await config.resolveHost();

	const channelId = uuidv4();
	const params = getFolderPickerParams(channelId);
	const baseUrl = config.getBaseUrl();

	// Get auth token first (before creating UI)
	const authToken = await getToken(undefined);
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

		const url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;

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
							const resource = command.resource;
							const newToken = await getTokenSilent(resource);
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
export async function openOneDriveItemPicker(): Promise<MultiItemPickerResult | null> {
	if (typeof window === 'undefined') {
		throw new Error('Not in browser environment');
	}

	// Initialize the organisational picker
	const config = OneDriveConfig.getInstance();
	await config.initialize();
	await config.resolveHost();

	const channelId = uuidv4();
	const params = getItemPickerParams(channelId);
	const baseUrl = config.getBaseUrl();

	// Get auth token first (before creating UI)
	const authToken = await getToken(undefined);
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

		const url = baseUrl + `/_layouts/15/FilePicker.aspx?${queryString}`;

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
							const resource = command.resource;
							const newToken = await getTokenSilent(resource);
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
export async function getGraphApiToken(allowPopup = true): Promise<string> {
	const config = OneDriveConfig.getInstance();
	await config.ensureInitialized();

	// Graph API scopes - Files.Read.All covers delta, list, download
	const scopes = ['https://graph.microsoft.com/Files.Read.All'];

	const authParams: PopupRequest = { scopes };
	let accessToken = '';

	try {
		const msalInstance = await config.getMsalInstance();
		const resp = await msalInstance.acquireTokenSilent(authParams);
		accessToken = resp.accessToken;
	} catch {
		if (!allowPopup) {
			businessLogin = { scopes };
			throw new Error('Sign in to OneDrive again to finish opening the picker.');
		}
		const msalInstance = await config.getMsalInstance();
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

export { getToken, OneDriveConfig };

let businessMsal: PublicClientApplication | undefined;
let businessLogin: PopupRequest | undefined;
let preparingBusiness: Promise<void> | undefined;

export function prepareBusinessDocumentPicker(): Promise<void> {
	if (preparingBusiness) return preparingBusiness;
	preparingBusiness = (async () => {
		const config = OneDriveConfig.getInstance();
		businessMsal = await config.getMsalInstance();
		const account = businessMsal.getActiveAccount() ?? businessMsal.getAllAccounts()[0];
		if (account) businessMsal.setActiveAccount(account);
		businessLogin = undefined;
		let scopes = ['https://graph.microsoft.com/Files.Read.All'];
		let host = config.getSharepointUrl();
		try {
			if (!host) {
				const token = await businessMsal.acquireTokenSilent({ scopes });
				host = await fetchOneDriveHost(token.accessToken);
			}
			scopes = [`${host.replace(/\/$/, '')}/.default`];
			await businessMsal.acquireTokenSilent({ scopes });
		} catch {
			businessLogin = { scopes };
			throw new Error('Sign in to OneDrive again to finish opening the picker.');
		}
	})().finally(() => {
		preparingBusiness = undefined;
	});
	return preparingBusiness;
}

export function beginBusinessDocumentPicker(): Promise<{
	references: DocumentReference[];
	account: AccountInfo;
}> {
	if (!businessMsal || preparingBusiness) {
		void prepareBusinessDocumentPicker().catch(() => {});
		throw new Error('OneDrive is loading. Open the menu and try again.');
	}
	const msal = businessMsal;
	// No await before loginPopup: Safari requires the original click gesture.
	const login = businessLogin ? msal.loginPopup(businessLogin) : Promise.resolve(null);
	return login.then(async (result) => {
		if (result?.account) msal.setActiveAccount(result.account);
		businessLogin = undefined;
		const account = msal.getActiveAccount();
		if (!account) throw new Error('Sign in to OneDrive again to finish opening the picker.');
		const references = await pickDocumentReferencesModal();
		if (msal.getActiveAccount()?.homeAccountId !== account.homeAccountId) {
			throw new Error('The picker account changed. Open the picker again.');
		}
		return { references, account };
	});
}
