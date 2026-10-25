<script lang="ts">
	import Modal from '$lib/components/common/Modal.svelte';
	import { getContext } from 'svelte';
	import { getModelOverview } from '$lib/apis/analytics';
	import ModelActivityChart from '$lib/components/admin/Evaluations/ModelActivityChart.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	export let show = false;
	export let model: { id: string; name: string } | null = null;
	export let onClose: () => void = () => {};

	const i18n = getContext('i18n');

	// Overview tab state
	type TimeRange = '30d' | '1y' | 'all';
	const TIME_RANGES: { key: TimeRange; label: string; days: number }[] = [
		{ key: '30d', label: '30D', days: 30 },
		{ key: '1y', label: '1Y', days: 365 },
		{ key: 'all', label: 'All', days: 0 }
	];
	let selectedRange: TimeRange = '30d';
	let history: Array<{ date: string; won: number; lost: number }> = [];
	let tags: Array<{ tag: string; count: number }> = [];
	let loadingOverview = false;

	const close = () => {
		show = false;
		history = [];
		tags = [];
		onClose();
	};

	const loadOverview = async (days: number) => {
		if (!model?.id) return;
		loadingOverview = true;
		try {
			const result = await getModelOverview(localStorage.token, model.id, days);
			history = result?.history ?? [];
			tags = result?.tags ?? [];
		} catch (err) {
			console.error('Failed to load overview:', err);
			history = [];
			tags = [];
		}
		loadingOverview = false;
	};

	const selectRange = (range: TimeRange) => {
		selectedRange = range;
		const config = TIME_RANGES.find((r) => r.key === range);
		if (config) {
			loadOverview(config.days);
		}
	};

	// Load overview when modal opens
	$: if (show && model?.id) {
		selectedRange = '30d';
		loadOverview(30);
	}
</script>

<Modal size="md" bind:show>
	{#if model}
		<div class="flex justify-between dark:text-gray-300 px-4 pt-3 pb-1">
			<Tooltip content={`${model.name} (${model.id})`} placement="top-start">
				<div class="text-sm font-medium self-center line-clamp-1">
					{model.name}
				</div>
			</Tooltip>
			<button
				class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
				on:click={close}
				aria-label="Close"
			>
				<XMark className={'size-4'} />
			</button>
		</div>

		<div class="px-5 pb-4 dark:text-gray-200">
			<!-- Activity Chart -->
			<div class="mb-4 mt-3">
				<div class="flex items-center justify-between mb-2">
					<Tooltip content={$i18n.t('Thumbs up/down ratings from users on model responses')}>
						<div class="text-xs text-gray-500 font-normal uppercase tracking-wide cursor-help">
							{$i18n.t('Feedback Activity')}
						</div>
					</Tooltip>
					<div
						class="inline-flex rounded-full bg-gray-100/80 p-0.5 dark:bg-gray-800/80 backdrop-blur-sm"
					>
						{#each TIME_RANGES as range}
							<button
								type="button"
								class="rounded-full transition-all duration-200 px-2.5 py-0.5 text-xs font-normal {selectedRange ===
								range.key
									? 'bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white'
									: 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'}"
								on:click={() => selectRange(range.key)}
							>
								{range.label}
							</button>
						{/each}
					</div>
				</div>
				<ModelActivityChart
					{history}
					loading={loadingOverview}
					aggregateWeekly={selectedRange === '1y' || selectedRange === 'all'}
				/>
			</div>

			<!-- Tags -->
			<div class="mb-4">
				<div class="text-xs text-gray-500 mb-2 font-normal uppercase tracking-wide">
					{$i18n.t('Tags')}
				</div>
				{#if tags.length}
					<div class="flex flex-wrap gap-1 -mx-1">
						{#each tags as tagInfo}
							<span class="px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-xs">
								{tagInfo.tag} <span class="text-gray-500 font-normal">{tagInfo.count}</span>
							</span>
						{/each}
					</div>
				{:else}
					<span class="text-gray-500 text-sm">-</span>
				{/if}
			</div>

			<div class="flex justify-end pt-4">
				<button
					class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
					type="button"
					on:click={close}
				>
					{$i18n.t('Close')}
				</button>
			</div>
		</div>
	{/if}
</Modal>
