<script lang="ts">
	import type { AgentDocument } from '$lib/utils/agentDocument';
	import type { RawSource } from './Messages/Citations/reduceSources';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';
	import { onMount, onDestroy, getContext } from 'svelte';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;
	const i18n = getContext<Writable<i18nType>>('i18n');

	import {
		chatId,
		config,
		showControls,
		showDocument,
		documentContents,
		selectedDocumentIndex,
		requestedWorkspaceFile
	} from '$lib/stores';
	import { copyToClipboard } from '$lib/utils';
	import { printDocument } from '$lib/utils/documentPrint';
	import { documentPdf } from '$lib/utils/documentPdf';

	import ContentRenderer from './Messages/ContentRenderer.svelte';
	import HtmlDocumentFrame from './Messages/HtmlDocumentFrame.svelte';
	import Citations from './Messages/SoevCitations.svelte';
	import { getExportMarkdown as exportMarkdown } from '$lib/utils/documentCitations';
	import Download from '../icons/Download.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Dropdown from '../common/Dropdown.svelte';
	import PptxPreview from '../common/PptxPreview.svelte';
	import FileKindIcon from './FileKindIcon.svelte';
	import {
		type DeliveredFile,
		downloadDeliveredFile,
		fileKind,
		newestFileVersionIndex,
		loadPages,
		isTextPreview,
		isHtmlPage
	} from '$lib/utils/deliveredFile';
	import DeliveredTextPreview from './DeliveredTextPreview.svelte';
	import DeliveredHtmlFrame from './DeliveredHtmlFrame.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import { groupDocuments } from '$lib/utils/documentGroups';

	export let overlay = false;

	let contents: Array<
		AgentDocument & { sources?: RawSource[]; messageId?: string; file?: DeliveredFile }
	> = [];
	let copied = false;
	let downloadOpen = false;
	let citationsElement: Citations | null = null;

	$: if ($requestedWorkspaceFile) {
		const newest = newestFileVersionIndex(contents, $requestedWorkspaceFile);
		if (newest >= 0) {
			$selectedDocumentIndex = newest;
			requestedWorkspaceFile.set(null);
		}
	}

	$: current = contents[$selectedDocumentIndex];
	// [Gradient] Shared snapshots have no workspace file download route.
	$: sharedChat = $page.url.pathname.startsWith('/s/');

	// [Gradient] A delivered workspace file shows its rendered pages, loaded with the user's token.
	let pages: string[] = [];
	let currentPage = 0;
	let pagesError = false;
	let pagesFor: DeliveredFile | undefined;
	$: previewFile = sharedChat ? undefined : current?.file;
	$: if (previewFile !== pagesFor) showPages(previewFile);

	const showPages = async (file: DeliveredFile | undefined) => {
		pagesFor = file;
		pages.forEach((url) => URL.revokeObjectURL(url));
		pages = [];
		currentPage = 0;
		pagesError = false;
		if (!file) return;
		try {
			const loaded = await loadPages(file, $chatId);
			if (pagesFor !== file) return loaded.forEach((url) => URL.revokeObjectURL(url));
			pages = loaded;
		} catch (e) {
			console.error(e);
			pagesError = true;
		}
	};

	onDestroy(() => pages.forEach((url) => URL.revokeObjectURL(url)));

	const downloadFile = async () => {
		if (!current?.file || sharedChat) return;
		try {
			await downloadDeliveredFile(current.file, $chatId);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to download file'));
		}
	};
	let documentMenuOpen = false;

	// Different documents are picked from the title menu; the arrows page through one document's versions.
	$: groups = groupDocuments(contents);
	// [Gradient] The picker lists the newest document first, each with its type.
	$: newestFirst = [...groups].reverse();
	const kindOf = (index: number): string => {
		const content = contents[index];
		return content?.file ? fileKind(content.file) : $i18n.t('Text');
	};
	$: currentGroup = groups.find((group) => group.indices.includes($selectedDocumentIndex));
	$: versions = currentGroup?.indices ?? [];
	$: versionPosition = versions.indexOf($selectedDocumentIndex);
	// [Gradient] A version switch closes the download menu; its options depend on the format.
	$: ($selectedDocumentIndex, (downloadOpen = false), (documentMenuOpen = false));

	function navigateContent(direction: 'prev' | 'next') {
		const position =
			direction === 'prev'
				? Math.max(versionPosition - 1, 0)
				: Math.min(versionPosition + 1, versions.length - 1);
		$selectedDocumentIndex = versions[position] ?? $selectedDocumentIndex;
	}

	function selectDocument(indices: number[]) {
		$selectedDocumentIndex = indices[indices.length - 1];
		documentMenuOpen = false;
	}

	const sanitizeFilename = (name: string) => {
		const cleaned = (name || 'document').replace(/[\\/:*?"<>|]+/g, '_').trim();
		return cleaned.length > 0 ? cleaned : 'document';
	};

	const getExportMarkdown = () => {
		if (!current) return '';
		return current.format === 'html'
			? current.content
			: exportMarkdown(current.content, current.sources ?? [], $i18n.t('Sources'));
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
		downloadOpen = false;
		try {
			const blob = await documentPdf(current, current.sources ?? [], $i18n.t('Sources'));
			saveAs(blob, `${sanitizeFilename(current.title)}.pdf`);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to export PDF'));
		}
	};

	const print = async () => {
		if (!current) return;
		// Close first: the print dialog blocks the page, and the menu must not outlive it.
		downloadOpen = false;
		try {
			await printDocument(current.title, getExportMarkdown(), current.format, current.page);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to export PDF'));
		}
	};

	const downloadDocx = async () => {
		if (!current) return;
		try {
			const { buildDocumentDocx } = await import('$lib/utils/documentDocx');
			const blob = await buildDocumentDocx(
				current.title,
				current.content,
				current.sources ?? [],
				$i18n.t('Sources')
			);
			saveAs(blob, `${sanitizeFilename(current.title)}.docx`);
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
					$selectedDocumentIndex = 0;
				}
			} else {
				hadContents = true;
				if (newContents.length > contents.length) {
					$selectedDocumentIndex = newContents.length - 1;
				} else if ($selectedDocumentIndex >= newContents.length) {
					$selectedDocumentIndex = Math.max(newContents.length - 1, 0);
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
						{#if versions.length > 1}
							<div class="flex items-center gap-0.5 self-center min-w-fit" dir="ltr">
								<button
									class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md transition disabled:cursor-not-allowed"
									on:click={() => navigateContent('prev')}
									disabled={versionPosition <= 0}
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
										selectedVersion: versionPosition + 1,
										totalVersions: versions.length
									})}
								</div>

								<button
									class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md transition disabled:cursor-not-allowed"
									on:click={() => navigateContent('next')}
									disabled={versionPosition >= versions.length - 1}
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
						{/if}

						{#if groups.length > 1}
							<Dropdown
								bind:show={documentMenuOpen}
								contentClass="select-none min-w-[240px] max-w-[min(28rem,90vw)] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
							>
								<button
									class="flex items-center gap-1.5 min-w-0 px-1.5 py-0.5 -mx-1.5 rounded-md hover:bg-black/5 dark:hover:bg-white/5 transition"
									aria-label={$i18n.t('Switch document')}
									title={current.title}
								>
									<FileKindIcon kind={kindOf($selectedDocumentIndex)} />
									<span class="text-sm font-medium truncate text-gray-900 dark:text-white">
										{current.title || $i18n.t('Document')}
									</span>
									<ChevronDown className="size-3 shrink-0" strokeWidth="2.5" />
								</button>

								<div slot="content">
									{#each newestFirst as group}
										<button
											class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full text-left"
											class:font-medium={group === currentGroup}
											on:click={() => selectDocument(group.indices)}
										>
											<FileKindIcon kind={kindOf(group.indices[group.indices.length - 1])} />
											<span class="flex-1 truncate">{group.title || $i18n.t('Document')}</span>
											{#if group.indices.length > 1}
												<span class="shrink-0 text-xs text-gray-500">
													{$i18n.t('{{count}} versions', { count: group.indices.length })}
												</span>
											{/if}
										</button>
									{/each}
								</div>
							</Dropdown>
						{:else if current.title}
							<div
								class="text-sm font-medium truncate text-gray-900 dark:text-white"
								title={current.title}
							>
								{current.title}
							</div>
						{/if}
					</div>

					{#if current.file && !sharedChat}
						<div class="flex items-center gap-1.5 shrink-0">
							<Tooltip content={$i18n.t('Download')}>
								<button
									class="bg-none border-none text-xs bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 transition rounded-md p-0.5"
									aria-label={$i18n.t('Download')}
									on:click={downloadFile}
								>
									<Download className="size-3.5" />
								</button>
							</Tooltip>
						</div>
					{:else if !current.file}
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

							{#key `${$selectedDocumentIndex}-${current.format}`}
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
										{#if current.format === 'markdown'}
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
										{/if}
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
											on:click={print}
										>
											<div class="flex items-center line-clamp-1">
												{$i18n.t('Print')}
											</div>
										</button>
										{#if current.format === 'markdown' && ($config?.features?.enable_docx_export ?? true)}
											<button
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
							{/key}
						</div>
					{/if}
				</div>
			</div>
		{/if}

		{#if overlay}
			<div class="absolute top-0 left-0 right-0 bottom-0 z-10"></div>
		{/if}

		<div class="flex-1 w-full h-full overflow-y-auto">
			<div class="h-full flex flex-col">
				{#if contents.length > 0 && current?.file}
					{#if sharedChat}
						<p class="m-auto text-xs text-gray-500 dark:text-gray-400">
							{$i18n.t('Files made in the workspace are not available in shared chats')}
						</p>
					{:else if pages.length > 0}
						<PptxPreview
							slides={pages}
							bind:currentSlide={currentPage}
							className="h-full"
							itemLabel={fileKind(current.file) === 'PPTX' ? $i18n.t('Slide') : $i18n.t('Page')}
							listLabel={fileKind(current.file) === 'PPTX' ? $i18n.t('Slides') : $i18n.t('Pages')}
						/>
					{:else if current.file.pages === 0 && isHtmlPage(current.file)}
						<DeliveredHtmlFrame file={current.file} chatId={$chatId} />
					{:else if current.file.pages === 0 && isTextPreview(current.file)}
						<DeliveredTextPreview file={current.file} chatId={$chatId} />
					{:else if current.file.pages === 0}
						<p class="m-auto text-xs text-gray-500 dark:text-gray-400">
							{$i18n.t('No preview available. Download the file to open it.')}
						</p>
					{:else if pagesError}
						<div class="m-auto text-xs text-red-500">
							{$i18n.t('Failed to load the preview. Download the file instead.')}
						</div>
					{/if}
				{:else if contents.length > 0 && current}
					{#if current.format === 'html'}
						{#key $selectedDocumentIndex}
							<HtmlDocumentFrame
								content={current.content}
								title={current.title}
								page={current.page}
								done={current.done}
							/>
						{/key}
					{:else}
						<div class="max-w-3xl w-full mx-auto px-6 py-6 prose dark:prose-invert">
							<ContentRenderer
								id={`document-${$chatId ?? 'preview'}-${$selectedDocumentIndex}`}
								messageId={`document-${$selectedDocumentIndex}`}
								history={undefined}
								content={current.content}
								done={current.done}
								editCodeBlock={false}
								sources={current.sources}
								floatingButtons={false}
								onSourceClick={(id: string | number) => citationsElement?.showSourceModal(id)}
							/>
						</div>
					{/if}
					{#if current.format === 'markdown' && (current.sources ?? []).length > 0}
						<!-- The message's own id lets the sources panel open that question's group. -->
						<Citations
							bind:this={citationsElement}
							id={current.messageId ?? ''}
							chatId={$chatId ?? ''}
							sources={current.sources}
							listed={false}
						/>
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
