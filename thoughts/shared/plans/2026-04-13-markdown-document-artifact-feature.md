---
date: 2026-04-13
author: Lex Lubbers
status: draft
branch: dev
repository: open-webui
related_research: thoughts/shared/research/2026-04-13-markdown-document-artifact-feature.md
tags: [plan, document-writer, artifacts, tool-calling, export, sidepanel]
---

# Markdown Document Writer — Implementation Plan

## Overview

Add a **Document Writer** feature that lets the model emit a structured markdown document which renders in the right-side Artifacts pane and is downloadable as `md`, `txt`, `pdf`, or `docx`. The feature combines three existing subsystems: **Code Interpreter**-style invocation (native tool + XML-tag fallback), **Artifacts**-style sidepanel rendering (with prev/next versioning), and the **Chat Export** pipeline (WeasyPrint + python-docx) for downloads.

Primary invocation path is a **native builtin tool** `write_document(title, markdown)` — chosen because mid-capability open-source models that are decently function-call-trained are more robust with JSON-schema-validated tool calls than with free-form XML tag syntax. XML-tag fallback (`<document title="…">…</document>`) covers models with native function calling disabled, following the same precedent as `execute_code`.

**Feature flag**: `ENABLE_DOCUMENT_WRITER`, **default off**, wired through Helm + configmap.

## Current State Analysis

- **Code interpreter invocation**: `backend/open_webui/utils/middleware.py:2362-2392` injects an XML-tag prompt; `middleware.py:3240-3430` detects tags in streaming output (see `output_type_map` at line 3279); `backend/open_webui/tools/builtin.py:362-519` defines the native `execute_code` tool; `backend/open_webui/utils/tools.py:478-485` registers it conditionally.
- **Artifacts**: `src/lib/components/chat/Artifacts.svelte` (270 lines) — complete component with prev/next navigation, copy, download, fullscreen. Stores in `src/lib/stores/index.ts:99-110` (`showArtifacts`, `artifactContents`, `artifactCode`). Mounted in `ChatControls.svelte:33, 289-290, 435-436`. Detection in `ContentRenderer.svelte:177-196`.
- **Chat export**: Backend `services/chat_export.py:33-113` (`generate_pdf`, `generate_docx`) already uses WeasyPrint + python-docx + htmldocx and exposes `POST /utils/chat/{pdf,docx}` at `routers/utils.py:120-157`. Frontend clients in `src/lib/apis/utils/index.ts:121-149`. Download handlers in `layout/Sidebar/ChatMenu.svelte:73, 87, 184`.
- **Feature flag infrastructure**: SaaS feature flags live in `backend/open_webui/utils/features.py` (FEATURE_FLAGS dict). Config exposure at `main.py:2405-2454`. Frontend consumes via `isFeatureEnabled('artifacts')` in `src/lib/utils/features.ts`.
- **Helm chart**: `helm/open-webui-tenant/values.yaml:234-236` defines `enableCodeInterpreter: "false"`. Configmap mapping at `helm/open-webui-tenant/templates/open-webui/configmap.yaml:147`.
- **Details-token plumbing**: `src/lib/utils/marked/extension.ts:19-95` parses `<details type="…">` into a token. `MarkdownTokens.svelte:56` has `GROUPABLE_DETAIL_TYPES`. This is the plumbing the XML-fallback path uses.

## Desired End State

After this plan is complete:

1. Admin enables `ENABLE_DOCUMENT_WRITER=true` (via env or Helm).
2. A model with `document_writer` capability can be toggled in the Message Input footer (just like code interpreter).
3. When chatting, the model calls the `write_document(title, markdown)` tool (or emits `<document>` XML if function calling is off).
4. The right-side Artifacts-style pane opens automatically, rendering the markdown as a styled document (title + body).
5. Multiple documents in one conversation are navigable via prev/next arrows.
6. The pane's download menu offers `md`, `txt`, `pdf`, `docx`.
7. All UI strings exist in both `en-US/translation.json` and `nl-NL/translation.json`.
8. The feature can be disabled centrally via Helm (default off).

**Verification**: End-to-end test — enable the flag, ask a model "write me a short markdown document about X", see the sidepanel open and download as each of the 4 formats successfully.

### Key Discoveries

- Three viable invocation paths already exist in this codebase; **native tool + XML tag fallback** is the established pattern (`execute_code` + `<code_interpreter>`). No third path needed.
- `artifactContents` store carries `{ type, content }[]` — extending to `{ type, content, title, version }[]` is cleanest, OR introducing a sibling `documentContents` store. **Decision: separate store** (`documentContents`) — keeps Artifacts unchanged so upstream merges stay clean (ref `world/preferences.md`: "prefer additive changes over modifying upstream code").
- `services/chat_export.py:33-39` `generate_pdf()` accepts `title` + `messages[]`. For a single-document variant we pass a single pseudo-message; easier: add a new `generate_document_pdf(title, markdown)` that uses the same Jinja environment but a new template `document_export.html` (single-body, no role headers, no sources).
- `utils/chat_export.py` already has `_md_to_html()` — reuse directly; no new markdown conversion code needed.
- `details-token type` is the universal side-channel; `type="document"` is naturally reserved space.

