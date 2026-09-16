<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { CitationDocument } from './citationDocuments';
	const i18n = getContext<Readable<I18n>>('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import { settings, config } from '$lib/stores';
	import { injectCsp } from '$lib/utils/csp';
	import {
		calculatePercentage,
		getRelevanceColor,
		getTextFragmentUrl
	} from './useCitationDocument';
	export let mergedDocuments: CitationDocument[] = [];
	export let showPercentage = false;
	export let showRelevance = true;
	const CONTENT_PREVIEW_LIMIT = 10000;
	export let expandedDocs: Set<number> = new Set();
</script>

{#each mergedDocuments as document, documentIdx}
	<div class="flex flex-col w-full gap-2">
		{#if document.metadata?.parameters}
			<div>
				<div class="text-sm font-medium dark:text-gray-300 mb-1">
					{$i18n.t('Parameters')}
				</div>

				<Textarea readonly value={JSON.stringify(document.metadata.parameters, null, 2)}></Textarea>
			</div>
		{/if}

		<div>
			<div class=" text-sm font-medium dark:text-gray-300 flex items-center gap-2 w-fit mb-1">
				{#if document.source?.url?.includes('http')}
					{@const snippetUrl = getTextFragmentUrl(document)}
					{#if snippetUrl}
						<a
							href={snippetUrl}
							target="_blank"
							class="underline hover:text-gray-500 dark:hover:text-gray-100">{$i18n.t('Content')}</a
						>
					{:else}
						{$i18n.t('Content')}
					{/if}
				{:else}
					{$i18n.t('Content')}
				{/if}

				{#if showRelevance && document.distance !== undefined}
					<Tooltip
						className="w-fit"
						content={$i18n.t('Relevance')}
						placement="top-start"
						tippyOptions={{ duration: [500, 0] }}
					>
						<div class="text-sm my-1 dark:text-gray-400 flex items-center gap-2 w-fit">
							{#if showPercentage}
								{@const percentage = calculatePercentage(document.distance)}

								{#if typeof percentage === 'number'}
									<span class={`px-1 rounded-sm font-medium ${getRelevanceColor(percentage)}`}>
										{percentage.toFixed(2)}%
									</span>
								{/if}
							{:else if typeof document?.distance === 'number'}
								<span class="text-gray-500 dark:text-gray-500">
									({(document?.distance ?? 0).toFixed(4)})
								</span>
							{/if}
						</div>
					</Tooltip>
				{/if}

				{#if Number.isInteger(document?.metadata?.page)}
					<span class="text-sm text-gray-500 dark:text-gray-400">
						({$i18n.t('page')}
						{Number(document.metadata?.page) + 1})
					</span>
				{/if}
			</div>

			{#if document.metadata?.html}
				<iframe
					class="w-full border-0 h-auto rounded-none"
					sandbox="{($settings?.iframeSandboxAllowScripts ?? true)
						? 'allow-scripts'
						: ''}{($settings?.iframeSandboxAllowForms ?? true)
						? ' allow-forms'
						: ''}{($settings?.iframeSandboxAllowDownloads ?? true)
						? ' allow-downloads'
						: ''}{($settings?.iframeSandboxAllowSameOrigin ?? false) ? ' allow-same-origin' : ''}"
					srcdoc={injectCsp(document.document ?? '', $config?.ui?.iframe_csp ?? '')}
					title={$i18n.t('Content')}
				></iframe>
			{:else}
				{@const rawContent = (document.document ?? '').trim().replace(/\n\n+/g, '\n\n')}
				{@const isTruncated =
					($settings?.renderMarkdownInPreviews ?? true) &&
					rawContent.length > CONTENT_PREVIEW_LIMIT &&
					!expandedDocs.has(documentIdx)}
				{#if $settings?.renderMarkdownInPreviews ?? true}
					<div
						class="text-sm prose dark:prose-invert max-w-full
							prose-h1:text-xl prose-h1:font-semibold prose-h1:mt-3 prose-h1:mb-1.5
							prose-h2:text-base prose-h2:font-semibold prose-h2:mt-2 prose-h2:mb-1
							prose-h3:text-base prose-h3:font-medium prose-h3:mt-2 prose-h3:mb-1
							prose-h4:text-base prose-h4:font-medium prose-h4:mt-2 prose-h4:mb-1
							prose-h5:text-base prose-h5:font-medium prose-h5:mt-2 prose-h5:mb-1
							prose-h6:text-base prose-h6:font-medium prose-h6:mt-2 prose-h6:mb-1"
					>
						<Markdown
							content={isTruncated ? rawContent.slice(0, CONTENT_PREVIEW_LIMIT) : rawContent}
							id="citation-{documentIdx}"
						/>
					</div>
					{#if isTruncated}
						<button
							class="mt-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
							on:click={() => {
								expandedDocs.add(documentIdx);
								expandedDocs = expandedDocs;
							}}
						>
							{$i18n.t('Show all ({{COUNT}} characters)', {
								COUNT: rawContent.length.toLocaleString()
							})}
						</button>
					{/if}
				{:else}
					<pre class="text-sm dark:text-gray-400 whitespace-pre-line">{rawContent}</pre>
				{/if}
			{/if}
		</div>
	</div>
{/each}
