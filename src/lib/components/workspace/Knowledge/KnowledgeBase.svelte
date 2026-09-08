<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';
<<<<<<< HEAD
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(relativeTime);
=======
>>>>>>> upstream/main

	import { onMount, getContext, onDestroy, tick } from 'svelte';
	import { get } from 'svelte/store';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { config, user, settings, socket } from '$lib/stores';

	import { uploadFile } from '$lib/apis/files';
	import {
		addFileToKnowledgeById,
		getKnowledgeById,
		removeFileFromKnowledgeById,
		resetKnowledgeById,
		updateKnowledgeById,
		updateKnowledgeAccessGrants,
		searchKnowledgeFilesById,
		createKnowledgeDirectory,
		updateKnowledgeDirectory,
		deleteKnowledgeDirectory,
		moveFileInKnowledge,
		syncKnowledgeDiff,
		syncKnowledgeCleanup,
		testExternalKnowledgeRetrieval
	} from '$lib/apis/knowledge';
<<<<<<< HEAD
	import { processWeb } from '$lib/apis/retrieval';
	import {
		createSyncApi,
		type SyncStatusResponse,
		type SyncErrorType,
		type FailedFile
	} from '$lib/apis/sync';
	import { startOneDriveSyncItems, type SyncItem as OneDriveSyncItem } from '$lib/apis/onedrive';
	import { openOneDriveItemPicker, getGraphApiToken } from '$lib/utils/onedrive-file-picker';
	import {
		startGoogleDriveSyncItems,
		type SyncItem as GoogleDriveSyncItem
	} from '$lib/apis/googledrive';
	import { createKnowledgePicker } from '$lib/utils/google-drive-picker';
	import {
		startConfluenceSyncItems,
		type SyncItem as ConfluenceSyncItem
	} from '$lib/apis/confluence';
	import ConfluencePickerModal from './ConfluencePickerModal.svelte';
=======
	import { processUrl } from '$lib/apis/retrieval';
>>>>>>> upstream/main
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import { blobToFile, copyToClipboard } from '$lib/utils';
	import { computeFileHash } from '$lib/utils/hash';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Files from './KnowledgeBase/Files.svelte';
	import KbSelectionHeader from './KnowledgeBase/KbSelectionHeader.svelte';
	import { createKbSelection } from './KnowledgeBase/selection';
	import SyncProgress from './KnowledgeBase/SyncProgress.svelte';
	import { buildSyncToast } from './utils/syncToast';
	import { canEditStructure, isLocalKnowledgeType } from './utils/structure';

	import AddContentMenu from './KnowledgeBase/AddContentMenu.svelte';
	import AddTextContentModal from './KnowledgeBase/AddTextContentModal.svelte';
	import NewDirectoryModal from './KnowledgeBase/NewDirectoryModal.svelte';
	import KnowledgeBreadcrumbs from './KnowledgeBase/KnowledgeBreadcrumbs.svelte';
	import EmptyStateCards from './KnowledgeBase/EmptyStateCards.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	import SyncConfirmDialog from '../../common/ConfirmDialog.svelte';
	import ConfirmDialog from '../../common/ConfirmDialog.svelte';
	import FileItemModal from '$lib/components/common/FileItemModal.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
<<<<<<< HEAD
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
=======
	import AccessButton from '$lib/components/common/AccessButton.svelte';
>>>>>>> upstream/main
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import FilesOverlay from '$lib/components/chat/MessageInput/FilesOverlay.svelte';
	import DropdownOptions from '$lib/components/common/DropdownOptions.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import AdjustmentsHorizontal from '$lib/components/icons/AdjustmentsHorizontal.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import AttachWebpageModal from '$lib/components/chat/MessageInput/AttachWebpageModal.svelte';

<<<<<<< HEAD
	// ===== Cloud sync provider configuration =====

	interface CloudSyncProvider {
		type: string; // "onedrive" | "google_drive"
		metaKey: string; // "onedrive_sync" | "google_drive_sync"
		eventPrefix: string; // "onedrive" | "googledrive"
		fileIdPrefix: string; // "onedrive-" | "googledrive-"
		sourceMetaField: string; // "onedrive" | "google_drive" (for file.meta.source)
		label: string; // "OneDrive" | "Google Drive"
		api: ReturnType<typeof createSyncApi>;
		startSyncParam: string; // "start_onedrive_sync" | "start_google_drive_sync"
		authCallbackType: string; // "onedrive_auth_callback" | "google_drive_auth_callback"
		authBasePath: string; // "onedrive" | "google-drive" (for auth URL)
		authPopupName: string; // "onedrive_auth" | "google_drive_auth"
		configKey: string; // "onedrive" | "google_drive" (for $config?.xxx?.has_client_secret)
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
			configKey: 'onedrive'
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
			configKey: 'google_drive'
		},
		confluence: {
			type: 'confluence',
			metaKey: 'confluence_sync',
			eventPrefix: 'confluence',
			fileIdPrefix: 'confluence-',
			sourceMetaField: 'confluence',
			label: 'Confluence',
			api: createSyncApi('confluence'),
			startSyncParam: 'start_confluence_sync',
			authCallbackType: 'confluence_auth_callback',
			authBasePath: 'confluence',
			authPopupName: 'confluence_auth',
			configKey: 'confluence'
		}
	};

	// ===== Unified cloud sync state =====

	let cloudSyncState: Record<
		string,
		{
			isSyncing: boolean;
			isCancelling: boolean;
			syncStatus: SyncStatusResponse | null;
			bgSyncAuthorized: boolean;
			bgSyncNeedsReauth: boolean;
			refreshDone: boolean;
		}
	> = {
		onedrive: {
			isSyncing: false,
			isCancelling: false,
			syncStatus: null,
			bgSyncAuthorized: false,
			bgSyncNeedsReauth: false,
			refreshDone: false
		},
		google_drive: {
			isSyncing: false,
			isCancelling: false,
			syncStatus: null,
			bgSyncAuthorized: false,
			bgSyncNeedsReauth: false,
			refreshDone: false
		},
		confluence: {
			isSyncing: false,
			isCancelling: false,
			syncStatus: null,
			bgSyncAuthorized: false,
			bgSyncNeedsReauth: false,
			refreshDone: false
		}
	};

	// Confluence picker modal state
	let showConfluencePicker = false;
	let pendingConfluenceResolve: ((items: ConfluenceSyncItem[] | null) => void) | null = null;

	function openConfluencePicker(): Promise<ConfluenceSyncItem[] | null> {
		showConfluencePicker = true;
		return new Promise((resolve) => {
			pendingConfluenceResolve = resolve;
		});
	}

	function handleConfluenceSelect(event: CustomEvent<{ items: ConfluenceSyncItem[] }>) {
		if (pendingConfluenceResolve) {
			pendingConfluenceResolve(event.detail.items);
			pendingConfluenceResolve = null;
		}
	}

	$: if (!showConfluencePicker && pendingConfluenceResolve) {
		pendingConfluenceResolve(null);
		pendingConfluenceResolve = null;
	}

	$: isSyncBusy = Object.values(cloudSyncState).some((s) => s.isSyncing || s.isCancelling);
	$: activeProvider = knowledge?.type ? (CLOUD_PROVIDERS[knowledge.type] ?? null) : null;
	$: activeState = activeProvider ? cloudSyncState[activeProvider.type] : null;

	// Single derived guard for all structure-write affordances (decision 5) —
	// local/untyped KBs with write access only. Cloud KBs browse the
	// sync-written directory structure read-only; push KBs are browse-only.
	$: structureEditable = canEditStructure(knowledge);

	let largeScreen = true;

	let pane;
	let showSidepanel = true;

=======
>>>>>>> upstream/main
	let showAddWebpageModal = false;
	let showAddTextContentModal = false;
	let showNewDirectoryModal = false;

	let showSyncConfirmModal = false;
	let showCancelSyncConfirmModal = false;
	let showAccessControlModal = false;
	let showResetConfirm = false;

<<<<<<< HEAD
	// Local-directory upload/sync pipeline (upstream v0.10.2)