## What We're NOT Doing

- **Not** editable documents — v1 is read-only (no store→message round-trip).
- **Not** a dedicated DB entity — documents are persisted inline in the assistant message's `details type="document"` token, same as tool_calls.
- **Not** frontmatter/structured headers — body is free-form markdown, title is a top-level attribute only.
- **Not** document sharing between chats or users.
- **Not** versioning beyond "multiple documents in a single response" (the prev/next UI is per-chat iteration of emitted documents).
- **Not** screenshot/stylized PDF path (we reuse server-side WeasyPrint only).
- **Not** touching upstream `Artifacts.svelte` — we add a sibling `Document.svelte`.
- **Not** writing Cypress E2E tests in this plan (manual verification only for v1).

## Implementation Approach

Six additive phases, each mergeable independently. Backend first (Phases 1+2) so we can smoke-test via curl; frontend after (Phases 3+4). Helm + i18n keep pace with each phase but are verified in Phase 5. Phase 6 is testing.

---

## Phase 1: Backend — invocation plumbing (config, tool, prompt, XML parser)

### Overview

Register a new `write_document` builtin tool, add all four feature-gating layers (global config + user permission + model capability + feature flag), inject the XML-tag prompt for non-native-FC models, and extend the streaming tag parser to emit a `details type="document"` token.

### Changes Required

#### 1.1 Config — feature flags and prompt template

**File**: `backend/open_webui/config.py`
**Changes**: Add after the `CODE_INTERPRETER_*` block (around line 2434):

