---
date: 2026-02-16T14:30:00+01:00
researcher: claude
git_commit: 832aeba216d20df24fa6cc2ff1383cdd0ef4dd52
branch: feat/sync-improvements
repository: open-webui
topic: "Citation Modal Document Preview Improvements"
tags: [research, codebase, citations, pdf-preview, document-viewer]
status: complete
last_updated: 2026-02-16
last_updated_by: claude
---

# Research: Citation Modal Document Preview Improvements

**Date**: 2026-02-16T14:30:00+01:00
**Researcher**: claude
**Git Commit**: 832aeba216d20df24fa6cc2ff1383cdd0ef4dd52
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

Can we improve the citation modal to show native document previews (PDF, etc.) instead of just parsed text? Specifically:
1. Can we show the PDF in the citation modal (and other doc types)?
2. Can we open the document on the page where the content came from?
3. Can we add a link icon to the title for "open in new tab"?
4. Can we have doc preview as default before parsed text?
5. Can we show page numbers per chunk?

## Summary

**All 5 improvements are feasible.** The codebase already has most building blocks in place — `FileItemModal.svelte` already renders PDF previews via iframe, and the backend already serves PDFs inline. The main work is bridging the gap between the citation modal (which shows parsed text) and the file preview capabilities that already exist.

## Detailed Findings

### Question 1: Can we show the PDF in the citation modal?

**YES — straightforward.** The building blocks already exist:

- **Backend serves PDFs inline**: `files.py:708-714` sets `Content-Disposition: inline` for PDFs, so `GET /api/v1/files/{id}/content` renders PDFs natively in browsers.
- **`FileItemModal.svelte` already does this**: Lines 366-371 render PDFs in an iframe:
  ```svelte
  <iframe title={item?.name} src={`${WEBUI_API_BASE_URL}/files/${item.id}/content`} class="w-full h-[70vh] border-0 rounded-lg" />
  ```
- **Citation modal has `file_id`**: `CitationModal.svelte:78` already constructs the URL `${WEBUI_API_BASE_URL}/files/${document?.metadata?.file_id}/content`.
- **What's needed**: Add an iframe to `CitationModal.svelte` for PDF files, similar to `FileItemModal.svelte`. Detect PDF via `metadata.name` or the file extension.

**Other doc types that can be previewed natively:**

| Type | In-browser support | Notes |
|------|-------------------|-------|
| **PDF** | Yes (all browsers) | iframe with `application/pdf` — already works |
| **Images** (png/jpg/gif/webp) | Yes | `<img>` tag |
| **Audio** (mp3/wav/ogg) | Yes | `<audio>` tag — already in `FileItemModal` |
| **Excel/CSV** (xlsx/xls/csv) | Yes (client-side) | Already in `FileItemModal` via `xlsx` library |
| **DOCX** | No native browser support | Would need conversion (e.g., mammoth.js or server-side HTML export). Not recommended for initial implementation. |
| **PPTX** | No native browser support | Same as DOCX — no simple path. |

**Recommendation**: Support PDF (iframe), images (img tag), and audio (audio tag) in the citation modal. Excel could be added later. DOCX/PPTX don't have good browser-native rendering.

### Question 2: Can we open the document on the correct page?

**YES — partially already implemented.**

- **`CitationModal.svelte:79` already appends `#page=N`** to the URL when opening in a new tab:
  ```js
  `${WEBUI_API_BASE_URL}/files/${document?.metadata?.file_id}/content${document?.metadata?.page !== undefined ? `#page=${document.metadata.page + 1}` : ''}`
  ```
- **For the in-modal iframe**, the same `#page=N` fragment works with browser PDF viewers. We just need to include it in the iframe `src`.
- **For the new-tab link** — this already works!

**Caveat on page data availability**: Page metadata is only available when using certain document loaders:
- **PyPDFLoader** (default for PDFs): Produces one `Document` per page with `{"page": N, "source": "path"}` metadata. The `page` key is 0-based. ✅
- **Mistral OCR loader**: Produces `{"page": N, "page_label": N+1, "total_pages": T}`. ✅
- **Tika, Docling, Datalab Marker, MinerU**: Return a single Document for the whole file — **no per-page metadata**. ❌

The `filter_metadata()` function (`retrieval/vector/utils.py:3`) strips `"pages"` (plural) but preserves `"page"` (singular), so page metadata survives to the vector DB.

**For multi-chunk citations from the same document**: The citation modal iterates over `mergedDocuments` — we could take the minimum page number and open the PDF to that page.

### Question 3: Can we add a link icon to the title?

**YES — trivial UI change.**

Currently the title in `CitationModal.svelte:76-86` is an `<a>` tag with underline on hover. We can add an external-link icon (square with outgoing arrow) before or after the text. The codebase uses Heroicons — there's likely an `ExternalLink` or `ArrowTopRightOnSquare` icon available.

**Implementation**: Add an SVG icon inline or import one from `$lib/components/icons/`. The icon should be small (size-4) and placed at the beginning of the title.

### Question 4: Can we have doc preview as default?

