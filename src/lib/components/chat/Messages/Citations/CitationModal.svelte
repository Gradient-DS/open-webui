<script lang="ts">
	import { getContext, tick } from 'svelte';
	import type { WorkBook } from 'xlsx';
	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import PDFViewer from '$lib/components/common/PDFViewer.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { settings } from '$lib/stores';
	import { getFileContentById } from '$lib/apis/files';
	import { renderDocxHtml, readWorkbook, renderSheetHtml } from '$lib/utils/officePreview';
	import { highlightDocx, scrollToFirstDocxHighlight } from '$lib/utils/citationDomHighlight';

	import XMark from '$lib/components/icons/XMark.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';

	const i18n = getContext('i18n');

	const CONTENT_PREVIEW_LIMIT = 10000;
	const SNIPPET_TRUNCATE = 200;
	let expandedDocs: Set<number> = new Set();

	export let show = false;
	export let citation;
	export let showPercentage = false;
	export let showRelevance = true;

	let mergedDocuments = [];
	let selectedTab = 'preview';
	let previewAvailable = true;

	// Active snippet (left rail) — drives the highlight in the right viewer.
	let activeSnippetIdx = 0;

	// PDF viewer instance — snippet switches call setHighlight() on it rather
	// than re-rendering the viewer.
	let pdfViewerRef: PDFViewer;

	// DOCX rendered-HTML state.
	let docxHtml = '';
	let docxContainer: HTMLDivElement;
	let officeLoading = false;
	let officeError = false;

	// XLSX workbook state.
	let xlsxWorkbook: WorkBook | null = null;
	let xlsxSheetNames: string[] = [];
	let selectedSheet = '';
	let xlsxHtml = '';

	const truncate = (text: string, limit: number): string =>
		text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;

	function calculatePercentage(distance: number) {
		if (typeof distance !== 'number') return null;
		if (distance < 0) return 0;
		if (distance > 1) return 100;
		return Math.round(distance * 10000) / 100;
	}

	function getRelevanceColor(percentage: number) {
		if (percentage >= 80)
			return 'bg-green-200 dark:bg-green-800 text-green-800 dark:text-green-200';
		if (percentage >= 60)
			return 'bg-yellow-200 dark:bg-yellow-800 text-yellow-800 dark:text-yellow-200';
		if (percentage >= 40)
			return 'bg-orange-200 dark:bg-orange-800 text-orange-800 dark:text-orange-200';
		return 'bg-red-200 dark:bg-red-800 text-red-800 dark:text-red-200';
	}

	$: if (citation) {
		expandedDocs = new Set();
		selectedTab = 'preview';
		activeSnippetIdx = 0;
		mergedDocuments = citation.document?.map((c, i) => {
			return {
				source: citation.source,
				document: c,
				metadata: citation.metadata?.[i],
				distance: citation.distances?.[i]
			};
		});
		if (mergedDocuments.every((doc) => doc.distance !== undefined)) {
			mergedDocuments = mergedDocuments.sort(
				(a, b) => (b.distance ?? Infinity) - (a.distance ?? Infinity)
			);
		}
	}

	// File type detection from first document's metadata
	$: fileName = mergedDocuments?.[0]?.metadata?.name ?? citation?.source?.name ?? '';
	$: fileId = mergedDocuments?.[0]?.metadata?.file_id;

	$: isPDF = fileName?.toLowerCase().endsWith('.pdf');
	$: isDocx = fileName?.toLowerCase().endsWith('.docx');
	$: isXlsx = fileName?.toLowerCase().endsWith('.xlsx');
	$: isImage = /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(fileName);
	$: isAudio = /\.(mp3|wav|ogg|m4a|webm)$/i.test(fileName);
	$: isPreviewable = fileId && (isPDF || isDocx || isXlsx || isImage || isAudio);

	// Split view = a document with cited snippets to highlight (PDF/DOCX).
	// Image/audio/XLSX render single-pane; web/missing-file fall to content.
	$: showSnippetRail = isPDF || isDocx;

	// Active snippet drives the highlight in the viewer pane.
	$: activeSnippet = mergedDocuments?.[activeSnippetIdx];
	$: activeSnippetText = activeSnippet?.document ?? '';
	$: activePage = Number.isInteger(activeSnippet?.metadata?.page)
		? activeSnippet.metadata.page
		: undefined;

	// Compute minimum page number across all chunks for PDF navigation
	$: minPage = (() => {
		const pages = (mergedDocuments ?? [])
			.filter((d) => Number.isInteger(d?.metadata?.page))
			.map((d) => d.metadata.page);
		return pages.length > 0 ? Math.min(...pages) + 1 : undefined;
	})();

	// Preview URL for iframe/img/audio (with page hash, used by the title link).
	$: previewUrl = fileId
		? `${WEBUI_API_BASE_URL}/files/${fileId}/content${isPDF && minPage ? `#page=${minPage}` : ''}`
		: '';

	// Plain content URL without the #page hash — PDFViewer owns paging now.
	$: previewUrlNoHash = fileId ? `${WEBUI_API_BASE_URL}/files/${fileId}/content` : '';

	// Check if file is still available when modal opens
	$: if (show && fileId) {
		previewAvailable = true;
		fetch(`${WEBUI_API_BASE_URL}/files/${fileId}/content`, { method: 'HEAD' })
			.then((res) => {
				if (!res.ok) {
					previewAvailable = false;
					selectedTab = 'content';
				}
			})
			.catch(() => {
				previewAvailable = false;
				selectedTab = 'content';
			});
	}

	// Load DOCX/XLSX content once per file when the preview opens. Keyed on the
	// file id so it does not re-fetch on every reactive tick (e.g. snippet click).
	// Reset on close so reopening the same file re-renders and re-applies the
	// snippet-0 highlight into a fresh container.
	let loadedOfficeFileId: string | null = null;
	$: if (!show) {
		loadedOfficeFileId = null;
	}
	$: if (show && previewAvailable && fileId && (isDocx || isXlsx) && fileId !== loadedOfficeFileId) {
		loadedOfficeFileId = fileId;
		loadOfficeContent(fileId, isDocx);
	}

	const loadOfficeContent = async (id: string, asDocx: boolean) => {
		officeLoading = true;
		officeError = false;
		docxHtml = '';
		xlsxWorkbook = null;
		xlsxSheetNames = [];
		selectedSheet = '';
		xlsxHtml = '';
		try {
			const buffer = await getFileContentById(id);
			if (!buffer) {
				officeError = true;
				return;
			}
			if (asDocx) {
				docxHtml = await renderDocxHtml(buffer);
				// Stop loading first so the container renders, then highlight it.
				officeLoading = false;
				await tick();
				highlightActiveDocx();
				return;
			}
			xlsxWorkbook = await readWorkbook(buffer);
			xlsxSheetNames = xlsxWorkbook.SheetNames;
			if (xlsxSheetNames.length > 0) {
				await selectSheet(xlsxSheetNames[0]);
			}
		} catch (error) {
			console.error('Office preview load error:', error);
			officeError = true;
		} finally {
			officeLoading = false;
		}
	};

	const selectSheet = async (sheet: string) => {
		if (!xlsxWorkbook) return;
		selectedSheet = sheet;
		const { html } = await renderSheetHtml(xlsxWorkbook, sheet);
		xlsxHtml = html;
	};

	// Re-highlight the rendered DOCX for the active snippet (no-op without match).
	const highlightDocxFor = (text: string) => {
		if (!docxContainer) return;
		highlightDocx(docxContainer, text);
		scrollToFirstDocxHighlight(docxContainer);
	};

	const highlightActiveDocx = () => highlightDocxFor(activeSnippetText);

	const selectSnippet = (idx: number) => {
		activeSnippetIdx = idx;
		// Read the snippet directly from idx — the reactive `activeSnippet*`
		// derivations have not updated yet within this synchronous handler.
		const snippet = mergedDocuments?.[idx];
		const text = snippet?.document ?? '';
		const page = Number.isInteger(snippet?.metadata?.page) ? snippet.metadata.page : undefined;
		if (isPDF) {
			pdfViewerRef?.setHighlight(text, (page ?? 0) + 1);
			return;
		}
		if (isDocx) {
			highlightDocxFor(text);
		}
	};

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch {
			return str;
		}
	};

	const getTextFragmentUrl = (doc: any): string | null => {
		const { metadata, source, document: content } = doc ?? {};
		const { file_id, page } = metadata ?? {};
		const sourceUrl = source?.url;

		const baseUrl = file_id
			? `${WEBUI_API_BASE_URL}/files/${file_id}/content${page !== undefined ? `#page=${page + 1}` : ''}`
			: sourceUrl?.includes('http')
				? sourceUrl
				: null;

		if (!baseUrl || !content) return baseUrl;

		// Extract first and last words for text fragment, filtering out URLs and emojis
		const words = content
			.trim()
			.replace(/\s+/g, ' ')
			.split(' ')
			.filter((w: string) => w.length > 0 && !/https?:\/\/|[\u{1F300}-\u{1F9FF}]/u.test(w));

		if (words.length === 0) return baseUrl;

		const clean = (w: string) => w.replace(/[^\w]/g, '');
		const first = clean(words[0]);
		const last = clean(words.at(-1));
		const fragment = words.length === 1 ? first : `${first},${last}`;

		return fragment ? `${baseUrl}#:~:text=${fragment}` : baseUrl;
	};
