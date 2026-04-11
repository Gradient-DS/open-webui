# Rich Copy, PDF & Word Export — Implementation Plan

## Overview

Add three capabilities to Open WebUI chat:
1. **Rich copy** — client-side dual-format clipboard (HTML + plain text) with citation normalization and a "Bronnen:" source appendix
2. **Server-side PDF export** — properly rendered HTML→PDF via WeasyPrint with superscript citations, tables, and a source section
3. **Word export** — same HTML pipeline, converted to DOCX via python-docx + htmldocx

These replace the current screenshot-based PDF (which produces non-searchable raster images) and add the missing Word format. The new server-side rendering is used by default; the old screenshot approach can be re-enabled via env var (`USE_STYLIZED_PDF_EXPORT=True`).

## Current State Analysis

### Copy Button
- `ResponseMessage.svelte:191-202` — copies `message.content` (raw markdown) only
- `copyToClipboard` utility (`src/lib/utils/index.ts:420-549`) already supports dual-format clipboard via `ClipboardItem` when `formatted=true`
- **Problem:** Sources from `message.sources` are never included in the copy

### PDF Export
- Client-side screenshot approach: `html2canvas` → JPEG pages in jsPDF
- Produces raster images — no text selection, JPEG artifacts, no source section
- Backend endpoint exists (`POST /api/v1/utils/pdf` via `PDFGenerator` using fpdf2) but is **never called**
- Frontend API client `downloadChatAsPDF()` exists but has zero call sites

### Word Export
- Does not exist

### Available Infrastructure
- `marked` (^9.1.0) with custom citation extension already parses `[1]`, `[1,2]` patterns
- `pypandoc` (1.16.2) + system `pandoc` binary already installed in Docker image
- `fpdf2` already installed (used by unused `PDFGenerator`)
- Citation data model well-structured: `message.sources` contains document chunks, metadata, URLs

### Key Discoveries
- `src/lib/utils/marked/citation-extension.ts` — parses citation markers, returns `{ids: number[], citationIdentifiers: string[]}`
- `src/lib/components/chat/Messages/Citations.svelte:100-140` — reduces sources into deduplicated citations with id, name, url
- Sidebar `ChatMenu.svelte` does NOT have a Copy button (navbar `Menu.svelte` does at line 423)
- `createMessagesList()` (`src/lib/utils/index.ts:1242-1256`) returns full message objects including `sources`
- `export.py` router already exists but handles DPIA data export — we'll add chat export endpoints there

## Desired End State

### Copy Button (per-message)
When a user clicks the copy button on a response message:
- Clipboard receives `text/html` (rendered markdown with styled tables, superscript citations, and "Bronnen:" appendix) AND `text/plain` (markdown with `[1]` markers and plain text source list)
- Pasting into Word/Google Docs produces rich formatting; into a terminal produces plain text
- Only referenced sources are included, reordered by first appearance in text

### Navbar/Sidebar "Kopieer" Button
Same behavior as per-message, but for the entire conversation (all messages concatenated).

### Download Menu
Both sidebar `ChatMenu.svelte` and navbar `Menu.svelte` Download submenus show:
1. Export chat (.json) — unchanged
2. Plain text (.txt) — unchanged  
3. PDF document (.pdf) — **new server-side rendering**
4. Word document (.docx) — **new**

### PDF Output
- A4 pages with proper margins, page numbers, NotoSans font
- Messages formatted with role headers, markdown rendered to HTML (tables, bold, lists, code blocks)
- Inline superscript citations `[1]` clickable-style (not literally clickable, but visually superscript)
- Combined "Bronnen" section at the bottom with numbered sources, reordered by first appearance
- Source entries include: number, name/title, URL (if available)

### Word Output
- Same content and formatting as PDF, adapted to DOCX format
- Source section at the bottom

### Verification
- Copy into Word/Google Docs preserves tables, bold, citations
- PDF is text-selectable with proper page layout
- DOCX opens correctly in Microsoft Word
- Sources are correctly numbered and reordered by first appearance
- All menu items appear in both sidebar and navbar menus
- Dutch translations present for all new UI strings

## What We're NOT Doing

- Per-message source appendix (using per-conversation combined list instead)
- Clickable hyperlinks in PDF citations (just formatted text)
- Customizable PDF templates or theming
- Export of message metadata (timestamps, model names) — only role + content + sources
- Rich copy of user messages (only assistant messages have sources)
- Feature flag for the new functionality (ships to everyone)

