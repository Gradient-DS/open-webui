<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Switch from '$lib/components/common/Switch.svelte';
	import type { ItemNoun } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');

	// Background-sync enable + cadence + per-sync cap. Extracted from the
	// three identical "Sync Settings" blocks in the CloudSync monolith
	// (Confluence / Google Drive / OneDrive). The blocks were byte-identical
	// except for the max-items label, which `itemNoun` selects.

	// Two-way bound config values.
	export let backgroundEnabled = false;
	export let intervalMinutes: number = 60;
	// 0 / blank = no per-sync limit. Kept nullable so a cleared input round-trips.
	export let maxPerSync: number | null = 0;

	// 'pages' (Confluence) | 'files' (Drive/OneDrive) | 'items' (generic).
	export let itemNoun: ItemNoun = 'items';

	// Whether to render the section heading. The orchestrator may suppress it
	// when the surrounding section already provides a heading.
	export let showHeading = true;

	$: maxLabel =
		itemNoun === 'pages'
			? $i18n.t('Maximum pages per sync')
			: itemNoun === 'files'
				? $i18n.t('Maximum files per sync')
				: $i18n.t('Maximum items per sync');
</script>

<div class="space-y-3">
	{#if showHeading}
		<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
			{$i18n.t('Sync Settings')}
		</div>
	{/if}

	<div class="flex justify-between items-center">
		<div class="font-medium">{$i18n.t('Background synchronization')}</div>
		<Switch bind:state={backgroundEnabled} />
	</div>

	<div>
		<div class="mb-1 text-xs text-gray-500">
			{$i18n.t('Sync interval (minutes)')}
		</div>
		<input
			class="w-full text-sm bg-transparent outline-hidden"
			type="number"
			bind:value={intervalMinutes}
			min="1"
			autocomplete="off"
		/>
	</div>

	<div>
		<div class="mb-1 text-xs text-gray-500">
			{maxLabel}
		</div>
		<input
			class="w-full text-sm bg-transparent outline-hidden"
			type="number"
			bind:value={maxPerSync}
			min="0"
			autocomplete="off"
			placeholder={$i18n.t('Leave empty for no limit')}
		/>
		<div class="mt-1 text-xs text-gray-500">
			{$i18n.t('Leave empty for no limit')}. {$i18n.t(
				'The knowledge base file-count limit still applies.'
			)}
		</div>
	</div>
</div>
