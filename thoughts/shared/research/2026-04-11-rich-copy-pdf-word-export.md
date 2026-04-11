---
date: 2026-04-11T09:56:00+02:00
researcher: Claude (Opus 4.6)
git_commit: 4b12eeb40e2d32328c3c083c3696358acbabc0bc
branch: dev
repository: open-webui
topic: "Rich copy, improved PDF export, and Word export for chat messages"
tags: [research, codebase, copy, pdf, word, export, citations, clipboard]
status: complete
last_updated: 2026-04-11
last_updated_by: Claude (Opus 4.6)
---

# Research: Rich Copy, Improved PDF Export, and Word Export

**Date**: 2026-04-11T09:56:00+02:00
**Researcher**: Claude (Opus 4.6)
**Git Commit**: 4b12eeb40e2d32328c3c083c3696358acbabc0bc
**Branch**: dev
**Repository**: open-webui

## Research Question

The current copy function copies raw markdown without sources. The PDF export is a screenshot-based JPEG dump with no citation appendix. The user wants:
1. Rich copy (HTML+plain text dual clipboard) with citations appended
2. Proper PDF export with markdown rendering, superscript citations, and a source section
3. Word (.docx) export with the same formatting
4. These options available in the sidebar chat menu's Download submenu

## Summary

### Current State

| Feature | Status | Quality |
|---------|--------|---------|
| Copy button | Works, copies raw markdown OR formatted HTML (toggle) | No sources/citations included |
| PDF (stylized) | Screenshot via html2canvas → JPEG pages | Raster image, no text selection, no sources section |
| PDF (plain) | jsPDF plain text rendering | Raw markdown dumped as 8pt text, no formatting |
| PDF (server-side) | Backend endpoint exists (`POST /api/v1/utils/pdf`) | **Never called** from frontend. Uses fpdf2, basic HTML |
| Word export | Does not exist | — |

### Key Finding: Infrastructure Already Exists

- **`marked`** (^9.1.0) is already used for markdown→HTML with custom extensions (KaTeX, citation, footnote, highlight.js)
- **`copyToClipboard`** already supports dual-format clipboard (`text/html` + `text/plain`) via `ClipboardItem` when `formatted=true`
- **`pypandoc`** (1.16.2) is already a backend dependency — can convert HTML→DOCX and HTML→PDF
- **`fpdf2`** is already installed and used by the unused `PDFGenerator`
- **`Markdown`** (Python, 3.10.2) and **`beautifulsoup4`** are backend dependencies
- The citation extension (`src/lib/utils/marked/citation-extension.ts`) already parses `[1]`, `[1,2]`, `[1#suffix]` patterns

## Detailed Findings

### 1. Copy Button — Current Implementation

**File:** `src/lib/components/chat/Messages/ResponseMessage.svelte:191-202`

```
copyToClipboard(message.content)  // line 999
```

The local wrapper:
1. Strips `<details>` blocks (thinking/reasoning) via `removeAllDetails()`
2. Appends watermark if configured (`$config?.ui?.response_watermark`)
3. Delegates to `_copyToClipboard(text, null, $settings?.copyFormatted ?? false)`

**Problem:** Only `message.content` is passed. The `message.sources` (citation metadata) is a separate field and is never included. Even with `copyFormatted=true`, the HTML rendering doesn't include a sources appendix.

**The `copyToClipboard` utility** (`src/lib/utils/index.ts:420-549`):
- When `formatted=true`: renders markdown via `marked` → wraps in styled `<div>` → creates `ClipboardItem` with both `text/html` and `text/plain`
- When `formatted=false` (default): plain `navigator.clipboard.writeText()`
- Already uses `marked` with KaTeX, highlight.js, and citation extensions

**The native selection copy handler** (`ResponseMessage.svelte:565-591`) independently handles Ctrl+C on selected text — copies both `text/html` and `text/plain` from the DOM selection. This already works well for partial selections.

### 2. Citation/Source Data Model

**Source data lives on `message.sources` (or `message.citations`):**

`ResponseMessage.svelte:90` declares `sources?: string[]` in the message type.

The `Citations` component (`src/lib/components/chat/Messages/Citations.svelte:100-140`) reduces sources into citations:

```typescript
// Each source has: document[], metadata[], distances[], source (with name, url, embed_url)
citations = sources.reduce((acc, source) => {
    source?.document?.forEach((document, index) => {
        const metadata = source?.metadata?.[index];
        const id = metadata?.source ?? source?.source?.id ?? 'N/A';
        // Deduplicates by id, accumulates documents per source
    });
}, []);
```

The **citation extension** (`src/lib/utils/marked/citation-extension.ts`) parses inline references like `[1]`, `[1,2]`, `[1#suffix]` in the markdown content. The renderer just returns `token.raw` (fallback text), but the Svelte markdown renderer (`MarkdownTokens.svelte`) renders them as clickable superscript buttons.