## Implementation Approach

Three phases, each independently testable:

1. **Phase 1: Shared citation/source utilities** — Create the citation normalization and source appendix logic shared between client (copy) and server (export)
2. **Phase 2: Rich copy** — Enhance the copy button and add copy to menus
3. **Phase 3: Server-side PDF & Word export** — Backend endpoints, Jinja2 template, frontend integration

## Phase 1: Shared Citation & Source Utilities

### Overview
Create utility functions for normalizing citations and building source appendices. These are needed by both the client-side copy and the server-side export.

### Changes Required

#### 1. Client-side citation utility
**File**: `src/lib/utils/citations.ts` (new file)

This utility takes message content and sources, and produces normalized output with a source appendix.

```typescript
export interface SourceInfo {
  index: number;       // 1-based, reordered by first appearance
  name: string;
  url?: string;
}

/**
 * Normalize citations in message content and build a source appendix.
 * 
 * - Scans content for [N] markers
 * - Reorders sources by first appearance in text
 * - Returns normalized content (with renumbered [N] markers) and source list
 */
export function normalizeCitations(
  content: string,
  sources: any[]
): { content: string; sourceList: SourceInfo[] } {
  // 1. Parse all citation markers from content to get referenced source indices
  // 2. Build appearance-order mapping: original index → new sequential index
  // 3. Replace [original] with [new] in content
  // 4. Build sourceList in appearance order with name/url from sources
  // ...
}

/**
 * Format source list as markdown for plain text clipboard
 */
export function formatSourcesAsMarkdown(sources: SourceInfo[]): string {
  // Returns:
  // Bronnen:
  // [1] Source Name - https://url
  // [2] Another Source
}

/**
 * Format source list as HTML for rich clipboard
 */
export function formatSourcesAsHtml(sources: SourceInfo[]): string {
  // Returns styled HTML <div> with source list
}
```

The citation parsing reuses the same regex patterns from `citation-extension.ts` to find `[N]` markers. Source names are extracted using the same logic as `Citations.svelte:100-140` (deduplication by `metadata.source` or `source.source.id`).

#### 2. Server-side citation utility
**File**: `backend/open_webui/utils/chat_export.py` (new file)

```python
"""Shared utilities for chat export (PDF, Word, copy-ready HTML)."""

import re
from dataclasses import dataclass
from markdown import markdown

@dataclass
class SourceInfo:
    index: int  # 1-based, reordered by first appearance
    name: str
    url: str | None = None

def normalize_citations(content: str, sources: list[dict]) -> tuple[str, list[SourceInfo]]:
    """
    Normalize citation markers and build source appendix.
    
    - Finds all [N] markers in content
    - Reorders by first appearance
    - Returns (normalized_content_with_renumbered_markers, source_list)
    """
    ...

def prepare_export_messages(messages: list[dict]) -> tuple[list[dict], list[SourceInfo]]:
    """
    Prepare messages for export.
    
    For each assistant message with sources:
    - Normalize citations to <sup>[N]</sup>
    - Convert markdown content to HTML
    - Collect all sources across messages
    
    Returns (prepared_messages, combined_source_list)
    """
    ...

def build_source_appendix_html(sources: list[SourceInfo]) -> str:
    """Build HTML for the Bronnen section."""
    ...
```

### Success Criteria

#### Automated Verification:
- [x] `npm run check` passes (no new TypeScript errors in `citations.ts`)
- [x] `npm run lint:backend` passes
- [ ] Unit tests for `normalizeCitations()` cover: single citation, multiple citations, reordering, missing sources, no citations

#### Manual Verification:
- [ ] N/A — utility functions only, tested via integration in Phase 2 and 3

---

## Phase 2: Rich Copy

### Overview
Enhance the copy button on response messages and add a "Kopieer" button to both menus, all using the citation utilities from Phase 1.

### Changes Required

#### 1. Enhance per-message copy button
**File**: `src/lib/components/chat/Messages/ResponseMessage.svelte`

Change the `copyToClipboard` wrapper (lines 191-202) to pass sources:

