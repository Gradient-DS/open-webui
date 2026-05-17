<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');
	import WebSearchResults from '../WebSearchResults.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import { t } from 'i18next';
	import { statusI18nParams } from './statusI18nParams';

	export let status = null;
	export let done = false;
	// StatusHistory is now the canonical display surface for tool activity, so
	// hidden=true entries (tool starts that used to be redundant with the
	// inline <details type="tool_calls"> marker) should still render here.
	// Callers set this to bypass the legacy `hidden` filter.
	export let forceVisible = false;
	// Set when this item is the header inside StatusHistory's collapsed-view
	// toggle button. Suppresses any nested expandable so the parent button
	// remains the sole click target.
	export let asHeader = false;
</script>

{#if forceVisible || !status?.hidden}
	<div class="status-description flex items-center gap-2 py-0.5 w-full text-left">
		{#if (status?.action === 'web_search' || status?.action === 'fetch_url') && (status?.urls || status?.items)}
			<WebSearchResults {status} {asHeader}>
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{(done || status?.done) === false
							? 'shimmer'
							: ''} text-base line-clamp-1 text-wrap"
					>
						<!-- $i18n.t("Generating search query") -->
						<!-- $i18n.t("No search query generated") -->
						<!-- $i18n.t('Searched {{count}} sites') -->
						{#if status?.description?.includes('{{count}}')}
							{$i18n.t(status?.description, {
								count: (status?.urls || status?.items).length,
								...statusI18nParams(status),
								query: status?.query
							})}
						{:else if status?.description}
							{$i18n.t(status.description, {
								...statusI18nParams(status),
								query: status?.query
							})}
						{/if}
					</div>
				</div>
			</WebSearchResults>
		{:else if status?.action === 'knowledge_search'}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
						searchQuery: status.query
					})}
				</div>
			</div>
		{:else if status?.action === 'web_search_queries_generated' && status?.queries}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{$i18n.t(`Searching`)}
				</div>

				<div class=" flex gap-1 flex-wrap mt-2">
					{#each status.queries as query, idx (query)}
						<div
							class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
						>
							<div>
								<Search className="size-3" />
							</div>

							<span class="line-clamp-1">
								{query}
							</span>
						</div>
					{/each}
				</div>
			</div>
		{:else if status?.action === 'queries_generated' && status?.queries}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{$i18n.t(`Querying`)}
				</div>

				<div class=" flex gap-1 flex-wrap mt-2">
					{#each status.queries as query, idx (query)}
						<div
							class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
						>
							<div>
								<Search className="size-3" />
							</div>

							<span class="line-clamp-1">
								{query}
							</span>
						</div>
					{/each}
				</div>
			</div>
		{:else if status?.action === 'sources_retrieved' && status?.count !== undefined}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{#if status.count === 0}
						{$i18n.t('No sources found')}
					{:else if status.count === 1}
						{$i18n.t('Retrieved 1 source')}
					{:else}
						<!-- {$i18n.t('Source')} -->
						<!-- {$i18n.t('No source available')} -->
						<!-- {$i18n.t('No distance available')} -->
						<!-- {$i18n.t('Retrieved {{count}} sources')} -->
						{$i18n.t('Retrieved {{count}} sources', {
							count: status.count
						})}
					{/if}
				</div>
			</div>
		{:else}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					<!-- Parser hints for translation keys emitted by the agent under emit_translatable_status=true. -->
					<!-- $i18n.t("Searching {{collection_name}} for \"{{query}}\"...") -->
					<!-- $i18n.t("Searching {{doc_title}} for \"{{query}}\"...") -->
					<!-- $i18n.t("Finding documents in {{collection_name}} for \"{{query}}\"...") -->
					<!-- $i18n.t("Listing documents in {{collection_name}}...") -->
					<!-- $i18n.t("Searching knowledge bases for \"{{query}}\"...") -->
					<!-- $i18n.t("Reading {{doc_title}}...") -->
					<!-- $i18n.t("Summarizing {{doc_title}}...") -->
					<!-- $i18n.t("Asking user...") -->
					<!-- $i18n.t("Searching the web for \"{{query}}\"...") -->
					<!-- $i18n.t("Fetching web pages...") -->
					<!-- $i18n.t("Searching the Groene Kennisnet knowledge base for \"{{query}}\"...") -->
					<!-- $i18n.t("Fetching and reading web pages...") -->
					<!-- $i18n.t("Researching files for \"{{query}}\"...") -->
					<!-- $i18n.t("Researching the web for \"{{query}}\"...") -->
					<!-- $i18n.t("{{count}} results in {{source}}") -->
					<!-- $i18n.t("Searches planned") -->
					<!-- $i18n.t("Analyzing the question...") -->
					<!-- $i18n.t("Planning the search strategy...") -->
					<!-- $i18n.t("Searching the documents...") -->
					<!-- $i18n.t("Assessing the gathered information...") -->
					{#if status?.description}
						{$i18n.t(
							status.description,
							/* statusI18nParams strips `query` and `count` (both reserved for
							   per-action branches). Re-add them here for templates in the
							   generic branch that use them as placeholders. */
							{ ...statusI18nParams(status), query: status?.query, count: status?.count }
						)}
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
