<script lang="ts">
	import { onMount, onDestroy, getContext, createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getConfluenceConfig,
		setConfluenceConfig,
		getGoogleDriveConfig,
		setGoogleDriveConfig,
		getOneDriveConfig,
		setOneDriveConfig
	} from '$lib/apis/configs';
	import {
		testConfluenceConnection,
		getConfluenceSharedKbStatus,
		provisionConfluenceSharedKb,
		syncConfluenceSharedKb,
		deleteConfluenceSharedKb,
		type ConfluenceSharedKbStatus,
		type ConfluenceSharedKbSpace,
		type SyncItem
	} from '$lib/apis/confluence';
	import { getAllUsers } from '$lib/apis/users';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ConfluencePickerModal from '$lib/components/workspace/Knowledge/ConfluencePickerModal.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	let loading = true;
	let saving = false;

	// --- Confluence ---
	let ENABLE_CONFLUENCE_INTEGRATION = false;
	let ENABLE_CONFLUENCE_SYNC = false;
	let CONFLUENCE_OAUTH_CLIENT_ID = '';
	let CONFLUENCE_SYNC_INTERVAL_MINUTES = 60;
	let CONFLUENCE_MAX_PAGES_PER_SYNC = 500;
	// The secret + API token round-trip through the form just like the upstream
	// Connections (API key) field — visible to the admin masked behind a reveal
	// toggle via SensitiveInput, sent back verbatim on save.
	let clientSecret = '';
	let CONFLUENCE_SITE_URL = '';
	let CONFLUENCE_BASIC_AUTH_USERNAME = '';
	let basicApiToken = '';
	let testingConnection = false;

	// Auth method and sync mode are independent axes:
	//   auth — 'oauth' (each user signs in) | 'basic' (one service account)
	//   kb   — 'per_user' (on-demand picker) | 'shared' (one pre-synced KB)
	// Every combination is valid except service-account + on-demand, which
	// flattens permissions for no benefit and is gated below.
	let CONFLUENCE_AUTH_MODE: 'oauth' | 'basic' = 'oauth';
	let CONFLUENCE_KB_MODE: 'per_user' | 'shared' = 'per_user';

	// Service account + on-demand has no use case — when auth is the service
	// account, force the pre-synced shared KB.
	$: if (CONFLUENCE_AUTH_MODE === 'basic' && CONFLUENCE_KB_MODE === 'per_user') {
		CONFLUENCE_KB_MODE = 'shared';
	}

	// Basic-mode owner pick: a transient form field, not a persisted config.
	// On provision it becomes ``kb.user_id`` (the sole source of truth for KB
	// ownership). Seeded from the KB row's owner on initial status load so the
	// dropdown reflects the existing owner when re-provisioning.
	let sharedKbOwnerId = '';
	let sharedKbOwnerInitialized = false;
	let adminUsers: { id: string; name: string; email: string }[] = [];
	let sharedKbStatus: ConfluenceSharedKbStatus | null = null;
	let provisioning = false;
	let syncingShared = false;
	let connectingAccount = false;

	// Shared-KB space picker modal + delete confirmation.
	let showSpacePicker = false;
	let showDeleteConfirm = false;
	let deletingShared = false;
	// Polls /shared/status while a sync runs so the last-sync date and file
	// count update without a manual page reload.
	let statusPollTimer: ReturnType<typeof setTimeout> | null = null;
	let statusPollDeadline = 0;
	let statusPollCount = 0;

	// Live state for the shared-KB Sync button. `isSharedSyncing` is derived
	// from the backend status too (not just the local flag) so the button
	// still shows "syncing" + progress after navigating away and back while
	// a sync is running. `syncProgress` is null until the worker reports a
	// total (a no-op sync never gets one — it finishes instantly).
	$: isSharedSyncing = syncingShared || sharedKbStatus?.status === 'syncing';
	$: syncProgress =
		(sharedKbStatus?.progress_total ?? 0) > 0
			? Math.min(
					100,
					Math.round(
						((sharedKbStatus?.progress_current ?? 0) /
							(sharedKbStatus?.progress_total ?? 1)) *
							100
					)
				)
			: null;

	// --- Google Drive ---
	let ENABLE_GOOGLE_DRIVE_INTEGRATION = false;
	let ENABLE_GOOGLE_DRIVE_SYNC = false;
	let GOOGLE_DRIVE_CLIENT_ID = '';
	let GOOGLE_DRIVE_API_KEY = '';
	let GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES = 60;
	let GOOGLE_DRIVE_MAX_FILES_PER_SYNC = 500;

	// --- OneDrive ---
	let ENABLE_ONEDRIVE_INTEGRATION = false;
	let ENABLE_ONEDRIVE_SYNC = false;
	let ENABLE_ONEDRIVE_PERSONAL = true;
	let ENABLE_ONEDRIVE_BUSINESS = true;
	let ONEDRIVE_CLIENT_ID_PERSONAL = '';
	let ONEDRIVE_CLIENT_ID_BUSINESS = '';
	let ONEDRIVE_SHAREPOINT_URL = '';
	let ONEDRIVE_SHAREPOINT_TENANT_ID = '';
	let ONEDRIVE_SYNC_INTERVAL_MINUTES = 60;
	let ONEDRIVE_MAX_FILES_PER_SYNC = 500;

	type ConfluenceConfigResponse = {
		ENABLE_CONFLUENCE_INTEGRATION?: boolean;
		ENABLE_CONFLUENCE_SYNC?: boolean;
		CONFLUENCE_OAUTH_CLIENT_ID?: string;
		CONFLUENCE_OAUTH_CLIENT_SECRET?: string;
		CONFLUENCE_SYNC_INTERVAL_MINUTES?: number;
		CONFLUENCE_MAX_PAGES_PER_SYNC?: number;
		CONFLUENCE_AUTH_MODE?: string;
		CONFLUENCE_SITE_URL?: string;
		CONFLUENCE_BASIC_AUTH_USERNAME?: string;
		CONFLUENCE_BASIC_AUTH_API_TOKEN?: string;
		CONFLUENCE_KB_MODE?: string;
	};

	type GoogleDriveConfigResponse = {
		ENABLE_GOOGLE_DRIVE_INTEGRATION?: boolean;
		ENABLE_GOOGLE_DRIVE_SYNC?: boolean;
		GOOGLE_DRIVE_CLIENT_ID?: string;
		GOOGLE_DRIVE_API_KEY?: string;
		GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES?: number;
		GOOGLE_DRIVE_MAX_FILES_PER_SYNC?: number;
	};

	type OneDriveConfigResponse = {
		ENABLE_ONEDRIVE_INTEGRATION?: boolean;
		ENABLE_ONEDRIVE_SYNC?: boolean;
		ENABLE_ONEDRIVE_PERSONAL?: boolean;
		ENABLE_ONEDRIVE_BUSINESS?: boolean;
		ONEDRIVE_CLIENT_ID_PERSONAL?: string;
		ONEDRIVE_CLIENT_ID_BUSINESS?: string;
		ONEDRIVE_SHAREPOINT_URL?: string;
		ONEDRIVE_SHAREPOINT_TENANT_ID?: string;
		ONEDRIVE_SYNC_INTERVAL_MINUTES?: number;
		ONEDRIVE_MAX_FILES_PER_SYNC?: number;
	};

	const applyConfluenceConfig = (config: ConfluenceConfigResponse | null) => {
		if (!config) return;
		ENABLE_CONFLUENCE_INTEGRATION = config.ENABLE_CONFLUENCE_INTEGRATION ?? false;
		ENABLE_CONFLUENCE_SYNC = config.ENABLE_CONFLUENCE_SYNC ?? false;
		CONFLUENCE_OAUTH_CLIENT_ID = config.CONFLUENCE_OAUTH_CLIENT_ID ?? '';
		CONFLUENCE_SYNC_INTERVAL_MINUTES = config.CONFLUENCE_SYNC_INTERVAL_MINUTES ?? 60;
		CONFLUENCE_MAX_PAGES_PER_SYNC = config.CONFLUENCE_MAX_PAGES_PER_SYNC ?? 0;
		clientSecret = config.CONFLUENCE_OAUTH_CLIENT_SECRET ?? '';
		CONFLUENCE_SITE_URL = config.CONFLUENCE_SITE_URL ?? '';
		CONFLUENCE_BASIC_AUTH_USERNAME = config.CONFLUENCE_BASIC_AUTH_USERNAME ?? '';
		basicApiToken = config.CONFLUENCE_BASIC_AUTH_API_TOKEN ?? '';
		// Each axis is loaded independently — see the decoupled controls below.
		CONFLUENCE_AUTH_MODE = config.CONFLUENCE_AUTH_MODE === 'basic' ? 'basic' : 'oauth';
		CONFLUENCE_KB_MODE = config.CONFLUENCE_KB_MODE === 'shared' ? 'shared' : 'per_user';
	};

	const applyGoogleDriveConfig = (config: GoogleDriveConfigResponse | null) => {
		if (!config) return;
		ENABLE_GOOGLE_DRIVE_INTEGRATION = config.ENABLE_GOOGLE_DRIVE_INTEGRATION ?? false;
		ENABLE_GOOGLE_DRIVE_SYNC = config.ENABLE_GOOGLE_DRIVE_SYNC ?? false;
		GOOGLE_DRIVE_CLIENT_ID = config.GOOGLE_DRIVE_CLIENT_ID ?? '';
		GOOGLE_DRIVE_API_KEY = config.GOOGLE_DRIVE_API_KEY ?? '';
		GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES = config.GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES ?? 60;
		GOOGLE_DRIVE_MAX_FILES_PER_SYNC = config.GOOGLE_DRIVE_MAX_FILES_PER_SYNC ?? 0;
	};

	const applyOneDriveConfig = (config: OneDriveConfigResponse | null) => {
		if (!config) return;
		ENABLE_ONEDRIVE_INTEGRATION = config.ENABLE_ONEDRIVE_INTEGRATION ?? false;
		ENABLE_ONEDRIVE_SYNC = config.ENABLE_ONEDRIVE_SYNC ?? false;
		ENABLE_ONEDRIVE_PERSONAL = config.ENABLE_ONEDRIVE_PERSONAL ?? true;
		ENABLE_ONEDRIVE_BUSINESS = config.ENABLE_ONEDRIVE_BUSINESS ?? true;
		ONEDRIVE_CLIENT_ID_PERSONAL = config.ONEDRIVE_CLIENT_ID_PERSONAL ?? '';
		ONEDRIVE_CLIENT_ID_BUSINESS = config.ONEDRIVE_CLIENT_ID_BUSINESS ?? '';
		ONEDRIVE_SHAREPOINT_URL = config.ONEDRIVE_SHAREPOINT_URL ?? '';
		ONEDRIVE_SHAREPOINT_TENANT_ID = config.ONEDRIVE_SHAREPOINT_TENANT_ID ?? '';
		ONEDRIVE_SYNC_INTERVAL_MINUTES = config.ONEDRIVE_SYNC_INTERVAL_MINUTES ?? 60;
		ONEDRIVE_MAX_FILES_PER_SYNC = config.ONEDRIVE_MAX_FILES_PER_SYNC ?? 0;
	};

	// Refreshes the shared-KB status block (provision state, last sync result).
	const loadSharedKbStatus = async () => {
		try {
			sharedKbStatus = await getConfluenceSharedKbStatus(localStorage.token);
		} catch (err) {
			console.error(err);
		}
	};

	// --- Shared-KB status polling --------------------------------------
	// While a sync runs, re-poll the status so the file count / last-sync
	// date refresh on their own. Polls a minimum number of times even when
	// the status already looks idle — a freshly-triggered background sync
	// may not have flipped to 'syncing' yet, and a no-op sync (0 changed
	// files) finishes between polls. `syncingShared` is held true for the
	// whole poll window so the Sync button shows progress.
	const pollSharedKbStatus = async () => {
		statusPollCount += 1;
		await loadSharedKbStatus();
		const stillSyncing = sharedKbStatus?.status === 'syncing';
		if ((stillSyncing || statusPollCount < 5) && Date.now() < statusPollDeadline) {
			statusPollTimer = setTimeout(pollSharedKbStatus, 2500);
		} else {
			statusPollTimer = null;
			syncingShared = false;
		}
	};

	const startStatusPolling = () => {
		stopStatusPolling();
		statusPollCount = 0;
		statusPollDeadline = Date.now() + 5 * 60 * 1000;
		statusPollTimer = setTimeout(pollSharedKbStatus, 2500);
	};

	const stopStatusPolling = () => {
		if (statusPollTimer) {
			clearTimeout(statusPollTimer);
			statusPollTimer = null;
		}
	};

	onDestroy(stopStatusPolling);

	onMount(async () => {
		try {
			const [confluence, googleDrive, oneDrive, users, shared] = await Promise.all([
				getConfluenceConfig(localStorage.token),
				getGoogleDriveConfig(localStorage.token),
				getOneDriveConfig(localStorage.token),
				getAllUsers(localStorage.token).catch(() => null),
				getConfluenceSharedKbStatus(localStorage.token).catch(() => null)
			]);
			applyConfluenceConfig(confluence);
			applyGoogleDriveConfig(googleDrive);
			applyOneDriveConfig(oneDrive);
			// Owner dropdown is limited to admins — they are the only valid
			// owners of a shared, org-wide knowledge base.
			adminUsers = ((users?.users ?? []) as { id: string; name: string; email: string; role: string }[])
				.filter((u) => u.role === 'admin')
				.map((u) => ({ id: u.id, name: u.name, email: u.email }));
			sharedKbStatus = shared;
			// Seed the basic-mode owner dropdown from the KB row's owner on first
			// load. Subsequent status reloads (after provisioning, etc.) leave the
			// dropdown alone so the admin's in-progress pick isn't clobbered.
			if (!sharedKbOwnerInitialized) {
				sharedKbOwnerId = sharedKbStatus?.owner_id ?? '';
				sharedKbOwnerInitialized = true;
			}
			if (sharedKbStatus?.status === 'syncing') {
				startStatusPolling();
			}
		} catch (err) {
			toast.error(`${err}`);
		}
		loading = false;
	});

	// Persists all three providers' config. Throws on failure so callers
	// (Save, Provision) can decide whether to continue.
	const persistConfig = async () => {
		const [confluence, googleDrive, oneDrive] = await Promise.all([
			setConfluenceConfig(localStorage.token, {
				ENABLE_CONFLUENCE_INTEGRATION,
				ENABLE_CONFLUENCE_SYNC,
				CONFLUENCE_OAUTH_CLIENT_ID,
				CONFLUENCE_OAUTH_CLIENT_SECRET: clientSecret,
				CONFLUENCE_SYNC_INTERVAL_MINUTES,
				// Blank/null input → 0 = no per-sync page limit.
				CONFLUENCE_MAX_PAGES_PER_SYNC: CONFLUENCE_MAX_PAGES_PER_SYNC ?? 0,
				CONFLUENCE_AUTH_MODE,
				CONFLUENCE_SITE_URL,
				CONFLUENCE_BASIC_AUTH_USERNAME,
				CONFLUENCE_BASIC_AUTH_API_TOKEN: basicApiToken,
				CONFLUENCE_KB_MODE
			}),
			setGoogleDriveConfig(localStorage.token, {
				ENABLE_GOOGLE_DRIVE_INTEGRATION,
				ENABLE_GOOGLE_DRIVE_SYNC,
				GOOGLE_DRIVE_CLIENT_ID,
				GOOGLE_DRIVE_API_KEY,
				GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES,
				GOOGLE_DRIVE_MAX_FILES_PER_SYNC: GOOGLE_DRIVE_MAX_FILES_PER_SYNC ?? 0
			}),
			setOneDriveConfig(localStorage.token, {
				ENABLE_ONEDRIVE_INTEGRATION,
				ENABLE_ONEDRIVE_SYNC,
				ENABLE_ONEDRIVE_PERSONAL,
				ENABLE_ONEDRIVE_BUSINESS,
				ONEDRIVE_CLIENT_ID_PERSONAL,
				ONEDRIVE_CLIENT_ID_BUSINESS,
				ONEDRIVE_SHAREPOINT_URL,
				ONEDRIVE_SHAREPOINT_TENANT_ID,
				ONEDRIVE_SYNC_INTERVAL_MINUTES,
				ONEDRIVE_MAX_FILES_PER_SYNC: ONEDRIVE_MAX_FILES_PER_SYNC ?? 0
			})
		]);
		applyConfluenceConfig(confluence);
		applyGoogleDriveConfig(googleDrive);
		applyOneDriveConfig(oneDrive);
	};

	const submitHandler = async () => {
		saving = true;
		try {
			await persistConfig();
			dispatch('save');
		} catch (err) {
			toast.error(`${err}`);
		}
		saving = false;
	};

	// Provisioning is driven by the space-picker modal's confirm event. It
	// saves the form first so the shared-KB endpoint reads the just-entered
	// owner / auth_mode rather than stale config, then applies the opted-in
	// space selection. It deliberately does NOT dispatch 'save' — that
	// triggers a full backend-config refetch in the parent; the explicit
	// Save button is the only place that should.
	const onSpacePickerConfirm = async (e: CustomEvent<{ items: SyncItem[] }>) => {
		provisioning = true;
		try {
			await persistConfig();
			// `provisionConfluenceSharedKb` accepts `ConfluenceSharedKbSpace[]`,
			// a superset of `SyncItem` — the picker output goes straight in.
			// Owner pick only matters in basic mode; OAuth ignores it (caller
			// becomes the owner server-side).
			const ownerForProvision = CONFLUENCE_AUTH_MODE === 'basic' ? sharedKbOwnerId : null;
			sharedKbStatus = await provisionConfluenceSharedKb(
				localStorage.token,
				e.detail.items as ConfluenceSharedKbSpace[],
				ownerForProvision
			);
			toast.success($i18n.t('Shared Confluence knowledge base provisioned.'));
		} catch (err) {
			toast.error(`${err}`);
		}
		provisioning = false;
	};

	// Normalise stored items (which can be the legacy {id, key, name, cloud_id}
	// space shape or the new SyncItem shape) into SyncItem[] for the picker's
	// `currentItems` prop — that way re-provisioning starts with the existing
	// selection ticked.
	$: currentSharedItems = ((sharedKbStatus?.spaces ?? []) as ConfluenceSharedKbSpace[])
		.map((s): SyncItem | null => {
			const type = (s.type === 'page' ? 'page' : 'space') as 'space' | 'page';
			const item_id = s.item_id ?? s.id ?? null;
			if (!item_id) return null;
			const name = s.name ?? s.key ?? item_id;
			return {
				type,
				cloud_id: s.cloud_id ?? '',
				space_id: s.space_id ?? (type === 'space' ? item_id : undefined),
				space_key: s.space_key ?? s.key ?? undefined,
				site_url: s.site_url ?? undefined,
				item_id,
				item_path: s.item_path ?? name,
				name,
				include_descendants: s.include_descendants ?? true
			};
		})
		.filter((x): x is SyncItem => x !== null);

	// Opens the space picker. The picker calls /shared/spaces on open, which
	// in OAuth mode reads the saved owner — so persist the form first, or the
	// picker queries with a stale auth mode / owner.
	const openSpacePicker = async () => {
		provisioning = true;
		try {
			await persistConfig();
			showSpacePicker = true;
		} catch (err) {
			toast.error(`${err}`);
		}
		provisioning = false;
	};

	const deleteSharedHandler = async () => {
		deletingShared = true;
		try {
			await deleteConfluenceSharedKb(localStorage.token);
			toast.success($i18n.t('Shared Confluence knowledge base deleted.'));
			await loadSharedKbStatus();
		} catch (err) {
			toast.error(`${err}`);
		}
		deletingShared = false;
	};

	const syncSharedHandler = async () => {
		syncingShared = true;
		try {
			await syncConfluenceSharedKb(localStorage.token);
			toast.success($i18n.t('Shared Confluence sync started.'));
			await loadSharedKbStatus();
			// startStatusPolling holds `syncingShared` true and re-polls until
			// the sync settles — it clears `syncingShared` itself, so the
			// button keeps showing progress and the status refreshes on its own.
			startStatusPolling();
		} catch (err) {
			toast.error(`${err}`);
			syncingShared = false;
		}
	};

	// Probe the basic-auth credentials currently in the form. A blank API
	// token field falls back server-side to the saved token.
	const testConnection = async () => {
		testingConnection = true;
		try {
			const result = await testConfluenceConnection(localStorage.token, {
				site_url: CONFLUENCE_SITE_URL,
				username: CONFLUENCE_BASIC_AUTH_USERNAME,
				api_token: basicApiToken
			});
			if (result.ok) {
				toast.success(
					$i18n.t('Confluence connection successful ({{count}} spaces visible).', {
						count: result.space_count ?? 0
					})
				);
			} else {
				toast.error($i18n.t('Confluence connection failed: {{error}}', { error: result.detail }));
			}
		} catch (err) {
			toast.error(`${err}`);
		}
		testingConnection = false;
	};

	// Opens the Atlassian OAuth popup so the signed-in admin connects their
	// own Confluence account — that token is what the pre-synced shared KB
	// will sync with. Ownership is intrinsic to the KB row (set at provision
	// time to whoever clicks Provision), so this handler does not need to
	// persist any config; ``owner_connected`` resolves against the calling
	// admin (pre-provision) or ``kb.user_id`` (post-provision) server-side.
	const connectConfluenceAccount = () => {
		connectingAccount = true;

		const popup = window.open(
			`${WEBUI_API_BASE_URL}/confluence/auth/initiate`,
			'confluence_auth',
			'width=600,height=700,scrollbars=yes'
		);

		const handleMessage = (event: MessageEvent) => {
			if (event.data?.type !== 'confluence_auth_callback') return;
			window.removeEventListener('message', handleMessage);
			if (event.data.success) {
				toast.success($i18n.t('Confluence account connected.'));
			} else {
				toast.error($i18n.t('Authorization failed: {{error}}', { error: event.data.error }));
			}
		};
		window.addEventListener('message', handleMessage);

		// The connected badge is driven by /shared/status (it reflects the
		// saved owner), so always re-fetch it when the popup closes — the
		// postMessage above only drives the toast and can be missed on an
		// origin mismatch.
		const checkClosed = setInterval(async () => {
			if (!popup?.closed) return;
			clearInterval(checkClosed);
			window.removeEventListener('message', handleMessage);
			connectingAccount = false;
			await loadSharedKbStatus();
		}, 500);
	};
