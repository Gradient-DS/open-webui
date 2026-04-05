<script lang="ts">
	import Fuse from 'fuse.js';
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import { PaneGroup, Pane, PaneResizer } from 'paneforge';

	dayjs.extend(relativeTime);

	import { onMount, getContext, onDestroy, tick } from 'svelte';
	const i18n = getContext('i18n');

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		mobile,
		showSidebar,
		knowledge as _knowledge,
		config,
		user,
		settings,
		socket
	} from '$lib/stores';

	import {
		updateFileDataContentById,
		uploadFile,
		deleteFileById,
		getFileById
	} from '$lib/apis/files';
	import {
		addFileToKnowledgeById,
		getKnowledgeById,
		removeFileFromKnowledgeById,
		resetKnowledgeById,
		updateFileFromKnowledgeById,
		updateKnowledgeById,
		updateKnowledgeAccessGrants,
		searchKnowledgeFilesById
	} from '$lib/apis/knowledge';
	import { processWeb, processYoutubeVideo } from '$lib/apis/retrieval';
	import { createSyncApi, type SyncStatusResponse, type SyncErrorType, type FailedFile } from '$lib/apis/sync';
	import { startOneDriveSyncItems, type SyncItem as OneDriveSyncItem } from '$lib/apis/onedrive';
	import { openOneDriveItemPicker, getGraphApiToken } from '$lib/utils/onedrive-file-picker';
	import {
		startGoogleDriveSyncItems,
		type SyncItem as GoogleDriveSyncItem,
	} from '$lib/apis/googledrive';
	import { createKnowledgePicker } from '$lib/utils/google-drive-picker';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import { blobToFile, isYoutubeUrl } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Files from './KnowledgeBase/Files.svelte';
	import SourceGroupedFiles from './KnowledgeBase/SourceGroupedFiles.svelte';
	import AddFilesPlaceholder from '$lib/components/AddFilesPlaceholder.svelte';

	import AddContentMenu from './KnowledgeBase/AddContentMenu.svelte';
	import AddTextContentModal from './KnowledgeBase/AddTextContentModal.svelte';
	import EmptyStateCards from './KnowledgeBase/EmptyStateCards.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	import SyncConfirmDialog from '../../common/ConfirmDialog.svelte';
	import Drawer from '$lib/components/common/Drawer.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import FilesOverlay from '$lib/components/chat/MessageInput/FilesOverlay.svelte';
	import DropdownOptions from '$lib/components/common/DropdownOptions.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import AttachWebpageModal from '$lib/components/chat/MessageInput/AttachWebpageModal.svelte';

	// ===== Cloud sync provider configuration =====

	interface CloudSyncProvider {
		type: string;                    // "onedrive" | "google_drive"
		metaKey: string;                 // "onedrive_sync" | "google_drive_sync"
		eventPrefix: string;             // "onedrive" | "googledrive"
		fileIdPrefix: string;            // "onedrive-" | "googledrive-"
		sourceMetaField: string;         // "onedrive" | "google_drive" (for file.meta.source)
		label: string;                   // "OneDrive" | "Google Drive"
		api: ReturnType<typeof createSyncApi>;
		startSyncParam: string;          // "start_onedrive_sync" | "start_google_drive_sync"
		authCallbackType: string;        // "onedrive_auth_callback" | "google_drive_auth_callback"
		authBasePath: string;            // "onedrive" | "google-drive" (for auth URL)
		authPopupName: string;           // "onedrive_auth" | "google_drive_auth"
		configKey: string;               // "onedrive" | "google_drive" (for $config?.xxx?.has_client_secret)
	}

	const CLOUD_PROVIDERS: Record<string, CloudSyncProvider> = {
		onedrive: {
			type: 'onedrive',
			metaKey: 'onedrive_sync',
			eventPrefix: 'onedrive',
			fileIdPrefix: 'onedrive-',
			sourceMetaField: 'onedrive',
			label: 'OneDrive',
			api: createSyncApi('onedrive'),
			startSyncParam: 'start_onedrive_sync',
			authCallbackType: 'onedrive_auth_callback',
			authBasePath: 'onedrive',
			authPopupName: 'onedrive_auth',
			configKey: 'onedrive',
		},
		google_drive: {
			type: 'google_drive',
			metaKey: 'google_drive_sync',
			eventPrefix: 'googledrive',
			fileIdPrefix: 'googledrive-',
			sourceMetaField: 'google_drive',
			label: 'Google Drive',
			api: createSyncApi('google-drive'),
			startSyncParam: 'start_google_drive_sync',
			authCallbackType: 'google_drive_auth_callback',
			authBasePath: 'google-drive',
			authPopupName: 'google_drive_auth',
			configKey: 'google_drive',
		},
	};

	// ===== Unified cloud sync state =====

	let cloudSyncState: Record<string, {
		isSyncing: boolean;
		isCancelling: boolean;
		syncStatus: SyncStatusResponse | null;
		bgSyncAuthorized: boolean;
		bgSyncNeedsReauth: boolean;
		refreshDone: boolean;
	}> = {
		onedrive: { isSyncing: false, isCancelling: false, syncStatus: null, bgSyncAuthorized: false, bgSyncNeedsReauth: false, refreshDone: false },
		google_drive: { isSyncing: false, isCancelling: false, syncStatus: null, bgSyncAuthorized: false, bgSyncNeedsReauth: false, refreshDone: false },
	};

	$: isSyncBusy = Object.values(cloudSyncState).some(s => s.isSyncing || s.isCancelling);
	$: activeProvider = knowledge?.type ? CLOUD_PROVIDERS[knowledge.type] ?? null : null;
	$: activeState = activeProvider ? cloudSyncState[activeProvider.type] : null;

	let largeScreen = true;

	let pane;
	let showSidepanel = true;

	let showAddWebpageModal = false;
	let showAddTextContentModal = false;

	let showSyncConfirmModal = false;
	let showCancelSyncConfirmModal = false;
	let showAccessControlModal = false;

	let minSize = 0;
	type Knowledge = {
		id: string;
		name: string;
		description: string;
		data: {
			file_ids: string[];
		};
		files: any[];
		access_grants?: any[];
		write_access?: boolean;
	};

	let id = null;
	let knowledge: Knowledge | null = null;
	let knowledgeId = null;

	let selectedFileId = null;
	let selectedFile = null;
	let selectedFileContent = '';

	let inputFiles = null;

	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let viewOption = null;
	let sortKey = null;
	let direction = null;

	let currentPage = 1;
	let fileItems = null;
	let fileItemsTotal = null;

	let loaded = false;
	let queryDebounceActive = false;
	let fetchId = 0;

	const reset = () => {
		currentPage = 1;
	};

	const init = async () => {
		reset();
		await getItemsPage();
	};

	// Consolidated reactive block — mirrors Knowledge.svelte list view pattern
	$: if (loaded && knowledgeId !== null) {
		// Track all dependencies explicitly
		void query, viewOption, sortKey, direction, currentPage;

		if (queryDebounceActive) {
			// User is typing — debounce
			clearTimeout(searchDebounceTimer);
			searchDebounceTimer = setTimeout(() => {
				reset();
				getItemsPage();
			}, 300);
		} else {
			// Filter/view/pagination change or initial load — fetch immediately
			getItemsPage();
		}
	}

	const getItemsPage = async () => {
		if (knowledgeId === null) return;

		// Don't null items — keep showing stale data during re-fetch
		const currentFetchId = ++fetchId;

		if (sortKey === null) {
			direction = null;
		}

		const isExternalSync = knowledge?.type === 'onedrive' || knowledge?.type === 'google_drive';
		const res = await searchKnowledgeFilesById(
			localStorage.token,
			knowledge.id,
			query,
			viewOption,
			sortKey,
			direction,
			currentPage,
			isExternalSync ? 250 : null
		).catch(() => {
			return null;
		});

		if (currentFetchId !== fetchId) return; // Stale response, discard

		if (res) {
			fileItems = res.items;
			fileItemsTotal = res.total;
		}
		queryDebounceActive = false;
		return res;
	};

	const fileSelectHandler = async (file) => {
		try {
			selectedFile = file;
			selectedFileContent = selectedFile?.data?.content || '';
		} catch (e) {
			toast.error($i18n.t('Failed to load file content.'));
		}
	};

	const createFileFromText = (name, content) => {
		const blob = new Blob([content], { type: 'text/plain' });
		const file = blobToFile(blob, `${name}.txt`);

		console.log(file);
		return file;
	};

	const uploadWeb = async (urls) => {
		if (!Array.isArray(urls)) {
			urls = [urls];
		}

		const newFileItems = urls.map((url) => ({
			type: 'file',
			file: '',
			id: null,
			url: url,
			name: url,
			size: null,
			status: 'uploading',
			error: '',
			itemId: uuidv4()
		}));

		// Display all items at once
		fileItems = [...newFileItems, ...(fileItems ?? [])];

		for (const fileItem of newFileItems) {
			try {
				console.log(fileItem);
				const res = await processWeb(localStorage.token, '', fileItem.url, false).catch((e) => {
					console.error('Error processing web URL:', e);
					return null;
				});

				if (res) {
					console.log(res);
					const file = createFileFromText(
						// Use URL as filename, sanitized
						fileItem.url
							.replace(/[^a-z0-9]/gi, '_')
							.toLowerCase()
							.slice(0, 50),
						res.content
					);

					const uploadedFile = await uploadFile(localStorage.token, file).catch((e) => {
						toast.error(`${e}`);
						return null;
					});

					if (uploadedFile) {
						console.log(uploadedFile);
						fileItems = fileItems.map((item) => {
							if (item.itemId === fileItem.itemId) {
								item.id = uploadedFile.id;
							}
							return item;
						});

						if (uploadedFile.error) {
							console.warn('File upload warning:', uploadedFile.error);
							toast.warning(uploadedFile.error);
							fileItems = fileItems.filter((file) => file.id !== uploadedFile.id);
						} else {
							await addFileHandler(uploadedFile.id);
						}
					} else {
						toast.error($i18n.t('Failed to upload file.'));
					}
				} else {
					// remove the item from fileItems
					fileItems = fileItems.filter((item) => item.itemId !== fileItem.itemId);
					toast.error($i18n.t('Failed to process URL: {{url}}', { url: fileItem.url }));
				}
			} catch (e) {
				// remove the item from fileItems
				fileItems = fileItems.filter((item) => item.itemId !== fileItem.itemId);
				toast.error(`${e}`);
			}
		}
	};

	const uploadFileHandler = async (file) => {
		console.log(file);

		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: '',
			name: file.name,
			size: file.size,
			status: 'uploading',
			error: '',
			itemId: uuidv4()
		};

		if (fileItem.size == 0) {
			toast.error($i18n.t('You cannot upload an empty file.'));
			return null;
		}

		if (
			($config?.file?.max_size ?? null) !== null &&
			file.size > ($config?.file?.max_size ?? 0) * 1024 * 1024
		) {
			console.log('File exceeds max size limit:', {
				fileSize: file.size,
				maxSize: ($config?.file?.max_size ?? 0) * 1024 * 1024
			});
			toast.error(
				$i18n.t(`File size should not exceed {{maxSize}} MB.`, {
					maxSize: $config?.file?.max_size
				})
			);
			return;
		}

		fileItems = [fileItem, ...(fileItems ?? [])];
		try {
			let metadata = {
				knowledge_id: knowledge.id,
				// If the file is an audio file, provide the language for STT.
				...((file.type.startsWith('audio/') || file.type.startsWith('video/')) &&
				$settings?.audio?.stt?.language
					? {
							language: $settings?.audio?.stt?.language
						}
					: {})
			};

			const uploadedFile = await uploadFile(localStorage.token, file, metadata).catch((e) => {
				toast.error(`${e}`);
				return null;
			});

			if (uploadedFile) {
				console.log(uploadedFile);
				fileItems = fileItems.map((item) => {
					if (item.itemId === fileItem.itemId) {
						item.id = uploadedFile.id;
					}
					return item;
				});

				if (uploadedFile.error) {
					console.warn('File upload warning:', uploadedFile.error);
					toast.warning(uploadedFile.error);
					fileItems = fileItems.filter((file) => file.id !== uploadedFile.id);
				}
				// Don't call addFileHandler here — Socket.IO 'file:status' event
				// will trigger it when background processing completes
			} else {
				toast.error($i18n.t('Failed to upload file.'));
			}
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	// Uploads multiple files with bounded concurrency.
	// All paths (file input, drag-drop, directory) route through this.
	const uploadFiles = async (
		files: File[],
		options: { concurrency?: number; onProgress?: (completed: number, total: number) => void } = {}
	) => {
		const { concurrency = 5, onProgress } = options;
		const total = files.length;
		if (total === 0) return;

		let completed = 0;
		const executing: Set<Promise<void>> = new Set();

		for (const file of files) {
			const task = uploadFileHandler(file).then(() => {
				completed++;
				executing.delete(task);
				onProgress?.(completed, total);
			});
			executing.add(task);

			if (executing.size >= concurrency) {
				await Promise.race(executing);
			}
		}

		await Promise.all(executing);
	};

	const uploadDirectoryHandler = async () => {
		// Check if File System Access API is supported
		const isFileSystemAccessSupported = 'showDirectoryPicker' in window;

		try {
			if (isFileSystemAccessSupported) {
				// Modern browsers (Chrome, Edge) implementation
				await handleModernBrowserUpload();
			} else {
				// Firefox fallback
				await handleFirefoxUpload();
			}
		} catch (error) {
			handleUploadError(error);
		}
	};

	// Helper function to check if a path contains hidden folders
	const hasHiddenFolder = (path) => {
		return path.split('/').some((part) => part.startsWith('.'));
	};

	// Recursively collects all non-hidden files from a directory handle
	async function collectDirectoryFiles(
		dirHandle: FileSystemDirectoryHandle,
		path = ''
	): Promise<File[]> {
		const files: File[] = [];
		for await (const entry of dirHandle.values()) {
			if (entry.name.startsWith('.')) continue;
			const entryPath = path ? `${path}/${entry.name}` : entry.name;
			if (hasHiddenFolder(entryPath)) continue;

			if (entry.kind === 'file') {
				const file = await entry.getFile();
				files.push(new File([file], entryPath, { type: file.type }));
			} else if (entry.kind === 'directory') {
				files.push(...(await collectDirectoryFiles(entry, entryPath)));
			}
		}
		return files;
	}

	// Collects all File objects from a DataTransfer drop (files and recursive directories)
	async function collectDroppedFiles(items: DataTransferItemList): Promise<File[]> {
		const files: File[] = [];

		const readEntry = (entry: FileSystemEntry): Promise<void> => {
			return new Promise((resolve) => {
				if (entry.isFile) {
					(entry as FileSystemFileEntry).file((file) => {
						files.push(file);
						resolve();
					});
				} else if (entry.isDirectory) {
					const reader = (entry as FileSystemDirectoryEntry).createReader();
					reader.readEntries(async (entries) => {
						await Promise.all(entries.map(readEntry));
						resolve();
					}, () => resolve());
				} else {
					resolve();
				}
			});
		};

		const entries: FileSystemEntry[] = [];
		for (let i = 0; i < items.length; i++) {
			const entry = items[i].webkitGetAsEntry();
			if (entry) entries.push(entry);
		}

		await Promise.all(entries.map(readEntry));
		return files;
	}

	// Modern browsers implementation using File System Access API
	const handleModernBrowserUpload = async () => {
		const dirHandle = await window.showDirectoryPicker();
		const files = await collectDirectoryFiles(dirHandle);

		if (files.length === 0) {
			console.log('No files to upload.');
			return;
		}

		toast.info(
			$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
				uploadedFiles: 0,
				totalFiles: files.length,
				percentage: '0.00'
			})
		);

		await uploadFiles(files, {
			onProgress: (done, total) => {
				const percentage = ((done / total) * 100).toFixed(2);
				toast.info(
					$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
						uploadedFiles: done,
						totalFiles: total,
						percentage
					})
				);
			}
		});
	};

	// Firefox fallback implementation using traditional file input
	const handleFirefoxUpload = async () => {
		return new Promise<void>((resolve, reject) => {
			const input = document.createElement('input');
			input.type = 'file';
			input.webkitdirectory = true;
			input.directory = true;
			input.multiple = true;
			input.style.display = 'none';
			document.body.appendChild(input);

			input.onchange = async () => {
				try {
					const files = Array.from(input.files)
						.filter((file) => !hasHiddenFolder(file.webkitRelativePath))
						.filter((file) => !file.name.startsWith('.'))
						.map((file) => {
							const relativePath = file.webkitRelativePath || file.name;
							return new File([file], relativePath, { type: file.type });
						});

					if (files.length > 0) {
						toast.info(
							$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
								uploadedFiles: 0,
								totalFiles: files.length,
								percentage: '0.00'
							})
						);

						await uploadFiles(files, {
							onProgress: (done, total) => {
								const percentage = ((done / total) * 100).toFixed(2);
								toast.info(
									$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
										uploadedFiles: done,
										totalFiles: total,
										percentage
									})
								);
							}
						});
					}

					document.body.removeChild(input);
					resolve();
				} catch (error) {
					reject(error);
				}
			};

			input.onerror = (error) => {
				document.body.removeChild(input);
				reject(error);
			};

			input.click();
		});
	};

	// Error handler
	const handleUploadError = (error) => {
		if (error.name === 'AbortError') {
			toast.info($i18n.t('Directory selection was cancelled'));
		} else {
			toast.error($i18n.t('Error accessing directory'));
			console.error('Directory access error:', error);
		}
	};

	// Helper function to maintain file paths within zip
	const syncDirectoryHandler = async () => {
		if (fileItems.length > 0) {
			const res = await resetKnowledgeById(localStorage.token, id).catch((e) => {
				toast.error(`${e}`);
			});

			if (res) {
				fileItems = [];
				toast.success($i18n.t('Knowledge reset successfully.'));

				// Upload directory
				uploadDirectoryHandler();
			}
		} else {
			uploadDirectoryHandler();
		}
	};

	// ===== Generic cloud sync handlers =====

	const cloudSyncHandler = async (provider: CloudSyncProvider) => {
		const state = cloudSyncState[provider.type];
		try {
			state.isSyncing = true;
			cloudSyncState = cloudSyncState;

			let syncItems: any[];
			let accessToken: string;

			// Provider-specific picker logic
			if (provider.type === 'onedrive') {
				const items = await openOneDriveItemPicker('organizations');
				if (!items || items.length === 0) {
					state.isSyncing = false;
					cloudSyncState = cloudSyncState;
					return;
				}

				accessToken = await getGraphApiToken('organizations');

				syncItems = items.map(item => ({
					type: item.type,
					drive_id: item.driveId,
					item_id: item.id,
					item_path: item.path,
					name: item.name
				}));

				state.refreshDone = false;
				cloudSyncState = cloudSyncState;
				await startOneDriveSyncItems(localStorage.token, {
					knowledge_id: knowledge.id,
					items: syncItems as OneDriveSyncItem[],
					access_token: accessToken,
					user_token: localStorage.token
				});
			} else if (provider.type === 'google_drive') {
				const result = await createKnowledgePicker(knowledge.id);
				if (!result) {
					state.isSyncing = false;
					cloudSyncState = cloudSyncState;
					return;
				}

				syncItems = result.items.map(item => ({
					type: item.type,
					item_id: item.id,
					item_path: item.path,
					name: item.name
				}));

				state.refreshDone = false;
				cloudSyncState = cloudSyncState;
				await startGoogleDriveSyncItems(localStorage.token, {
					knowledge_id: knowledge.id,
					items: syncItems as GoogleDriveSyncItem[],
				});
			}

			// Refresh knowledge to get updated sources
			const updatedKnowledge = await getKnowledgeById(localStorage.token, id);
			if (updatedKnowledge) {
				knowledge = updatedKnowledge;
			}

			toast.success($i18n.t('{{label}} sync started', { label: provider.label }));
			pollCloudSyncStatus(provider);
		} catch (error) {
			console.error(`${provider.label} sync error:`, error);
			toast.error($i18n.t('Failed to sync from {{label}}: ', { label: provider.label }) + (error instanceof Error ? error.message : String(error)));
			state.isSyncing = false;
			cloudSyncState = cloudSyncState;
		}
	};

	const cloudResyncHandler = async (provider: CloudSyncProvider) => {
		const state = cloudSyncState[provider.type];
		const sources = knowledge?.meta?.[provider.metaKey]?.sources;
		if (!sources?.length) return;

		try {
			state.isSyncing = true;
			cloudSyncState = cloudSyncState;

			if (provider.type === 'onedrive') {
				const accessToken = await getGraphApiToken('organizations');
				const syncItems: OneDriveSyncItem[] = sources.map((source: any) => ({
					type: source.type,
					drive_id: source.drive_id,
					item_id: source.item_id,
					item_path: source.item_path,
					name: source.name
				}));

				state.refreshDone = false;
				cloudSyncState = cloudSyncState;
				await startOneDriveSyncItems(localStorage.token, {
					knowledge_id: knowledge.id,
					items: syncItems,
					access_token: accessToken,
					user_token: localStorage.token
				});
			} else if (provider.type === 'google_drive') {
				const syncItems: GoogleDriveSyncItem[] = sources.map((source: any) => ({
					type: source.type,
					item_id: source.item_id,
					item_path: source.item_path,
					name: source.name
				}));

				state.refreshDone = false;
				cloudSyncState = cloudSyncState;
				await startGoogleDriveSyncItems(localStorage.token, {
					knowledge_id: knowledge.id,
					items: syncItems,
				});
			}

			toast.success($i18n.t('{{label}} sync started', { label: provider.label }));
			pollCloudSyncStatus(provider);
		} catch (error) {
			console.error(`${provider.label} resync error:`, error);
			toast.error($i18n.t('Failed to start sync: ') + (error instanceof Error ? error.message : String(error)));
			state.isSyncing = false;
			cloudSyncState = cloudSyncState;
		}
	};

	const pollCloudSyncStatus = async (provider: CloudSyncProvider) => {
		const state = cloudSyncState[provider.type];
		try {
			state.syncStatus = await provider.api.getSyncStatus(localStorage.token, knowledge.id);
			cloudSyncState = cloudSyncState;

			if (state.syncStatus.status === 'syncing') {
				setTimeout(() => pollCloudSyncStatus(provider), 2000);
			} else if (state.syncStatus.status === 'file_limit_exceeded') {
				toast.error(state.syncStatus.error || $i18n.t('File limit exceeded'));
				state.isSyncing = false;
				cloudSyncState = cloudSyncState;
				if (!state.refreshDone) {
					state.refreshDone = true;
					cloudSyncState = cloudSyncState;
					await init();
				}
			} else if (state.syncStatus.status === 'access_revoked') {
				// access_revoked is a transient status during sync, keep polling
				setTimeout(() => pollCloudSyncStatus(provider), 2000);
			} else if (state.syncStatus.status === 'completed' || state.syncStatus.status === 'completed_with_errors') {
				// Toast is handled by Socket.IO handler, just refresh
				state.isSyncing = false;
				cloudSyncState = cloudSyncState;
				if (!state.refreshDone) {
					state.refreshDone = true;
					cloudSyncState = cloudSyncState;
					await init();
				}
			} else if (state.syncStatus.status === 'failed') {
				// Only show error if Socket.IO didn't already handle it
				if (state.isSyncing) {
					toast.error($i18n.t('{{label}} sync failed: {{error}}', { label: provider.label, error: state.syncStatus.error }));
					state.isSyncing = false;
					cloudSyncState = cloudSyncState;
				}
			} else if (state.syncStatus.status === 'cancelled') {
				state.isSyncing = false;
				state.isCancelling = false;
				cloudSyncState = cloudSyncState;
				if (!state.refreshDone) {
					state.refreshDone = true;
					cloudSyncState = cloudSyncState;
					await init();
				}
			}
		} catch (error) {
			console.error(`Failed to get ${provider.label} sync status:`, error);
		}
	};

	const cancelCloudSyncHandler = async (provider: CloudSyncProvider) => {
		const state = cloudSyncState[provider.type];
		try {
			state.isCancelling = true;
			cloudSyncState = cloudSyncState;
			await provider.api.cancelSync(localStorage.token, knowledge.id);
			toast.info($i18n.t('Cancelling {{label}} sync...', { label: provider.label }));
		} catch (error) {
			console.error(`Failed to cancel ${provider.label} sync:`, error);
			toast.error($i18n.t('Failed to cancel sync: ') + (error instanceof Error ? error.message : String(error)));
			state.isCancelling = false;
			cloudSyncState = cloudSyncState;
		}
	};

	// Helper to get user-friendly error type message
	const getErrorTypeMessage = (errorType: SyncErrorType): string => {
		switch (errorType) {
			case 'timeout':
				return $i18n.t('Processing timeout');
			case 'empty_content':
				return $i18n.t('Empty file');
			case 'processing_error':
				return $i18n.t('Processing error');
			case 'download_error':
				return $i18n.t('Download failed');
			default:
				return $i18n.t('Error');
		}
	};

	// Format failed files for display in toast
	const formatFailedFilesMessage = (failedFiles: FailedFile[]): string => {
		if (!failedFiles || failedFiles.length === 0) return '';

		// Show up to 3 failed files
		const maxToShow = 3;
		const filesToShow = failedFiles.slice(0, maxToShow);
		const remaining = failedFiles.length - maxToShow;

		const lines = filesToShow.map(
			(f) => `- ${f.filename}: ${getErrorTypeMessage(f.error_type)}`
		);

		if (remaining > 0) {
			lines.push($i18n.t('and {{COUNT}} more', { COUNT: remaining }));
		}

		return '\n' + lines.join('\n');
	};

	// ===== Generic socket handlers =====

	function handleCloudFileProcessing(providerType: string, data: {
		knowledge_id: string;
		file: {
			item_id: string;
			name: string;
			size?: number;
			source_item_id?: string;
			relative_path?: string;
		};
	}) {
		const provider = CLOUD_PROVIDERS[providerType];
		const state = cloudSyncState[providerType];

		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) return;

		// Ignore new file events when cancellation is in progress
		if (state.isCancelling) return;

		// Use provider-specific prefix for file ID
		const fileId = `${provider.fileIdPrefix}${data.file.item_id}`;

		// Check if file is already in the list
		if (fileItems?.some((f) => f.id === fileId || f.itemId === fileId)) return;

		// Add the file to the list with 'uploading' status
		const newFileItem = {
			type: 'file',
			file: null,
			id: fileId,
			url: '',
			name: data.file.name,
			size: data.file.size || 0,
			status: 'uploading',
			error: '',
			itemId: fileId,
			meta: {
				source_item_id: data.file.source_item_id,
				source: provider.sourceMetaField,
				relative_path: data.file.relative_path
			}
		};

		// Add to beginning of list
		fileItems = [newFileItem, ...(fileItems ?? [])];
		fileItemsTotal = (fileItemsTotal ?? 0) + 1;

		console.log(`${provider.label} file processing started:`, data.file.name);
	}

	function handleCloudFileAdded(providerType: string, data: {
		knowledge_id: string;
		file: {
			id: string;
			filename: string;
			meta?: {
				name?: string;
				content_type?: string;
				size?: number;
				source?: string;
				source_item_id?: string;
				relative_path?: string;
			};
			created_at?: number;
			updated_at?: number;
		};
	}) {
		const state = cloudSyncState[providerType];
		const provider = CLOUD_PROVIDERS[providerType];

		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) return;

		// Ignore new file events when cancellation is in progress
		if (state.isCancelling) return;

		// Find existing file item and update status
		const idx = fileItems?.findIndex((f) => f.id === data.file.id);
		if (idx !== undefined && idx >= 0 && fileItems) {
			// Update existing item to 'uploaded' status
			fileItems[idx].status = 'uploaded';
			fileItems[idx].file = data.file;
			fileItems[idx].name = data.file.filename;
			fileItems[idx].size = data.file.meta?.size || fileItems[idx].size;
			fileItems[idx].meta = data.file.meta;
			fileItems = fileItems; // Trigger reactivity
			console.log(`${provider.label} file completed:`, data.file.filename);
		} else {
			// File not in list yet (edge case), add it
			const newFileItem = {
				type: 'file',
				file: data.file,
				id: data.file.id,
				url: '',
				name: data.file.filename,
				size: data.file.meta?.size || 0,
				status: 'uploaded',
				error: '',
				itemId: data.file.id,
				meta: data.file.meta
			};
			fileItems = [newFileItem, ...(fileItems ?? [])];
			fileItemsTotal = (fileItemsTotal ?? 0) + 1;
			console.log(`${provider.label} file added:`, data.file.filename);
		}
	}

	async function handleCloudSyncProgress(providerType: string, data: {
		knowledge_id: string;
		status: string;
		current: number;
		total: number;
		filename: string;
		error?: string;
		files_processed?: number;
		files_failed?: number;
		deleted_count?: number;
		failed_files?: FailedFile[];
	}) {
		const state = cloudSyncState[providerType];
		const provider = CLOUD_PROVIDERS[providerType];

		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) return;

		// Update sync status
		state.syncStatus = {
			knowledge_id: data.knowledge_id,
			status: data.status as SyncStatusResponse['status'],
			progress_current: data.current,
			progress_total: data.total,
			error: data.error,
			failed_files: data.failed_files
		};
		cloudSyncState = cloudSyncState;

		// Handle access revoked
		if (data.status === 'access_revoked') {
			toast.warning(data.error || $i18n.t('Access to a {{label}} source has been revoked', { label: provider.label }));
		}

		// Handle file limit exceeded
		if (data.status === 'file_limit_exceeded') {
			toast.error(data.error || $i18n.t('File limit exceeded'));
			state.isSyncing = false;
			state.refreshDone = true;
			cloudSyncState = cloudSyncState;
			await init();
			return;
		}

		// Handle completion states
		if (data.status === 'completed' || data.status === 'completed_with_errors') {
			const count = data.files_processed ?? 0;
			const failed = data.files_failed ?? 0;
			if (count > 0 && failed > 0) {
				const failedDetails = data.failed_files
					? formatFailedFilesMessage(data.failed_files)
					: '';
				toast.warning(
					$i18n.t('Synced {{count}} files from {{label}} ({{failed}} failed)', {
						count,
						failed,
						label: provider.label
					}) + failedDetails
				);
			} else if (count > 0) {
				toast.success($i18n.t('Synced {{count}} files from {{label}}', { count, label: provider.label }));
			} else if (failed > 0) {
				const failedDetails = data.failed_files
					? formatFailedFilesMessage(data.failed_files)
					: '';
				toast.error(
					$i18n.t('{{label}} sync failed: all {{failed}} files failed to process', {
						failed,
						label: provider.label
					}) + failedDetails
				);
			} else {
				toast.success($i18n.t('{{label}} sync completed - no changes', { label: provider.label }));
			}
			state.isSyncing = false;
			state.refreshDone = true;
			cloudSyncState = cloudSyncState;
			// Refresh knowledge metadata to update last_sync_at timestamp
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
			await init(); // Refresh file list
		} else if (data.status === 'failed') {
			toast.error($i18n.t('{{label}} sync failed: {{error}}', { label: provider.label, error: data.error || 'Unknown error' }));
			state.isSyncing = false;
			cloudSyncState = cloudSyncState;
		} else if (data.status === 'cancelled') {
			toast.info($i18n.t('{{label}} sync cancelled', { label: provider.label }));
			state.isSyncing = false;
			state.isCancelling = false;
			state.refreshDone = true;
			cloudSyncState = cloudSyncState;
			// Refresh knowledge metadata to update last_sync_at timestamp
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
			await init(); // Refresh file list
		}
	}

	const authorizeBackgroundSync = async (provider: CloudSyncProvider) => {
		const state = cloudSyncState[provider.type];
		const authUrl = `${WEBUI_API_BASE_URL}/${provider.authBasePath}/auth/initiate?knowledge_id=${knowledge.id}`;

		// Open popup
		const popup = window.open(
			authUrl,
			provider.authPopupName,
			'width=600,height=700,scrollbars=yes'
		);

		let messageReceived = false;

		// Listen for postMessage from callback
		const handleMessage = (event: MessageEvent) => {
			if (event.data?.type !== provider.authCallbackType) return;

			messageReceived = true;
			window.removeEventListener('message', handleMessage);

			if (event.data.success) {
				state.bgSyncAuthorized = true;
				state.bgSyncNeedsReauth = false;
				cloudSyncState = cloudSyncState;
				toast.success($i18n.t('Background sync authorized'));
			} else {
				toast.error($i18n.t('Authorization failed: {{error}}', { error: event.data.error }));
			}
		};

		window.addEventListener('message', handleMessage);

		// When popup closes, check token status as fallback (postMessage may fail due to origin mismatch)
		const checkClosed = setInterval(async () => {
			if (popup?.closed) {
				clearInterval(checkClosed);
				window.removeEventListener('message', handleMessage);

				if (!messageReceived) {
					try {
						const status = await provider.api.getTokenStatus(localStorage.token, knowledge.id);
						if (status.has_token && !status.is_expired) {
							state.bgSyncAuthorized = true;
							state.bgSyncNeedsReauth = false;
							cloudSyncState = cloudSyncState;
							toast.success($i18n.t('Background sync authorized'));
						}
					} catch (e) {
						console.warn(`Failed to check ${provider.label} background sync token status:`, e);
						toast.error($i18n.t('Failed to check background sync status'));
					}
				}
			}
		}, 500);
	};

	const removeCloudSourceHandler = async (provider: CloudSyncProvider, itemId: string, sourceName: string) => {
		try {
			const result = await provider.api.removeSource(localStorage.token, knowledge.id, itemId);
			toast.success($i18n.t('Source "{{name}}" removed. {{count}} file(s) cleaned up.', {
				name: result.source_name,
				count: result.files_removed
			}));
			// Refresh knowledge metadata and file list
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
			await init();
		} catch (e) {
			console.error(`Error removing ${provider.label} source:`, e);
			toast.error($i18n.t('Failed to remove source: {{error}}', {
				error: e instanceof Error ? e.message : String(e)
			}));
		}
	};

	let successfulFileCount = 0;
	let fileStatusQueue: Promise<void> = Promise.resolve();

	const showBatchedSuccessToast = () => {
		if (successfulFileCount > 0) {
			const count = successfulFileCount;
			successfulFileCount = 0;
			toast.success(
				count === 1
					? $i18n.t('File added successfully.')
					: $i18n.t('{{count}} files added successfully.', { count: count.toString() })
			);
			init();
		}
	};

	// Serialize socket events via a queue to prevent concurrent state mutations
	const handleFileStatus = (data: {
		file_id: string;
		status: string;
		error?: string;
		collection_name?: string;
	}) => {
		fileStatusQueue = fileStatusQueue.then(() => _processFileStatus(data));
	};

	const _processFileStatus = async (data: {
		file_id: string;
		status: string;
		error?: string;
		collection_name?: string;
	}) => {
		if (!fileItems) return;

		const idx = fileItems.findIndex((f) => f.id === data.file_id);
		if (idx < 0) return;

		if (data.status === 'completed') {
			fileItems[idx].status = 'uploaded';
			await addFileHandler(data.file_id, { batch: true });
			successfulFileCount++;
		} else if (data.status === 'failed') {
			fileItems[idx].status = 'error';
			fileItems[idx].error = data.error || 'Processing failed';
			toast.error(
				$i18n.t('File processing failed: {{error}}', {
					error: data.error || 'Unknown error'
				})
			);
			fileItems = fileItems.filter((file) => file.id !== data.file_id);
		}

		fileItems = fileItems;

		const stillUploading = fileItems.some((f) => f.status === 'uploading');
		if (!stillUploading) {
			showBatchedSuccessToast();
		}
	};

	const addFileHandler = async (fileId, { batch = false } = {}) => {
		const res = await addFileToKnowledgeById(localStorage.token, id, fileId).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			if (res.warning) {
				toast.warning(res.warning);
			}
			if (batch) {
				// Success toast + init() deferred to showBatchedSuccessToast
			} else {
				toast.success($i18n.t('File added successfully.'));
				if (res.knowledge) {
					knowledge = res.knowledge;
				}
			}
		} else {
			toast.error($i18n.t('Failed to add file.'));
			fileItems = fileItems.filter((file) => file.id !== fileId);
		}
	};

	const deleteFileHandler = async (fileId) => {
		try {
			console.log('Starting file deletion process for:', fileId);

			// Remove from knowledge base only
			const res = await removeFileFromKnowledgeById(localStorage.token, id, fileId);
			console.log('Knowledge base updated:', res);

			if (res) {
				toast.success($i18n.t('File removed successfully.'));
				await init();
			}
		} catch (e) {
			console.error('Error in deleteFileHandler:', e);
			toast.error(`${e}`);
		}
	};

	let debounceTimeout = null;
	let mediaQuery;
	let dragged = false;
	let isSaving = false;

	const updateFileContentHandler = async () => {
		if (isSaving) {
			console.log('Save operation already in progress, skipping...');
			return;
		}

		isSaving = true;

		try {
			const res = await updateFileDataContentById(
				localStorage.token,
				selectedFile.id,
				selectedFileContent
			).catch((e) => {
				toast.error(`${e}`);
				return null;
			});

			if (res) {
				toast.success($i18n.t('File content updated successfully.'));

				selectedFileId = null;
				selectedFile = null;
				selectedFileContent = '';

				await init();
			}
		} finally {
			isSaving = false;
		}
	};

	const changeDebounceHandler = () => {
		console.log('debounce');
		if (debounceTimeout) {
			clearTimeout(debounceTimeout);
		}

		debounceTimeout = setTimeout(async () => {
			if (knowledge.name.trim() === '' || knowledge.description.trim() === '') {
				toast.error($i18n.t('Please fill in all fields.'));
				return;
			}

			const res = await updateKnowledgeById(localStorage.token, id, {
				...knowledge,
				name: knowledge.name,
				description: knowledge.description,
				access_grants: knowledge.access_grants ?? []
			}).catch((e) => {
				toast.error(`${e}`);
			});

			if (res) {
				toast.success($i18n.t('Knowledge updated successfully'));
			}
		}, 1000);
	};

	const handleMediaQuery = async (e) => {
		if (e.matches) {
			largeScreen = true;
		} else {
			largeScreen = false;
		}
	};

	const onDragOver = (e) => {
		e.preventDefault();

		// Check if a file is being draggedOver.
		if (e.dataTransfer?.types?.includes('Files')) {
			dragged = true;
		} else {
			dragged = false;
		}
	};

	const onDragLeave = () => {
		dragged = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();
		dragged = false;

		if (!knowledge?.write_access) {
			toast.error($i18n.t('You do not have permission to upload files to this knowledge base.'));
			return;
		}

		if ($config?.integration_providers?.[knowledge?.type]) {
			toast.error($i18n.t('Files for this knowledge base are managed via the integration API.'));
			return;
		}

		if (e.dataTransfer?.types?.includes('Files') && e.dataTransfer?.items) {
			const inputItems = e.dataTransfer.items;
			if (inputItems.length > 0) {
				const files = await collectDroppedFiles(inputItems);
				if (files.length > 0) {
					await uploadFiles(files);
				}
			} else {
				toast.error($i18n.t(`File not found.`));
			}
		}
	};

	// ===== Socket event handler references (for cleanup) =====
	const socketHandlers: Array<{ event: string; handler: Function }> = [];

	onMount(async () => {
		// listen to resize 1024px
		mediaQuery = window.matchMedia('(min-width: 1024px)');

		mediaQuery.addEventListener('change', handleMediaQuery);
		handleMediaQuery(mediaQuery);

		// Select the container element you want to observe
		const container = document.getElementById('collection-container');

		// initialize the minSize based on the container width
		minSize = !largeScreen ? 100 : Math.floor((300 / container.clientWidth) * 100);

		// Create a new ResizeObserver instance
		const resizeObserver = new ResizeObserver((entries) => {
			for (let entry of entries) {
				const width = entry.contentRect.width;
				// calculate the percentage of 300
				const percentage = (300 / width) * 100;
				// set the minSize to the percentage, must be an integer
				minSize = !largeScreen ? 100 : Math.floor(percentage);

				if (showSidepanel) {
					if (pane && pane.isExpanded() && pane.getSize() < minSize) {
						pane.resize(minSize);
					}
				}
			}
		});

		// Start observing the container's size changes
		resizeObserver.observe(container);

		if (pane) {
			pane.expand();
		}

		id = $page.params.id;
		const res = await getKnowledgeById(localStorage.token, id).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			knowledge = res;
			if (!Array.isArray(knowledge?.access_grants)) {
				knowledge.access_grants = [];
			}
			knowledgeId = knowledge?.id;

			// Check background sync token status for cloud providers
			for (const provider of Object.values(CLOUD_PROVIDERS)) {
				if (knowledge?.type === provider.type) {
					try {
						const status = await provider.api.getTokenStatus(localStorage.token, knowledge.id);
						const state = cloudSyncState[provider.type];
						state.bgSyncAuthorized = status.has_token && !status.is_expired;
						state.bgSyncNeedsReauth = status.needs_reauth ?? false;
						cloudSyncState = cloudSyncState;
					} catch (e) {
						console.warn(`Failed to check ${provider.label} background sync token status:`, e);
						if (provider.type === 'onedrive') {
							toast.error($i18n.t('Failed to check background sync status'));
						}
					}
				}
			}

			// Resume sync UI if a sync is already in progress
			for (const provider of Object.values(CLOUD_PROVIDERS)) {
				if (knowledge?.meta?.[provider.metaKey]?.status === 'syncing') {
					const state = cloudSyncState[provider.type];
					state.isSyncing = true;
					state.refreshDone = false;
					cloudSyncState = cloudSyncState;
					pollCloudSyncStatus(provider);
				}
			}

			// Auto-start sync if directed from creation flow
			for (const provider of Object.values(CLOUD_PROVIDERS)) {
				if ($page.url.searchParams.get(provider.startSyncParam) === 'true' && knowledge) {
					const url = new URL(window.location.href);
					url.searchParams.delete(provider.startSyncParam);
					history.replaceState({}, '', url.toString());

					await tick();
					cloudSyncHandler(provider);
				}
			}
		} else {
			goto('/workspace/knowledge');
		}

		loaded = true;

		const dropZone = document.querySelector('body');
		dropZone?.addEventListener('dragover', onDragOver);
		dropZone?.addEventListener('drop', onDrop);
		dropZone?.addEventListener('dragleave', onDragLeave);

		// Register socket handlers for all cloud providers
		for (const provider of Object.values(CLOUD_PROVIDERS)) {
			const progressHandler = (data) => handleCloudSyncProgress(provider.type, data);
			const processingHandler = (data) => handleCloudFileProcessing(provider.type, data);
			const addedHandler = (data) => handleCloudFileAdded(provider.type, data);

			$socket?.on(`${provider.eventPrefix}:sync:progress`, progressHandler);
			$socket?.on(`${provider.eventPrefix}:file:processing`, processingHandler);
			$socket?.on(`${provider.eventPrefix}:file:added`, addedHandler);

			socketHandlers.push(
				{ event: `${provider.eventPrefix}:sync:progress`, handler: progressHandler },
				{ event: `${provider.eventPrefix}:file:processing`, handler: processingHandler },
				{ event: `${provider.eventPrefix}:file:added`, handler: addedHandler },
			);
		}

		// Listen for file processing status events via Socket.IO
		$socket?.on('file:status', handleFileStatus);
	});

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
		mediaQuery?.removeEventListener('change', handleMediaQuery);
		const dropZone = document.querySelector('body');
		dropZone?.removeEventListener('dragover', onDragOver);
		dropZone?.removeEventListener('drop', onDrop);
		dropZone?.removeEventListener('dragleave', onDragLeave);

		// Clean up all cloud provider socket listeners
		for (const { event, handler } of socketHandlers) {
			$socket?.off(event, handler);
		}

		// Clean up file status listener
		$socket?.off('file:status', handleFileStatus);

	});

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch (e) {
			return str;
		}
	};
