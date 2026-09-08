<script lang="ts">
	import { getContext, tick } from 'svelte';
	import { fly } from 'svelte/transition';
	import { toast } from 'svelte-sonner';

	import {
		config,
		user,
		tools as _tools,
		mobile,
		knowledge,
		chats,
		settings,
		toolServers
	} from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';

	import { updateUserSettings } from '$lib/apis/users';
	import { getTools } from '$lib/apis/tools';
	import { getOAuthClientAuthorizationUrl } from '$lib/apis/configs';
	import { getKnowledgeById } from '$lib/apis/knowledge';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	import Camera from '$lib/components/icons/Camera.svelte';
	import Clip from '$lib/components/icons/Clip.svelte';
<<<<<<< HEAD
=======
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';
	import Refresh from '$lib/components/icons/Refresh.svelte';
>>>>>>> upstream/main
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import FolderOpen from '$lib/components/icons/FolderOpen.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import PageEdit from '$lib/components/icons/PageEdit.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Pin from '$lib/components/icons/Pin.svelte';
	import PinSlash from '$lib/components/icons/PinSlash.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import Terminal from '$lib/components/icons/Terminal.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Knobs from '$lib/components/icons/Knobs.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';

	import Chats from './InputMenu/Chats.svelte';
	import Notes from './InputMenu/Notes.svelte';
	import Knowledge from './InputMenu/Knowledge.svelte';
	import AttachWebpageModal from './AttachWebpageModal.svelte';

	const i18n = getContext('i18n');

	export let files = [];

	export let selectedModels: string[] = [];
	export let fileUploadCapableModels: string[] = [];

	export let screenCaptureHandler: Function;
	export let uploadFilesHandler: Function;
	export let inputFilesHandler: Function;

	export let uploadGoogleDriveHandler: Function;
	export let uploadOneDriveHandler: Function;
	export let uploadConfluenceHandler: Function = () => {};

	export let onUpload: Function;
	export let onClose: Function;
	export let toolApprovalMode = 'full';
	export let onToolApprovalModeChange: Function = () => {};

	// Capability toggle states (two-way binding from parent)
	export let selectedToolIds: string[] = [];
	export let selectedFilterIds: string[] = [];
	export let webSearchEnabled = false;
	export let imageGenerationEnabled = false;
	export let codeInterpreterEnabled = false;
	export let documentWriterEnabled = false;

	// Visibility flags
	export let showToolsButton = false;
	export let showWebSearchButton = false;
	export let showImageGenerationButton = false;
	export let showCodeInterpreterButton = false;
	export let showDocumentWriterButton = false;
	export let toggleFilters: {
		id: string;
		name: string;
		description?: string;
		icon?: string;
		has_user_valves?: boolean;
	}[] = [];

	// Valve handling
	export let onShowValves: Function = (e) => {};
	export let closeOnOutsideClick = true;
	// When non-null, restricts the menu to only the listed item keys.
	// Item keys match the strings passed to ``pinItemHandler`` —
	// 'upload_files', 'capture', 'attach_webpage', 'attach_notes',
	// 'google_drive', 'onedrive', 'confluence', 'knowledge',
	// 'reference_chats', 'tools', 'filters'. Default null = upstream
	// behavior, every globally-enabled item shows.
	export let restrictTo: string[] | null = null;
	$: itemAllowed = (key: string) => restrictTo === null || restrictTo.includes(key);

	// Strict data separation (data-sovereignty): gray out + tooltip the items on the side
	// the conversation cannot use. All false/no-op unless the feature flag is enabled.
	export let openInternetBlocked = false;
	export let internalBlocked = false;
	export let dataSeparationMessage = '';
	const OPEN_INTERNET_ITEMS = new Set(['attach_webpage']);
	const INTERNAL_ITEMS = new Set([
		'upload_files',
		'capture',
		'attach_notes',
		'google_drive',
		'onedrive',
		'confluence',
		'knowledge',
		'reference_chats'
	]);
	$: isStrictBlocked = (key: string) =>
		(OPEN_INTERNET_ITEMS.has(key) && openInternetBlocked) ||
		(INTERNAL_ITEMS.has(key) && internalBlocked);
	// Section visibility — hide the header when no items in the section
	// are allowed (otherwise the menu shows a lonely caption).
	$: anyContextAllowed = [
		'upload_files',
		'capture',
		'attach_webpage',
		'attach_notes',
		'google_drive',
		'onedrive',
		'confluence'
	].some((k) => itemAllowed(k));
	$: anyDatabaseAllowed = ['knowledge', 'reference_chats'].some((k) => itemAllowed(k));
	$: anyCapabilityAllowed = ['tools', 'filters'].some((k) => itemAllowed(k));

	let show = false;
	let tab = '';
	let directTab = false;

	let showAttachWebpageModal = false;
	const toolApprovalModes = [
		{
			value: 'full',
			label: 'Full access',
			description: 'Run tools without asking for approval.'
		},
		{
			value: 'ask',
			label: 'Ask for approval',
			description: 'Stop before each tool call until you allow or deny it.'
		}
	];

	let fileUploadEnabled = true;
	$: fileUploadEnabled =
		fileUploadCapableModels.length === selectedModels.length &&
		($user?.role === 'admin' || $user?.permissions?.chat?.file_upload);

<<<<<<< HEAD
=======
	let webUploadEnabled = true;
	$: webUploadEnabled = $user?.role === 'admin' || ($user?.permissions?.chat?.web_upload ?? true);
	$: toolPermissionsEnabled = $config?.features?.enable_tool_permissions ?? false;

