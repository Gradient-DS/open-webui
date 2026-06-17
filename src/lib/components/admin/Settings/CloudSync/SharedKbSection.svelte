<script lang="ts">
	import { getContext, createEventDispatcher, onDestroy } from 'svelte';
	import type { ComponentType, SvelteComponent } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import type { ItemNoun, SharedKbApi, SharedKbStatusLike } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher<{
		provisioned: { status: SharedKbStatusLike };
		deleted: void;
		synced: void;
	}>();

	// Generalised shared-KB management block. Ports the Confluence shared-KB
	// behaviour from the CloudSync monolith — provision / re-provision /
	// sync-now / delete, status badges, progress %, optional owner dropdown,
	// and 2.5s polling while a sync runs — with every provider-specific
	// concern injected via props so TOPdesk (or any future shared-KB
	// provider) reuses it unchanged.

	// ── Injected provider API ──────────────────────────────────────────
	// `provision` receives the generic payload built below; the caller's
	// client adapts it (e.g. Confluence maps items → spaces).
	export let api: SharedKbApi<SharedKbStatusLike, { items: unknown[]; ownerUserId: string | null }>;

	// Item-picker modal component. Opened to select what the shared KB syncs.
	// It must accept `bind:show`, `currentItems`, optional `title` /
	// `confirmLabel`, and dispatch `select` with `{ items }`.
	export let pickerComponent: ComponentType<SvelteComponent>;
	// Extra props forwarded verbatim to the picker (e.g. `pagesOnly`).
	export let pickerProps: Record<string, unknown> = {};
	// Items currently opted into the shared KB — pre-fills the picker.
	export let currentItems: unknown[] = [];

	// ── Owner pick (service-account / basic auth) ──────────────────────
	export let showOwnerPick = false;
	// Two-way bound selected owner id ('' = system-owned).
	export let ownerId = '';
	// Admin candidates for ownership: { id, name, email }.
	export let owners: { id: string; name: string; email: string }[] = [];

	// ── Labels / behaviour ─────────────────────────────────────────────
	// Drives the picker modal title ("pages" → "Pages to sync", etc.).
	export let itemNoun: ItemNoun = 'items';
	// Current provider shared-KB status (owned by the parent — passed in and
	// kept fresh via the `provisioned` event + polling callbacks below).
	export let status: SharedKbStatusLike | null = null;

	// Optional async hook run before opening the picker and before
	// provisioning — the orchestrator wires this to persist the config form
	// so the backend reads fresh credentials/owner. Returning a rejected
	// promise aborts the action.
	export let beforeAction: (() => Promise<void>) | null = null;

	// Provider label for the confirm-delete copy (already translated).
	export let providerLabel = '';

	let showPicker = false;
	let showDeleteConfirm = false;
	let provisioning = false;
	let syncingShared = false;
	let deletingShared = false;

	// ── Status polling ──────────────────────────────────────────────────
	// While a sync runs, re-poll status so file count / last-sync refresh on
	// their own. Polls a minimum number of times even when idle — a freshly
	// triggered background sync may not have flipped to 'syncing' yet, and a
	// no-op sync finishes between polls. `syncingShared` is held true for the
	// whole window so the Sync button shows progress.
	let statusPollTimer: ReturnType<typeof setTimeout> | null = null;
	let statusPollDeadline = 0;
	let statusPollCount = 0;

	const refreshStatus = async () => {
		try {
			status = await api.getStatus();
		} catch (err) {
			console.error(err);
		}
	};

	const pollStatus = async () => {
		statusPollCount += 1;
		await refreshStatus();
		const stillSyncing = status?.status === 'syncing';
		if ((stillSyncing || statusPollCount < 5) && Date.now() < statusPollDeadline) {
			statusPollTimer = setTimeout(pollStatus, 2500);
		} else {
			statusPollTimer = null;
			syncingShared = false;
		}
	};

	const stopStatusPolling = () => {
		if (statusPollTimer) {
			clearTimeout(statusPollTimer);
			statusPollTimer = null;
		}
	};

	const startStatusPolling = () => {
		stopStatusPolling();
		statusPollCount = 0;
		statusPollDeadline = Date.now() + 5 * 60 * 1000;
		statusPollTimer = setTimeout(pollStatus, 2500);
	};

	onDestroy(stopStatusPolling);

	// Restore the monolith's reload-during-sync resume behaviour: if the page is
	// reloaded while a shared sync is in flight, the parent's `load()` populates
	// `status` asynchronously with `status: 'syncing'`. The cross-provider 5s poll
	// only feeds the card header, so without this the progress % would freeze.
	// A one-shot reactive guard kicks off the poll loop once the first non-null
	// status lands (a plain `onMount` check would race the async `load()`).
	let initialResumeChecked = false;
	$: if (!initialResumeChecked && status) {
		initialResumeChecked = true;
		if (status.status === 'syncing' && !statusPollTimer) {
			syncingShared = true;
			startStatusPolling();
		}
	}

	// Live sync state — derived from the backend status too (not just the
	// local flag) so progress shows after navigating away and back while a
	// sync runs. `syncProgress` is null until the worker reports a total.
	$: isSharedSyncing = syncingShared || status?.status === 'syncing';
	$: syncProgress =
		(status?.progress_total ?? 0) > 0
			? Math.min(
					100,
					Math.round(((status?.progress_current ?? 0) / (status?.progress_total ?? 1)) * 100)
				)
			: null;

	// Suspended state — the KB hit a terminal credential/access failure. The
	// "Sync now" button doubles as the recovery path: it re-checks access and
	// resumes if the credential is restored, so we relabel it (not disable it)
	// and explain what suspension means.
	$: isSuspended = status?.suspended_at != null || status?.status === 'suspended';
	// Human-readable reason for the suspended badge explanation.
	$: suspendedReasonText =
		status?.suspended_reason === 'service_credential_invalid'
			? $i18n.t('The service credential is no longer valid.')
			: status?.suspended_reason === 'service_credential_missing'
				? $i18n.t('No service credential is configured.')
				: status?.suspended_reason === 'owner_access_lost'
					? $i18n.t('The owner no longer has access to the source.')
					: $i18n.t('Access to the source could not be verified.');

	// Picker modal title — noun-aware so the same component reads naturally
	// for every provider ("Pages to sync" / "Files to sync" / "Items to sync").
	$: pickerTitle =
		itemNoun === 'pages'
			? $i18n.t('Pages to sync')
			: itemNoun === 'files'
				? $i18n.t('Files to sync')
				: $i18n.t('Items to sync');

	// Opens the picker. Runs `beforeAction` first (the picker may call a
	// provider endpoint that reads the saved config).
	const openPicker = async () => {
		provisioning = true;
		try {
			if (beforeAction) await beforeAction();
			showPicker = true;
		} catch (err) {
			console.error(err);
			toast.error($i18n.t('Could not save the configuration.'));
		}
		provisioning = false;
	};

	// Picker confirm → provision. `beforeAction` persists the form so the
	// provision endpoint reads the just-entered owner / credentials.
	const onPickerConfirm = async (e: CustomEvent<{ items: unknown[] }>) => {
		provisioning = true;
		try {
			if (beforeAction) await beforeAction();
			const ownerForProvision = showOwnerPick ? ownerId : null;
			status = await api.provision({ items: e.detail.items, ownerUserId: ownerForProvision });
			toast.success($i18n.t('Shared knowledge base provisioned.'));
			dispatch('provisioned', { status });
			// Auto-start the first sync: the admin just chose what to sync, so kick
			// it off immediately rather than requiring a separate "Sync now" click.
			// syncHandler swallows its own errors (toasts + clears state), so a sync
			// failure won't mask the successful provision above.
			await syncHandler();
		} catch (err) {
			console.error(err);
			toast.error($i18n.t('Could not provision the shared knowledge base.'));
		}
		provisioning = false;
	};

	const syncHandler = async () => {
		syncingShared = true;
		try {
			await api.sync();
			toast.success($i18n.t('Shared sync started.'));
			await refreshStatus();
			// startStatusPolling holds `syncingShared` true and re-polls until
			// the sync settles — it clears `syncingShared` itself, so the button
			// keeps showing progress and the status refreshes on its own.
			startStatusPolling();
			dispatch('synced');
		} catch (err) {
			console.error(err);
			toast.error($i18n.t('Could not start synchronization.'));
			syncingShared = false;
		}
	};

	const deleteHandler = async () => {
		deletingShared = true;
		try {
			await api.remove();
			toast.success($i18n.t('Shared knowledge base deleted.'));
			await refreshStatus();
			dispatch('deleted');
		} catch (err) {
			console.error(err);
			toast.error($i18n.t('Could not delete the shared knowledge base.'));
		}
		deletingShared = false;
	};
