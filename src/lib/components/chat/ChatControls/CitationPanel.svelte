<script lang="ts">
	// [Gradient] Citation prototype host. Additional layouts reuse the same document state.
	import { getContext, onDestroy } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import { citationPanel, citationPanelVariant, showCitationPanel } from '$lib/stores';
	import CitationStackBody from '../Messages/Citations/CitationStackBody.svelte';
	import CitationFocusBody from '../Messages/Citations/CitationFocusBody.svelte';
	import CitationSourceList from '../Messages/Citations/CitationSourceList.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import CitationHeader from '../Messages/Citations/CitationHeader.svelte';
	import CitationModal from '../Messages/Citations/CitationModal.svelte';
	import {
		mergeCitationDocuments,
		probeFileAvailable,
		type CitationDocument
	} from '../Messages/Citations/citationDocuments';
	import { citationFileInfo, resolveExternalUrl } from '../Messages/Citations/useCitationDocument';
	import type { DisplayCitation } from '../Messages/Citations/reduceSources';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ArrowsPointingOut from '$lib/components/icons/ArrowsPointingOut.svelte';

	const i18n = getContext<Readable<I18n>>('i18n');
	export let overlay = false;
	let previousCitation: DisplayCitation | null = null;
	let mergedDocuments: CitationDocument[] = [];
	let activeSnippetIdx = 0;
	let expandedDocs: Set<number> = new Set();
	let selectedTab: 'preview' | 'content' = 'preview';
	let previewAvailable = true;
	let showModal = false;
	let focusBody: CitationFocusBody;
	let panelElement: HTMLElement;
	let probeVersion = 0;

	// [Gradient] Keep the last selected citation in the payload when returning to the list.
	$: visibleCitations = $citationPanel?.visibleCitations ?? [];
	$: navigator = $citationPanelVariant === 'navigator';
	$: listLevel = navigator && ($citationPanel?.level === 'list' || !$citationPanel?.citation);
	$: sourcePosition = citation ? visibleCitations.indexOf(citation) : -1;
	$: if (!navigator && $citationPanel?.level === 'list') {
		const selected = $citationPanel.citation ?? visibleCitations[0];
		if (selected) selectSource(selected);
	}
	function selectSource(selected: DisplayCitation) {
		if ($citationPanel)
			citationPanel.set({ ...$citationPanel, citation: selected, level: 'detail' });
	}
	function backToSources() {
		if ($citationPanel) citationPanel.set({ ...$citationPanel, level: 'list' });
	}

	$: citation = $citationPanel?.citation ?? null;
	$: showPercentage = $citationPanel?.showPercentage ?? false;
	$: showRelevance = $citationPanel?.showRelevance ?? true;
	$: if (citation !== previousCitation) {
		previousCitation = citation;
		mergedDocuments = mergeCitationDocuments(citation);
		activeSnippetIdx = 0;
		expandedDocs = new Set();
		selectedTab = 'preview';
		showModal = false;
	}
	$: ({ fileId, isPreviewable } = citationFileInfo(citation, mergedDocuments));
	$: externalUrl = resolveExternalUrl(citation, mergedDocuments);
	$: checkAvailability(fileId);
	async function checkAvailability(id: string | undefined) {
		const version = ++probeVersion;
		previewAvailable = true;
		if (id) {
			const available = await probeFileAvailable(id);
			if (version === probeVersion) previewAvailable = available;
		}
	}
	onDestroy(() => {
		probeVersion++;
	});

	function close() {
		showCitationPanel.set(false);
		citationPanel.set(null);
	}
</script>

<svelte:window
	on:keydown={(event) => {
		if (
			event.target instanceof Node &&
			panelElement?.contains(event.target) &&
			$citationPanelVariant === 'focus' &&
			selectedTab === 'preview' &&
			isPreviewable &&
			previewAvailable &&
			!showModal
		)
			focusBody?.handleKeydown(event);
	}}
/>

