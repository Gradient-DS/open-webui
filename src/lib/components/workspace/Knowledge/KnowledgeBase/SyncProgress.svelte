<script lang="ts">
	import { getContext } from 'svelte';
	import { tweened } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';
	import type { StageCounts } from '$lib/apis/sync';

	const i18n = getContext<any>('i18n');

	export let current: number = 0;
	export let total: number = 0;
	export let stageCounts: StageCounts | undefined = undefined;

	// Collapse the pipeline stages into four user-facing statuses:
	// queued → pending, in progress → downloading + parsing + ingesting,
	// done → ok, error → failed.
	$: counts = {
		queued: stageCounts?.pending ?? 0,
		inProgress:
			(stageCounts?.downloading ?? 0) +
			(stageCounts?.parsing ?? 0) +
			(stageCounts?.ingesting ?? 0),
		done: stageCounts?.ok ?? 0,
		error: stageCounts?.failed ?? 0
	};

	// Bar-segment order (left → right): done → in progress → error.
	// Queued is the gray track tail.
	$: barSegments = [
		{ key: 'done', value: counts.done, barClass: 'bg-green-500' },
		{ key: 'inProgress', value: counts.inProgress, barClass: 'bg-blue-500' },
		{ key: 'error', value: counts.error, barClass: 'bg-red-500' }
	];

	// Pill order (left → right): queued → in progress → done → error.
	$: pills = [
		{
			key: 'queued',
			label: $i18n.t('Queued'),
			value: counts.queued,
			textOn: 'text-gray-600 dark:text-gray-300',
			dotOn: 'bg-gray-400 dark:bg-gray-500'
		},
		{
			key: 'inProgress',
			label: $i18n.t('In progress'),
			value: counts.inProgress,
			textOn: 'text-blue-600 dark:text-blue-400',
			dotOn: 'bg-blue-500'
		},
		{
			key: 'done',
			label: $i18n.t('Done'),
			value: counts.done,
			textOn: 'text-green-600 dark:text-green-400',
			dotOn: 'bg-green-500'
		},
		{
			key: 'error',
			label: $i18n.t('Error'),
			value: counts.error,
			textOn: 'text-red-600 dark:text-red-400',
			dotOn: 'bg-red-500'
		}
	];

	$: denom = Math.max(total, 1);

	function pct(n: number): number {
		return (n / denom) * 100;
	}

	// Tweened "current" so the n/m counter ticks smoothly instead of jumping.
	const tweenedCurrent = tweened(0, { duration: 400, easing: cubicOut });
	$: tweenedCurrent.set(current);
</script>

<div class="flex flex-col gap-1.5 min-w-[200px] max-w-[320px]">
	<div class="flex items-center gap-2">
		<span class="text-xs text-blue-500 font-medium shrink-0 tabular-nums">
			{#if total > 0}
				{Math.round($tweenedCurrent)} / {total}
			{:else}
				{$i18n.t('Starting...')}
			{/if}
		</span>
		<div
			class="flex-1 flex h-1.5 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-800 relative"
			role="progressbar"
			aria-valuenow={current}
			aria-valuemin={0}
			aria-valuemax={total}
			aria-label={$i18n.t('Sync progress')}
		>
			{#each barSegments as segment (segment.key)}
				<div
					class="{segment.barClass} transition-[width] duration-500 ease-out"
					style="width: {pct(segment.value)}%"
				></div>
			{/each}
			{#if total === 0}
				<!-- Indeterminate shimmer overlay while we wait for the first
				     progress event. Disappears when total > 0 fades the bar in. -->
				<div class="absolute inset-0 pointer-events-none overflow-hidden">
					<div class="sync-progress-indeterminate h-full w-1/3 bg-blue-400/60 rounded-full"></div>
				</div>
			{/if}
		</div>
	</div>

	<div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] font-medium tabular-nums leading-none">
		{#each pills as pill (pill.key)}
			<span
				class="inline-flex items-center gap-1 transition-colors duration-300 {pill.value > 0
					? pill.textOn
					: 'text-gray-400 dark:text-gray-600'}"
			>
				<span
					class="size-1.5 rounded-full transition-colors duration-300 {pill.value > 0
						? pill.dotOn
						: 'bg-gray-300 dark:bg-gray-700'}"
				></span>
				<span>{pill.label}</span>
				<span>{pill.value}</span>
			</span>
		{/each}
	</div>
</div>

<style>
	.sync-progress-indeterminate {
		animation: sync-progress-slide 1.4s ease-in-out infinite;
	}
	@keyframes sync-progress-slide {
		0% {
			transform: translateX(-100%);
		}
		100% {
			transform: translateX(400%);
		}
	}
</style>
