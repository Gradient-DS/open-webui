<script lang="ts">
	// [Gradient] Derive message-level rows locally; listing sources never loads their files.
	import { getContext, onMount } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { DisplayCitation } from './reduceSources';
	import { mergeCitationDocuments } from './citationDocuments';
	import { calculatePercentage, decodeString, getRelevanceColor } from './useCitationDocument';
	import Document from '$lib/components/icons/Document.svelte';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let visibleCitations: DisplayCitation[] = [];
	export let selectedCitation: DisplayCitation | null = null;
	export let showPercentage = false;
	export let showRelevance = true;
	export let onSelect: (citation: DisplayCitation) => void;
	let list: HTMLDivElement;
	$: rows = visibleCitations.map((citation) => {
		const documents = mergeCitationDocuments(citation);
		const scores = documents
			.map((doc) => doc.distance)
			.filter((score): score is number => typeof score === 'number' && Number.isFinite(score));
		return {
			citation,
			count: documents.length,
			best: scores.length ? Math.max(...scores) : undefined
		};
	});
	onMount(() => {
		list?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
	});
</script>

<div bind:this={list} class="flex-1 min-h-0 overflow-y-auto scrollbar-thin p-2 space-y-1">
	{#each rows as row}
		{@const name = decodeString(row.citation.source.name ?? '')}
		<button
			class="flex w-full items-start gap-2 rounded-xl p-3 text-left hover:bg-gray-50 dark:hover:bg-gray-850 {selectedCitation ===
			row.citation
				? 'bg-gray-100 dark:bg-gray-800'
				: ''}"
			aria-label={$i18n.t('View source: {{name}}', { name })}
			aria-current={selectedCitation === row.citation ? 'true' : undefined}
			on:click={() => onSelect(row.citation)}
		>
			{#if row.citation.source.name?.startsWith('http')}
				<img
					src="https://www.google.com/s2/favicons?sz=32&domain={row.citation.source.name}"
					alt=""
					class="size-4 mt-0.5 shrink-0 rounded-full"
					on:error={(event) => {
						// LICENSE covers this Open WebUI fallback logo.
						// Do not alter, remove, obscure, or replace it except as LICENSE permits:
						// https://docs.openwebui.com/license.
						(event.currentTarget as HTMLImageElement).src = '/favicon.png';
					}}
				/>
			{:else}<Document className="size-4 mt-0.5 shrink-0 text-gray-500" />{/if}
			<div class="min-w-0 flex-1">
				<div class="line-clamp-1 text-sm">{name}</div>
				<div class="mt-1 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
					<span>{$i18n.t('{{count}} passages', { count: row.count })}</span>
					{#if showRelevance && row.best !== undefined}
						{#if showPercentage}
							{@const percentage = calculatePercentage(row.best)}
							{#if percentage !== null}<span class="px-1 rounded-sm {getRelevanceColor(percentage)}"
									>{percentage.toFixed(0)}%</span
								>{/if}
						{:else}<span>({row.best.toFixed(4)})</span>{/if}
					{/if}
				</div>
			</div>
		</button>
	{/each}
</div>
