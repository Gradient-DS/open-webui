<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { getTopdeskConfig, setTopdeskConfig } from '$lib/apis/configs';
	import {
		testTopdeskConnection,
		getTopdeskSharedKbStatus,
		provisionTopdeskSharedKb,
		syncTopdeskSharedKb,
		deleteTopdeskSharedKb,
		type TopdeskSharedKbStatus,
		type TopdeskKbItem
	} from '$lib/apis/topdesk';
	import { getAllUsers } from '$lib/apis/users';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import SyncSettingsSection from './SyncSettingsSection.svelte';
	import SharedKbSection from './SharedKbSection.svelte';
	import TopdeskPickerModal from './TopdeskPickerModal.svelte';
	import { connectionErrorMessage } from './errors';
	import type { TopdeskConfigResponse, SharedKbApi, SharedKbStatusLike } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');
	// Re-emit the shared-KB lifecycle events so the orchestrator can refresh the
	// cross-provider status line.
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
	// providers' config; standalone it defaults to persisting just this section.
	export let beforeSharedKbAction: (() => Promise<void>) | null = null;

	let ENABLE_TOPDESK_INTEGRATION = false;
	let ENABLE_TOPDESK_SYNC = false;
	let TOPDESK_URL = '';
	let TOPDESK_USERNAME = '';
	// The application password round-trips through the form like the upstream
	// Connections (API key) field — visible to the admin masked behind a reveal
	// toggle via SensitiveInput, sent back verbatim on save. It is the operator
	// credential and feeds either auth-header form (Basic with the operator login
	// name when set, TOKEN id="..." otherwise).
	let appPassword = '';
	let TOPDESK_SYNC_INTERVAL_MINUTES = 60;
	let TOPDESK_MAX_ITEMS_PER_SYNC: number | null = 500;
	// Which knowledge items to sync into the shared KB: 'ssp' (Self-Service Portal
	// visible, default) | 'public' (public items only) | 'all' (every readable item).
	let TOPDESK_SYNC_SCOPE = 'ssp';
	let testingConnection = false;

	// Mirror the enable flag out so the card header badge stays in sync.
	$: enabled = ENABLE_TOPDESK_INTEGRATION;

	// Autosave: notify the orchestrator whenever a persisted field changes. The
	// baseline is re-established on every applyTopdeskConfig() (load/save), and the
	// first run only seeds it — so neither load nor save triggers a spurious save.
	export let onChange: (() => void) | null = null;
	let savedBaseline: string | null = null;
	$: changeSnapshot = JSON.stringify([
		ENABLE_TOPDESK_INTEGRATION,
		ENABLE_TOPDESK_SYNC,
		TOPDESK_URL,
		TOPDESK_USERNAME,
		appPassword,
		TOPDESK_SYNC_INTERVAL_MINUTES,
		TOPDESK_MAX_ITEMS_PER_SYNC,
		TOPDESK_SYNC_SCOPE
	]);
	$: {
		if (savedBaseline === null) {
			savedBaseline = changeSnapshot;
		} else if (changeSnapshot !== savedBaseline) {
			savedBaseline = changeSnapshot;
			onChange?.();
		}
	}

	// Shared-KB owner pick: a transient form field, not a persisted config. On
	// provision it becomes ``kb.user_id``. Seeded from the KB row's owner on
	// initial status load so the dropdown reflects the existing owner when
	// re-provisioning.
	let sharedKbOwnerId = '';
	let sharedKbOwnerInitialized = false;
	let adminUsers: { id: string; name: string; email: string }[] = [];
	let sharedKbStatus: TopdeskSharedKbStatus | null = null;

	const applyTopdeskConfig = (config: TopdeskConfigResponse | null) => {
		if (!config) return;
		ENABLE_TOPDESK_INTEGRATION = config.ENABLE_TOPDESK_INTEGRATION ?? false;
		ENABLE_TOPDESK_SYNC = config.ENABLE_TOPDESK_SYNC ?? false;
		TOPDESK_URL = config.TOPDESK_URL ?? '';
		TOPDESK_USERNAME = config.TOPDESK_USERNAME ?? '';
		appPassword = config.TOPDESK_APP_PASSWORD ?? '';
		TOPDESK_SYNC_INTERVAL_MINUTES = config.TOPDESK_SYNC_INTERVAL_MINUTES ?? 60;
		TOPDESK_MAX_ITEMS_PER_SYNC = config.TOPDESK_MAX_ITEMS_PER_SYNC ?? 0;
		TOPDESK_SYNC_SCOPE = config.TOPDESK_SYNC_SCOPE ?? 'ssp';
		// Re-baseline against the freshly-loaded/saved values so the snapshot
		// watcher treats them as the new "clean" state.
		savedBaseline = null;
	};

	export async function load() {
		const [config, users, shared] = await Promise.all([
			getTopdeskConfig(localStorage.token),
			getAllUsers(localStorage.token).catch(() => null),
			getTopdeskSharedKbStatus(localStorage.token).catch(() => null)
		]);
		applyTopdeskConfig(config);
		// Owner dropdown is limited to admins — they are the only valid owners of
		// a shared, org-wide knowledge base.
		adminUsers = (
			(users?.users ?? []) as {
				id: string;
				name: string;
				email: string;
				role: string;
			}[]
		)
			.filter((u) => u.role === 'admin')
			.map((u) => ({ id: u.id, name: u.name, email: u.email }));
		sharedKbStatus = shared;
		// Seed the owner dropdown from the KB row's owner on first load only;
		// subsequent reloads leave the admin's in-progress pick alone.
		if (!sharedKbOwnerInitialized) {
			sharedKbOwnerId = sharedKbStatus?.owner_id ?? '';
			sharedKbOwnerInitialized = true;
		}
	}

	// Persists this provider's config. Throws on failure so callers (the
	// orchestrator's Save, the shared-KB `beforeAction`) can decide whether to
	// continue.
	export async function persist() {
		const config = await setTopdeskConfig(localStorage.token, {
			ENABLE_TOPDESK_INTEGRATION,
			ENABLE_TOPDESK_SYNC,
			TOPDESK_URL,
			TOPDESK_USERNAME,
			TOPDESK_APP_PASSWORD: appPassword,
			TOPDESK_SYNC_INTERVAL_MINUTES,
			// Blank/null input → 0 = no per-sync item limit.
			TOPDESK_MAX_ITEMS_PER_SYNC: TOPDESK_MAX_ITEMS_PER_SYNC ?? 0,
			TOPDESK_SYNC_SCOPE
		});
		applyTopdeskConfig(config);
	}

	// ── Shared-KB API adapter ───────────────────────────────────────────
	// Maps the generic SharedKbSection contract onto the TOPdesk client. The
	// picker emits `TopdeskKbItem[]`, which `provisionTopdeskSharedKb` takes
	// directly.
	const sharedKbApi: SharedKbApi<
		TopdeskSharedKbStatus,
		{ items: unknown[]; ownerUserId: string | null }
	> = {
		getStatus: () => getTopdeskSharedKbStatus(localStorage.token),
		provision: ({ items, ownerUserId }) =>
			provisionTopdeskSharedKb(localStorage.token, items as TopdeskKbItem[], ownerUserId),
		sync: () => syncTopdeskSharedKb(localStorage.token),
		remove: () => deleteTopdeskSharedKb(localStorage.token)
	};

	// Persist the form before the picker opens / provisioning runs so the
	// shared-KB endpoints read the just-entered owner / credentials rather than
	// stale config. Delegates to the injected hook (orchestrator persists all
	// providers); falls back to persisting just this section.
	const runBeforeSharedKbAction = async () => {
		if (beforeSharedKbAction) {
			await beforeSharedKbAction();
		} else {
			await persist();
		}
	};

	// Existing selection → picker `currentItems` so re-provisioning starts with
	// the current set ticked.
	$: currentSharedItems = (sharedKbStatus?.items ?? []) as TopdeskKbItem[];

	// "Items: ..." detail line for the shared-KB block.
	$: sharedItemsLabel =
		(sharedKbStatus?.items ?? [])
			.map((it) => it.name ?? it.item_id ?? '')
			.filter((n) => n)
			.join(', ') || $i18n.t('None');

	// Probe the service credential currently in the form. A blank
	// application-password field falls back server-side to the saved credential.
	// Failures are reported with a fully-localized message derived from the
	// backend `reason` code (the English `detail` is logged for debugging only).
	const testConnection = async () => {
		testingConnection = true;
		try {
			const result = await testTopdeskConnection(localStorage.token, {
				url: TOPDESK_URL,
				username: TOPDESK_USERNAME,
				app_password: appPassword
			});
			if (result.ok) {
				toast.success($i18n.t('TOPdesk connection successful.'));
			} else {
				console.error('TOPdesk connection failed:', result.reason, result.detail);
				toast.error(
					$i18n.t('TOPdesk connection failed: {{error}}', {
						error: connectionErrorMessage($i18n, result.reason, result.detail)
					})
				);
			}
		} catch (err) {
			console.error(err);
			toast.error(
				$i18n.t('TOPdesk connection failed: {{error}}', {
					error: connectionErrorMessage($i18n, 'unreachable')
				})
			);
		}
		testingConnection = false;
	};
