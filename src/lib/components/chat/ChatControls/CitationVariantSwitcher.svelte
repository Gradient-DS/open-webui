<script lang="ts">
	// [Gradient] SPIKE — remove before merge.
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import { citationPanelVariant, type CitationPanelVariant } from '$lib/stores';
	const i18n = getContext<Readable<I18n>>('i18n');
	const variants: { value: CitationPanelVariant; label: string; description: string }[] = [
		{
			value: 'modal',
			label: 'Modal',
			description: 'Show citations in the original centered modal'
		},
		{
			value: 'navigator',
			label: 'Navigator',
			description: 'Browse all message sources and open their passages'
		}
	];
</script>

<div
	class="fixed bottom-3 left-3 z-[60] flex gap-0.5 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-1 shadow-lg"
	role="group"
	aria-label={$i18n.t('Citation panel variant')}
>
	{#each variants as variant}
		<button
			class="rounded-full px-2.5 py-1 text-xs transition {$citationPanelVariant === variant.value
				? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
				: 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'}"
			title={$i18n.t(variant.description)}
			aria-pressed={$citationPanelVariant === variant.value}
			on:click={() => citationPanelVariant.set(variant.value)}
		>
			{$i18n.t(variant.label)}
		</button>
	{/each}
	<!-- [Gradient] Help for the two exposed citation layouts. -->
	<Tooltip
		content={$i18n.t(
			'Modal: citations in a centered window. Navigator: all message sources, then source detail.'
		)}
	>
		<button
			class="rounded-full px-2 text-xs text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
			aria-label={$i18n.t('About citation panel variants')}>?</button
		>
	</Tooltip>
</div>