>>>>>>> upstream/main
	$: if (!fileUploadEnabled && files.length > 0) {
		files = [];
	}

	// Tools state
	let tools = null;

	$: if (show) {
		initTools();
	}

	const initTools = async () => {
		if ($_tools === null) {
			await _tools.set(await getTools(localStorage.token));
		}

		if ($_tools) {
			tools = $_tools.reduce((a, tool, i, arr) => {
				a[tool.id] = {
					name: tool.name,
					description: tool.meta.description,
					enabled: selectedToolIds.includes(tool.id),
					...tool
				};
				return a;
			}, {});
		}

		if ($toolServers) {
			for (const serverIdx in $toolServers) {
				const server = $toolServers[serverIdx];
				if (server.info) {
					tools[`direct_server:${serverIdx}`] = {
						name: server?.info?.title ?? server.url,
						description: server.info.description ?? '',
						enabled: selectedToolIds.includes(`direct_server:${serverIdx}`)
					};
				}
			}
		}

		selectedToolIds = selectedToolIds.filter((id) => Object.keys(tools).includes(id));
	};

	// Pin handler
	const pinItemHandler = async (itemId) => {
		let pinnedItems = $settings?.pinnedInputItems ?? [];
		if (pinnedItems.includes(itemId)) {
			pinnedItems = pinnedItems.filter((id) => id !== itemId);
		} else {
			pinnedItems = [...new Set([...pinnedItems, itemId])];
		}
		settings.set({ ...$settings, pinnedInputItems: pinnedItems });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

	$: pinnedInputItems = $settings?.pinnedInputItems ?? [];

	const detectMobile = () => {
		const userAgent = navigator.userAgent || navigator.vendor || window.opera;
		return /android|iphone|ipad|ipod|windows phone/i.test(userAgent);
	};

	const handleFileChange = (event) => {
		const inputFiles = Array.from(event.target?.files);
		if (inputFiles && inputFiles.length > 0) {
			console.log(inputFiles);
			inputFilesHandler(inputFiles);
		}
	};

	const onSelect = (item) => {
		if (files.find((f) => f.id === item.id)) {
			return;
		}
		files = [
			...files,
			{
				...item,
				status: 'processed'
			}
		];

		// Dropdown's onOpenChange only fires from its own close paths, so we
		// must reset tab state here when closing via bind:show.
		tab = '';
		directTab = false;
		show = false;
	};

	// One-click attach of the shared, read-only Confluence KB (shared mode).
	// The KB carries a user:*:read grant, so getKnowledgeById works for any
	// user; the resulting collection is identical to picking it from the
	// Knowledge submenu. Exported so the pinned bottom-bar Confluence button
	// in MessageInput can reuse the same attach flow without duplicating it.
	export const attachSharedConfluenceKb = async () => {
		const kbId = $config?.features?.confluence_shared_kb_id;
		if (!kbId) {
			return;
		}
		show = false;

		const kb = await getKnowledgeById(localStorage.token, kbId).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (!kb) {
			return;
		}

		onSelect({
			...kb,
			knowledge_type: kb.type,
			type: 'collection'
		});
	};

	// Expose openTab for external use (pinned items in bottom bar)
	export const openTab = (tabName) => {
		tab = tabName;
		directTab = true;
		show = true;
	};

	export const openWebpageModal = () => {
		showAttachWebpageModal = true;
	};
</script>

<AttachWebpageModal
	bind:show={showAttachWebpageModal}
	onSubmit={(e) => {
		onUpload(e);
	}}
/>

<!-- Hidden file input used to open the camera on mobile -->
<input
	id="camera-input"
	type="file"
	accept="image/*"
	capture="environment"
	on:change={handleFileChange}
	class="hidden"
/>

<Dropdown
	bind:show
<<<<<<< HEAD
	{closeOnOutsideClick}
	onOpenChange={(state) => {
		if (!state) {
			tab = '';
			directTab = false;
=======
	visualViewportAware
	on:change={(e) => {
		if (e.detail === false) {
>>>>>>> upstream/main
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content">
<<<<<<< HEAD
		<div
			class="w-84 rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg max-h-96 overflow-y-auto overflow-x-hidden scrollbar-thin transition"
		>
			{#if tab === ''}
				<div in:fly={{ x: -20, duration: 150 }}>
					<!-- ═══ ATTACH CONTEXT ═══ -->
					{#if anyContextAllowed}
						<div
							class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide px-3 py-1.5"
=======
		<DropdownMenu className="w-70 max-h-72 overflow-hidden transition">
			{#if tab === ''}
				<div
					class="max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin"
					in:fly={{ x: -20, duration: 150 }}
				>
					{#if toolPermissionsEnabled}
						<button
							class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl"
							on:click={() => {
								tab = 'tool_permissions';
							}}
						>
							<svg
								class="size-3.5 shrink-0"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="1.5"
								stroke-linecap="round"
								stroke-linejoin="round"
							>
								<path d="M12 22C12 22 20 18 20 12V5L12 2L4 5V12C4 18 12 22 12 22Z" />
							</svg>

							<div class="flex items-center w-full justify-between min-w-0">
								<div class="line-clamp-1">
									{$i18n.t('Tool Permissions')}
								</div>

								<div class="flex items-center gap-2 min-w-0">
									<div class="text-xs text-gray-500 truncate">
										{$i18n.t(
											toolApprovalModes.find((mode) => mode.value === toolApprovalMode)?.label ??
												'Full access'
										)}
									</div>
									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</div>
						</button>

						<div class="h-px mx-1 my-1 bg-gray-100 dark:bg-gray-800"></div>
					{/if}

					<Tooltip
						content={fileUploadCapableModels.length !== selectedModels.length
							? $i18n.t('Model(s) do not support file upload')
							: !fileUploadEnabled
								? $i18n.t('You do not have permission to upload files.')
								: ''}
						className="w-full"
					>
						<button
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!fileUploadEnabled
								? 'opacity-50'
								: ''}"
							type="button"
							on:click={() => {
								if (fileUploadEnabled) {
									uploadFilesHandler();
									show = false;
								}
							}}
>>>>>>> upstream/main
						>
							{$i18n.t('Attach context')}
						</div>
					{/if}

<<<<<<< HEAD
					<!-- Upload Files -->
					{#if itemAllowed('upload_files')}
=======
							<div class="line-clamp-1">{$i18n.t('Upload Files')}</div>
						</button>
					</Tooltip>

					<Tooltip
						content={fileUploadCapableModels.length !== selectedModels.length
							? $i18n.t('Model(s) do not support file upload')
							: !fileUploadEnabled
								? $i18n.t('You do not have permission to upload files.')
								: ''}
						className="w-full"
					>
						<button
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!fileUploadEnabled
								? 'opacity-50'
								: ''}"
							type="button"
							on:click={() => {
								if (fileUploadEnabled) {
									if (!detectMobile()) {
										screenCaptureHandler();
									} else {
										const cameraInputElement = document.getElementById('camera-input');

										if (cameraInputElement) {
											cameraInputElement.click();
										}
									}
									show = false;
								}
							}}
						>
							<Camera />
							<div class=" line-clamp-1">{$i18n.t('Capture')}</div>
						</button>
					</Tooltip>

					<Tooltip
						content={!webUploadEnabled
							? $i18n.t('You do not have permission to upload web content.')
							: ''}
						className="w-full"
					>
						<button
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!webUploadEnabled
								? 'opacity-50'
								: ''}"
							type="button"
							on:click={() => {
								if (webUploadEnabled) {
									showAttachWebpageModal = true;
									show = false;
								}
							}}
						>
							<GlobeAlt />
							<div class="line-clamp-1">{$i18n.t('Attach Webpage')}</div>
						</button>
					</Tooltip>

					<Tooltip
						content={fileUploadCapableModels.length !== selectedModels.length
							? $i18n.t('Model(s) do not support file upload')
							: !fileUploadEnabled
								? $i18n.t('You do not have permission to upload files.')
								: ''}
						className="w-full"
					>
						<button
							class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!fileUploadEnabled
								? 'opacity-50'
								: ''}"
							on:click={() => {
								if (fileUploadEnabled) {
									tab = 'files';
								}
							}}
						>
							<DocumentArrowUp />

							<div class="flex items-center w-full justify-between">
								<div class="line-clamp-1">
									{$i18n.t('Attach Files')}
								</div>

								<div class="text-gray-500">
									<ChevronRight />
								</div>
							</div>
						</button>
					</Tooltip>

					{#if $config?.features?.enable_notes ?? false}
>>>>>>> upstream/main
						<Tooltip
							content={isStrictBlocked('upload_files')
								? dataSeparationMessage
								: fileUploadCapableModels.length !== selectedModels.length
									? $i18n.t('Model(s) do not support file upload')
									: !fileUploadEnabled
										? $i18n.t('You do not have permission to upload files.')
										: ''}
							className="w-full"
						>
							<button
<<<<<<< HEAD
								class="flex gap-2 w-full text-left items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl {!fileUploadEnabled ||
								isStrictBlocked('upload_files')
=======
								class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!fileUploadEnabled
>>>>>>> upstream/main
									? 'opacity-50'
									: ''}"
								type="button"
								on:click={() => {
									if (fileUploadEnabled && !isStrictBlocked('upload_files')) {
										show = false;
										uploadFilesHandler();
									}
								}}
							>
								<Clip />
								<div class="flex-1 line-clamp-1">{$i18n.t('Files')}</div>
								<Tooltip
									content={pinnedInputItems.includes('upload_files')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('upload_files')}
									>
										{#if pinnedInputItems.includes('upload_files')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						</Tooltip>
					{/if}

<<<<<<< HEAD
					<!-- Capture -->
					{#if isFeatureEnabled('capture') && itemAllowed('capture')}
						<Tooltip
							content={isStrictBlocked('capture')
								? dataSeparationMessage
								: fileUploadCapableModels.length !== selectedModels.length
									? $i18n.t('Model(s) do not support file upload')
									: !fileUploadEnabled
										? $i18n.t('You do not have permission to upload files.')
										: ''}
							className="w-full"
						>
							<button
								class="flex gap-2 w-full text-left items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl {!fileUploadEnabled ||
								isStrictBlocked('capture')
									? 'opacity-50'
									: ''}"
								type="button"
								on:click={() => {
									if (fileUploadEnabled && !isStrictBlocked('capture')) {
										show = false;
										if (!detectMobile()) {
											screenCaptureHandler();
										} else {
											const cameraInputElement = document.getElementById('camera-input');
											if (cameraInputElement) {
												cameraInputElement.click();
											}
										}
									}
								}}
							>
								<Camera />
								<div class="flex-1 line-clamp-1">{$i18n.t('Capture')}</div>
								<Tooltip
									content={pinnedInputItems.includes('capture') ? $i18n.t('Unpin') : $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('capture')}
									>
										{#if pinnedInputItems.includes('capture')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						</Tooltip>
					{/if}

					<!-- Attach Webpage (Link icon) -->
					{#if isFeatureEnabled('webpage_url') && itemAllowed('attach_webpage')}
						<Tooltip
							content={isStrictBlocked('attach_webpage')
								? dataSeparationMessage
								: fileUploadCapableModels.length !== selectedModels.length
									? $i18n.t('Model(s) do not support file upload')
									: !fileUploadEnabled
										? $i18n.t('You do not have permission to upload files.')
										: ''}
							className="w-full"
						>
							<button
								class="flex gap-2 w-full text-left items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl {!fileUploadEnabled ||
								isStrictBlocked('attach_webpage')
									? 'opacity-50'
									: ''}"
								type="button"
								on:click={() => {
									if (fileUploadEnabled && !isStrictBlocked('attach_webpage')) {
										show = false;
										showAttachWebpageModal = true;
									}
								}}
							>
								<Link />
								<div class="flex-1 line-clamp-1">{$i18n.t('Webpage URL')}</div>
								<Tooltip
									content={pinnedInputItems.includes('attach_webpage')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('attach_webpage')}
									>
										{#if pinnedInputItems.includes('attach_webpage')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						</Tooltip>
					{/if}

					<!-- Attach Notes -->
					{#if ($config?.features?.enable_notes ?? false) && itemAllowed('attach_notes')}
						<Tooltip
							content={isStrictBlocked('attach_notes')
								? dataSeparationMessage
								: fileUploadCapableModels.length !== selectedModels.length
									? $i18n.t('Model(s) do not support file upload')
									: !fileUploadEnabled
										? $i18n.t('You do not have permission to upload files.')
										: ''}
							className="w-full"
						>
							<button
								class="flex gap-2 w-full items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl {!fileUploadEnabled ||
								isStrictBlocked('attach_notes')
									? 'opacity-50'
									: ''}"
								on:click={() => {
									if (isStrictBlocked('attach_notes')) return;
									tab = 'notes';
								}}
							>
								<PageEdit />
								<div class="flex-1 flex items-center justify-between">
									<div class="line-clamp-1">{$i18n.t('Attach Notes')}</div>
									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
								<Tooltip
									content={pinnedInputItems.includes('attach_notes')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('attach_notes')}
									>
										{#if pinnedInputItems.includes('attach_notes')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						</Tooltip>
					{/if}

					<!-- Google Drive -->
					{#if fileUploadEnabled && !internalBlocked}
						{#if $config?.features?.enable_google_drive_integration && itemAllowed('google_drive')}
							<button
								class="flex gap-2 w-full text-left items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl"
=======
					<Tooltip
						content={fileUploadCapableModels.length !== selectedModels.length
							? $i18n.t('Model(s) do not support file upload')
							: !fileUploadEnabled
								? $i18n.t('You do not have permission to upload files.')
								: ''}
						className="w-full"
					>
						<button
							class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!fileUploadEnabled
								? 'opacity-50'
								: ''}"
							on:click={() => {
								tab = 'knowledge';
							}}
						>
							<Database />

							<div class="flex items-center w-full justify-between">
								<div class=" line-clamp-1">
									{$i18n.t('Attach Knowledge')}
								</div>

								<div class="text-gray-500">
									<ChevronRight />
								</div>
							</div>
						</button>
					</Tooltip>

					<Tooltip
						content={fileUploadCapableModels.length !== selectedModels.length
							? $i18n.t('Model(s) do not support file upload')
							: !fileUploadEnabled
								? $i18n.t('You do not have permission to upload files.')
								: ''}
						className="w-full"
					>
						<button
							class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!fileUploadEnabled
								? 'opacity-50'
								: ''}"
							on:click={() => {
								tab = 'chats';
							}}
						>
							<ClockRotateRight />

							<div class="flex items-center w-full justify-between">
								<div class=" line-clamp-1">
									{$i18n.t('Reference Chats')}
								</div>

								<div class="text-gray-500">
									<ChevronRight />
								</div>
							</div>
						</button>
					</Tooltip>

					{#if fileUploadEnabled}
						{#if $config?.features?.enable_google_drive_integration}
							<button
								class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl"
>>>>>>> upstream/main
								type="button"
								on:click={() => {
									show = false;
									uploadGoogleDriveHandler();
								}}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 87.3 78"
									class="size-3.5 shrink-0"
								>
									<path
										d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z"
										fill="#0066da"
									/>
									<path
										d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z"
										fill="#00ac47"
									/>
									<path
										d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z"
										fill="#ea4335"
									/>
									<path
										d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z"
										fill="#00832d"
									/>
									<path
										d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z"
										fill="#2684fc"
									/>
									<path
										d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z"
										fill="#ffba00"
									/>
								</svg>
								<div class="flex-1 line-clamp-1">{$i18n.t('Google Drive')}</div>
								<Tooltip
									content={pinnedInputItems.includes('google_drive')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('google_drive')}
									>
										{#if pinnedInputItems.includes('google_drive')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						{/if}

						<!-- Microsoft OneDrive (simplified — work only) -->
						{#if $config?.features?.enable_onedrive_integration && $config?.features?.enable_onedrive_business && itemAllowed('onedrive')}
							<button
<<<<<<< HEAD
								class="flex gap-2 w-full text-left items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl"
								type="button"
								on:click={() => {
									show = false;
									uploadOneDriveHandler('organizations');
								}}
							>
								<OneDrive className="size-4" />
								<div class="flex-1 line-clamp-1">{$i18n.t('OneDrive Files')}</div>
								<Tooltip
									content={pinnedInputItems.includes('onedrive')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('onedrive')}
									>
										{#if pinnedInputItems.includes('onedrive')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						{/if}

						<!-- Confluence — per-user page picker. Hidden in company-wide
						     mode, where the shared KB is the only Confluence surface. -->
						{#if $config?.features?.enable_confluence_integration && $config?.features?.enable_confluence_sync && $config?.features?.confluence_kb_mode !== 'shared' && $config?.features?.confluence_oauth_configured && itemAllowed('confluence')}
							<button
								class="flex gap-2 w-full text-left items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl"
								type="button"
								on:click={() => {
									show = false;
									uploadConfluenceHandler();
								}}
							>
								<Confluence className="size-4" />
								<div class="flex-1 line-clamp-1">{$i18n.t('Confluence')}</div>
								<Tooltip
									content={pinnedInputItems.includes('confluence')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('confluence')}
									>
										{#if pinnedInputItems.includes('confluence')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						{/if}

						<!-- Confluence (shared mode) — one-click attach of the shared,
						     read-only KB. Mutually exclusive with the per-user picker
						     above via the confluence_kb_mode check. -->
						{#if $config?.features?.enable_confluence_integration && $config?.features?.enable_confluence_sync && $config?.features?.confluence_kb_mode === 'shared' && $config?.features?.confluence_shared_kb_id && itemAllowed('confluence')}
							<button
								class="flex gap-2 w-full text-left items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl"
								type="button"
								on:click={attachSharedConfluenceKb}
							>
								<Confluence className="size-4" />
								<div class="flex-1 line-clamp-1">{$i18n.t('Confluence')}</div>
								<Tooltip
									content={pinnedInputItems.includes('confluence')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('confluence')}
									>
										{#if pinnedInputItems.includes('confluence')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						{/if}
					{/if}

					<!-- ═══ ATTACH DATABASE ═══ -->
					{#if isFeatureEnabled('knowledge') && anyDatabaseAllowed && itemAllowed('knowledge')}
						<div class="my-1 border-t border-gray-100 dark:border-gray-800" />
						<div
							class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide px-3 py-1.5"
						>
							{$i18n.t('Attach databases')}
						</div>

						<Tooltip
							content={isStrictBlocked('knowledge')
								? dataSeparationMessage
								: fileUploadCapableModels.length !== selectedModels.length
									? $i18n.t('Model(s) do not support file upload')
									: !fileUploadEnabled
										? $i18n.t('You do not have permission to upload files.')
										: ''}
							className="w-full"
						>
							<button
								class="flex gap-2 w-full items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl {!fileUploadEnabled ||
								isStrictBlocked('knowledge')
=======
								class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl {!fileUploadEnabled
>>>>>>> upstream/main
									? 'opacity-50'
									: ''}"
								on:click={() => {
									if (isStrictBlocked('knowledge')) return;
									tab = 'knowledge';
								}}
							>
<<<<<<< HEAD
								<FolderOpen />
								<div class="flex-1 flex items-center justify-between">
									<div class="line-clamp-1">{$i18n.t('Knowledge database')}</div>
=======
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 32 32"
									class="size-3.5"
									fill="none"
								>
									<mask
										id="mask0_87_7796"
										style="mask-type:alpha"
										maskUnits="userSpaceOnUse"
										x="0"
										y="6"
										width="32"
										height="20"
									>
										<path
											d="M7.82979 26C3.50549 26 0 22.5675 0 18.3333C0 14.1921 3.35322 10.8179 7.54613 10.6716C9.27535 7.87166 12.4144 6 16 6C20.6308 6 24.5169 9.12183 25.5829 13.3335C29.1316 13.3603 32 16.1855 32 19.6667C32 23.0527 29 26 25.8723 25.9914L7.82979 26Z"
											fill="#C4C4C4"
										/>
									</mask>
									<g mask="url(#mask0_87_7796)">
										<path
											d="M7.83017 26.0001C5.37824 26.0001 3.18957 24.8966 1.75391 23.1691L18.0429 16.3335L30.7089 23.4647C29.5926 24.9211 27.9066 26.0001 26.0004 25.9915C23.1254 26.0001 12.0629 26.0001 7.83017 26.0001Z"
											fill="url(#paint0_linear_87_7796)"
										/>
										<path
											d="M25.5785 13.3149L18.043 16.3334L30.709 23.4647C31.5199 22.4065 32.0004 21.0916 32.0004 19.6669C32.0004 16.1857 29.1321 13.3605 25.5833 13.3337C25.5817 13.3274 25.5801 13.3212 25.5785 13.3149Z"
											fill="url(#paint1_linear_87_7796)"
										/>
										<path
											d="M7.06445 10.7028L18.0423 16.3333L25.5779 13.3148C24.5051 9.11261 20.6237 6 15.9997 6C12.4141 6 9.27508 7.87166 7.54586 10.6716C7.3841 10.6773 7.22358 10.6877 7.06445 10.7028Z"
											fill="url(#paint2_linear_87_7796)"
										/>
										<path
											d="M1.7535 23.1687L18.0425 16.3331L7.06471 10.7026C3.09947 11.0792 0 14.3517 0 18.3331C0 20.1665 0.657197 21.8495 1.7535 23.1687Z"
											fill="url(#paint3_linear_87_7796)"
										/>
									</g>
									<defs>
										<linearGradient
											id="paint0_linear_87_7796"
											x1="4.42591"
											y1="24.6668"
											x2="27.2309"
											y2="23.2764"
											gradientUnits="userSpaceOnUse"
										>
											<stop stop-color="#2086B8" />
											<stop offset="1" stop-color="#46D3F6" />
										</linearGradient>
										<linearGradient
											id="paint1_linear_87_7796"
											x1="23.8302"
											y1="19.6668"
											x2="30.2108"
											y2="15.2082"
											gradientUnits="userSpaceOnUse"
										>
											<stop stop-color="#1694DB" />
											<stop offset="1" stop-color="#62C3FE" />
										</linearGradient>
										<linearGradient
											id="paint2_linear_87_7796"
											x1="8.51037"
											y1="7.33333"
											x2="23.3335"
											y2="15.9348"
											gradientUnits="userSpaceOnUse"
										>
											<stop stop-color="#0D3D78" />
											<stop offset="1" stop-color="#063B83" />
										</linearGradient>
										<linearGradient
											id="paint3_linear_87_7796"
											x1="-0.340429"
											y1="19.9998"
											x2="14.5634"
											y2="14.4649"
											gradientUnits="userSpaceOnUse"
										>
											<stop stop-color="#16589B" />
											<stop offset="1" stop-color="#1464B7" />
										</linearGradient>
									</defs>
								</svg>

								<div class="flex items-center w-full justify-between">
									<div class=" line-clamp-1">
										{$i18n.t('Microsoft OneDrive')}
									</div>

>>>>>>> upstream/main
									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
								<Tooltip
									content={pinnedInputItems.includes('knowledge')
										? $i18n.t('Unpin')
										: $i18n.t('Pin')}
								>
									<button
										class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
										on:click|stopPropagation={() => pinItemHandler('knowledge')}
									>
										{#if pinnedInputItems.includes('knowledge')}
											<PinSlash className="size-3.5" />
										{:else}
											<Pin className="size-3.5" />
										{/if}
									</button>
								</Tooltip>
							</button>
						</Tooltip>

						<!-- Reference Chats -->
						{#if isFeatureEnabled('reference_chats') && ($chats ?? []).length > 0 && itemAllowed('reference_chats')}
							<Tooltip
								content={isStrictBlocked('reference_chats')
									? dataSeparationMessage
									: fileUploadCapableModels.length !== selectedModels.length
										? $i18n.t('Model(s) do not support file upload')
										: !fileUploadEnabled
											? $i18n.t('You do not have permission to upload files.')
											: ''}
								className="w-full"
							>
								<button
									class="flex gap-2 w-full items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl {!fileUploadEnabled ||
									isStrictBlocked('reference_chats')
										? 'opacity-50'
										: ''}"
									on:click={() => {
										if (isStrictBlocked('reference_chats')) return;
										tab = 'chats';
									}}
								>
									<ClockRotateRight />
									<div class="flex-1 flex items-center justify-between">
										<div class="line-clamp-1">{$i18n.t('Reference chats')}</div>
										<div class="text-gray-500">
											<ChevronRight />
										</div>
									</div>
									<Tooltip
										content={pinnedInputItems.includes('reference_chats')
											? $i18n.t('Unpin')
											: $i18n.t('Pin')}
									>
										<button
											class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
											on:click|stopPropagation={() => pinItemHandler('reference_chats')}
										>
											{#if pinnedInputItems.includes('reference_chats')}
												<PinSlash className="size-3.5" />
											{:else}
												<Pin className="size-3.5" />
											{/if}
										</button>
									</Tooltip>
								</button>
							</Tooltip>
						{/if}
					{/if}

					<!-- ═══ ATTACH CAPABILITY ═══ -->
					{#if anyCapabilityAllowed}
						{#if showWebSearchButton || showImageGenerationButton || showCodeInterpreterButton || showDocumentWriterButton || showToolsButton || (toggleFilters && toggleFilters.length > 0)}
							<div class="my-1 border-t border-gray-100 dark:border-gray-800" />
							<div
								class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide px-3 py-1.5"
							>
								{$i18n.t('Attach tools')}
							</div>

							<!-- Tools -->
							{#if isFeatureEnabled('tools') && itemAllowed('tools')}
								{#if tools}
									{#if Object.keys(tools).length > 0}
										<button
											class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
											on:click={() => {
												tab = 'tools';
											}}
										>
											<Wrench />
											<div class="flex-1 flex items-center justify-between">
												<div class="line-clamp-1">
													{$i18n.t('Tools')}
													<span class="ml-0.5 text-gray-500">{Object.keys(tools).length}</span>
												</div>
												<div class="text-gray-500">
													<ChevronRight />
												</div>
											</div>
											<Tooltip
												content={pinnedInputItems.includes('tools')
													? $i18n.t('Unpin')
													: $i18n.t('Pin')}
											>
												<button
													class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
													on:click|stopPropagation={() => pinItemHandler('tools')}
												>
													{#if pinnedInputItems.includes('tools')}
														<PinSlash className="size-3.5" />
													{:else}
														<Pin className="size-3.5" />
													{/if}
												</button>
											</Tooltip>
										</button>
									{/if}
								{:else}
									<div class="py-4">
										<Spinner />
									</div>
								{/if}
							{/if}

							<!-- Filters -->
							{#if toggleFilters && toggleFilters.length > 0 && itemAllowed('filters')}
								{#each toggleFilters.sort( (a, b) => a.name.localeCompare( b.name, undefined, { sensitivity: 'base' } ) ) as filter, filterIdx (filter.id)}
									<Tooltip content={filter?.description} placement="top-start">
										<button
											class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
											on:click={() => {
												if (selectedFilterIds.includes(filter.id)) {
													selectedFilterIds = selectedFilterIds.filter((id) => id !== filter.id);
												} else {
													selectedFilterIds = [...selectedFilterIds, filter.id];
												}
											}}
										>
											<div class="flex-1 truncate">
												<div class="flex flex-1 gap-2 items-center">
													<div class="shrink-0">
														{#if filter?.icon}
															<div class="size-4 items-center flex justify-center">
																<img
																	src={filter.icon}
																	class="size-3.5 {filter.icon.includes('svg')
																		? 'dark:invert-[80%]'
																		: ''}"
																	style="fill: currentColor;"
																	alt={filter.name}
																/>
															</div>
														{:else}
															<Sparkles className="size-4" strokeWidth="1.75" />
														{/if}
													</div>
													<div class="truncate">{filter?.name}</div>
												</div>
											</div>

											{#if filter?.has_user_valves}
												<div class="shrink-0">
													<Tooltip content={$i18n.t('Valves')}>
														<button
															class="self-center w-fit text-sm text-gray-600 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition rounded-full"
															type="button"
															on:click={(e) => {
																e.stopPropagation();
																e.preventDefault();
																onShowValves({
																	type: 'function',
																	id: filter.id
																});
															}}
														>
															<Knobs />
														</button>
													</Tooltip>
												</div>
											{/if}

											<Tooltip
												content={pinnedInputItems.includes(`filter:${filter.id}`)
													? $i18n.t('Unpin')
													: $i18n.t('Pin')}
											>
												<button
													class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
													on:click|stopPropagation={() => pinItemHandler(`filter:${filter.id}`)}
												>
													{#if pinnedInputItems.includes(`filter:${filter.id}`)}
														<PinSlash className="size-3.5" />
													{:else}
														<Pin className="size-3.5" />
													{/if}
												</button>
											</Tooltip>

											<div class="shrink-0">
												<Switch
													state={selectedFilterIds.includes(filter.id)}
													on:change={async (e) => {
														const state = e.detail;
														await tick();
													}}
												/>
											</div>
										</button>
									</Tooltip>
								{/each}
							{/if}

							<!-- Web Search -->
							{#if showWebSearchButton}
								<Tooltip
									content={openInternetBlocked
										? dataSeparationMessage
										: imageGenerationEnabled
											? $i18n.t('Web search and image generation cannot run in the same turn')
											: $i18n.t('Search the internet')}
									placement="top-start"
								>
									<button
										class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50 {openInternetBlocked
											? 'opacity-50'
											: ''}"
										on:click={() => {
											if (openInternetBlocked) return;
											webSearchEnabled = !webSearchEnabled;
											if (webSearchEnabled) {
												imageGenerationEnabled = false;
											}
										}}
									>
										<div class="flex-1 truncate">
											<div class="flex flex-1 gap-2 items-center">
												<div class="shrink-0">
													<GlobeAlt />
												</div>
												<div class="truncate">{$i18n.t('Search the web')}</div>
											</div>
										</div>

										<Tooltip
											content={pinnedInputItems.includes('web_search')
												? $i18n.t('Unpin')
												: $i18n.t('Pin')}
										>
											<button
												class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
												on:click|stopPropagation={() => pinItemHandler('web_search')}
											>
												{#if pinnedInputItems.includes('web_search')}
													<PinSlash className="size-3.5" />
												{:else}
													<Pin className="size-3.5" />
												{/if}
											</button>
										</Tooltip>

										<div class="shrink-0">
											<Switch
												state={webSearchEnabled}
												on:change={async (e) => {
													const state = e.detail;
													await tick();
												}}
											/>
										</div>
									</button>
								</Tooltip>
							{/if}

							<!-- Image Generation -->
							{#if showImageGenerationButton}
								<Tooltip
									content={webSearchEnabled
										? $i18n.t('Web search and image generation cannot run in the same turn')
										: $i18n.t('Generate an image')}
									placement="top-start"
								>
									<button
										class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
										on:click={() => {
											imageGenerationEnabled = !imageGenerationEnabled;
											if (imageGenerationEnabled) {
												webSearchEnabled = false;
											}
										}}
									>
										<div class="flex-1 truncate">
											<div class="flex flex-1 gap-2 items-center">
												<div class="shrink-0">
													<Photo className="size-4" strokeWidth="1.5" />
												</div>
												<div class="truncate">{$i18n.t('Image')}</div>
											</div>
										</div>

										<Tooltip
											content={pinnedInputItems.includes('image_generation')
												? $i18n.t('Unpin')
												: $i18n.t('Pin')}
										>
											<button
												class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
												on:click|stopPropagation={() => pinItemHandler('image_generation')}
											>
												{#if pinnedInputItems.includes('image_generation')}
													<PinSlash className="size-3.5" />
												{:else}
													<Pin className="size-3.5" />
												{/if}
											</button>
										</Tooltip>

										<div class="shrink-0">
											<Switch
												state={imageGenerationEnabled}
												on:change={async (e) => {
													const state = e.detail;
													await tick();
												}}
											/>
										</div>
									</button>
								</Tooltip>
							{/if}

							<!-- Document Writer -->
							{#if showDocumentWriterButton}
								<Tooltip content={$i18n.t('Write a downloadable document')} placement="top-start">
									<button
										class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
										aria-pressed={documentWriterEnabled}
										aria-label={documentWriterEnabled
											? $i18n.t('Disable Document Writer')
											: $i18n.t('Enable Document Writer')}
										on:click={() => {
											documentWriterEnabled = !documentWriterEnabled;
										}}
									>
										<div class="flex-1 truncate">
											<div class="flex flex-1 gap-2 items-center">
												<div class="shrink-0">
													<Document className="size-3.5" strokeWidth="1.75" />
												</div>
												<div class="truncate">{$i18n.t('Document Writer')}</div>
											</div>
										</div>

										<Tooltip
											content={pinnedInputItems.includes('document_writer')
												? $i18n.t('Unpin')
												: $i18n.t('Pin')}
										>
											<button
												class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
												on:click|stopPropagation={() => pinItemHandler('document_writer')}
											>
												{#if pinnedInputItems.includes('document_writer')}
													<PinSlash className="size-3.5" />
												{:else}
													<Pin className="size-3.5" />
												{/if}
											</button>
										</Tooltip>

										<div class="shrink-0">
											<Switch
												state={documentWriterEnabled}
												on:change={async (e) => {
													const state = e.detail;
													await tick();
												}}
											/>
										</div>
									</button>
								</Tooltip>
							{/if}

							<!-- Code Interpreter -->
							{#if showCodeInterpreterButton}
								<Tooltip content={$i18n.t('Execute code for analysis')} placement="top-start">
									<button
										class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
										aria-pressed={codeInterpreterEnabled}
										aria-label={codeInterpreterEnabled
											? $i18n.t('Disable Code Interpreter')
											: $i18n.t('Enable Code Interpreter')}
										on:click={() => {
											codeInterpreterEnabled = !codeInterpreterEnabled;
										}}
									>
										<div class="flex-1 truncate">
											<div class="flex flex-1 gap-2 items-center">
												<div class="shrink-0">
													<Terminal className="size-3.5" strokeWidth="1.75" />
												</div>
												<div class="truncate">{$i18n.t('Code Interpreter')}</div>
											</div>
										</div>

										<Tooltip
											content={pinnedInputItems.includes('code_interpreter')
												? $i18n.t('Unpin')
												: $i18n.t('Pin')}
										>
											<button
												class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
												on:click|stopPropagation={() => pinItemHandler('code_interpreter')}
											>
												{#if pinnedInputItems.includes('code_interpreter')}
													<PinSlash className="size-3.5" />
												{:else}
													<Pin className="size-3.5" />
												{/if}
											</button>
										</Tooltip>

										<div class="shrink-0">
											<Switch
												state={codeInterpreterEnabled}
												on:change={async (e) => {
													const state = e.detail;
													await tick();
												}}
											/>
										</div>
									</button>
								</Tooltip>
							{/if}
						{/if}
					{/if}
				</div>
<<<<<<< HEAD
			{:else if tab === 'knowledge' && isFeatureEnabled('knowledge')}
				<div in:fly={{ x: directTab ? 0 : 20, duration: directTab ? 0 : 150 }}>
					{#if !directTab}
						<button
							class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
							on:click={() => {
								tab = '';
							}}
						>
							<ChevronLeft />
=======
			{:else if tab === 'tool_permissions'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Tool Permissions')}
							</div>
						</div>
					</button>

					<div class="mt-1 space-y-1">
						{#each toolApprovalModes as mode}
							<Tooltip content={$i18n.t(mode.description)} className="w-full">
								<button
									class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl"
									on:click={() => {
										toolApprovalMode = mode.value;
										onToolApprovalModeChange(mode.value);
										tab = '';
									}}
								>
									<div class="flex items-center w-full justify-between min-w-0">
										<div class="line-clamp-1">{$i18n.t(mode.label)}</div>
										{#if toolApprovalMode === mode.value}
											<svg
												class="size-3 shrink-0 text-gray-500"
												viewBox="0 0 24 24"
												fill="none"
												stroke="currentColor"
												stroke-width="2.5"
												stroke-linecap="round"
												stroke-linejoin="round"
											>
												<polyline points="20 6 9 17 4 12" />
											</svg>
										{/if}
									</div>
								</button>
							</Tooltip>
						{/each}
					</div>
				</div>
			{:else if tab === 'knowledge'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />
>>>>>>> upstream/main

							<div class="flex items-center w-full justify-between">
								<div>
									{$i18n.t('Knowledge')}
								</div>
							</div>
						</button>
					{/if}

					<Knowledge {onSelect} />
				</div>
			{:else if tab === 'notes'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
<<<<<<< HEAD
						class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
=======
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
>>>>>>> upstream/main
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Notes')}
							</div>
						</div>
					</button>

					<Notes {onSelect} />
				</div>
<<<<<<< HEAD
=======
			{:else if tab === 'files'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Files')}
							</div>
						</div>
					</button>

					<Files {onSelect} />
				</div>
>>>>>>> upstream/main
			{:else if tab === 'chats'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
<<<<<<< HEAD
						class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
=======
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
>>>>>>> upstream/main
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Chats')}
							</div>
						</div>
					</button>

					<Chats {onSelect} />
				</div>
			{:else if tab === 'tools' && tools && isFeatureEnabled('tools')}
				<div in:fly={{ x: 20, duration: 150 }}>
					<button
<<<<<<< HEAD
						class="flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
=======
						class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
>>>>>>> upstream/main
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Tools')}
								<span class="ml-0.5 text-gray-500">{Object.keys(tools).length}</span>
							</div>
						</div>
					</button>

					{#each Object.keys(tools) as toolId}
						<button
<<<<<<< HEAD
							class="relative flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
							on:click={async (e) => {
								if (!(tools[toolId]?.authenticated ?? true)) {
									e.preventDefault();

									let parts = toolId.split(':');
									let serverId = parts?.at(-1) ?? toolId;

									const authUrl = getOAuthClientAuthorizationUrl(serverId, 'mcp');
									window.open(authUrl, '_self', 'noopener');
								} else {
									tools[toolId].enabled = !tools[toolId].enabled;

									const state = tools[toolId].enabled;
									await tick();

									if (state) {
										selectedToolIds = [...selectedToolIds, toolId];
									} else {
										selectedToolIds = selectedToolIds.filter((id) => id !== toolId);
									}
								}
							}}
						>
							{#if !(tools[toolId]?.authenticated ?? true)}
								<div class="absolute inset-0 opacity-50 rounded-xl cursor-pointer z-10" />
							{/if}
							<div class="flex-1 truncate">
								<div class="flex flex-1 gap-2 items-center">
									<Tooltip content={tools[toolId]?.name ?? ''} placement="top">
										<div class="shrink-0">
											<Wrench />
										</div>
									</Tooltip>
									<Tooltip content={tools[toolId]?.description ?? ''} placement="top-start">
										<div class="truncate">{tools[toolId].name}</div>
									</Tooltip>
								</div>
							</div>

							{#if tools[toolId]?.has_user_valves}
								<div class="shrink-0">
									<Tooltip content={$i18n.t('Valves')}>
										<button
											class="self-center w-fit text-sm text-gray-600 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition rounded-full"
											type="button"
											on:click={(e) => {
												e.stopPropagation();
												e.preventDefault();
												onShowValves({
													type: 'tool',
													id: toolId
												});
											}}
										>
											<Knobs />
										</button>
									</Tooltip>
								</div>
							{/if}

							<div class="shrink-0">
								<Switch state={tools[toolId].enabled} />
=======
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
							type="button"
							on:click={() => {
								uploadOneDriveHandler('personal');
								show = false;
							}}
						>
							<div class="flex flex-col">
								<div class="line-clamp-1">{$i18n.t('Microsoft OneDrive (personal)')}</div>
							</div>
						</button>
					{/if}

					{#if $config?.features?.enable_onedrive_business}
						<button
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
							type="button"
							on:click={() => {
								uploadOneDriveHandler('organizations');
								show = false;
							}}
						>
							<div class="line-clamp-1">
								{$i18n.t('Microsoft OneDrive (work/school)')}
>>>>>>> upstream/main
							</div>
						</button>
					{/each}
				</div>
			{/if}
		</DropdownMenu>
	</div>
</Dropdown>
