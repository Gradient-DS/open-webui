<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { getConfluenceConfig, setConfluenceConfig } from '$lib/apis/configs';
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
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import ConfluencePickerModal from '$lib/components/workspace/Knowledge/ConfluencePickerModal.svelte';
	import SyncSettingsSection from './SyncSettingsSection.svelte';
	import SharedKbSection from './SharedKbSection.svelte';
	import { connectionErrorMessage } from './errors';
	import type { ConfluenceConfigResponse, SharedKbApi, SharedKbStatusLike } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');
	// Re-emit the shared-KB lifecycle events so the orchestrator can refresh the
	// cross-provider status line. The payload is forwarded verbatim from
	// SharedKbSection, so it carries its `SharedKbStatusLike` shape.
	const dispatch = createEventDispatcher<{
		provisioned: { status: SharedKbStatusLike };
		deleted: void;
		synced: void;
	}>();

	// Provider config — owned by this section. The orchestrator calls
	// `load()`/`persist()` (instance bindings) and reads `enabled` for the card
	// header.
	export let enabled = false;

	// Hook run before opening the shared-KB picker and before provisioning so the
	// backend reads fresh config. The orchestrator overrides this to persist ALL
	// providers' config (matching the monolith's `persistConfig()`); standalone
	// it defaults to persisting just this section.
	export let beforeSharedKbAction: (() => Promise<void>) | null = null;

	let ENABLE_CONFLUENCE_INTEGRATION = false;
	let ENABLE_CONFLUENCE_SYNC = false;
	let CONFLUENCE_OAUTH_CLIENT_ID = '';
	let CONFLUENCE_SYNC_INTERVAL_MINUTES = 60;
	let CONFLUENCE_MAX_PAGES_PER_SYNC: number | null = 500;
	// The secret + API token round-trip through the form just like the upstream
	// Connections (API key) field — visible to the admin masked behind a reveal
	// toggle via SensitiveInput, sent back verbatim on save.
	let clientSecret = '';
	let CONFLUENCE_SITE_URL = '';
	let CONFLUENCE_BASIC_AUTH_USERNAME = '';
	let basicApiToken = '';
	// Scoped-mode credential + the optional gateway cloudId. The scoped token
	// round-trips masked via SensitiveInput like the basic token; the cloudId is
	// auto-resolved from the site URL server-side when left blank.
	let scopedApiToken = '';
	let CONFLUENCE_CLOUD_ID = '';
	let testingConnection = false;

	// Auth method and sync mode are independent axes:
	//   auth — 'oauth' (each user signs in) | 'basic' (service account, classic
	//          API token) | 'scoped' (service account, scoped read-only token)
	//   kb   — 'per_user' (on-demand picker) | 'shared' (one pre-synced KB)
	// Every combination is valid except service-account + on-demand, which
	// flattens permissions for no benefit and is gated below.
	let CONFLUENCE_AUTH_MODE: 'oauth' | 'basic' | 'scoped' = 'oauth';
	let CONFLUENCE_KB_MODE: 'per_user' | 'shared' = 'per_user';
	// The auth method as last loaded/saved — the baseline used to detect a switch
	// in `persist()` (switching auth while a shared KB exists is blocked, since the
	// KB was built under the old identity's permissions).
	let loadedAuthMode: 'oauth' | 'basic' | 'scoped' = 'oauth';

	// 'basic' and 'scoped' are the two service-account modes — both reach
	// Confluence with one shared credential (no per-user OAuth token) and force
	// the pre-synced shared KB.
	$: isServiceMode = CONFLUENCE_AUTH_MODE !== 'oauth';

	// Service account + on-demand has no use case — when auth is a service mode,
	// force the pre-synced shared KB.
	$: if (isServiceMode && CONFLUENCE_KB_MODE === 'per_user') {
		CONFLUENCE_KB_MODE = 'shared';
	}

	// Mirror the enable flag out so the card header badge stays in sync.
	$: enabled = ENABLE_CONFLUENCE_INTEGRATION;

	// Autosave: notify the orchestrator whenever a persisted field changes (incl.
	// the programmatic basic⇒shared coupling above). The baseline is re-established
	// on every applyConfluenceConfig() (load/save) and the first run only seeds it,
	// so neither load nor save triggers a spurious save. A blocked switch still
	// throws in persist() → the orchestrator shows it inline.
	export let onChange: (() => void) | null = null;
	let savedBaseline: string | null = null;
	$: changeSnapshot = JSON.stringify([
		ENABLE_CONFLUENCE_INTEGRATION,
		ENABLE_CONFLUENCE_SYNC,
		CONFLUENCE_OAUTH_CLIENT_ID,
		CONFLUENCE_SYNC_INTERVAL_MINUTES,
		CONFLUENCE_MAX_PAGES_PER_SYNC,
		clientSecret,
		CONFLUENCE_SITE_URL,
		CONFLUENCE_BASIC_AUTH_USERNAME,
		basicApiToken,
		scopedApiToken,
		CONFLUENCE_CLOUD_ID,
		CONFLUENCE_AUTH_MODE,
		CONFLUENCE_KB_MODE
	]);
	$: {
		if (savedBaseline === null) {
			savedBaseline = changeSnapshot;
		} else if (changeSnapshot !== savedBaseline) {
			savedBaseline = changeSnapshot;
			onChange?.();
		}
	}

	// Basic-mode owner pick: a transient form field, not a persisted config.
	// On provision it becomes ``kb.user_id`` (the sole source of truth for KB
	// ownership). Seeded from the KB row's owner on initial status load so the
	// dropdown reflects the existing owner when re-provisioning.
	let sharedKbOwnerId = '';
	let sharedKbOwnerInitialized = false;
	// Passed in from CloudSync.svelte (fetched once there, shared with TOPdesk).
	export let adminUsers: { id: string; name: string; email: string }[] = [];
	let sharedKbStatus: ConfluenceSharedKbStatus | null = null;
	let connectingAccount = false;

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
		scopedApiToken = config.CONFLUENCE_SCOPED_API_TOKEN ?? '';
		CONFLUENCE_CLOUD_ID = config.CONFLUENCE_CLOUD_ID ?? '';
		// Each axis is loaded independently — see the decoupled controls below.
		CONFLUENCE_AUTH_MODE =
			config.CONFLUENCE_AUTH_MODE === 'basic' || config.CONFLUENCE_AUTH_MODE === 'scoped'
				? config.CONFLUENCE_AUTH_MODE
				: 'oauth';
		CONFLUENCE_KB_MODE = config.CONFLUENCE_KB_MODE === 'shared' ? 'shared' : 'per_user';
		// Reset the switch-detection baseline to the persisted value (also runs after
		// a successful save, so the next switch is measured from the new state).
		loadedAuthMode = CONFLUENCE_AUTH_MODE;
		// Re-baseline the autosave snapshot against the freshly-loaded/saved values.
		savedBaseline = null;
	};

	export async function load() {
		const [config, shared] = await Promise.all([
			getConfluenceConfig(localStorage.token),
			getConfluenceSharedKbStatus(localStorage.token).catch(() => null)
		]);
		applyConfluenceConfig(config);
		sharedKbStatus = shared;
		// Seed the basic-mode owner dropdown from the KB row's owner on first load.
		// Subsequent status reloads (after provisioning, etc.) leave the dropdown
		// alone so the admin's in-progress pick isn't clobbered.
		if (!sharedKbOwnerInitialized) {
			sharedKbOwnerId = sharedKbStatus?.owner_id ?? '';
			sharedKbOwnerInitialized = true;
		}
	}

	// Persists this provider's config. Throws on failure so callers (the
	// orchestrator's Save, the shared-KB `beforeAction`) can decide whether to
	// continue. Payload shape is identical to the monolith's.
	export async function persist() {
		// Two guards mirror the backend's: a provisioned shared KB must be deleted
		// before either switch, so neither silently strands nor corrupts it. We throw
		// the bare message (not toast + throw) so the orchestrator's persistAll catch
		// surfaces it as a single clean toast, matching the backend-error path.
		//
		// 1. Switching the auth method while a shared KB exists — the KB's pages were
		//    gathered under the old identity's permissions; re-syncing under a
		//    different identity would mix auth identities and silently change content.
		if (CONFLUENCE_AUTH_MODE !== loadedAuthMode && sharedKbStatus?.provisioned) {
			throw $i18n.t(
				'Delete the shared Confluence knowledge base before switching authentication method.'
			);
		}
		// 2. Switching away from the pre-synced shared mode would orphan the KB.
		if (CONFLUENCE_KB_MODE !== 'shared' && sharedKbStatus?.provisioned) {
			throw $i18n.t(
				'Delete the shared Confluence knowledge base before switching to on-request (per-user) mode.'
			);
		}

		const config = await setConfluenceConfig(localStorage.token, {
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
			CONFLUENCE_SCOPED_API_TOKEN: scopedApiToken,
			CONFLUENCE_CLOUD_ID,
			CONFLUENCE_KB_MODE
		});
		applyConfluenceConfig(config);
	}

	// ── Shared-KB API adapter ───────────────────────────────────────────
	// Maps the generic SharedKbSection contract onto the Confluence client.
	// Owner pick only matters in basic mode; OAuth ignores it (the caller
	// becomes the owner server-side). `provisionConfluenceSharedKb` accepts
	// `ConfluenceSharedKbSpace[]`, a superset of `SyncItem` — the picker output
	// goes straight in.
	const sharedKbApi: SharedKbApi<
		ConfluenceSharedKbStatus,
		{ items: unknown[]; ownerUserId: string | null }
	> = {
		getStatus: () => getConfluenceSharedKbStatus(localStorage.token),
		provision: ({ items, ownerUserId }) =>
			provisionConfluenceSharedKb(
				localStorage.token,
				items as ConfluenceSharedKbSpace[],
				ownerUserId
			),
		sync: () => syncConfluenceSharedKb(localStorage.token),
		remove: () => deleteConfluenceSharedKb(localStorage.token)
	};

	// Persist the form before the picker opens / provisioning runs so the
	// shared-KB endpoints read the just-entered owner / auth_mode / credentials
	// rather than stale config. Delegates to the injected hook (orchestrator
	// persists all providers); falls back to persisting just this section.
	const runBeforeSharedKbAction = async () => {
		if (beforeSharedKbAction) {
			await beforeSharedKbAction();
		} else {
			await persist();
		}
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

	// "Spaces: ..." detail line for the shared-KB block.
	$: sharedSpacesLabel =
		(sharedKbStatus?.spaces ?? [])
			.map((s) => s.name ?? s.key ?? s.item_id ?? s.id ?? '')
			.filter((n) => n)
			.join(', ') || $i18n.t('None');

	// Probe the service-account credentials currently in the form. A blank token
	// field falls back server-side to the saved token. The probe targets the
	// active service mode: 'basic' (token → site) or 'scoped' (token + cloudId →
	// gateway). Only reachable from a service-mode block, so the mode is never
	// 'oauth' here.
	const testConnection = async () => {
		testingConnection = true;
		try {
			const result = await testConfluenceConnection(localStorage.token, {
				mode: CONFLUENCE_AUTH_MODE === 'scoped' ? 'scoped' : 'basic',
				site_url: CONFLUENCE_SITE_URL,
				username: CONFLUENCE_BASIC_AUTH_USERNAME,
				api_token: CONFLUENCE_AUTH_MODE === 'scoped' ? scopedApiToken : basicApiToken,
				cloud_id: CONFLUENCE_CLOUD_ID
			});
			if (result.ok) {
				toast.success(
					$i18n.t('Confluence connection successful ({{count}} spaces visible).', {
						count: result.space_count ?? 0
					})
				);
			} else {
				console.error('Confluence connection failed:', result.reason, result.detail);
				toast.error(
					$i18n.t('Confluence connection failed: {{error}}', {
						error: connectionErrorMessage($i18n, result.reason, result.detail)
					})
				);
			}
		} catch (err) {
			console.error(err);
			toast.error(
				$i18n.t('Confluence connection failed: {{error}}', {
					error: connectionErrorMessage($i18n, 'unreachable')
				})
			);
		}
		testingConnection = false;
	};

	// Opens the Atlassian OAuth popup so the signed-in admin connects their own
	// Confluence account — that token is what the pre-synced shared KB will sync
	// with. Ownership is intrinsic to the KB row (set at provision time to whoever
	// clicks Provision), so ownership needs no persisting here. But /auth/initiate
	// builds the authorization URL from the stored OAuth client credentials, so we
	// must flush the form first: under debounced autosave a just-entered client
	// ID/secret may not have been written yet. The popup is opened synchronously
	// to a blank page (a window.open after an await loses the user gesture and is
	// blocked), then navigated to /auth/initiate once the save lands.
	const connectConfluenceAccount = () => {
		connectingAccount = true;

		const popup = window.open(
			'about:blank',
			'confluence_auth',
			'width=600,height=700,scrollbars=yes'
		);
		if (!popup) {
			connectingAccount = false;
			toast.error($i18n.t('Please allow popups to connect Confluence.'));
			return;
		}

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

		// The connected badge is driven by /shared/status (it reflects the saved
		// owner), so always re-fetch it when the popup closes — the postMessage
		// above only drives the toast and can be missed on an origin mismatch.
		const checkClosed = setInterval(async () => {
			if (!popup.closed) return;
			clearInterval(checkClosed);
			window.removeEventListener('message', handleMessage);
			connectingAccount = false;
			try {
				sharedKbStatus = await getConfluenceSharedKbStatus(localStorage.token);
			} catch (err) {
				console.error(err);
			}
		}, 500);

		// Flush the form so the OAuth client credentials are persisted, then point
		// the already-open popup at the initiate endpoint. On a persist failure
		// (e.g. a blocked auth-mode switch) close the popup and surface the error.
		(async () => {
			try {
				await persist();
			} catch (err) {
				clearInterval(checkClosed);
				window.removeEventListener('message', handleMessage);
				connectingAccount = false;
				popup.close();
				toast.error(`${err}`);
				return;
			}
			popup.location.href = `${WEBUI_API_BASE_URL}/confluence/auth/initiate`;
		})();
	};
</script>

<div class="space-y-3">
	<div class="text-xs text-gray-500">
		{$i18n.t('Configure Confluence as a knowledge base sync source.')}
	</div>

	<div class="flex justify-between items-center">
		<div class="font-medium">{$i18n.t('Enable Confluence integration')}</div>
		<Switch bind:state={ENABLE_CONFLUENCE_INTEGRATION} />
	</div>

	{#if ENABLE_CONFLUENCE_INTEGRATION}
		<!-- Auth method and sync mode are two independent controls. Auth method
		     picks how Confluence is reached; sync mode picks whether each user
		     builds their own KBs on-demand or one pre-synced company KB is served
		     to everyone. The background-sync toggle lives in the Sync Settings
		     section. -->
		<div class="flex justify-between items-center">
			<div class="font-medium">{$i18n.t('Authentication method')}</div>
			<select
				class="w-fit pr-8 rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
				bind:value={CONFLUENCE_AUTH_MODE}
			>
				<option value="oauth">{$i18n.t('OAuth')}</option>
				<option value="basic">{$i18n.t('Service account')}</option>
				<option value="scoped">{$i18n.t('Service account (scoped token)')}</option>
			</select>
		</div>
		<div class="text-xs text-gray-500">
			{#if CONFLUENCE_AUTH_MODE === 'oauth'}
				{$i18n.t(
					'Each user signs in with their own Atlassian account; Confluence is reached with their personal OAuth token.'
				)}
			{:else if CONFLUENCE_AUTH_MODE === 'scoped'}
				{$i18n.t(
					'Confluence is reached with one service account using a scoped, read-only API token — no per-user sign-in and no OAuth app.'
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
				<option value="per_user" disabled={isServiceMode}>
					{$i18n.t('On-demand')}
				</option>
				<option value="shared">{$i18n.t('Pre-synced')}</option>
			</select>
		</div>
		<div class="text-xs text-gray-500">
			{#if isServiceMode}
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

		{#if isServiceMode}
			<div class="space-y-3 pt-2">
				<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
					{$i18n.t('Service account')}
				</div>
				<div class="text-xs text-gray-500">
					{#if CONFLUENCE_AUTH_MODE === 'scoped'}
						{$i18n.t(
							'Authenticate with a service-account email and a scoped (read-only) API token.'
						)}
					{:else}
						{$i18n.t('Authenticate with a Confluence username and API token.')}
					{/if}
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

				{#if CONFLUENCE_AUTH_MODE === 'scoped'}
					<div>
						<div class="mb-1 text-xs text-gray-500">{$i18n.t('Scoped API token')}</div>
						<div class="flex gap-2">
							<SensitiveInput
								bind:value={scopedApiToken}
								required={false}
								autocomplete="new-password"
							/>
						</div>
					</div>

					<div>
						<div class="mb-1 text-xs text-gray-500">{$i18n.t('Cloud ID')}</div>
						<input
							class="w-full text-sm bg-transparent outline-hidden"
							type="text"
							bind:value={CONFLUENCE_CLOUD_ID}
							autocomplete="off"
							placeholder=""
						/>
						<div class="mt-1 text-xs text-gray-500">
							{$i18n.t('Auto-detected from the site URL; set it manually only if detection fails.')}
						</div>
					</div>

					<div class="text-xs text-gray-500">
						{$i18n.t(
							'Create the token via "Create API token with scopes", set Scope type to "Granular", and grant these Confluence read scopes: read:space:confluence, read:page:confluence, read:hierarchical-content:confluence, read:content-details:confluence, read:label:confluence.'
						)}
					</div>
				{:else}
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
				{/if}

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

		<div class="pt-4">
			<SyncSettingsSection
				bind:backgroundEnabled={ENABLE_CONFLUENCE_SYNC}
				bind:intervalMinutes={CONFLUENCE_SYNC_INTERVAL_MINUTES}
				bind:maxPerSync={CONFLUENCE_MAX_PAGES_PER_SYNC}
				itemNoun="pages"
			/>
		</div>

		{#if CONFLUENCE_KB_MODE === 'shared' || sharedKbStatus?.provisioned}
			<div class="pt-4">
				<SharedKbSection
					api={sharedKbApi}
					pickerComponent={ConfluencePickerModal}
					currentItems={currentSharedItems}
					showOwnerPick={isServiceMode}
					bind:ownerId={sharedKbOwnerId}
					owners={adminUsers}
					itemNoun="pages"
					bind:status={sharedKbStatus}
					beforeAction={runBeforeSharedKbAction}
					providerLabel={$i18n.t('Confluence')}
					on:provisioned={(e) => dispatch('provisioned', e.detail)}
					on:synced={() => dispatch('synced')}
					on:deleted={() => dispatch('deleted')}
				>
					<!-- OAuth: the owner is forced to whoever clicks Connect (their
					     per-user token is what the sync runs with), so there is
					     nothing to pick — just show connection state and a Connect
					     button. The service modes (basic/scoped) use the SharedKbSection
					     owner dropdown instead (showOwnerPick), so this is oauth-only. -->
					<svelte:fragment slot="owner">
						{#if CONFLUENCE_AUTH_MODE === 'oauth'}
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
					</svelte:fragment>

					<svelte:fragment slot="detail">
						<div class="truncate">
							{$i18n.t('Spaces')}: {sharedSpacesLabel}
						</div>
					</svelte:fragment>
				</SharedKbSection>
			</div>
		{/if}
	{/if}
</div>
