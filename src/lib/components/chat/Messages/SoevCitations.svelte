<script lang="ts">
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import { citationPanel, openSourcesTabSignal } from '$lib/stores/citations';

	import Citations from './Citations.svelte';
	import { reduceSources, type DisplayCitation, type RawSource } from './Citations/reduceSources';
	// [Gradient] Share answer-used scope with the grouped Sources tab.
	import { usedCitations } from './Citations/panelScope';
	import { calculateShowRelevance, shouldShowPercentage } from './Citations/relevanceDisplay';

	// [Gradient] Type the shared translation store for citation controls.
	const i18n = getContext<Readable<I18n>>('i18n');

	export let id = '';
	export let chatId = '';

	export let sources: RawSource[] = [];
	export let readOnly = false;
	/**
	 * [Gradient] Whether the parent message has finished streaming. Used to
	 * suppress the bottom pill until the agent's final source dispatch is
	 * in. Intermediate dispatches (one after every tool iteration) carry the
	 * full "retrieved so far" set with `current_turn` true, so a web-search
	 * turn briefly shows the entire result corpus (e.g. 19 hits) before the
	 * post-answer dispatch settles the `cited_this_turn` flags. The final
	 * dispatch arrives right after the answer text finishes streaming —
	 * effectively the same moment as `done` flipping true — so gating on
	 * done avoids the flash without delaying anything that was stable
	 * mid-stream.
	 *
	 * Defaults to `true` so non-streaming callers (e.g. `Document.svelte`)
	 * keep their previous behavior without opting in.
	 */
	export let messageDone: boolean = true;

	let citations: DisplayCitation[] = [];
	let visibleCitations: DisplayCitation[] = [];
	let showPercentage = false;
	let showRelevance = true;

	$: citationRelevanceEnabled = $config?.features?.enable_citation_relevance ?? true;

	let inner: Citations;

	// [Gradient] Navigator's pill opens the message list without selecting a source.
	const openPanel = (citation: DisplayCitation | null, level: 'list' | 'detail') => {
		citationPanel.set({
			citation,
			level,
			citations,
			showPercentage: citationRelevanceEnabled && showPercentage,
			showRelevance: citationRelevanceEnabled && showRelevance,
			messageId: id,
			chatId
		});
		openSourcesTabSignal.update((value) => value + 1);
	};
	export const showSourceModal = (sourceId: string | number) => {
		if (readOnly) return inner?.showSourceModal(sourceId);
		const index =
			typeof sourceId === 'string' ? parseInt(sourceId.split('#')[0]) - 1 : sourceId - 1;
		if (citations[index]) openPanel(citations[index], 'detail');
	};

	// [Gradient] Inline [N] retains cumulative numbering; the pill lists only answer-used sources.
	$: citations = reduceSources(sources);
	$: visibleCitations = usedCitations(citations);
	$: showRelevance = calculateShowRelevance(visibleCitations);
	$: showPercentage = shouldShowPercentage(visibleCitations);
</script>

{#if readOnly}
	<Citations {id} {chatId} {sources} {readOnly} {messageDone} bind:this={inner} />
{:else if visibleCitations.length > 0 && messageDone}
	{@const urlCitations = visibleCitations.filter((c) => c?.source?.name?.startsWith('http'))}
	<div class=" py-1 -mx-0.5 w-full flex gap-1 items-center flex-wrap">
		<button
			class="text-xs font-normal text-gray-600 dark:text-gray-300 px-3.5 h-8 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition flex items-center gap-1 border border-gray-50 dark:border-gray-850/30"
			aria-label={$i18n.t('Sources')}
			on:click={() => openPanel(null, 'list')}
		>
			{#if urlCitations.length > 0}
				<div class="flex -space-x-1 items-center">
					{#each urlCitations.slice(0, 3) as citation}
						<img
							src="https://www.google.com/s2/favicons?sz=32&domain={citation.source.name}"
							alt="favicon"
							class="size-4 rounded-full shrink-0 border border-white dark:border-gray-850 bg-white dark:bg-gray-900"
							on:error={(e) => {
								// LICENSE covers this Open WebUI fallback logo.
								// Do not alter, remove, obscure, or replace it except as LICENSE permits:
								// https://docs.openwebui.com/license.
								e.target.src = '/favicon.png';
							}}
						/>
					{/each}
					<!-- [Gradient] The favicon overflow counts this answer's used sources too. -->
					{#if visibleCitations.length > 3}
						<div
							class="size-4 rounded-full shrink-0 border border-white dark:border-gray-850 bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-[0.5rem] font-normal text-gray-500 dark:text-gray-400 whitespace-nowrap tracking-tighter"
							aria-hidden="true"
						>
							+{visibleCitations.length - Math.min(urlCitations.length, 3)}
						</div>
					{/if}
				</div>
			{/if}
			<div>
				{#if visibleCitations.length === 1}
					{$i18n.t('1 Source')}
				{:else}
					{$i18n.t('{{COUNT}} Sources', {
						COUNT: visibleCitations.length
					})}
				{/if}
			</div>
		</button>
	</div>
{/if}
