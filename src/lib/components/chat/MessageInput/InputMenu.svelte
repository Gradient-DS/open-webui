<script lang="ts">
	// [Gradient] One "+" menu for the composer: context, knowledge and tools sections, each
	// item pinnable to the composer bar ($settings.pinnedInputItems). It absorbs upstream's
	// separate integrations menu; tenant gates apply to every item.
	import { isFeatureEnabled } from '$lib/utils/features';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { getContext, onDestroy, tick } from 'svelte';
	import type { ChatInputCallbacks } from '$lib/types/chatAttachment';
	import { fly } from 'svelte/transition';
	import {
		BINARY_TOOL_STATES,
		DOCUMENT_WRITER_STATES,
		documentWriterFlags,
		TOOL_OFF_DESCRIPTION,
		TOOL_STATE_LABELS,
		WEB_SEARCH_STATE_DESCRIPTIONS,
		DOCUMENT_WRITER_STATE_DESCRIPTIONS,
		documentWriterState,
		WEB_SEARCH_STATES,
		nextToolState,
		webSearchFlags,
		webSearchState,
		type ToolState
	} from '$lib/utils/toolState';
	import { prepareBusinessDocumentPicker } from '$lib/utils/onedrive-file-picker';
	import { connectLiveSource, prefetchLiveConnections } from '$lib/utils/live-connections';
	import { LIVE_DOCUMENT_STATES } from '$lib/utils/toolState';
	import { toast } from 'svelte-sonner';

	import { config, user, tools as _tools, skills as _skills, toolServers } from '$lib/stores';

	import { deleteOAuthSession } from '$lib/apis/auths';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import DocumentArrowUp from '$lib/components/icons/DocumentArrowUp.svelte';
	import Camera from '$lib/components/icons/Camera.svelte';
	import Clip from '$lib/components/icons/Clip.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import FolderOpen from '$lib/components/icons/FolderOpen.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import PageEdit from '$lib/components/icons/PageEdit.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import Terminal from '$lib/components/icons/Terminal.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import Cube from '$lib/components/icons/Cube.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Knobs from '$lib/components/icons/Knobs.svelte';
	import LinkSlash from '$lib/components/icons/LinkSlash.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import OneDriveSearch from '$lib/components/icons/OneDriveSearch.svelte';
	import MailSearch from '$lib/components/icons/MailSearch.svelte';
	import Chats from './InputMenu/Chats.svelte';
	import Files from './InputMenu/Files.svelte';
	import Notes from './InputMenu/Notes.svelte';
	import Knowledge from './InputMenu/Knowledge.svelte';
	import MenuItem from './InputMenu/MenuItem.svelte';
	import SearchInput from './InputMenu/SearchInput.svelte';
	import AttachWebpageModal from './AttachWebpageModal.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	type IntegrationItem = {
		id: string;
		name: string;
		description?: string;
		meta?: { description?: string };
		is_active?: boolean;
		authenticated?: boolean;
		has_user_valves?: boolean;
		[key: string]: unknown;
	};

	type ToggleFilter = {
		id: string;
		name: string;
		description?: string;
		icon?: string;
		has_user_valves?: boolean;
	};

	export let files = [];

	export let selectedModels: string[] = [];
	export let fileUploadCapableModels: string[] = [];

	export let screenCaptureHandler: () => void;
	export let uploadFilesHandler: () => void;
	export let inputFilesHandler: (files: File[]) => void;

	export let uploadGoogleDriveHandler: () => void;
	export let uploadOneDriveHandler: () => void;
	// [Gradient] Assistant-builder restrictions and strict data-separation state. Item keys
	// match the pin ids: 'upload_files', 'capture', 'attach_webpage', 'attach_notes',
	// 'google_drive', 'onedrive', 'knowledge', 'reference_chats', 'tools', 'skills',
	// 'filters', 'web_search', 'image_generation', 'code_interpreter', 'document_writer'.
	// Null shows every globally enabled item.
	export let restrictTo: string[] | null = null;
	$: itemAllowed = (key: string) => restrictTo === null || restrictTo.includes(key);
	export let openInternetBlocked = false;
	export let internalBlocked = false;
	export let dataSeparationMessage = '';

	export let onUpload: ChatInputCallbacks['onUpload'];
	export let onClose: () => void;
	export let toolApprovalMode = 'full';
	export let onToolApprovalModeChange: (mode: string) => void = () => {};

	// Tools section state (two-way bound by MessageInput).
	export let selectedToolIds: string[] = [];
	export let selectedSkillIds: string[] = [];
	export let selectedFilterIds: string[] = [];
	export let toggleFilters: ToggleFilter[] = [];
	export let showWebSearchButton = false;
	export let webSearchEnabled = false;
	export let liveDocumentsState: ToolState = 'off';
	export let liveMailState: ToolState = 'off';
	let connectingDocuments = false;
	let connectingMail = false;
	async function cycleLive(family: 'live_documents' | 'mail') {
		const mail = family === 'mail';
		if (!(mail ? $config?.features?.enable_live_mail : $config?.features?.enable_live_documents))
			return;
		if (mail ? connectingMail : connectingDocuments) return;
		const state = mail ? liveMailState : liveDocumentsState;
		const next = nextToolState(state, LIVE_DOCUMENT_STATES);
		if (mail) connectingMail = true;
		else connectingDocuments = true;
		try {
			if (next !== 'off' && state === 'off')
				await connectLiveSource(localStorage.token, mail ? 'outlook_mail' : 'onedrive', family);
			if (mail) liveMailState = next;
			else liveDocumentsState = next;
		} catch (error) {
			toast.error(String(error));
		} finally {
			if (mail) connectingMail = false;
			else connectingDocuments = false;
		}
	}
	export const cycleLiveDocuments = () => cycleLive('live_documents');
	export const cycleLiveMail = () => cycleLive('mail');
	// [Gradient] Altijd; webSearchEnabled alone is Auto.
	export let webSearchRequired = false;
	export let showImageGenerationButton = false;
	export let imageGenerationEnabled = false;
	export let showCodeInterpreterButton = false;
	export let codeInterpreterEnabled = false;
	export let showDocumentWriterButton = false;
	export let documentWriterEnabled = true;
	export let documentWriterRequired = false;
	export let oauthRedirectHandler: (tool: {
		id: string;
		serverId: string;
		authType?: string | null;
	}) => void = () => {};
	export let onShowValves: (e: { type: string; id: string }) => void = () => {};
	export let onWebSearchToggle: (enabled: boolean) => void = () => {};
	export let closeOnOutsideClick = true;

	let show = false;
	$: if (
		show &&
		($config?.features?.enable_live_documents || $config?.features?.enable_onedrive_business)
	) {
		void prefetchLiveConnections(localStorage.token).catch(() => {});
	}
	$: if (show && $config?.features?.enable_live_mail) {
		void prefetchLiveConnections(localStorage.token, 'outlook_mail', 'mail').catch(() => {});
	}
	$: if (show && $config?.features?.enable_onedrive_business) {
		void prepareBusinessDocumentPicker().catch(() => {});
	}
	let tab = '';
	// Opened straight into a submenu from a pinned composer button: no back row.
	let directTab = false;

	let showAttachWebpageModal = false;
	// [Gradient] Programmatic Webpage URL entry point (GRA-222).
	export const openWebpageModal = () => (showAttachWebpageModal = true);
	// Opens the menu on a submenu, for pinned composer buttons.
	export const openTab = (tabName: string) => {
		tab = tabName;
		directTab = true;
		show = true;
	};

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

	let webUploadEnabled = true;
	$: webUploadEnabled = $user?.role === 'admin' || ($user?.permissions?.chat?.web_upload ?? true);
	$: toolPermissionsEnabled = $config?.features?.enable_tool_permissions ?? false;
	$: valvesAllowed = $user?.role === 'admin' || ($user?.permissions?.chat?.valves ?? true);

	$: if (!fileUploadEnabled && files.length > 0) {
		files = [];
	}

	$: uploadTooltip = internalBlocked
		? dataSeparationMessage
		: fileUploadCapableModels.length !== selectedModels.length
			? $i18n.t('Model(s) do not support file upload')
			: !fileUploadEnabled
				? $i18n.t('You do not have permission to upload files.')
				: '';

	$: oneDriveBusiness = !!$config?.features?.enable_onedrive_business;

	// Item visibility, also used to hide empty section headers.
	$: showUploadFiles = itemAllowed('upload_files');
	$: showCapture = itemAllowed('capture') && isFeatureEnabled('capture');
	$: showWebpage = itemAllowed('attach_webpage') && isFeatureEnabled('webpage_url');
	$: showNotes = itemAllowed('attach_notes') && ($config?.features?.enable_notes ?? false);
	$: showGoogleDrive =
		fileUploadEnabled &&
		itemAllowed('google_drive') &&
		!!$config?.features?.enable_google_drive_integration;
	$: showOneDrive =
		fileUploadEnabled &&
		itemAllowed('onedrive') &&
		!!$config?.features?.enable_onedrive_integration &&
		oneDriveBusiness;
	$: showKnowledge = itemAllowed('knowledge') && isFeatureEnabled('knowledge');
	$: showReferenceChats = itemAllowed('reference_chats') && isFeatureEnabled('reference_chats');

	$: showTools =
		itemAllowed('tools') && isFeatureEnabled('tools') && Object.keys(tools ?? {}).length > 0;
	$: showSkills =
		itemAllowed('skills') && isFeatureEnabled('skills') && Object.keys(skills ?? {}).length > 0;
	$: showFilters = itemAllowed('filters') && (toggleFilters ?? []).length > 0;
	$: showLiveMail =
		itemAllowed('live_mail') && (!!$config?.features?.enable_live_mail || liveMailState !== 'off');
	$: showLiveDocuments =
		itemAllowed('live_documents') &&
		(!!$config?.features?.enable_live_documents || liveDocumentsState !== 'off');
	$: showWebSearch = itemAllowed('web_search') && (showWebSearchButton || webSearchEnabled);
	$: showImageGeneration =
		itemAllowed('image_generation') && (showImageGenerationButton || imageGenerationEnabled);
	$: showCodeInterpreter =
		itemAllowed('code_interpreter') && (showCodeInterpreterButton || codeInterpreterEnabled);
	$: showDocumentWriter =
		itemAllowed('document_writer') && (showDocumentWriterButton || documentWriterEnabled);
	$: showToolPermissions =
		toolPermissionsEnabled && itemAllowed('tools') && isFeatureEnabled('tools');

	$: anyContext =
		showUploadFiles || showCapture || showWebpage || showNotes || showGoogleDrive || showOneDrive;
	$: anyKnowledge = showKnowledge || showReferenceChats;
	$: anyTools =
		showLiveMail ||
		showLiveDocuments ||
		showTools ||
		showSkills ||
		showFilters ||
		showWebSearch ||
		showImageGeneration ||
		showCodeInterpreter ||
		showDocumentWriter ||
		showToolPermissions;
	// Tools and skills load when the menu opens; keep the section visible meanwhile.
	$: toolsLoading =
		tools === null &&
		itemAllowed('tools') &&
		(isFeatureEnabled('tools') || isFeatureEnabled('skills'));

	$: sortedToggleFilters = [...(toggleFilters ?? [])].sort((a, b) =>
		a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
	);

	const detectMobile = () => {
		const userAgent = navigator.userAgent || navigator.vendor;
		return /android|iphone|ipad|ipod|windows phone/i.test(userAgent);
	};

	const handleFileChange = (event: Event) => {
		const inputFiles = Array.from((event.currentTarget as HTMLInputElement).files ?? []);
		if (inputFiles && inputFiles.length > 0) {
			console.log(inputFiles);
			inputFilesHandler(inputFiles);
		}
	};

	const closeMenu = () => {
		// Dropdown's onOpenChange only fires from its own close paths.
		tab = '';
		directTab = false;
		show = false;
	};

	const onSelect = (item) => {
		if (internalBlocked) return;
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

		closeMenu();
	};

	// Tools and skills (ported from upstream's IntegrationsMenu).
	let tools: Record<string, IntegrationItem> | null = null;
	let skills: Record<string, IntegrationItem> | null = null;
	let toolQuery = '';
	let skillQuery = '';
	let searchedToolQuery = '';
	let searchedSkillQuery = '';
	let toolSearchDebounceTimer: ReturnType<typeof setTimeout>;
	let skillSearchDebounceTimer: ReturnType<typeof setTimeout>;
	let toolRequestId = 0;
	let skillRequestId = 0;

	$: toolIds = Object.keys(tools ?? {});
	$: skillIds = Object.keys(skills ?? {});

	$: if (show && toolQuery !== searchedToolQuery) {
		scheduleToolSearch();
	}

	$: if (show && skillQuery !== searchedSkillQuery) {
		scheduleSkillSearch();
	}

	$: if (show) {
		initIntegrations();
	}

	const initIntegrations = async () => {
		await Promise.all([loadTools(), loadSkills()]);
	};

	const setTools = (toolItems: IntegrationItem[] | null, query = '') => {
		const q = query.trim().toLowerCase();
		const items = (toolItems ?? []).reduce<Record<string, IntegrationItem>>((a, tool) => {
			a[tool.id] = {
				...tool,
				name: tool.name,
				description: tool.meta?.description
			};
			return a;
		}, {});

		const servers = ($toolServers ?? []) as {
			url: string;
			info?: { title?: string; description?: string };
		}[];
		for (const serverIdx in servers) {
			const server = servers[serverIdx];
			if (server.info) {
				const name = server?.info?.title ?? server.url;
				if (q && !name.toLowerCase().includes(q)) {
					continue;
				}

				items[`direct_server:${serverIdx}`] = {
					id: `direct_server:${serverIdx}`,
					name,
					description: server.info.description ?? ''
				};
			}
		}

		tools = items;
	};

	const setSkills = (skillItems: IntegrationItem[] | null, query = '') => {
		skills = (skillItems ?? [])
			.filter((skill) => skill.is_active)
			.reduce<Record<string, IntegrationItem>>((a, skill) => {
				a[skill.id] = {
					...skill,
					name: skill.name,
					description: skill.description
				};
				return a;
			}, {});
	};

	const loadTools = async (query = toolQuery) => {
		const requestId = ++toolRequestId;
		const q = query.trim();
		searchedToolQuery = query;

		if (q) {
			const toolItems = await getTools(localStorage.token, q).catch(() => []);
			if (requestId !== toolRequestId) return;
			setTools(toolItems, q);
			return;
		}

		if ($_tools === null) {
			await _tools.set(await getTools(localStorage.token));
		}
		if (requestId !== toolRequestId) return;
		setTools($_tools, q);
	};

	const loadSkills = async (query = skillQuery) => {
		const requestId = ++skillRequestId;
		const q = query.trim();
		searchedSkillQuery = query;

		if (q) {
			const skillItems = await getSkills(localStorage.token, q).catch(() => []);
			if (requestId !== skillRequestId) return;
			setSkills(skillItems, q);
			return;
		}

		if ($_skills === null) {
			await _skills.set(await getSkills(localStorage.token));
		}
		if (requestId !== skillRequestId) return;
		setSkills($_skills, q);
	};

	const scheduleToolSearch = () => {
		clearTimeout(toolSearchDebounceTimer);
		toolSearchDebounceTimer = setTimeout(() => {
			loadTools();
		}, 200);
	};

	const scheduleSkillSearch = () => {
		clearTimeout(skillSearchDebounceTimer);
		skillSearchDebounceTimer = setTimeout(() => {
			loadSkills();
		}, 200);
	};

	const toggleTool = async (toolId: string, e: MouseEvent) => {
		const tool = tools?.[toolId];
		if (!tool) return;

		if (!(tool.authenticated ?? true)) {
			e.preventDefault();

			const parts = toolId.split(':');
			oauthRedirectHandler({
				id: toolId,
				serverId: parts.at(-1) ?? toolId,
				authType: parts.length > 1 ? (parts[0] === 'server' ? parts[1] : parts[0]) : null
			});
			return;
		}

		const state = !selectedToolIds.includes(toolId);
		await tick();

		if (state) {
			selectedToolIds = [...selectedToolIds, toolId];
		} else {
			selectedToolIds = selectedToolIds.filter((id) => id !== toolId);
		}
	};

	const toggleSkill = async (skillId: string) => {
		if (!skills?.[skillId]) return;

		const state = !selectedSkillIds.includes(skillId);
		await tick();

		if (state) {
			selectedSkillIds = [...selectedSkillIds, skillId];
		} else {
			selectedSkillIds = selectedSkillIds.filter((id) => id !== skillId);
		}
	};

	const toggleFilter = (filterId: string) => {
		if (selectedFilterIds.includes(filterId)) {
			selectedFilterIds = selectedFilterIds.filter((id) => id !== filterId);
		} else {
			selectedFilterIds = [...selectedFilterIds, filterId];
		}
	};

	$: webSearchToolState = webSearchState(webSearchEnabled, webSearchRequired);
	$: imageGenerationState = (imageGenerationEnabled ? 'required' : 'off') as ToolState;
	$: codeInterpreterState = (codeInterpreterEnabled ? 'required' : 'off') as ToolState;
	$: documentWriterToolState = documentWriterState(documentWriterEnabled, documentWriterRequired);

	const stateAriaLabel = (label: string, state: ToolState, description: string) =>
		`${label}: ${$i18n.t(TOOL_STATE_LABELS[state])}. ${description}`;

	// [Gradient] Web search cycles Auto, Altijd, Uit. Exported for the pinned composer button.
	export const cycleWebSearch = () => {
		// Strict data separation and #171 capability exclusion.
		if (openInternetBlocked) return;
		const wasOff = !webSearchEnabled;
		const { enabled, required } = webSearchFlags(
			nextToolState(webSearchToolState, WEB_SEARCH_STATES)
		);
		webSearchEnabled = enabled;
		webSearchRequired = required;
		if (webSearchEnabled && imageGenerationEnabled) {
			toast.message($i18n.t('Web search and image generation cannot run in the same turn'));
			imageGenerationEnabled = false;
		}
		// The confirmation flow cares about web search becoming possible, not about Altijd.
		if (wasOff !== !webSearchEnabled) onWebSearchToggle(webSearchEnabled);
	};

	// [Gradient] PDF writer cycles Auto, Altijd, Uit; the other tools use Uit and Altijd. Exported for the pinned composer buttons.
	export const cycleTool = (tool: 'image_generation' | 'code_interpreter' | 'document_writer') => {
		if (tool === 'image_generation') {
			imageGenerationEnabled =
				nextToolState(imageGenerationState, BINARY_TOOL_STATES) === 'required';
			// #171: image generation takes over from web search, which must then be Uit.
			if (imageGenerationEnabled && webSearchEnabled) {
				toast.message($i18n.t('Web search and image generation cannot run in the same turn'));
				webSearchEnabled = false;
				webSearchRequired = false;
				onWebSearchToggle(false);
			}
		} else if (tool === 'code_interpreter') {
			codeInterpreterEnabled =
				nextToolState(codeInterpreterState, BINARY_TOOL_STATES) === 'required';
		} else {
			const { enabled, required } = documentWriterFlags(
				nextToolState(documentWriterToolState, DOCUMENT_WRITER_STATES)
			);
			documentWriterEnabled = enabled;
			documentWriterRequired = required;
		}
	};

	onDestroy(() => {
		clearTimeout(toolSearchDebounceTimer);
		clearTimeout(skillSearchDebounceTimer);
	});

	const sectionHeaderClass =
		'px-2 pt-1.5 pb-1 text-[0.6875rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500 select-none';
	const backRowClass =
		'flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/60';
	const subRowClass =
		'relative flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/60';
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
	{closeOnOutsideClick}
	visualViewportAware
	onOpenChange={(state) => {
		// [Gradient] Dropdown exposes a callback, not a change event.
		if (state === false) {
			tab = '';
			directTab = false;
			toolQuery = '';
			skillQuery = '';
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content">
		<!-- [Gradient] Capped to the room the dropdown has, so the menu and its submenus
		     scroll inside it on short windows and mobile. -->
		<DropdownMenu
			className="w-84 max-w-[calc(100vw-2rem)] flex flex-col overflow-hidden"
			style="max-height: min(24rem, var(--dropdown-available-height, 24rem));"
		>
			{#if tab === ''}
				<div
					class="min-h-0 overflow-y-auto overflow-x-hidden scrollbar-thin"
					in:fly={{ x: -20, duration: 150 }}
				>
					<!-- ═══ Context ═══ -->
					{#if anyContext}
						<div class={sectionHeaderClass}>{$i18n.t('Attach context')}</div>
					{/if}

					{#if showUploadFiles}
						<MenuItem
							label={$i18n.t('Files')}
							pinId="upload_files"
							tooltip={uploadTooltip}
							disabled={internalBlocked || !fileUploadEnabled}
							onClick={() => {
								if (internalBlocked) return;
								if (fileUploadEnabled) {
									uploadFilesHandler();
									closeMenu();
								}
							}}
						>
							<Clip slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showCapture}
						<MenuItem
							label={$i18n.t('Capture')}
							pinId="capture"
							tooltip={uploadTooltip}
							disabled={internalBlocked || !fileUploadEnabled}
							onClick={() => {
								if (internalBlocked) return;
								if (fileUploadEnabled) {
									if (!detectMobile()) {
										screenCaptureHandler();
									} else {
										document.getElementById('camera-input')?.click();
									}
									closeMenu();
								}
							}}
						>
							<Camera slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showWebpage}
						<MenuItem
							label={$i18n.t('Webpage URL')}
							pinId="attach_webpage"
							tooltip={openInternetBlocked
								? dataSeparationMessage
								: !webUploadEnabled
									? $i18n.t('You do not have permission to upload web content.')
									: ''}
							disabled={openInternetBlocked || !webUploadEnabled}
							onClick={() => {
								if (openInternetBlocked) return;
								if (webUploadEnabled) {
									showAttachWebpageModal = true;
									closeMenu();
								}
							}}
						>
							<Link slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showUploadFiles}
						<MenuItem
							label={$i18n.t('Attach Files')}
							pinId="attach_files"
							submenu
							tooltip={uploadTooltip}
							disabled={internalBlocked || !fileUploadEnabled}
							onClick={() => {
								if (internalBlocked) return;
								if (fileUploadEnabled) {
									tab = 'files';
								}
							}}
						>
							<DocumentArrowUp slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showNotes}
						<MenuItem
							label={$i18n.t('Attach Notes')}
							pinId="attach_notes"
							submenu
							tooltip={uploadTooltip}
							disabled={internalBlocked || !fileUploadEnabled}
							onClick={() => {
								if (internalBlocked) return;
								tab = 'notes';
							}}
						>
							<PageEdit slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showGoogleDrive}
						<MenuItem
							label={$i18n.t('Google Drive')}
							pinId="google_drive"
							tooltip={internalBlocked ? dataSeparationMessage : ''}
							disabled={internalBlocked}
							onClick={() => {
								if (internalBlocked) return;
								uploadGoogleDriveHandler();
								closeMenu();
							}}
						>
							<GoogleDrive slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showOneDrive}
						<MenuItem
							label={$i18n.t('OneDrive Files')}
							pinId="onedrive"
							tooltip={internalBlocked ? dataSeparationMessage : ''}
							disabled={internalBlocked}
							onClick={() => {
								if (internalBlocked) return;
								uploadOneDriveHandler();
								closeMenu();
							}}
						>
							<OneDrive slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					<!-- ═══ Knowledge ═══ -->
					{#if anyKnowledge}
						{#if anyContext}
							<div class="h-px mx-1 my-1 bg-gray-100 dark:bg-gray-800"></div>
						{/if}
						<div class={sectionHeaderClass}>{$i18n.t('Attach databases')}</div>
					{/if}

					{#if showKnowledge}
						<MenuItem
							label={$i18n.t('Knowledge database')}
							pinId="knowledge"
							submenu
							tooltip={uploadTooltip}
							disabled={internalBlocked || !fileUploadEnabled}
							onClick={() => {
								if (internalBlocked) return;
								tab = 'knowledge';
							}}
						>
							<FolderOpen slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showReferenceChats}
						<MenuItem
							label={$i18n.t('Reference chats')}
							pinId="reference_chats"
							submenu
							tooltip={uploadTooltip}
							disabled={internalBlocked || !fileUploadEnabled}
							onClick={() => {
								if (internalBlocked) return;
								tab = 'chats';
							}}
						>
							<ClockRotateRight slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					<!-- ═══ Tools ═══ -->
					{#if anyTools || toolsLoading}
						{#if anyContext || anyKnowledge}
							<div class="h-px mx-1 my-1 bg-gray-100 dark:bg-gray-800"></div>
						{/if}
						<div class={sectionHeaderClass}>{$i18n.t('Attach tools')}</div>
					{/if}

					{#if showLiveMail}
						<MenuItem
							label={$i18n.t('Mail search')}
							pinId="live_mail"
							ariaLabel={stateAriaLabel(
								$i18n.t('Mail search'),
								liveMailState,
								liveMailState === 'off'
									? $i18n.t(TOOL_OFF_DESCRIPTION)
									: $i18n.t('The model decides whether to search your mail')
							)}
							tooltip={!$config?.features?.enable_live_mail
								? $i18n.t('Unavailable')
								: stateAriaLabel(
										$i18n.t('Mail search'),
										liveMailState,
										liveMailState === 'off'
											? $i18n.t(TOOL_OFF_DESCRIPTION)
											: $i18n.t('The model decides whether to search your mail')
									)}
							toolState={liveMailState}
							disabled={connectingMail || !$config?.features?.enable_live_mail}
							onClick={cycleLiveMail}
						>
							<MailSearch slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showLiveDocuments}
						<MenuItem
							label={$i18n.t('OneDrive search')}
							pinId="live_documents"
							ariaLabel={stateAriaLabel(
								$i18n.t('OneDrive search'),
								liveDocumentsState,
								liveDocumentsState === 'off'
									? $i18n.t(TOOL_OFF_DESCRIPTION)
									: $i18n.t('The model decides whether to search OneDrive')
							)}
							tooltip={!$config?.features?.enable_live_documents
								? $i18n.t('Unavailable')
								: stateAriaLabel(
										$i18n.t('OneDrive search'),
										liveDocumentsState,
										liveDocumentsState === 'off'
											? $i18n.t(TOOL_OFF_DESCRIPTION)
											: $i18n.t('The model decides whether to search OneDrive')
									)}
							toolState={liveDocumentsState}
							disabled={connectingDocuments || !$config?.features?.enable_live_documents}
							onClick={cycleLiveDocuments}
						>
							<OneDriveSearch slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showWebSearch}
						<MenuItem
							label={$i18n.t('Search the web')}
							pinId="web_search"
							toolState={webSearchToolState}
							tooltipPlacement="top-start"
							tooltip={!showWebSearchButton
								? $i18n.t('Unavailable')
								: openInternetBlocked
									? dataSeparationMessage
									: imageGenerationEnabled
										? $i18n.t('Web search and image generation cannot run in the same turn')
										: $i18n.t(WEB_SEARCH_STATE_DESCRIPTIONS[webSearchToolState])}
							ariaLabel={stateAriaLabel(
								$i18n.t('Search the web'),
								webSearchToolState,
								$i18n.t(WEB_SEARCH_STATE_DESCRIPTIONS[webSearchToolState])
							)}
							disabled={!showWebSearchButton || openInternetBlocked}
							onClick={() => showWebSearchButton && cycleWebSearch()}
						>
							<GlobeAlt slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showImageGeneration}
						<!-- [Gradient] #171: explain the reciprocal web search exclusion. -->
						<MenuItem
							label={$i18n.t('Image')}
							pinId="image_generation"
							toolState={imageGenerationState}
							tooltipPlacement="top-start"
							tooltip={!showImageGenerationButton
								? $i18n.t('Unavailable')
								: webSearchEnabled
									? $i18n.t('Web search and image generation cannot run in the same turn')
									: imageGenerationEnabled
										? $i18n.t('Generate an image')
										: $i18n.t(TOOL_OFF_DESCRIPTION)}
							ariaLabel={stateAriaLabel(
								$i18n.t('Image'),
								imageGenerationState,
								imageGenerationEnabled
									? $i18n.t('Generate an image')
									: $i18n.t(TOOL_OFF_DESCRIPTION)
							)}
							disabled={!showImageGenerationButton}
							onClick={() => showImageGenerationButton && cycleTool('image_generation')}
						>
							<Photo slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showCodeInterpreter}
						<MenuItem
							label={$i18n.t('Code Interpreter')}
							pinId="code_interpreter"
							toolState={codeInterpreterState}
							tooltipPlacement="top-start"
							tooltip={!showCodeInterpreterButton
								? $i18n.t('Unavailable')
								: codeInterpreterEnabled
									? $i18n.t('Execute code for analysis')
									: $i18n.t(TOOL_OFF_DESCRIPTION)}
							ariaLabel={stateAriaLabel(
								$i18n.t('Code Interpreter'),
								codeInterpreterState,
								codeInterpreterEnabled
									? $i18n.t('Execute code for analysis')
									: $i18n.t(TOOL_OFF_DESCRIPTION)
							)}
							disabled={!showCodeInterpreterButton}
							onClick={() => showCodeInterpreterButton && cycleTool('code_interpreter')}
						>
							<Terminal slot="icon" className="size-3.5" strokeWidth="1.75" />
						</MenuItem>
					{/if}

					{#if showDocumentWriter}
						<MenuItem
							label={$i18n.t('PDF writer')}
							pinId="document_writer"
							toolState={documentWriterToolState}
							tooltipPlacement="top-start"
							tooltip={!showDocumentWriterButton
								? $i18n.t('Unavailable')
								: $i18n.t(DOCUMENT_WRITER_STATE_DESCRIPTIONS[documentWriterToolState])}
							ariaLabel={stateAriaLabel(
								$i18n.t('PDF writer'),
								documentWriterToolState,
								$i18n.t(DOCUMENT_WRITER_STATE_DESCRIPTIONS[documentWriterToolState])
							)}
							disabled={!showDocumentWriterButton}
							onClick={() => showDocumentWriterButton && cycleTool('document_writer')}
						>
							<Document slot="icon" className="size-3.5" strokeWidth="1.75" />
						</MenuItem>
					{/if}

					{#if showFilters}
						{#each sortedToggleFilters as filter (filter.id)}
							<MenuItem
								label={filter.name}
								pinId={`filter:${filter.id}`}
								toggle={selectedFilterIds.includes(filter.id)}
								tooltipPlacement="top-start"
								tooltip={filter?.description ?? ''}
								onClick={() => toggleFilter(filter.id)}
							>
								<svelte:fragment slot="icon">
									{#if filter?.icon}
										<img
											src={filter.icon}
											class="size-3.5 {filter.icon.includes('data:image/svg')
												? 'dark:invert-[80%]'
												: ''}"
											style="fill: currentColor;"
											alt={filter.name}
										/>
									{:else}
										<Sparkles className="size-3.5" strokeWidth="1.75" />
									{/if}
								</svelte:fragment>

								<svelte:fragment slot="actions">
									{#if filter?.has_user_valves && valvesAllowed}
										<Tooltip content={$i18n.t('Valves')} className="flex shrink-0">
											<button
												class="self-center w-fit text-sm text-gray-600 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition rounded-full"
												type="button"
												aria-label={$i18n.t('Valves')}
												on:click={(e) => {
													e.stopPropagation();
													e.preventDefault();
													onShowValves({ type: 'function', id: filter.id });
												}}
											>
												<Knobs />
											</button>
										</Tooltip>
									{/if}
								</svelte:fragment>
							</MenuItem>
						{/each}
					{/if}

					{#if showTools}
						<MenuItem
							label={$i18n.t('Tools')}
							count={toolIds.length}
							pinId="tools"
							submenu
							onClick={() => {
								tab = 'tools';
							}}
						>
							<Wrench slot="icon" className="size-3.5" />
						</MenuItem>
					{/if}

					{#if showSkills}
						<MenuItem
							label={$i18n.t('Skills')}
							count={skillIds.length}
							pinId="skills"
							submenu
							onClick={() => {
								tab = 'skills';
							}}
						>
							<Cube slot="icon" className="size-3.5" strokeWidth="1.75" />
						</MenuItem>
					{/if}

					{#if toolsLoading}
						<div class="py-2">
							<Spinner className="size-4" />
						</div>
					{/if}

					{#if showToolPermissions}
						<MenuItem
							label={$i18n.t('Tool Permissions')}
							submenu
							onClick={() => {
								tab = 'tool_permissions';
							}}
						>
							<svg
								slot="icon"
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
							<div slot="actions" class="shrink-0 text-xs text-gray-500 truncate max-w-24">
								{$i18n.t(
									toolApprovalModes.find((mode) => mode.value === toolApprovalMode)?.label ??
										'Full access'
								)}
							</div>
						</MenuItem>
					{/if}
				</div>
			{:else}
				<div
					class="flex min-h-0 flex-1 flex-col gap-0.5 overflow-hidden"
					in:fly={{ x: directTab ? 0 : 20, duration: directTab ? 0 : 150 }}
				>
					{#if !directTab}
						<button
							class={backRowClass}
							on:click={() => {
								tab = '';
								toolQuery = '';
								skillQuery = '';
							}}
						>
							<ChevronLeft />

							<div class="flex items-center w-full justify-between">
								<div>
									{#if tab === 'knowledge'}
										{$i18n.t('Knowledge database')}
									{:else if tab === 'notes'}
										{$i18n.t('Notes')}
									{:else if tab === 'files'}
										{$i18n.t('Files')}
									{:else if tab === 'chats'}
										{$i18n.t('Reference chats')}
									{:else if tab === 'tool_permissions'}
										{$i18n.t('Tool Permissions')}
									{:else if tab === 'tools'}
										{$i18n.t('Tools')}
										<span class="ml-0.5 text-gray-500">{toolIds.length}</span>
									{:else if tab === 'skills'}
										{$i18n.t('Skills')}
										<span class="ml-0.5 text-gray-500">{skillIds.length}</span>
									{/if}
								</div>
							</div>
						</button>
					{/if}

					{#if tab === 'knowledge'}
						<Knowledge {onSelect} />
					{:else if tab === 'notes'}
						<Notes {onSelect} />
					{:else if tab === 'files'}
						<Files {onSelect} />
					{:else if tab === 'chats'}
						<Chats {onSelect} />
					{:else if tab === 'tool_permissions'}
						<div class="space-y-1">
							{#each toolApprovalModes as mode}
								<Tooltip content={$i18n.t(mode.description)} className="w-full">
									<button
										class={subRowClass}
										on:click={() => {
											toolApprovalMode = mode.value;
											onToolApprovalModeChange(mode.value);
											tab = '';
											directTab = false;
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
					{:else if tab === 'tools'}
						<SearchInput bind:value={toolQuery} placeholder={$i18n.t('Search tools')} />

						<div class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin">
							{#if tools === null}
								<div class="py-4">
									<Spinner />
								</div>
							{:else if toolIds.length === 0}
								<div class="text-center text-xs text-gray-500 py-3">
									{$i18n.t('No tools found')}
								</div>
							{:else}
								<div class="flex flex-col gap-0.5">
									{#each toolIds as toolId}
										<button
											class={subRowClass}
											aria-pressed={(tools?.[toolId]?.authenticated ?? true)
												? selectedToolIds.includes(toolId)
												: undefined}
											on:click={async (e) => {
												await toggleTool(toolId, e);
											}}
										>
											{#if !(tools?.[toolId]?.authenticated ?? true)}
												<!-- make it slighly darker and not clickable -->
												<div
													class="absolute inset-0 opacity-50 rounded-xl cursor-pointer z-10"
												></div>
											{/if}
											<div class="flex-1 truncate">
												<div class="flex flex-1 gap-2 items-center">
													<Tooltip content={tools?.[toolId]?.name ?? ''} placement="top">
														<div class="shrink-0">
															<Wrench className="size-3.5" />
														</div>
													</Tooltip>
													<Tooltip
														content={tools?.[toolId]?.description ?? ''}
														placement="top-start"
													>
														<div class=" truncate">{tools?.[toolId]?.name}</div>
													</Tooltip>
												</div>
											</div>

											{#if tools?.[toolId]?.authenticated === true && toolId.startsWith('server:mcp:')}
												<div class="shrink-0">
													<Tooltip content={$i18n.t('Disconnect OAuth')}>
														<button
															class="self-center w-fit text-sm text-gray-600 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition rounded-full"
															type="button"
															on:click={async (e) => {
																e.stopPropagation();
																e.preventDefault();

																const parts = toolId.split(':');
																const serverId = parts.at(-1) ?? toolId;
																const provider = `mcp:${serverId}`;

																try {
																	await deleteOAuthSession(localStorage.token, provider);
																	toast.success($i18n.t('OAuth session disconnected'));

																	// Refresh tools to update authenticated state
																	_tools.set(await getTools(localStorage.token));
																	selectedToolIds = selectedToolIds.filter((id) => id !== toolId);
																	await initIntegrations();
																} catch (err) {
																	toast.error(`${err ?? $i18n.t('Failed to disconnect')}`);
																}
															}}
														>
															<LinkSlash className="size-3.5" />
														</button>
													</Tooltip>
												</div>
											{/if}

											{#if tools?.[toolId]?.has_user_valves && valvesAllowed}
												<div class=" shrink-0">
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

											<div class=" shrink-0" inert>
												<Switch state={selectedToolIds.includes(toolId)} />
											</div>
										</button>
									{/each}
								</div>
							{/if}
						</div>
					{:else if tab === 'skills'}
						<SearchInput bind:value={skillQuery} placeholder={$i18n.t('Search skills')} />

						<div class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin">
							{#if skills === null}
								<div class="py-4">
									<Spinner />
								</div>
							{:else if skillIds.length === 0}
								<div class="text-center text-xs text-gray-500 py-3">
									{$i18n.t('No skills found')}
								</div>
							{:else}
								<div class="flex flex-col gap-0.5">
									{#each skillIds as skillId}
										<button
											class={subRowClass}
											aria-pressed={selectedSkillIds.includes(skillId)}
											on:click={async () => {
												await toggleSkill(skillId);
											}}
										>
											<div class="flex-1 truncate">
												<div class="flex flex-1 gap-2 items-center">
													<Tooltip content={skills?.[skillId]?.name ?? ''} placement="top">
														<div class="shrink-0">
															<Cube className="size-3.5" strokeWidth="1.75" />
														</div>
													</Tooltip>
													<Tooltip
														content={skills?.[skillId]?.description ?? ''}
														placement="top-start"
													>
														<div class=" truncate">{skills?.[skillId]?.name}</div>
													</Tooltip>
												</div>
											</div>

											<div class=" shrink-0" inert>
												<Switch state={selectedSkillIds.includes(skillId)} />
											</div>
										</button>
									{/each}
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/if}
		</DropdownMenu>
	</div>
</Dropdown>