</script>

<FilesOverlay show={dragged} />
<SyncConfirmDialog
	bind:show={showSyncConfirmModal}
	message={$i18n.t(
		'This will reset the knowledge base and sync all files. Do you wish to continue?'
	)}
	on:confirm={() => {
		syncDirectoryHandler();
	}}
/>

<SyncConfirmDialog
	bind:show={showCancelSyncConfirmModal}
	title={$i18n.t(activeProvider ? `Cancel ${activeProvider.label} Sync` : 'Cancel Sync')}
	message={$i18n.t('Are you sure you want to cancel the ongoing sync? Files already synced will be kept.')}
	confirmLabel={$i18n.t('Cancel Sync')}
	on:confirm={() => {
		if (activeProvider) {
			cancelCloudSyncHandler(activeProvider);
		}
	}}
/>

<AttachWebpageModal
	bind:show={showAddWebpageModal}
	onSubmit={async (e) => {
		uploadWeb(e.data);
	}}
/>

<AddTextContentModal
	bind:show={showAddTextContentModal}
	on:submit={(e) => {
		const file = createFileFromText(e.detail.name, e.detail.content);
		uploadFileHandler(file);
	}}
/>

<input
	id="files-input"
	bind:files={inputFiles}
	type="file"
	multiple
	hidden
	on:change={async () => {
		if (inputFiles && inputFiles.length > 0) {
			const sortedFiles = Array.from(inputFiles).sort((a, b) =>
				b.name.localeCompare(a.name)
			);
			await uploadFiles(sortedFiles);

			inputFiles = null;
			const fileInputElement = document.getElementById('files-input');

			if (fileInputElement) {
				fileInputElement.value = '';
			}
		} else {
			toast.error($i18n.t(`File not found.`));
		}
	}}
