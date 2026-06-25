<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { getGoogleDriveConfig, setGoogleDriveConfig } from '$lib/apis/configs';
	import Switch from '$lib/components/common/Switch.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import SyncSettingsSection from './SyncSettingsSection.svelte';
	import type { GoogleDriveConfigResponse } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');

	// Provider config — owned by this section. The orchestrator calls
	// `load()`/`persist()` (instance bindings) and reads `enabled` for the card
	// header.
	export let enabled = false;

	let ENABLE_GOOGLE_DRIVE_INTEGRATION = false;
	let ENABLE_GOOGLE_DRIVE_SYNC = false;
	let GOOGLE_DRIVE_CLIENT_ID = '';
	let GOOGLE_DRIVE_API_KEY = '';
	let GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES = 60;
	let GOOGLE_DRIVE_MAX_FILES_PER_SYNC: number | null = 500;

	// Mirror the enable flag out so the card header badge stays in sync.
	$: enabled = ENABLE_GOOGLE_DRIVE_INTEGRATION;

	// Autosave: notify the orchestrator whenever a persisted field changes. The
	// baseline is re-established on every apply() (load/save), and the very first
	// run only seeds it — so neither load nor save ever triggers a spurious save.
	export let onChange: (() => void) | null = null;
	let savedBaseline: string | null = null;
	$: changeSnapshot = JSON.stringify([
		ENABLE_GOOGLE_DRIVE_INTEGRATION,
		ENABLE_GOOGLE_DRIVE_SYNC,
		GOOGLE_DRIVE_CLIENT_ID,
		GOOGLE_DRIVE_API_KEY,
		GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES,
		GOOGLE_DRIVE_MAX_FILES_PER_SYNC
	]);
	$: {
		if (savedBaseline === null) {
			savedBaseline = changeSnapshot;
		} else if (changeSnapshot !== savedBaseline) {
			savedBaseline = changeSnapshot;
			onChange?.();
		}
	}

	const apply = (config: GoogleDriveConfigResponse | null) => {
		if (!config) return;
		ENABLE_GOOGLE_DRIVE_INTEGRATION = config.ENABLE_GOOGLE_DRIVE_INTEGRATION ?? false;
		ENABLE_GOOGLE_DRIVE_SYNC = config.ENABLE_GOOGLE_DRIVE_SYNC ?? false;
		GOOGLE_DRIVE_CLIENT_ID = config.GOOGLE_DRIVE_CLIENT_ID ?? '';
		GOOGLE_DRIVE_API_KEY = config.GOOGLE_DRIVE_API_KEY ?? '';
		GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES = config.GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES ?? 60;
		GOOGLE_DRIVE_MAX_FILES_PER_SYNC = config.GOOGLE_DRIVE_MAX_FILES_PER_SYNC ?? 0;
		// Re-baseline against the freshly-loaded/saved values so the snapshot
		// watcher treats them as the new "clean" state.
		savedBaseline = null;
	};

	export async function load() {
		apply(await getGoogleDriveConfig(localStorage.token));
	}

	// Persists this provider's config. Throws on failure so the orchestrator's
	// Save handler can surface it. Payload shape is identical to the monolith's.
	export async function persist() {
		const config = await setGoogleDriveConfig(localStorage.token, {
			ENABLE_GOOGLE_DRIVE_INTEGRATION,
			ENABLE_GOOGLE_DRIVE_SYNC,
			GOOGLE_DRIVE_CLIENT_ID,
			GOOGLE_DRIVE_API_KEY,
			GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES,
			// Blank/null input → 0 = no per-sync file limit.
			GOOGLE_DRIVE_MAX_FILES_PER_SYNC: GOOGLE_DRIVE_MAX_FILES_PER_SYNC ?? 0
		});
		apply(config);
	}
</script>

<div class="space-y-3">
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

		<div class="pt-4">
			<SyncSettingsSection
				bind:backgroundEnabled={ENABLE_GOOGLE_DRIVE_SYNC}
				bind:intervalMinutes={GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES}
				bind:maxPerSync={GOOGLE_DRIVE_MAX_FILES_PER_SYNC}
				itemNoun="files"
			/>
		</div>
	{/if}
</div>
