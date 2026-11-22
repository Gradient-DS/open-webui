<script lang="ts">
	// [Gradient] What a search-result citation is: the page's or file's title and address, that only the
	// search snippet was read, and a link to open the original. A council result also shows its fragment with
	// the matched words marked and an image of the PDF page it is on, which opens the PDF at that page.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { CitationDocument } from './citationDocuments';
	import { searchResultPage, snippetSegments } from './searchSnippet';
	import ArrowTopRightOnSquare from '$lib/components/icons/ArrowTopRightOnSquare.svelte';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let document: CitationDocument;
	$: page = searchResultPage(document);
	$: segments =
		page?.kind === 'council'
			? snippetSegments(document?.document ?? '', document?.metadata?.highlights)
			: [];
	let previewFailed = false;
</script>

{#if page}
	<div
		class="flex flex-col gap-1 rounded-lg border border-gray-100 dark:border-gray-800 p-2.5 text-xs"
	>
		<div class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words">{page.title}</div>
		{#if page.address && page.kind !== 'council'}
			<div class="truncate text-gray-500 dark:text-gray-400" title={page.url}>{page.address}</div>
		{/if}
		<p class="text-gray-500 dark:text-gray-400">
			{page.kind === 'council'
				? $i18n.t('Council search result — fragment only, the document was not opened')
				: page.file
					? $i18n.t('Search result in your files — snippet only, the file was not opened')
					: $i18n.t('Search result — snippet only, the page was not opened')}
		</p>
		{#if segments.some((segment) => segment.marked)}
			<p class="text-gray-700 dark:text-gray-300 break-words">
				{#each segments as segment}{#if segment.marked}<mark
							class="rounded-sm bg-yellow-100 px-0.5 text-inherit dark:bg-yellow-500/30"
							>{segment.text}</mark
						>{:else}{segment.text}{/if}{/each}
			</p>
		{/if}
		{#if page.preview && !previewFailed}
			<svelte:element
				this={page.url ? 'a' : 'div'}
				href={page.url}
				target={page.url ? '_blank' : undefined}
				rel={page.url ? 'noopener noreferrer' : undefined}
				class="block w-fit"
			>
				<img
					src={page.preview}
					alt={$i18n.t('Page of {{title}}', { title: page.title })}
					loading="lazy"
					referrerpolicy="no-referrer"
					class="max-h-64 w-auto rounded border border-gray-100 dark:border-gray-800"
					on:error={() => (previewFailed = true)}
				/>
			</svelte:element>
		{/if}
		{#if page.url}
			<a
				class="inline-flex w-fit items-center gap-1 underline hover:text-gray-500 dark:hover:text-gray-100"
				href={page.url}
				target="_blank"
				rel="noopener noreferrer"
			>
				{page.kind === 'council'
					? $i18n.t('Open original document')
					: page.file
						? $i18n.t('Open original file')
						: $i18n.t('Open original page')}
				<ArrowTopRightOnSquare className="size-3" />
			</a>
		{/if}
	</div>
{/if}
