<script lang="ts">
	// [Gradient] Citation prototype host. Additional layouts reuse the same document state.
	import { getContext, onDestroy } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import { citationPanel, citationPanelVariant, showCitationPanel } from '$lib/stores';
	import CitationHeader from '../Messages/Citations/CitationHeader.svelte';
	import CitationSnippetList from '../Messages/Citations/CitationSnippetList.svelte';
	import CitationViewer from '../Messages/Citations/CitationViewer.svelte';
	import CitationContent from '../Messages/Citations/CitationContent.svelte';
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
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';

	const i18n = getContext<Readable<I18n>>('i18n');
	export let overlay = false;
	let previousCitation: DisplayCitation | null = null;
	let mergedDocuments: CitationDocument[] = [];
	let activeSnippetIdx = 0;
	let expandedDocs: Set<number> = new Set();
	let expanded = true;
	let selectedTab: 'preview' | 'content' = 'preview';
	let previewAvailable = true;
	let showModal = false;
	let viewer: CitationViewer;
	let probeVersion = 0;

	$: citation = $citationPanel?.citation ?? null;
	$: showPercentage = $citationPanel?.showPercentage ?? false;
	$: showRelevance = $citationPanel?.showRelevance ?? true;
	$: if (citation !== previousCitation) {
		previousCitation = citation;
		mergedDocuments = mergeCitationDocuments(citation);
		activeSnippetIdx = 0;
		expandedDocs = new Set();
		selectedTab = 'preview';
		expanded = true;
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

	function selectSnippet(idx: number) {
		if (idx < 0 || idx >= mergedDocuments.length) return;
		activeSnippetIdx = idx;
		viewer?.selectSnippet(idx);
	}
	function close() {
		showCitationPanel.set(false);
		citationPanel.set(null);
	}
</script>

{#if citation}
	<CitationModal bind:show={showModal} {citation} {showPercentage} {showRelevance} />
	<div class="relative flex flex-col h-full min-h-0 w-full text-gray-900 dark:text-gray-100">
		<div
			class="flex items-center justify-between gap-2 px-3 py-3 shrink-0 border-b border-gray-100 dark:border-gray-800"
		>
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
		<div class="relative flex flex-col flex-1 min-h-0 overflow-hidden">
			{#if $citationPanelVariant === 'focus' || $citationPanelVariant === 'navigator'}
				<p class="p-4 text-sm text-gray-500">
					{$i18n.t("Variant '{{name}}' is not built yet", {
						name: $citationPanelVariant === 'focus' ? $i18n.t('Focus') : $i18n.t('Navigator')
					})}
				</p>
			{:else if isPreviewable && previewAvailable && selectedTab === 'preview'}
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
			{#if overlay}<div class="absolute inset-0 z-10"></div>{/if}
		</div>
	</div>
{/if}