**YES — follows the `FileItemModal` tab pattern.**

`FileItemModal.svelte:322-345` already has a tab switcher with "Content" (parsed text) and "Preview" (native rendering). Currently "Content" is the default (`selectedTab = ''`).

**For the citation modal**:
- Add the same tab system (Preview / Content)
- Default to "Preview" tab when the file type supports native rendering (PDF, images, audio)
- Fall back to "Content" tab for unsupported types
- This means `selectedTab = 'preview'` as default when `isPDF` or similar

### Question 5: Can we show page numbers per chunk?

**YES — already partially implemented!**

`CitationModal.svelte:155-160` already shows page numbers:
```svelte
{#if Number.isInteger(document?.metadata?.page)}
    <span class="text-sm text-gray-500 dark:text-gray-400">
        ({$i18n.t('page')} {document.metadata.page + 1})
    </span>
{/if}
```

**However**, this only works when the metadata includes `page`. As noted above:
- **PyPDFLoader** (default for PDF): ✅ Sets `page` (0-based) per page
- **Mistral OCR**: ✅ Sets `page` (0-based) per page
- **Other loaders**: ❌ No page info

**For opening the PDF at the right page**: When a citation has multiple chunks from the same document, collect all page numbers and use `Math.min()` to open to the lowest page:
```js
const pages = mergedDocuments
    .filter(d => Number.isInteger(d?.metadata?.page))
    .map(d => d.metadata.page);
const startPage = pages.length > 0 ? Math.min(...pages) + 1 : undefined;
```

## Architecture: Current Citation Data Flow

```
1. Document uploaded → Loader (PyPDFLoader/Mistral/etc.) → Documents with metadata (incl. page)
2. Documents → Text splitter → Chunks with inherited metadata
3. Chunks → filter_metadata() → Vector DB (page metadata preserved)
4. Query → Vector DB search → SearchResult {documents, metadatas, distances}
5. Backend middleware → Builds sources with metadata → WebSocket "source" event
6. Frontend Chat.svelte → message.sources
7. Citations.svelte → Groups by source ID → citation = {source, document[], metadata[], distances[]}
8. CitationModal.svelte → Renders content + page number + relevance
```

## Code References

### Citation Modal (what we're improving)
- `src/lib/components/chat/Messages/Citations/CitationModal.svelte` - Main citation detail modal
- `src/lib/components/chat/Messages/Citations/CitationsModal.svelte` - Multi-citation list modal
- `src/lib/components/chat/Messages/Citations.svelte` - Citation bar below responses

### Existing Preview Infrastructure (to reuse)
- `src/lib/components/common/FileItemModal.svelte:366-371` - PDF iframe preview
- `src/lib/components/common/FileItemModal.svelte:359-365` - Audio preview
- `src/lib/components/common/FileItemModal.svelte:372-403` - Excel preview
- `src/lib/components/common/FileItemModal.svelte:322-345` - Tab switcher (Content/Preview)

### Backend File Serving
- `backend/open_webui/routers/files.py:671-738` - `GET /api/v1/files/{id}/content` serves files inline
- `backend/open_webui/routers/files.py:708-714` - PDF-specific inline Content-Disposition

### Page Metadata Sources
- `backend/open_webui/retrieval/loaders/main.py:363-365` - PyPDFLoader (default, produces per-page docs)
- `backend/open_webui/retrieval/loaders/mistral.py:555-568` - Mistral OCR (per-page docs with `page` metadata)
- `backend/open_webui/retrieval/vector/utils.py:3` - `KEYS_TO_EXCLUDE` does NOT include "page" (preserved)
- `backend/open_webui/routers/retrieval.py:1735-1747` - Metadata merge during processing

## Implementation Plan

### Phase 1: PDF Preview in Citation Modal (main value)
1. **Detect file type** in `CitationModal.svelte` from `metadata.name` extension or `metadata.content_type`
2. **Add tab switcher** (Preview / Content) — reuse pattern from `FileItemModal.svelte`
3. **Default to Preview** when file type supports it (PDF initially)
4. **Render PDF iframe** with `#page=N` fragment for page navigation
5. **Add external link icon** to the title

### Phase 2: Multi-chunk page handling
1. When multiple chunks exist for same document, compute minimum page number
2. Open PDF preview to that page
3. Show page range in UI (e.g., "pages 3, 5, 7")

### Phase 3: Additional file type previews
1. Images via `<img>` tag
2. Audio via `<audio>` tag
3. Excel via `xlsx` library (would need to fetch the file binary)

## Open Questions

1. **Auth for iframe**: The PDF iframe URL needs authentication. Currently `FileItemModal.svelte` uses direct URL without auth headers — this works because the browser sends cookies. Need to verify the citation modal iframe also works with cookie-based auth (should be fine since it's same-origin).
2. **S3 storage**: When using S3 backend, the endpoint returns a `StreamingResponse`. Need to verify this works in an iframe.
3. **Large PDFs**: Should we add a loading spinner while the PDF loads in the iframe?
4. **Mobile**: PDF iframes don't always work well on mobile. Consider a fallback or download link.
