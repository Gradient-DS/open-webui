<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import {
		getSkippedItems,
		type SkippedItem,
		type Connection,
		type Schedule,
		type ScheduleAction
	} from '$lib/apis/cloudSync';
	import Badge from '$lib/components/common/Badge.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import { runIsLive, type SchedulePair } from '../utils/cloudSync';
	import {
		runProgress,
		sourceState,
		sourceTiming,
		skippedReason,
		type SourceState
	} from '../utils/sourceState';

	dayjs.extend(relativeTime);
	const i18n = getContext<Writable<I18n>>('i18n');
	const dispatch = createEventDispatcher<{
		action: { schedules: Schedule[]; action: ScheduleAction | 'delete' };
		reconnect: Connection;
	}>();
	// [Gradient] The inline sync chrome of one cloud source. The slots keep a
	// fixed order (action or live progress, skipped files, status badge, menu)
	// so the badge stays put when a sync starts: the primary button gives way
	// to the progress readout in the same place instead of vanishing.
	export let knowledgeId: string;
	export let pair: SchedulePair;
	export let writeAccess = false;
	export let busy = false;
	export let isAdmin = false;
	let showMenu = false;
	let showAccessHelp = false;
	const badges: Record<SourceState, { label: string; type: string; definition: string }> = {
		syncing: { label: 'Syncing', type: 'info', definition: 'Syncing: files show up as they land' },
		up_to_date: {
			label: 'Up to date',
			type: 'success',
			definition: 'Up to date: the last check found nothing new'
		},
		partly_synced: {
			label: 'Partly synced',
			type: 'warning',
			definition: 'Partly synced: some files were skipped, see details'
		},
		scheduled: {
			label: 'Scheduled',
			type: 'muted',
			definition: 'Scheduled: waiting for the first sync'
		},
		paused: {
			label: 'Paused',
			type: 'warning',
			definition: 'Paused: syncing is paused; synced files stay searchable'
		},
		needs_reconnect: {
			label: 'Needs reconnect',
			type: 'error',
			definition: 'Needs reconnect: your {{provider}} sign-in expired; synced files stay searchable'
		},
		needs_access: {
			label: 'Needs access',
			type: 'error',
			definition: 'Needs access: you can no longer edit this knowledge base'
		},
		error: {
			label: 'Failed',
			type: 'error',
			definition: 'Error: the last sync failed; it will retry automatically'
		}
	};
	const primary = {
		sync_now: 'Sync now',
		reconnect: 'Reconnect',
		resume: 'Resume',
		request_access: 'Request access'
	};
	$: schedule = pair.content ?? pair.acl!;
	$: view = sourceState(pair, schedule.connection);
	$: progress = pair.content ? runProgress(pair.content) : null;
	$: timing = sourceTiming(view, (time) => dayjs(time).locale($i18n.language).fromNow());
	$: badge =
		view.state === 'error' &&
		(schedule.document_count ?? schedule.last_run?.counts?.landed ?? 0) === 0 &&
		(schedule.last_run?.counts?.failed ?? 0) > 0
			? { ...badges.error, definition: 'Failed: no file could be synced; see details' }
			: badges[view.state];
	$: targets = [pair.content, pair.acl].filter((item): item is Schedule => !!item);
	$: expiry = targets.flatMap((item) =>
		typeof item.provider_secret_days_to_expiry === 'number'
			? [item.provider_secret_days_to_expiry]
			: []
	);
	$: details = [
		$i18n.t(badge.definition, { provider: $i18n.t(view.provider) }),
		$i18n.t(timing.lastSync.key, timing.lastSync.values),
		timing.nextCheck ? $i18n.t(timing.nextCheck.key, timing.nextCheck.values) : '',
		view.otherKbs > 0
			? $i18n.t('Also in {{count}} other knowledge bases', { count: view.otherKbs })
			: '',
		expiry.length && Math.min(...expiry) <= 30
			? $i18n.t('Provider credentials expire in {{count}} days. Contact your administrator.', {
					count: Math.min(...expiry)
				})
			: ''
	].filter(Boolean);
	$: skipped = view.skipped;
	let skippedOpen = false;
	let skippedItems: SkippedItem[] = [];
	let skippedError = false;
	let skippedLoading = false;
	let loadedRunKey = '';
	$: runKey = JSON.stringify([
		pair.content?.id,
		pair.content?.last_run?.id,
		pair.content?.last_run?.finished_at
	]);
	$: if (skippedOpen && runKey !== loadedRunKey) void loadSkipped(runKey, pair.content);
	async function loadSkipped(key: string, content: Schedule | undefined) {
		loadedRunKey = key;
		skippedLoading = true;
		skippedError = false;
		try {
			const items = content
				? await getSkippedItems(localStorage.token, knowledgeId, content.id)
				: [];
			if (key === loadedRunKey) skippedItems = items;
		} catch {
			if (key === loadedRunKey) skippedError = true;
		} finally {
			if (key === loadedRunKey) skippedLoading = false;
		}
	}
	const action = (schedules: Schedule[], action: ScheduleAction | 'delete') => {
		showMenu = false;
		dispatch('action', { schedules, action });
	};
	function primaryAction() {
		if (view.primary === 'reconnect') dispatch('reconnect', schedule.connection);
		else if (view.primary === 'resume') action(targets, 'resume');
		else if (view.primary === 'sync_now' && pair.content) action([pair.content], 'run');
		else if (view.primary === 'request_access') showAccessHelp = !showAccessHelp;
	}
