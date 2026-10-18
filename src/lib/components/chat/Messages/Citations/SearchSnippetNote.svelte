<script lang="ts">
	// [Gradient] What a search-result citation is: the page's title and address, that only the
	// search engine's snippet was read, and a link to open the page.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { CitationDocument } from './citationDocuments';
	import { searchResultPage } from './searchSnippet';
	import ArrowTopRightOnSquare from '$lib/components/icons/ArrowTopRightOnSquare.svelte';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let document: CitationDocument;
	$: page = searchResultPage(document);
</script>

{#if page}
	<div
		class="flex flex-col gap-1 rounded-lg border border-gray-100 dark:border-gray-800 p-2.5 text-xs"
	>
		<div class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words">{page.title}</div>
		<div class="truncate text-gray-500 dark:text-gray-400" title={page.url}>{page.address}</div>
		<p class="text-gray-500 dark:text-gray-400">
			{$i18n.t('Search result — snippet only, the page was not opened')}
		</p>
		<a
			class="inline-flex w-fit items-center gap-1 underline hover:text-gray-500 dark:hover:text-gray-100"
			href={page.url}
			target="_blank"
			rel="noopener noreferrer"
		>
			{$i18n.t('Open original page')}
			<ArrowTopRightOnSquare className="size-3" />
		</a>
	</div>
{/if}
