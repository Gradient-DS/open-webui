<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import type { SyncRun } from '$lib/apis/cloudSync';
	import { runCounts } from '../utils/cloudSync';

	const i18n = getContext<Writable<I18n>>('i18n');
	export let run: SyncRun;

	$: statusLabel =
		{
			queued: $i18n.t('Queued'),
			running: $i18n.t('In progress'),
			completed: $i18n.t('Done'),
			failed: $i18n.t('Error'),
			cancelled: $i18n.t('Cancelled')
		}[run.status] ?? run.status;
</script>

<div class="flex flex-wrap gap-2 text-xs tabular-nums" role="status">
	<span>{statusLabel}</span>
	{#each runCounts(run) as counter (counter.label)}
		<span>{$i18n.t(counter.label)}: {counter.count}</span>
	{/each}
</div>