</script>

<div class="flex shrink-0 items-center gap-1.5">
	{#if showAccessHelp}<span class="text-xs text-gray-500" role="status">
			{$i18n.t('Ask the knowledge base owner to restore your edit access, then resume syncing.')}
		</span>{/if}
	{#if view.state === 'syncing'}
		<span
			class="flex items-center gap-1 px-2 py-0.5 text-xs text-gray-500 dark:text-gray-400"
			role="status"
			aria-live="polite"
		>
			<Spinner className="size-3" />
			{#if progress?.total}
				{$i18n.t('{{done}} of {{total}} · {{percent}}%', {
					done: progress.done,
					total: progress.total,
					percent: Math.floor((100 * progress.done) / progress.total)
				})}
			{:else}
				{$i18n.t('{{count}} documents so far', { count: progress?.done ?? view.documents })}
			{/if}
		</span>
	{:else if view.primary && (writeAccess || view.primary === 'request_access') && (view.primary !== 'resume' || isAdmin)}
		<button
			type="button"
			class="rounded-lg border px-2 py-0.5 text-xs disabled:opacity-50 dark:border-gray-700"
			disabled={busy}
			on:click={primaryAction}>{$i18n.t(primary[view.primary])}</button
		>
	{/if}
	{#if skipped > 0}
		<Dropdown
			bind:show={skippedOpen}
			align="end"
			onOpenChange={(open) => {
				if (!open && skippedError) loadedRunKey = '';
			}}
		>
			<button type="button" class="text-xs text-amber-700 hover:underline dark:text-amber-300"
				>{$i18n.t('{{n}} files skipped', { n: skipped })}</button
			>
			<div slot="content">
				<DropdownMenu className="max-w-sm p-2 text-xs">
					{#if skippedLoading}<p role="status">{$i18n.t('Loading...')}</p>
					{:else if skippedError}<p role="alert">
							{$i18n.t('Failed to load skipped files. Reopen to try again.')}
						</p>
					{:else}<ul class="list-inside list-disc">
							{#each skippedItems as item}<li>
									{item.name || item.source_id} — {$i18n.t(skippedReason(item.code))}
								</li>{/each}
						</ul>
						<p class="mt-1 text-gray-500">
							{$i18n.t('Open the folder to see why each file was skipped.')}
						</p>{/if}
				</DropdownMenu>
			</div>
		</Dropdown>
	{/if}
	<Tooltip content={details.join('<br>')} className="flex">
		<button type="button" aria-label={details[0]}
			><Badge type={badge.type} content={$i18n.t(badge.label)} /></button
		>
	</Tooltip>
	{#if isAdmin || (writeAccess && view.state === 'syncing')}
		<Dropdown bind:show={showMenu} align="end">
			<button
				type="button"
				class="rounded-lg px-1.5 py-0.5 text-xs"
				aria-label={$i18n.t('Source actions')}
				disabled={busy}>⋯</button
			>
			<div slot="content">
				<DropdownMenu>
					{#if writeAccess && view.state === 'syncing'}<button
							disabled={busy}
							on:click={() =>
								action(
									targets.filter((item) => runIsLive(item.last_run)),
									'cancel'
								)}>{$i18n.t('Cancel')}</button
						>{/if}
					{#if isAdmin}
						{#if schedule.lifecycle === 'enabled'}<button
								disabled={busy}
								on:click={() => action(targets, 'suspend')}>{$i18n.t('Pause')}</button
							>
						{:else if schedule.lifecycle === 'suspended'}<button
								disabled={busy || schedule.connection.lifecycle !== 'enabled'}
								on:click={() => action(targets, 'resume')}>{$i18n.t('Resume')}</button
							>{/if}
						<button disabled={busy} on:click={() => action([...targets].reverse(), 'delete')}
							>{$i18n.t('Remove')}</button
						>
					{/if}
				</DropdownMenu>
			</div>
		</Dropdown>
	{/if}
</div>