</script>

<Modal size="xl" bind:show>
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-4.5 pt-3 pb-2">
			<div class=" text-lg font-medium self-center flex items-center">
				{#if citation?.source?.name}
					{@const document = mergedDocuments?.[0]}
					{#if document?.metadata?.file_id || document.source?.url?.includes('http')}
						{@const isFileMissing =
							!!document?.metadata?.file_id && !previewAvailable}
						<Tooltip
							className="w-fit"
							content={isFileMissing
								? $i18n.t('File no longer available')
								: document.source?.url?.includes('http')
									? $i18n.t('Open link')
									: $i18n.t('Open file')}
							placement="top-start"
							tippyOptions={{ duration: [500, 0] }}
						>
							{#if isFileMissing}
								<span
									class="grow line-clamp-1 text-gray-500 dark:text-gray-400 cursor-not-allowed"
								>
									{decodeString(citation?.source?.name)}
								</span>
							{:else}
								<a
									class="hover:text-gray-500 dark:hover:text-gray-100 underline grow line-clamp-1"
									href={document?.metadata?.file_id
										? `${WEBUI_API_BASE_URL}/files/${document?.metadata?.file_id}/content${document?.metadata?.page !== undefined ? `#page=${document.metadata.page + 1}` : ''}`
										: document.source?.url?.includes('http')
											? document.source.url
											: `#`}
									target="_blank"
								>
									{decodeString(citation?.source?.name)}
								</a>
							{/if}
						</Tooltip>
					{:else}
						{decodeString(citation?.source?.name)}
					{/if}
				{:else}
					{$i18n.t('Citation')}
				{/if}
			</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close citation modal')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col w-full px-5 pb-5">
			<!-- Tab switcher: only shown for previewable file types with available files -->
			{#if isPreviewable && previewAvailable}
				<div class="flex gap-1 mb-3">
					<button
						class="px-3 py-1 text-xs font-medium rounded-lg transition {selectedTab === 'preview'
							? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
							: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
						on:click={() => (selectedTab = 'preview')}
					>
						{$i18n.t('Preview')}
					</button>
					<button
						class="px-3 py-1 text-xs font-medium rounded-lg transition {selectedTab === 'content'
							? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
							: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
						on:click={() => (selectedTab = 'content')}
					>
						{$i18n.t('Content')}
					</button>
				</div>
			{/if}

			<!-- Preview tab -->
			{#if isPreviewable && previewAvailable && selectedTab === 'preview'}
				{#if showSnippetRail}
					<!-- Split view: cited snippets (left) + rendered document (right) -->
					<div class="flex flex-col md:flex-row w-full gap-3 h-[70vh]">
						<!-- Snippet rail -->
						<div
							class="w-full md:w-72 shrink-0 overflow-y-auto scrollbar-thin flex flex-col gap-1.5"
						>
							{#each mergedDocuments as document, snippetIdx}
								<button
									class="text-left w-full rounded-lg border p-2.5 transition {snippetIdx ===
									activeSnippetIdx
										? 'border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-850'
										: 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-850/50'}"
									on:click={() => selectSnippet(snippetIdx)}
								>
									<div class="flex items-center gap-2 mb-1">
										{#if showRelevance && document.distance !== undefined}
											{#if showPercentage}
												{@const percentage = calculatePercentage(document.distance)}
												{#if typeof percentage === 'number'}
													<span
														class={`px-1 rounded-sm text-xs font-medium ${getRelevanceColor(percentage)}`}
													>
														{percentage.toFixed(0)}%
													</span>
												{/if}
											{:else if typeof document?.distance === 'number'}
												<span class="text-xs text-gray-500 dark:text-gray-500">
													({(document?.distance ?? 0).toFixed(4)})
												</span>
											{/if}
										{/if}
										{#if Number.isInteger(document?.metadata?.page)}
											<span class="text-xs text-gray-500 dark:text-gray-400">
												({$i18n.t('page')}
												{document.metadata.page + 1})
											</span>
										{/if}
									</div>
									<div class="text-xs text-gray-700 dark:text-gray-300 line-clamp-4">
										{truncate(document.document?.trim() ?? '', SNIPPET_TRUNCATE)}
									</div>
								</button>
							{/each}
						</div>

						<!-- Viewer pane -->
						<div class="flex-1 min-w-0 rounded-lg overflow-hidden">
							{#if isPDF}
								<PDFViewer
									bind:this={pdfViewerRef}
									url={previewUrlNoHash}
									className="w-full h-full"
									highlightText={activeSnippetText}
									initialPage={(activePage ?? 0) + 1}
								/>
							{:else if isDocx}
								{#if officeLoading}
									<div class="flex items-center justify-center h-full">
										<Spinner className="size-5" />
									</div>
								{:else if officeError}
									<div
										class="flex items-center justify-center h-full text-sm text-gray-400"
									>
										{$i18n.t('Could not read file.')}
									</div>
								{:else}
									<div
										bind:this={docxContainer}
										class="office-preview h-full overflow-y-auto scrollbar-thin p-4 prose dark:prose-invert max-w-full text-sm"
									>
										<!-- eslint-disable-next-line svelte/no-at-html-tags — docxHtml is DOMPurify-sanitized in renderDocxHtml -->
										{@html docxHtml}
									</div>
								{/if}
							{/if}
						</div>
					</div>
				{:else if isXlsx}
					<!-- XLSX: view-only sheet grid with sheet tabs -->
					{#if officeLoading}
						<div class="flex items-center justify-center h-[70vh]">
							<Spinner className="size-5" />
						</div>
					{:else if officeError}
						<div class="flex items-center justify-center h-[70vh] text-sm text-gray-400">
							{$i18n.t('Could not read file.')}
						</div>
					{:else}
						<div class="flex flex-col h-[70vh]">
							<div class="office-preview overflow-auto flex-1 min-h-0 rounded-lg">
								<!-- eslint-disable-next-line svelte/no-at-html-tags — xlsxHtml is DOMPurify-sanitized in renderSheetHtml/excelToTable -->
								{@html xlsxHtml}
							</div>
							{#if xlsxSheetNames.length > 1}
								<div
									class="flex items-center gap-1 py-1.5 px-3 border-t border-gray-100 dark:border-gray-800 overflow-x-auto"
								>
									{#each xlsxSheetNames as sheet}
										<button
											class="shrink-0 px-3 py-1 text-xs rounded-md transition-colors
												{selectedSheet === sheet
												? 'bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 font-medium'
												: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'}"
											on:click={() => selectSheet(sheet)}
										>
											{sheet}
										</button>
									{/each}
								</div>
							{/if}
						</div>
					{/if}
				{:else if isImage}
					<img
						src={previewUrl}
						alt={fileName}
						class="max-w-full max-h-[70vh] rounded-lg object-contain mx-auto"
					/>
				{:else if isAudio}
					<audio src={previewUrl} class="w-full rounded-lg" controls playsinline />
				{/if}
			{:else}
				<!-- Content tab (upstream text view with Markdown) -->
				<div class="flex flex-col md:flex-row w-full md:space-x-4">
					<div
						class="flex flex-col w-full dark:text-gray-200 overflow-y-scroll max-h-[22rem] scrollbar-thin gap-1"
					>
						{#each mergedDocuments as document, documentIdx}
							<div class="flex flex-col w-full gap-2">
								{#if document.metadata?.parameters}
									<div>
										<div class="text-sm font-medium dark:text-gray-300 mb-1">
											{$i18n.t('Parameters')}
										</div>

										<Textarea readonly value={JSON.stringify(document.metadata.parameters, null, 2)}
										></Textarea>
									</div>
								{/if}

								<div>
									<div
										class=" text-sm font-medium dark:text-gray-300 flex items-center gap-2 w-fit mb-1"
									>
										{#if document.source?.url?.includes('http')}
											{@const snippetUrl = getTextFragmentUrl(document)}
											{#if snippetUrl}
												<a
													href={snippetUrl}
													target="_blank"
													class="underline hover:text-gray-500 dark:hover:text-gray-100"
													>{$i18n.t('Content')}</a
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
															<span
																class={`px-1 rounded-sm font-medium ${getRelevanceColor(percentage)}`}
															>
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
												{document.metadata.page + 1})
											</span>
										{/if}
									</div>

									{#if document.metadata?.html}
										<iframe
											class="w-full border-0 h-auto rounded-none"
											sandbox="allow-scripts allow-forms{($settings?.iframeSandboxAllowSameOrigin ??
											false)
												? ' allow-same-origin'
												: ''}"
											srcdoc={document.document}
											title={$i18n.t('Content')}
										></iframe>
									{:else}
										{@const rawContent = document.document.trim().replace(/\n\n+/g, '\n\n')}
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
													content={isTruncated
														? rawContent.slice(0, CONTENT_PREVIEW_LIMIT)
														: rawContent}
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
					</div>
				</div>
			{/if}
		</div>
	</div>
</Modal>

<style>
	/*
	 * Citation highlight for DOCX — applied to <mark> elements injected by
	 * citationDomHighlight.ts into the {@html}-rendered document. :global is
	 * required because those marks are created at runtime, not by Svelte.
	 * Mirrors the PDF highlight colors in PDFViewer.svelte.
	 */
	:global(.office-preview mark.citation-highlight) {
		background: rgba(250, 204, 21, 0.45); /* amber-300 */
		color: inherit;
		border-radius: 2px;
		padding: 0 1px;
	}
	:global(.dark .office-preview mark.citation-highlight) {
		background: rgba(250, 204, 21, 0.35);
	}

	/*
	 * Office-preview styles DUPLICATED from FilePreview.svelte. Svelte scopes
	 * styles per component, so the DOCX/XLSX markup rendered here cannot inherit
	 * FilePreview's rules. FOLLOW-UP: extract these into a shared/global
	 * stylesheet imported by both components (and FileItemModal) to remove the
	 * duplication — see Phase 3 notes. Kept minimal: only the rules the citation
	 * DOCX/XLSX panes actually use.
	 */
	:global(.office-preview) {
		font-size: 0.875rem;
		line-height: 1.6;
		color: #1f2937;
		background: #fff;
		border-radius: 4px;
	}
	:global(.dark .office-preview) {
		color: #e5e7eb;
		background: #1a1a2e;
	}
	:global(.office-preview table) {
		border-collapse: collapse;
		font-size: 0.75rem;
		font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
		line-height: 1.3;
	}
	:global(.office-preview table td),
	:global(.office-preview table th) {
		border: 1px solid rgba(200, 200, 200, 0.5);
		padding: 4px 10px;
		text-align: left;
		white-space: nowrap;
		user-select: text;
		cursor: cell;
		max-width: 300px;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	:global(.dark .office-preview table td),
	:global(.dark .office-preview table th) {
		border-color: rgba(80, 80, 80, 0.5);
	}
	/* Column letter headers */
	:global(.office-preview table th.excel-col-hdr) {
		position: sticky;
		top: 0;
		z-index: 2;
		background: #f0f0f0;
		color: #666;
		font-weight: 500;
		font-size: 0.65rem;
		text-align: center;
		padding: 3px 10px;
		border-bottom: 2px solid rgba(180, 180, 180, 0.6);
	}
	:global(.dark .office-preview table th.excel-col-hdr) {
		background: #2a2a3e;
		color: #888;
		border-bottom-color: rgba(100, 100, 100, 0.6);
	}
	/* Row number cells */
	:global(.office-preview .excel-row-num) {
		position: sticky;
		left: 0;
		z-index: 1;
		background: #f0f0f0;
		color: #999;
		font-size: 0.6rem;
		text-align: right !important;
		padding: 4px 8px 4px 4px !important;
		user-select: none;
		width: 1px;
		white-space: nowrap;
		border-right: 2px solid rgba(180, 180, 180, 0.6) !important;
	}
	:global(.dark .office-preview .excel-row-num) {
		background: #2a2a3e;
		color: #666;
		border-right-color: rgba(100, 100, 100, 0.6) !important;
	}
	/* Corner cell (intersection of row nums and col headers) */
	:global(.office-preview thead .excel-row-num) {
		z-index: 3;
	}
	/* Number cells right-aligned */
	:global(.office-preview .excel-num) {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	:global(.office-preview table tbody tr:nth-child(even) td:not(.excel-row-num)) {
		background: rgba(0, 0, 0, 0.015);
	}
	:global(.dark .office-preview table tbody tr:nth-child(even) td:not(.excel-row-num)) {
		background: rgba(255, 255, 255, 0.02);
	}
	:global(.office-preview table tbody tr:hover td:not(.excel-row-num)) {
		background: rgba(59, 130, 246, 0.06);
	}
	:global(.dark .office-preview table tbody tr:hover td:not(.excel-row-num)) {
		background: rgba(59, 130, 246, 0.1);
	}
	/* DOCX / generic office styles */
	:global(.office-preview img) {
		max-width: 100%;
		height: auto;
	}
	:global(.office-preview h1) {
		font-size: 1.5rem;
		font-weight: 700;
		margin: 0.75em 0 0.5em;
	}
	:global(.office-preview h2) {
		font-size: 1.25rem;
		font-weight: 600;
		margin: 0.75em 0 0.5em;
	}
	:global(.office-preview h3) {
		font-size: 1.1rem;
		font-weight: 600;
		margin: 0.5em 0 0.25em;
	}
	:global(.office-preview p) {
		margin: 0.25em 0;
	}
	:global(.office-preview ul),
	:global(.office-preview ol) {
		padding-left: 1.5em;
		margin: 0.5em 0;
	}
</style>
