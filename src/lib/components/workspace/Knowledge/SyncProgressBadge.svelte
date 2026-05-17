<script lang="ts">
	import { getContext } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext<any>('i18n');

	export let sync:
		| {
				status?: string;
				progress_current?: number;
				progress_total?: number;
				stage_counts?: { ok?: number; failed?: number };
		  }
		| undefined = undefined;

	$: total = sync?.progress_total ?? 0;
	$: processed = (sync?.stage_counts?.ok ?? 0) + (sync?.stage_counts?.failed ?? 0);
	// Fall back to progress_current when stage_counts isn't populated yet
	// (early sync ticks). Once stage_counts arrives, ok+failed is authoritative.
	$: tracked = processed > 0 ? processed : (sync?.progress_current ?? 0);
	$: percent = total > 0 ? Math.min(100, Math.round((tracked / total) * 100)) : null;
</script>

<Tooltip content={$i18n.t('Syncing...')}>
	{#if percent !== null}
		<span class="text-[10px] font-semibold text-blue-600 dark:text-blue-400 tabular-nums">
			{percent}%
		</span>
	{:else}
		<Spinner className="size-3" />
	{/if}
</Tooltip>