**Key insight:** The citation markers in `message.content` (e.g., `[1]`) correspond by index to entries in `message.sources`. The mapping is positional: `[1]` → `sources[0]`, `[2]` → `sources[1]`, etc.

### 3. PDF Export — Current Implementation

**Two menu locations, identical code:**
- Sidebar: `src/lib/components/layout/Sidebar/ChatMenu.svelte:84-243`
- Navbar: `src/lib/components/layout/Navbar/Menu.svelte:76-230`

**Stylized mode (default):** Screenshot approach
1. Renders full `<Messages>` component in a hidden div (`id="full-messages-container"`)
2. Clones DOM, sets to 800px width, positions off-screen
3. `html2canvas-pro` renders to canvas at 2x scale
4. Slices canvas into A4-sized chunks, converts each to JPEG (0.7 quality)
5. `jsPDF` assembles pages from JPEG images

**Problems:**
- Output is raster images (no text selection in PDF)
- Citations panel may not be expanded/visible in the screenshot
- No dedicated source section
- JPEG compression artifacts
- Dark mode creates black background PDFs

**Plain text mode:** Raw text dump
- `getChatAsText()` → `### ROLE\ncontent\n\n` for each message
- jsPDF renders as 8pt monospace text with manual line wrapping
- No formatting, no citations, no sources

**Server-side endpoint** (`backend/open_webui/routers/utils.py:90-102`):
- `POST /api/v1/utils/pdf` — accepts `ChatTitleMessagesForm`
- Uses `PDFGenerator` (`backend/open_webui/utils/pdf_generator.py`)
- Uses fpdf2 with NotoSans fonts and HTML rendering via `pdf.write_html()`
- **Never called from frontend** — `downloadChatAsPDF()` in `src/lib/apis/utils/index.ts:94` is imported but has zero call sites
- Does not handle citations or sources either

### 4. Chat Sidebar Menu Structure

**File:** `src/lib/components/layout/Sidebar/ChatMenu.svelte:311-354`

Download submenu (inside `DropdownSub`):
1. Export chat (.json) — gated by `$user.permissions?.chat?.export`
2. Plain text (.txt)
3. PDF document (.pdf)

**Where to add new items:** After the PDF button (line 353), before `</DropdownSub>` (line 354).

**Navbar menu** has the same structure at `src/lib/components/layout/Navbar/Menu.svelte:381-421`, plus an existing "Copy" button at line 423 that calls `copyToClipboard(await getChatAsText())`.

### 5. Available Libraries

**Frontend (package.json):**
| Library | Version | Relevant Use |
|---------|---------|-------------|
| `marked` | ^9.1.0 | Markdown → HTML (already configured with extensions) |
| `katex` | ^0.16.22 | Math rendering |
| `highlight.js` | ^11.9.0 | Code syntax highlighting |
| `jspdf` | ^4.0.0 | Client-side PDF (currently used for screenshot approach) |
| `html2canvas-pro` | ^1.5.11 | DOM screenshot (currently used) |
| `file-saver` | ^2.0.5 | `saveAs()` for downloads |
| `dompurify` | ^3.2.6 | HTML sanitization |

**Backend (pyproject.toml):**
| Library | Version | Relevant Use |
|---------|---------|-------------|
| `fpdf2` | 2.8.7 | PDF generation (used by unused `PDFGenerator`) |
| `Markdown` | 3.10.2 | Python markdown → HTML |
| `pypandoc` | 1.16.2 | **Document format conversion** (HTML → DOCX, HTML → PDF) |
| `beautifulsoup4` | 4.14.3 | HTML manipulation |
| `docx2txt` | 0.9 | DOCX text extraction (read direction only) |

**Not installed but potentially needed:**
| Library | Purpose |
|---------|---------|
| `weasyprint` | High-quality HTML → PDF with CSS @page support |
| `python-docx` | Python DOCX generation |
| `htmldocx` | HTML → DOCX conversion via python-docx |

## Architecture Recommendations

Based on lessons from the soev.ai project and the existing codebase:

### Recommended Approach

#### 1. Rich Copy (Client-Side)

Keep copy client-side for instant feedback. Enhance the existing `copyToClipboard` flow:

1. **Extend `ResponseMessage.svelte` copy handler** to pass `message.sources` alongside `message.content`
2. **Create `formatMessageForCopy()` utility** in `src/lib/utils/` that:
   - Takes `content` + `sources` 
   - Normalizes citation markers (`[1]`, `[2]`) to numbered superscripts
   - Appends a "Bronnen:" section with source name, URL
   - Returns both markdown (for `text/plain`) and HTML (for `text/html`)
3. **Use existing `marked` setup** for markdown → HTML conversion
4. **Write dual-format clipboard** using existing `ClipboardItem` pattern

#### 2. PDF & Word Export (Server-Side)

Server-side is the right call — one HTML template serves both formats:

