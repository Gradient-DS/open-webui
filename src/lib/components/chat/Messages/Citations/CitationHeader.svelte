<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { CitationDocument } from './citationDocuments';
	const i18n = getContext<Readable<I18n>>('i18n');

	import type { DisplayCitation } from './reduceSources';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ArrowTopRightOnSquare from '$lib/components/icons/ArrowTopRightOnSquare.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { decodeString } from './useCitationDocument';
	export let citation: DisplayCitation | null = null;
	export let mergedDocuments: CitationDocument[] = [];
	export let previewAvailable = true;
	export let externalUrl: string | null = null;
</script>

<!-- [Gradient] Let long source titles shrink while the adjacent actions keep their width. -->
<div class="text-lg font-medium self-center flex-1 flex items-center gap-1.5 min-w-0">
	{#if citation?.source?.name}
		{@const document = mergedDocuments?.[0]}
		{@const docFileId = document?.metadata?.file_id}
		{#if docFileId || externalUrl}
			{@const isFileMissing = !!docFileId && !previewAvailable}
			{@const linksToFile = !!docFileId && !isFileMissing}
			<Tooltip
				className="w-fit min-w-0"
				content={isFileMissing && !externalUrl
					? $i18n.t('File no longer available')
					: linksToFile
						? $i18n.t('Open file')
						: $i18n.t('Open link')}
				placement="top-start"
				tippyOptions={{ duration: [500, 0] }}
			>
				{#if isFileMissing && !externalUrl}
					<span class="block min-w-0 truncate text-gray-500 dark:text-gray-400 cursor-not-allowed">
						{decodeString(citation?.source?.name)}
					</span>
				{:else}
					<a
						class="hover:text-gray-500 dark:hover:text-gray-100 underline block min-w-0 truncate"
						href={linksToFile
							? `${WEBUI_API_BASE_URL}/files/${docFileId}/content${document?.metadata?.page !== undefined ? `#page=${Number(document.metadata.page) + 1}` : ''}`
							: (externalUrl ?? `#`)}
						target="_blank"
						rel="noreferrer"
					>
						{decodeString(citation?.source?.name)}
					</a>
				{/if}
			</Tooltip>
			{#if externalUrl && linksToFile}
				<!-- Original-page link, shown alongside the file download only
				     when the title already points at the file (avoids a
				     redundant icon for plain web/fetch citations, where the
				     title itself links to the external URL). -->
				<Tooltip
					className="w-fit shrink-0"
					content={$i18n.t('Open original page')}
					placement="top-start"
					tippyOptions={{ duration: [500, 0] }}
				>
					<a
						class="shrink-0 text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-200"
						href={externalUrl}
						target="_blank"
						rel="noreferrer"
						aria-label={$i18n.t('Open original page')}
					>
						<ArrowTopRightOnSquare className="size-4" />
					</a>
				</Tooltip>
			{/if}
		{:else}
			<span class="min-w-0 truncate">{decodeString(citation?.source?.name)}</span>
		{/if}
	{:else}
		{$i18n.t('Citation')}
	{/if}
</div>
<slot name="actions" />
