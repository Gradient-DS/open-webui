<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { CitationDocument } from './citationDocuments';
	const i18n = getContext<Readable<I18n>>('i18n');

	import { tick, onDestroy } from 'svelte';
	import type { WorkBook } from 'xlsx';
	import type { DisplayCitation } from './reduceSources';
	import PDFViewer from '$lib/components/common/PDFViewer.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { config } from '$lib/stores';
	import { getFileContentById } from '$lib/apis/files';
	import { renderDocxHtml, readWorkbook, renderSheetHtml } from '$lib/utils/officePreview';
	import { highlightDocx, scrollToFirstDocxHighlight } from '$lib/utils/citationDomHighlight';
	import {
		citationFileInfo,
		isDocumentSnippet,
		rectsFromSnippet,
		snippetPage,
		minimumPage,
		fileContentUrl
	} from './useCitationDocument';
	export let citation: DisplayCitation | null = null;
	export let mergedDocuments: CitationDocument[] = [];
	export let activeSnippetIdx = 0;
	export let previewAvailable = true;
	export let imageHeightClass = 'max-h-full';
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

	$: ({ fileName, fileId, isPDF, isDocx, isXlsx, isImage, isAudio } = citationFileInfo(
		citation,
		mergedDocuments
	));
	$: activeSnippet = mergedDocuments?.[activeSnippetIdx];
	$: activeSnippetText = activeSnippet?.document ?? '';
	$: citationTextHighlightEnabled = $config?.features?.enable_citation_text_highlight ?? false;
	$: activePage = snippetPage(activeSnippet);
	$: activeRects = rectsFromSnippet(activeSnippet);
	$: activeIsDocument = isDocumentSnippet(activeSnippet);
	$: previewUrl = fileContentUrl(fileId, isPDF ? minimumPage(mergedDocuments) : undefined);
	$: previewUrlNoHash = fileContentUrl(fileId);
	let loadedOfficeFileId: string | null = null;
	$: if (fileId !== loadedOfficeFileId) {
		loadedOfficeFileId = null;
		if (previewAvailable && fileId && (isDocx || isXlsx)) {
			loadedOfficeFileId = fileId;
			loadOfficeContent(fileId, isDocx);
		}
	}
	onDestroy(() => {
		loadedOfficeFileId = null;
	});
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
			// Stale-load guard: the `citation` prop may switch files mid-flight.
			// `loadedOfficeFileId` always holds the latest requested id, so if it no
			// longer equals `id` a newer load has started — discard this one's results
			// without touching shared state (no render, no highlight). The latest load
			// owns the final write.
			if (id !== loadedOfficeFileId) return;
			if (!buffer) {
				officeError = true;
				return;
			}
			if (asDocx) {
				const html = await renderDocxHtml(buffer);
				if (id !== loadedOfficeFileId) return;
				docxHtml = html;
				// Stop loading first so the container renders, then highlight it.
				officeLoading = false;
				await tick();
				if (id !== loadedOfficeFileId) return;
				highlightActiveDocx();
				return;
			}
			const workbook = await readWorkbook(buffer);
			if (id !== loadedOfficeFileId) return;
			xlsxWorkbook = workbook;
			xlsxSheetNames = workbook.SheetNames;
			if (xlsxSheetNames.length > 0) {
				await selectSheet(xlsxSheetNames[0]);
			}
		} catch (error) {
			console.error('Office preview load error:', error);
			if (id !== loadedOfficeFileId) return;
			officeError = true;
		} finally {
			// Only the latest load may clear the spinner; a superseded load must not
			// flip `officeLoading` for the file that replaced it.
			if (id === loadedOfficeFileId) {
				officeLoading = false;
			}
		}
	};

	const selectSheet = async (sheet: string) => {
		if (!xlsxWorkbook) return;
		selectedSheet = sheet;
		const workbook = xlsxWorkbook;
		const id = loadedOfficeFileId;
		const { html } = await renderSheetHtml(workbook, sheet);
		if (id === loadedOfficeFileId && workbook === xlsxWorkbook && selectedSheet === sheet)
			xlsxHtml = html;
	};

	// Re-highlight the rendered DOCX for the active snippet (no-op without match).
	// Skipped entirely when text-match highlighting is disabled.
	const highlightDocxFor = (text: string) => {
		if (!citationTextHighlightEnabled || !docxContainer) return;
		highlightDocx(docxContainer, text);
		scrollToFirstDocxHighlight(docxContainer);
	};

	const highlightActiveDocx = () => highlightDocxFor(activeIsDocument ? '' : activeSnippetText);

	export function selectSnippet(idx: number) {
		activeSnippetIdx = idx;
		// Read the snippet directly from idx — the reactive `activeSnippet*`
		// derivations have not updated yet within this synchronous handler.
		const snippet = mergedDocuments?.[idx];
		const text = snippet?.document ?? '';
		const page = snippetPage(snippet);
		const documentLevel = isDocumentSnippet(snippet);
		if (isPDF) {
			pdfViewerRef?.setHighlight(
				citationTextHighlightEnabled && !documentLevel ? text : null,
				(page ?? 0) + 1,
				rectsFromSnippet(snippet)
			);
			return;
		}
		if (isDocx) {
			highlightDocxFor(documentLevel ? '' : text);
		}
	}
</script>

<div class="h-full min-h-0 min-w-0 rounded-lg overflow-hidden">
	{#if isPDF}
		<PDFViewer
			bind:this={pdfViewerRef}
			url={previewUrlNoHash}
			className="w-full h-full"
			highlightText={citationTextHighlightEnabled && !activeIsDocument ? activeSnippetText : null}
			initialPage={(activePage ?? 0) + 1}
			highlightRects={activeRects}
		/>
	{:else if isDocx}
		{#if officeLoading}
			<div class="flex items-center justify-center h-full">
				<Spinner className="size-5" />
			</div>
		{:else if officeError}
			<div class="flex items-center justify-center h-full text-sm text-gray-400">
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
	{:else if isXlsx}
		<!-- XLSX: view-only sheet grid with sheet tabs -->
		{#if officeLoading}
			<div class="flex items-center justify-center h-full">
				<Spinner className="size-5" />
			</div>
		{:else if officeError}
			<div class="flex items-center justify-center h-full text-sm text-gray-400">
				{$i18n.t('Could not read file.')}
			</div>
		{:else}
			<div class="flex flex-col h-full">
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
			class="max-w-full {imageHeightClass} rounded-lg object-contain mx-auto"
		/>
	{:else if isAudio}
		<audio src={previewUrl} class="w-full rounded-lg" controls playsinline></audio>
	{/if}
</div>

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