1. **Create `backend/open_webui/routers/export.py`** (new router, separate from upstream `utils.py`)
   - `POST /api/v1/export/chat/pdf` 
   - `POST /api/v1/export/chat/docx`
2. **Shared `_prepare_export_data()`** function:
   - Accept chat messages + sources
   - Normalize citations to `<sup>[1]</sup>` numbered references
   - Convert markdown content to HTML (using Python `Markdown` library)
   - Build source appendix per message or per conversation
3. **Jinja2 HTML template** (`backend/open_webui/templates/chat_export.html`):
   - A4 page layout, proper fonts, page numbers
   - Question/answer formatting
   - Source section with superscript references
4. **PDF via WeasyPrint** (new dependency) — proper CSS support with `@page` rules
5. **Word via pypandoc** (already installed) or `python-docx` + `htmldocx` (new dependencies)
6. **Frontend API clients** in `src/lib/apis/export/` — thin fetch + blob download

#### 3. Menu Integration

Add to both `ChatMenu.svelte` and `Navbar/Menu.svelte`:
- "Word document (.docx)" button in the Download submenu
- Optionally replace existing PDF button to use the server-side endpoint
- Consider a "Kopieer geformateerd" (copy formatted) option outside the Download submenu

### Key Design Decisions

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| Copy: client vs server | Client-side | Instant feedback, data already in Svelte stores |
| PDF/Word: client vs server | Server-side | One HTML template for both formats, no client-side library bloat |
| Citation resolution | Shared utility (both client + server) | Citation mapping logic needed in both contexts |
| New router vs modify utils.py | New `export.py` router | Avoid upstream merge conflicts on utils.py |
| WeasyPrint vs fpdf2 | WeasyPrint for PDF | Much better CSS/HTML support, proper page layout |
| Word generation | pypandoc (already installed) | Already a dependency, handles HTML→DOCX well |
| Source section | Per-conversation appendix | Collect all cited sources, deduplicate, list at bottom |

### Data Flow

```
=== Copy (Client-Side) ===
ResponseMessage.svelte
  → formatMessageForCopy(message.content, message.sources)
  → marked.parse() for HTML
  → append "Bronnen:" section
  → ClipboardItem({ text/html, text/plain })

=== PDF/Word Export (Server-Side) ===
ChatMenu.svelte → POST /api/v1/export/chat/{format}
  → send { title, messages (with sources) }
  → _prepare_export_data()
    → normalize citations → <sup>[n]</sup>
    → markdown → HTML per message
    → build source appendix
  → Jinja2 template → styled HTML
  → WeasyPrint (PDF) or pypandoc (DOCX)
  → Response(blob) → saveAs()
```

## Code References

- `src/lib/utils/index.ts:420-549` — `copyToClipboard()` utility with dual-format clipboard support
- `src/lib/components/chat/Messages/ResponseMessage.svelte:191-202` — Copy button handler (only passes content, not sources)
- `src/lib/components/chat/Messages/ResponseMessage.svelte:565-591` — Native selection copy handler
- `src/lib/components/chat/Messages/Citations.svelte:100-140` — Citation reduction from sources
- `src/lib/utils/marked/citation-extension.ts` — Citation marker parser for `[1]`, `[1,2]` patterns
- `src/lib/components/layout/Sidebar/ChatMenu.svelte:84-243` — Current PDF export (screenshot-based)
- `src/lib/components/layout/Sidebar/ChatMenu.svelte:311-354` — Download submenu structure
- `src/lib/components/layout/Navbar/Menu.svelte:76-230` — Navbar PDF export (identical to sidebar)
- `src/lib/components/layout/Navbar/Menu.svelte:423-439` — Navbar copy button
- `src/lib/apis/utils/index.ts:94-119` — `downloadChatAsPDF()` API client (exists but never called)
- `backend/open_webui/routers/utils.py:90-102` — Server-side PDF endpoint (exists but never called)
- `backend/open_webui/utils/pdf_generator.py` — `PDFGenerator` using fpdf2 (unused)

## Open Questions

1. **Source section scope:** Per-message source appendix or per-conversation? Per-conversation (deduplicated) is cleaner but loses per-turn context.
2. **Citation format:** Use `[1]` style or superscript `¹` style in exported documents?
3. **WeasyPrint system dependency:** WeasyPrint requires system libraries (Pango, Cairo, GDK-PixBuf). Need to verify these are available in the Docker image. Alternative: use fpdf2's `write_html()` which is already installed but has weaker CSS support.
4. **pypandoc requires Pandoc:** pypandoc is installed but needs the `pandoc` binary. Need to verify it's in the Docker image. Alternative: `python-docx` + `htmldocx` for more control.
5. **Should the existing screenshot PDF be replaced or kept as an alternative?** The screenshot approach captures exact visual fidelity but produces non-searchable raster PDFs.
6. **Feature flag?** Should the new export options be behind a feature flag for gradual rollout?
