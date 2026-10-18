<script lang="ts">
	// [Gradient] Derive message-level rows locally; listing sources never loads their files.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { DisplayCitation } from './reduceSources';
	import { mergeCitationDocuments } from './citationDocuments';
	import { decodeString, isDocumentSnippet } from './useCitationDocument';
	import SourceIcon from './SourceIcon.svelte';
	const i18n = getContext<Readable<I18n>>('i18n');
	// [Gradient] Groups own scrolling and header positioning when this list is embedded.
	export let embedded = false;
	export let visibleCitations: DisplayCitation[] = [];
	export let onSelect: (citation: DisplayCitation) => void;
	$: rows = visibleCitations.map((citation) => {
		// [Gradient] No relevance in the list: a per-source maximum over its passages
		// misrepresents how the scores work. Relevance stays on the passages.
		const documents = mergeCitationDocuments(citation);
		// A whole document's text is one citation, not a passage.
		const whole = documents.length > 0 && documents.every(isDocumentSnippet);
		return { citation, count: documents.length, whole };
	});
</script>

<div class="space-y-1 {embedded ? '' : 'flex-1 min-h-0 overflow-y-auto scrollbar-thin p-2'}">
	{#each rows as row}
		{@const name = decodeString(row.citation.source.name ?? '')}
		<button
			class="flex w-full items-start gap-2 rounded-xl p-3 text-left hover:bg-gray-50 dark:hover:bg-gray-850"
			aria-label={$i18n.t('View source: {{name}}', { name })}
			on:click={() => onSelect(row.citation)}
		>
			<SourceIcon source={row.citation.source} className="size-4 mt-0.5 shrink-0" />
			<div class="min-w-0 flex-1">
				<div class="line-clamp-1 text-sm">{name}</div>
				<div class="mt-1 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
					<span
						>{row.whole
							? $i18n.t('Whole-document citation')
							: $i18n.t('{{count}} passages', { count: row.count })}</span
					>
				</div>
			</div>
		</button>
	{/each}
</div>