/>

<div class="flex flex-col w-full h-full min-h-0" id="collection-container">
	{#if id && knowledge && fileItems !== null}
		{#if knowledge?.type === 'local' || !knowledge?.type}
			<AccessControlModal
				bind:show={showAccessControlModal}
				bind:accessGrants={knowledge.access_grants}
				share={$user?.permissions?.sharing?.knowledge || $user?.role === 'admin'}
				sharePublic={$user?.permissions?.sharing?.public_knowledge || $user?.role === 'admin'}
				shareUsers={($user?.permissions?.access_grants?.allow_users ?? true) ||
					$user?.role === 'admin'}
				onChange={async () => {
					try {
						await updateKnowledgeAccessGrants(localStorage.token, id, knowledge.access_grants ?? []);
						toast.success($i18n.t('Saved'));
					} catch (error) {
						toast.error(`${error}`);
					}
				}}
				accessRoles={['read', 'write']}
			/>
		{/if}
		<div class="w-full px-2">
			<div class=" flex w-full">
				<div class="shrink-0 self-start mt-1.5 mr-1">
					<button
						class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						on:click={() => {
							goto('/workspace/knowledge');
						}}
					>
						<ChevronLeft className="size-4" strokeWidth="2.5" />
					</button>
				</div>
				<div class="flex-1">
					<div class="flex items-center justify-between w-full">
						<div class="w-full flex justify-between items-center">
							<input
								type="text"
								class="text-left w-full font-medium text-lg font-primary bg-transparent outline-hidden flex-1"
								bind:value={knowledge.name}
								aria-label={$i18n.t('Knowledge Name')}
								placeholder={$i18n.t('Knowledge Name')}
								disabled={!knowledge?.write_access}
								on:input={() => {
									changeDebounceHandler();
								}}
							/>

							<div class="shrink-0 mr-2.5 flex items-center gap-2">
								{#if activeProvider}
									<Badge type="info" content={$i18n.t(activeProvider.label)} />
								{:else if $config?.integration_providers?.[knowledge?.type]}
									<Badge
										type={$config.integration_providers[knowledge.type].badge_type}
										content={$config.integration_providers[knowledge.type].name}
									/>
								{:else}
									<Badge type="muted" content={$i18n.t('Local')} />
								{/if}
								{#if activeProvider && $config?.[activeProvider.configKey]?.has_client_secret}
									{#if activeState?.bgSyncNeedsReauth}
										<button
											class="text-xs text-red-500 hover:text-red-600 flex items-center gap-1"
											on:click={() => authorizeBackgroundSync(activeProvider)}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3.5">
												<path fill-rule="evenodd" d="M6.701 2.25c.577-1 2.02-1 2.598 0l5.196 9a1.5 1.5 0 0 1-1.299 2.25H2.804a1.5 1.5 0 0 1-1.3-2.25l5.197-9ZM8 4a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 8 4Zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" clip-rule="evenodd" />
											</svg>
											{$i18n.t('Re-authorize background sync')}
										</button>
									{:else if activeState?.bgSyncAuthorized}
										<span class="text-xs text-green-600 flex items-center gap-1">
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3.5">
												<path fill-rule="evenodd" d="M12.416 3.376a.75.75 0 0 1 .208 1.04l-5 7.5a.75.75 0 0 1-1.154.114l-3-3a.75.75 0 0 1 1.06-1.06l2.353 2.353 4.493-6.74a.75.75 0 0 1 1.04-.207Z" clip-rule="evenodd" />
											</svg>
											{$i18n.t('Background sync enabled')}
										</span>
									{:else if knowledge?.meta?.[activeProvider.metaKey]?.sources?.length}
										<button
											class="text-xs text-blue-500 hover:text-blue-600 flex items-center gap-1"
											on:click={() => authorizeBackgroundSync(activeProvider)}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3.5">
												<path fill-rule="evenodd" d="M8 1a3.5 3.5 0 0 0-3.5 3.5V7A1.5 1.5 0 0 0 3 8.5v5A1.5 1.5 0 0 0 4.5 15h7a1.5 1.5 0 0 0 1.5-1.5v-5A1.5 1.5 0 0 0 11.5 7V4.5A3.5 3.5 0 0 0 8 1Zm2 6V4.5a2 2 0 1 0-4 0V7h4Z" clip-rule="evenodd" />
											</svg>
											{$i18n.t('Enable background sync')}
										</button>
									{/if}
								{/if}
								{#if activeState?.isCancelling}
									<Tooltip content={$i18n.t('Click to cancel sync')}>
										<button
											class="p-1 rounded-lg text-gray-400 cursor-not-allowed"
											disabled
										>
											<Spinner className="size-3.5" />
										</button>
									</Tooltip>
								{:else if activeState?.isSyncing}
									<Tooltip content={$i18n.t('Click to cancel sync')}>
										<button
											class="p-1 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 text-blue-500 hover:text-red-500 transition"
											on:click={() => { showCancelSyncConfirmModal = true; }}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-4">
												<path d="M4.5 2A2.5 2.5 0 0 0 2 4.5v7A2.5 2.5 0 0 0 4.5 14h7a2.5 2.5 0 0 0 2.5-2.5v-7A2.5 2.5 0 0 0 11.5 2h-7Z" />
											</svg>
										</button>
									</Tooltip>
								{:else if activeProvider && knowledge?.meta?.[activeProvider.metaKey]?.sources?.length && knowledge?.user_id === $user?.id}
									<Tooltip content={knowledge?.meta?.[activeProvider.metaKey]?.last_sync_at
										? $i18n.t('Last synced: {{date}}', { date: dayjs(knowledge.meta[activeProvider.metaKey].last_sync_at * 1000).fromNow() })
										: $i18n.t('Sync {{label}} files', { label: activeProvider.label })}>
										<button
											class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
											on:click={() => cloudResyncHandler(activeProvider)}
										>
											{#if activeProvider.type === 'onedrive'}
												<OneDrive className="size-4" />
											{:else if activeProvider.type === 'google_drive'}
												<GoogleDrive className="size-4" />
											{/if}
										</button>
									</Tooltip>
								{/if}
								{#if isSyncBusy && activeState?.syncStatus?.progress_total}
									<Tooltip content={$i18n.t('Sync progress')}>
										<div class="text-xs text-blue-500 font-medium">
											{activeState.syncStatus.progress_current ?? 0} / {activeState.syncStatus.progress_total}
										</div>
									</Tooltip>
								{:else if isSyncBusy}
									<div class="text-xs text-blue-500 font-medium flex items-center gap-1">
										<Spinner className="size-3" />
									</div>
								{:else if fileItemsTotal}
									{#if knowledge?.type !== 'local' && knowledge?.type}
										{@const maxFiles = $config?.integration_providers?.[knowledge?.type]?.max_files_per_kb || $config?.features?.knowledge_max_file_count || 250}
										<Tooltip content={$i18n.t('Maximum {{count}} files per knowledge base', { count: maxFiles })}>
											<div class="text-xs text-gray-500">
												{fileItemsTotal} / {maxFiles} {$i18n.t('files')}
											</div>
										</Tooltip>
									{:else}
										<div class="text-xs text-gray-500">
											{$i18n.t('{{COUNT}} files', {
												COUNT: fileItemsTotal
											})}
										</div>
									{/if}
								{/if}
							</div>
						</div>

						{#if knowledge?.write_access && (knowledge?.type === 'local' || !knowledge?.type || $config?.integration_providers?.[knowledge?.type])}
							<div class="self-center shrink-0">
								<button
									class="bg-gray-50 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
									type="button"
									on:click={() => {
										showAccessControlModal = true;
									}}
								>
									<LockClosed strokeWidth="2.5" className="size-3.5" />

									<div class="text-sm font-medium shrink-0">
										{$i18n.t('Access')}
									</div>
								</button>
							</div>
						{:else if knowledge?.write_access}
							<div class="text-xs shrink-0 text-gray-500 flex items-center gap-1">
								<LockClosed strokeWidth="2.5" className="size-3" />
								{$i18n.t('Private')}
							</div>
						{:else}
							<div class="text-xs shrink-0 text-gray-500">
								{$i18n.t('Read Only')}
							</div>
						{/if}
					</div>

					<div class="flex w-full">
						<input
							type="text"
							class="text-left text-xs w-full text-gray-500 bg-transparent outline-hidden"
							bind:value={knowledge.description}
							aria-label={$i18n.t('Knowledge Description')}
							placeholder={$i18n.t('Knowledge Description')}
							disabled={!knowledge?.write_access}
							on:input={() => {
								changeDebounceHandler();
							}}
						/>
					</div>
				</div>
			</div>
		</div>

		<div
			class="mt-2 mb-2.5 py-2 -mx-0 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100/30 dark:border-gray-850/30 flex-1 flex flex-col overflow-hidden min-h-0"
		>
			<div class="px-3.5 flex shrink-0 items-center w-full space-x-2 py-0.5 pb-2">
				<div class="flex flex-1 items-center">
					<div class=" self-center ml-1 mr-3">
						<Search className="size-3.5" />
					</div>
					<input
						class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
						bind:value={query}
						aria-label={$i18n.t('Search Collection')}
						placeholder={$i18n.t('Search Collection')}
						on:input={() => {
							queryDebounceActive = true;
						}}
						on:focus={() => {
							selectedFileId = null;
						}}
					/>

					{#if knowledge?.write_access}
						<div>
							{#if activeProvider}
								<Tooltip content={$i18n.t('Sync from {{label}}', { label: activeProvider.label })}>
									<button
										class="p-1.5 rounded-xl hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 transition font-medium text-sm flex items-center space-x-1 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={isSyncBusy}
										on:click={() => {
											cloudSyncHandler(activeProvider);
										}}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 16 16"
											fill="currentColor"
											class="w-4 h-4"
										>
											<path
												d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z"
											/>
										</svg>
									</button>
								</Tooltip>
							{:else if $config?.integration_providers?.[knowledge?.type]}
								<!-- No add button for push providers -- files come via API -->
							{:else}
								<AddContentMenu
									onUpload={(data) => {
										if (data.type === 'directory') {
											uploadDirectoryHandler();
										} else if (data.type === 'web') {
											showAddWebpageModal = true;
										} else if (data.type === 'text') {
											showAddTextContentModal = true;
										} else {
											document.getElementById('files-input').click();
										}
									}}
									onSync={() => {
										showSyncConfirmModal = true;
									}}
								/>
							{/if}
						</div>
					{/if}
				</div>
			</div>

			<div class="px-3 flex justify-between">
				<div
					class="flex w-full bg-transparent overflow-x-auto scrollbar-none"
					on:wheel={(e) => {
						if (e.deltaY !== 0) {
							e.preventDefault();
							e.currentTarget.scrollLeft += e.deltaY;
						}
					}}
				>
					<div
						class="flex gap-3 w-fit text-center text-sm rounded-full bg-transparent px-0.5 whitespace-nowrap"
					>
						<DropdownOptions
							align="start"
							className="flex shrink-0 items-center gap-2 px-3 py-1.5 text-sm bg-gray-50 dark:bg-gray-850 rounded-xl placeholder-gray-400 outline-hidden focus:outline-hidden"
							bind:value={viewOption}
							items={[
								{ value: null, label: $i18n.t('All') },
								{ value: 'created', label: $i18n.t('Created by you') },
								{ value: 'shared', label: $i18n.t('Shared with you') }
							]}
							onChange={(value) => {
								if (value) {
									localStorage.workspaceViewOption = value;
								} else {
									delete localStorage.workspaceViewOption;
								}
							}}
						/>

						<DropdownOptions
							align="start"
							bind:value={sortKey}
							placeholder={$i18n.t('Sort')}
							items={[
								{ value: 'name', label: $i18n.t('Name') },
								{ value: 'created_at', label: $i18n.t('Created At') },
								{ value: 'updated_at', label: $i18n.t('Updated At') }
							]}
						/>

						{#if sortKey}
							<DropdownOptions
								align="start"
								bind:value={direction}
								items={[
									{ value: 'asc', label: $i18n.t('Asc') },
									{ value: null, label: $i18n.t('Desc') }
								]}
							/>
						{/if}
					</div>
				</div>
			</div>

			{#if fileItems !== null && fileItemsTotal !== null}
				<div class="flex flex-row flex-1 min-h-0 gap-3 px-2.5 mt-2">
					<div class="flex-1 flex">
						<div class=" flex flex-col w-full space-x-2 rounded-lg h-full">
							<div class="w-full h-full flex flex-col min-h-0">
								{#if fileItems.length > 0}
									<div class=" flex overflow-y-auto h-full w-full scrollbar-hidden text-xs">
										{#if activeProvider && knowledge?.meta?.[activeProvider.metaKey]?.sources?.length}
											<SourceGroupedFiles
												sources={knowledge.meta[activeProvider.metaKey].sources}
												files={fileItems}
												{knowledge}
												{selectedFileId}
												isSyncing={activeState?.isSyncing ?? false}
												onClick={(fileId) => {
													selectedFileId = fileId;

													if (fileItems) {
														const file = fileItems.find((file) => file.id === selectedFileId);
														if (file) {
															fileSelectHandler(file);
														} else {
															selectedFile = null;
														}
													}
												}}
												onRemoveSource={(itemId, sourceName) => {
													selectedFileId = null;
													selectedFile = null;
													removeCloudSourceHandler(activeProvider, itemId, sourceName);
												}}
												onDelete={(fileId) => {
													selectedFileId = null;
													selectedFile = null;
													deleteFileHandler(fileId);
												}}
											/>
										{:else}
											<Files
												files={fileItems}
												{knowledge}
												{selectedFileId}
												onClick={(fileId) => {
													selectedFileId = fileId;

													if (fileItems) {
														const file = fileItems.find((file) => file.id === selectedFileId);
														if (file) {
															fileSelectHandler(file);
														} else {
															selectedFile = null;
														}
													}
												}}
												onDelete={(fileId) => {
													selectedFileId = null;
													selectedFile = null;

													deleteFileHandler(fileId);
												}}
											/>
										{/if}
									</div>

									{#if !activeProvider && fileItemsTotal > 30}
										<Pagination bind:page={currentPage} count={fileItemsTotal} perPage={30} />
									{/if}
								{:else}
									{#if isSyncBusy}
										<div class="my-auto flex flex-col items-center justify-center text-center gap-3 py-8">
											<Spinner className="size-5" />
											<div class="text-xs text-gray-500">
												{$i18n.t('Starting sync...')}
											</div>
										</div>
									{:else if knowledge?.write_access && !query && !viewOption}
										<EmptyStateCards
											knowledgeType={knowledge?.type || 'local'}
											integrationProviders={$config?.integration_providers}
											onAction={(type) => {
												if (type === 'integration') {
													// No-op: files are managed via API
												} else if (type === 'onedrive') {
													cloudSyncHandler(CLOUD_PROVIDERS.onedrive);
												} else if (type === 'google_drive') {
													cloudSyncHandler(CLOUD_PROVIDERS.google_drive);
												} else if (type === 'directory') {
													uploadDirectoryHandler();
												} else if (type === 'web') {
													showAddWebpageModal = true;
												} else if (type === 'text') {
													showAddTextContentModal = true;
												} else {
													document.getElementById('files-input')?.click();
												}
											}}
										/>
									{:else}
										<div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
											<div>
												{$i18n.t('No content found')}
											</div>
										</div>
									{/if}
								{/if}
							</div>
						</div>
					</div>

					{#if selectedFileId !== null}
						<Drawer
							className="h-full"
							show={selectedFileId !== null}
							onClose={() => {
								selectedFileId = null;
								selectedFile = null;
							}}
						>
							<div class="flex flex-col justify-start h-full max-h-full">
								<div class=" flex flex-col w-full h-full max-h-full">
									<div class="shrink-0 flex items-center p-2">
										<div class="mr-2">
											<button
												class="w-full text-left text-sm p-1.5 rounded-lg dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-gray-850"
												aria-label={$i18n.t('Close')}
												on:click={() => {
													selectedFileId = null;
													selectedFile = null;
												}}
											>
												<ChevronLeft strokeWidth="2.5" />
											</button>
										</div>
										<div class=" flex-1 text-lg line-clamp-1">
											{selectedFile?.meta?.name}
										</div>

										{#if knowledge?.write_access}
											<div>
												<button
													class="flex self-center w-fit text-sm py-1 px-2.5 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
													disabled={isSaving}
													on:click={() => {
														updateFileContentHandler();
													}}
												>
													{$i18n.t('Save')}
													{#if isSaving}
														<div class="ml-2 self-center">
															<Spinner />
														</div>
													{/if}
												</button>
											</div>
										{/if}
									</div>

									{#key selectedFile.id}
										<textarea
											class="w-full h-full text-sm outline-none resize-none px-3 py-2"
											bind:value={selectedFileContent}
											disabled={!knowledge?.write_access}
											aria-label={$i18n.t('File content')}
											placeholder={$i18n.t('Add content here')}
										/>
									{/key}
								</div>
							</div>
						</Drawer>
					{/if}
				</div>
			{/if}
		</div>
	{:else}
		<Spinner className="size-5" />
	{/if}
</div>
