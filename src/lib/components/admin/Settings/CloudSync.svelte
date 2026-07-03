<script lang="ts">
	import { onMount, onDestroy, getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { getCloudSyncStatus } from '$lib/apis/configs';
	import { getAllUsers } from '$lib/apis/users';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';

	import ProviderCard from './CloudSync/ProviderCard.svelte';
	import ConfluenceSection from './CloudSync/ConfluenceSection.svelte';
	import GoogleDriveSection from './CloudSync/GoogleDriveSection.svelte';
	import OneDriveSection from './CloudSync/OneDriveSection.svelte';
	import type {
		ProviderDescriptor,
		CloudSyncStatusResponse,
		CloudSyncSection
	} from './CloudSync/types';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher();

	let loading = true;

	// Autosave status surfaced in the footer (replaces the old manual Save button).
	let saveState: 'idle' | 'saving' | 'saved' | 'error' = 'idle';
	let saveError = '';
	// Per-provider debounce timers + the "Saved" auto-clear timer.
	const autosaveTimers: Record<string, ReturnType<typeof setTimeout>> = {};
	let savedResetTimer: ReturnType<typeof setTimeout> | null = null;

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

	// Slug-keyed section state, populated generically inside the `{#each}` loop.
	// Adding a provider needs no orchestrator state here — only an icon+section
	// import, a descriptor entry and one `{#if}` branch.
	//
	// `sectionRefs` holds each mounted section's instance (bind:this) so the
	// orchestrator can drive its load()/persist(). `enabledBySlug` holds each
	// section's `enabled` flag (bind:enabled) for the card header badge — the
	// object-property bind reassigns the map, so the template read stays live.
	let sectionRefs: Record<string, CloudSyncSection> = {};
	let enabledBySlug: Record<string, boolean> = {};

	// Cross-provider status (KB count / file count / last sync / syncing),
	// keyed by provider slug. Drives the card header status lines.
	let status: CloudSyncStatusResponse = {};

	// Admin user list fetched once on mount and passed to child sections that
	// need an owner-selector dropdown (Confluence). Avoids each section
	// independently fetching the same endpoint on mount.
	let adminUsers: { id: string; name: string; email: string }[] = [];

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

	onDestroy(() => {
		stopStatusPolling();
		Object.values(autosaveTimers).forEach((t) => clearTimeout(t));
		if (savedResetTimer) clearTimeout(savedResetTimer);
	});

	onMount(async () => {
		// Child sections mount before the parent's onMount fires (bottom-up
		// mount order), so `sectionRefs` is already populated here.
		// Fetch the admin user list once here and pass it to sections that
		// need it via the `adminUsers` prop.
		try {
			const usersResponse = await getAllUsers(localStorage.token).catch(() => null);
			adminUsers = (
				(usersResponse?.users ?? []) as {
					id: string;
					name: string;
					email: string;
					role: string;
				}[]
			)
				.filter((u) => u.role === 'admin')
				.map((u) => ({ id: u.id, name: u.name, email: u.email }));
			await Promise.all([...Object.values(sectionRefs).map((s) => s?.load?.()), refreshStatus()]);
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
		await Promise.all(Object.values(sectionRefs).map((s) => s?.persist?.()));
	};

	// Briefly show "All changes saved", then fade back to idle.
	const markSaved = () => {
		saveState = 'saved';
		if (savedResetTimer) clearTimeout(savedResetTimer);
		savedResetTimer = setTimeout(() => {
			if (saveState === 'saved') saveState = 'idle';
		}, 2500);
	};

	// Run a persist (one section or all), driving the footer status. Errors —
	// including the Confluence switch-guards that throw — surface inline rather
	// than as a toast per keystroke. On success the parent refreshes the backend
	// config (integration-enabled flags feed the chat '+' menu) without a toast.
	const runPersist = async (fn: () => Promise<void>) => {
		saveState = 'saving';
		saveError = '';
		try {
			await fn();
			dispatch('save');
			markSaved();
		} catch (err) {
			saveState = 'error';
			saveError = `${err}`;
		}
	};

	const persistSection = (slug: string) => {
		const section = sectionRefs[slug];
		if (!section?.persist) return;
		runPersist(() => section.persist());
	};

	// Debounced autosave: a section reports a field change, we save just that
	// provider ~0.8s after the last edit so rapid typing collapses to one write.
	const scheduleAutosave = (slug: string) => {
		if (autosaveTimers[slug]) clearTimeout(autosaveTimers[slug]);
		autosaveTimers[slug] = setTimeout(() => persistSection(slug), 800);
	};

	// Enter inside a field (or any form submit) flushes a full save immediately.
	const saveAllNow = () => runPersist(persistAll);
</script>

<form class="flex flex-col h-full justify-between text-sm" on:submit|preventDefault={saveAllNow}>
	<div class="overflow-y-scroll scrollbar-hidden h-full pr-1.5 pt-2">
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
							bind:this={sectionRefs[descriptor.slug]}
							bind:enabled={enabledBySlug[descriptor.slug]}
							{adminUsers}
							beforeSharedKbAction={persistAll}
							onChange={() => scheduleAutosave(descriptor.slug)}
							on:provisioned={refreshStatus}
							on:synced={refreshStatus}
							on:deleted={refreshStatus}
						/>
					{:else if descriptor.slug === 'google_drive'}
						<GoogleDriveSection
							bind:this={sectionRefs[descriptor.slug]}
							bind:enabled={enabledBySlug[descriptor.slug]}
							onChange={() => scheduleAutosave(descriptor.slug)}
						/>
					{:else if descriptor.slug === 'onedrive'}
						<OneDriveSection
							bind:this={sectionRefs[descriptor.slug]}
							bind:enabled={enabledBySlug[descriptor.slug]}
							onChange={() => scheduleAutosave(descriptor.slug)}
						/>
					{/if}
				</ProviderCard>
			{/each}
		</div>
	</div>

	<!-- No Save button: every field autosaves. This footer just reflects status. -->
	<div class="flex justify-end items-center pt-3 text-sm min-h-[1.75rem]">
		{#if saveState === 'saving'}
			<span class="flex items-center gap-1.5 text-gray-500">
				<Spinner className="size-3" />
				{$i18n.t('Saving...')}
			</span>
		{:else if saveState === 'saved'}
			<span class="text-emerald-600 dark:text-emerald-500">{$i18n.t('All changes saved')}</span>
		{:else if saveState === 'error'}
			<span class="text-red-500">{saveError || $i18n.t('Save failed')}</span>
		{/if}
	</div>
</form>
