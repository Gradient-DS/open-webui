<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import type { Connection, Schedule, ScheduleAction } from '$lib/apis/cloudSync';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { CLOUD_PROVIDERS, pairSchedules, sourceStatus } from '../utils/cloudSync';
	import { syncErrorMessage } from './syncStatus';

	dayjs.extend(relativeTime);
	const i18n = getContext<Writable<I18n>>('i18n');
	const dispatch = createEventDispatcher<{
		action: { schedules: Schedule[]; action: ScheduleAction | 'delete' };
		reconnect: Connection;
	}>();
	export let schedules: Schedule[] = [];
	export let reconnectNeeded: Connection[] = [];
	export let syncStatusError = false;
	export let writeAccess = false;
	export let busy = false;
	export let isAdmin = false;

	$: rows = pairSchedules(schedules).map((pair) => ({ pair, ...sourceStatus(pair) }));
	const action = (targets: (Schedule | undefined)[], action: ScheduleAction | 'delete') =>
		dispatch('action', { schedules: targets.filter((item): item is Schedule => !!item), action });
</script>

<section
	class="mx-4 mb-3 rounded-xl border border-gray-200 p-3 dark:border-gray-700"
	aria-label={$i18n.t('Cloud Sync')}
>
	{#each reconnectNeeded as connection (connection.id)}
		<div
			class="mb-2 flex items-center justify-between gap-3 rounded-lg bg-amber-50 p-3 text-sm dark:bg-amber-950"
			role="status"
		>
			<span
				>{#if connection.last_error === 'owner_mismatch'}
					{$i18n.t(
						'The account you signed in with is not yours to connect; sign in with your own account.'
					)}
				{:else}
					{$i18n.t('Reconnect {{provider}} to resume syncing.', {
						provider: CLOUD_PROVIDERS[connection.source_kind]?.label ?? connection.source_kind
					})}
				{/if}</span
			>
			{#if writeAccess}
				<button
					class="font-medium underline"
					disabled={busy}
					on:click={() => dispatch('reconnect', connection)}>{$i18n.t('Reconnect')}</button
				>
			{/if}
		</div>
	{/each}
	{#if syncStatusError}
		<p role="alert" class="text-sm text-red-500">
			{$i18n.t('Failed to check background sync status')}
		</p>
	{/if}
	{#each rows as row (row.schedule.id)}
		<div class="flex flex-wrap items-center gap-3 border-b py-2 last:border-0 dark:border-gray-700">
			<span class="text-xs"
				>{CLOUD_PROVIDERS[row.schedule.source_kind]?.label ?? row.schedule.source_kind}
				· {row.schedule.scope.single_file ? $i18n.t('File') : $i18n.t('Folder')}</span
			>
			{#if row.subscriberCount > 1}
				<span class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Also in {{count}} other knowledge bases', {
						count: row.subscriberCount - 1
					})}
				</span>
			{/if}
			<div class="text-xs tabular-nums" role="status">
				{#if row.live}
					<span class="inline-flex items-center gap-1"
						><Spinner className="size-3" />
						{$i18n.t('Syncing… {{landed}} landed', { landed: row.landed })}</span
					>
				{:else if row.lastSynced}
					<span
						>{$i18n.t('Last synced {{time}} · {{landed}} documents', {
							time: dayjs(row.lastSynced).locale($i18n.language).fromNow(),
							landed: row.landed
						})}</span
					>
				{:else}
					<span>{$i18n.t('Not synced yet')}</span>
				{/if}
				{#if !row.live && (row.failed > 0 || row.errorCode)}
					<span class="text-amber-600">
						{#if row.failed > 0}
							· {#if row.tooLarge > 0}
								{$i18n.t('{{failed}} failed ({{tooLarge}} too large)', {
									failed: row.failed,
									tooLarge: row.tooLarge
								})}
							{:else}
								{$i18n.t('{{failed}} failed', { failed: row.failed })}
							{/if}
						{/if}
						{$i18n.t(syncErrorMessage(row.errorCode), {
							provider: CLOUD_PROVIDERS[row.schedule.source_kind]?.label ?? row.schedule.source_kind
						})}
					</span>
				{/if}
				{#if row.linkGrantsDropped > 0}
					<p class="text-gray-500 dark:text-gray-400">
						{$i18n.t('{{count}} link-only shares not mirrored', { count: row.linkGrantsDropped })}
					</p>
				{/if}
				{#if row.aclStatus}
					<p class="text-amber-600">
						{$i18n.t('Access sync: {{status}}', {
							status: row.aclStatus === 'partial' ? $i18n.t('Partly failed') : $i18n.t('Error')
						})}
					</p>
				{/if}
			</div>
			{#if row.expiry !== null && row.expiry <= 30}
				<span class="text-xs text-amber-600"
					>{$i18n.t('Provider credentials expire in {{count}} days. Contact your administrator.', {
						count: row.expiry
					})}</span
				>
			{/if}
			{#if writeAccess}
				<button
					class="text-xs underline"
					disabled={busy ||
						row.live ||
						!row.pair.content ||
						row.schedule.lifecycle !== 'enabled' ||
						row.schedule.connection.lifecycle !== 'enabled'}
					on:click={() => action([row.pair.content], 'run')}>{$i18n.t('Sync now')}</button
				>
				{#if row.live}
					<button
						class="text-xs underline"
						disabled={busy}
						on:click={() => action(row.liveSchedules, 'cancel')}>{$i18n.t('Cancel')}</button
					>
				{/if}
			{/if}
			{#if isAdmin}
				{#if row.schedule.lifecycle === 'enabled'}
					<button
						class="text-xs underline"
						disabled={busy}
						on:click={() => action([row.pair.content, row.pair.acl], 'suspend')}
						>{$i18n.t('Pause')}</button
					>
				{:else if row.schedule.lifecycle === 'suspended'}
					<button
						class="text-xs underline"
						disabled={busy || row.schedule.connection.lifecycle !== 'enabled'}
						on:click={() => action([row.pair.content, row.pair.acl], 'resume')}
						>{$i18n.t('Resume')}</button
					>
				{/if}
				<button
					class="text-xs underline"
					disabled={busy}
					on:click={() => action([row.pair.acl, row.pair.content], 'delete')}
					>{$i18n.t('Remove')}</button
				>
			{/if}
		</div>
	{/each}
</section>