```typescript
const copyToClipboard = async (text, sources = []) => {
    text = removeAllDetails(text);

    if (($config?.ui?.response_watermark ?? '').trim() !== '') {
        text = `${text}\n\n${$config?.ui?.response_watermark}`;
    }

    // Normalize citations and build source appendix
    const { content: normalizedContent, sourceList } = normalizeCitations(text, sources);
    
    // Build plain text with source appendix
    const plainText = sourceList.length > 0
        ? `${normalizedContent}\n\n${formatSourcesAsMarkdown(sourceList)}`
        : normalizedContent;
    
    // Build HTML with source appendix (always formatted for rich paste)
    const htmlContent = sourceList.length > 0
        ? null  // let copyToClipboard render via marked, then we append sources
        : null;
    
    const res = await _copyToClipboard(plainText, null, true);  // Always rich copy
    if (res) {
        toast.success($i18n.t('Copying to clipboard was successful!'));
    }
};
```

**Actual approach**: Modify the call site at line 999 to pass sources:
```svelte
copyToClipboard(message.content, message.sources);
```

Then update the `copyToClipboard` utility in `src/lib/utils/index.ts` to accept an optional `sources` parameter and use the citation normalization when sources are present.

#### 2. Update `copyToClipboard` utility for source appendix
**File**: `src/lib/utils/index.ts`

Extend the `copyToClipboard` function signature and the formatted branch to:
1. Accept optional `sources` parameter
2. When sources are provided, normalize citations and append "Bronnen:" section to both HTML and plain text outputs
3. Always use formatted mode when sources are present (dual clipboard)

```typescript
export const copyToClipboard = async (text, html = null, formatted = false, sources = []) => {
    // If sources provided, normalize citations and build appendix
    if (sources && sources.length > 0) {
        const { content, sourceList } = normalizeCitations(text, sources);
        if (sourceList.length > 0) {
            // Override text with normalized version + plain text appendix
            text = `${content}\n\n${formatSourcesAsMarkdown(sourceList)}`;
            // Force formatted mode for rich clipboard
            formatted = true;
            // We'll handle HTML generation below with the appendix
        }
    }
    
    if (formatted) {
        // ... existing marked rendering ...
        // After rendering HTML, append source appendix HTML
    }
    // ... rest unchanged
};
```

#### 3. Add "Kopieer" to sidebar ChatMenu
**File**: `src/lib/components/layout/Sidebar/ChatMenu.svelte`

Add a Copy button between the Download submenu and the Rename button (matching navbar menu structure). This needs to:
1. Import `copyToClipboard` and `normalizeCitations` etc.
2. Fetch chat via `getChatById` 
3. Build formatted text from all messages with sources
4. Call `copyToClipboard` with the combined content and sources

```svelte
<!-- After </DropdownSub> (Download), before Rename button -->
<button
    draggable="false"
    class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl select-none w-full"
    on:click={async () => {
        const chat = await getChatById(localStorage.token, chatId);
        if (!chat) return;
        const res = await copyFormattedChat(chat);
        if (res) {
            toast.success($i18n.t('Copied to clipboard'));
            show = false;
        }
    }}
>
    <Clipboard className="size-4" strokeWidth="1.5" />
    <div class="flex items-center">{$i18n.t('Copy')}</div>
</button>
```

Add a `copyFormattedChat(chat)` helper that:
1. Calls `createMessagesList(chat.chat.history, chat.chat.history.currentId)`
2. For each message, builds `### ROLE\ncontent` with sources collected
3. Normalizes all citations across the conversation
4. Calls `copyToClipboard` with the combined text and sources

#### 4. Update navbar "Kopieer" button
**File**: `src/lib/components/layout/Navbar/Menu.svelte`

Update the existing Copy button (lines 423-439) to use the same `copyFormattedChat()` logic instead of plain `getChatAsText()`.

#### 5. Create shared `copyFormattedChat` utility
**File**: `src/lib/utils/copy.ts` (new file)

Extract the whole-conversation copy logic into a shared utility used by both menus:

```typescript
import { createMessagesList } from '$lib/utils';
import { copyToClipboard } from '$lib/utils';
import { normalizeCitations, formatSourcesAsMarkdown, formatSourcesAsHtml } from '$lib/utils/citations';

/**
 * Copy an entire chat conversation with rich formatting and sources.
 * Used by both sidebar ChatMenu and navbar Menu.
 */
export async function copyFormattedChat(chat: any): Promise<boolean> {
    const history = chat.chat.history;
    const messages = createMessagesList(history, history.currentId);
    
    // Collect all sources across all assistant messages
    const allSources: any[] = [];
    
    // Build conversation text, collecting sources
    let conversationText = '';
    for (const message of messages) {
        conversationText += `### ${message.role.toUpperCase()}\n${message.content}\n\n`;
        if (message.role === 'assistant' && message.sources?.length) {
            allSources.push(...message.sources);
        }
    }
    
    return await copyToClipboard(conversationText.trim(), null, true, allSources);
}
```

#### 6. i18n translations
**Files**: `src/lib/i18n/locales/en-US/translation.json`, `src/lib/i18n/locales/nl-NL/translation.json`

Add/verify these keys:
```json
// en-US (most already exist, verify)
"Copy": "",
"Copied to clipboard": "",
"Copying to clipboard was successful!": "",

