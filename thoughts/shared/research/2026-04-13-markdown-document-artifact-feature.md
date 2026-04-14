---
date: 2026-04-13T08:56:02+0200
researcher: Lex Lubbers
git_commit: a79c7c9c044479458f45620c6db3e6b2e631079a
branch: dev
repository: open-webui
topic: 'Markdown Document Action — tool-invoked sidepanel with docx/pdf/txt/md export'
tags:
  [
    research,
    codebase,
    code-interpreter,
    artifacts,
    export,
    docx,
    pdf,
    tool-calling,
    sidepanel,
    markdown
  ]
status: complete
last_updated: 2026-04-13
last_updated_by: Lex Lubbers
---

# Research: Markdown Document Action — tool-invoked sidepanel with docx/pdf/txt/md export

**Date**: 2026-04-13T08:56:02+0200
**Researcher**: Lex Lubbers
**Git Commit**: a79c7c9c044479458f45620c6db3e6b2e631079a
**Branch**: dev
**Repository**: open-webui

## Research Question

Build a new model-invokable action — analogous to "code execution" — that lets the model write a markdown document. The document should:

1. Render in a sidepanel similar to Artifacts
2. Be downloadable as docx, txt, pdf, and markdown (mirroring the chat export flow)

Research all the relevant code needed to design and implement this feature.

## Summary

This is a combine-three-existing-subsystems feature — nothing here needs to be invented from scratch:

1. **Invocation** — mirror the **Code Interpreter** pattern: XML tag emission by the model (`<code_interpreter type="code" lang="python">`), backend detection in `middleware.py`, system-prompt injection, feature flag + user permission + model capability + per-chat toggle.
2. **Sidepanel rendering** — mirror the **Artifacts** subsystem: right-side `PaneGroup` inside `ChatControls.svelte`, Svelte stores (`showArtifacts`, `artifactContents`), automatic detection in `ContentRenderer.svelte` on code-block language, with navigation/copy/download/fullscreen controls.
3. **Export** — reuse the **Chat Export** stack already shipped in PR #93 (`256eb08f5`): frontend `file-saver` + `jspdf`, backend `weasyprint` (PDF) + `python-docx` + `htmldocx` (DOCX) + Jinja2 template. A new `document_export.py` service + `POST /api/v1/utils/document/{pdf,docx}` endpoints would sit next to the existing `chat_export` service with minimal new code.

The cleanest shape is: new XML tag `<document title="…">…markdown…</document>` (or a native tool call `write_document`), detected by a new parser hook in `middleware.py`, stored as a `details type="document"` token, and surfaced by extending `ContentRenderer` to push markdown content into a new `documentContents` store that `Artifacts.svelte` (or a new `Document.svelte` sibling) consumes in the same ChatControls pane.

## Detailed Findings

### 1. Code Interpreter — invocation pattern to mirror

**Model invocation (XML tags, not native tool by default):**

- Tag format: `<code_interpreter type="code" lang="python">…</code_interpreter>`
- Detection: regex in `backend/open_webui/utils/middleware.py:3240-3430` (streaming tag output handler)
- System prompt injection: `backend/open_webui/utils/middleware.py:2362-2392` — adds `DEFAULT_CODE_INTERPRETER_PROMPT` to user message when feature enabled and native function calling is off
- Default prompt text: `backend/open_webui/config.py:2404-2419`
- Native alternative: `backend/open_webui/tools/builtin.py:362-519` exposes `execute_code` as a registered OpenAI-compatible tool; `backend/open_webui/utils/tools.py:478-485` gates the registration

**Feature gating (four layers):**

- Global config: `ENABLE_CODE_INTERPRETER` in `backend/open_webui/config.py:2331-2342`
- User permission: `USER_PERMISSIONS_FEATURES_CODE_INTERPRETER` in `config.py:1467-1548`
- Model capability: `code_interpreter: true` in model metadata; default in `src/lib/constants.ts:100-106`
- Per-chat toggle: `Chat.svelte:372-378` computes `codeInterpreterEnabled`; sent via `features.code_interpreter` in request metadata (`Chat.svelte:2198-2200`)

**UI toggle:**

- Button in `src/lib/components/chat/MessageInput.svelte:1890-1910` (gated by `showCodeInterpreterButton`, `MessageInput.svelte:526-532`)
- Pinnable via `src/lib/components/chat/MessageInput/InputMenu.svelte`

