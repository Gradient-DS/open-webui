<script lang="ts">
	// [Gradient] Shared stack preview and content fallback for every panel variant.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { DisplayCitation } from './reduceSources';
	import type { CitationDocument } from './citationDocuments';
	import CitationSnippetList from './CitationSnippetList.svelte';
	import CitationViewer from './CitationViewer.svelte';
	import CitationContent from './CitationContent.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let citation: DisplayCitation;
	export let mergedDocuments: CitationDocument[] = [];
	export let activeSnippetIdx = 0;
	export let expandedDocs: Set<number> = new Set();
	export let showPercentage = false;
	export let showRelevance = true;
	export let previewAvailable = true;
	export let preview = true;
	let expanded = true;
	let viewer: CitationViewer;
	function selectSnippet(idx: number) {
		if (idx < 0 || idx >= mergedDocuments.length) return;
		activeSnippetIdx = idx;
		viewer?.selectSnippet(idx);
	}
</script>

{#if preview}
	<div
		class="flex flex-col max-h-[34%] shrink-0 min-h-0 border-b border-gray-100 dark:border-gray-800"
	>
		<div
			class="flex items-center justify-between gap-2 px-3 py-2 shrink-0 text-xs text-gray-500 dark:text-gray-400"
		>
			<span
				>{expanded || !mergedDocuments.length
					? $i18n.t('{{count}} passages', { count: mergedDocuments.length })
					: $i18n.t('Passage {{n}} of {{count}}', {
							n: activeSnippetIdx + 1,
							count: mergedDocuments.length
						})}</span
			>
			<div class="flex items-center gap-1">
				{#if !expanded}
					<button
						class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
						disabled={activeSnippetIdx === 0}
						aria-label={$i18n.t('Previous passage')}
						title={$i18n.t('Previous passage')}
						on:click={() => selectSnippet(activeSnippetIdx - 1)}
						><ChevronLeft className="size-4" /></button
					>
					<button
						class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
						disabled={activeSnippetIdx >= mergedDocuments.length - 1}
						aria-label={$i18n.t('Next passage')}
						title={$i18n.t('Next passage')}
						on:click={() => selectSnippet(activeSnippetIdx + 1)}
						><ChevronRight className="size-4" /></button
					>
				{/if}
				<button
					class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800"
					aria-expanded={expanded}
					aria-label={expanded ? $i18n.t('Collapse passages') : $i18n.t('Expand passages')}
					title={expanded ? $i18n.t('Collapse passages') : $i18n.t('Expand passages')}
					on:click={() => (expanded = !expanded)}
				>
					{#if expanded}<ChevronUp className="size-4" />{:else}<ChevronDown
							className="size-4"
						/>{/if}
				</button>
			</div>
		</div>
		{#if expanded}
			<div class="overflow-y-auto min-h-0 scrollbar-thin px-3 pb-2">
				<CitationSnippetList
					{mergedDocuments}
					{activeSnippetIdx}
					{showPercentage}
					{showRelevance}
					layout="compact"
					onSelect={selectSnippet}
				/>
			</div>
		{/if}
	</div>
	<div class="flex-1 min-h-0 p-2">
		{#key citation}
			<CitationViewer
				bind:this={viewer}
				{citation}
				{mergedDocuments}
				{activeSnippetIdx}
				{previewAvailable}
			/>
		{/key}
	</div>
{:else}
	<div class="flex flex-col flex-1 min-h-0 overflow-y-auto scrollbar-thin gap-1 p-4">
		<CitationContent bind:expandedDocs {mergedDocuments} {showPercentage} {showRelevance} />
	</div>
{/if}
