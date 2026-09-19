<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import type { Connection, Schedule, ScheduleAction } from '$lib/apis/cloudSync';
	import { CLOUD_PROVIDERS, pairSchedules } from '../utils/cloudSync';
	import { sourceState } from '../utils/sourceState';
	import SourceRow from './SourceRow.svelte';
	const i18n = getContext<Writable<I18n>>('i18n');
	const dispatch = createEventDispatcher<{
		action: { schedules: Schedule[]; action: ScheduleAction | 'delete' };
		reconnect: Connection;
	}>();
	export let schedules: Schedule[] = [];
	export let reconnectNeeded: Connection[] = [];
	export let finishingConnectionId: string | null = null;
	export let syncStatusError = false;
	export let writeAccess = false;
	export let busy = false;
	export let isAdmin = false;
	$: pairs = pairSchedules(schedules);
	$: rows = pairs.map((pair) => ({ pair, id: (pair.content ?? pair.acl!).id }));
	$: providers = [
		...new Set(
			schedules.map(
				(schedule) => CLOUD_PROVIDERS[schedule.source_kind]?.label ?? schedule.source_kind
			)
		)
	];
	$: keepSearchable = pairs.some((pair) =>
		['paused', 'error', 'partly_synced', 'needs_reconnect'].includes(
			sourceState(pair, (pair.content ?? pair.acl!).connection).state
		)
	);
</script>

<section
	class="mx-4 mb-3 rounded-xl border border-gray-200 p-3 dark:border-gray-700"
	aria-label={$i18n.t('Sources')}
>
	<h3 class="mb-1 text-sm font-medium">{$i18n.t('Sources')}</h3>
	{#if finishingConnectionId}
		<p role="status" class="mb-2 text-sm">{$i18n.t('Finishing the connection…')}</p>
	{/if}
	{#each reconnectNeeded.filter((connection) => connection.id !== finishingConnectionId && !schedules.some((schedule) => schedule.connection_id === connection.id)) as connection (connection.id)}
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

	{#each rows as row (row.id)}
		<SourceRow pair={row.pair} {writeAccess} {busy} {isAdmin} on:action on:reconnect />
	{/each}
	<footer class="mt-3 space-y-1 text-xs text-gray-500 dark:text-gray-400">
		{#each providers as provider}<p>
				{$i18n.t('You only see files you can open in {{provider}}.', {
					provider: $i18n.t(provider)
				})}
			</p>{/each}
		{#if keepSearchable}<p>{$i18n.t('Synced documents stay searchable.')}</p>{/if}
	</footer>
</section>