=======
>>>>>>> upstream/main
	type DirectoryFileEntry = { path: string; filename: string; file: File };
	type DirectoryManifestEntry = DirectoryFileEntry & { checksum: string; size: number };
	let pendingSyncFiles: DirectoryFileEntry[] | null = null;
	let syncing: string | null = null;

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
		type?: string;
		user_id?: string;
		meta?: Record<string, any>;
	};

	let id = null;
	let knowledge: Knowledge | null = null;
	let knowledgeId = null;

	// Present only in the "+ Add knowledge" builder flow: a path back to
	// the assistant being edited. When set, a "Back to assistant" button
	// returns there with ?selectKb=<this KB id> so it gets attached.
	$: returnTo = $page.url.searchParams.get('returnTo');

	let selectedFileId: string | null = null;
	let selectedFile = null;
	let showFilePreview = false;

	let inputFiles = null;

	let query = '';
	let includeContent = false;
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let viewOption = null;
	let sortKey = null;
	let direction = null;

	let currentPage = 1;
	let fileItems = null;
	let fileItemsTotal = null;

	// Directory state (upstream per-level browsing)
	let currentDirectoryId: string | null = null;
	let directoryItems: any[] = [];
	let breadcrumbs: { id: string; name: string }[] = [];
	// KB-wide file total for the cloud quota header (fileItemsTotal is
	// level/search-scoped in per-level browsing).
	let kbFileTotal: number | null = null;

	let showDeleteDirectoryConfirm = false;
	let pendingDeleteDirectoryId: string | null = null;
	let deleteDirectoryContents = true;

	// External (query-only) KB panel
	let externalTestQuery = '';
	let externalTestResult: {
		documents?: string[];
		metadatas?: Record<string, any>[];
		distances?: number[];
	} | null = null;
	$: isExternalKnowledge = knowledge?.meta?.source === 'external';

	let loaded = false;
	let queryDebounceActive = false;
	let fetchId = 0;

	// Coalesced view refresh for per-file events. Server-rendered rows only
	// resolve their spinners via a level re-fetch. Each burst of events
	// triggers at most one refresh per 2s, independent of isSyncing, so late
	// completions (files queued behind another KB's pipeline job) still flip.
	let treeRefreshTimer: ReturnType<typeof setTimeout> | null = null;
	const scheduleTreeRefresh = () => {
		if (treeRefreshTimer) return;
		treeRefreshTimer = setTimeout(() => {
			treeRefreshTimer = null;
			getItemsPage();
		}, 2000);
	};

	// While a cloud sync runs, poll the current level every 4s — socket
	// events cover most flips; this catches drops and rollup-count drift.
	const SYNC_LEVEL_POLL_MS = 4000;
	let syncLevelPoll: ReturnType<typeof setInterval> | null = null;
	$: {
		const wantPoll = loaded && (activeState?.isSyncing ?? false);
		if (wantPoll && !syncLevelPoll) {
			syncLevelPoll = setInterval(() => getItemsPage(), SYNC_LEVEL_POLL_MS);
		} else if (!wantPoll && syncLevelPoll) {
			clearInterval(syncLevelPoll);
			syncLevelPoll = null;
		}
	}

	// Multiselect (bulk delete) model — shared across all three list views.
	const selection = createKbSelection();
	const {
		count: bulkCount,
		breakdown: bulkBreakdown,
		allSelected: bulkAllSelected,
		indeterminate: bulkIndeterminate
	} = selection;
	let showBulkRemoveConfirm = false;

	// Clear the selection when the search query changes — search mode renders
	// a different row set, so a lingering selection would strand there.
	let lastSelQuery = '';
	$: if (query !== lastSelQuery) {
		lastSelQuery = query;
		selection.clear();
	}

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
		(void query, viewOption, sortKey, direction, currentPage, includeContent);

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

		const isCloudKb = knowledge?.type && knowledge.type !== 'local';

		// Upstream per-level browsing: one call returns the level's files
		// (30/page), child directories and breadcrumbs. With a query the
		// search goes KB-wide and flat (decision 4) — directory rows are
		// hidden and each hit renders its meta.relative_path breadcrumb.
		// Cloud/push KBs additionally need a cheap KB-wide total (limit=1,
		// metadata-only) to keep the quota header honest — fileItemsTotal
		// is level-scoped here.
		const isSearching = !!query;
		const [res, totalRes] = await Promise.all([
			searchKnowledgeFilesById(
				localStorage.token,
				knowledge.id,
				query,
				viewOption,
				sortKey,
				direction,
				currentPage,
				null,
				true,
				isSearching ? undefined : (currentDirectoryId ?? null),
				isSearching ? includeContent : false
			).catch(() => null),
			isCloudKb
				? searchKnowledgeFilesById(
						localStorage.token,
						knowledge.id,
						'',
						null,
						null,
						null,
						1,
						1,
						true
					).catch(() => null)
				: Promise.resolve(null)
		]);

		if (currentFetchId !== fetchId) return; // Stale response, discard

		if (res) {
			fileItems = res.items;
			fileItemsTotal = res.total;
			if (!isSearching) {
				directoryItems = res.directories ?? [];
				breadcrumbs = res.breadcrumbs ?? [];
			}
		}
		if (totalRes) {
			kbFileTotal = totalRes.total;
		}
		queryDebounceActive = false;
		return res;
	};

	// Open the read-only file preview popup. FileItemModal fetches its own
	// content; `file` may come from the flat list (top-level name/size + meta)
	// or the lazy tree ({ id, name, meta: { name, size } }).
	const openFilePreview = (file) => {
		if (!file) {
			selectedFile = null;
			showFilePreview = false;
			return;
		}

		selectedFile = {
			id: file.id,
			name: file?.meta?.name ?? file?.name,
			type: 'file',
			size: file?.meta?.size ?? file?.size,
			meta: file?.meta ?? { name: file?.name, size: file?.size }
		};
		showFilePreview = true;
	};

	// Closing the preview (via the modal's own close button) also clears the
	// row selection highlight in the file list.
	$: if (!showFilePreview && selectedFileId !== null) {
		selectedFileId = null;
		selectedFile = null;
	}

	const createFileFromText = (name, content) => {
		const blob = new Blob([content], { type: 'text/plain' });
		const file = blobToFile(blob, `${name}.txt`);

		console.log(file);
		return file;
	};

	const uploadWeb = async (urls) => {
		if (!knowledge) {
			toast.error($i18n.t('Knowledge base not found.'));
			return;
		}

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
				const res = await processUrl(localStorage.token, fileItem.url).catch((e) => {
					console.error('Error processing URL:', e);
					return null;
				});

				if (res) {
					console.log(res);
					let uploadedFile = res.file;

<<<<<<< HEAD
					const uploadedFile = await uploadFile(localStorage.token, file).catch((e) => {
						toast.error(`${e}`);
						return null;
					});
=======
					if (res.type === 'web' || res.type === 'youtube') {
						const file = createFileFromText(
							// Use URL as filename, sanitized
							fileItem.url
								.replace(/[^a-z0-9]/gi, '_')
								.toLowerCase()
								.slice(0, 50),
							res.content ?? ''
						);

						uploadedFile = await uploadFile(localStorage.token, file, {
							knowledge_id: knowledge.id,
							directory_id: currentDirectoryId,
							source_url: fileItem.url
						}).catch((e) => {
							toast.error(`${e}`);
							return null;
						});
					} else if (uploadedFile?.id) {
						const linkedKnowledge = await addFileToKnowledgeById(
							localStorage.token,
							knowledge.id,
							uploadedFile.id,
							currentDirectoryId
						).catch((e) => {
							toast.error(`${e}`);
							return null;
						});
						if (!linkedKnowledge) {
							uploadedFile = null;
						}
					}
>>>>>>> upstream/main

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

		// Reject disallowed file types client-side, before uploading. The server
		// allow-list only rejects after the whole file has been received, so a
		// large disallowed file (e.g. a .dmg) would otherwise upload in full
		// before failing. An empty extension passes through, matching the backend
		// gate (extension-less docs are decided server-side by content type).
		const allowedExtensions = ($config?.file?.allowed_extensions ?? []).filter((ext) => ext);
		const dotIndex = file.name.lastIndexOf('.');
		const extension = dotIndex > 0 ? file.name.slice(dotIndex + 1).toLowerCase() : '';
		if (extension && allowedExtensions.length > 0 && !allowedExtensions.includes(extension)) {
			toast.error($i18n.t('File type {{extension}} is not allowed.', { extension }));
			return;
		}

		fileItems = [fileItem, ...(fileItems ?? [])];
		try {
			let metadata = {
				knowledge_id: knowledge.id,
				// Place the upload in the directory the user is browsing
				// (null = KB root; the backend derives relative_path from it).
				directory_id: currentDirectoryId,
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
				} else {
					// Don't call addFileHandler here — Socket.IO 'file:status' event
					// will trigger it when background processing completes. Arm a
					// 30s polling fallback in case that emit drops (see pollers).
					armUploadStatusFallback(uploadedFile.id);
				}
			} else {
				toast.error($i18n.t('Failed to upload file.'));
				// The upload call errored (e.g. allow-list 400) so no real file id
				// was assigned and no status poller was armed. Remove the optimistic
				// 'uploading' row (keyed by itemId — it never got an id) or it spins
				// forever until a page reload. Mirrors the uploadedFile.error branch
				// above and uploadWeb's cleanup.
				fileItems = fileItems.filter((item) => item.itemId !== fileItem.itemId);
			}
		} catch (e) {
			toast.error(`${e}`);
			fileItems = fileItems.filter((item) => item.itemId !== fileItem.itemId);
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
		// Structure-preserving upload: collect entries with their relative
		// paths, mirror the folder structure as knowledge_directory rows,
		// then upload each file into its directory.
		const entries = filterAllowedEntries(await collectDirectoryEntries());
		if (entries?.length) {
			await uploadDirectoryEntries(entries);
		}
	};

	// Helper function to check if a path contains hidden folders
	const hasHiddenFolder = (path) => {
		return path.split('/').some((part) => part.startsWith('.'));
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

	// ===== Structure-preserving directory upload + incremental sync (upstream v0.10.2) =====

	// Collect files (with their relative paths) from a picked directory
	// without uploading. Feeds the structure-preserving upload + sync flows.
	const collectDirectoryEntries = async (): Promise<DirectoryFileEntry[] | null> => {
		const isFileSystemAccessSupported = 'showDirectoryPicker' in window;

		try {
			if (isFileSystemAccessSupported) {
				const dirHandle = await window.showDirectoryPicker();
				const collected: DirectoryFileEntry[] = [];

				const traverse = async (handle: FileSystemDirectoryHandle, dirPath = '') => {
					for await (const entry of handle.values()) {
						if (entry.name.startsWith('.')) continue;
						const entryPath = dirPath ? `${dirPath}/${entry.name}` : entry.name;
						if (hasHiddenFolder(entryPath)) continue;

						if (entry.kind === 'file') {
							const file = await entry.getFile();
							collected.push({ path: dirPath, filename: entry.name, file });
						} else if (entry.kind === 'directory') {
							await traverse(entry, entryPath);
						}
					}
				};

				await traverse(dirHandle, dirHandle.name);
				return collected;
			} else {
				// Firefox fallback
				return new Promise((resolve, reject) => {
					const input = document.createElement('input');
					input.type = 'file';
					input.webkitdirectory = true;
					input.directory = true;
					input.multiple = true;
					input.style.display = 'none';
					document.body.appendChild(input);

					input.onchange = () => {
						try {
							const files = Array.from(input.files || []).filter(
								(file) => !hasHiddenFolder(file.webkitRelativePath) && !file.name.startsWith('.')
							);

							const collected = files.map((file) => {
								const parts = file.webkitRelativePath.split('/');
								const filename = parts.pop() || file.name;
								const path = parts.join('/');
								return { path, filename, file };
							});

							document.body.removeChild(input);
							resolve(collected);
						} catch (error) {
							document.body.removeChild(input);
							reject(error);
						}
					};

					input.onerror = (error) => {
						document.body.removeChild(input);
						reject(error);
					};

					input.click();
				});
			}
		} catch (error) {
			handleUploadError(error);
			return null;
		}
	};

	// Client-side extension allow-list for directory entries — mirrors
	// uploadFileHandler's gate, so a disallowed file never uploads in full
	// before the server rejects it. Disallowed files can't be in the KB, so
	// dropping them from a sync manifest is consistent with server state.
	const filterAllowedEntries = (
		entries: DirectoryFileEntry[] | null
	): DirectoryFileEntry[] | null => {
		if (!entries) return entries;
		const allowedExtensions = ($config?.file?.allowed_extensions ?? []).filter((ext) => ext);
		if (allowedExtensions.length === 0) return entries;

		const kept = entries.filter(({ filename }) => {
			const dotIndex = filename.lastIndexOf('.');
			const extension = dotIndex > 0 ? filename.slice(dotIndex + 1).toLowerCase() : '';
			return !extension || allowedExtensions.includes(extension);
		});

		const skipped = entries.length - kept.length;
		if (skipped > 0) {
			toast.warning(
				$i18n.t('{{count}} file(s) skipped: file type not allowed.', { count: skipped })
			);
		}
		return kept;
	};

	const buildDirectoryManifest = async (
		entries: DirectoryFileEntry[]
	): Promise<DirectoryManifestEntry[]> => {
		return Promise.all(
			entries.map(async (entry) => ({
				...entry,
				checksum: await computeFileHash(entry.file),
				size: entry.file.size
			}))
		);
	};

	const createMissingDirectories = async (diff: any) => {
		if (!knowledge) return {};

		const directoryIdByPath: Record<string, string> = { ...(diff.directory_map || {}) };

		for (const dirPath of diff.mkdir) {
			const segments = dirPath.split('/');
			const name = segments.at(-1)!;
			const parentPath = segments.slice(0, -1).join('/');
			const parentId = parentPath ? directoryIdByPath[parentPath] : null;

			const directory = await createKnowledgeDirectory(
				localStorage.token,
				knowledge.id,
				name,
				parentId
			);
			if (directory) {
				directoryIdByPath[dirPath] = directory.id;
			}
		}

		return directoryIdByPath;
	};

	const getDirectoryUploadPath = (path: string) => {
		const currentPath = breadcrumbs.map((crumb) => crumb.name).join('/');
		return currentPath && path ? `${currentPath}/${path}` : currentPath || path;
	};

<<<<<<< HEAD
	// Upload a set of manifest entries with bounded concurrency (the fork's
	// upload hardening), updating the `syncing` progress line as each lands.
	const uploadManifestEntries = async (
		entries: DirectoryManifestEntry[],
		resolveDirectoryId: (entry: DirectoryManifestEntry) => string | null
	) => {
		const total = entries.length;
		let done = 0;
		const executing: Set<Promise<void>> = new Set();

		for (const entry of entries) {
			const fileObject = new File([entry.file], entry.filename, { type: entry.file.type });
			const task = uploadFile(localStorage.token, fileObject, {
				knowledge_id: knowledge.id,
				file_hash: entry.checksum,
				directory_id: resolveDirectoryId(entry)
			})
				.catch((e) => {
					toast.error(`${e}`);
					return null;
				})
				.then(() => {
					done++;
					const displayPath = entry.path ? `${entry.path}/${entry.filename}` : entry.filename;
					syncing = $i18n.t('Uploading {{current}}/{{total}}: {{file}}', {
						current: done,
						total,
						file: displayPath
					});
					executing.delete(task);
				});
			executing.add(task);

			if (executing.size >= 5) {
				await Promise.race(executing);
			}
		}

		await Promise.all(executing);
=======
	const uploadManifestEntries = async (
		entries: DirectoryManifestEntry[],
		resolveDirectoryId: (entry: DirectoryManifestEntry) => string | null | undefined
	) => {
		let failedCount = 0;

		for (const [index, entry] of entries.entries()) {
			const displayPath = entry.path ? `${entry.path}/${entry.filename}` : entry.filename;
			syncing = $i18n.t('Uploading {{current}}/{{total}}: {{file}}', {
				current: index + 1,
				total: entries.length,
				file: displayPath
			});

			const fileObject = new File([entry.file], entry.filename, { type: entry.file.type });
			const uploadedFile = await uploadFile(localStorage.token, fileObject, {
				knowledge_id: knowledge.id,
				file_hash: entry.checksum,
				directory_id: resolveDirectoryId(entry)
			}).catch((error) => ({ error }));

			if (!uploadedFile || uploadedFile.error) {
				const error = uploadedFile?.error;
				const reason =
					typeof error === 'string'
						? error
						: (error?.detail ?? error?.message ?? $i18n.t('Failed to upload file.'));

				failedCount++;
				console.error('Upload failed:', displayPath, reason);
			}
		}

		if (failedCount > 0) {
			toast.error(
				$i18n.t('Upload failed for {{failed}} of {{total}} files.', {
					failed: failedCount,
					total: entries.length
				})
			);
		}

		return failedCount;
>>>>>>> upstream/main
	};

	const uploadDirectoryEntries = async (entries: DirectoryFileEntry[]) => {
		if (!knowledge) return;

		try {
			syncing = $i18n.t('Computing checksums ({{count}} files)', { count: entries.length });
			const manifest = await buildDirectoryManifest(entries);

			syncing = $i18n.t('Comparing with knowledge base...');
			const diff = await syncKnowledgeDiff(
				localStorage.token,
				id,
				manifest.map(({ filename, path, checksum, size }) => ({
					filename,
					path: getDirectoryUploadPath(path),
					checksum,
					size
				}))
			);

			if (!diff) {
				toast.error($i18n.t('Failed to compare files.'));
				return;
			}

			const directoryIdByPath = await createMissingDirectories(diff);

<<<<<<< HEAD
			await uploadManifestEntries(manifest, (entry) =>
				entry.path ? directoryIdByPath[getDirectoryUploadPath(entry.path)] : currentDirectoryId
			);

			toast.success($i18n.t('File uploaded successfully'));
			// Awaited: `finally` clears `syncing` on return, and that state change
			// re-triggers the reactive getItemsPage() above. Racing it against this
			// refresh let the fetchId guard discard the post-upload response, so a
			// freshly uploaded folder rendered with child_count 0 until you
			// navigated into it and back.
			await init();
=======
			const failedCount = await uploadManifestEntries(manifest, (entry) =>
				entry.path ? directoryIdByPath[getDirectoryUploadPath(entry.path)] : currentDirectoryId
			);

			if (failedCount === 0) {
				toast.success($i18n.t('File uploaded successfully'));
			}

			init();
>>>>>>> upstream/main
		} catch (e) {
			toast.error(`${e}`);
		} finally {
			syncing = null;
		}
	};

	// Incremental sync: hash locally → diff on server → upload only what
	// changed, remove what disappeared, mirror the folder structure.
	const syncDirectoryHandler = async () => {
		if (!structureEditable || !pendingSyncFiles?.length) return;

		try {
			syncing = $i18n.t('Computing checksums ({{count}} files)', {
				count: pendingSyncFiles.length
			});
			const manifest = await buildDirectoryManifest(pendingSyncFiles);
			pendingSyncFiles = null;

			syncing = $i18n.t('Comparing with knowledge base...');
			const diff = await syncKnowledgeDiff(
				localStorage.token,
				id,
				manifest.map(({ filename, path, checksum, size }) => ({ filename, path, checksum, size }))
			);

			if (!diff) {
				toast.error($i18n.t('Failed to compare files.'));
				return;
			}

			// Cleanup — remove deleted + stale modified files first (routes
			// through the fork's full deletion cascade server-side).
			const staleFileIds = [
				...diff.deleted.map((d: any) => d.file_id),
				...diff.modified.map((m: any) => m.stale_file_id)
			];

			if (staleFileIds.length > 0 || diff.rmdir.length > 0) {
				syncing = $i18n.t('Removing {{count}} stale files...', { count: staleFileIds.length });
				await syncKnowledgeCleanup(localStorage.token, id, staleFileIds, diff.rmdir);
			}

			// Create missing directories (parents first)
			const directoryIdByPath = await createMissingDirectories(diff);

			// Upload added + modified files only
			const filesToUpload = manifest.filter(
				(entry) =>
					diff.added.some((a: any) => a.filename === entry.filename && a.path === entry.path) ||
					diff.modified.some((m: any) => m.filename === entry.filename && m.path === entry.path)
			);

<<<<<<< HEAD
			await uploadManifestEntries(filesToUpload, (entry) =>
				entry.path ? directoryIdByPath[entry.path] : null
			);

			toast.success(
				$i18n.t(
					'Sync complete: {{added}} added, {{modified}} modified, {{deleted}} deleted, {{unmodified}} unmodified',
					{
						added: diff.added.length,
						modified: diff.modified.length,
						deleted: diff.deleted.length,
						unmodified: diff.unmodified_count
					}
				)
			);
			// Awaited for the same reason as uploadDirectoryEntries above --
			// same try/finally shape, same refresh race.
			await init();
=======
			const failedCount = await uploadManifestEntries(filesToUpload, (entry) =>
				entry.path ? directoryIdByPath[entry.path] : null
			);

			// ── 7. Report ──
			if (failedCount === 0) {
				toast.success(
					$i18n.t(
						'Sync complete: {{added}} added, {{modified}} modified, {{deleted}} deleted, {{unmodified}} unmodified',
						{
							added: diff.added.length,
							modified: diff.modified.length,
							deleted: diff.deleted.length,
							unmodified: diff.unmodified_count
						}
					)
				);
			}
			init();
>>>>>>> upstream/main
		} catch (e) {
			toast.error(`${e}`);
		} finally {
			syncing = null;
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

				syncItems = items.map((item) => ({
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

				syncItems = result.items.map((item) => ({
					type: item.type,
					item_id: item.id,
					item_path: item.path,
					name: item.name
				}));

				state.refreshDone = false;
				cloudSyncState = cloudSyncState;
				await startGoogleDriveSyncItems(localStorage.token, {
					knowledge_id: knowledge.id,
					items: syncItems as GoogleDriveSyncItem[]
				});
			} else if (provider.type === 'confluence') {
				// Ensure we have a valid OAuth token before opening the picker.
				const tokenStatus = await provider.api
					.getTokenStatus(localStorage.token, knowledge.id)
					.catch(() => null);
				const tokenValid = !!(
					tokenStatus &&
					tokenStatus.has_token &&
					!tokenStatus.is_expired &&
					!tokenStatus.needs_reauth
				);
				if (!tokenValid) {
					toast.info($i18n.t('Authorize Confluence to continue.'));
					await authorizeBackgroundSync(provider);
					const recheck = await provider.api
						.getTokenStatus(localStorage.token, knowledge.id)
						.catch(() => null);
					const nowValid = !!(
						recheck &&
						recheck.has_token &&
						!recheck.is_expired &&
						!recheck.needs_reauth
					);
					if (!nowValid) {
						state.isSyncing = false;
						cloudSyncState = cloudSyncState;
						return;
					}
				}

				const items = await openConfluencePicker();
				if (!items || items.length === 0) {
					state.isSyncing = false;
					cloudSyncState = cloudSyncState;
					return;
				}

				syncItems = items;

				state.refreshDone = false;
				cloudSyncState = cloudSyncState;
				await startConfluenceSyncItems(localStorage.token, {
					knowledge_id: knowledge.id,
					items: syncItems as ConfluenceSyncItem[]
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
			const rawError = error instanceof Error ? error.message : String(error);
			// Translate known OneDrive host-derivation errors via static keys (so they
			// stay i18n-discoverable); fall back to the raw message for everything else.
			const errorDetail =
				rawError === 'No OneDrive found for your account.'
					? $i18n.t('No OneDrive found for your account.')
					: rawError === 'Could not connect to OneDrive to determine your location.'
						? $i18n.t('Could not connect to OneDrive to determine your location.')
						: rawError;
			toast.error(
				$i18n.t('Failed to sync from {{label}}: ', { label: provider.label }) + errorDetail
			);
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
					items: syncItems
				});
			} else if (provider.type === 'confluence') {
				// Stored sources have type='folder'/'file' (translated by the backend
				// on first sync) and the original under confluence_type. The API model
				// requires 'space' | 'page', so prefer confluence_type.
				const syncItems: ConfluenceSyncItem[] = sources.map((source: any) => ({
					type: source.confluence_type ?? source.type,
					cloud_id: source.cloud_id,
					space_id: source.space_id,
					space_key: source.space_key,
					site_url: source.site_url,
					item_id: source.item_id,
					item_path: source.item_path,
					name: source.name,
					include_descendants: source.include_descendants ?? true
				}));

				state.refreshDone = false;
				cloudSyncState = cloudSyncState;
				await startConfluenceSyncItems(localStorage.token, {
					knowledge_id: knowledge.id,
					items: syncItems
				});
			}

			toast.success($i18n.t('{{label}} sync started', { label: provider.label }));
			pollCloudSyncStatus(provider);
		} catch (error) {
			console.error(`${provider.label} resync error:`, error);
			toast.error(
				$i18n.t('Failed to start sync: ') + (error instanceof Error ? error.message : String(error))
			);
			state.isSyncing = false;
			cloudSyncState = cloudSyncState;
		}
	};

	const pollCloudSyncStatus = async (provider: CloudSyncProvider) => {
		const state = cloudSyncState[provider.type];
		try {
			const fetched = await provider.api.getSyncStatus(localStorage.token, knowledge.id);
			// Merge instead of replace so the higher-frequency Socket.IO event
			// stream (which carries stage_counts) doesn't get clobbered every
			// 2s by this polling fallback.
			state.syncStatus = {
				...(state.syncStatus ?? {}),
				...fetched,
				stage_counts: fetched.stage_counts ?? state.syncStatus?.stage_counts
			};
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
			} else if (
				state.syncStatus.status === 'completed' ||
				state.syncStatus.status === 'completed_with_errors'
			) {
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
					toast.error(
						$i18n.t('{{label}} sync failed: {{error}}', {
							label: provider.label,
							error: state.syncStatus.error
						})
					);
					state.isSyncing = false;
					cloudSyncState = cloudSyncState;
				}
				// Failed files carry status 'error' in meta — refresh so the
				// tree shows error badges instead of eternal spinners.
				if (!state.refreshDone) {
					state.refreshDone = true;
					cloudSyncState = cloudSyncState;
					await init();
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
			toast.error(
				$i18n.t('Failed to cancel sync: ') +
					(error instanceof Error ? error.message : String(error))
			);
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
			case 'config_error':
				return $i18n.t('Sync configuration error');
			case 'schema_error':
				return $i18n.t('Document processor error');
			case 'needs_token_refresh':
				return $i18n.t('Reauthorization required');
			case 'unsupported_content_type':
				return $i18n.t('Unsupported file type');
			case 'source_access_revoked':
				return $i18n.t('Access revoked');
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

		// Show only the filename + translated category label. The
		// loader-worker's underlying ``error_message`` is English and
		// frequently includes opaque IDs / URLs (e.g. Drive file IDs in
		// 403 responses); user-facing strings should be consistent and
		// translatable, so we omit it from the toast. The full text is
		// preserved server-side in ``last_result.failed_files`` for
		// operator debugging.
		const lines = filesToShow.map((f) => `- ${f.filename}: ${getErrorTypeMessage(f.error_type)}`);

		if (remaining > 0) {
			lines.push($i18n.t('and {{COUNT}} more', { COUNT: remaining }));
		}

		return '\n' + lines.join('\n');
	};

	// ===== Generic socket handlers =====

	function handleCloudFileProcessing(
		providerType: string,
		data: {
			knowledge_id: string;
			file: {
				item_id: string;
				name: string;
				size?: number;
				source_item_id?: string;
				relative_path?: string;
			};
		}
	) {
		const provider = CLOUD_PROVIDERS[providerType];
		const state = cloudSyncState[providerType];

		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) return;

		// Lazy tree: the fileItems mutation below has no tree effect — nudge
		// the tree itself so the new file's spinner appears without a reload.
		scheduleTreeRefresh();

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

	function handleCloudFileAdded(
		providerType: string,
		data: {
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
		}
	) {
		const state = cloudSyncState[providerType];
		const provider = CLOUD_PROVIDERS[providerType];

		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) return;

		// Lazy tree: this file just reached 'ok' server-side — nudge the tree
		// so its spinner flips to done within the throttle window.
		scheduleTreeRefresh();

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

	async function handleCloudSyncProgress(
		providerType: string,
		data: {
			knowledge_id: string;
			status: string;
			current: number;
			total: number;
			filename: string;
			error?: string;
			files_processed?: number;
			files_failed?: number;
			files_added?: number;
			files_updated?: number;
			files_unchanged?: number;
			files_removed?: number;
			deleted_count?: number;
			failed_files?: FailedFile[];
			stage_counts?: import('$lib/apis/sync').StageCounts;
		}
	) {
		const state = cloudSyncState[providerType];
		const provider = CLOUD_PROVIDERS[providerType];

		// Only process events for the current knowledge base
		if (data.knowledge_id !== knowledge?.id) return;

		// Update sync status. Preserve any existing stage_counts when the
		// incoming event doesn't carry one (e.g. completion/cancellation
		// emits) — otherwise the SyncProgress component unmounts and the UI
		// flickers between bar and legacy badge mid-sync.
		state.syncStatus = {
			...(state.syncStatus ?? {}),
			knowledge_id: data.knowledge_id,
			status: data.status as SyncStatusResponse['status'],
			progress_current: data.current,
			progress_total: data.total,
			error: data.error,
			failed_files: data.failed_files,
			stage_counts: data.stage_counts ?? state.syncStatus?.stage_counts
		};
		cloudSyncState = cloudSyncState;

		// A 'syncing' event while we're not tracking a sync means it was
		// started elsewhere (scheduler, another tab). Adopt it — mirrors the
		// resume-on-load path in onMount: flags the progress banner + the
		// tree's 4s poll active, and arms the HTTP status fallback so the
		// terminal is caught even if the terminal socket event is missed.
		if (data.status === 'syncing' && !state.isSyncing) {
			state.isSyncing = true;
			state.refreshDone = false;
			cloudSyncState = cloudSyncState;
			pollCloudSyncStatus(provider);
		}

		// Handle access revoked
		if (data.status === 'access_revoked') {
			toast.warning(
				data.error ||
					$i18n.t('Access to a {{label}} source has been revoked', { label: provider.label })
			);
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
			const failed = data.files_failed ?? 0;
			const { variant, message } = buildSyncToast($i18n, provider.label, {
				added: data.files_added ?? 0,
				updated: data.files_updated ?? 0,
				unchanged: data.files_unchanged ?? 0,
				failed,
				removed: data.files_removed ?? data.deleted_count ?? 0
			});
			const failedDetails =
				failed > 0 && data.failed_files ? formatFailedFilesMessage(data.failed_files) : '';
			toast[variant](message + failedDetails);
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
			toast.error(
				$i18n.t('{{label}} sync failed: {{error}}', {
					label: provider.label,
					error: data.error || 'Unknown error'
				})
			);
			state.isSyncing = false;
			cloudSyncState = cloudSyncState;
			// Failed files carry status 'error' in meta — refresh so the tree
			// shows error badges instead of eternal spinners.
			if (!state.refreshDone) {
				state.refreshDone = true;
				cloudSyncState = cloudSyncState;
				await init();
			}
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

	const removeCloudSourceHandler = async (
		provider: CloudSyncProvider,
		itemId: string,
		sourceName: string
	) => {
		try {
			const result = await provider.api.removeSource(localStorage.token, knowledge.id, itemId);
			toast.success(
				$i18n.t('Source "{{name}}" removed. {{count}} file(s) cleaned up.', {
					name: result.source_name,
					count: result.files_removed
				})
			);
			// Refresh knowledge metadata and file list
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
			await init();
		} catch (e) {
			console.error(`Error removing ${provider.label} source:`, e);
			toast.error(
				$i18n.t('Failed to remove source: {{error}}', {
					error: e instanceof Error ? e.message : String(e)
				})
			);
		}
	};

	let uploadBatch = { added: 0, failed: 0 };
	let fileStatusQueue: Promise<void> = Promise.resolve();
	// Polling fallback: when a 'file:status' Socket.IO emit drops on the
	// floor (typical cause: the loop_bridge couldn't reach uvicorn's main
	// loop, see utils/loop_bridge.py), the upload spinner sits forever.
	// Each pending upload gets a 30s timer that, if the spinner is still
	// up, polls /files/{id}/process/status until it terminates or the
	// 5-minute hard cap kicks in.
	const pollers = new Map<string, ReturnType<typeof setInterval>>();

	const showBatchedUploadToast = () => {
		if (uploadBatch.added === 0 && uploadBatch.failed === 0) return;
		const { variant, message } = buildSyncToast($i18n, null, {
			added: uploadBatch.added,
			failed: uploadBatch.failed
		});
		toast[variant](message);
		uploadBatch = { added: 0, failed: 0 };
		init();
	};

	const startPollingFallback = (fileId: string) => {
		if (pollers.has(fileId)) return;
		const startedAt = Date.now();
		const interval = setInterval(async () => {
			if (Date.now() - startedAt > 5 * 60 * 1000) {
				handleFileStatus({ file_id: fileId, status: 'failed', error: 'Processing timed out' });
				clearInterval(interval);
				pollers.delete(fileId);
				return;
			}
			try {
				const res = await fetch(`${WEBUI_API_BASE_URL}/files/${fileId}/process/status`, {
					headers: { Authorization: `Bearer ${localStorage.token}` }
				});
				if (!res.ok) return;
				const data = await res.json();
				if (data?.status === 'completed' || data?.status === 'failed') {
					handleFileStatus({ file_id: fileId, status: data.status, error: data.error });
					clearInterval(interval);
					pollers.delete(fileId);
				}
			} catch (_e) {
				// Network blip; let the next tick retry.
			}
		}, 5000);
		pollers.set(fileId, interval);
	};

	const armUploadStatusFallback = (fileId: string) => {
		setTimeout(() => {
			if (!fileItems) return;
			const item = fileItems.find((f: { id: string; status: string }) => f.id === fileId);
			if (item?.status === 'uploading') {
				startPollingFallback(fileId);
			}
		}, 30_000);
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
		// Lazy tree: file rows render from the server-side tree, and the
		// fileItems lookup below misses (the array is kept empty in lazy
		// mode) — nudge the tree so a direct upload's spinner resolves.
		scheduleTreeRefresh();

		if (!fileItems) return;

		const idx = fileItems.findIndex((f) => f.id === data.file_id);
		if (idx < 0) return;

		// If the polling fallback is still ticking, stop it — the socket
		// emit (or another poll) just landed.
		const poller = pollers.get(data.file_id);
		if (poller) {
			clearInterval(poller);
			pollers.delete(data.file_id);
		}

		if (data.status === 'completed') {
			fileItems[idx].status = 'uploaded';
			// A completed file can still carry a warning (e.g. warren parsed zero
			// chunks — a scanned/no-text PDF). It's kept as a member but flagged so
			// the list shows a warning triangle; mirrors meta.warning on reload.
			if (data.error) {
				fileItems[idx].warning = data.error;
			}
			// Backend already linked the file to the KB during upload (see
			// process_uploaded_file in routers/files.py — Phase 2 of the
			// 2026-05-25 plan). Re-invoking addFileHandler here would call
			// /knowledge/{id}/file/add and re-trigger process_file, embedding
			// the file's chunks a second time into the KB collection.
			uploadBatch.added++;
		} else if (data.status === 'failed') {
			fileItems[idx].status = 'error';
			fileItems[idx].error = data.error || 'Processing failed';
			uploadBatch.failed++;
			fileItems = fileItems.filter((file) => file.id !== data.file_id);
		}

		fileItems = fileItems;

		const stillUploading = fileItems.some((f) => f.status === 'uploading');
		if (!stillUploading) {
			showBatchedUploadToast();
		}
	};

	const addFileHandler = async (fileId, { batch = false } = {}) => {
		const res = await addFileToKnowledgeById(
			localStorage.token,
			id,
			fileId,
			currentDirectoryId
		).catch((e) => {
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

	// ===== Directory handlers (upstream per-level navigation + CRUD) =====

	const navigateToDirectory = (directoryId: string | null) => {
		currentDirectoryId = directoryId;
		currentPage = 1;
		selectedFileId = null;
		selectedFile = null;
		selection.clear();
		getItemsPage();
	};

	const createDirectoryHandler = async (name: string) => {
		if (!structureEditable) return;
		const res = await createKnowledgeDirectory(
			localStorage.token,
			knowledge.id,
			name,
			currentDirectoryId
		).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Directory created.'));
			getItemsPage();
		}
	};

<<<<<<< HEAD
	const renameDirectoryHandler = async (dirId: string, name: string) => {
		if (!structureEditable) return;
		const res = await updateKnowledgeDirectory(localStorage.token, knowledge.id, dirId, {
			name
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});
=======
	const openFileHandler = (fileId: string) => {
		window.open(`${WEBUI_API_BASE_URL}/files/${encodeURIComponent(fileId)}/content`, '_blank');
	};

	let debounceTimeout = null;
>>>>>>> upstream/main

		if (res) {
			toast.success($i18n.t('Directory renamed.'));
			getItemsPage();
		}
	};

	const confirmDeleteDirectory = (dirId: string) => {
		if (!structureEditable) return;
		pendingDeleteDirectoryId = dirId;
		showDeleteDirectoryConfirm = true;
	};

	const deleteDirectoryHandler = async (moveFiles: boolean) => {
		if (!structureEditable || !pendingDeleteDirectoryId) return;

		const res = await deleteKnowledgeDirectory(
			localStorage.token,
			knowledge.id,
			pendingDeleteDirectoryId,
			moveFiles
		).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Directory deleted.'));
			getItemsPage();
		}
		pendingDeleteDirectoryId = null;
	};

	const moveFilesToDirectoryHandler = async (fileIds: string[], directoryId: string | null) => {
		if (!structureEditable || fileIds.length === 0) return;

		let moved = 0;
		for (const fileId of fileIds) {
			const res = await moveFileInKnowledge(
				localStorage.token,
				knowledge.id,
				fileId,
				directoryId
			).catch((e) => {
				toast.error(`${e}`);
				return null;
			});
			if (res) moved++;
		}

		if (moved > 0) {
			toast.success(
				moved === 1 ? $i18n.t('File moved.') : $i18n.t('Moved {{count}} files.', { count: moved })
			);
			selection.clear();
			getItemsPage();
		}
	};

	const moveDirectoryHandler = async (dirId: string, targetParentId: string | null) => {
		if (!structureEditable || dirId === targetParentId) return;
		const res = await updateKnowledgeDirectory(localStorage.token, knowledge.id, dirId, {
			parent_id: targetParentId
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Directory moved.'));
			getItemsPage();
		}
	};

	const externalTestHandler = async () => {
		if (!isExternalKnowledge || !externalTestQuery.trim()) return;

		const external = knowledge?.meta?.external ?? {};
		const res = await testExternalKnowledgeRetrieval(localStorage.token, external.connection_id, {
			query: externalTestQuery,
			source: external.source,
			count: 5
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			externalTestResult = res;
		}
	};

	// Bulk remove: replays each selected item's own removal (file-remove,
	// directory-delete, or remove-source) without per-item toast/init, then
	// refreshes once. Directories always delete their contents.
	const bulkRemoveHandler = async () => {
		const items = [...get(selection.selected).values()];
		if (items.length === 0) return;

		let ok = 0;
		let removedSource = false;
		for (const item of items) {
			try {
				if (item.kind === 'file') {
					await removeFileFromKnowledgeById(localStorage.token, id, item.fileId);
					ok++;
				} else if (item.kind === 'directory') {
					const res = await deleteKnowledgeDirectory(localStorage.token, id, item.dirId, false);
					if (res) ok++;
				} else if (activeProvider) {
					await activeProvider.api.removeSource(localStorage.token, knowledge.id, item.itemId);
					removedSource = true;
					ok++;
				}
			} catch (e) {
				console.error('Bulk remove failed for', item.key, e);
			}
		}

		toast[ok > 0 ? 'success' : 'error'](
			$i18n.t('Removed {{ok}} of {{total}} items', { ok, total: items.length })
		);

		selection.clear();

		if (removedSource) {
			const res = await getKnowledgeById(localStorage.token, id);
			if (res) {
				knowledge = res;
			}
		}
		await init();
	};

	let debounceTimeout = null;
	let mediaQuery;
	let dragged = false;

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

<<<<<<< HEAD
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

	// Path-preserving traversal of dropped directory entries (upstream) —
	// feeds uploadDirectoryEntries so dropped folders keep their structure.
=======
>>>>>>> upstream/main
	const readDirectoryEntries = async (reader: any) => {
		const entries: any[] = [];

		for (;;) {
			const batch = await new Promise<any[]>((resolve, reject) => {
				reader.readEntries(resolve, reject);
			});

			if (batch.length === 0) {
				break;
			}

			entries.push(...batch);
		}

		return entries;
	};

	const collectDroppedEntryFiles = async (
		entry: any,
		entryPath = entry.name
	): Promise<DirectoryFileEntry[]> => {
		if (entry.name.startsWith('.') || hasHiddenFolder(entryPath)) {
			return [];
		}

		if (entry.isFile) {
			const file = await new Promise<File>((resolve, reject) => {
				entry.file(resolve, reject);
			});
			const parts = entryPath.split('/');
			const filename = parts.pop() || file.name;
			return [{ path: parts.join('/'), filename, file }];
		}

		if (entry.isDirectory) {
			const reader = entry.createReader();
			const entries = await readDirectoryEntries(reader);
			const nested = await Promise.all(
				entries.map((child) => collectDroppedEntryFiles(child, `${entryPath}/${child.name}`))
			);
			return nested.flat();
		}

		return [];
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

		// Cloud-synced KBs on the upstream scaffold are browse-only — the sync
		// pipeline owns their content and structure (decision 5). Legacy mode
		// keeps its old permissive behavior until the kill-switch retires.
		if (!isLocalKnowledgeType(knowledge?.type)) {
			toast.error($i18n.t('Files in this knowledge base are managed by cloud sync.'));
			return;
		}

		if (e.dataTransfer?.types?.includes('Files') && e.dataTransfer?.items) {
			const inputItems = e.dataTransfer.items;
			if (inputItems.length > 0) {
				// Split loose files (fork concurrency pipeline) from dropped
				// directories (structure-preserving pipeline).
				const directoryEntries: DirectoryFileEntry[] = [];
				const looseFiles: File[] = [];

<<<<<<< HEAD
				for (const rawItem of Array.from(inputItems)) {
					const item = rawItem as DataTransferItem & { webkitGetAsEntry?: () => any };
					const entry = item.webkitGetAsEntry?.();

					if (entry?.isDirectory) {
						directoryEntries.push(...(await collectDroppedEntryFiles(entry)));
					} else {
						const file = item.getAsFile();
						if (file) {
							looseFiles.push(file);
=======
						if (entry?.isDirectory) {
							try {
								directoryEntries.push(...(await collectDroppedEntryFiles(entry)));
							} catch (error) {
								handleUploadError(error);
								return;
							}
						} else {
							const file = item.getAsFile();
							if (file) {
								looseFiles.push(file);
							}
>>>>>>> upstream/main
						}
					}
				}

				if (looseFiles.length > 0) {
					await uploadFiles(looseFiles);
				}

				const allowedEntries = filterAllowedEntries(directoryEntries);
				if (allowedEntries?.length) {
					await uploadDirectoryEntries(allowedEntries);
				}
			} else {
				toast.error($i18n.t(`File not found.`));
			}
		}
	};

	// ===== Socket event handler references (for cleanup) =====
	const socketHandlers: Array<{ event: string; handler: Function }> = [];

	onMount(async () => {
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
				{ event: `${provider.eventPrefix}:file:added`, handler: addedHandler }
			);
		}

		// Listen for file processing status events via Socket.IO
		$socket?.on('file:status', handleFileStatus);
	});

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
		if (treeRefreshTimer) {
			clearTimeout(treeRefreshTimer);
			treeRefreshTimer = null;
		}
		if (syncLevelPoll) {
			clearInterval(syncLevelPoll);
			syncLevelPoll = null;
		}
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

		// Stop any in-flight upload polling fallbacks
		for (const interval of pollers.values()) {
			clearInterval(interval);
		}
		pollers.clear();
	});
</script>

<svelte:window
	on:keydown={(e) => {
		if (e.key === 'Escape' && $bulkCount > 0) {
			selection.clear();
		}
	}}
	on:pointerup={() => selection.endDrag()}
/>

<FilesOverlay show={dragged} />
<SyncConfirmDialog
	bind:show={showSyncConfirmModal}
	message={$i18n.t(
		'{{count}} files selected. Only new and modified files will be uploaded. Deleted files will be removed. The folder structure will be mirrored. Continue?',
		{ count: pendingSyncFiles?.length ?? 0 }
	)}
	on:confirm={() => {
		syncDirectoryHandler();
	}}
	on:cancel={() => {
		pendingSyncFiles = null;
	}}
/>

<SyncConfirmDialog
	bind:show={showCancelSyncConfirmModal}
	title={$i18n.t(activeProvider ? `Cancel ${activeProvider.label} Sync` : 'Cancel Sync')}
	message={$i18n.t(
		'Are you sure you want to cancel the ongoing sync? Files already synced will be kept.'
	)}
	confirmLabel={$i18n.t('Cancel Sync')}
	on:confirm={() => {
		if (activeProvider) {
			cancelCloudSyncHandler(activeProvider);
		}
	}}
/>

<SyncConfirmDialog
	bind:show={showBulkRemoveConfirm}
	title={$bulkBreakdown.sources > 0
		? $i18n.t('Delete {{fileCount}} file(s) and {{sourceCount}} source(s)?', {
				fileCount: $bulkBreakdown.totalFiles,
				sourceCount: $bulkBreakdown.sources
			})
		: $bulkBreakdown.directories > 0
			? $i18n.t('Delete {{fileCount}} file(s) and {{folderCount}} folder(s)?', {
					fileCount: $bulkBreakdown.totalFiles,
					folderCount: $bulkBreakdown.directories
				})
			: $i18n.t('Delete {{count}} files?', { count: $bulkBreakdown.totalFiles })}
	message={$bulkBreakdown.sources > 0
		? $i18n.t('Removing a source stops its sync and deletes all of its files.')
		: $bulkBreakdown.directories > 0
			? $i18n.t('Deleting a folder also deletes all files inside it.')
			: $i18n.t('This will remove the selected files from this knowledge base.')}
	confirmLabel={$i18n.t('Delete')}
	on:confirm={() => {
		bulkRemoveHandler();
	}}
/>

<AttachWebpageModal
	bind:show={showAddWebpageModal}
	onSubmit={async (e) => {
		uploadWeb(e.data);
	}}
/>

<ConfluencePickerModal bind:show={showConfluencePicker} on:select={handleConfluenceSelect} />

<AddTextContentModal
	bind:show={showAddTextContentModal}
	on:submit={(e) => {
		const file = createFileFromText(e.detail.name, e.detail.content);
		uploadFileHandler(file);
	}}
/>

<NewDirectoryModal
	bind:show={showNewDirectoryModal}
	on:submit={(e) => {
		createDirectoryHandler(e.detail.name);
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
			const sortedFiles = Array.from(inputFiles).sort((a, b) => b.name.localeCompare(a.name));
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
						await updateKnowledgeAccessGrants(
							localStorage.token,
							id,
							knowledge.access_grants ?? []
						);
						toast.success($i18n.t('Saved'));
					} catch (error) {
						toast.error(`${error}`);
					}
				}}
				accessRoles={['read', 'write']}
			/>
		{/if}
		<div class="w-full px-2">
			<button
				class="mb-1 flex h-6 w-fit items-center gap-1 rounded-md text-xs text-gray-400 transition-colors duration-75 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
				type="button"
				on:click={() => {
					goto('/workspace/knowledge');
				}}
			>
				<ChevronLeft className="size-3" strokeWidth="2" />
				<span>{$i18n.t('Back')}</span>
			</button>

			<div class=" flex w-full">
<<<<<<< HEAD
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
=======
				<div class="flex-1 px-1">
>>>>>>> upstream/main
					<div class="flex items-center justify-between w-full">
						<div class="w-full flex justify-between items-center">
							<input
								type="text"
<<<<<<< HEAD
								class="text-left w-full font-medium text-lg font-primary bg-transparent outline-hidden flex-1"
=======
								class="text-left w-full text-sm bg-transparent outline-hidden flex-1"
>>>>>>> upstream/main
								bind:value={knowledge.name}
								aria-label={$i18n.t('Knowledge Name')}
								placeholder={$i18n.t('Knowledge Name')}
								disabled={!knowledge?.write_access}
								on:input={() => {
									changeDebounceHandler();
								}}
							/>

							<div class="shrink-0 mr-2.5 flex items-center gap-2">
								{#if returnTo}
									<button
										class="px-3 py-1 text-sm rounded-full bg-black text-white dark:bg-white dark:text-black font-medium shrink-0"
										type="button"
										on:click={() =>
											goto(
												`${returnTo}${returnTo.includes('?') ? '&' : '?'}selectKb=${knowledge?.id}`
											)}
									>
										{$i18n.t('Back to assistant')}
									</button>
								{/if}
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
								{#if activeProvider && $config?.[activeProvider.configKey]?.has_client_secret && knowledge?.write_access}
									{#if activeState?.bgSyncNeedsReauth}
										<button
											class="text-xs text-red-500 hover:text-red-600 flex items-center gap-1"
											on:click={() => authorizeBackgroundSync(activeProvider)}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 16 16"
												fill="currentColor"
												class="size-3.5"
											>
												<path
													fill-rule="evenodd"
													d="M6.701 2.25c.577-1 2.02-1 2.598 0l5.196 9a1.5 1.5 0 0 1-1.299 2.25H2.804a1.5 1.5 0 0 1-1.3-2.25l5.197-9ZM8 4a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 8 4Zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
													clip-rule="evenodd"
												/>
											</svg>
											{$i18n.t('Re-authorize background sync')}
										</button>
									{:else if activeState?.bgSyncAuthorized}
										<span class="text-xs text-green-600 flex items-center gap-1">
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 16 16"
												fill="currentColor"
												class="size-3.5"
											>
												<path
													fill-rule="evenodd"
													d="M12.416 3.376a.75.75 0 0 1 .208 1.04l-5 7.5a.75.75 0 0 1-1.154.114l-3-3a.75.75 0 0 1 1.06-1.06l2.353 2.353 4.493-6.74a.75.75 0 0 1 1.04-.207Z"
													clip-rule="evenodd"
												/>
											</svg>
											{$i18n.t('Background sync enabled')}
										</span>
									{:else if knowledge?.meta?.[activeProvider.metaKey]?.sources?.length}
										<button
											class="text-xs text-blue-500 hover:text-blue-600 flex items-center gap-1"
											on:click={() => authorizeBackgroundSync(activeProvider)}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 16 16"
												fill="currentColor"
												class="size-3.5"
											>
												<path
													fill-rule="evenodd"
													d="M8 1a3.5 3.5 0 0 0-3.5 3.5V7A1.5 1.5 0 0 0 3 8.5v5A1.5 1.5 0 0 0 4.5 15h7a1.5 1.5 0 0 0 1.5-1.5v-5A1.5 1.5 0 0 0 11.5 7V4.5A3.5 3.5 0 0 0 8 1Zm2 6V4.5a2 2 0 1 0-4 0V7h4Z"
													clip-rule="evenodd"
												/>
											</svg>
											{$i18n.t('Enable background sync')}
										</button>
									{/if}
								{/if}
								{#if activeState?.isCancelling}
									<Tooltip content={$i18n.t('Click to cancel sync')}>
										<button class="p-1 rounded-lg text-gray-400 cursor-not-allowed" disabled>
											<Spinner className="size-3.5" />
										</button>
									</Tooltip>
								{:else if activeState?.isSyncing}
									<Tooltip content={$i18n.t('Click to cancel sync')}>
										<button
											class="p-1 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 text-blue-500 hover:text-red-500 transition"
											on:click={() => {
												showCancelSyncConfirmModal = true;
											}}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 16 16"
												fill="currentColor"
												class="size-4"
											>
												<path
													d="M4.5 2A2.5 2.5 0 0 0 2 4.5v7A2.5 2.5 0 0 0 4.5 14h7a2.5 2.5 0 0 0 2.5-2.5v-7A2.5 2.5 0 0 0 11.5 2h-7Z"
												/>
											</svg>
										</button>
									</Tooltip>
								{:else if activeProvider && knowledge?.meta?.[activeProvider.metaKey]?.sources?.length && knowledge?.user_id === $user?.id}
									<Tooltip
										content={knowledge?.meta?.[activeProvider.metaKey]?.last_sync_at
											? $i18n.t('Last synced: {{date}}', {
													date: dayjs(
														knowledge.meta[activeProvider.metaKey].last_sync_at * 1000
													).fromNow()
												})
											: $i18n.t('Sync {{label}} files', { label: activeProvider.label })}
									>
										<button
											class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
											on:click={() => cloudResyncHandler(activeProvider)}
										>
											{#if activeProvider.type === 'onedrive'}
												<OneDrive className="size-4" />
											{:else if activeProvider.type === 'google_drive'}
												<GoogleDrive className="size-4" />
											{:else if activeProvider.type === 'confluence'}
												<Confluence className="size-4" />
											{/if}
										</button>
									</Tooltip>
								{/if}
								{#if isSyncBusy}
									<SyncProgress
										current={activeState?.syncStatus?.progress_current ?? 0}
										total={activeState?.syncStatus?.progress_total ?? 0}
										stageCounts={activeState?.syncStatus?.stage_counts}
									/>
								{:else if fileItemsTotal || kbFileTotal}
									{#if knowledge?.type !== 'local' && knowledge?.type}
										{@const maxFiles =
											$config?.integration_providers?.[knowledge?.type]?.max_files_per_kb ||
											$config?.features?.knowledge_max_file_count ||
											250}
										<Tooltip
											content={$i18n.t('Maximum {{count}} files per knowledge base', {
												count: maxFiles
											})}
										>
											<div class="text-xs text-gray-500">
												{kbFileTotal ?? fileItemsTotal} / {maxFiles}
												{$i18n.t('files')}
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
								<AccessButton
									on:click={() => {
										showAccessControlModal = true;
									}}
								/>
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

					<div class="flex w-full items-center">
						{#if knowledge?.meta?.confluence_sync?.shared}
							<!-- The shared Confluence KB is system-managed — its
							     description is a fixed, localized string. -->
							<div class="text-left text-xs w-full text-gray-500 flex-1">
								{$i18n.t('Read-only Confluence knowledge base managed by administrators.')}
							</div>
						{:else}
							<input
								type="text"
								class="text-left text-xs w-full text-gray-500 bg-transparent outline-hidden flex-1"
								bind:value={knowledge.description}
								aria-label={$i18n.t('Knowledge Description')}
								placeholder={$i18n.t('Knowledge Description')}
								disabled={!knowledge?.write_access}
								on:input={() => {
									changeDebounceHandler();
								}}
							/>
						{/if}

						<div class="hidden md:block">
							<Tooltip content={$i18n.t('Click to copy ID')}>
								<button
									class="text-xs text-gray-500 font-mono shrink-0 px-2 py-1 rounded-lg cursor-pointer hover:underline transition whitespace-nowrap"
									on:click={() => {
										copyToClipboard(id);
										toast.success($i18n.t('ID copied to clipboard'));
									}}
								>
									{id}
								</button>
							</Tooltip>
						</div>
					</div>
				</div>
			</div>
		</div>

		<div
<<<<<<< HEAD
			class="mt-2 mb-2.5 py-2 -mx-0 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100/30 dark:border-gray-850/30 flex-1 flex flex-col overflow-hidden min-h-0"
=======
			class="mt-1.5 mb-2 py-1.5 -mx-0 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100/30 dark:border-gray-850/30 flex-1"
>>>>>>> upstream/main
		>
			{#if isExternalKnowledge}
				<div class="p-5 flex flex-col gap-4">
					<div class="flex flex-wrap gap-2 text-xs">
						<div class="px-2 py-1 rounded-lg bg-gray-50 dark:bg-gray-850">
							{$i18n.t('Connected')}
						</div>
						<div class="px-2 py-1 rounded-lg bg-gray-50 dark:bg-gray-850">
							{$i18n.t('Read Only')}
						</div>
						<div class="px-2 py-1 rounded-lg bg-gray-50 dark:bg-gray-850">
							{knowledge?.meta?.external?.provider ?? $i18n.t('Provider')}
						</div>
						<div class="px-2 py-1 rounded-lg bg-gray-50 dark:bg-gray-850">
							{$i18n.t('Service Account')}
						</div>
					</div>

					<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('Mapped Source')}</div>
							<div class="rounded-xl bg-gray-50 dark:bg-gray-850 px-3 py-2">
								{knowledge?.meta?.external?.source?.name ?? $i18n.t('Not configured')}
							</div>
						</div>
						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('Auth Mode')}</div>
							<div class="rounded-xl bg-gray-50 dark:bg-gray-850 px-3 py-2">
								{$i18n.t('Admin-managed service account')}
							</div>
						</div>
					</div>

					<div class="text-xs text-gray-500">
						<!-- LICENSE covers this Open WebUI wordmark.
						Do not alter, remove, obscure, or replace it except as LICENSE permits:
						https://docs.openwebui.com/license. -->
						{$i18n.t(
							'This knowledge base retrieves from a connected source. Open WebUI can query it, but cannot upload, sync, edit, delete, reset, or reindex its source data.'
						)}
					</div>

					<div class="flex flex-col gap-2">
						<div class="text-xs">{$i18n.t('Test Query')}</div>
						<div class="flex gap-2">
							<input
								class="w-full text-xs rounded-xl bg-gray-50 dark:bg-gray-850 px-3 py-2 outline-hidden"
								bind:value={externalTestQuery}
								placeholder={$i18n.t('Ask this knowledge source a test question')}
							/>
							<button
								class="px-3 py-2 rounded-xl bg-black text-white dark:bg-white dark:text-black text-xs"
								on:click={externalTestHandler}
							>
								{$i18n.t('Test')}
							</button>
						</div>
					</div>

					{#if externalTestResult}
						<div class="rounded-xl bg-gray-50 dark:bg-gray-850 p-3 text-xs">
							<div class="mb-2">{$i18n.t('Preview')}</div>
							{#each externalTestResult.documents ?? [] as document, idx}
								<div class="border-t border-gray-100 dark:border-gray-800 py-2">
									<div class="line-clamp-4">{document}</div>
									<div class="text-gray-500 mt-1">
										{externalTestResult.metadatas?.[idx]?.source ?? ''}
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{:else}
<<<<<<< HEAD
				<div class="px-3.5 flex shrink-0 items-center w-full space-x-2 py-0.5 pb-2">
=======
				<div class="px-3 flex flex-1 items-center w-full space-x-1.5">
>>>>>>> upstream/main
					<div class="flex flex-1 items-center">
						<div class=" self-center ml-1 mr-2">
							<Search className="size-3.5" />
						</div>
						<input
							class=" w-full text-xs pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
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

						<Dropdown align="end">
							<button
								class="p-1.5 mr-1 rounded-xl text-gray-500 bg-transparent hover:text-gray-900 dark:hover:text-gray-100 transition"
								type="button"
							>
								<AdjustmentsHorizontal className="size-3.5" strokeWidth="2" />
							</button>

							<div slot="content">
								<DropdownMenu className="min-w-[11.25rem]">
									<button
										class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-[0.8125rem] hover:text-gray-900 dark:hover:text-gray-100"
										type="button"
										on:click={() => {
											includeContent = !includeContent;
											currentPage = 1;
										}}
									>
										<Checkbox
											state={includeContent ? 'checked' : 'unchecked'}
											on:change={(e) => {
												includeContent = e.detail === 'checked';
												currentPage = 1;
											}}
										/>
										{$i18n.t('File content')}
									</button>
								</DropdownMenu>
							</div>
						</Dropdown>

						{#if knowledge?.write_access}
							<div>
								{#if activeProvider}
									<Tooltip
										content={$i18n.t('Sync from {{label}}', { label: activeProvider.label })}
									>
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
										{structureEditable}
										onUpload={(data) => {
											if (data.type === 'directory') {
												uploadDirectoryHandler();
											} else if (data.type === 'new_directory') {
												showNewDirectoryModal = true;
											} else if (data.type === 'web') {
												showAddWebpageModal = true;
											} else if (data.type === 'text') {
												showAddTextContentModal = true;
											} else {
												document.getElementById('files-input').click();
											}
										}}
										onSync={structureEditable
											? async () => {
													pendingSyncFiles = filterAllowedEntries(await collectDirectoryEntries());
													if (pendingSyncFiles?.length) {
														showSyncConfirmModal = true;
													} else {
														pendingSyncFiles = null;
													}
												}
											: null}
										onReset={structureEditable
											? () => {
													showResetConfirm = true;
												}
											: null}
									/>
								{/if}
							</div>
						{/if}
					</div>
				</div>

				<div class="px-2.5 flex justify-between">
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
							class="flex gap-2 w-fit text-center text-sm rounded-full bg-transparent px-0.5 whitespace-nowrap"
						>
							<DropdownOptions
								align="end"
								className="flex h-8 shrink-0 items-center gap-1.5 rounded-xl bg-transparent px-1.5 text-xs text-gray-700 transition placeholder-gray-400 outline-hidden hover:text-gray-900 focus:outline-hidden dark:text-gray-200 dark:hover:text-gray-100"
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
									currentPage = 1;
								}}
							/>

							<DropdownOptions
								align="end"
								bind:value={sortKey}
								placeholder={$i18n.t('Sort')}
								items={[
									{ value: 'name', label: $i18n.t('Name') },
									{ value: 'created_at', label: $i18n.t('Created') },
									{ value: 'updated_at', label: $i18n.t('Updated') }
								]}
							/>

							{#if sortKey}
								<DropdownOptions
									align="end"
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

<<<<<<< HEAD
				<!-- Always rendered (not just inside a folder) so entering/leaving the
				     root doesn't insert/remove the row and shift the list (layout jump). -->
				{#if !query}
					<div class="px-4 mt-2 flex shrink-0">
=======
				{#if currentDirectoryId !== null}
					<div class="px-4 mb-1">
>>>>>>> upstream/main
						<KnowledgeBreadcrumbs
							rootLabel={knowledge.name}
							{breadcrumbs}
							onNavigate={(dirId) => navigateToDirectory(dirId)}
							onMoveFiles={(fileIds, dirId) => moveFilesToDirectoryHandler(fileIds, dirId)}
							onMoveDir={(dirId, targetId) => moveDirectoryHandler(dirId, targetId)}
						/>
					</div>
				{/if}

				{#if syncing}
<<<<<<< HEAD
					<div class="mx-2.5 mt-2.5 -mb-0.5 shrink-0">
						<div class="flex items-center gap-2.5 rounded-xl py-2 px-3 bg-gray-50 dark:bg-gray-850">
=======
					<div class="mx-2 mt-2 -mb-0.5">
						<div
							class="flex items-center gap-2 rounded-xl py-1.5 px-2.5 bg-gray-50 dark:bg-gray-850"
						>
>>>>>>> upstream/main
							<Spinner className="size-3.5 shrink-0" />
							<div class="text-xs text-gray-500 dark:text-gray-400 truncate">
								{syncing}
							</div>
						</div>
					</div>
				{/if}

				{#if fileItems !== null && fileItemsTotal !== null}
<<<<<<< HEAD
					<div class="flex flex-row flex-1 min-h-0 gap-3 px-2.5 mt-2">
=======
					<div class="flex flex-row flex-1 gap-2 px-2">
>>>>>>> upstream/main
						<div class="flex-1 flex">
							<div class=" flex flex-col w-full space-x-2 rounded-lg h-full">
								<div class="w-full h-full flex flex-col min-h-0">
									<!-- Mirrors the row-list condition below so a level holding only
								     directories still renders the header. KbSelectionHeader is
								     fixed-height by design ("the list never jumps") — gating it on
								     fileItems alone defeated that, since entering a folder with
								     files made the header appear and shift the rows down. -->
								{#if knowledge?.write_access && fileItems && (fileItems.length > 0 || (!query && directoryItems.length > 0))}
										<div class="pb-1.5 shrink-0">
											<KbSelectionHeader
												count={$bulkCount}
												allSelected={$bulkAllSelected}
												indeterminate={$bulkIndeterminate}
												onToggleSelectAll={() => selection.toggleSelectAll()}
												onDelete={() => (showBulkRemoveConfirm = true)}
											/>
										</div>
									{/if}
									{#if fileItems.length > 0 || (!query && directoryItems.length > 0)}
										<div class=" flex overflow-y-auto h-full w-full scrollbar-hidden text-xs">
											<Files
												files={fileItems}
												directories={query ? [] : directoryItems}
												searchMode={!!query}
												{structureEditable}
												sources={activeProvider
													? (knowledge?.meta?.[activeProvider.metaKey]?.sources ?? [])
													: []}
												isSyncing={activeState?.isSyncing ?? false}
												onRemoveSource={activeProvider && knowledge?.write_access
													? (itemId, sourceName) => {
															selectedFileId = null;
															selectedFile = null;
															removeCloudSourceHandler(activeProvider, itemId, sourceName);
														}
													: null}
												{knowledge}
												{selectedFileId}
												onClick={(fileId) => {
													selectedFileId = fileId;

													if (fileItems) {
														const file = fileItems.find((file) => file.id === selectedFileId);
														if (file) {
															openFilePreview(file);
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
												onNavigateDirectory={(dirId) => navigateToDirectory(dirId)}
												onRenameDirectory={(dirId, name) => renameDirectoryHandler(dirId, name)}
												onDeleteDirectory={(dirId) => confirmDeleteDirectory(dirId)}
												onMoveFilesToDirectory={(fileIds, dirId) =>
													moveFilesToDirectoryHandler(fileIds, dirId)}
												onMoveDirectoryToDirectory={(dirId, targetId) =>
													moveDirectoryHandler(dirId, targetId)}
												selection={knowledge?.write_access ? selection : null}
											/>
										</div>

										{#if fileItemsTotal > 30}
											<Pagination bind:page={currentPage} count={fileItemsTotal} perPage={30} />
										{/if}
									{:else if isSyncBusy}
										<div
											class="my-auto flex flex-col items-center justify-center text-center gap-3 py-8"
										>
											<Spinner className="size-5" />
											<div class="text-xs text-gray-500">
												{$i18n.t('Starting sync...')}
											</div>
										</div>
									{:else if knowledge?.write_access && !query && !viewOption && currentDirectoryId === null}
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
												} else if (type === 'confluence') {
													cloudSyncHandler(CLOUD_PROVIDERS.confluence);
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
										<div
											class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs"
										>
											<div>
												{$i18n.t('No content found')}
											</div>
										</div>
									{/if}
								</div>
							</div>
						</div>

<<<<<<< HEAD
						<FileItemModal bind:show={showFilePreview} item={selectedFile} edit={false} />
=======
						{#if selectedFileId !== null}
							<Drawer
								className="h-full"
								show={selectedFileId !== null}
								onClose={() => {
									selectedFileId = null;
									selectedFile = null;
									selectedFileContent = '';
									loadingFileContent = false;
								}}
							>
								<div class="flex flex-col justify-start h-full max-h-full">
									<div class=" flex flex-col w-full h-full max-h-full">
										<div class="shrink-0 flex items-center p-2">
											<div class="mr-2">
												<button
													class="w-full text-left text-xs p-1.5 rounded-lg dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-gray-850"
													aria-label={$i18n.t('Close')}
													on:click={() => {
														selectedFileId = null;
														selectedFile = null;
														selectedFileContent = '';
														loadingFileContent = false;
													}}
												>
													<ChevronLeft strokeWidth="2.5" />
												</button>
											</div>
											<div class="flex-1 text-sm line-clamp-1">
												<a
													href="#"
													class="hover:underline line-clamp-1"
													on:click|preventDefault={() => {
														if (selectedFile?.id) {
															openFileHandler(selectedFile.id);
														}
													}}
												>
													{selectedFile?.meta?.name}
												</a>
											</div>

											{#if knowledge?.write_access}
												<div>
													<button
														class="flex self-center w-fit text-xs py-1 px-2.5 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
														disabled={isSaving || loadingFileContent}
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

										{#key selectedFile?.id}
											<textarea
												class="w-full h-full text-xs outline-none resize-none px-3 py-2"
												bind:value={selectedFileContent}
												disabled={!knowledge?.write_access || loadingFileContent}
												aria-label={$i18n.t('File content')}
												placeholder={$i18n.t('Add content here')}
											></textarea>
										{/key}
									</div>
								</div>
							</Drawer>
						{/if}
					</div>
				{:else}
					<div class="my-10">
						<Spinner className="size-4" />
>>>>>>> upstream/main
					</div>
				{/if}
			{/if}
		</div>
	{:else}
		<Spinner className="size-5" />
	{/if}
</div>

<ConfirmDialog
	bind:show={showDeleteDirectoryConfirm}
	title={$i18n.t('Delete directory?')}
	on:confirm={() => {
		deleteDirectoryHandler(!deleteDirectoryContents);
	}}
	on:cancel={() => {
		pendingDeleteDirectoryId = null;
	}}
>
	<div class="text-sm text-gray-700 dark:text-gray-300 flex-1 line-clamp-3 mb-2">
		{$i18n.t(`Are you sure you want to delete this directory?`)}
	</div>

	<div class="flex items-center gap-1.5">
		<input type="checkbox" bind:checked={deleteDirectoryContents} />

		<div class="text-xs text-gray-500">
			{$i18n.t('Delete all contents inside this directory')}
		</div>
	</div>
</ConfirmDialog>

<ConfirmDialog
	bind:show={showResetConfirm}
	title={$i18n.t('Reset knowledge base?')}
	on:confirm={async () => {
		const res = await resetKnowledgeById(localStorage.token, id).catch((e) => {
			toast.error(`${e}`);
			return null;
		});
		if (res) {
			toast.success($i18n.t('Knowledge base has been reset'));
			currentDirectoryId = null;
			init();
		}
	}}
>
	<div class="text-sm text-gray-700 dark:text-gray-300 flex-1 line-clamp-3">
		{$i18n.t(
			'This will remove all files and directories from this knowledge base. This action cannot be undone.'
		)}
	</div>
</ConfirmDialog>