</script>

<form class="flex flex-col h-full justify-between text-sm" on:submit|preventDefault={submitHandler}>
	<div class="overflow-y-scroll scrollbar-hidden h-full pr-1.5">
		{#if loading}
			<div class="flex justify-center py-8">
				<Spinner />
			</div>
		{:else}
			<!-- Confluence section -->
			<div class="space-y-3">
				<div class="flex items-center gap-2">
					<Confluence className="size-5" />
					<div class="text-base font-medium">{$i18n.t('Confluence')}</div>
				</div>
				<div class="text-xs text-gray-500">
					{$i18n.t('Configure Confluence as a knowledge base sync source.')}
				</div>

				<div class="flex justify-between items-center">
					<div class="font-medium">{$i18n.t('Enable Confluence integration')}</div>
					<Switch bind:state={ENABLE_CONFLUENCE_INTEGRATION} />
				</div>

				{#if ENABLE_CONFLUENCE_INTEGRATION}
					<!-- Auth method and sync mode are two independent controls.
					     Auth method picks how Confluence is reached; sync mode
					     picks whether each user builds their own KBs on-demand or
					     one pre-synced company KB is served to everyone. The
					     background-sync toggle lives in the Sync Settings section. -->
					<div class="flex justify-between items-center">
						<div class="font-medium">{$i18n.t('Authentication method')}</div>
						<select
							class="w-fit pr-8 rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
							bind:value={CONFLUENCE_AUTH_MODE}
						>
							<option value="oauth">{$i18n.t('OAuth')}</option>
							<option value="basic">{$i18n.t('Service account')}</option>
						</select>
					</div>
					<div class="text-xs text-gray-500">
						{#if CONFLUENCE_AUTH_MODE === 'oauth'}
							{$i18n.t(
								'Each user signs in with their own Atlassian account; Confluence is reached with their personal OAuth token.'
							)}
						{:else}
							{$i18n.t(
								'Confluence is reached with one shared service account (username + API token) — no per-user sign-in.'
							)}
						{/if}
					</div>

					<div class="flex justify-between items-center">
						<div class="font-medium">{$i18n.t('Sync mode')}</div>
						<select
							class="w-fit pr-8 rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
							bind:value={CONFLUENCE_KB_MODE}
						>
							<option value="per_user" disabled={CONFLUENCE_AUTH_MODE === 'basic'}>
								{$i18n.t('On-demand')}
							</option>
							<option value="shared">{$i18n.t('Pre-synced')}</option>
						</select>
					</div>
					<div class="text-xs text-gray-500">
						{#if CONFLUENCE_AUTH_MODE === 'basic'}
							{$i18n.t(
								'A service account always serves one pre-synced, read-only knowledge base shared with every user.'
							)}
						{:else if CONFLUENCE_KB_MODE === 'shared'}
							{$i18n.t(
								'One read-only knowledge base, pre-synced from selected Confluence spaces, visible to every user.'
							)}
						{:else}
							{$i18n.t(
								'Each user picks Confluence spaces and pages on-demand and builds their own knowledge bases.'
							)}
						{/if}
					</div>

					{#if CONFLUENCE_AUTH_MODE === 'basic'}
						<div class="space-y-3 pt-2">
							<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
								{$i18n.t('Service account')}
							</div>
							<div class="text-xs text-gray-500">
								{$i18n.t('Authenticate with a Confluence username and API token.')}
							</div>

							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('Confluence site URL')}</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden"
									type="text"
									bind:value={CONFLUENCE_SITE_URL}
									autocomplete="off"
									placeholder="https://your-domain.atlassian.net"
								/>
							</div>

							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('Username')}</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden"
									type="text"
									bind:value={CONFLUENCE_BASIC_AUTH_USERNAME}
									autocomplete="off"
									placeholder="name@example.com"
								/>
							</div>

							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('API token')}</div>
								<div class="flex gap-2">
									<SensitiveInput
										bind:value={basicApiToken}
										required={false}
										autocomplete="new-password"
									/>
								</div>
							</div>

							<div>
								<button
									type="button"
									class="px-3 py-1.5 text-sm rounded-lg bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
									on:click={testConnection}
									disabled={testingConnection}
								>
									{$i18n.t('Test connection')}
									{#if testingConnection}
										<Spinner className="size-3" />
									{/if}
								</button>
							</div>
						</div>
					{:else}
						<div class="space-y-3 pt-2">
							<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
								{$i18n.t('OAuth Credentials')}
							</div>

							<div>
								<div class="mb-1 text-xs text-gray-500">
									{$i18n.t('Confluence OAuth Client ID')}
								</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden"
									type="text"
									bind:value={CONFLUENCE_OAUTH_CLIENT_ID}
									autocomplete="off"
								/>
							</div>

							<div>
								<div class="mb-1 text-xs text-gray-500">
									{$i18n.t('Confluence OAuth Client Secret')}
								</div>
								<div class="flex gap-2">
									<SensitiveInput
										bind:value={clientSecret}
										required={false}
										autocomplete="new-password"
									/>
								</div>
							</div>
						</div>
					{/if}

					<div class="space-y-3 pt-4">
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
							{$i18n.t('Sync Settings')}
						</div>

						<div class="flex justify-between items-center">
							<div class="font-medium">{$i18n.t('Background synchronization')}</div>
							<Switch bind:state={ENABLE_CONFLUENCE_SYNC} />
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Sync interval (minutes)')}
							</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								bind:value={CONFLUENCE_SYNC_INTERVAL_MINUTES}
								min="1"
								autocomplete="off"
							/>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Maximum pages per sync')}
							</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								bind:value={CONFLUENCE_MAX_PAGES_PER_SYNC}
								min="0"
								autocomplete="off"
								placeholder={$i18n.t('Leave empty for no limit')}
							/>
							<div class="mt-1 text-xs text-gray-500">
								{$i18n.t('Leave empty for no limit')}. {$i18n.t(
									'The knowledge base file-count limit still applies.'
								)}
							</div>
						</div>
					</div>

					{#if CONFLUENCE_KB_MODE === 'shared'}
						<div class="space-y-3 pt-4">
							<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
								{$i18n.t('Shared knowledge base')}
							</div>

							{#if CONFLUENCE_AUTH_MODE === 'basic'}
								<div>
									<div class="mb-1 text-xs text-gray-500">
										{$i18n.t('Shared knowledge base owner')}
									</div>
									<select
										class="w-full text-sm bg-transparent outline-hidden rounded-sm py-1"
										bind:value={sharedKbOwnerId}
									>
										<option value="">{$i18n.t('No owner (system)')}</option>
										{#each adminUsers as admin}
											<option value={admin.id}>{admin.name} ({admin.email})</option>
										{/each}
									</select>
									<div class="mt-1 text-xs text-gray-500">
										{$i18n.t(
											'Optional — pick an admin to own the knowledge base, or leave it system-owned.'
										)}
									</div>
								</div>
							{:else}
								<!-- OAuth: the owner is forced to whoever clicks Connect
								     (their per-user token is what the sync runs with),
								     so there is nothing to pick — just show connection
								     state. The Connect handler auto-sets the current
								     admin as owner and persists. -->
								<div>
									<div class="mb-1 flex items-center gap-2">
										<span class="text-xs text-gray-500">{$i18n.t('Confluence account')}</span>
										{#if sharedKbStatus?.owner_connected}
											<Badge type="success" content={$i18n.t('Account connected')} />
										{:else}
											<Badge type="muted" content={$i18n.t('No account connected')} />
										{/if}
									</div>
									<button
										type="button"
										class="px-3 py-1.5 text-sm rounded-lg bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
										on:click={connectConfluenceAccount}
										disabled={connectingAccount}
									>
										{sharedKbStatus?.owner_connected
											? $i18n.t('Reconnect')
											: $i18n.t('Connect Confluence account')}
										{#if connectingAccount}
											<Spinner className="size-3" />
										{/if}
									</button>
								</div>
							{/if}

							<div class="rounded-lg bg-gray-50 dark:bg-gray-850 p-3 space-y-2">
								<div class="flex items-center justify-between">
									<div class="font-medium">{$i18n.t('Shared knowledge base')}</div>
									{#if sharedKbStatus?.provisioned}
										{#if sharedKbStatus?.suspended_at}
											<Badge type="warning" content={$i18n.t('Suspended')} />
										{:else}
											<Badge type="success" content={$i18n.t('Provisioned')} />
										{/if}
									{:else}
										<Badge type="muted" content={$i18n.t('Not provisioned')} />
									{/if}
								</div>

								{#if sharedKbStatus?.provisioned}
									<div class="text-xs text-gray-500 space-y-0.5">
										<div>{$i18n.t('Files synced')}: {sharedKbStatus?.file_count ?? 0}</div>
										<div>
											{$i18n.t('Last sync')}:
											{sharedKbStatus?.last_sync_at
												? new Date(sharedKbStatus.last_sync_at * 1000).toLocaleString()
												: $i18n.t('Never')}
										</div>
										<div>{$i18n.t('Status')}: {sharedKbStatus?.status ?? 'idle'}</div>
										<div class="truncate">
											{$i18n.t('Spaces')}:
											{(sharedKbStatus?.spaces ?? [])
												.map((s) => s.name ?? s.key ?? s.item_id ?? s.id ?? '')
												.filter((n) => n)
												.join(', ') || $i18n.t('None')}
										</div>
									</div>
								{/if}

								<div class="flex gap-2 pt-1 items-center">
									<button
										type="button"
										class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
										on:click={openSpacePicker}
										disabled={provisioning}
									>
										{sharedKbStatus?.provisioned
											? $i18n.t('Re-provision')
											: $i18n.t('Provision')}
										{#if provisioning}
											<Spinner className="size-3" />
										{/if}
									</button>
									{#if sharedKbStatus?.provisioned}
										<button
											type="button"
											class="px-3 py-1.5 h-8 text-sm rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 flex items-center justify-center gap-1.5 min-w-40 disabled:opacity-50 disabled:cursor-not-allowed"
											on:click={syncSharedHandler}
											disabled={isSharedSyncing}
										>
											{#if isSharedSyncing}
												{#if syncProgress !== null}{syncProgress}%{/if}
												<Spinner className="size-3" />
											{:else}
												{$i18n.t('Sync now')}
											{/if}
										</button>
										<button
											type="button"
											class="ml-auto px-3 py-1.5 text-sm rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
											on:click={() => (showDeleteConfirm = true)}
											disabled={deletingShared}
										>
											{$i18n.t('Delete')}
											{#if deletingShared}
												<Spinner className="size-3" />
											{/if}
										</button>
									{/if}
								</div>
							</div>
						</div>
					{/if}
				{/if}
			</div>

			<hr class="border-gray-100/30 dark:border-gray-850/30 my-4" />

			<!-- Google Drive section -->
			<div class="space-y-3">
				<div class="flex items-center gap-2">
					<GoogleDrive className="size-5" />
					<div class="text-base font-medium">{$i18n.t('Google Drive')}</div>
				</div>
				<div class="text-xs text-gray-500">
					{$i18n.t('Configure Google Drive as a knowledge base sync source.')}
				</div>

				<div class="flex justify-between items-center">
					<div class="font-medium">{$i18n.t('Enable Google Drive integration')}</div>
					<Switch bind:state={ENABLE_GOOGLE_DRIVE_INTEGRATION} />
				</div>

				{#if ENABLE_GOOGLE_DRIVE_INTEGRATION}
					<div class="space-y-3 pt-2">
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
							{$i18n.t('OAuth Credentials')}
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Google Drive OAuth Client ID')}
							</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="text"
								bind:value={GOOGLE_DRIVE_CLIENT_ID}
								autocomplete="off"
							/>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Google Drive API Key')}
							</div>
							<div class="flex gap-2">
								<SensitiveInput bind:value={GOOGLE_DRIVE_API_KEY} required={false} />
							</div>
						</div>
					</div>

					<div class="space-y-3 pt-4">
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
							{$i18n.t('Sync Settings')}
						</div>

						<div class="flex justify-between items-center">
							<div class="font-medium">{$i18n.t('Background synchronization')}</div>
							<Switch bind:state={ENABLE_GOOGLE_DRIVE_SYNC} />
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Sync interval (minutes)')}
							</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								bind:value={GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES}
								min="1"
								autocomplete="off"
							/>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Maximum files per sync')}
							</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								bind:value={GOOGLE_DRIVE_MAX_FILES_PER_SYNC}
								min="0"
								autocomplete="off"
								placeholder={$i18n.t('Leave empty for no limit')}
							/>
							<div class="mt-1 text-xs text-gray-500">
								{$i18n.t('Leave empty for no limit')}. {$i18n.t(
									'The knowledge base file-count limit still applies.'
								)}
							</div>
						</div>
					</div>
				{/if}
			</div>

			<hr class="border-gray-100/30 dark:border-gray-850/30 my-4" />

			<!-- OneDrive section -->
			<div class="space-y-3">
				<div class="flex items-center gap-2">
					<OneDrive className="size-5" />
					<div class="text-base font-medium">{$i18n.t('OneDrive')}</div>
				</div>
				<div class="text-xs text-gray-500">
					{$i18n.t('Configure OneDrive as a knowledge base sync source.')}
				</div>

				<div class="flex justify-between items-center">
					<div class="font-medium">{$i18n.t('Enable OneDrive integration')}</div>
					<Switch bind:state={ENABLE_ONEDRIVE_INTEGRATION} />
				</div>

				{#if ENABLE_ONEDRIVE_INTEGRATION}
					<div class="space-y-3 pt-2">
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
							{$i18n.t('Accounts')}
						</div>

						<div class="flex justify-between items-center">
							<div>{$i18n.t('Allow personal accounts')}</div>
							<Switch bind:state={ENABLE_ONEDRIVE_PERSONAL} />
						</div>

						{#if ENABLE_ONEDRIVE_PERSONAL}
							<div>
								<div class="mb-1 text-xs text-gray-500">
									{$i18n.t('OneDrive Client ID (personal)')}
								</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden"
									type="text"
									bind:value={ONEDRIVE_CLIENT_ID_PERSONAL}
									autocomplete="off"
								/>
							</div>
						{/if}

						<div class="flex justify-between items-center">
							<div>{$i18n.t('Allow business accounts')}</div>
							<Switch bind:state={ENABLE_ONEDRIVE_BUSINESS} />
						</div>

						{#if ENABLE_ONEDRIVE_BUSINESS}
							<div>
								<div class="mb-1 text-xs text-gray-500">
									{$i18n.t('OneDrive Client ID (business)')}
								</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden"
									type="text"
									bind:value={ONEDRIVE_CLIENT_ID_BUSINESS}
									autocomplete="off"
								/>
							</div>
						{/if}
					</div>

					{#if ENABLE_ONEDRIVE_BUSINESS}
						<div class="space-y-3 pt-4">
							<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
								{$i18n.t('SharePoint')}
							</div>

							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('SharePoint URL')}</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden"
									type="text"
									bind:value={ONEDRIVE_SHAREPOINT_URL}
									autocomplete="off"
									placeholder="https://contoso.sharepoint.com"
								/>
							</div>

							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('SharePoint Tenant ID')}</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden"
									type="text"
									bind:value={ONEDRIVE_SHAREPOINT_TENANT_ID}
									autocomplete="off"
								/>
							</div>
						</div>
					{/if}

					<div class="space-y-3 pt-4">
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
							{$i18n.t('Sync Settings')}
						</div>

						<div class="flex justify-between items-center">
							<div class="font-medium">{$i18n.t('Background synchronization')}</div>
							<Switch bind:state={ENABLE_ONEDRIVE_SYNC} />
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Sync interval (minutes)')}
							</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								bind:value={ONEDRIVE_SYNC_INTERVAL_MINUTES}
								min="1"
								autocomplete="off"
							/>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">
								{$i18n.t('Maximum files per sync')}
							</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								bind:value={ONEDRIVE_MAX_FILES_PER_SYNC}
								min="0"
								autocomplete="off"
								placeholder={$i18n.t('Leave empty for no limit')}
							/>
							<div class="mt-1 text-xs text-gray-500">
								{$i18n.t('Leave empty for no limit')}. {$i18n.t(
									'The knowledge base file-count limit still applies.'
								)}
							</div>
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-1.5 {saving
				? 'cursor-not-allowed'
				: ''}"
			type="submit"
			disabled={saving}
		>
			{$i18n.t('Save')}
			{#if saving}
				<Spinner className="size-3" />
			{/if}
		</button>
	</div>
</form>

<!-- Confluence shared-KB space picker — opens from the Provision button. -->
<!-- Confluence shared-KB picker — same tree explorer the per-user picker uses,
     wired here to pre-select items already opted into the shared KB and to
     hand the result straight to provisionConfluenceSharedKb. -->
<ConfluencePickerModal
	bind:show={showSpacePicker}
	title={$i18n.t('Spaces to sync')}
	confirmLabel={$i18n.t('Provision')}
	currentItems={currentSharedItems}
	on:select={onSpacePickerConfirm}
/>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete shared knowledge base')}
	message={$i18n.t(
		'The shared Confluence knowledge base and all its synced pages will be removed. This cannot be undone.'
	)}
	confirmLabel={$i18n.t('Delete')}
	on:confirm={deleteSharedHandler}
/>
