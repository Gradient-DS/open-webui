# Citation Modal Document Preview — Implementation Plan

## Overview

Improve `CitationModal.svelte` to show native document previews (PDF, images, audio) instead of only parsed text chunks. Add a tab switcher (Preview/Content), default to Preview for supported file types, open PDFs on the correct page, and add an external link icon to the title.

## Current State Analysis

**CitationModal.svelte** (181 lines) currently:
- Receives a `citation` object with parallel arrays of `document[]`, `metadata[]`, `distances[]`
- Merges these into `mergedDocuments` and renders each chunk's parsed text
- Shows relevance scores (percentage or raw distance) and page numbers per chunk
- Title links to `/api/v1/files/{file_id}/content#page=N` for new-tab opening
- Has no native file preview — only plain text and HTML iframe (for `metadata.html`)

**FileItemModal.svelte** has a mature preview system:
- Tab switcher: Content (parsed text) vs Preview (native rendering)
- PDF: `<iframe src=".../files/{id}/content" class="w-full h-[70vh]" />`
- Audio: `<audio src=".../files/{id}/content" controls playsinline />`
- Type detection via `meta.content_type` + file extension fallback
- Excel, code, markdown also supported

**Key difference**: FileItemModal previews a single file by `item.id`. CitationModal shows chunks from a file identified by `metadata.file_id`. Both use the same backend endpoint (`/api/v1/files/{id}/content`).

### Key Discoveries:
- `CitationModal.svelte:78-79` — already constructs the file content URL with `#page=N`
- `FileItemModal.svelte:43-45` — PDF detection: `content_type === 'application/pdf'` OR name ends with `.pdf`
- `FileItemModal.svelte:366-371` — PDF iframe: `<iframe src={url} class="w-full h-[70vh] border-0 rounded-lg" />`
- No dedicated external link icon component — inline Heroicons Mini SVG used in `WebSearchResults.svelte` and `Banner.svelte`
- `mergedDocuments[0].metadata.file_id` gives us the file ID for preview URL construction
- `mergedDocuments[0].metadata.name` or `citation.source.name` gives us the filename for type detection

## Desired End State

When a user clicks a citation:
1. **PDF files**: Modal opens with the PDF rendered in an iframe, navigated to the lowest-numbered page across all chunks. A "Content" tab allows switching to the current parsed text view.
2. **Image files**: Modal shows the image natively. Content tab available.
3. **Audio files**: Modal shows an audio player. Content tab available.
4. **Other file types**: Modal shows parsed text (current behavior, no tabs).
5. **Title**: Shows an external link arrow icon next to the filename, indicating it opens in a new tab.

### Verification:
- Upload a PDF to a knowledge base, ask a question that triggers RAG retrieval
- Click the citation → PDF should render in the modal, navigated to the correct page
- Click "Content" tab → parsed text chunks should display as before
- The title should have a small arrow icon and link to the file in a new tab

## What We're NOT Doing

- **Excel/CSV preview** — would need SheetJS and binary file fetching; defer to a future iteration
- **DOCX/PPTX preview** — no browser-native rendering; not feasible without conversion
- **Code/Markdown preview** — citation chunks are text fragments, not full files; native rendering wouldn't add value
- **Creating a shared tab component** — the FileItemModal and CitationModal tab patterns are similar but have different enough contexts that extracting a shared component isn't worth the abstraction cost
- **Backend changes** — the file serving endpoint already works for all needed types

## Implementation Approach

