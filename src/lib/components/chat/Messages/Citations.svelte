<script lang="ts">
	import { getContext } from 'svelte';
	import { config, embed, showControls, showEmbeds } from '$lib/stores';

	import CitationModal from './Citations/CitationModal.svelte';

	const i18n = getContext('i18n');

	export let id = '';
	export let chatId = '';

	export let sources = [];
	export let readOnly = false;
	/**
	 * [Gradient] Cumulative `[N]` ids that should appear in the bottom panel
	 * for this message. The agent service dispatches this alongside the
	 * cumulative `sources` list so the panel can scope per-message while the
	 * inline `[N]` lookup keeps working across cross-turn cites.
	 *
	 * `null` (the default) means "show everything" — keeps back-compat for
	 * older messages and for upstream providers that don't dispatch the
	 * `panel_filter` event.
	 */
	export let panelFilter: number[] | null = null;
	/**
	 * [Gradient] Whether the parent message has finished streaming. Used to
	 * suppress the bottom pill until the agent's final `panel_filter` is
	 * in. Intermediate dispatches (one after every tool iteration) carry
	 * the full "retrieved so far" set, so a web-search turn briefly shows
	 * the entire result corpus (e.g. 19 hits) before the post-answer
	 * dispatch narrows it to what the LLM actually cited (e.g. 7). The
	 * final panel_filter arrives right after the answer text finishes
	 * streaming — effectively the same moment as `done` flipping true —
	 * so gating on done avoids the flash without delaying anything that
	 * was stable mid-stream.
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
		citations = sources.reduce((acc, source) => {
			if (Object.keys(source).length === 0) {
				return acc;
			}

			source?.document?.forEach((document, index) => {
				const metadata = source?.metadata?.[index];
				const distance = source?.distances?.[index];

				// Within the same citation there could be multiple documents
				const id = metadata?.source ?? source?.source?.id ?? 'N/A';
				let _source = source?.source;

				if (metadata?.name) {
					_source = { ..._source, name: metadata.name };
				}

				if (id.startsWith('http://') || id.startsWith('https://')) {
					_source = { ..._source, name: id, url: id };
				}

				const existingSource = acc.find((item) => item.id === id);

				if (existingSource) {
					existingSource.document.push(document);
					existingSource.metadata.push(metadata);
					if (distance !== undefined) existingSource.distances.push(distance);
				} else {
					acc.push({
						id: id,
						source: _source,
						document: [document],
						metadata: metadata ? [metadata] : [],
						distances: distance !== undefined ? [distance] : []
					});
				}
			});

			return acc;
		}, []);
		console.log('citations', citations);

		showRelevance = calculateShowRelevance(citations);
		showPercentage = shouldShowPercentage(citations);
	}

	// [Gradient] Filter to the per-message panel scope. `idx + 1` is the
	// citation's cumulative `[N]` (its 1-based position in the dense
	// `sources` array the inline render uses). When `panelFilter` is set
	// we keep only those positions; when it's `null` we show everything.
	// The filter does NOT touch the underlying `citations` array — inline
	// `[N]` clicks still resolve via `showSourceModal(N)` against the
	// cumulative list.
	$: {
		if (panelFilter == null) {
			visibleCitations = citations;
		} else {
			const allowed = new Set(panelFilter);
			visibleCitations = citations.filter((_, idx) => allowed.has(idx + 1));
		}
	}

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

{#if visibleCitations.length > 0 && messageDone}
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