```python
####################################
# Document Writer
####################################

ENABLE_DOCUMENT_WRITER = PersistentConfig(
    'ENABLE_DOCUMENT_WRITER',
    'document_writer.enable',
    os.environ.get('ENABLE_DOCUMENT_WRITER', 'False').lower() == 'true',
)

DOCUMENT_WRITER_PROMPT_TEMPLATE = PersistentConfig(
    'DOCUMENT_WRITER_PROMPT_TEMPLATE',
    'document_writer.prompt_template',
    os.environ.get('DOCUMENT_WRITER_PROMPT_TEMPLATE', ''),
)

DEFAULT_DOCUMENT_WRITER_PROMPT = """
#### Document Writer

You can produce a structured markdown document that renders in a side panel and can be downloaded by the user as Markdown, Plain Text, PDF, or Word.

- Wrap the document in `<document title="…">…</document>` XML tags. The `title` attribute is required.
- Inside the tags, write the full document body as well-structured **Markdown**: headings (`#`, `##`, `###`), paragraphs, lists, tables, code blocks, quotes, emphasis.
- Write in a document style — proper paragraphs, complete sentences, clear section headings. Not chat-style.
- Do **not** wrap the document in triple backticks — the tags contain raw markdown, not a code block.
- You may write explanatory text before and after the `<document>` block in your reply; the document itself only contains the document body.
- Respond in the chat's primary language. Default to English if multilingual.
"""
```

Add USER_PERMISSIONS around line 1469 (after `USER_PERMISSIONS_FEATURES_CODE_INTERPRETER`):

```python
USER_PERMISSIONS_FEATURES_DOCUMENT_WRITER = (
    os.environ.get('USER_PERMISSIONS_FEATURES_DOCUMENT_WRITER', 'True').lower() == 'true'
)
```

Add to `DEFAULT_USER_PERMISSIONS['features']` around line 1549:

```python
'document_writer': USER_PERMISSIONS_FEATURES_DOCUMENT_WRITER,
```

#### 1.2 Feature-flag registry

**File**: `backend/open_webui/utils/features.py`
**Changes**: Add `FEATURE_DOCUMENT_WRITER` to imports (line 11-32), add `'document_writer'` to the `Feature` literal (line 34-55), and add `'document_writer': FEATURE_DOCUMENT_WRITER` to `FEATURE_FLAGS` (line 57-78). Add `FEATURE_DOCUMENT_WRITER` to `config.py` in the feature flags block (search for `FEATURE_ARTIFACTS` definition and mirror it).

#### 1.3 Native builtin tool

**File**: `backend/open_webui/tools/builtin.py`
**Changes**: Add after `execute_code` (line 519):

```python
# =============================================================================
# DOCUMENT WRITER TOOLS
# =============================================================================


async def write_document(
    title: str,
    markdown: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
) -> str:
    """
    Produce a structured markdown document that the user sees in a side panel
    and can download as Markdown, Plain Text, PDF, or Word. Use this whenever
    the user asks you to write, draft, compose, or produce a document, letter,
    report, memo, or similar long-form piece of writing. Do NOT use it for
    conversational replies or short answers.

    :param title: Short title of the document (used as filename stem and as
        the heading in the rendered panel)
    :param markdown: Full document body as Markdown. Write in document style
        with headings, paragraphs, and appropriate structure.
    :return: JSON with title and status — the markdown itself is surfaced to
        the user via the side panel.
    """
    return json.dumps(
        {
            'status': 'success',
            'title': title,
            'message': 'Document rendered in side panel. User can download as md, txt, pdf, or docx.',
        },
        ensure_ascii=False,
    )
```

> **Note**: The tool returns only a confirmation. The `markdown` argument itself is what the frontend picks up — the native FC path extracts it from the tool-call arguments rendered in `ToolCallDisplay.svelte`, and `ContentRenderer.svelte` detects the `write_document` tool name to populate the sidepanel store.

#### 1.4 Conditional tool registration

**File**: `backend/open_webui/utils/tools.py`
**Changes**: Add after lines 478-485 (after `execute_code` registration):

```python
# Add document writer tool if builtin category enabled AND enabled globally AND model has document_writer capability
if (
    is_builtin_tool_enabled('document_writer')
    and getattr(request.app.state.config, 'ENABLE_DOCUMENT_WRITER', False)
    and get_model_capability('document_writer')
    and features.get('document_writer')
):
    builtin_functions.append(write_document)
```

Add `write_document` to the import block at the top of the file (search for `execute_code` import, add next to it).

Also add `'document_writer'` to the builtin categories list (search for where `is_builtin_tool_enabled('code_interpreter')` is defined/registered so the admin UI can toggle the category).

#### 1.5 Prompt injection + XML tag detection

**File**: `backend/open_webui/utils/middleware.py`
**Changes**: After the `code_interpreter` block (lines 2362-2392), add:

```python
if 'document_writer' in features and features['document_writer']:
    # Skip XML-tag prompt injection when native FC is enabled —
    # write_document will be injected as a builtin tool instead.
    if metadata.get('params', {}).get('function_calling') != 'native' and not AGENT_API_ENABLED:
        prompt = (
            request.app.state.config.DOCUMENT_WRITER_PROMPT_TEMPLATE
            if request.app.state.config.DOCUMENT_WRITER_PROMPT_TEMPLATE != ''
            else DEFAULT_DOCUMENT_WRITER_PROMPT
        )
        form_data['messages'] = add_or_update_user_message(
            prompt,
            form_data['messages'],
        )
```

Extend `output_type_map` at middleware.py:3279 to include:

```python
'document': 'open_webui:document',
```

And ensure the regex and tag extraction in `tag_output_handler()` (middleware.py:3240-3430) recognize `<document …>…</document>` — the existing `extract_attributes` helper at line 3253 already handles `title="…"`. The pattern should be added next to where `<code_interpreter>` is handled.

The tag, once closed, should be serialized as an HTML `<details type="document" title="…" done="true">…markdown body…</details>` in the message content so the marked extension (`src/lib/utils/marked/extension.ts:19-95`) picks it up.

Import `DEFAULT_DOCUMENT_WRITER_PROMPT` at the top of middleware.py alongside `DEFAULT_CODE_INTERPRETER_PROMPT`.

#### 1.6 Expose config to frontend

**File**: `backend/open_webui/main.py`
**Changes**: Add after line 2416 (after `enable_code_interpreter`):

```python
'enable_document_writer': app.state.config.ENABLE_DOCUMENT_WRITER,
```

And in the `feature_*` block near line 2441:

```python
'feature_document_writer': FEATURE_DOCUMENT_WRITER,
```

### Success Criteria

#### Automated Verification

- [x] Backend imports cleanly: `cd backend && python -c "from open_webui.main import app"`
- [x] New tool is registered: `cd backend && python -c "from open_webui.tools.builtin import write_document; print(write_document.__doc__)"`
- [x] Config persists: `cd backend && python -c "from open_webui.config import ENABLE_DOCUMENT_WRITER, DEFAULT_DOCUMENT_WRITER_PROMPT; print(bool(ENABLE_DOCUMENT_WRITER), len(DEFAULT_DOCUMENT_WRITER_PROMPT) > 0)"`
- [x] Backend lint passes: `npm run lint:backend` (only one snake_case warning on `DETECT_DOCUMENT_WRITER`, mirrors existing `DETECT_CODE_INTERPRETER`)
- [x] Backend format passes: `npm run format:backend`

#### Manual Verification

- [ ] With `ENABLE_DOCUMENT_WRITER=true` and a function-calling model, the `write_document` tool appears in the tool list for the model at runtime (curl `/api/models` or inspect logs)
- [ ] With the flag on but native FC off, the document-writer prompt appears in the final prompt sent to the model (log the form_data['messages'])
- [ ] With the flag off, neither tool nor prompt is present

**Implementation Note**: Pause after Phase 1 for manual confirmation before proceeding.

---

## Phase 2: Backend — export endpoints

### Overview

Add `POST /api/v1/utils/document/pdf` and `POST /api/v1/utils/document/docx` reusing the WeasyPrint + python-docx + htmldocx stack from `services/chat_export.py`. Plain text and markdown download happen client-side with `file-saver` — no backend work needed.

### Changes Required

#### 2.1 New service module

**File**: `backend/open_webui/services/document_export.py` (new)
**Changes**:

```python
"""Document export service — single-body Markdown → PDF / DOCX."""

import logging
from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from open_webui.utils.chat_export import _md_to_html

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / 'templates'


def _render_document_html(title: str, markdown: str) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(default_for_string=True, default=True),
    )
    template = env.get_template('document_export.html')
    body_html = _md_to_html(markdown)
    return template.render(title=title, body=body_html)


def generate_document_pdf(title: str, markdown: str) -> bytes:
    from weasyprint import HTML

    html_string = _render_document_html(title, markdown)
    return HTML(string=html_string).write_pdf()


def generate_document_docx(title: str, markdown: str) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor
    from htmldocx import HtmlToDocx

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)

    doc.add_heading(title, level=1)

    body_html = _md_to_html(markdown)
    parser = HtmlToDocx()
    parser.add_html_to_document(f'<div>{body_html}</div>', doc)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.read()
```

#### 2.2 New Jinja template

**File**: `backend/open_webui/templates/document_export.html` (new)
**Changes**: Copy `chat_export.html` styling (NotoSans font, A4 page, heading sizes) but emit a single title + body:

```html
<!DOCTYPE html>
<html>
	<head>
		<meta charset="UTF-8" />
		<title>{{ title }}</title>
		<style>
			@page {
				size: A4;
				margin: 2cm;
			}
			body {
				font-family: 'Noto Sans', sans-serif;
				font-size: 11pt;
				line-height: 1.5;
				color: #222;
			}
			h1 {
				font-size: 22pt;
				margin-bottom: 0.6em;
			}
			h2 {
				font-size: 16pt;
				margin-top: 1.2em;
			}
			h3 {
				font-size: 13pt;
				margin-top: 1em;
			}
			pre {
				background: #f5f5f5;
				padding: 10px;
				border-radius: 4px;
				font-size: 10pt;
				overflow-x: auto;
			}
			code {
				background: #f0f0f0;
				padding: 1px 4px;
				border-radius: 3px;
				font-family: 'Noto Sans Mono', monospace;
			}
			blockquote {
				border-left: 3px solid #ccc;
				padding-left: 10px;
				color: #555;
			}
			table {
				border-collapse: collapse;
				width: 100%;
				margin: 1em 0;
			}
			th,
			td {
				border: 1px solid #ddd;
				padding: 6px 10px;
				text-align: left;
			}
			th {
				background: #f5f5f5;
			}
			img {
				max-width: 100%;
			}
		</style>
	</head>
	<body>
		<h1>{{ title }}</h1>
		{{ body | safe }}
	</body>
</html>
```

#### 2.3 New endpoints

**File**: `backend/open_webui/routers/utils.py`
**Changes**: Add after the `/chat/docx` endpoint (line 157):

```python
class DocumentExportForm(BaseModel):
    title: str
    markdown: str


@router.post('/document/pdf')
async def export_document_as_pdf(
    form_data: DocumentExportForm,
    user=Depends(get_verified_user),
):
    from open_webui.services.document_export import generate_document_pdf

    try:
        pdf_bytes = generate_document_pdf(form_data.title, form_data.markdown)
        return Response(
            content=pdf_bytes,
            media_type='application/pdf',
            headers={'Content-Disposition': _safe_filename(form_data.title, 'pdf')},
        )
    except Exception as e:
        log.exception(f'Error generating document PDF: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/document/docx')
async def export_document_as_docx(
    form_data: DocumentExportForm,
    user=Depends(get_verified_user),
):
    from open_webui.services.document_export import generate_document_docx

    try:
        docx_bytes = generate_document_docx(form_data.title, form_data.markdown)
        return Response(
            content=docx_bytes,
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={'Content-Disposition': _safe_filename(form_data.title, 'docx')},
        )
    except Exception as e:
        log.exception(f'Error generating document DOCX: {e}')
        raise HTTPException(status_code=500, detail=str(e))
```

Add `BaseModel` import if not already present.

### Success Criteria

#### Automated Verification

- [x] Service imports cleanly: `cd backend && python -c "from open_webui.services.document_export import generate_document_pdf, generate_document_docx"`
- [x] PDF generation works with sample input: `cd backend && python -c "from open_webui.services.document_export import generate_document_pdf; b = generate_document_pdf('Test', '# H1\n\nHello **world**.'); assert len(b) > 1000; print('ok', len(b))"`
- [x] DOCX generation works: same pattern with `generate_document_docx`
- [x] Backend lint passes: `npm run lint:backend`

#### Manual Verification

- [ ] `curl -X POST http://localhost:8080/api/v1/utils/document/pdf -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"title":"Test","markdown":"# H1\n\nHello"}' -o out.pdf` produces a valid PDF
- [ ] Same for `/document/docx` → opens correctly in Word / LibreOffice
- [ ] Title renders as H1, body renders with proper formatting
- [ ] Code blocks, lists, tables render in both PDF and DOCX

**Implementation Note**: Pause after Phase 2 for manual confirmation before proceeding.

---

## Phase 3: Frontend — sidepanel component and stores

### Overview

Add `documentContents` store, a new `Document.svelte` sibling to `Artifacts.svelte`, mount it in `ChatControls.svelte`, and extend `ContentRenderer.svelte` + `Chat.svelte` to detect `details type="document"` tokens (and `write_document` tool calls) and populate the store.

### Changes Required

#### 3.1 New stores

**File**: `src/lib/stores/index.ts`
**Changes**: Add after line 110:

```typescript
export const showDocument = writable(false);
export const documentContents = writable<Array<{ title: string; markdown: string }> | null>(null);
```

#### 3.2 Document sidepanel component

**File**: `src/lib/components/chat/Document.svelte` (new)
**Changes**: Mirror structure of `Artifacts.svelte` (lines 1-270). Key differences:

- Renders markdown body via existing `Markdown.svelte` (`src/lib/components/chat/Messages/Markdown.svelte`) instead of `<iframe>` or `SvgPanZoom`
- Displays `title` in header
- Download dropdown with 4 options (md, txt, pdf, docx) instead of a single download button
- Reuses `navigateContent('prev'|'next')`, `copyToClipboard`, close logic verbatim
- Subscribes to `documentContents` instead of `artifactContents`
- Closes via `showDocument.set(false)` + `showControls.set(false)`

Skeleton:

```svelte
<script lang="ts">
	import { onMount, getContext, createEventDispatcher, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { saveAs } from 'file-saver';
	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	import { chatId, showControls, showDocument, documentContents, settings } from '$lib/stores';
	import { copyToClipboard } from '$lib/utils';
	import { exportDocumentAsPdf, exportDocumentAsDocx } from '$lib/apis/utils';

	import Markdown from './Messages/Markdown.svelte';
	import XMark from '../icons/XMark.svelte';
	import Download from '../icons/Download.svelte';
	import ArrowLeft from '../icons/ArrowLeft.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Dropdown from '../common/Dropdown.svelte';
	import { DropdownMenu } from 'bits-ui';

	export let overlay = false;

	let contents: Array<{ title: string; markdown: string }> = [];
	let selectedIdx = 0;
	let copied = false;

	$: current = contents[selectedIdx];

	function navigate(dir: 'prev' | 'next') {
		selectedIdx =
			dir === 'prev'
				? Math.max(selectedIdx - 1, 0)
				: Math.min(selectedIdx + 1, contents.length - 1);
	}

	const downloadMd = () => {
		saveAs(
			new Blob([current.markdown], { type: 'text/markdown' }),
			`${current.title || 'document'}.md`
		);
	};

	const downloadTxt = () => {
		saveAs(
			new Blob([current.markdown], { type: 'text/plain' }),
			`${current.title || 'document'}.txt`
		);
	};

	const downloadPdf = async () => {
		try {
			const blob = await exportDocumentAsPdf(localStorage.token, current.title, current.markdown);
			saveAs(blob, `${current.title || 'document'}.pdf`);
		} catch (e) {
			toast.error($i18n.t('Failed to export PDF'));
		}
	};

	const downloadDocx = async () => {
		try {
			const blob = await exportDocumentAsDocx(localStorage.token, current.title, current.markdown);
			saveAs(blob, `${current.title || 'document'}.docx`);
		} catch (e) {
			toast.error($i18n.t('Failed to export Word document'));
		}
	};

	onMount(() => {
		const unsub = documentContents.subscribe((value) => {
			const next = value ?? [];
			if (next.length === 0) {
				showControls.set(false);
				showDocument.set(false);
				selectedIdx = 0;
			} else if (next.length > contents.length) {
				selectedIdx = next.length - 1;
			}
			contents = next;
		});
		return () => unsub();
	});
</script>

<!-- Layout mirrors Artifacts.svelte — header (prev/next, title, copy, download-menu, close) + body -->
```

Full Markdown rendering: use `<Markdown id={`doc-${selectedIdx}`} tokens={[]} ... />` — consult `Markdown.svelte:1-94` for the exact props.

#### 3.3 Mount in ChatControls

**File**: `src/lib/components/chat/ChatControls.svelte`
**Changes**:

- Add `showDocument` to the store imports (line 16-24)
- Add `import Document from './Document.svelte';` (after line 33)
- Add `showDocument` to `closeHandler` (line 250-257): `showDocument.set(false);`
- Add `|| $showDocument` to `specialPanel` (line 262)
- Add new `{:else if $showDocument}` branch in mobile drawer block (after line 289, next to `$showArtifacts`): `<Document {history} />`
- Add the same branch in desktop pane block (after line 435)

#### 3.4 Detection in ContentRenderer

**File**: `src/lib/components/chat/Messages/ContentRenderer.svelte`
**Changes**: Extend the marked-extension callback. The simplest path: detect a `details type="document"` token in the token tree and call a new helper that pushes `{ title, markdown }` into `documentContents` and opens the pane.

Easiest hook: the `onUpdate` currently at line 175-188 only fires for code blocks. We add a sibling hook on the Markdown component. In `Markdown.svelte` (or a new emitter), when the token parser yields a `details type="document"` node, emit a CustomEvent `on:document={(e) => handleDocument(e.detail)}` that bubbles up.

In `ContentRenderer.svelte`, handle it:

```typescript
const handleDocument = async (payload: { title: string; markdown: string }) => {
	if (!isFeatureEnabled('document_writer')) return;
	if (!($settings?.detectDocuments ?? true)) return;
	if ($mobile || !$chatId) return;

	// append or replace — rely on Chat.svelte:getDocuments() to sweep history
	await tick();
	showDocument.set(true);
	showControls.set(true);
};
```

#### 3.5 History sweep

**File**: `src/lib/components/chat/Chat.svelte`
**Changes**: Mirror `getContents()` at lines 1017-1065 with a new `getDocuments()` function that walks `history` messages, finds `<details type="document" title="…">…</details>` blocks in `message.content` (regex parse — same approach as `src/lib/utils/index.ts:1740-1832` for code blocks), and sets `documentContents`.

Also handle tool-call path: if a message contains a `write_document` tool call (arguments are stringified JSON), extract `{ title, markdown }` from the arguments. The tool-call result-rendering already goes through `serialize_output()` in middleware.py:406-528 which emits a `details type="tool_calls"` with `name="write_document"`; we parse the `arguments` attribute (JSON-escaped) to recover `title`/`markdown`.

Call `getDocuments()` from `onHistoryChange` alongside `getContents()`.

Add metadata injection near line 2198-2200 (mirroring `code_interpreter`):

```typescript
document_writer:
    $config?.features?.enable_document_writer &&
    ($user?.role === 'admin' || $user?.permissions?.features?.document_writer)
        ? documentWriterEnabled
        : false,
```

Add `let documentWriterEnabled = false;` and capability-defaults handling mirroring line 372-378.

#### 3.6 API client

**File**: `src/lib/apis/utils/index.ts`
**Changes**: Add after `exportChatAsDocx` (line 149):

```typescript
export const exportDocumentAsPdf = async (token: string, title: string, markdown: string) => {
	const blob = await fetch(`${WEBUI_API_BASE_URL}/utils/document/pdf`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
		body: JSON.stringify({ title, markdown })
	}).then((res) => {
		if (!res.ok) throw new Error('Document PDF export failed');
		return res.blob();
	});
	return blob;
};

export const exportDocumentAsDocx = async (token: string, title: string, markdown: string) => {
	const blob = await fetch(`${WEBUI_API_BASE_URL}/utils/document/docx`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
		body: JSON.stringify({ title, markdown })
	}).then((res) => {
		if (!res.ok) throw new Error('Document DOCX export failed');
		return res.blob();
	});
	return blob;
};
```

### Success Criteria

#### Automated Verification

- [x] Frontend builds: `npm run build`
- [x] Type-check passes for new files (baseline errors tolerated, no new ones from our code): `npm run check 2>&1 | grep -E "Document\.svelte|document_export|exportDocument" || echo "no new errors"` — Document.svelte has the same pre-existing baseline errors as Artifacts.svelte (i18n unknown, file-saver no types)
- [ ] Lint passes: `npm run lint:frontend` — pre-existing eslint config crash unrelated to Phase 3 (FilePreview.svelte)
- [x] Format passes: `npm run format`

#### Manual Verification

- [ ] Hand-edit a message to include `<details type="document" title="Hello">…markdown…</details>` — the sidepanel opens with correct title and rendered markdown
- [ ] Prev/Next navigation between multiple document blocks works
- [ ] Close button collapses both `showDocument` and `showControls`
- [ ] Mobile drawer works (resize window below `md`)

**Implementation Note**: Pause after Phase 3 for manual confirmation before proceeding.

---

## Phase 4: Frontend — feature gates, toggle button, capability

### Overview

Wire the four feature-gating layers on the frontend: `isFeatureEnabled('document_writer')` SaaS gate, `config.features.enable_document_writer` admin gate, `capabilities.document_writer` model gate, per-chat toggle via MessageInput button.

### Changes Required

#### 4.1 Frontend feature-flag table

**File**: `src/lib/utils/features.ts`
**Changes**: Add `'document_writer'` to the `Feature` union and map it to `config.features.feature_document_writer`.

#### 4.2 Default capabilities

**File**: `src/lib/constants.ts`
**Changes**: Add to `DEFAULT_CAPABILITIES` object (line 100-109):

```typescript
document_writer: true,
```

#### 4.3 MessageInput toggle button

**File**: `src/lib/components/chat/MessageInput.svelte`
**Changes**:

- Add `let documentWriterEnabled = false;` + derived `showDocumentWriterButton` at line 533 (after `showCodeInterpreterButton`):

```typescript
let showDocumentWriterButton = false;
$: showDocumentWriterButton =
	(atSelectedModel?.id ? [atSelectedModel.id] : selectedModels).length ===
		documentWriterCapableModels.length &&
	$config?.features?.enable_document_writer &&
	($_user.role === 'admin' || $_user?.permissions?.features?.document_writer);
```

Add `documentWriterCapableModels` computed analogous to `codeInterpreterCapableModels` (search for its definition).

- Add a new `{:else if itemId === 'document_writer' && showDocumentWriterButton}` branch after the code-interpreter branch (line 1905):

```svelte
{:else if itemId === 'document_writer' && showDocumentWriterButton}
  <Tooltip content={$i18n.t('Document Writer')} placement="top">
    <button
      aria-label={documentWriterEnabled
        ? $i18n.t('Disable Document Writer')
        : $i18n.t('Enable Document Writer')}
      aria-pressed={documentWriterEnabled}
      on:click|preventDefault={() => (documentWriterEnabled = !documentWriterEnabled)}
      type="button"
      class="p-[7px] flex gap-1.5 items-center text-sm rounded-full border transition-colors duration-300 focus:outline-hidden max-w-full overflow-hidden {documentWriterEnabled
        ? 'text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-700/10 border-sky-200/40 dark:border-sky-500/20'
        : 'border-transparent bg-transparent text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'}"
    >
      <!-- icon: use `DocumentText` or existing document icon -->
    </button>
  </Tooltip>
```

Use an existing icon from `$lib/components/icons/` (search for `Document` — there is `DocumentText.svelte` or similar; if not, add a minimal one).

Bind `documentWriterEnabled` from parent via `export let documentWriterEnabled = false;` at top of MessageInput.

#### 4.4 InputMenu pinning

**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`
**Changes**: Add `document_writer` to the pinnable items list (search for `code_interpreter` in the file and mirror).

#### 4.5 Admin settings toggle

**File**: `src/lib/components/admin/Settings/Interface.svelte` (or whichever admin panel controls `ENABLE_CODE_INTERPRETER` — search for it)
**Changes**: Add a toggle for `ENABLE_DOCUMENT_WRITER`. Ensure it's gated by `isFeatureEnabled('document_writer')` so SaaS tiers without the feature don't see it.

#### 4.6 User settings (detectDocuments opt-out)

**File**: `src/lib/components/chat/Settings/Interface.svelte` (or wherever `detectArtifacts` is exposed — grep for `detectArtifacts`)
**Changes**: Add a sibling toggle `detectDocuments` (default `true`) so users can opt-out of auto-opening the panel.

### Success Criteria

#### Automated Verification

- [x] Frontend builds: `npm run build`
- [ ] Lint passes: `npm run lint:frontend` — pre-existing eslint config crash on FilePreview.svelte (unrelated)
- [x] Grep sanity: `grep -r "document_writer" src/lib/ | wc -l` returns > 10 references (53 hits)

#### Manual Verification

- [ ] Admin sees toggle in Settings > Code Execution (with flag on); flipping it is persisted
- [ ] With `ENABLE_DOCUMENT_WRITER=true`, a model that has `document_writer: true` in capabilities, the button appears in MessageInput footer
- [ ] With any of the 4 gates off, the button does not appear
- [ ] Toggling the button sends `features.document_writer: true/false` in the request metadata (verify via network tab)
- [ ] `detectDocuments=false` in user settings prevents auto-opening the panel

**Implementation Note**: Pause after Phase 4 for manual confirmation before proceeding.

---

## Phase 5: Helm chart, i18n, and admin config plumbing

### Overview

Wire `ENABLE_DOCUMENT_WRITER` through the Helm chart (default `false`) and add all user-facing strings to `en-US/translation.json` + `nl-NL/translation.json`.

### Changes Required

#### 5.1 Helm values

**File**: `helm/open-webui-tenant/values.yaml`
**Changes**: Add after `enableCodeInterpreter` (line 234):

```yaml
# Document Writer (disabled)
enableDocumentWriter: 'false'
userPermissionsFeaturesDocumentWriter: 'false'
```

#### 5.2 Helm configmap

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
**Changes**: Add after line 147 (`ENABLE_CODE_INTERPRETER`):

```yaml
ENABLE_DOCUMENT_WRITER: { { .Values.openWebui.config.enableDocumentWriter | quote } }
USER_PERMISSIONS_FEATURES_DOCUMENT_WRITER:
  { { .Values.openWebui.config.userPermissionsFeaturesDocumentWriter | quote } }
```

#### 5.3 i18n — en-US

**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add alphabetically (empty string = use key as-is):

```json
"Disable Document Writer": "",
"Document Writer": "",
"Enable Document Writer": "",
"Failed to export Word document": "",  // likely already exists
"detectDocuments": "",
// any other new strings introduced in Phases 3+4
```

#### 5.4 i18n — nl-NL

**File**: `src/lib/i18n/locales/nl-NL/translation.json`
**Changes**: Add alphabetically:

```json
"Disable Document Writer": "Document Writer uitschakelen",
"Document Writer": "Document Writer",
"Enable Document Writer": "Document Writer inschakelen",
```

(Confirm any existing equivalents — don't duplicate.)

#### 5.5 Parse new strings

Run `npm run i18n:parse` to pick up any missed strings.

### Success Criteria

#### Automated Verification

- [x] Helm lint passes: `helm lint helm/open-webui-tenant`
- [x] Helm template renders: `helm template test helm/open-webui-tenant --set openWebui.config.enableDocumentWriter=true | grep ENABLE_DOCUMENT_WRITER`
- [x] Translation JSON parses: `python -c "import json; json.load(open('src/lib/i18n/locales/en-US/translation.json')); json.load(open('src/lib/i18n/locales/nl-NL/translation.json'))"`
- [x] `npm run i18n:parse` reports no unresolved strings

#### Manual Verification

- [ ] Deploy Helm chart with `enableDocumentWriter: "true"` to a test namespace; pod env contains `ENABLE_DOCUMENT_WRITER=true`
- [ ] Switch UI language to Dutch — all button labels and settings labels show the Dutch strings

---

## Phase 6: End-to-end verification

### Overview

Exercise the full flow with a function-calling-capable open-source model (e.g. a local Qwen or Hermes variant) and a non-FC model to verify both invocation paths.

### Manual Test Plan

1. **Native tool path (function calling model)**
   - Set `ENABLE_DOCUMENT_WRITER=true`, model has `document_writer: true` capability, user permission granted, button toggled on
   - Prompt: "Write me a one-page memo about Q2 results"
   - Expect: model emits a `write_document` tool call; sidepanel opens with rendered memo; title populated; download-menu shows 4 options

2. **XML fallback path (non-FC model or model with native FC off)**
   - Same config, but `function_calling` param set to non-native
   - Prompt: same
   - Expect: model emits `<document title="Q2 Memo">…markdown…</document>`; middleware parses it; sidepanel opens; behavior identical to native path

3. **Downloads — all four formats**
   - Click Download → Markdown → file saved with `.md`
   - Click Download → Plain text → `.txt`
   - Click Download → PDF → `.pdf` opens in Preview/Adobe, renders correctly
   - Click Download → Word → `.docx` opens in Word/LibreOffice, renders correctly

4. **Multi-document navigation**
   - Ask model to "write two documents, one about X and one about Y"
   - Expect: prev/next arrows active, shows "Version 1 of 2" / "2 of 2" like Artifacts

5. **Feature-flag off at each layer**
   - `ENABLE_DOCUMENT_WRITER=false` → button hidden, tool not registered, prompt not injected
   - User permission off → button hidden, tool not registered
   - Model capability false → button hidden
   - Button off (per-chat) → tool not sent in request

6. **i18n**
   - Switch to Dutch → all strings Dutch

7. **Mobile**
   - Shrink to mobile width → drawer opens with same content

### Automated Verification

- [ ] Full frontend build: `npm run build`
- [ ] Frontend lint: `npm run lint:frontend`
- [ ] Backend lint: `npm run lint:backend`
- [ ] Backend format: `npm run format:backend`
- [ ] Frontend unit tests: `npm run test:frontend`
- [ ] Backend unit tests (if we add any): `cd backend && pytest tests/services/test_document_export.py` (new — TBD)

### Backend unit tests (optional, recommended)

**File**: `backend/tests/services/test_document_export.py` (new, if tests dir structure exists for services)
**Changes**: Test `generate_document_pdf` and `generate_document_docx` produce non-empty bytes with sensible minimum size for sample inputs; test malformed markdown doesn't crash.

---

## Testing Strategy

### Unit Tests

- Backend: `generate_document_pdf(title, markdown)` / `generate_document_docx(title, markdown)` produce valid binary output for minimal markdown, heavy markdown (tables, code blocks, lists), and empty body.
- Backend: `write_document` tool returns expected JSON envelope.
- Frontend: None planned for v1 (existing codebase has ~8000 pre-existing type errors per `MEMORY.md`; adding unit tests carries friction).

### Integration Tests

- Covered by the Phase 6 manual matrix.

### Manual Testing Steps

See Phase 6.

## Performance Considerations

- WeasyPrint PDF generation is synchronous and CPU-bound. For large documents (>10k words) this could block the event loop. v1 ships as-is; if complaints emerge, wrap in `run_in_executor`.
- Multiple documents per response: prev/next is already paginated; no DOM blow-up risk since only `contents[selectedIdx]` is rendered.
- No new streaming parser state — we piggyback on the existing `tag_output_handler` infrastructure.

## Migration Notes

No DB migrations — documents are inline in message content as `details type="document"` tokens. Existing chats remain valid; new chats with the feature off will never emit the tag.

## References

- Research: `thoughts/shared/research/2026-04-13-markdown-document-artifact-feature.md`
- Precedent (export pipeline): commit `256eb08f5` (PR #93), plan `thoughts/shared/plans/2026-04-11-rich-copy-pdf-word-export.md`
- Precedent (invocation pattern): `backend/open_webui/tools/builtin.py:362-519`, `backend/open_webui/utils/middleware.py:2362-2392, 3240-3430`
- Precedent (sidepanel): `src/lib/components/chat/Artifacts.svelte` (whole file), `src/lib/components/chat/ChatControls.svelte:33, 289-290, 435-436`
- World preference: `collab/world/preferences.md` — "prefer additive changes over modifying upstream code"
