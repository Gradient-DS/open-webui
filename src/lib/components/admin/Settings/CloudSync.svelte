<script lang="ts">
	import { onMount, onDestroy, getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { getCloudSyncStatus } from '$lib/apis/configs';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';

	import ProviderCard from './CloudSync/ProviderCard.svelte';
	import ConfluenceSection from './CloudSync/ConfluenceSection.svelte';
	import GoogleDriveSection from './CloudSync/GoogleDriveSection.svelte';
	import OneDriveSection from './CloudSync/OneDriveSection.svelte';
	import type { ProviderDescriptor, CloudSyncStatusResponse } from './CloudSync/types';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher();

	let loading = true;
	let saving = false;

	// Descriptor-driven accordion: one card per provider. The matching section
	// component is rendered in the card body (bound below). `name` is resolved
	// through i18n at render time.
	const descriptors: ProviderDescriptor[] = [
		{
			slug: 'confluence',
			name: 'Confluence',
			icon: Confluence,
			hasTestConnection: true,
			supportsSharedKb: true,
			itemNoun: 'pages',
			authModes: ['oauth', 'basic']
		},
		{
			slug: 'google_drive',
			name: 'Google Drive',
			icon: GoogleDrive,
			hasTestConnection: false,
			supportsSharedKb: false,
			itemNoun: 'files'
		},
		{
			slug: 'onedrive',
			name: 'OneDrive',
			icon: OneDrive,
			hasTestConnection: false,
			supportsSharedKb: false,
			itemNoun: 'files'
		}
	];

	// Single-open accordion — only one card expanded at a time. '' = all closed.
	let expandedSlug = '';

	// Section component instance bindings — the orchestrator drives each
	// provider's load/persist through these. Each section also surfaces its
	// `enabled` flag for the card header badge.
	let confluenceSection: ConfluenceSection;
	let googleDriveSection: GoogleDriveSection;
	let oneDriveSection: OneDriveSection;
	let confluenceEnabled = false;
	let googleDriveEnabled = false;
	let oneDriveEnabled = false;

	// Cross-provider status (KB count / file count / last sync / syncing),
	// keyed by provider slug. Drives the card header status lines.
	let status: CloudSyncStatusResponse = {};

	$: enabledBySlug = {
		confluence: confluenceEnabled,
		google_drive: googleDriveEnabled,
		onedrive: oneDriveEnabled
	} as Record<string, boolean>;

	// ── Cross-provider status polling ──────────────────────────────────
	// Refresh on mount and whenever a shared-KB lifecycle event fires
	// (provisioned / synced / deleted). On top of that, poll continuously
	// while any provider reports `syncing` so the header status lines (KB /
	// file counts, last sync, syncing badge) update on their own — and stop
	// as soon as nothing is syncing.
	let statusPollTimer: ReturnType<typeof setTimeout> | null = null;

	$: anySyncing = Object.values(status).some((s) => s?.status === 'syncing' || s?.syncing);

	const stopStatusPolling = () => {
		if (statusPollTimer) {
			clearTimeout(statusPollTimer);
			statusPollTimer = null;
		}
	};

	const refreshStatus = async () => {
		try {
			status = (await getCloudSyncStatus(localStorage.token)) ?? {};
		} catch (err) {
			console.error(err);
		}
	};

	// One self-rescheduling poll loop. It keeps running only while something is
	// syncing and re-arms from `refreshStatus` results, so it naturally stops
	// when the backend reports idle.
	const pollStatus = async () => {
		await refreshStatus();
		statusPollTimer = anySyncing ? setTimeout(pollStatus, 5000) : null;
	};

	// Start the loop when syncing begins; the loop stops itself when idle.
	$: if (anySyncing && !statusPollTimer) {
		statusPollTimer = setTimeout(pollStatus, 5000);
	}

	onDestroy(stopStatusPolling);

	onMount(async () => {
		try {
			await Promise.all([
				confluenceSection?.load(),
				googleDriveSection?.load(),
				oneDriveSection?.load(),
				refreshStatus()
			]);
		} catch (err) {
			toast.error(`${err}`);
		}
		loading = false;
	});

	const onToggle = (slug: string, expanded: boolean) => {
		// Single-open: expanding one collapses the rest.
		expandedSlug = expanded ? slug : '';
	};

	// Persists every provider's config. Mirrors the monolith's `persistConfig()`
	// (which saved all three in one Promise.all). Passed to ConfluenceSection as
	// the shared-KB `beforeAction` so a provision/sync saves the whole form
	// first — matching the monolith — and reused by Save below.
	const persistAll = async () => {
		await Promise.all([
			confluenceSection?.persist(),
			googleDriveSection?.persist(),
			oneDriveSection?.persist()
		]);
	};

	const submitHandler = async () => {
		saving = true;
		try {
			await persistAll();
			dispatch('save');
		} catch (err) {
			toast.error(`${err}`);
		}
		saving = false;
	};
</script>

<form class="flex flex-col h-full justify-between text-sm" on:submit|preventDefault={submitHandler}>
	<div class="overflow-y-scroll scrollbar-hidden h-full pr-1.5">
		{#if loading}
			<div class="flex justify-center py-8">
				<Spinner />
			</div>
		{/if}

		<!-- Section components stay mounted even while collapsed (ProviderCard
		     hides its body rather than unmounting) so their instance bindings
		     (load/persist) are always available — the orchestrator calls every
		     section's load() on mount and persist() on save regardless of which
		     card is open. The whole list is hidden until the initial load
		     finishes. -->
		<div class="space-y-2.5 {loading ? 'hidden' : ''}">
			{#each descriptors as descriptor (descriptor.slug)}
				<ProviderCard
					name={$i18n.t(descriptor.name)}
					icon={descriptor.icon}
					enabled={enabledBySlug[descriptor.slug] ?? false}
					status={status[descriptor.slug] ?? null}
					expanded={expandedSlug === descriptor.slug}
					on:toggle={(e) => onToggle(descriptor.slug, e.detail.expanded)}
				>
					{#if descriptor.slug === 'confluence'}
						<ConfluenceSection
							bind:this={confluenceSection}
							bind:enabled={confluenceEnabled}
							beforeSharedKbAction={persistAll}
							on:provisioned={refreshStatus}
							on:synced={refreshStatus}
							on:deleted={refreshStatus}
						/>
					{:else if descriptor.slug === 'google_drive'}
						<GoogleDriveSection
							bind:this={googleDriveSection}
							bind:enabled={googleDriveEnabled}
						/>
					{:else if descriptor.slug === 'onedrive'}
						<OneDriveSection bind:this={oneDriveSection} bind:enabled={oneDriveEnabled} />
					{/if}
				</ProviderCard>
			{/each}
		</div>
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