</script>

<div class="space-y-3">
	<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
		{$i18n.t('Shared knowledge base')}
	</div>

	{#if showOwnerPick}
		<div>
			<div class="mb-1 text-xs text-gray-500">
				{$i18n.t('Shared knowledge base owner')}
			</div>
			<select
				class="w-full text-sm bg-transparent outline-hidden rounded-sm py-1"
				bind:value={ownerId}
			>
				<option value="">{$i18n.t('No owner (system)')}</option>
				{#each owners as owner}
					<option value={owner.id}>{owner.name} ({owner.email})</option>
				{/each}
			</select>
			<div class="mt-1 text-xs text-gray-500">
				{$i18n.t('Optional — pick an admin to own the knowledge base, or leave it system-owned.')}
			</div>
		</div>
	{:else}
		<!-- Provider-supplied auth/connection controls (e.g. Confluence's
		     "Connect account" button) render here via the named slot. -->
		<slot name="owner" />
	{/if}

	<div class="rounded-lg bg-gray-50 dark:bg-gray-850 p-3 space-y-2">
		<div class="flex items-center justify-between">
			<div class="font-medium">{$i18n.t('Shared knowledge base')}</div>
			{#if status?.provisioned}
				{#if status?.suspended_at}
					<Badge type="warning" content={$i18n.t('Suspended')} />
				{:else}
					<Badge type="success" content={$i18n.t('Provisioned')} />
				{/if}
			{:else}
				<Badge type="muted" content={$i18n.t('Not provisioned')} />
			{/if}
		</div>

		{#if status?.provisioned}
			<div class="text-xs text-gray-500 space-y-0.5">
				<div>{$i18n.t('Files synced')}: {status?.file_count ?? 0}</div>
				<div>
					{$i18n.t('Last sync')}:
					{status?.last_sync_at
						? new Date(status.last_sync_at * 1000).toLocaleString()
						: $i18n.t('Never')}
				</div>
				<div>{$i18n.t('Status')}: {status?.status ?? 'idle'}</div>
				<!-- Provider-specific detail (e.g. Confluence "Spaces: ...") -->
				<slot name="detail" />
			</div>
		{/if}

		{#if status?.provisioned && isSuspended}
			<!-- Suspended explanation: why it happened, that it will NOT be
			     auto-deleted (managed shared KB), and how to recover. -->
			<div
				class="rounded-md bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-200 text-xs p-2 space-y-0.5"
			>
				<div>{suspendedReasonText}</div>
				<div>
					{$i18n.t(
						'This shared knowledge base is not auto-deleted — its files are kept. Use Retry sync to resume once access is restored.'
					)}
				</div>
			</div>
		{/if}

		<div class="flex gap-2 pt-1 items-center">
			<button
				type="button"
				class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
				on:click={openPicker}
				disabled={provisioning}
			>
				{status?.provisioned ? $i18n.t('Re-provision') : $i18n.t('Provision')}
				{#if provisioning}
					<Spinner className="size-3" />
				{/if}
			</button>
			{#if status?.provisioned}
				<button
					type="button"
					class="px-3 py-1.5 h-8 text-sm rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 flex items-center justify-center gap-1.5 min-w-40 disabled:opacity-50 disabled:cursor-not-allowed"
					on:click={syncHandler}
					disabled={isSharedSyncing}
					title={isSuspended
						? $i18n.t(
								'Re-checks access and resumes syncing if the credential or access has been restored.'
							)
						: ''}
				>
					{#if isSharedSyncing}
						{#if syncProgress !== null}{syncProgress}%{/if}
						<Spinner className="size-3" />
					{:else if isSuspended}
						{$i18n.t('Retry sync')}
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

<!-- Item picker — same tree explorer the per-user picker uses, wired here to
     pre-select items already opted into the shared KB and hand the result to
     `api.provision`. -->
<svelte:component
	this={pickerComponent}
	bind:show={showPicker}
	title={pickerTitle}
	confirmLabel={$i18n.t('Provision')}
	{currentItems}
	{...pickerProps}
	on:select={onPickerConfirm}
/>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete shared knowledge base')}
	message={$i18n.t(
		'The shared {{provider}} knowledge base and all its synced files will be removed. This cannot be undone.',
		{ provider: providerLabel }
	)}
	confirmLabel={$i18n.t('Delete')}
	on:confirm={deleteHandler}
/>
