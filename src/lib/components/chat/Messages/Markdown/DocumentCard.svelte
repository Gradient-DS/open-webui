<script lang="ts">
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { page as appPage } from '$app/stores';
	import { toast } from 'svelte-sonner';
	import { getContext, tick } from 'svelte';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;
	const i18n = getContext<Writable<i18nType>>('i18n');

	import {
		config,
		showControls,
		showDocument,
		openDocumentTabSignal,
		documentContents,
		selectedDocumentIndex,
		requestedOfficeFile
	} from '$lib/stores';
	import { getExportMarkdown } from '$lib/utils/documentCitations';
	import { documentPdf } from '$lib/utils/documentPdf';

	import type { DocumentFormat } from '$lib/utils/agentDocument';
	import Document from '$lib/components/icons/Document.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { type DeliveredFile, downloadDeliveredFile, fileKind } from '$lib/utils/deliveredFile';

	export let id: string = '';
	export let title: string = '';
	export let content: string = '';
	export let format: DocumentFormat = 'markdown';
	export let page: string | undefined = undefined;
	export let done: boolean = true;
	export let messageDone: boolean = true;
	export let className: string = '';
	// [Gradient] An Office file an agent delivered: downloaded as is, not exported from markdown.
	export let file: DeliveredFile | null = null;
	export let chatId: string = '';

	// [Gradient] Shared snapshots have no Office download route.
	$: sharedChat = $appPage.url.pathname.startsWith('/s/');
	let downloadOpen = false;

	const downloadFile = async () => {
		if (!file || sharedChat) return;
		try {
			await downloadDeliveredFile(file, chatId);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to download file'));
		}
	};

	$: isExecuting = !done || !messageDone;
	$: displayTitle = title || $i18n.t('Document');

	const openDocument = async () => {
		if (file) {
			// [Gradient] Document.svelte selects the newest version of this file.
			requestedOfficeFile.set(file);
			showDocument.set(true);
			showControls.set(true);
			openDocumentTabSignal.update((n) => n + 1);
			return;
		}
		const index = ($documentContents ?? []).findIndex(
			(document) =>
				document.title === title && document.content === content && document.format === format
		);
		showDocument.set(true);
		showControls.set(true);
		openDocumentTabSignal.update((n) => n + 1);
		await tick();
		if (index !== -1) selectedDocumentIndex.set(index);
	};

	const sanitizeFilename = (name: string) => {
		const cleaned = (name || 'document').replace(/[\\/:*?"<>|]+/g, '_').trim();
		return cleaned.length > 0 ? cleaned : 'document';
	};

	$: sources =
		($documentContents ?? []).find(
			(document) =>
				document.title === title && document.content === content && document.format === format
		)?.sources ?? [];
	$: exportContent =
		format === 'markdown' ? getExportMarkdown(content, sources, $i18n.t('Sources')) : content;

	const downloadMd = () => {
		if (!content) return;
		saveAs(
			new Blob([exportContent], { type: 'text/markdown;charset=utf-8' }),
			`${sanitizeFilename(displayTitle)}.md`
		);
		downloadOpen = false;
	};

	const downloadTxt = () => {
		if (!content) return;
		saveAs(
			new Blob([exportContent], { type: 'text/plain;charset=utf-8' }),
			`${sanitizeFilename(displayTitle)}.txt`
		);
		downloadOpen = false;
	};

	const downloadPdf = async () => {
		if (!content) return;
		downloadOpen = false;
		try {
			const blob = await documentPdf(
				{ title: displayTitle, content, format, page },
				sources,
				$i18n.t('Sources')
			);
			saveAs(blob, `${sanitizeFilename(displayTitle)}.pdf`);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to export PDF'));
		}
	};

	const downloadDocx = async () => {
		if (!content) return;
		try {
			const { buildDocumentDocx } = await import('$lib/utils/documentDocx');
			const blob = await buildDocumentDocx(displayTitle, content, sources, $i18n.t('Sources'));
			saveAs(blob, `${sanitizeFilename(displayTitle)}.docx`);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to export Word document'));
		}
		downloadOpen = false;
	};
</script>

{#if file && sharedChat}
	<p class="text-xs text-gray-500 dark:text-gray-400">
		{$i18n.t('Office files are not available in shared chats')}
	</p>
{:else}
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div
		{id}
		class="{className} group relative flex items-center gap-3 w-full max-w-md p-2.5 pr-2 rounded-2xl border border-gray-100 dark:border-gray-800/60 bg-white dark:bg-gray-850 hover:bg-gray-50 dark:hover:bg-gray-800/60 transition cursor-pointer"
		role="button"
		tabindex="0"
		aria-label={$i18n.t('Open document: {{title}}', { title: displayTitle })}
		on:click={openDocument}
		on:keydown={(e) => {
			if (e.key === 'Enter' || e.key === ' ') {
				e.preventDefault();
				openDocument();
			}
		}}
	>
		<div
			class="shrink-0 flex items-center justify-center size-10 rounded-xl bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
		>
			<Document className="size-5" strokeWidth="1.5" />
		</div>

		<div class="flex-1 min-w-0 {isExecuting ? 'shimmer' : ''}">
			<div class="text-sm font-medium text-gray-900 dark:text-white line-clamp-1">
				{displayTitle}
			</div>
			<div class="text-xs text-gray-500 dark:text-gray-400 line-clamp-1">
				{#if file}
					{fileKind(file)}
					{#if file.version > 1}
						<span class="ml-1">{$i18n.t('v{{version}}', { version: file.version })}</span>
					{/if}
				{:else}
					{format === 'html' ? $i18n.t('HTML') : $i18n.t('Markdown')} · {isExecuting
						? $i18n.t('Writing document...')
						: $i18n.t('Done')}
				{/if}
			</div>
		</div>

		{#if file}
			<div class="shrink-0" on:click|stopPropagation on:keydown|stopPropagation>
				<button
					type="button"
					class="flex items-center gap-1.5 text-xs font-medium bg-gray-50 hover:bg-gray-100 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 transition rounded-full px-3 py-1.5"
					aria-label={$i18n.t('Download')}
					on:click={downloadFile}
				>
					<Download className="size-3.5" strokeWidth="2" />
					<span>{$i18n.t('Download')}</span>
				</button>
			</div>
		{:else if !isExecuting && content}
			<div class="shrink-0" on:click|stopPropagation on:keydown|stopPropagation>
				<Dropdown
					bind:show={downloadOpen}
					align="end"
					contentClass="select-none min-w-[200px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
				>
					<Tooltip content={$i18n.t('Download')}>
						<button
							type="button"
							class="flex items-center gap-1.5 text-xs font-medium bg-gray-50 hover:bg-gray-100 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 transition rounded-full px-3 py-1.5"
							aria-label={$i18n.t('Download')}
						>
							<Download className="size-3.5" strokeWidth="2" />
							<span>{$i18n.t('Download')}</span>
						</button>
					</Tooltip>

					<div slot="content">
						{#if format === 'markdown'}
							<button
								type="button"
								class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								on:click={downloadMd}
							>
								<div class="flex items-center line-clamp-1">
									{$i18n.t('Markdown (.md)')}
								</div>
							</button>
							<button
								type="button"
								class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								on:click={downloadTxt}
							>
								<div class="flex items-center line-clamp-1">
									{$i18n.t('Plain text (.txt)')}
								</div>
							</button>
						{/if}
						<button
							type="button"
							class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
							on:click={downloadPdf}
						>
							<div class="flex items-center line-clamp-1">
								{$i18n.t('PDF document (.pdf)')}
							</div>
						</button>
						{#if format === 'markdown' && ($config?.features?.enable_docx_export ?? true)}
							<button
								type="button"
								class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								on:click={downloadDocx}
							>
								<div class="flex items-center line-clamp-1">
									{$i18n.t('Word document (.docx)')}
								</div>
							</button>
						{/if}
					</div>
				</Dropdown>
			</div>
		{/if}
	</div>
{/if}
