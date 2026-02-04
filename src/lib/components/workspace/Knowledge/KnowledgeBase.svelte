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
		searchKnowledgeFilesById
	} from '$lib/apis/knowledge';
	import { processWeb, processYoutubeVideo } from '$lib/apis/retrieval';
	import { startOneDriveSyncItems, getSyncStatus, cancelSync, type SyncStatusResponse, type SyncItem, type FailedFile, type SyncErrorType } from '$lib/apis/onedrive';
	import { openOneDriveItemPicker, getGraphApiToken } from '$lib/utils/onedrive-file-picker';

	import { blobToFile, isYoutubeUrl } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Files from './KnowledgeBase/Files.svelte';
	import AddFilesPlaceholder from '$lib/components/AddFilesPlaceholder.svelte';

	import AddContentMenu from './KnowledgeBase/AddContentMenu.svelte';
	import AddTextContentModal from './KnowledgeBase/AddTextContentModal.svelte';

	import SyncConfirmDialog from '../../common/ConfirmDialog.svelte';
	import Drawer from '$lib/components/common/Drawer.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import FileAdditionConflictModal from '../common/FileAdditionConflictModal.svelte';
	import type { FileAdditionConflict } from '$lib/apis/knowledge/permissions';
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

	let showSyncConfirmModal = false;
	let showCancelSyncConfirmModal = false;
	let showAccessControlModal = false;
	let showFileConflictModal = false;
	let fileConflict: FileAdditionConflict | null = null;
	let fileConflictPendingFileId: string | null = null;

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

	const init = async () => {
		_skipReactiveRefresh = true;
		currentPage = 1;
		_skipReactiveRefresh = false;
		await getItemsPage();
	};

	// Single reactive block: when filter/sort params change, reset page and fetch.
	// When only currentPage changes (pagination), just fetch.
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

	// Reset to page 1 when filter/sort params change (but NOT on currentPage change)
	$: if (
		query !== undefined &&
		viewOption !== undefined &&
		sortKey !== undefined &&
		direction !== undefined
	) {
		// This will trigger the above reactive via currentPage change,
		// which is the desired single fetch. No separate fetch needed here.
		currentPage = 1;
	}

	const getItemsPage = async () => {
		if (knowledgeId === null) return;

		if (sortKey === null) {
			direction = null;
		}

		const res = await searchKnowledgeFilesById(
			localStorage.token,
			knowledge.id,
			query,
			viewOption,
			sortKey,
			direction,
			currentPage
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

	const oneDriveSyncHandler = async () => {
		try {
			isSyncingOneDrive = true;
			_syncRefreshDone = false;

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

			// Start sync with new endpoint (clear exclusions since user is re-adding from picker)
			await startOneDriveSyncItems(localStorage.token, {
				knowledge_id: knowledge.id,
				items: syncItems,
				access_token: accessToken,
				user_token: localStorage.token,
				clear_exclusions: true
			});

			toast.success($i18n.t('OneDrive sync started'));

			// Poll for status
			pollOneDriveSyncStatus();
		} catch (error) {
			console.error('OneDrive sync error:', error);
			const errorMsg = error instanceof Error ? error.message : String(error);
			// Translate known error messages
			const translatedMsg = errorMsg.includes('Cannot sync OneDrive files to a public knowledge base')
				? $i18n.t('Cannot sync OneDrive files to a public knowledge base. Make the knowledge base private first, then share it with users who have source access.')
				: errorMsg;
			toast.error($i18n.t('Failed to sync from OneDrive') + ': ' + translatedMsg);
		} finally {
			isSyncingOneDrive = false;
		}
	};

	const oneDriveResyncHandler = async () => {
		const sources = knowledge?.meta?.onedrive_sync?.sources;
		if (!sources?.length) return;

		try {
			isSyncingOneDrive = true;
			_syncRefreshDone = false;

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
			} else if (oneDriveSyncStatus.status === 'completed' || oneDriveSyncStatus.status === 'completed_with_errors') {
				isSyncingOneDrive = false;
				// Only refresh if Socket.IO handler hasn't already done it
				if (!_syncRefreshDone) {
					const res = await getKnowledgeById(localStorage.token, id);
					if (res) {
						knowledge = res;
					}
					await init();
				}
				_syncRefreshDone = false;
			} else if (oneDriveSyncStatus.status === 'failed') {
				// Only show error if Socket.IO didn't already handle it
				if (isSyncingOneDrive) {
					toast.error($i18n.t('OneDrive sync failed: {{error}}', { error: oneDriveSyncStatus.error }));
					isSyncingOneDrive = false;
				}
			} else if (oneDriveSyncStatus.status === 'cancelled') {
				isSyncingOneDrive = false;
				if (!_syncRefreshDone) {
					const res = await getKnowledgeById(localStorage.token, id);
					if (res) {
						knowledge = res;
					}
					await init();
				}
				_syncRefreshDone = false;
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
			itemId: fileId
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
				itemId: data.file.id
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
			await init();
		} else if (data.status === 'failed') {
			const error = data.error || 'Unknown error';
			// Translate known error messages
			const translatedError = error.includes('Cannot sync OneDrive files to a public knowledge base')
				? $i18n.t('Cannot sync OneDrive files to a public knowledge base. Make the knowledge base private first, then share it with users who have source access.')
				: error;
			toast.error($i18n.t('OneDrive sync failed') + ': ' + translatedError);
			isSyncingOneDrive = false;
		} else if (data.status === 'cancelled') {
			toast.info($i18n.t('OneDrive sync cancelled'));
			isSyncingOneDrive = false;
			_syncRefreshDone = true;
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
			await init();
		}
	};

	const addFileHandler = async (fileId) => {
		const res = await addFileToKnowledgeById(localStorage.token, id, fileId).catch((e) => {
			const errorStr = `${e}`;
			// Detect source access conflict (409 from backend)
			if (errorStr.includes('Cannot add') && errorStr.includes('public knowledge base')) {
				// Extract source type from error message (e.g. "Cannot add onedrive files...")
				const sourceMatch = errorStr.match(/Cannot add (\w+) files/);
				const sourceType = sourceMatch ? sourceMatch[1] : 'external';

				fileConflict = {
					has_conflict: true,
					kb_is_public: true,
					users_without_access: [],
					user_details: [],
					source_type: sourceType,
					grant_access_url: null
				};
				fileConflictPendingFileId = fileId;
				showFileConflictModal = true;
				return null;
			}
			toast.error(errorStr);
			return null;
		});

		if (res) {
			// Success toast is batched in handleFileStatus
			// Just update the knowledge object if needed
			if (res.knowledge) {
				knowledge = res.knowledge;
			}
		} else if (!showFileConflictModal) {
			// Only show generic error if not showing conflict modal
			toast.error($i18n.t('Failed to add file.'));
			fileItems = fileItems.filter((file) => file.id !== fileId);
		}
	};

	const handleFileConflictMakePrivate = async () => {
		// Make the KB private (owner-only) and retry the file addition
		knowledge.access_control = { read: { group_ids: [], user_ids: [] }, write: { group_ids: [], user_ids: [] } };

		const res = await updateKnowledgeById(localStorage.token, id, {
			...knowledge,
			name: knowledge.name,
			description: knowledge.description,
			access_control: knowledge.access_control
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			knowledge = res;
			toast.success($i18n.t('Knowledge base made private.'));

			// Retry the file addition
			if (fileConflictPendingFileId) {
				await addFileHandler(fileConflictPendingFileId);
			}
		}

		showFileConflictModal = false;
		fileConflict = null;
		fileConflictPendingFileId = null;
	};

	const handleFileConflictCancel = () => {
		// Remove the pending file from the list
		if (fileConflictPendingFileId) {
			fileItems = (fileItems || []).filter((file) => file.id !== fileConflictPendingFileId);
		}
		showFileConflictModal = false;
		fileConflict = null;
		fileConflictPendingFileId = null;
	};

	const deleteFileHandler = async (fileId) => {
		// Optimistically remove from the list immediately
		const previousItems = fileItems;
		const previousTotal = fileItemsTotal;
		fileItems = (fileItems ?? []).filter((file) => file.id !== fileId);
		fileItemsTotal = Math.max(0, (fileItemsTotal ?? 1) - 1);

		try {
			const res = await removeFileFromKnowledgeById(localStorage.token, id, fileId);

			if (res) {
				knowledge = res;
				toast.success($i18n.t('File removed successfully.'));
				// Background refresh to sync with server state (no spinner due to Phase 1)
				await getItemsPage();
			} else {
				// Revert on failure
				fileItems = previousItems;
				fileItemsTotal = previousTotal;
			}
		} catch (e) {
			console.error('Error in deleteFileHandler:', e);
			toast.error(`${e}`);
			// Revert on error
			fileItems = previousItems;
			fileItemsTotal = previousTotal;
		}
	};

	let debounceTimeout = null;
	let mediaQuery;

	let dragged = false;
	let isSaving = false;
	let isSyncingOneDrive = false;
	let _syncRefreshDone = false;
	let oneDriveSyncStatus: SyncStatusResponse | null = null;

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

				// Background refresh instead of init() - no page reset needed
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

<FileAdditionConflictModal
	bind:show={showFileConflictModal}
	conflict={fileConflict}
	strictMode={$config?.features?.strict_source_permissions ?? true}
	on:makePrivate={handleFileConflictMakePrivate}
	on:cancel={handleFileConflictCancel}
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
		<AccessControlModal
			bind:show={showAccessControlModal}
			bind:accessControl={knowledge.access_control}
			share={$user?.permissions?.sharing?.knowledge || $user?.role === 'admin'}
			sharePublic={$user?.permissions?.sharing?.public_knowledge || $user?.role === 'admin'}
			onChange={() => {
				changeDebounceHandler();
			}}
			accessRoles={['read', 'write']}
			knowledgeId={knowledge.id}
			knowledgeName={knowledge.name}
			strictSourcePermissions={$config?.features?.strict_source_permissions ?? true}
		/>
		<div class="w-full px-2">
			<div class=" flex w-full">
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
								{#if fileItemsTotal}
									<div class="text-xs text-gray-500">
										<!-- {$i18n.t('{{COUNT}} files')} -->
										{$i18n.t('{{COUNT}} files', {
											COUNT: fileItemsTotal
										})}
									</div>
								{/if}
							</div>
						</div>

						{#if knowledge?.write_access}
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
								hideSyncDirectory={!!($config?.features?.enable_onedrive_integration &&
								$config?.features?.enable_onedrive_sync)}
								onOneDriveSync={$config?.features?.enable_onedrive_integration &&
								$config?.features?.enable_onedrive_sync
									? oneDriveSyncHandler
									: null}
							/>
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
									</div>

									{#if fileItemsTotal > 30}
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