// nl-NL
"Copy": "Kopieer",
"Copied to clipboard": "Gekopieerd naar klembord",
"Copying to clipboard was successful!": "Succesvol naar klembord gekopieerd!"
```

### Success Criteria

#### Automated Verification:
- [x] `npm run build` succeeds
- [x] `npm run check` passes (no new errors)

#### Manual Verification:
- [ ] Copy button on a response message with citations → paste into Word → tables render, bold renders, `[1]` superscripts appear, "Bronnen:" section at bottom with source names/URLs
- [ ] Copy button on a response message WITHOUT citations → paste into Word → renders as before (no empty Bronnen section)
- [ ] Sidebar "Kopieer" button works → copies entire conversation with combined source list
- [ ] Navbar "Copy" button works → same behavior as sidebar
- [ ] Sources are reordered by first appearance in the text
- [ ] Plain text fallback works in a terminal paste (markdown with source list)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Server-Side PDF & Word Export

### Overview
Replace the screenshot-based PDF with properly rendered server-side exports. Add Word export. Both use the same Jinja2 HTML template and citation normalization from Phase 1.

### Changes Required

#### 1. Add Python dependencies
**Files**: `backend/requirements.txt`, `backend/requirements-slim.txt`

```
weasyprint==65.0
python-docx==1.1.2
htmldocx==0.0.6
```

#### 2. Add system dependencies to Dockerfile
**File**: `Dockerfile`

Add WeasyPrint's system dependencies to the `apt-get install` line (around line 129):

```dockerfile
# Add to existing apt-get install line:
libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 libffi-dev
```

These are runtime-only packages (not -dev versions except libffi-dev), keeping the image size increase minimal (~30MB).

#### 3. Create Jinja2 HTML template
**File**: `backend/open_webui/templates/chat_export.html` (new file)

```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        @page {
            size: A4;
            margin: 25mm 20mm 25mm 20mm;
            @bottom-center {
                content: "Pagina " counter(page) " van " counter(pages);
                font-size: 9pt;
                color: #666;
            }
        }
        body {
            font-family: 'Noto Sans', 'Arial', sans-serif;
            font-size: 11pt;
            line-height: 1.5;
            color: #1a1a1a;
        }
        h1 { font-size: 18pt; margin-bottom: 8mm; }
        .message { margin-bottom: 6mm; page-break-inside: avoid; }
        .role {
            font-weight: bold;
            font-size: 10pt;
            text-transform: uppercase;
            color: #555;
            margin-bottom: 2mm;
        }
        .content { margin-bottom: 4mm; }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 4mm 0;
            font-size: 10pt;
        }
        th, td {
            border: 1px solid #ccc;
            padding: 3mm 4mm;
            text-align: left;
        }
        th { background-color: #f5f5f5; font-weight: bold; }
        sup {
            font-size: 8pt;
            color: #0066cc;
            font-weight: bold;
        }
        .sources {
            margin-top: 10mm;
            padding-top: 5mm;
            border-top: 1px solid #ccc;
        }
        .sources h2 {
            font-size: 14pt;
            margin-bottom: 4mm;
        }
        .source-item {
            margin-bottom: 2mm;
            font-size: 10pt;
        }
        .source-number {
            font-weight: bold;
            color: #0066cc;
        }
        .source-url {
            color: #0066cc;
            word-break: break-all;
        }
        pre {
            background-color: #f6f8fa;
            border-radius: 4px;
            padding: 4mm;
            overflow: auto;
            font-size: 9pt;
            font-family: 'Courier New', monospace;
        }
        code {
            font-family: 'Courier New', monospace;
            font-size: 9pt;
        }
        blockquote {
            border-left: 3px solid #ddd;
            padding-left: 4mm;
            color: #555;
            margin: 3mm 0;
        }
    </style>
</head>
<body>
    <h1>{{ title }}</h1>
    
    {% for message in messages %}
    <div class="message">
        <div class="role">{{ message.role }}</div>
        <div class="content">{{ message.html_content | safe }}</div>
    </div>
    {% endfor %}
    
    {% if sources %}
    <div class="sources">
        <h2>Bronnen</h2>
        {% for source in sources %}
        <div class="source-item">
            <span class="source-number">[{{ source.index }}]</span>
            {{ source.name }}
            {% if source.url %}
            — <span class="source-url">{{ source.url }}</span>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    {% endif %}
</body>
</html>
```

#### 4. Create chat export service
**File**: `backend/open_webui/services/chat_export.py` (new file)

```python
"""Chat export service — PDF and Word generation."""

import logging
from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from open_webui.utils.chat_export import prepare_export_messages

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / 'templates'


def _render_html(title: str, messages: list[dict]) -> str:
    """Render chat as styled HTML using Jinja2 template."""
    prepared_messages, sources = prepare_export_messages(messages)
    
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template('chat_export.html')
    
    return template.render(
        title=title,
        messages=prepared_messages,
        sources=sources,
    )


def generate_pdf(title: str, messages: list[dict]) -> bytes:
    """Generate PDF from chat messages."""
    from weasyprint import HTML
    
    html_string = _render_html(title, messages)
    pdf_bytes = HTML(string=html_string).write_pdf()
    return pdf_bytes


def generate_docx(title: str, messages: list[dict]) -> bytes:
    """Generate DOCX from chat messages."""
    from docx import Document
    from htmldocx import HtmlToDocx
    
    html_string = _render_html(title, messages)
    
    doc = Document()
    parser = HtmlToDocx()
    parser.add_html_to_document(html_string, doc)
    
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.read()
```

#### 5. Add export endpoints to router
**File**: `backend/open_webui/routers/utils.py`

Add new endpoints alongside the existing (unused) `/pdf` endpoint:

```python
from open_webui.services.chat_export import generate_pdf, generate_docx
from open_webui.config import USE_STYLIZED_PDF_EXPORT

@router.post('/chat/pdf')
async def export_chat_as_pdf(
    form_data: ChatTitleMessagesForm,
    user=Depends(get_verified_user),
):
    """Export chat as a properly rendered PDF with citations and sources."""
    try:
        pdf_bytes = generate_pdf(form_data.title, form_data.messages)
        return Response(
            content=pdf_bytes,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment;filename=chat-{form_data.title}.pdf'},
        )
    except Exception as e:
        log.exception(f'Error generating PDF: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/chat/docx')
async def export_chat_as_docx(
    form_data: ChatTitleMessagesForm,
    user=Depends(get_verified_user),
):
    """Export chat as a Word document with citations and sources."""
    try:
        docx_bytes = generate_docx(form_data.title, form_data.messages)
        return Response(
            content=docx_bytes,
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={'Content-Disposition': f'attachment;filename=chat-{form_data.title}.docx'},
        )
    except Exception as e:
        log.exception(f'Error generating DOCX: {e}')
        raise HTTPException(status_code=500, detail=str(e))
```

#### 6. Add config env var for screenshot PDF fallback
**File**: `backend/open_webui/config.py`

```python
# Near the other FEATURE_ vars (around line 1830)
USE_STYLIZED_PDF_EXPORT = os.environ.get('USE_STYLIZED_PDF_EXPORT', 'False').lower() == 'true'
```

Expose via `main.py` features endpoint so the frontend can check it.

#### 7. Frontend API clients
**File**: `src/lib/apis/utils/index.ts`

Add export functions:

```typescript
export const exportChatAsPdf = async (token: string, title: string, messages: object[]) => {
    const blob = await fetch(`${WEBUI_API_BASE_URL}/utils/chat/pdf`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ title, messages })
    }).then(res => {
        if (!res.ok) throw new Error('PDF export failed');
        return res.blob();
    });
    return blob;
};