{#if $citationPanel}
	<CitationModal bind:show={showModal} {citation} {showPercentage} {showRelevance} />
	<section
		class="relative flex flex-col h-full min-h-0 w-full text-gray-900 dark:text-gray-100"
		bind:this={panelElement}
		tabindex="-1"
		aria-label={listLevel ? $i18n.t('Sources') : $i18n.t('Citation')}
	>
		{#if listLevel}
			<div
				class="flex items-center justify-between gap-2 px-3 py-3 shrink-0 border-b border-gray-100 dark:border-gray-800"
			>
				<h2 class="text-lg font-medium">{$i18n.t('Sources')}</h2>
				<button
					class="rounded-lg p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
					aria-label={$i18n.t('Close citation panel')}
					on:click={close}><XMark className="size-4" /></button
				>
			</div>
			<CitationSourceList
				{visibleCitations}
				citations={$citationPanel.citations}
				selectedCitation={citation}
				{showPercentage}
				{showRelevance}
				onSelect={selectSource}
			/>
		{:else if citation}
			<div
				class="flex items-center justify-between gap-2 px-3 py-3 shrink-0 border-b border-gray-100 dark:border-gray-800"
			>
				{#if navigator}
					<button
						class="shrink-0 rounded-lg p-1 hover:bg-gray-100 dark:hover:bg-gray-800"
						aria-label={$i18n.t('Back to sources')}
						on:click={backToSources}><ChevronLeft className="size-4" /></button
					>
				{/if}
				<CitationHeader {citation} {mergedDocuments} {previewAvailable} {externalUrl}>
					<div slot="actions" class="flex items-center gap-1 shrink-0">
						{#if isPreviewable && previewAvailable}
							<div
								class="flex gap-0.5 rounded-lg bg-gray-100 dark:bg-gray-800 p-0.5"
								role="group"
								aria-label={$i18n.t('Citation view')}
							>
								<button
									class="rounded-md px-2 py-1 text-xs {selectedTab === 'preview'
										? 'bg-white dark:bg-gray-700 shadow-sm'
										: 'text-gray-500 dark:text-gray-400'}"
									aria-pressed={selectedTab === 'preview'}
									on:click={() => (selectedTab = 'preview')}>{$i18n.t('Preview')}</button
								>
								<button
									class="rounded-md px-2 py-1 text-xs {selectedTab === 'content'
										? 'bg-white dark:bg-gray-700 shadow-sm'
										: 'text-gray-500 dark:text-gray-400'}"
									aria-pressed={selectedTab === 'content'}
									on:click={() => (selectedTab = 'content')}>{$i18n.t('Content')}</button
								>
							</div>
						{/if}
						<button
							class="rounded-lg p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
							title={$i18n.t('Open in modal')}
							aria-label={$i18n.t('Open in modal')}
							on:click={() => (showModal = true)}><ArrowsPointingOut className="size-4" /></button
						>
						<button
							class="rounded-lg p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
							title={$i18n.t('Close citation panel')}
							aria-label={$i18n.t('Close citation panel')}
							on:click={close}><XMark className="size-4" /></button
						>
					</div>
				</CitationHeader>
			</div>
			{#if navigator && sourcePosition >= 0}
				<div
					class="flex items-center gap-1 px-3 py-1 shrink-0 text-xs text-gray-500 dark:text-gray-400"
				>
					<button
						class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
						disabled={sourcePosition === 0}
						aria-label={$i18n.t('Previous source')}
						on:click={() => selectSource(visibleCitations[sourcePosition - 1])}
						><ChevronLeft className="size-3" /></button
					>
					<span
						>{$i18n.t('Source {{i}} of {{n}}', {
							i: sourcePosition + 1,
							n: visibleCitations.length
						})}</span
					>
					<button
						class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
						disabled={sourcePosition >= visibleCitations.length - 1}
						aria-label={$i18n.t('Next source')}
						on:click={() => selectSource(visibleCitations[sourcePosition + 1])}
						><ChevronRight className="size-3" /></button
					>
				</div>
			{/if}
			<div class="relative flex flex-col flex-1 min-h-0 overflow-hidden">
				{#key citation}
					{#if $citationPanelVariant === 'focus' && isPreviewable && previewAvailable && selectedTab === 'preview'}
						<CitationFocusBody
							bind:this={focusBody}
							{citation}
							{mergedDocuments}
							bind:activeSnippetIdx
							{showPercentage}
							{showRelevance}
							{previewAvailable}
						/>
					{:else}
						<CitationStackBody
							{citation}
							{mergedDocuments}
							bind:activeSnippetIdx
							bind:expandedDocs
							{showPercentage}
							{showRelevance}
							{previewAvailable}
							preview={isPreviewable && previewAvailable && selectedTab === 'preview'}
						/>
					{/if}
				{/key}
			</div>
		{/if}
		{#if overlay}<div class="absolute inset-0 z-10"></div>{/if}
	</section>
{/if}
