<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { CitationDocument } from './citationDocuments';
	const i18n = getContext<Readable<I18n>>('i18n');

	// [Gradient] Shared citation building blocks retain the modal presentation.
	import { onDestroy } from 'svelte';
	import type { DisplayCitation } from './reduceSources';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import CitationHeader from './CitationHeader.svelte';
	import CitationViewer from './CitationViewer.svelte';
	import CitationSnippetList from './CitationSnippetList.svelte';
	import CitationContent from './CitationContent.svelte';
	import { mergeCitationDocuments, probeFileAvailable } from './citationDocuments';
	import { citationFileInfo, resolveExternalUrl } from './useCitationDocument';
	export let show = false;
	export let citation: DisplayCitation | null = null;
	export let showPercentage = false;
	export let showRelevance = true;
	let mergedDocuments: CitationDocument[] = [];
	let previewAvailable = true;
	let selectedTab: 'preview' | 'content' = 'preview';
	let activeSnippetIdx = 0;
	let viewer: CitationViewer;
	let probeVersion = 0;
	$: if (citation) {
		selectedTab = 'preview';
		activeSnippetIdx = 0;
		mergedDocuments = mergeCitationDocuments(citation);
	}
	$: ({ fileId, isPreviewable, showSnippetRail, isImage, isAudio } = citationFileInfo(
		citation,
		mergedDocuments
	));
	$: externalUrl = resolveExternalUrl(citation, mergedDocuments);
	$: checkAvailability(show, fileId);
	async function checkAvailability(open: boolean, id: string | undefined) {
		const version = ++probeVersion;
		previewAvailable = true;
		if (open && id) {
			const available = await probeFileAvailable(id);
			if (version === probeVersion) previewAvailable = available;
		}
	}
	onDestroy(() => {
		probeVersion++;
	});
	function selectSnippet(idx: number) {
		activeSnippetIdx = idx;
		viewer?.selectSnippet(idx);
	}
</script>

<Modal size="xl" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-300 px-4.5 pt-3 pb-2">
			<CitationHeader {citation} {mergedDocuments} {previewAvailable} {externalUrl}>
				<button
					slot="actions"
					class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
					aria-label={$i18n.t('Close citation modal')}
					on:click={() => {
						show = false;
					}}
				>
					<XMark className={'size-4'} />
				</button>
			</CitationHeader>
		</div>
		<div class="flex flex-col w-full px-5 pb-5">
			{#if isPreviewable && previewAvailable}
				<div class="flex gap-1 mb-3">
					<button
						class="px-3 py-1 text-xs font-medium rounded-lg transition {selectedTab === 'preview'
							? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
							: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
						on:click={() => (selectedTab = 'preview')}
					>
						{$i18n.t('Preview')}
					</button>
					<button
						class="px-3 py-1 text-xs font-medium rounded-lg transition {selectedTab === 'content'
							? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
							: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
						on:click={() => (selectedTab = 'content')}
					>
						{$i18n.t('Content')}
					</button>
				</div>
			{/if}

			{#key citation}
				{#if isPreviewable && previewAvailable && selectedTab === 'preview'}
					{#if showSnippetRail}
						<div class="flex flex-col md:flex-row w-full gap-3 h-[70vh]">
							<div
								class="w-full md:w-72 shrink-0 overflow-y-auto scrollbar-thin flex flex-col gap-1.5"
							>
								<CitationSnippetList
									{mergedDocuments}
									{activeSnippetIdx}
									{showPercentage}
									{showRelevance}
									onSelect={selectSnippet}
								/>
							</div>
							<div class="flex-1 min-w-0 rounded-lg overflow-hidden">
								<CitationViewer
									bind:this={viewer}
									{citation}
									{mergedDocuments}
									{activeSnippetIdx}
									{previewAvailable}
								/>
							</div>
						</div>
					{:else}
						<div class={isImage ? 'max-h-[70vh]' : isAudio ? '' : 'h-[70vh]'}>
							<CitationViewer
								imageHeightClass="max-h-[70vh]"
								{citation}
								{mergedDocuments}
								{activeSnippetIdx}
								{previewAvailable}
							/>
						</div>
					{/if}
				{:else}
					<div class="flex flex-col md:flex-row w-full md:space-x-4">
						<div
							class="flex flex-col w-full dark:text-gray-200 overflow-y-scroll max-h-[22rem] scrollbar-thin gap-1"
						>
							<CitationContent {mergedDocuments} {showPercentage} {showRelevance} />
						</div>
					</div>
				{/if}
			{/key}
		</div>
	</div>
</Modal>