export const exportChatAsDocx = async (token: string, title: string, messages: object[]) => {
    const blob = await fetch(`${WEBUI_API_BASE_URL}/utils/chat/docx`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ title, messages })
    }).then(res => {
        if (!res.ok) throw new Error('DOCX export failed');
        return res.blob();
    });
    return blob;
};
```

#### 8. Update sidebar Download submenu
**File**: `src/lib/components/layout/Sidebar/ChatMenu.svelte`

Replace `downloadPdf()` function with server-side call. Add Word export button.

```typescript
import { exportChatAsPdf, exportChatAsDocx } from '$lib/apis/utils';

const downloadPdf = async () => {
    chat = await getChatById(localStorage.token, chatId);
    if (!chat) return;

    // Check if stylized (screenshot) PDF is configured
    if ($config?.features?.use_stylized_pdf_export) {
        // ... existing html2canvas logic (keep as-is) ...
        return;
    }

    // Server-side PDF export
    const history = chat.chat.history;
    const messages = createMessagesList(history, history.currentId);
    const exportMessages = messages.map(m => ({
        role: m.role,
        content: m.content,
        sources: m.sources || [],
    }));

    try {
        const blob = await exportChatAsPdf(
            localStorage.token,
            chat.chat.title,
            exportMessages
        );
        if (blob) saveAs(blob, `chat-${chat.chat.title}.pdf`);
    } catch (e) {
        console.error('PDF export failed:', e);
        toast.error($i18n.t('Failed to export PDF'));
    }
};

