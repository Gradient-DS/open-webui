<script lang="ts">
	// [Gradient] Selection stays in this chat and never enables an unselected connection.
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import type { ToolSource } from '$lib/apis/toolSources';
	import Check from '$lib/components/icons/Check.svelte';
	import SearchInput from './SearchInput.svelte';

	let {
		sources,
		selectedToolSourceIds = $bindable([])
	}: {
		sources: ToolSource[];
		selectedToolSourceIds: string[];
	} = $props();
	const i18n = getContext<Writable<I18n>>('i18n');
	let query = $state('');
	const filtered = $derived(
		sources.filter((source) =>
			`${source.name} ${source.group_name}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())
		)
	);

	function toggle(id: string) {
		if (selectedToolSourceIds.includes(id)) {
			selectedToolSourceIds = selectedToolSourceIds.filter((entry) => entry !== id);
		} else if (selectedToolSourceIds.length < 8) {
			selectedToolSourceIds = [...selectedToolSourceIds, id];
		}
	}
</script>

<div class="flex min-h-0 flex-1 flex-col gap-0.5 overflow-hidden">
	<SearchInput bind:value={query} placeholder={$i18n.t('Search external connections')} />
	{#if selectedToolSourceIds.length > 0}
		<button
			type="button"
			class="px-2 py-1 text-left text-xs text-gray-500"
			onclick={() => (selectedToolSourceIds = [])}
		>
			{$i18n.t('Clear selection')}
		</button>
	{/if}
	<div class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin">
		{#each filtered as source (source.id)}
			{@const selected = selectedToolSourceIds.includes(source.id)}
			<button
				type="button"
				aria-pressed={selected}
				disabled={!selected && selectedToolSourceIds.length >= 8}
				class="flex w-full items-center justify-between gap-2 rounded-xl px-2 py-1 text-left text-[0.8125rem] hover:bg-gray-50/40 disabled:opacity-40 dark:hover:bg-gray-800/40"
				onclick={() => toggle(source.id)}
			>
				<span class="min-w-0 flex-1"
					><span class="block truncate">{source.name}</span><span
						class="block truncate text-xs text-gray-500">{source.group_name}</span
					></span
				>
				{#if selected}<Check className="size-3.5" />{/if}
			</button>
		{:else}
			<div class="py-4 text-center text-sm text-gray-500">
				{$i18n.t('No external connections found.')}
			</div>
		{/each}
	</div>
</div>