**Backend execution endpoint** (only for code — a document action wouldn't need an equivalent "execute"):

- `POST /api/v1/utils/code/execute` — `backend/open_webui/routers/utils.py:58-88`

### 2. Artifacts — sidepanel pattern to mirror

**Component:**

- `src/lib/components/chat/Artifacts.svelte` (270 lines) — header with version prev/next, copy, download, fullscreen, close. Renders iframe (HTML/CSS/JS) or `SvgPanZoom` (SVG).
- Mounted inside `src/lib/components/chat/ChatControls.svelte:33, 289-290, 435-436` — both the mobile drawer and the desktop right pane use the same component.

**Layout:**

- Desktop: `PaneGroup` horizontal split in `src/lib/components/chat/Chat.svelte:2943` — messages left (50%), `ChatControls` right (min 30%), `PaneResizer` between.
- Mobile: full-screen drawer.

**Stores** (`src/lib/stores/index.ts`):

- `showControls` (line 99) — is the right pane visible
- `showArtifacts` (line 102) — is Artifacts the active panel
- `artifactCode` (line 109) — currently highlighted artifact
- `artifactContents` (line 110) — array of `{ type: 'iframe' | 'svg', content }`

**Detection & population:**

- `src/lib/components/chat/Messages/ContentRenderer.svelte:177-196` — condition: `isFeatureEnabled('artifacts') && $settings.detectArtifacts && (['html','svg'].includes(lang) || (lang==='xml' && code.includes('svg'))) && !$mobile && $chatId` → sets both `showArtifacts` and `showControls` true.
- `src/lib/components/chat/Chat.svelte:1017-1065` — `getContents()` walks history, calls `getCodeBlockContents()` (from `src/lib/utils/index.ts:1740-1832`), builds the iframe/svg array, wraps HTML blocks in full `<!DOCTYPE html>`.

**Feature flags:**

- `settings.detectArtifacts` — user-level opt-out
- `config.features.feature_artifacts` — admin/SaaS gate
- Wrapped by `isFeatureEnabled('artifacts')`

### 3. Chat export — stack to reuse for docx/pdf/txt/md

**Frontend entry points (already exist for chats — pattern to copy):**

- `src/lib/components/layout/Sidebar/ChatMenu.svelte:273-326` — Download submenu with JSON/TXT/PDF/DOCX options
- `src/lib/components/layout/Navbar/Menu.svelte:338+` — same menu in navbar

**Frontend handlers** (all in `ChatMenu.svelte`):

- `downloadJSONExport()` — line 207
- `downloadTxt()` — line 73
- `downloadPdf()` — line 87 (branches on `config.features.use_stylized_pdf_export`: old html2canvas→jsPDF vs new server-side WeasyPrint)
- `downloadDocx()` — line 184 — calls `exportChatAsDocx()` + `file-saver`

**Frontend API client:**

- `src/lib/apis/utils/index.ts:121-134` — `exportChatAsPdf(token, title, messages)`
- `src/lib/apis/utils/index.ts:136-149` — `exportChatAsDocx(token, title, messages)`

**Frontend libraries** (already in `package.json`):

- `file-saver ^2.0.5`, `jspdf ^4.0.0`, `html2canvas-pro ^1.5.11`, `marked ^9.1.0`

**Backend endpoints:**

- `POST /api/v1/utils/chat/pdf` — `backend/open_webui/routers/utils.py:120-137` — uses WeasyPrint
- `POST /api/v1/utils/chat/docx` — `backend/open_webui/routers/utils.py:140-157` — uses python-docx + htmldocx
- Legacy screenshot PDF: `POST /api/v1/utils/pdf` — `utils.py:105-117` — fpdf2 (unused after PR #93)

**Backend services:**

- `backend/open_webui/services/chat_export.py:33-39` — `generate_pdf()` via WeasyPrint
- `backend/open_webui/services/chat_export.py:42-113` — `generate_docx()` via python-docx + htmldocx
- `backend/open_webui/utils/chat_export.py` — citation normalization, source deduplication, `_md_to_html()`

**Backend libraries** (already in `requirements.txt`):

- `weasyprint==68.0`, `python-docx==1.1.2`, `htmldocx==0.0.6`, `Markdown==3.10.2`

**Template:**

- `backend/open_webui/templates/chat_export.html` — Jinja2, A4 page styling, NotoSans font, citation footer. Directly adaptable for a document template (just drop the multi-message loop, render a single title + body).

**Historical context:**

- Commit `256eb08f5` — "feat: export function to word/rich copy and pdf"
- PR #93
- Plan: `thoughts/shared/plans/2026-04-11-rich-copy-pdf-word-export.md`

### 4. Markdown rendering — reusable for sidepanel body

**Main components:**

- `src/lib/components/chat/Messages/Markdown.svelte` — entry; calls `marked.lexer()`, requestAnimationFrame-throttled for streaming (line 80-83).
- `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte` (554 lines) — dispatcher for all token types. Lines 56-76 group consecutive `details` tokens (tool_calls, reasoning, code_interpreter) for collapsible rendering.

**Extensions** (`src/lib/utils/marked/`):

- `extension.ts` — `<details>` parser capturing attributes (lines 19-95) → this is exactly the hook a new `type="document"` details token would use
- katex, colon-fence, strikethrough, mention, footnote, citation extensions

**Sub-renderers reusable for a document body:**

- `CodeBlock.svelte` — syntax-highlighted code with copy/download
- `KatexRenderer.svelte`, `AlertRenderer.svelte`
- `HTMLToken.svelte` — DOMPurify-sanitized HTML, also handles video/audio/iframe
- Native tables w/ CSV export (`MarkdownTokens.svelte:180-266`)

### 5. Tool call / details-token plumbing (the third viable invocation path)

- Backend `serialize_output()` in `backend/open_webui/utils/middleware.py:406-528` turns Responses-API output items into HTML `<details type="tool_calls" id=… name=… arguments=… result=… files=… embeds=…>` — with JSON-escaped attributes.
- The marked details extension parses these back into a token.
- `src/lib/components/common/ToolCallDisplay.svelte` (300+ lines) renders the result with full markdown (lines 200+) and even supports `embeds` for iframe side-panel rendering (lines 91-109).
- Native-tool path: `backend/open_webui/utils/tools.py:148-347` loads tools and converts them to OpenAI function schema. A `write_document(title, markdown)` builtin tool could sit next to `execute_code` in `backend/open_webui/tools/builtin.py`.

## Code References

### Code Interpreter (pattern to copy for invocation)

- `backend/open_webui/utils/middleware.py:2362-2392` — prompt injection
- `backend/open_webui/utils/middleware.py:3240-3430` — tag output detection
- `backend/open_webui/utils/middleware.py:4461-4590` — post-stream execution trigger
- `backend/open_webui/tools/builtin.py:362-519` — `execute_code` native tool
- `backend/open_webui/utils/tools.py:478-485` — conditional tool registration
- `backend/open_webui/config.py:2331-2434` — all CODE*INTERPRETER*\* config + default prompt
- `backend/open_webui/routers/utils.py:58-88` — `POST /utils/code/execute`
- `src/lib/components/chat/Chat.svelte:372-378, 2198-2200` — feature gate + metadata injection
- `src/lib/components/chat/MessageInput.svelte:526-532, 1890-1910` — toggle button
- `src/lib/components/chat/Messages/CodeBlock.svelte` — result renderer
- `src/lib/components/chat/Messages/Markdown/ConsecutiveDetailsGroup.svelte` — summary UI for multi-block results
- `src/lib/constants.ts:100-106` — `DEFAULT_CAPABILITIES`

### Artifacts (pattern to copy for sidepanel)

- `src/lib/components/chat/Artifacts.svelte` — the whole file (270 lines)
- `src/lib/components/chat/ChatControls.svelte:33, 250-257, 289-290, 435-436` — mounting
- `src/lib/components/chat/Chat.svelte:1017-1065, 2943, 3173-3194` — PaneGroup + content extraction
- `src/lib/components/chat/Messages/ContentRenderer.svelte:177-196` — detection + panel open
- `src/lib/stores/index.ts:99, 102, 109, 110` — stores
- `src/lib/utils/index.ts:1740-1832` — `getCodeBlockContents()`
- `src/lib/components/common/SVGPanZoom.svelte` — pan/zoom viewer

### Chat export (reuse for downloads)

- `src/lib/components/layout/Sidebar/ChatMenu.svelte:73, 87, 184, 207, 273-343` — all download handlers + menu
- `src/lib/components/layout/Navbar/Menu.svelte:338+` — duplicate menu
- `src/lib/apis/utils/index.ts:121-149` — `exportChatAsPdf`, `exportChatAsDocx`
- `src/lib/utils/index.ts:420-549` — `copyToClipboard()` with rich ClipboardItem
- `src/lib/utils/copy.ts` — `copyFormattedChat()`
- `src/lib/utils/citations.ts` — citation normalization
- `backend/open_webui/routers/utils.py:105-157` — all three PDF/DOCX endpoints
- `backend/open_webui/services/chat_export.py:33-113` — WeasyPrint + python-docx generators
- `backend/open_webui/utils/chat_export.py` — `prepare_export_messages`, `_md_to_html`, `normalize_citations`
- `backend/open_webui/templates/chat_export.html` — Jinja2 template

### Markdown rendering (reusable as body renderer)

- `src/lib/components/chat/Messages/Markdown.svelte:1-94`
- `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte:1-554` (especially lines 56-76, 379-433)
- `src/lib/utils/marked/extension.ts:19-95` — `<details>` parser
- `src/lib/components/common/ToolCallDisplay.svelte:91-109, 186-195, 200+` — details-token result renderer with embeds field

### Tool-call plumbing (alternative invocation path)

- `backend/open_webui/utils/middleware.py:406-528` — `serialize_output()`
- `backend/open_webui/utils/misc.py:132-276` — `convert_output_to_messages()` multi-turn reconstruction
- `backend/open_webui/utils/tools.py:148-347` — tool loading & OpenAPI tool servers
- `backend/open_webui/routers/tools.py:64-350` — tool management endpoints
- `src/lib/apis/streaming/index.ts:1-143` — SSE parsing + `streamLargeDeltasAsRandomChunks`

## Architecture Insights

- **"Additive only" is already enforced by precedent** — code interpreter, artifacts, and export all live as separate concerns wired through shared stores and feature flags. A document feature fits this mold cleanly and should cause zero upstream merge pressure.
- **Two viable invocation shapes:**
  1. **XML tag (like code interpreter)** — simplest, works with any model, no native tool-calling dependency. Add `<document title="…">…</document>` parser in middleware + prompt injection.
  2. **Native tool `write_document`** — cleaner for models with function calling, registers as builtin like `execute_code`. Already has infrastructure (`tools/builtin.py` + `utils/tools.py`).
  - Following the existing pattern, **implement both** gated by `native_function_calling` setting — minimal extra work.
- **The `details` token with custom `type` attribute is the universal side-channel** — used for code_interpreter, reasoning, tool_calls. A new `type="document"` fits naturally and inherits collapsible UI, version grouping, and markdown parsing for free.
- **Sidepanel is not coupled to Artifacts** — `ChatControls.svelte` is a generic right-pane host. Adding `Document.svelte` as a third pane sibling (alongside `Artifacts` and the existing controls) only requires a new store (`showDocument`/`documentContents`) and a mount block. Or reuse Artifacts.svelte and widen its content types to include `{ type: 'markdown', content, title }`.
- **Export for markdown is nearly free:**
  - `.md` — just `new Blob([content], {type:'text/markdown'})` + `file-saver.saveAs()`
  - `.txt` — strip/flatten markdown or emit raw (matches chat `downloadTxt` pattern)
  - `.pdf` — reuse WeasyPrint pipeline: `markdown` → `_md_to_html` → Jinja2 template → `HTML(string=…).write_pdf()`. Need a new `document_export.html` template (or a flag in the existing one to render single-body mode).
  - `.docx` — reuse python-docx + htmldocx pipeline. htmldocx handles the HTML-to-docx conversion; only a slim wrapper is needed.
- **Feature gating should follow the four-layer pattern** (global config / user permission / model capability / per-chat toggle) so admins get the same control granularity they already have for code interpreter.
- **i18n reminder (from `world/preferences.md`)**: every new user-facing string must go into both `en-US/translation.json` and `nl-NL/translation.json`.

## Historical Context (from thoughts/)

- `thoughts/shared/plans/2026-04-11-rich-copy-pdf-word-export.md` — design doc for PR #93 covering the exact export stack to reuse (WeasyPrint + python-docx + htmldocx, Jinja2 template, rich clipboard).

## Related Research

None directly overlapping. Closest prior work is PR #93 ("feat: export function to word/rich copy and pdf", commit `256eb08f5`) which established the export pipeline this feature will extend.

## Open Questions

1. **Invocation shape** — XML tag only, native tool only, or both? Precedent says both; decide based on target models.
2. **Document persistence model** — store as a dedicated DB entity (like artifacts in Claude.ai) or keep inline in the message's details token (like tool_calls)? Inline is simpler and survives chat export; a dedicated entity enables revisioning/sharing but adds schema + migration.
3. **Editability** — read-only sidepanel (like current Artifacts) or editable? Editing would require a store-round-trip back into the message, which no current feature does.
4. **Scope of the "document"** — single markdown body only, or also frontmatter (title, author, date) rendered as a header? Affects the Jinja2 template and tool signature.
5. **Multi-document per response** — if the model emits two `<document>` blocks, version-navigate like Artifacts, or stack them? Artifacts' prev/next pattern seems to apply directly.
6. **Feature flag name** — `ENABLE_DOCUMENT_WRITER` / `feature_document_writer` / `write_document` capability? Should be decided upfront to keep config and UI strings consistent.
