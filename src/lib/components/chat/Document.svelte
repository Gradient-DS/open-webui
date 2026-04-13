<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;
	const i18n = getContext('i18n');

	import { chatId, showControls, showDocument, documentContents } from '$lib/stores';
	import { copyToClipboard } from '$lib/utils';
	import { exportDocumentAsPdf, exportDocumentAsDocx } from '$lib/apis/utils';

	import ContentRenderer from './Messages/ContentRenderer.svelte';
	import Citations from './Messages/Citations.svelte';
	import { normalizeCitations, formatSourcesAsMarkdown } from '$lib/utils/citations';
	import Download from '../icons/Download.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Dropdown from '../common/Dropdown.svelte';

	export let overlay = false;

	let contents: Array<{
		title: string;
		markdown: string;
		sources?: any[];
	}> = [];
	let selectedContentIdx = 0;
	let copied = false;
	let downloadOpen = false;
	let citationsElement: any = null;

	$: current = contents[selectedContentIdx];

	function navigateContent(direction: 'prev' | 'next') {
		selectedContentIdx =
			direction === 'prev'
				? Math.max(selectedContentIdx - 1, 0)
				: Math.min(selectedContentIdx + 1, contents.length - 1);
	}

	const sanitizeFilename = (name: string) => {
		const cleaned = (name || 'document').replace(/[\\/:*?"<>|]+/g, '_').trim();
		return cleaned.length > 0 ? cleaned : 'document';
	};

	// Build the content to export: normalize [N] citations in the markdown and append a
	// source appendix using the same utility as the message-level copy button.
	// The [N] marker format ensures Word/pandoc won't renumber it as a continuation of a
	// preceding numbered list in the body.
	const getExportMarkdown = () => {
		if (!current) return '';
		const sources = current.sources ?? [];
		if (sources.length === 0) return current.markdown;
		const { content, sourceList } = normalizeCitations(current.markdown, sources);
		if (sourceList.length === 0) return current.markdown;
		return `${content}\n\n---\n\n${formatSourcesAsMarkdown(sourceList)}\n`;
	};

	const downloadMd = () => {
		if (!current) return;
		saveAs(
			new Blob([getExportMarkdown()], { type: 'text/markdown;charset=utf-8' }),
			`${sanitizeFilename(current.title)}.md`
		);
		downloadOpen = false;
	};

	const downloadTxt = () => {
		if (!current) return;
		saveAs(
			new Blob([getExportMarkdown()], { type: 'text/plain;charset=utf-8' }),
			`${sanitizeFilename(current.title)}.txt`
		);
		downloadOpen = false;
	};

	const downloadPdf = async () => {
		if (!current) return;
		try {
			const blob = await exportDocumentAsPdf(
				localStorage.token,
				current.title,
				getExportMarkdown()
			);
			if (blob) saveAs(blob, `${sanitizeFilename(current.title)}.pdf`);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to export PDF'));
		}
		downloadOpen = false;
	};

	const downloadDocx = async () => {
		if (!current) return;
		try {
			const blob = await exportDocumentAsDocx(
				localStorage.token,
				current.title,
				getExportMarkdown()
			);
			if (blob) saveAs(blob, `${sanitizeFilename(current.title)}.docx`);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to export Word document'));
		}
		downloadOpen = false;
	};

	onMount(() => {
		let hadContents = false;
		const unsubscribe = documentContents.subscribe((value) => {
			const newContents = value ?? [];

			if (newContents.length === 0) {
				if (hadContents) {
					showControls.set(false);
					showDocument.set(false);
					selectedContentIdx = 0;
				}
			} else {
				hadContents = true;
				if (newContents.length > contents.length) {
					selectedContentIdx = newContents.length - 1;
				} else if (selectedContentIdx >= newContents.length) {
					selectedContentIdx = Math.max(newContents.length - 1, 0);
				}
			}

			contents = newContents;
		});

		return () => {
			unsubscribe();
		};
	});
</script>

<div class="w-full h-full relative flex flex-col bg-white dark:bg-gray-850" id="document-container">
	<div class="w-full h-full flex flex-col flex-1 relative">
		{#if contents.length > 0 && current}
			<div
				class="pointer-events-auto z-20 flex justify-between items-center p-2.5 font-primary text-gray-900 dark:text-white"
			>
				<div class="flex-1 flex items-center justify-between pr-1 min-w-0">
					<div class="flex items-center space-x-2 min-w-0">
						<div class="flex items-center gap-0.5 self-center min-w-fit" dir="ltr">
							<button
								class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md transition disabled:cursor-not-allowed"
								on:click={() => navigateContent('prev')}
								disabled={contents.length <= 1}
								aria-label={$i18n.t('Previous')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke="currentColor"
									stroke-width="2.5"
									class="size-3.5"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="M15.75 19.5 8.25 12l7.5-7.5"
									/>
								</svg>
							</button>

							<div class="text-xs self-center dark:text-gray-100 min-w-fit">
								{$i18n.t('Version {{selectedVersion}} of {{totalVersions}}', {
									selectedVersion: selectedContentIdx + 1,
									totalVersions: contents.length
								})}
							</div>

							<button
								class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md transition disabled:cursor-not-allowed"
								on:click={() => navigateContent('next')}
								disabled={contents.length <= 1}
								aria-label={$i18n.t('Next')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke="currentColor"
									stroke-width="2.5"
									class="size-3.5"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="m8.25 4.5 7.5 7.5-7.5 7.5"
									/>
								</svg>
							</button>
						</div>

						{#if current.title}
							<div
								class="text-sm font-medium truncate text-gray-900 dark:text-white"
								title={current.title}
							>
								{current.title}
							</div>
						{/if}
					</div>

					<div class="flex items-center gap-1.5 shrink-0">
						<button
							class="copy-code-button bg-none border-none text-xs bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 transition rounded-md px-1.5 py-0.5 whitespace-nowrap"
							on:click={() => {
								copyToClipboard(getExportMarkdown(), null, true);
								copied = true;
								setTimeout(() => {
									copied = false;
								}, 2000);
							}}>{copied ? $i18n.t('Copied') : $i18n.t('Copy')}</button
						>

						<Dropdown
							bind:show={downloadOpen}
							align="end"
							contentClass="select-none min-w-[180px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
						>
							<Tooltip content={$i18n.t('Download')}>
								<button
									class="bg-none border-none text-xs bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 transition rounded-md p-0.5"
									aria-label={$i18n.t('Download')}
								>
									<Download className="size-3.5" />
								</button>
							</Tooltip>

							<div slot="content">
								<button
									class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
									on:click={downloadMd}
								>
									<div class="flex items-center line-clamp-1">
										{$i18n.t('Markdown (.md)')}
									</div>
								</button>
								<button
									class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
									on:click={downloadTxt}
								>
									<div class="flex items-center line-clamp-1">
										{$i18n.t('Plain text (.txt)')}
									</div>
								</button>
								<button
									class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
									on:click={downloadPdf}
								>
									<div class="flex items-center line-clamp-1">
										{$i18n.t('PDF document (.pdf)')}
									</div>
								</button>
								<button
									class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
									on:click={downloadDocx}
								>
									<div class="flex items-center line-clamp-1">
										{$i18n.t('Word document (.docx)')}
									</div>
								</button>
							</div>
						</Dropdown>
					</div>
				</div>
			</div>
		{/if}

		{#if overlay}
			<div class="absolute top-0 left-0 right-0 bottom-0 z-10"></div>
		{/if}

		<div class="flex-1 w-full h-full overflow-y-auto">
			<div class="h-full flex flex-col">
				{#if contents.length > 0 && current}
					<div class="max-w-3xl w-full mx-auto px-6 py-6 prose dark:prose-invert">
						<ContentRenderer
							id={`document-${$chatId ?? 'preview'}-${selectedContentIdx}`}
							content={current.markdown}
							done={true}
							editCodeBlock={false}
							sources={current.sources}
							floatingButtons={false}
							onSourceClick={((id: any) => citationsElement?.showSourceModal(id)) as any}
						/>
					</div>
					{#if (current.sources ?? []).length > 0}
						<div class="max-w-3xl w-full mx-auto px-6 pb-6">
							<Citations
								bind:this={citationsElement}
								id={`document-${$chatId ?? 'preview'}-${selectedContentIdx}`}
								chatId={$chatId ?? ''}
								sources={current.sources}
							/>
						</div>
					{/if}
				{:else}
					<div class="m-auto font-medium text-xs text-gray-900 dark:text-white">
						{$i18n.t('No document content found.')}
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>