</script>

<div class="space-y-3">
	<div class="text-xs text-gray-500">
		{$i18n.t('Configure TOPdesk as a knowledge base sync source.')}
	</div>

	<div class="flex justify-between items-center">
		<div class="font-medium">{$i18n.t('Enable TOPdesk integration')}</div>
		<Switch bind:state={ENABLE_TOPDESK_INTEGRATION} />
	</div>

	{#if ENABLE_TOPDESK_INTEGRATION}
		<!-- TOPdesk has a single service-account auth mode (no OAuth, no per-user
		     sign-in, no sync-mode choice). One read-only shared KB is pre-synced
		     from selected TOPdesk knowledge items and served to every user. -->
		<div class="text-xs text-gray-500">
			{$i18n.t('Service account · pre-synced shared knowledge base')}
		</div>

		<div class="space-y-3 pt-2">
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
				{$i18n.t('Service account')}
			</div>

			<div>
				<div class="mb-1 text-xs text-gray-500">{$i18n.t('TOPdesk URL')}</div>
				<input
					class="w-full text-sm bg-transparent outline-hidden"
					type="text"
					bind:value={TOPDESK_URL}
					autocomplete="off"
					placeholder="https://your-domain.topdesk.net"
				/>
			</div>

			<div>
				<div class="mb-1 text-xs text-gray-500">{$i18n.t('Login name')}</div>
				<input
					class="w-full text-sm bg-transparent outline-hidden"
					type="text"
					bind:value={TOPDESK_USERNAME}
					autocomplete="off"
				/>
				<div class="mt-1 text-xs text-gray-500">
					{$i18n.t('The login name of the TOPdesk operator (API account).')}
				</div>
			</div>

			<div>
				<div class="mb-1 text-xs text-gray-500">{$i18n.t('Application password')}</div>
				<div class="flex gap-2">
					<SensitiveInput bind:value={appPassword} required={false} autocomplete="new-password" />
				</div>
				<div class="mt-1 text-xs text-gray-500">
					{$i18n.t(
						"The application password created in TOPdesk under the operator's User menu → Application passwords."
					)}
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

		<div class="pt-4">
			<SyncSettingsSection
				bind:backgroundEnabled={ENABLE_TOPDESK_SYNC}
				bind:intervalMinutes={TOPDESK_SYNC_INTERVAL_MINUTES}
				bind:maxPerSync={TOPDESK_MAX_ITEMS_PER_SYNC}
				itemNoun="items"
			/>
		</div>

		<div class="pt-4">
			<div class="mb-1 text-xs text-gray-500">{$i18n.t('Knowledge items to sync')}</div>
			<select
				class="w-full text-sm bg-transparent outline-hidden dark:text-gray-100"
				bind:value={TOPDESK_SYNC_SCOPE}
			>
				<option value="ssp" class="dark:bg-gray-900"
					>{$i18n.t('Visible in Self-Service Portal')}</option
				>
				<option value="public" class="dark:bg-gray-900">{$i18n.t('Public only')}</option>
				<option value="all" class="dark:bg-gray-900">{$i18n.t('All readable')}</option>
			</select>
			<div class="mt-1 text-xs text-gray-500">
				{$i18n.t('Which TOPdesk knowledge items are pulled into the shared knowledge base.')}
			</div>
		</div>

		<div class="pt-4">
			<SharedKbSection
				api={sharedKbApi}
				pickerComponent={TopdeskPickerModal}
				currentItems={currentSharedItems}
				showOwnerPick={true}
				bind:ownerId={sharedKbOwnerId}
				owners={adminUsers}
				itemNoun="items"
				bind:status={sharedKbStatus}
				beforeAction={runBeforeSharedKbAction}
				providerLabel={$i18n.t('TOPdesk')}
				on:provisioned={(e) => dispatch('provisioned', e.detail)}
				on:synced={() => dispatch('synced')}
				on:deleted={() => dispatch('deleted')}
			>
				<svelte:fragment slot="detail">
					<div class="truncate">
						{$i18n.t('Items')}: {sharedItemsLabel}
					</div>
				</svelte:fragment>
			</SharedKbSection>
		</div>
	{/if}
</div>
