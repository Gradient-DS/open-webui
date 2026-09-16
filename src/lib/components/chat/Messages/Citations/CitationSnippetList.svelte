<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { CitationDocument } from './citationDocuments';
	const i18n = getContext<Readable<I18n>>('i18n');

	import { tick } from 'svelte';
	import {
		calculatePercentage,
		getRelevanceColor,
		isDocumentSnippet,
		truncate
	} from './useCitationDocument';
	export let mergedDocuments: CitationDocument[] = [];
	export let activeSnippetIdx = 0;
	export let showPercentage = false;
	export let showRelevance = true;
	export let layout: 'rail' | 'compact' = 'rail';
	export let onSelect: (idx: number) => void;
	const SNIPPET_TRUNCATE = 200;
	let list: HTMLDivElement;
	$: if (layout === 'compact') scrollActiveIntoView(activeSnippetIdx, list);
	async function scrollActiveIntoView(idx: number, container: HTMLDivElement | undefined) {
		await tick();
		container?.querySelector(`[data-snippet-index="${idx}"]`)?.scrollIntoView({ block: 'nearest' });
	}
</script>

<div bind:this={list} class="flex flex-col gap-1.5">
	{#each mergedDocuments as document, snippetIdx}
		<button
			class="text-left w-full rounded-lg border {layout === 'compact'
				? 'p-2'
				: 'p-2.5'} transition {snippetIdx === activeSnippetIdx
				? 'border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-850'
				: 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-850/50'}"
			on:click={() => onSelect(snippetIdx)}
			aria-current={snippetIdx === activeSnippetIdx ? 'true' : undefined}
			data-snippet-index={snippetIdx}
		>
			<div class="flex items-center gap-2 mb-1">
				{#if showRelevance && document.distance !== undefined}
					{#if showPercentage}
						{@const percentage = calculatePercentage(document.distance)}
						{#if typeof percentage === 'number'}
							<span class={`px-1 rounded-sm text-xs font-normal ${getRelevanceColor(percentage)}`}>
								{percentage.toFixed(0)}%
							</span>
						{/if}
					{:else if typeof document?.distance === 'number'}
						<span class="text-xs text-gray-500 dark:text-gray-500">
							({(document?.distance ?? 0).toFixed(4)})
						</span>
					{/if}
				{/if}
				{#if isDocumentSnippet(document)}
					<span class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Full document')}
					</span>
				{:else if Number.isInteger(document?.metadata?.page)}
					<span class="text-xs text-gray-500 dark:text-gray-400">
						({$i18n.t('page')}
						{Number(document.metadata.page) + 1})
					</span>
				{/if}
			</div>
			<div
				class="text-xs text-gray-700 dark:text-gray-300 {layout === 'compact'
					? 'line-clamp-2'
					: 'line-clamp-4'}"
			>
				{truncate(document.document?.trim() ?? '', SNIPPET_TRUNCATE)}
			</div>
		</button>
	{/each}
</div>
