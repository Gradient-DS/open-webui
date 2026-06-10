<script lang="ts">
	import { getContext } from 'svelte';
	import { config, embed, showControls, showEmbeds } from '$lib/stores';

	import CitationModal from './Citations/CitationModal.svelte';
	import { reduceSources } from './Citations/reduceSources';
	import { deriveVisibleSources, hasTaggedSources } from '$lib/utils/citations';

	const i18n = getContext('i18n');

	export let id = '';
	export let chatId = '';

	export let sources = [];
	export let readOnly = false;
	/**
	 * [Gradient] Cumulative `[N]` ids cited inline in this message's content,
	 * computed by the parent (which owns the content) via `extractCitedNs`.
	 * For identity-tagged sources the bottom panel derives as
	 * "retrieved this turn (`current_turn`) ∪ cited inline (`n` ∈ citedNs)" —
	 * the same markers the chips read, so panel and chips cannot disagree.
	 * Ignored for untagged sources (vanilla show-all path).
	 */
	export let citedNs: Set<number> | null = null;
	/**
	 * [Gradient] Whether the parent message has finished streaming. Gates the
	 * bottom pill for UNTAGGED sources only (vanilla path, where mid-stream
	 * source sets can be unstable). Tagged sources render progressively —
	 * `current_turn` provenance makes mid-stream panels already correct.
	 *
	 * Defaults to `true` so non-streaming callers (e.g. `Document.svelte`)
	 * keep their previous behavior without opting in.
	 */
	export let messageDone: boolean = true;

	let citations = [];
	let visibleCitations = [];
	let showPercentage = false;
	let showRelevance = true;

	$: citationRelevanceEnabled = $config?.features?.enable_citation_relevance ?? true;

	let citationModal = null;

	let showCitations = false;
	let showCitationModal = false;

	let selectedCitation: any = null;

	export const showSourceModal = (sourceId) => {
		let index;
		let suffix = null;

		if (typeof sourceId === 'string') {
			const output = sourceId.split('#');
			index = parseInt(output[0]) - 1;

			if (output.length > 1) {
				suffix = output[1];
			}
		} else {
			index = sourceId - 1;
		}

		if (citations[index]) {
			console.log('Showing citation modal for:', citations[index]);

			if (citations[index]?.source?.embed_url) {
				const embedUrl = citations[index].source.embed_url;
				if (embedUrl) {
					if (readOnly) {
						// Open in new tab if readOnly
						window.open(embedUrl, '_blank');
						return;
					} else {
						showControls.set(true);
						showEmbeds.set(true);
						embed.set({
							url: embedUrl,
							title: citations[index]?.source?.name || 'Embedded Content',
							source: citations[index],
							chatId: chatId,
							messageId: id,
							sourceId: sourceId
						});
					}
				} else {
					selectedCitation = citations[index];
					showCitationModal = true;
				}
			} else {
				selectedCitation = citations[index];
				showCitationModal = true;
			}
		}
	};

	function calculateShowRelevance(sources: any[]) {
		const distances = sources.flatMap((citation) => citation.distances ?? []);
		const inRange = distances.filter((d) => d !== undefined && d >= -1 && d <= 1).length;
		const outOfRange = distances.filter((d) => d !== undefined && (d < -1 || d > 1)).length;

		if (distances.length === 0) {
			return false;
		}

		if (
			(inRange === distances.length - 1 && outOfRange === 1) ||
			(outOfRange === distances.length - 1 && inRange === 1)
		) {
			return false;
		}

		return true;
	}

	function shouldShowPercentage(sources: any[]) {
		const distances = sources.flatMap((citation) => citation.distances ?? []);
		return distances.every((d) => d !== undefined && d >= -1 && d <= 1);
	}

	$: {
		citations = reduceSources(sources);
		showRelevance = calculateShowRelevance(citations);
		showPercentage = shouldShowPercentage(citations);
	}

	// [Gradient] Derive the per-message panel scope from citation identity.
	// Tagged sources (agent service, `n` > 0) show when retrieved this turn
	// or cited inline; untagged sources show unconditionally (vanilla
	// show-all path). The derivation does NOT touch the underlying
	// `citations` array — inline `[N]` clicks still resolve via
	// `showSourceModal(N)` against the cumulative list.
	$: tagged = hasTaggedSources(sources);
	$: visibleCitations = tagged
		? reduceSources(deriveVisibleSources(sources, citedNs ?? new Set()))
		: citations;

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch (e) {
			return str;
		}
	};
</script>

<CitationModal
	bind:show={showCitationModal}
	citation={selectedCitation}
	showPercentage={citationRelevanceEnabled && showPercentage}
	showRelevance={citationRelevanceEnabled && showRelevance}
/>

{#if visibleCitations.length > 0 && (messageDone || tagged)}
	{@const urlCitations = visibleCitations.filter((c) =>
		c?.source?.name?.startsWith('http')
	)}
	<div class=" py-1 -mx-0.5 w-full flex gap-1 items-center flex-wrap">
		<button
			class="text-xs font-medium text-gray-600 dark:text-gray-300 px-3.5 h-8 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition flex items-center gap-1 border border-gray-50 dark:border-gray-850/30"
			aria-label={visibleCitations.length === 1
				? $i18n.t('Toggle 1 source')
				: $i18n.t('Toggle {{COUNT}} sources', { COUNT: visibleCitations.length })}
			aria-expanded={showCitations}
			on:click={() => {
				showCitations = !showCitations;
			}}
		>
			{#if urlCitations.length > 0}
				<div class="flex -space-x-1 items-center">
					{#each urlCitations.slice(0, 3) as citation, idx}
						<img
							src="https://www.google.com/s2/favicons?sz=32&domain={citation.source.name}"
							alt="favicon"
							class="size-4 rounded-full shrink-0 border border-white dark:border-gray-850 bg-white dark:bg-gray-900"
							on:error={(e) => {
								e.target.src = '/favicon.png';
							}}
						/>
					{/each}
					{#if citations.length > 3}
						<div
							class="size-4 rounded-full shrink-0 border border-white dark:border-gray-850 bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-[8px] font-semibold text-gray-500 dark:text-gray-400 whitespace-nowrap tracking-tighter"
							aria-hidden="true"
						>
							+{citations.length - Math.min(urlCitations.length, 3)}
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

{#if showCitations}
	<div class="py-1.5">
		<div class="text-xs gap-2 flex flex-col">
			{#each visibleCitations as citation, idx}
				<button
					id={`source-${id}-${idx + 1}`}
					aria-label={$i18n.t('View source: {{name}}', {
						name: decodeString(citation.source.name)
					})}
					class="no-toggle outline-hidden flex dark:text-gray-300 bg-transparent text-gray-600 rounded-xl gap-1.5 items-center"
					on:click={() => {
						showCitationModal = true;
						selectedCitation = citation;
					}}
				>
					<div class=" font-medium bg-gray-50 dark:bg-gray-850 rounded-md px-1">
						{idx + 1}
					</div>
					<div
						class="flex-1 truncate hover:text-black dark:text-white/60 dark:hover:text-white transition text-left"
					>
						{decodeString(citation.source.name)}
					</div>
				</button>
			{/each}
		</div>
	</div>
{/if}
