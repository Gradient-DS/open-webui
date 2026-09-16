<script lang="ts">
	// [Gradient] Viewer-first layout; all passage changes use the shared viewer API.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { DisplayCitation } from './reduceSources';
	import type { CitationDocument } from './citationDocuments';
	import CitationViewer from './CitationViewer.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ListBullet from '$lib/components/icons/ListBullet.svelte';
	import {
		calculatePercentage,
		getRelevanceColor,
		isDocumentSnippet,
		snippetPage,
		truncate
	} from './useCitationDocument';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let citation: DisplayCitation;
	export let mergedDocuments: CitationDocument[] = [];
	export let activeSnippetIdx = 0;
	export let showPercentage = false;
	export let showRelevance = true;
	export let previewAvailable = true;
	let viewer: CitationViewer;
	let dropdown: Dropdown;
	let expanded = false;
	let showPassages = false;
	$: active = mergedDocuments[activeSnippetIdx];
	$: page = snippetPage(active);
	function selectSnippet(idx: number) {
		if (idx < 0 || idx >= mergedDocuments.length) return;
		activeSnippetIdx = idx;
		expanded = false;
		viewer?.selectSnippet(idx);
	}
	export function handleKeydown(event: KeyboardEvent) {
		if (
			event.defaultPrevented ||
			showPassages ||
			event.altKey ||
			event.ctrlKey ||
			event.metaKey ||
			event.shiftKey
		)
			return;
		if (
			event.target instanceof HTMLElement &&
			event.target.closest(
				'input, textarea, select, [contenteditable]:not([contenteditable="false"])'
			)
		)
			return;
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
		event.preventDefault();
		selectSnippet(activeSnippetIdx + (event.key === 'ArrowLeft' ? -1 : 1));
	}
</script>

<div class="flex items-center gap-1 px-3 py-2 shrink-0 text-xs text-gray-500 dark:text-gray-400">
	<button
		class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
		disabled={activeSnippetIdx === 0}
		aria-label={$i18n.t('Previous passage')}
		on:click={() => selectSnippet(activeSnippetIdx - 1)}><ChevronLeft className="size-4" /></button
	>
	<span class="whitespace-nowrap" aria-live="polite"
		>{$i18n.t('Passage {{n}} of {{count}}', {
			n: mergedDocuments.length ? activeSnippetIdx + 1 : 0,
			count: mergedDocuments.length
		})}</span
	>
	<button
		class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
		disabled={activeSnippetIdx >= mergedDocuments.length - 1}
		aria-label={$i18n.t('Next passage')}
		on:click={() => selectSnippet(activeSnippetIdx + 1)}><ChevronRight className="size-4" /></button
	>
	{#if isDocumentSnippet(active)}
		<span class="truncate">{$i18n.t('Full document')}</span>
	{:else if page !== undefined}
		<span class="whitespace-nowrap">{$i18n.t('p. {{page}}', { page: page + 1 })}</span>
	{/if}
	{#if showRelevance && typeof active?.distance === 'number'}
		{#if showPercentage}
			{@const percentage = calculatePercentage(active.distance)}
			{#if percentage !== null}<span class="px-1 rounded-sm {getRelevanceColor(percentage)}"
					>{percentage.toFixed(0)}%</span
				>{/if}
		{:else}<span>({active.distance.toFixed(4)})</span>{/if}
	{/if}
	<div class="ml-auto shrink-0">
		<Dropdown
			bind:this={dropdown}
			bind:show={showPassages}
			align="end"
			maxHeight="min(24rem, 60dvh)"
		>
			<button
				class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
				disabled={!mergedDocuments.length}
				aria-label={$i18n.t('Choose passage')}
				aria-haspopup="menu"
				aria-expanded={showPassages}><ListBullet className="size-4" /></button
			>
			<div slot="content">
				<DropdownMenu className="w-80 max-w-[calc(100vw-2rem)]">
					<div>
						{#each mergedDocuments as document, idx}
							<button
								role="menuitem"
								class="flex w-full items-start gap-2 rounded-lg p-2 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-800 {activeSnippetIdx ===
								idx
									? 'bg-gray-100 dark:bg-gray-800'
									: ''}"
								on:click={() => {
									selectSnippet(idx);
									dropdown.close();
								}}
							>
								<span class="shrink-0 text-gray-500">{idx + 1}</span>
								<div class="min-w-0">
									{#if isDocumentSnippet(document)}<div class="text-gray-500">
											{$i18n.t('Full document')}
										</div>
									{:else if snippetPage(document) !== undefined}<div class="text-gray-500">
											{$i18n.t('p. {{page}}', { page: Number(snippetPage(document)) + 1 })}
										</div>{/if}
									<div class="line-clamp-2">{truncate(document.document.trim(), 80)}</div>
								</div>
							</button>
						{/each}
					</div>
				</DropdownMenu>
			</div>
		</Dropdown>
	</div>
</div>
{#if active}
	<div
		class="mx-3 mb-2 flex flex-col shrink-0 min-h-0 max-h-[40%] rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-850 p-2"
	>
		<div
			class="min-h-0 text-xs whitespace-pre-wrap break-words text-gray-700 dark:text-gray-300 {expanded
				? 'overflow-y-auto scrollbar-thin'
				: 'line-clamp-2'}"
		>
			{active.document}
		</div>
		<button
			class="self-start shrink-0 mt-1 text-xs text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
			aria-expanded={expanded}
			on:click={() => (expanded = !expanded)}
			>{expanded ? $i18n.t('Show less') : $i18n.t('Show more')}</button
		>
	</div>
{/if}
<div class="flex-1 min-h-0 p-2">
	<CitationViewer
		bind:this={viewer}
		{citation}
		{mergedDocuments}
		{activeSnippetIdx}
		{previewAvailable}
	/>
</div>