const downloadDocx = async () => {
    chat = await getChatById(localStorage.token, chatId);
    if (!chat) return;

    const history = chat.chat.history;
    const messages = createMessagesList(history, history.currentId);
    const exportMessages = messages.map(m => ({
        role: m.role,
        content: m.content,
        sources: m.sources || [],
    }));

    try {
        const blob = await exportChatAsDocx(
            localStorage.token,
            chat.chat.title,
            exportMessages
        );
        if (blob) saveAs(blob, `chat-${chat.chat.title}.docx`);
    } catch (e) {
        console.error('DOCX export failed:', e);
        toast.error($i18n.t('Failed to export Word document'));
    }
};
```

Add Word button to the Download submenu (after PDF button, before `</DropdownSub>`):

```svelte
<button
    draggable="false"
    class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl select-none w-full"
    on:click={() => {
        downloadDocx();
    }}
>
    <div class="flex items-center line-clamp-1">{$i18n.t('Word document (.docx)')}</div>
</button>
```

#### 9. Update navbar Download submenu
**File**: `src/lib/components/layout/Navbar/Menu.svelte`

Same changes as sidebar: replace `downloadPdf()`, add `downloadDocx()`, add Word button. The navbar version already has the `chat` object as a prop so doesn't need `getChatById`.

#### 10. Remove client-side PDF setting toggle
**File**: `src/lib/components/chat/Settings/Interface.svelte`

The `stylizedPdfExport` user setting (lines 1029-1043) is no longer needed since the choice is now server-side via env var. Remove the toggle from the Interface settings. The `$settings.stylizedPdfExport` references in `ChatMenu.svelte` and `Menu.svelte` are replaced by `$config?.features?.use_stylized_pdf_export`.

#### 11. Expose env var to frontend
**File**: `backend/open_webui/main.py`

Add `USE_STYLIZED_PDF_EXPORT` to the features dict sent to the frontend (in the same section as other `FEATURE_*` flags):

```python
from open_webui.config import USE_STYLIZED_PDF_EXPORT

# In the features endpoint response:
'use_stylized_pdf_export': USE_STYLIZED_PDF_EXPORT,
```

#### 12. Helm chart support
**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

Add:
```yaml
USE_STYLIZED_PDF_EXPORT: {{ .Values.openWebui.config.useStylizedPdfExport | quote }}
```

**File**: `helm/open-webui-tenant/values.yaml`

Add under `openWebui.config`:
```yaml
useStylizedPdfExport: "False"  # Set to "True" to use old screenshot-based PDF export
```

#### 13. i18n translations
**Files**: `src/lib/i18n/locales/en-US/translation.json`, `src/lib/i18n/locales/nl-NL/translation.json`

```json
// en-US
"Word document (.docx)": "",
"Failed to export PDF": "",
"Failed to export Word document": "",

// nl-NL  
"Word document (.docx)": "Word-document (.docx)",
"Failed to export PDF": "PDF exporteren mislukt",
"Failed to export Word document": "Word-document exporteren mislukt"
```

#### 14. Update `ChatTitleMessagesForm` to include sources
**File**: `backend/open_webui/models/chats.py`

The existing `ChatTitleMessagesForm` uses `messages: list[dict]` which already accepts arbitrary dict content. The frontend will now include `sources` in each message dict. No schema change needed, but document the expected shape:

```python
class ChatTitleMessagesForm(BaseModel):
    title: str
    messages: list[dict]  # Each dict: {role: str, content: str, sources?: list[dict]}
