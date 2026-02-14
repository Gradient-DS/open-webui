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
		updateFileFromKnowledgeById,
		updateKnowledgeById,
		searchKnowledgeFilesById
	} from '$lib/apis/knowledge';
	import { processWeb, processYoutubeVideo } from '$lib/apis/retrieval';
	import { startOneDriveSyncItems, getSyncStatus, cancelSync, getTokenStatus, removeSource, type SyncStatusResponse, type SyncItem, type FailedFile, type SyncErrorType } from '$lib/apis/onedrive';
	import { openOneDriveItemPicker, getGraphApiToken } from '$lib/utils/onedrive-file-picker';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import { blobToFile, isYoutubeUrl } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Files from './KnowledgeBase/Files.svelte';
	import SourceGroupedFiles from './KnowledgeBase/SourceGroupedFiles.svelte';
	import AddFilesPlaceholder from '$lib/components/AddFilesPlaceholder.svelte';

	import AddContentMenu from './KnowledgeBase/AddContentMenu.svelte';
	import AddTextContentModal from './KnowledgeBase/AddTextContentModal.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	import SyncConfirmDialog from '../../common/ConfirmDialog.svelte';
	import Drawer from '$lib/components/common/Drawer.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import FilesOverlay from '$lib/components/chat/MessageInput/FilesOverlay.svelte';
	import DropdownOptions from '$lib/components/common/DropdownOptions.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import AttachWebpageModal from '$lib/components/chat/MessageInput/AttachWebpageModal.svelte';

	let largeScreen = true;

	let pane;
	let showSidepanel = true;

	let showAddWebpageModal = false;
	let showAddTextContentModal = false;

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
	};

	let id = null;
	let knowledge: Knowledge | null = null;
	let knowledgeId = null;

	let selectedFileId = null;
	let selectedFile = null;
	let selectedFileContent = '';

	let inputFiles = null;

	let query = '';
	let viewOption = null;
	let sortKey = null;
	let direction = null;

	let currentPage = 1;
	let fileItems = null;
	let fileItemsTotal = null;

	let _skipReactiveRefresh = false;

	const reset = () => {
		currentPage = 1;
	};

	const init = async () => {
		_skipReactiveRefresh = true;
		reset();
		_skipReactiveRefresh = false;
		await getItemsPage();
	};

	$: if (
		knowledgeId !== null &&
		query !== undefined &&
		viewOption !== undefined &&
		sortKey !== undefined &&
		direction !== undefined &&
		currentPage !== undefined
	) {
		if (!_skipReactiveRefresh) {
			getItemsPage();
		}
	}

	$: if (
		query !== undefined &&
		viewOption !== undefined &&
		sortKey !== undefined &&
		direction !== undefined
	) {
		reset();
	}

	const getItemsPage = async () => {
		if (knowledgeId === null) return;

		if (sortKey === null) {
			direction = null;
		}

		const isOneDrive = knowledge?.type === 'onedrive';
		const res = await searchKnowledgeFilesById(
			localStorage.token,
			knowledge.id,
			query,
			viewOption,
			sortKey,
			direction,
			currentPage,
			isOneDrive ? 250 : null
		).catch(() => {
			return null;
		});

		if (res) {
			fileItems = res.items;
			fileItemsTotal = res.total;
		}
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

		// Reject files with disallowed extensions when allowed_extensions is configured
		const allowedExtensions = $config?.file?.allowed_extensions ?? [];
		if (allowedExtensions.length > 0) {
			const fileExtension = file.name.split('.').pop()?.toLowerCase() ?? '';
			if (!allowedExtensions.includes(fileExtension)) {
				toast.error(
					$i18n.t('Unsupported file type: .{{extension}}. Allowed types: {{types}}', {
						extension: fileExtension,
						types: allowedExtensions.join(', ')
					})
				);
				return null;
			}
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
				console.log('File upload started, waiting for processing:', uploadedFile);
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
				// Don't call addFileHandler here - Socket.IO 'file:status' event will trigger it
				// when processing completes
			} else {
				toast.error($i18n.t('Failed to upload file.'));
			}
		} catch (e) {
			toast.error(`${e}`);
		}
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

	// Modern browsers implementation using File System Access API
	const handleModernBrowserUpload = async () => {
		const dirHandle = await window.showDirectoryPicker();
		let totalFiles = 0;
		let uploadedFiles = 0;

		// Function to update the UI with the progress
		const updateProgress = () => {
			const percentage = (uploadedFiles / totalFiles) * 100;
			toast.info(
				$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
					uploadedFiles: uploadedFiles,
					totalFiles: totalFiles,
					percentage: percentage.toFixed(2)
				})
			);
		};

		// Recursive function to count all files excluding hidden ones
		async function countFiles(dirHandle) {
			for await (const entry of dirHandle.values()) {
				// Skip hidden files and directories
				if (entry.name.startsWith('.')) continue;

				if (entry.kind === 'file') {
					totalFiles++;
				} else if (entry.kind === 'directory') {
					// Only process non-hidden directories
					if (!entry.name.startsWith('.')) {
						await countFiles(entry);
					}
				}
			}
		}

		// Recursive function to process directories excluding hidden files and folders
		async function processDirectory(dirHandle, path = '') {
			for await (const entry of dirHandle.values()) {
				// Skip hidden files and directories
				if (entry.name.startsWith('.')) continue;

				const entryPath = path ? `${path}/${entry.name}` : entry.name;

				// Skip if the path contains any hidden folders
				if (hasHiddenFolder(entryPath)) continue;

				if (entry.kind === 'file') {
					const file = await entry.getFile();
					const fileWithPath = new File([file], entryPath, { type: file.type });

					await uploadFileHandler(fileWithPath);
					uploadedFiles++;
					updateProgress();
				} else if (entry.kind === 'directory') {
					// Only process non-hidden directories
					if (!entry.name.startsWith('.')) {
						await processDirectory(entry, entryPath);
					}
				}
			}
		}

		await countFiles(dirHandle);
		updateProgress();

		if (totalFiles > 0) {
			await processDirectory(dirHandle);
		} else {
			console.log('No files to upload.');
		}
	};

	// Firefox fallback implementation using traditional file input
	const handleFirefoxUpload = async () => {
		return new Promise((resolve, reject) => {
			// Create hidden file input
			const input = document.createElement('input');
			input.type = 'file';
			input.webkitdirectory = true;
			input.directory = true;
			input.multiple = true;
			input.style.display = 'none';

			// Add input to DOM temporarily
			document.body.appendChild(input);

			input.onchange = async () => {
				try {
					const files = Array.from(input.files)
						// Filter out files from hidden folders
						.filter((file) => !hasHiddenFolder(file.webkitRelativePath));

					let totalFiles = files.length;
					let uploadedFiles = 0;

					// Function to update the UI with the progress
					const updateProgress = () => {
						const percentage = (uploadedFiles / totalFiles) * 100;
						toast.info(
							$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
								uploadedFiles: uploadedFiles,
								totalFiles: totalFiles,
								percentage: percentage.toFixed(2)
							})
						);
					};

					updateProgress();

					// Process all files
					for (const file of files) {
						// Skip hidden files (additional check)
						if (!file.name.startsWith('.')) {
							const relativePath = file.webkitRelativePath || file.name;
							const fileWithPath = new File([file], relativePath, { type: file.type });

							await uploadFileHandler(fileWithPath);
							uploadedFiles++;
							updateProgress();
						}
					}

					// Clean up
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

			// Trigger file picker
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

	const oneDriveSyncHandler = async () => {
		try {
			isSyncingOneDrive = true;

			// Open item picker (files and folders, uses business/SharePoint accounts)
			const items = await openOneDriveItemPicker('organizations');
			if (!items || items.length === 0) {
				isSyncingOneDrive = false;
				return;
			}

			// Get access token specifically for Graph API calls (different from picker token)
			// The picker uses SharePoint-scoped tokens, but the backend needs Graph API tokens
			const accessToken = await getGraphApiToken('organizations');

			// Convert to API format
			const syncItems: SyncItem[] = items.map(item => ({
				type: item.type,
				drive_id: item.driveId,
				item_id: item.id,
				item_path: item.path,
				name: item.name
			}));

			// Start sync with new endpoint
			_syncRefreshDone = false;
			await startOneDriveSyncItems(localStorage.token, {
				knowledge_id: knowledge.id,
				items: syncItems,
				access_token: accessToken,
				user_token: localStorage.token
			});

			// Refresh knowledge to get updated sources (saved by backend before sync starts)
			const updatedKnowledge = await getKnowledgeById(localStorage.token, id);
			if (updatedKnowledge) {
				knowledge = updatedKnowledge;
			}

			toast.success($i18n.t('OneDrive sync started'));

			// Poll for status
			pollOneDriveSyncStatus();
		} catch (error) {
			console.error('OneDrive sync error:', error);
			toast.error($i18n.t('Failed to sync from OneDrive: ' + (error instanceof Error ? error.message : String(error))));
		} finally {
			isSyncingOneDrive = false;
		}
	};

	const oneDriveResyncHandler = async () => {
		const sources = knowledge?.meta?.onedrive_sync?.sources;
		if (!sources?.length) return;

		try {
			isSyncingOneDrive = true;

			// Get fresh access token for Graph API
			const accessToken = await getGraphApiToken('organizations');

			// Build items from sources
			const syncItems: SyncItem[] = sources.map((source: any) => ({
				type: source.type,
				drive_id: source.drive_id,
				item_id: source.item_id,
				item_path: source.item_path,
				name: source.name
			}));

			_syncRefreshDone = false;
			await startOneDriveSyncItems(localStorage.token, {
				knowledge_id: knowledge.id,
				items: syncItems,
				access_token: accessToken,
				user_token: localStorage.token
			});

			toast.success($i18n.t('OneDrive sync started'));
			pollOneDriveSyncStatus();
		} catch (error) {
			console.error('OneDrive resync error:', error);
			toast.error($i18n.t('Failed to start sync: ' + (error instanceof Error ? error.message : String(error))));
			isSyncingOneDrive = false;
		}
	};

	const pollOneDriveSyncStatus = async () => {
		try {
			oneDriveSyncStatus = await getSyncStatus(localStorage.token, knowledge.id);

			if (oneDriveSyncStatus.status === 'syncing') {
				setTimeout(pollOneDriveSyncStatus, 2000);
			} else if (oneDriveSyncStatus.status === 'file_limit_exceeded') {
				toast.error(oneDriveSyncStatus.error || $i18n.t('File limit exceeded'));
				isSyncingOneDrive = false;
				if (!_syncRefreshDone) {
					_syncRefreshDone = true;
					await init();
				}
			} else if (oneDriveSyncStatus.status === 'access_revoked') {
				// access_revoked is a transient status during sync, keep polling
				setTimeout(pollOneDriveSyncStatus, 2000);
			} else if (oneDriveSyncStatus.status === 'completed' || oneDriveSyncStatus.status === 'completed_with_errors') {
				// Toast is handled by Socket.IO handler, just refresh
				isSyncingOneDrive = false;
				if (!_syncRefreshDone) {
					_syncRefreshDone = true;
					await init();
				}
			} else if (oneDriveSyncStatus.status === 'failed') {
				// Only show error if Socket.IO didn't already handle it
				if (isSyncingOneDrive) {
					toast.error($i18n.t('OneDrive sync failed: {{error}}', { error: oneDriveSyncStatus.error }));
					isSyncingOneDrive = false;
				}
			} else if (oneDriveSyncStatus.status === 'cancelled') {
				isSyncingOneDrive = false;
				if (!_syncRefreshDone) {
					_syncRefreshDone = true;
					await init();
				}
			}
		} catch (error) {
			console.error('Failed to get sync status:', error);
		}
	};

	const cancelOneDriveSyncHandler = async () => {
		try {
			await cancelSync(localStorage.token, knowledge.id);
			toast.info($i18n.t('Cancelling OneDrive sync...'));
		} catch (error) {
			console.error('Failed to cancel sync:', error);
			toast.error($i18n.t('Failed to cancel sync: ' + (error instanceof Error ? error.message : String(error))));
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

	// Batched success toast for file uploads - tracks completed files until all uploads finish
	let successfulFileCount = 0;

	const showBatchedSuccessToast = () => {
		if (successfulFileCount > 0) {
			const count = successfulFileCount;
			successfulFileCount = 0;
			toast.success(
				count === 1
					? $i18n.t('File added successfully.')
					: $i18n.t('{{count}} files successfully added.', { count })
			);
		}
	};

	// Socket.IO handler for file processing status updates
	const handleFileStatus = async (data: {
		file_id: string;
		status: string;
		error?: string;
		collection_name?: string;
	}) => {
		if (!fileItems) return;

		const idx = fileItems.findIndex((f) => f.id === data.file_id);
		if (idx >= 0) {
			if (data.status === 'completed') {
				fileItems[idx].status = 'uploaded';
				// Now that file is processed, add it to the knowledge base
				await addFileHandler(data.file_id);
				successfulFileCount++;
			} else if (data.status === 'failed') {
				fileItems[idx].status = 'error';
				fileItems[idx].error = data.error || 'Processing failed';
				toast.error(`File processing failed: ${data.error || 'Unknown error'}`);
				// Remove failed file from the list
				fileItems = fileItems.filter((file) => file.id !== data.file_id);
			}
			fileItems = fileItems; // Trigger reactivity

			// Check if all files are done (no more 'uploading' status)
			const stillUploading = fileItems.some((f) => f.status === 'uploading');
			if (!stillUploading) {
				showBatchedSuccessToast();
			}
		}
	};

	// Socket.IO handler for OneDrive file processing started (shows file with loading state)
	const handleOneDriveFileProcessing = (data: {
		knowledge_id: string;
		file: {
			item_id: string;
			name: string;
			size?: number;
			source_item_id?: string;
			relative_path?: string;
		};
	}) => {
		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) {
			return;
		}

		// Use onedrive-{item_id} as the file ID (matches backend pattern)
		const fileId = `onedrive-${data.file.item_id}`;

		// Check if file is already in the list
		if (fileItems?.some((f) => f.id === fileId || f.itemId === fileId)) {
			return;
		}

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
				source: 'onedrive',
				relative_path: data.file.relative_path
			}
		};

		// Add to beginning of list
		fileItems = [newFileItem, ...(fileItems ?? [])];
		fileItemsTotal = (fileItemsTotal ?? 0) + 1;

		console.log('OneDrive file processing started:', data.file.name);
	};

	// Socket.IO handler for OneDrive file added events (updates status to uploaded)
	const handleOneDriveFileAdded = (data: {
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
	}) => {
		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) {
			return;
		}

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
			console.log('OneDrive file completed:', data.file.filename);
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
			console.log('OneDrive file added:', data.file.filename);
		}
	};

	// Socket.IO handler for real-time sync progress updates
	const handleOneDriveSyncProgress = async (data: {
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
	}) => {
		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) {
			return;
		}

		// Update sync status
		oneDriveSyncStatus = {
			knowledge_id: data.knowledge_id,
			status: data.status as 'idle' | 'syncing' | 'completed' | 'completed_with_errors' | 'failed',
			progress_current: data.current,
			progress_total: data.total,
			error: data.error,
			failed_files: data.failed_files
		};

		// Handle access revoked
		if (data.status === 'access_revoked') {
			toast.warning(data.error || $i18n.t('Access to a OneDrive source has been revoked'));
		}

		// Handle file limit exceeded
		if (data.status === 'file_limit_exceeded') {
			toast.error(data.error || $i18n.t('File limit exceeded'));
			isSyncingOneDrive = false;
			_syncRefreshDone = true;
			await init();
			return;
		}

		// Handle completion states
		if (data.status === 'completed' || data.status === 'completed_with_errors') {
			const count = data.files_processed ?? 0;
			if (count > 0) {
				if (data.files_failed && data.files_failed > 0) {
					const failedDetails = data.failed_files
						? formatFailedFilesMessage(data.failed_files)
						: '';
					toast.warning(
						$i18n.t('Synced {{count}} files from OneDrive ({{failed}} failed)', {
							count,
							failed: data.files_failed
						}) + failedDetails
					);
				} else {
					toast.success($i18n.t('Synced {{count}} files from OneDrive', { count }));
				}
			} else {
				toast.success($i18n.t('OneDrive sync completed - no changes'));
			}
			isSyncingOneDrive = false;
			_syncRefreshDone = true;
			// Refresh knowledge metadata to update last_sync_at timestamp
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
			await init(); // Refresh file list
		} else if (data.status === 'failed') {
			toast.error($i18n.t('OneDrive sync failed: {{error}}', { error: data.error || 'Unknown error' }));
			isSyncingOneDrive = false;
		} else if (data.status === 'cancelled') {
			toast.info($i18n.t('OneDrive sync cancelled'));
			isSyncingOneDrive = false;
			_syncRefreshDone = true;
			// Refresh knowledge metadata to update last_sync_at timestamp
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
			await init(); // Refresh file list
		}
	};

	const authorizeBackgroundSync = async () => {
		const authUrl = `${WEBUI_API_BASE_URL}/onedrive/auth/initiate?knowledge_id=${knowledge.id}`;

		// Open popup
		const popup = window.open(
			authUrl,
			'onedrive_auth',
			'width=600,height=700,scrollbars=yes'
		);

		let messageReceived = false;

		// Listen for postMessage from callback
		const handleMessage = (event: MessageEvent) => {
			if (event.data?.type !== 'onedrive_auth_callback') return;

			messageReceived = true;
			window.removeEventListener('message', handleMessage);

			if (event.data.success) {
				backgroundSyncAuthorized = true;
				backgroundSyncNeedsReauth = false;
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
						const status = await getTokenStatus(localStorage.token, knowledge.id);
						if (status.has_token && !status.is_expired) {
							backgroundSyncAuthorized = true;
							backgroundSyncNeedsReauth = false;
							toast.success($i18n.t('Background sync authorized'));
						}
					} catch (e) {
						console.warn('Failed to check background sync token status:', e);
						toast.error($i18n.t('Failed to check background sync status'));
					}
				}
			}
		}, 500);
	};

	const addFileHandler = async (fileId) => {
		const res = await addFileToKnowledgeById(localStorage.token, id, fileId).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			// Success toast is batched in handleFileStatus
			// Just update the knowledge object if needed
			if (res.knowledge) {
				knowledge = res.knowledge;
			}
		} else {
			toast.error($i18n.t('Failed to add file.'));
			fileItems = fileItems.filter((file) => file.id !== fileId);
		}
	};

	const removeSourceHandler = async (itemId: string, sourceName: string) => {
		try {
			const result = await removeSource(localStorage.token, knowledge.id, itemId);
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
			console.error('Error removing source:', e);
			toast.error($i18n.t('Failed to remove source: {{error}}', {
				error: e instanceof Error ? e.message : String(e)
			}));
		}
	};

	const deleteFileHandler = async (fileId) => {
		const previousItems = fileItems;
		const previousTotal = fileItemsTotal;
		fileItems = (fileItems ?? []).filter((file) => file.id !== fileId);
		fileItemsTotal = Math.max(0, (fileItemsTotal ?? 1) - 1);

		try {
			const res = await removeFileFromKnowledgeById(localStorage.token, id, fileId);
			if (res) {
				toast.success($i18n.t('File removed successfully.'));
				await getItemsPage();
			} else {
				fileItems = previousItems;
				fileItemsTotal = previousTotal;
			}
		} catch (e) {
			console.error('Error in deleteFileHandler:', e);
			fileItems = previousItems;
			fileItemsTotal = previousTotal;
			toast.error(`${e}`);
		}
	};

	let _syncRefreshDone = false;

	let debounceTimeout = null;
	let mediaQuery;

	let dragged = false;
	let isSaving = false;
	let isSyncingOneDrive = false;
	let oneDriveSyncStatus: SyncStatusResponse | null = null;
	let backgroundSyncAuthorized = false;
	let backgroundSyncNeedsReauth = false;

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

				await getItemsPage();
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
				access_control: knowledge.access_control
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

		const handleUploadingFileFolder = (items) => {
			for (const item of items) {
				if (item.isFile) {
					item.file((file) => {
						uploadFileHandler(file);
					});
					continue;
				}

				// Not sure why you have to call webkitGetAsEntry and isDirectory seperate, but it won't work if you try item.webkitGetAsEntry().isDirectory
				const wkentry = item.webkitGetAsEntry();
				const isDirectory = wkentry.isDirectory;
				if (isDirectory) {
					// Read the directory
					wkentry.createReader().readEntries(
						(entries) => {
							handleUploadingFileFolder(entries);
						},
						(error) => {
							console.error('Error reading directory entries:', error);
						}
					);
				} else {
					toast.info($i18n.t('Uploading file...'));
					uploadFileHandler(item.getAsFile());
					toast.success($i18n.t('File uploaded!'));
				}
			}
		};

		if (e.dataTransfer?.types?.includes('Files')) {
			if (e.dataTransfer?.files) {
				const inputItems = e.dataTransfer?.items;

				if (inputItems && inputItems.length > 0) {
					handleUploadingFileFolder(inputItems);
				} else {
					toast.error($i18n.t(`File not found.`));
				}
			}
		}
	};

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
			knowledgeId = knowledge?.id;

			// Check background sync token status for OneDrive KBs
			if (knowledge?.type === 'onedrive' && knowledge?.meta?.onedrive_sync?.sources?.length) {
				try {
					const status = await getTokenStatus(localStorage.token, knowledge.id);
					backgroundSyncAuthorized = status.has_token && !status.is_expired;
					backgroundSyncNeedsReauth = status.needs_reauth ?? false;
				} catch (e) {
					console.warn('Failed to check background sync token status:', e);
					toast.error($i18n.t('Failed to check background sync status'));
				}
			}

			// Auto-start OneDrive sync if directed from creation flow
			if ($page.url.searchParams.get('start_onedrive_sync') === 'true' && knowledge) {
				const url = new URL(window.location.href);
				url.searchParams.delete('start_onedrive_sync');
				history.replaceState({}, '', url.toString());

				await tick();
				oneDriveSyncHandler();
			}
		} else {
			goto('/workspace/knowledge');
		}

		const dropZone = document.querySelector('body');
		dropZone?.addEventListener('dragover', onDragOver);
		dropZone?.addEventListener('drop', onDrop);
		dropZone?.addEventListener('dragleave', onDragLeave);

		// Listen for OneDrive sync progress events via Socket.IO
		$socket?.on('onedrive:sync:progress', handleOneDriveSyncProgress);

		// Listen for OneDrive file processing/added events for progressive UI updates
		$socket?.on('onedrive:file:processing', handleOneDriveFileProcessing);
		$socket?.on('onedrive:file:added', handleOneDriveFileAdded);

		// Listen for file processing status events via Socket.IO
		$socket?.on('file:status', handleFileStatus);
	});

	onDestroy(() => {
		mediaQuery?.removeEventListener('change', handleMediaQuery);
		const dropZone = document.querySelector('body');
		dropZone?.removeEventListener('dragover', onDragOver);
		dropZone?.removeEventListener('drop', onDrop);
		dropZone?.removeEventListener('dragleave', onDragLeave);

		// Clean up OneDrive sync progress listener
		$socket?.off('onedrive:sync:progress', handleOneDriveSyncProgress);

		// Clean up OneDrive file processing/added listeners
		$socket?.off('onedrive:file:processing', handleOneDriveFileProcessing);
		$socket?.off('onedrive:file:added', handleOneDriveFileAdded);

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
	bind:show={showCancelSyncConfirmModal}
	title={$i18n.t('Cancel OneDrive Sync')}
	message={$i18n.t('Are you sure you want to cancel the ongoing sync? Files already synced will be kept.')}
	confirmLabel={$i18n.t('Cancel Sync')}
	on:confirm={() => {
		cancelOneDriveSyncHandler();
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
			// Fire all uploads in parallel
			await Promise.all(Array.from(inputFiles).map((file) => uploadFileHandler(file)));

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

<div class="flex flex-col w-full h-full min-h-full" id="collection-container">
	{#if id && knowledge}
		{#if knowledge?.type === 'local' || !knowledge?.type}
			<AccessControlModal
				bind:show={showAccessControlModal}
				bind:accessControl={knowledge.access_control}
				share={$user?.permissions?.sharing?.knowledge || $user?.role === 'admin'}
				sharePublic={$user?.permissions?.sharing?.public_knowledge || $user?.role === 'admin'}
				onChange={() => {
					changeDebounceHandler();
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
								placeholder={$i18n.t('Knowledge Name')}
								disabled={!knowledge?.write_access}
								on:input={() => {
									changeDebounceHandler();
								}}
							/>

							<div class="shrink-0 mr-2.5 flex items-center gap-2">
								{#if knowledge?.type === 'onedrive'}
									<Badge type="info" content={$i18n.t('OneDrive')} />
								{:else}
									<Badge type="muted" content={$i18n.t('Local')} />
								{/if}
								{#if knowledge?.meta?.onedrive_sync?.sources?.length && !isSyncingOneDrive && knowledge?.user_id === $user?.id}
									<Tooltip content={knowledge?.meta?.onedrive_sync?.last_sync_at
										? $i18n.t('Last synced: {{date}}', { date: dayjs(knowledge.meta.onedrive_sync.last_sync_at * 1000).fromNow() })
										: $i18n.t('Sync OneDrive files')}>
										<button
											class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
											on:click={oneDriveResyncHandler}
										>
											<OneDrive className="size-4" />
										</button>
									</Tooltip>
								{/if}
								{#if oneDriveSyncStatus?.status === 'syncing' || isSyncingOneDrive}
									<Tooltip content={$i18n.t('Click to cancel sync')}>
										<button
											class="text-xs text-blue-500 flex items-center gap-1 hover:text-red-500 transition-colors cursor-pointer"
											on:click={() => {
												showCancelSyncConfirmModal = true;
											}}
										>
											<div class="relative">
												<Spinner className="size-3" />
												<svg
													class="absolute -top-0.5 -right-0.5 size-2 text-red-500 opacity-0 hover:opacity-100"
													viewBox="0 0 24 24"
													fill="currentColor"
												>
													<rect x="4" y="4" width="16" height="16" rx="2" />
												</svg>
											</div>
											{#if oneDriveSyncStatus?.progress_total}
												{$i18n.t('Syncing: {{current}}/{{total}}', {
													current: oneDriveSyncStatus.progress_current || 0,
													total: oneDriveSyncStatus.progress_total
												})}
											{:else}
												{$i18n.t('Starting sync...')}
											{/if}
										</button>
									</Tooltip>
								{/if}
								{#if knowledge?.type === 'onedrive' && $config?.onedrive?.has_client_secret}
									{#if backgroundSyncNeedsReauth}
										<button
											class="text-xs text-red-500 hover:text-red-600 flex items-center gap-1"
											on:click={authorizeBackgroundSync}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3.5">
												<path fill-rule="evenodd" d="M6.701 2.25c.577-1 2.02-1 2.598 0l5.196 9a1.5 1.5 0 0 1-1.299 2.25H2.804a1.5 1.5 0 0 1-1.3-2.25l5.197-9ZM8 4a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 8 4Zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" clip-rule="evenodd" />
											</svg>
											{$i18n.t('Re-authorize background sync')}
										</button>
									{:else if backgroundSyncAuthorized}
										<span class="text-xs text-green-600 flex items-center gap-1">
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3.5">
												<path fill-rule="evenodd" d="M12.416 3.376a.75.75 0 0 1 .208 1.04l-5 7.5a.75.75 0 0 1-1.154.114l-3-3a.75.75 0 0 1 1.06-1.06l2.353 2.353 4.493-6.74a.75.75 0 0 1 1.04-.207Z" clip-rule="evenodd" />
											</svg>
											{$i18n.t('Background sync enabled')}
										</span>
									{:else if knowledge?.meta?.onedrive_sync?.sources?.length}
										<button
											class="text-xs text-blue-500 hover:text-blue-600 flex items-center gap-1"
											on:click={authorizeBackgroundSync}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-3.5">
												<path fill-rule="evenodd" d="M8 1a3.5 3.5 0 0 0-3.5 3.5V7A1.5 1.5 0 0 0 3 8.5v5A1.5 1.5 0 0 0 4.5 15h7a1.5 1.5 0 0 0 1.5-1.5v-5A1.5 1.5 0 0 0 11.5 7V4.5A3.5 3.5 0 0 0 8 1Zm2 6V4.5a2 2 0 1 0-4 0V7h4Z" clip-rule="evenodd" />
											</svg>
											{$i18n.t('Enable background sync')}
										</button>
									{/if}
								{/if}
								{#if fileItemsTotal}
									<div class="text-xs text-gray-500">
										{#if knowledge?.type !== 'local' && knowledge?.type}
											{fileItemsTotal} / 250 {$i18n.t('files')}
										{:else}
											{$i18n.t('{{COUNT}} files', {
												COUNT: fileItemsTotal
											})}
										{/if}
									</div>
								{/if}
							</div>
						</div>

						{#if knowledge?.write_access && (knowledge?.type === 'local' || !knowledge?.type)}
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
			class="mt-2 mb-2.5 py-2 -mx-0 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100/30 dark:border-gray-850/30 flex-1"
		>
			<div class="px-3.5 flex flex-1 items-center w-full space-x-2 py-0.5 pb-2">
				<div class="flex flex-1 items-center">
					<div class=" self-center ml-1 mr-3">
						<Search className="size-3.5" />
					</div>
					<input
						class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
						bind:value={query}
						placeholder={`${$i18n.t('Search Collection')}`}
						on:focus={() => {
							selectedFileId = null;
						}}
					/>

					{#if knowledge?.write_access}
						<div>
							{#if knowledge?.type === 'onedrive'}
								<Tooltip content={$i18n.t('Sync from OneDrive')}>
									<button
										class="p-1.5 rounded-xl hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 transition font-medium text-sm flex items-center space-x-1"
										on:click={() => {
											oneDriveSyncHandler();
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
							className="flex w-full items-center gap-2 truncate px-3 py-1.5 text-sm bg-gray-50 dark:bg-gray-850 rounded-xl  placeholder-gray-400 outline-hidden focus:outline-hidden"
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
				<div class="flex flex-row flex-1 gap-3 px-2.5 mt-2">
					<div class="flex-1 flex">
						<div class=" flex flex-col w-full space-x-2 rounded-lg h-full">
							<div class="w-full h-full flex flex-col min-h-full">
								{#if fileItems.length > 0}
									<div class=" flex overflow-y-auto h-full w-full scrollbar-hidden text-xs">
										{#if knowledge?.type === 'onedrive' && knowledge?.meta?.onedrive_sync?.sources?.length}
											<SourceGroupedFiles
												sources={knowledge.meta.onedrive_sync.sources}
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
												onRemoveSource={(itemId, sourceName) => {
													selectedFileId = null;
													selectedFile = null;
													removeSourceHandler(itemId, sourceName);
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

									{#if knowledge?.type !== 'onedrive' && fileItemsTotal > 30}
										<Pagination bind:page={currentPage} count={fileItemsTotal} perPage={30} />
									{/if}
								{:else}
									<div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
										<div>
											{$i18n.t('No content found')}
										</div>
									</div>
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
											placeholder={$i18n.t('Add content here')}
										/>
									{/key}
								</div>
							</div>
						</Drawer>
					{/if}
				</div>
			{:else}
				<div class="my-10">
					<Spinner className="size-4" />
				</div>
			{/if}
		</div>
	{:else}
		<Spinner className="size-5" />
	{/if}
</div>
