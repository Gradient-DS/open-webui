<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import type { Connection, Schedule, ScheduleAction } from '$lib/apis/cloudSync';
	import Badge from '$lib/components/common/Badge.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import FolderOpen from '$lib/components/icons/FolderOpen.svelte';
	import { runIsLive, type SchedulePair } from '../utils/cloudSync';
	import { sourceState, type SourceState } from '../utils/sourceState';

	dayjs.extend(relativeTime);
	const i18n = getContext<Writable<I18n>>('i18n');
	const dispatch = createEventDispatcher<{
		action: { schedules: Schedule[]; action: ScheduleAction | 'delete' };
		reconnect: Connection;
	}>();
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
			label: 'Error',
			type: 'warning',
			definition: 'Error: the last sync failed; it will retry automatically'
		}
	};
	$: schedule = pair.content ?? pair.acl!;
	$: view = sourceState(pair, schedule.connection);
	$: badge = badges[view.state];
	$: targets = [pair.content, pair.acl].filter((item): item is Schedule => !!item);
	$: expiry = targets.flatMap((item) =>
		typeof item.provider_secret_days_to_expiry === 'number'
			? [item.provider_secret_days_to_expiry]
			: []
	);
	$: skipped = Math.max(view.skipped.failed, view.skipped.tooLarge) + view.skipped.linkOnly;
	$: primary = {
		sync_now: 'Sync now',
		reconnect: 'Reconnect',
		resume: 'Resume',
		request_access: 'Request access'
	};
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

<div class="flex flex-wrap items-start gap-3 border-b py-3 last:border-0 dark:border-gray-700">
	<div class="mt-1 shrink-0" aria-label={$i18n.t(view.provider)}>
		<svelte:component
			this={schedule.source_kind === 'onedrive'
				? OneDrive
				: schedule.source_kind === 'google_drive'
					? GoogleDrive
					: FolderOpen}
			className="size-5"
		/>
	</div>
	<div class="min-w-0 flex-1 basis-48 space-y-1">
		<div class="flex flex-wrap items-center gap-2">
			<span class="break-words text-sm font-medium">{schedule.label || $i18n.t(view.label)}</span>
			<Tooltip content={$i18n.t(badge.definition, { provider: $i18n.t(view.provider) })}>
				<button
					type="button"
					aria-label={$i18n.t(badge.definition, { provider: $i18n.t(view.provider) })}
					><Badge type={badge.type} content={$i18n.t(badge.label)} /></button
				>
			</Tooltip>
		</div>
		{#if view.path}<p class="break-all text-xs text-gray-500 dark:text-gray-400">
				{view.path}
			</p>{/if}
		<p class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t('Last synced {{time}} · {{count}} documents', {
				time: view.lastSyncedAt
					? dayjs(view.lastSyncedAt).locale($i18n.language).fromNow()
					: $i18n.t('Not synced yet'),
				count: view.documents
			})}
		</p>
		{#if view.nextDueAt}<p class="text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('Next check {{time}}', {
					time: dayjs(view.nextDueAt).locale($i18n.language).fromNow()
				})}
			</p>{/if}
		{#if view.otherKbs > 0}<p class="text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('Also in {{count}} other knowledge bases', { count: view.otherKbs })}
			</p>{/if}
		{#if skipped > 0}
			<details class="text-xs text-amber-700 dark:text-amber-300">
				<summary class="cursor-pointer">{$i18n.t('{{n}} files skipped', { n: skipped })}</summary>
				<ul class="mt-1 list-inside list-disc">
					<li>{$i18n.t('Too large: {{n}}', { n: view.skipped.tooLarge })}</li>
					<li>{$i18n.t('Link-only shares: {{n}}', { n: view.skipped.linkOnly })}</li>
					<li>
						{$i18n.t('Other: {{n}}', {
							n: Math.max(0, view.skipped.failed - view.skipped.tooLarge)
						})}
					</li>
				</ul>
			</details>
		{/if}
		{#if expiry.length && Math.min(...expiry) <= 30}<p class="text-xs text-amber-600">
				{$i18n.t('Provider credentials expire in {{count}} days. Contact your administrator.', {
					count: Math.min(...expiry)
				})}
			</p>{/if}
		{#if showAccessHelp}<p class="text-xs" role="status">
				{$i18n.t('Ask the knowledge base owner to restore your edit access, then resume syncing.')}
			</p>{/if}
	</div>
	<div class="flex items-center gap-2">
		{#if view.primary && (writeAccess || view.primary === 'request_access') && (view.primary !== 'resume' || isAdmin)}
			<button
				class="rounded-lg border px-3 py-1.5 text-xs disabled:opacity-50 dark:border-gray-700"
				disabled={busy}
				on:click={primaryAction}>{$i18n.t(primary[view.primary])}</button
			>
		{/if}
		{#if isAdmin || (writeAccess && view.state === 'syncing')}
			<Dropdown bind:show={showMenu} align="end">
				<button class="rounded-lg px-2 py-1" aria-label={$i18n.t('Source actions')} disabled={busy}
					>⋯</button
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
</div>