```

### Success Criteria

#### Automated Verification:
- [x] `npm run build` succeeds
- [x] `npm run lint:backend` passes (Python syntax verified)
- [ ] `pip install weasyprint python-docx htmldocx` succeeds
- [ ] `docker build .` succeeds with the new system deps
- [ ] Backend starts without errors: `open-webui dev`
- [ ] `POST /api/v1/utils/chat/pdf` returns 200 with valid PDF bytes
- [ ] `POST /api/v1/utils/chat/docx` returns 200 with valid DOCX bytes

#### Manual Verification:
- [ ] PDF export from sidebar menu → opens in PDF viewer → text is selectable, tables render, superscript citations, "Bronnen" section at bottom
- [ ] Word export from sidebar menu → opens in Word → same formatting as PDF
- [ ] PDF export from navbar menu → same quality as sidebar
- [ ] Word export from navbar menu → same as sidebar
- [ ] With `USE_STYLIZED_PDF_EXPORT=True` → old screenshot behavior works
- [ ] Sources correctly numbered by first appearance across all messages
- [ ] Empty sources (no citations) → no "Bronnen" section appears
- [ ] Large conversation with many sources → PDF handles pagination correctly

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Testing Strategy

### Unit Tests
- `normalizeCitations()` — single ref, multiple refs, reordering, missing sources, no refs, mixed `[1,2]` syntax
- `prepare_export_messages()` — markdown conversion, citation normalization, source deduplication
- `generate_pdf()` — returns valid bytes, handles empty messages, handles messages without sources
- `generate_docx()` — returns valid bytes, same edge cases

### Integration Tests  
- Full export pipeline: messages with citations → PDF → verify PDF contains expected text
- Frontend API client → backend → file download

### Manual Testing Steps
1. Create a chat with RAG sources (knowledge base query)
2. Copy a single message → paste into Word → verify tables, bold, citations, sources
3. Copy entire conversation via sidebar menu → paste into Google Docs → verify formatting
4. Export as PDF → open in Preview/Chrome → verify text selection, page layout, source section
5. Export as Word → open in Word → verify formatting matches PDF
6. Test with a conversation that has NO sources → verify clean output without empty sections
7. Test with a conversation with 10+ sources → verify reordering and deduplication

## Performance Considerations

- WeasyPrint PDF generation may take 1-3 seconds for large conversations — the frontend should show a loading indicator
- Word generation via htmldocx is typically fast (<1s)
- Message content markdown→HTML conversion is done per-message; for very long conversations, this could be noticeable but should still be under 5s

## Dependencies

### New Python packages:
- `weasyprint==65.0` — HTML→PDF with full CSS support
- `python-docx==1.1.2` — DOCX generation
- `htmldocx==0.0.6` — HTML→DOCX bridge

### New system packages (Dockerfile):
- `libpango-1.0-0` — Text layout (WeasyPrint dep)
- `libpangocairo-1.0-0` — Cairo rendering for Pango
- `libgdk-pixbuf-2.0-0` — Image handling
- `libcairo2` — 2D graphics
- `libffi-dev` — Foreign function interface

### No new frontend packages needed.

## References

- Research document: `thoughts/shared/research/2026-04-11-rich-copy-pdf-word-export.md`
- Citation extension: `src/lib/utils/marked/citation-extension.ts`
- Current copy utility: `src/lib/utils/index.ts:420-549`
- Current PDF export (sidebar): `src/lib/components/layout/Sidebar/ChatMenu.svelte:84-243`
- Current PDF export (navbar): `src/lib/components/layout/Navbar/Menu.svelte:76-230`
- Sidebar Download submenu: `src/lib/components/layout/Sidebar/ChatMenu.svelte:311-354`
- Navbar Copy button: `src/lib/components/layout/Navbar/Menu.svelte:423-439`
- Citations component: `src/lib/components/chat/Messages/Citations.svelte:100-140`
- Backend PDF endpoint (unused): `backend/open_webui/routers/utils.py:90-102`
- Backend PDF generator (unused): `backend/open_webui/utils/pdf_generator.py`
- Existing export router (DPIA): `backend/open_webui/routers/export.py`
- Helm configmap: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