Single phase, all changes in `CitationModal.svelte`. We'll:
1. Add file type detection (reusing FileItemModal's pattern)
2. Compute the preview URL with minimum page number across all chunks
3. Add a tab switcher that appears only for previewable types
4. Default to Preview tab for supported types, Content tab for others
5. Add an external link icon to the title

## Phase 1: Document Preview in Citation Modal

### Overview
All changes in one file: `src/lib/components/chat/Messages/Citations/CitationModal.svelte`

### Changes Required:

#### 1. Script Section — Add State and Type Detection

After the existing `mergedDocuments` declaration (line 17), add:

```svelte
let selectedTab = 'preview'; // 'preview' or 'content'
```

After the reactive `$: if (citation)` block (after line 50), add reactive type detection and URL computation:

```javascript
// File type detection from first document's metadata
$: fileName = mergedDocuments?.[0]?.metadata?.name ?? citation?.source?.name ?? '';
$: fileId = mergedDocuments?.[0]?.metadata?.file_id;

$: isPDF = fileName?.toLowerCase().endsWith('.pdf');
$: isImage = /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(fileName);
$: isAudio = /\.(mp3|wav|ogg|m4a|webm)$/i.test(fileName);
$: isPreviewable = fileId && (isPDF || isImage || isAudio);

// Compute minimum page number across all chunks for PDF navigation
$: minPage = (() => {
    const pages = (mergedDocuments ?? [])
        .filter(d => Number.isInteger(d?.metadata?.page))
        .map(d => d.metadata.page);
    return pages.length > 0 ? Math.min(...pages) + 1 : undefined;
})();

// Preview URL for iframe/img/audio
$: previewUrl = fileId
    ? `${WEBUI_API_BASE_URL}/files/${fileId}/content${isPDF && minPage ? `#page=${minPage}` : ''}`
    : '';
```

Also reset `selectedTab` when citation changes (inside the existing `$: if (citation)` block):

```javascript
$: if (citation) {
    // ... existing mergedDocuments logic ...
    selectedTab = 'preview'; // Reset to preview when citation changes
}
```

#### 2. Title Section — Add External Link Icon

Replace the title `<a>` tag content (line 84-86) to include an arrow icon after the text. The `<a>` tag at lines 76-86 becomes:

```svelte
<a
    class="hover:text-gray-500 dark:hover:text-gray-100 underline grow line-clamp-1 flex items-center gap-1"
    href={document?.metadata?.file_id
        ? `${WEBUI_API_BASE_URL}/files/${document?.metadata?.file_id}/content${document?.metadata?.page !== undefined ? `#page=${document.metadata.page + 1}` : ''}`
        : document.source?.url?.includes('http')
            ? document.source.url
            : `#`}
    target="_blank"
>
    <span class="line-clamp-1">{decodeString(citation?.source?.name)}</span>
    <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 16 16"
        fill="currentColor"
        class="size-3.5 shrink-0"
    >
        <path
            fill-rule="evenodd"
            d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
            clip-rule="evenodd"
        />
    </svg>
</a>
```

This uses the same Heroicons Mini `arrow-up-right` SVG that's already used in `WebSearchResults.svelte` and `Banner.svelte`.

#### 3. Content Area — Add Tab Switcher and Preview

Replace the content area (lines 105-179) with a tab-aware layout:

```svelte
<div class="flex flex-col w-full px-5 pb-5">
    <!-- Tab switcher: only shown for previewable file types -->
    {#if isPreviewable}
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
    {#if isPreviewable && selectedTab === 'preview'}
        {#if isPDF}
            <iframe
                title={fileName}
                src={previewUrl}
                class="w-full h-[70vh] border-0 rounded-lg"
            />
        {:else if isImage}
            <img
                src={previewUrl}
                alt={fileName}
                class="max-w-full max-h-[70vh] rounded-lg object-contain mx-auto"
            />
        {:else if isAudio}
            <audio
                src={previewUrl}
                class="w-full rounded-lg"
                controls
                playsinline
            />
        {/if}
    {:else}
        <!-- Content tab (existing parsed text view) -->
        <div class="flex flex-col md:flex-row w-full md:space-x-4">
            <div
                class="flex flex-col w-full dark:text-gray-200 overflow-y-scroll max-h-[22rem] scrollbar-thin gap-1"
            >
                {#each mergedDocuments as document, documentIdx}
                    <!-- existing per-document rendering (parameters + content + relevance + page) -->
                    <div class="flex flex-col w-full gap-2">
                        {#if document.metadata?.parameters}
                            <div>
                                <div class="text-sm font-medium dark:text-gray-300 mb-1">
                                    {$i18n.t('Parameters')}
                                </div>
                                <Textarea readonly value={JSON.stringify(document.metadata.parameters, null, 2)} />
                            </div>
                        {/if}

                        <div>
                            <div class="text-sm font-medium dark:text-gray-300 flex items-center gap-2 w-fit mb-1">
                                {$i18n.t('Content')}

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
                                        ({$i18n.t('page')} {document.metadata.page + 1})
                                    </span>
                                {/if}
                            </div>

                            {#if document.metadata?.html}
                                <iframe
                                    class="w-full border-0 h-auto rounded-none"
                                    sandbox="allow-scripts allow-forms allow-same-origin"
                                    srcdoc={document.document}
                                    title={$i18n.t('Content')}
                                />
                            {:else}
                                <pre class="text-sm dark:text-gray-400 whitespace-pre-line">{document.document
                                        .trim()
                                        .replace(/\n\n+/g, '\n\n')}</pre>
                            {/if}
                        </div>
                    </div>
                {/each}
            </div>
        </div>
    {/if}
</div>
```

### i18n Keys

Add to `src/lib/i18n/locales/en-US/translation.json`:
- `"Preview": ""` — empty string means "use the key" per project convention

(The keys `Content`, `page`, `Parameters`, `Relevance`, `Open file`, `Open link`, `Citation` already exist.)

### Success Criteria:

#### Automated Verification:
- [x] Build succeeds: `npm run build`
- [x] No new lint errors in the modified file

#### Manual Verification:
- [ ] Upload a PDF to a knowledge base, ask a question that triggers RAG citation
- [ ] Click citation → modal opens with PDF preview in iframe, navigated to correct page
- [ ] Click "Content" tab → shows parsed text chunks with relevance and page numbers (as before)
- [ ] Click "Preview" tab → returns to PDF view
- [ ] Title shows external link arrow icon, clicking opens file in new tab
- [ ] For non-PDF citations (e.g., plain text files), modal shows parsed text only (no tabs)
- [ ] For image file citations, modal shows the image natively
- [ ] For audio file citations, modal shows an audio player
- [ ] Web URL citations (HTTP sources) still work correctly with no tabs
- [ ] Mobile: PDF preview is usable (may need scroll)

## References

- Research: `thoughts/shared/research/2026-02-16-citation-modal-document-preview.md`
- Current implementation: `src/lib/components/chat/Messages/Citations/CitationModal.svelte`
- Tab pattern reference: `src/lib/components/common/FileItemModal.svelte:322-345`
- PDF iframe reference: `src/lib/components/common/FileItemModal.svelte:366-371`
- External link icon SVG: `src/lib/components/chat/Messages/ResponseMessage/WebSearchResults.svelte:47-54`
