<script lang="ts">
	// [Gradient] Citation prototype host. Additional layouts reuse the same document state.
	import { getContext, onDestroy, tick } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import { config } from '$lib/stores';
	import { citationPanel } from '$lib/stores/citations';
	import CitationStackBody from '../Messages/Citations/CitationStackBody.svelte';
	import CitationSourceGroups from '../Messages/Citations/CitationSourceGroups.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import CitationHeader from '../Messages/Citations/CitationHeader.svelte';
	import {
		mergeCitationDocuments,
		probeFileAvailable,
		type CitationDocument
	} from '../Messages/Citations/citationDocuments';
	import { citationFileInfo, resolveExternalUrl } from '../Messages/Citations/useCitationDocument';
	import type { DisplayCitation } from '../Messages/Citations/reduceSources';
	import { usedCitations } from '../Messages/Citations/panelScope';
	import {
		calculateShowRelevance,
		shouldShowPercentage
	} from '../Messages/Citations/relevanceDisplay';
	import { sourceGroups, type SourceGroup, type CitationHistory } from './citationTab';

	const i18n = getContext<Readable<I18n>>('i18n');
	export let overlay = false;
	export let history: CitationHistory | null = null;
	export let chatId = '';
	let previousCitation: DisplayCitation | null = null;
	let mergedDocuments: CitationDocument[] = [];
	let activeSnippetIdx = 0;
	let expandedDocs: Set<number> = new Set();
	let selectedTab: 'preview' | 'content' = 'preview';
	let previewAvailable = true;
	let panelElement: HTMLElement;
	let probeVersion = 0;

	// [Gradient] The list comes from the live branch; payloads identify only the target answer.
	$: groups = sourceGroups(history);
	$: panel = $citationPanel?.chatId === chatId ? $citationPanel : null;
	$: activeGroup = groups.find((group) => group.messageId === panel?.messageId);
	$: if ($citationPanel && ($citationPanel.chatId !== chatId || !activeGroup)) {
		citationPanel.set(null);
	}
	$: relevanceEnabled = $config?.features?.enable_citation_relevance ?? true;
	let expandedGroups = new Set<string>();
	let expandedChatId: string | null = null;
	let handledPayload: typeof $citationPanel = null;
	let defaultMessageId: string | undefined;
	let groupList: CitationSourceGroups | undefined;
	let scrollVersion = 0;
	$: syncExpandedGroups(groups, panel, chatId);

	function syncExpandedGroups(
		currentGroups: SourceGroup[],
		payload: typeof $citationPanel,
		currentChatId: string
	) {
		if (expandedChatId !== currentChatId) {
			expandedChatId = currentChatId;
			expandedGroups = new Set();
			handledPayload = null;
			defaultMessageId = undefined;
			scrollVersion++;
		}
		const ids = new Set(currentGroups.map((group) => group.messageId));
		if ([...expandedGroups].some((id) => !ids.has(id))) {
			expandedGroups = new Set([...expandedGroups].filter((id) => ids.has(id)));
		}
		if (payload && ids.has(payload.messageId)) {
			if (payload !== handledPayload) {
				expandedGroups = new Set([...expandedGroups, payload.messageId]);
				void scrollToGroup(payload.messageId);
			}
		} else if (!payload) {
			const latestId = currentGroups[currentGroups.length - 1]?.messageId;
			if (latestId !== defaultMessageId || handledPayload) {
				expandedGroups = new Set(latestId ? [latestId] : []);
				defaultMessageId = latestId;
				if (latestId) void scrollToGroup(latestId);
			}
		}
		handledPayload = payload;
	}

	async function scrollToGroup(messageId: string) {
		const version = ++scrollVersion;
		await tick();
		if (version === scrollVersion) groupList?.scrollToGroup(messageId);
	}

	function toggleGroup(messageId: string) {
		const next = new Set(expandedGroups);
		if (next.has(messageId)) next.delete(messageId);
		else next.add(messageId);
		expandedGroups = next;
	}

	// [Gradient] Match by stable source id: history reduction creates new citation objects.
	$: citation = panel?.citation ?? null;
	$: cumulativeCitations = activeGroup?.citations ?? panel?.citations ?? [];
	$: groupUsed = activeGroup?.used ?? usedCitations(cumulativeCitations);
	$: visibleCitations =
		citation && !groupUsed.some((item) => item.id === citation.id)
			? cumulativeCitations
			: groupUsed;
	$: listLevel = panel?.level === 'list' || !citation;
	$: sourcePosition = citation ? visibleCitations.findIndex((item) => item.id === citation.id) : -1;
	$: showPercentage = relevanceEnabled && shouldShowPercentage(visibleCitations);
	$: showRelevance = relevanceEnabled && calculateShowRelevance(visibleCitations);

	function selectSource(selected: DisplayCitation, group: SourceGroup | undefined = activeGroup) {
		if (!group) return;
		citationPanel.set({
			citation: selected,
			citations: group.citations,
			showPercentage: relevanceEnabled && group.showPercentage,
			showRelevance: relevanceEnabled && group.showRelevance,
			messageId: group.messageId,
			chatId,
			level: 'detail'
		});
	}
	function backToSources() {
		if (panel) citationPanel.set({ ...panel, level: 'list' });
	}

	$: if (citation !== previousCitation) {
		previousCitation = citation;
		mergedDocuments = mergeCitationDocuments(citation);
		activeSnippetIdx = 0;
		expandedDocs = new Set();
		selectedTab = 'preview';
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
		scrollVersion++;
	});
</script>

{#if groups.length > 0}
	<section
		class="relative flex flex-col h-full min-h-0 w-full text-gray-900 dark:text-gray-100"
		bind:this={panelElement}
		tabindex="-1"
		aria-label={listLevel ? $i18n.t('Sources') : $i18n.t('Citation')}
	>
		{#if listLevel}
			<CitationSourceGroups
				bind:this={groupList}
				{groups}
				expanded={expandedGroups}
				onToggle={toggleGroup}
				onSelect={selectSource}
			/>
		{:else if citation}
			<div
				class="flex items-center justify-between gap-2 px-3 py-3 shrink-0 border-b border-gray-100 dark:border-gray-800"
			>
				<button
					class="shrink-0 rounded-lg p-1 hover:bg-gray-100 dark:hover:bg-gray-800"
					aria-label={$i18n.t('Back to sources')}
					on:click={backToSources}><ChevronLeft className="size-4" /></button
				>
				<CitationHeader {citation} {mergedDocuments} {previewAvailable} {externalUrl} size="sm">
					<div slot="actions" class="flex items-center gap-1 shrink-0 whitespace-nowrap">
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
					</div>
				</CitationHeader>
			</div>
			{#if sourcePosition >= 0}
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
					<CitationStackBody
						showDocumentNote
						{citation}
						{mergedDocuments}
						bind:activeSnippetIdx
						bind:expandedDocs
						{showPercentage}
						{showRelevance}
						{previewAvailable}
						preview={isPreviewable && previewAvailable && selectedTab === 'preview'}
					/>
				{/key}
			</div>
		{/if}
		{#if overlay}<div class="absolute inset-0 z-10"></div>{/if}
	</section>
{:else}
	<div
		class="flex h-full min-h-0 items-center justify-center px-6 text-center text-sm text-gray-500 dark:text-gray-400"
	>
		{$i18n.t('Sources will appear here when you add them or when soev.ai finds them')}
	</div>
{/if}
