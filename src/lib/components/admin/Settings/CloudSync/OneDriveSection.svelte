<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { getOneDriveConfig, setOneDriveConfig } from '$lib/apis/configs';
	import Switch from '$lib/components/common/Switch.svelte';
	import SyncSettingsSection from './SyncSettingsSection.svelte';
	import type { OneDriveConfigResponse } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');

	// Provider config — owned by this section. The orchestrator calls
	// `load()`/`persist()` (instance bindings) and reads `enabled` for the card
	// header.
	export let enabled = false;

	let ENABLE_ONEDRIVE_INTEGRATION = false;
	let ENABLE_ONEDRIVE_SYNC = false;
	let ENABLE_ONEDRIVE_PERSONAL = true;
	let ENABLE_ONEDRIVE_BUSINESS = true;
	let ONEDRIVE_CLIENT_ID_PERSONAL = '';
	let ONEDRIVE_CLIENT_ID_BUSINESS = '';
	let ONEDRIVE_SHAREPOINT_URL = '';
	let ONEDRIVE_SHAREPOINT_TENANT_ID = '';
	let ONEDRIVE_SYNC_INTERVAL_MINUTES = 60;
	let ONEDRIVE_MAX_FILES_PER_SYNC: number | null = 500;

	// Mirror the enable flag out so the card header badge stays in sync.
	$: enabled = ENABLE_ONEDRIVE_INTEGRATION;

	// Autosave: notify the orchestrator whenever a persisted field changes. The
	// baseline is re-established on every apply() (load/save), and the very first
	// run only seeds it — so neither load nor save ever triggers a spurious save.
	export let onChange: (() => void) | null = null;
	let savedBaseline: string | null = null;
	$: changeSnapshot = JSON.stringify([
		ENABLE_ONEDRIVE_INTEGRATION,
		ENABLE_ONEDRIVE_SYNC,
		ENABLE_ONEDRIVE_PERSONAL,
		ENABLE_ONEDRIVE_BUSINESS,
		ONEDRIVE_CLIENT_ID_PERSONAL,
		ONEDRIVE_CLIENT_ID_BUSINESS,
		ONEDRIVE_SHAREPOINT_URL,
		ONEDRIVE_SHAREPOINT_TENANT_ID,
		ONEDRIVE_SYNC_INTERVAL_MINUTES,
		ONEDRIVE_MAX_FILES_PER_SYNC
	]);
	$: {
		if (savedBaseline === null) {
			savedBaseline = changeSnapshot;
		} else if (changeSnapshot !== savedBaseline) {
			savedBaseline = changeSnapshot;
			onChange?.();
		}
	}

	const apply = (config: OneDriveConfigResponse | null) => {
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
		// Re-baseline against the freshly-loaded/saved values so the snapshot
		// watcher treats them as the new "clean" state.
		savedBaseline = null;
	};

	export async function load() {
		apply(await getOneDriveConfig(localStorage.token));
	}

	// Persists this provider's config. Throws on failure so the orchestrator's
	// Save handler can surface it. Payload shape is identical to the monolith's.
	export async function persist() {
		const config = await setOneDriveConfig(localStorage.token, {
			ENABLE_ONEDRIVE_INTEGRATION,
			ENABLE_ONEDRIVE_SYNC,
			ENABLE_ONEDRIVE_PERSONAL,
			ENABLE_ONEDRIVE_BUSINESS,
			ONEDRIVE_CLIENT_ID_PERSONAL,
			ONEDRIVE_CLIENT_ID_BUSINESS,
			ONEDRIVE_SHAREPOINT_URL,
			ONEDRIVE_SHAREPOINT_TENANT_ID,
			ONEDRIVE_SYNC_INTERVAL_MINUTES,
			// Blank/null input → 0 = no per-sync file limit.
			ONEDRIVE_MAX_FILES_PER_SYNC: ONEDRIVE_MAX_FILES_PER_SYNC ?? 0
		});
		apply(config);
	}
</script>

<div class="space-y-3">
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

		<div class="pt-4">
			<SyncSettingsSection
				bind:backgroundEnabled={ENABLE_ONEDRIVE_SYNC}
				bind:intervalMinutes={ONEDRIVE_SYNC_INTERVAL_MINUTES}
				bind:maxPerSync={ONEDRIVE_MAX_FILES_PER_SYNC}
				itemNoun="files"
			/>
		</div>
	{/if}
</div>
