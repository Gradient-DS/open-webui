---
date: 2026-04-13
author: Lex Lubbers (with Claude Opus 4.6)
git_commit: 95e12cbb14754632ef47926ce21edf605f74583a
branch: feat/doc-writer
repository: open-webui
topic: 'Document feature polish — tab-ify sidebar, Claude-style inline card, citation wiring parity'
tags: [plan, document-writer, chat-controls, tabs, citations, markdown-tokens, inline-card]
status: ready
research: thoughts/shared/research/2026-04-13-document-tabification-inline-card-citations.md
---

# Document feature polish — Implementation Plan

## Overview

Three related UX fixes for the Document Writer feature, shipped as a single PR:

1. Migrate the Document panel from a full-panel override into the existing ChatControls tab system, so reopening the sidebar lands the user back on the Document tab.
2. Replace the generic `<details>` inline rendering (which re-expands the full markdown) with a flat Claude-style card — document icon + shimmer title — that opens the side panel on click. Applies to both the XML `<details type="document">` path and the `write_document` tool-call path.
3. Fix inline source pills not firing click handlers in the Document panel by swapping `<Markdown>` for `<ContentRenderer>`, guaranteeing wiring parity with chat messages.

## Current State Analysis

**Tab system already exists** in `ChatControls.svelte`. `savedTab: 'controls' | 'files' | 'overview'` is a module-level variable (persists across remounts), `activeTab` is bound to it, per-tab visibility flags (`showControlsTab`, `showFilesTab`, `showOverviewTab`) govern which buttons render, and a fallback picker at `:83-88` switches away when the active tab becomes hidden. Document is the anomaly — it is routed around this system as a full-panel override at `ChatControls.svelte:294-295` (mobile) and `:442-443` (desktop): `{:else if $showDocument}<Document />`. Consequence: closing Document with its X button collapses the sidebar; reopening the sidebar returns to Controls, not Document.

**Inline `<details type="document">` has no special handling.** `MarkdownTokens.svelte:423-466` routes it through the generic `Collapsible` path, recursively re-rendering the document markdown inline — duplicating what the side panel already shows. Only `type === 'tool_calls'` gets a dedicated branch (`ToolCallDisplay`). The `write_document` tool-call variant currently renders as a generic `ToolCallDisplay` (Input/Output rows), not as a document card.

**Citation pills fire in chat but not in Document.** User verified: clicking a `[N]` pill inside the Document panel produces zero output from the existing `console.log('[Document] source click', …)` at `Document.svelte:282-289`, while clicking the same-looking pill in a chat message works. Data is equivalent — `sourceIdsFromMessage` in `Chat.svelte:1141-1157` and `getSourceIds` in `ContentRenderer.svelte:77-97` produce matching arrays — so the divergence is in the rendering pipeline, not the data. `ContentRenderer.svelte` wraps `<Markdown>` with a reactive `sources → sourceIds` derivation and passes its own `onSourceClick` through; `Document.svelte` calls `<Markdown>` directly with a pre-computed `sourceIds` array and a separate `onSourceClick`. Using `ContentRenderer` in Document eliminates the parallel code path.

### Key Discoveries:

- Tab system is hand-rolled `<button>`s inside `ChatControls.svelte` — there is no generic `<Tabs>` primitive to reuse (`ChatControls.svelte:302-334, 450-482`).
- `savedTab` is module-private, so external components can't set the active tab; need an exported setter or a small store.
- `closeHandler` (`ChatControls.svelte:252-260`) already resets `showDocument` when `chatId` clears — fine to keep.
- `Controls.svelte` already suppresses its own header when `embed={true}` (`Controls.svelte:35-48`). Document does not have equivalent suppression, and per user preference (Q1a), we keep its toolbar as-is — we just render it inside a tab body instead of the full panel.
- Shimmer animation is already defined globally in `src/app.css:188-229` and consumed by `ToolCallDisplay.svelte:120-122` conditioned on `attributes?.done !== 'true' && !messageDone`.
- `getDocuments()` is called on every `onHistoryChange` (`Chat.svelte:1022-1029`), so `current.sources` / `current.sourceIds` stay fresh as new retrieval arrives.
- `Document` icon already exists at `src/lib/components/icons/Document.svelte` and is imported in `Menu.svelte`.
- The `Document` tab label string `"Document"` already has a nl-NL translation (via `Menu.svelte:348`). Must verify before shipping.

## Desired End State

After this plan is complete:

1. Opening Document (via Menu reopen, via ContentRenderer auto-detect, or by clicking the new inline card) sets the active tab to Document inside the ChatControls sidebar. The sidebar shows a tab bar including a Document tab.
2. Closing the Document view via its X button closes the entire sidebar (`showControls=false`), matching user expectation.
3. Reopening the sidebar (Chat Controls button) lands on the Document tab, because `savedTab='document'` persists across remounts.
4. In the assistant's message, `<details type="document">` and `<details type="tool_calls" name="write_document">` render as a compact flat card with a document icon, the document's title, shimmer-while-streaming, and no duplicate inline markdown. Clicking the card opens the Document side panel (sets `showDocument=true` + `showControls=true`) at the latest version.
5. Inline citation pills inside the Document panel click through to the source modal, matching chat-message behavior.
6. The Document panel's citation footer continues to show the same sources as chat (we are not changing citation data flow — just fixing the wiring).

**Verification**: automated (lint, type-check, unit tests pass) + manual (tab-ify flow, card rendering, pill clicks all produce expected behavior on both mobile and desktop viewports).

## What We're NOT Doing

- **No citation renumbering.** The research doc's original "renumber at extraction time" theory was over-engineered per user feedback. We trust the data.
- **No new store for `selectedDocumentIdx`.** Card click opens at latest version (Q3).
- **Not migrating Artifacts/Embeds into the tab system.** They remain full-panel overrides.
- **No tab-switch-on-close ceremony.** Document's X button still closes the sidebar (Q1a).
- **No redesign of Document's internal toolbar.** Copy, download, version-nav, and X stay in their current positions inside `Document.svelte` (Q1a).
- **No changes to `SourceToken`, `Source`, or `Citations` components.** Those are canonical and work — we fix Document's wiring instead.
- **No renaming of `DocumentCard` to a generic `PanelOpenerCard`.** Artifacts does not need one; scope creep.
- **No dev-mode warning for undefined `sourceIds[id - 1]`.** Out of scope.

## Implementation Approach

Single PR, three phases, each independently testable. Phase 1 is self-contained (tab plumbing). Phase 2 depends on nothing in Phase 1 (it's rendering-only, but the click handler for the card uses the same `showDocument.set(true)` that Phase 1 wires to `activeTab='document'`). Phase 3 is a swap inside `Document.svelte`, independent of the other two.

---

## Phase 1: Tab-ify Document in ChatControls

### Overview

Migrate Document out of the `{:else if $showDocument}<Document />` override and into the tab system. Keep its close semantics (X closes the sidebar). Persist `savedTab='document'` so reopening lands on Document.

### Changes Required:

#### 1. Extend tab union and visibility flags

**File**: `src/lib/components/chat/ChatControls.svelte`

- `:2` — Extend `savedTab` union to `'controls' | 'files' | 'overview' | 'document'`.
- `:76-88` — Add `showDocumentTab` derived flag:
  ```svelte
  $: showDocumentTab = isFeatureEnabled('document_writer') && ($documentContents?.length ?? 0) > 0;
  ```
- Add fallback: `$: if (!showDocumentTab && activeTab === 'document') activeTab = 'controls';` alongside the existing ones at `:83-88`.
- `:91-93` — Extend auto-close condition to include `!showDocumentTab`.

Add `documentContents` to the import list at `:12-25`.

#### 2. Sync `$showDocument` → `activeTab='document'`

**File**: `src/lib/components/chat/ChatControls.svelte`

Add a reactive watcher mirroring the pattern at `:96-107`:

```svelte
$: if ($showDocument) {
    activeTab = 'document';
    showControls.set(true);
}
```

This replaces the `{:else if $showDocument}` override — whenever any upstream (Menu reopen, ContentRenderer auto-detect, new DocumentCard click) sets `$showDocument=true`, the active tab switches and the pane opens.

#### 3. Remove the full-panel Document override

**File**: `src/lib/components/chat/ChatControls.svelte`

- `:294-295` (mobile Drawer if/else chain) — delete the `{:else if $showDocument}<Document />` branch.
- `:442-443` (desktop Pane if/else chain) — delete the same branch.
- `:265` — drop `$showDocument` from the `specialPanel` derivation (so Document no longer suppresses the tab-body container styling).

#### 4. Add Document tab button and body

**File**: `src/lib/components/chat/ChatControls.svelte`

In both tab-bar blocks (mobile `:302-334`, desktop `:450-482`), add after the Controls button:

```svelte
{#if showDocumentTab}
	<button
		class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab === 'document'
			? 'bg-gray-100 dark:bg-gray-800 font-medium text-gray-900 dark:text-white'
			: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
		on:click={() => (activeTab = 'document')}
	>
		{$i18n.t('Document')}
	</button>
{/if}
```

In both body blocks (mobile `:361-376`, desktop `:509-529`), add a branch:

```svelte
{:else if activeTab === 'document'}
    <Document />
```

Placement: the Document branch should come before the Controls fallback (`{:else} <Controls … />`) so an unexpected `activeTab` still falls through to Controls.

Note: we do NOT pass `embed={true}` to Document — its internal toolbar is retained per Q1a. The wrapper `div` around the tab body already provides scrolling. We may need to adjust the `activeTab === 'controls'` conditional padding class at `:357-359` / `:505-507` to also exclude `'document'`, because Document has its own internal padding/scroll (see `Document.svelte:118, 271`):

```svelte
class="flex-1 min-h-0 {activeTab === 'overview'
	? 'h-full'
	: activeTab === 'controls'
		? 'overflow-y-auto px-3 pt-1'
		: activeTab === 'document'
			? 'h-full'
			: ''}"
```

#### 5. Keep Document's X handler closing the sidebar

**File**: `src/lib/components/chat/Document.svelte`

No change required at `:253-263` — it already sets `showControls.set(false)` + `showDocument.set(false)`. That behavior is correct per Q1a.

The `onMount` subscription at `:89-99` (clearing the pane when `documentContents` goes empty) is also fine — but we should add: when we go to zero documents AND `savedTab==='document'`, the fallback picker in ChatControls (step 1) handles the tab switch. No code change in Document.svelte for this.

#### 6. Remove now-redundant mutual-exclusion store resets

**File**: `src/lib/components/layout/Navbar/Menu.svelte`

At `:334-350`, the Document reopen button sets `showDocument=true` + zeroes out artifacts/overview/embeds. After Phase 1, `$showDocument=true` auto-selects the Document tab, so the mutual-exclusion resets become belt-and-suspenders but are still correct. **No change required.**

Similarly at `:316-332` (Artifacts reopen button), `showDocument.set(false)` still works — setting `$showDocument=false` simply means the reactive watcher in step 2 won't fire; activeTab remains on its last value (likely already switched to Artifacts via `showArtifacts.set(true)` which is still routed through the old override path). This preserves existing Artifacts behavior untouched.

### Success Criteria:

#### Automated Verification:

- [x] Type checking passes: `npm run check` (no new errors introduced; repo baseline has many pre-existing errors)
- [x] Frontend lint passes: `npm run lint:frontend` (repo baseline pre-existing)
- [x] Frontend build succeeds: `npm run build`
- [x] Prettier formatting clean: `npm run format`

#### Manual Verification:

- [ ] Open a chat with a document response → Document tab appears in the sidebar tab bar, active by default when the document arrives (via ContentRenderer auto-detect).
- [ ] Click the X button on Document → sidebar closes entirely.
- [ ] Click Chat Controls button to reopen → sidebar reopens with Document tab active (because `savedTab='document'` persisted).
- [ ] Open a chat with no documents → Document tab does NOT appear; active tab falls back to Controls.
- [ ] Switch to Files or Overview tab, then click the "Document" entry in the Navbar menu → Document tab becomes active again.
- [ ] On mobile viewport (Drawer path): same flow works — Document tab appears, X closes the drawer.
- [ ] When a document's content clears (new chat), the Document tab disappears and activeTab gracefully falls back to Controls.
- [ ] Artifacts and Embeds continue to render as full-panel overrides (unchanged), no regression.

**Implementation Note**: After completing Phase 1 and verifying automated checks, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: DocumentCard replacing inline dropdown

### Overview

Create a compact, flat clickable card for `<details type="document">` and `<details type="tool_calls" name="write_document">`. Modeled on `ToolCallDisplay`'s header row, but without a chevron or expandable body. Clicking opens the Document side panel at the latest version.

### Changes Required:

#### 1. New DocumentCard component

**File**: `src/lib/components/chat/Messages/Markdown/DocumentCard.svelte` (new file)

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { showControls, showDocument } from '$lib/stores';
	import Document from '$lib/components/icons/Document.svelte';

	export let id: string = '';
	export let title: string = '';
	export let done: boolean = true;
	export let messageDone: boolean = true;
	export let className: string = '';

	$: isExecuting = !done || !messageDone;
	$: displayTitle = title || $i18n.t('Document');

	const openDocument = () => {
		showDocument.set(true);
		showControls.set(true);
	};
</script>

<button
	type="button"
	{id}
	class="{className} cursor-pointer text-left w-full text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
	on:click={openDocument}
	aria-label={$i18n.t('Open document: {{title}}', { title: displayTitle })}
>
	<div
		class="w-full max-w-full font-medium flex items-center gap-1.5 {isExecuting ? 'shimmer' : ''}"
	>
		<div class="text-gray-400 dark:text-gray-500 shrink-0">
			<Document className="size-4" strokeWidth="1.5" />
		</div>
		<div class="flex-1 line-clamp-1 font-normal text-black dark:text-white">
			{displayTitle}
		</div>
	</div>
</button>
```

Notes:

- Uses the existing `Document` icon at `src/lib/components/icons/Document.svelte` (already used in `Menu.svelte`).
- Shimmer class conditioned on `!done || !messageDone`, mirroring `ToolCallDisplay.svelte:120-122` semantics (`attributes.done !== 'true' && !messageDone`).
- No chevron, no expandable body — flat row.
- Native `<button>` for accessibility (Tab-focusable, Enter/Space activation for free).

#### 2. Route `<details type="document">` to DocumentCard

**File**: `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte`

**At `:423-466` (standalone `details` token):**

Add a new branch BEFORE the existing `type === 'tool_calls'` branch at `:426`:

```svelte
{#if token?.attributes?.type === 'document'}
    <DocumentCard
        id={`${id}-${tokenIdx}-doc`}
        title={token?.attributes?.title ?? token.summary ?? ''}
        done={token?.attributes?.done !== 'false'}
        messageDone={done}
        className="w-full"
    />
{:else if token?.attributes?.type === 'tool_calls' && token?.attributes?.name === 'write_document'}
    <DocumentCard
        id={`${id}-${tokenIdx}-wdoc`}
        title={extractWriteDocumentTitle(token?.attributes?.arguments)}
        done={token?.attributes?.done === 'true'}
        messageDone={done}
        className="w-full"
    />
{:else if token?.attributes?.type === 'tool_calls'}
    <!-- existing ToolCallDisplay branch -->
    …
```

**At `:369-422` (ConsecutiveDetailsGroup):** mirror the same two new branches before the existing `type === 'tool_calls'` check at `:379`.

#### 3. Helper to extract title from `write_document` arguments

**File**: `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte`

Add a small helper in the `<script>` block (near the existing `getDetailTextContent` helper):

```ts
import { decode } from 'html-entities';

const extractWriteDocumentTitle = (argsStr: string | undefined): string => {
	if (!argsStr) return '';
	try {
		const parsed = JSON.parse(decode(argsStr));
		return typeof parsed?.title === 'string' ? parsed.title : '';
	} catch {
		return '';
	}
};
```

This mirrors the title extraction in `Chat.svelte:1125-1128`. Keep the logic here minimal — tool-call arguments may still be streaming (partial JSON); a parse failure yields an empty title, which DocumentCard falls back to the translated "Document" label.

#### 4. Add import and register component

**File**: `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte`

Add near the other component imports:

```ts
import DocumentCard from './DocumentCard.svelte';
```

#### 5. Stop extracting `<details type="document">` into the visible token stream

No change. We rely on the existing `documentContents` extraction in `Chat.svelte:1099-1176` and the side panel for the expanded view. The inline `<details>` still parses as a `details` token in the markdown stream; we just render it as a card.

### Success Criteria:

#### Automated Verification:

- [ ] Type checking passes: `npm run check`
- [ ] Frontend lint passes: `npm run lint:frontend`
- [ ] Frontend build succeeds: `npm run build`
- [ ] Prettier formatting clean: `npm run format`

#### Manual Verification:

- [ ] Assistant produces a `<details type="document" title="…">` — inline rendering shows a flat card with the Document icon and the title, no expandable body.
- [ ] While the document is streaming (during `write_document` tool call), the card title shimmers.
- [ ] Once streaming completes, shimmer stops.
- [ ] Clicking the card opens the side panel with the Document tab active (if Phase 1 is in).
- [ ] Assistant produces a `<details type="tool_calls" name="write_document">` (native tool-call path) — renders as the DocumentCard, not the generic ToolCallDisplay Input/Output view.
- [ ] Non-document tool calls (`type="tool_calls"` with other `name` values) still render as `ToolCallDisplay` unchanged.
- [ ] Non-document details (`<details>` without `type` or with other types) still render as `Collapsible` unchanged.
- [ ] The card inside `ConsecutiveDetailsGroup` (when adjacent to other `<details>` elements) renders correctly.
- [ ] Inline card behaves correctly in dark mode (text visible, shimmer animates).

**Implementation Note**: After completing Phase 2 and verifying automated checks, pause here for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Citation wiring parity — swap Markdown for ContentRenderer

### Overview

In `Document.svelte`, replace the direct `<Markdown>` call with `<ContentRenderer>`, passing `sources` instead of the pre-computed `sourceIds`. This eliminates the parallel rendering path that's silently diverging and guarantees pills behave identically to chat messages.

### Observation (2026-04-13) — mobile works, desktop doesn't

User verified that **on mobile**, citation pills open the source modal and the Citations footer expands/collapses on click. **On desktop**, neither works.

Likely culprit: the `overlay` drag-intercept div.

- Desktop renders `<Document overlay={dragged} />` (ChatControls.svelte desktop Pane branch).
- Mobile renders `<Document />` without the overlay prop.
- `Document.svelte:267-268` renders `<div class="absolute top-0 left-0 right-0 bottom-0 z-10"></div>` whenever `overlay` is truthy.
- `dragged` is toggled by `document.addEventListener('mousedown' / 'mouseup')` in `ChatControls.svelte:182-187, 236-237`. A mousedown anywhere in the document flips `dragged=true`, Svelte renders the overlay on the next tick covering the pill, and mouseup then fires on the overlay instead of the pill — so no click event reaches the pill or the Citations accordion.

This is orthogonal to the Markdown → ContentRenderer swap (ContentRenderer would have the same issue because the overlay is inside `Document.svelte`, above the content), but Phase 3 should fix it at the same time since it's the same user-visible bug (citations broken in Document on desktop). Options:

1. **Scope `dragged` to the PaneResizer only**, not the whole document. Attach the mousedown/mouseup listeners to the resizer element, not `document`. Cleanest fix.
2. **Drop the overlay entirely** — it was added to prevent click-through during resize drag, but `dragged` is set on ANY mousedown so it's broken as-is.
3. **Short-circuit**: only set `dragged=true` if the mousedown target is the PaneResizer.

Recommend option 1 or 3 as part of Phase 3. Out-of-scope alternative: keep overlay but at `z-0` so it doesn't intercept — but that defeats its purpose.

### Changes Required:

#### 1. Swap Markdown → ContentRenderer in Document.svelte

**File**: `src/lib/components/chat/Document.svelte`

At `:13` — replace:

```svelte
import Markdown from './Messages/Markdown.svelte';
```

with:

```svelte
import ContentRenderer from './Messages/ContentRenderer.svelte';
```

At `:275-292` — replace the Markdown block with:

```svelte
<ContentRenderer
	id={`document-${$chatId ?? 'preview'}-${selectedContentIdx}`}
	content={current.markdown}
	done={true}
	editCodeBlock={false}
	sources={current.sources ?? []}
	floatingButtons={false}
	onSourceClick={(id) => citationsElement?.showSourceModal(id)}
/>
```

Notes:

- `ContentRenderer` derives `sourceIds` from `sources` via its `$: getSourceIds(sources)` at `:48`. Same derivation logic as `sourceIdsFromMessage` in Chat.svelte.
- `floatingButtons={false}` disables the selection-based "ask question about this" UI which is not wanted in the Document panel.
- `ContentRenderer`'s internal document-detection at `:51-75` checks for `<details type="document">` in the content. Since `current.markdown` is the already-extracted inner content (the `<details>` wrapper has been stripped by `extractDocumentsFromMessage` at `Chat.svelte:1111`), the detection will not fire. Safe.
- `ContentRenderer`'s artifact detection via `onUpdate` at `:203-217` checks html/svg code blocks in the markdown — if a document legitimately contains an HTML code block, this could auto-open artifacts. **Action**: accept this behavior for now (documents rarely contain html/svg blocks; if they do, opening Artifacts is arguably correct). If it becomes a real problem, follow-up by adding a `detectArtifacts={false}` prop to ContentRenderer — out of scope for this plan.

#### 2. Remove the console.log diagnostic

**File**: `src/lib/components/chat/Document.svelte`

The inline `onSourceClick` at `:282-289` had a diagnostic `console.log('[Document] source click', …)`. Replace with a clean arrow function (as shown in step 1 above). The new `onSourceClick={(id) => citationsElement?.showSourceModal(id)}` is the final form.

#### 3. Drop `sourceIds` from `documentContents`

**File**: `src/lib/components/chat/Chat.svelte`

- `:1141-1157` — delete the `sourceIdsFromMessage` helper. It was only consumed by `getDocuments` for the Document panel; now ContentRenderer derives sourceIds internally.
- `:1166-1170` — drop the `sourceIds` field from documents built in `getDocuments`:
  ```ts
  docs = [...docs, ...found.map((doc) => ({ ...doc, sources }))];
  ```

**File**: `src/lib/components/chat/Document.svelte`

- `:22-27` — drop `sourceIds` from the `contents` type annotation:
  ```ts
  let contents: Array<{
  	title: string;
  	markdown: string;
  	sources?: any[];
  }> = [];
  ```

No other consumers reference `documentContents[].sourceIds` — verified by searching the codebase.

#### 4. Citations binding unchanged

`Document.svelte:294-302` continues to render `<Citations bind:this={citationsElement} sources={current.sources} />` — no change. The bind ensures `citationsElement?.showSourceModal(id)` from Phase 3 step 1 resolves.

### Success Criteria:

#### Automated Verification:

- [x] Type checking passes: `npm run check` (no new errors introduced)
- [x] Frontend lint passes: `npm run lint:frontend`
- [x] Frontend build succeeds: `npm run build`
- [x] Prettier formatting clean: `npm run format`
- [x] No remaining references to `sourceIdsFromMessage`
- [x] No remaining references to `.sourceIds` on document entries

#### Manual Verification:

- [ ] Open a chat where the assistant used RAG and produced a document with `[N]` citations.
- [ ] Open the Document panel — inline `[N]` pills render as styled chips (not plain text).
- [ ] Click an inline pill → source modal opens with the source details (matches behavior in the chat message above it).
- [ ] The Citations footer at the bottom of the panel lists the same sources as the chat message's citations.
- [ ] Compare against the same chat: clicking the pill in the chat message and the matching pill in the Document both open the same modal.
- [ ] When `message.sources` is empty (no retrieval), the Document panel hides the Citations footer gracefully (existing behavior at `Document.svelte:294-303`).
- [ ] Selection-based floating buttons do NOT appear inside the Document panel (because `floatingButtons={false}`).
- [ ] No regressions on chat-message citation behavior (unchanged code path).

**Implementation Note**: After completing Phase 3 and verifying automated checks, pause here for manual confirmation before finalizing the PR.

---

## i18n

All user-visible strings must have en-US and nl-NL entries in `src/lib/i18n/locales/*/translation.json`.

Keys introduced/touched:

- `"Document"` — already exists (used by `Menu.svelte:348`). Reuse for tab button label and DocumentCard fallback. **Verify** both `en-US/translation.json` and `nl-NL/translation.json` contain it.
- `"Open document: {{title}}"` — new, used for the DocumentCard aria-label.

Add to both locale files (keys alphabetically sorted):

- en-US: `"Open document: {{title}}": ""` (empty string = use the key itself)
- nl-NL: `"Open document: {{title}}": "Document openen: {{title}}"`

If `"Document"` is missing from either locale, add:

- en-US: `"Document": ""`
- nl-NL: `"Document": "Document"` (Dutch uses the same word)

Verify with `npm run i18n:parse` before committing.

---

## Testing Strategy

### Unit Tests

The existing codebase does not have unit tests for `ChatControls.svelte`, `Document.svelte`, or `MarkdownTokens.svelte`. We do not introduce new test infrastructure in this plan — existing test patterns are E2E (Cypress) and no relevant suite exists for the Document feature. Manual verification via the checklists above is the primary verification.

### Manual Testing Steps

1. **Tab-ification flow (Phase 1):**
   - Start a fresh chat on desktop (>=1024px).
   - Prompt for a document (e.g., "Write a document about X").
   - Wait for the Document side panel to auto-open — confirm it appears as the "Document" tab (not a full-panel override).
   - Click the tab bar's "Controls" tab, verify switch works.
   - Click "Document" tab again — Document renders.
   - Click X in Document's toolbar — sidebar closes.
   - Click the Chat Controls button in the navbar — sidebar reopens on Document tab.
   - Start a new chat — Document tab should disappear (no documents yet); active tab falls back to Controls.
   - Repeat on mobile viewport (<1024px) — same flow via Drawer.

2. **Inline card flow (Phase 2):**
   - In the same chat, scroll to the assistant message. Where the full document markdown used to be expanded inline, verify a single compact card with the Document icon + title appears.
   - Prompt for another document — while streaming, verify the card title shimmers.
   - Click the card — Document tab activates in the sidebar (Phase 1 wiring).
   - Test both the XML path (`<details type="document">`) and the tool-call path (`<details type="tool_calls" name="write_document">`) if the backend emits both.
   - Verify other tool calls (non-write_document) still render as ToolCallDisplay unchanged.

3. **Citation pill flow (Phase 3):**
   - Set up a chat that uses RAG retrieval (e.g., ask about a knowledge-base document) and generates a written document with citations.
   - In the chat message, confirm clicking a `[N]` pill opens the source modal.
   - In the Document panel, click the same-numbered pill — source modal should open identically.
   - Click multiple pills at different indices — all resolve to correct sources.
   - Expand the Citations footer chip at the bottom — verify sources list matches chat.
   - Verify selection in the Document does NOT show floating buttons (disabled).

## Performance Considerations

- `ContentRenderer` is a slightly heavier component than `Markdown` (adds FloatingButtons infra and mouseup listeners). With `floatingButtons={false}`, the listener setup in `ContentRenderer.svelte:172-186` is still attached; this is a trivial DOM-listener cost. No concerns for Document's document-count scale (handful of versions per chat).
- The reactive `showDocument` → `activeTab='document'` watcher runs whenever `$showDocument` changes. It's a trivial assignment; no perf concerns.
- DocumentCard replaces a recursive Markdown re-render with a single button row. **Significant perf improvement** for long documents, because the inline `<details>` currently re-parses and re-renders the full document markdown on every stream tick.

## Migration Notes

No database or schema changes. No backwards-compatibility hacks — this is a frontend-only refactor of a recently shipped feature on `feat/doc-writer`. All changes are additive or replace-in-place.

If the branch has downstream work in progress (or is behind `main`), rebase before starting to avoid conflicts in `ChatControls.svelte` and `MarkdownTokens.svelte`, which are both high-churn areas in upstream Open WebUI.

## References

- Research: `thoughts/shared/research/2026-04-13-document-tabification-inline-card-citations.md`
- Prior research: `thoughts/shared/research/2026-04-13-document-sidepanel-reopen-and-citations-footer.md`
- Prior research: `thoughts/shared/research/2026-04-13-markdown-document-artifact-feature.md`
- Original plan: `thoughts/shared/plans/2026-04-13-markdown-document-artifact-feature.md`
- Reference pattern (ToolCallDisplay): `src/lib/components/common/ToolCallDisplay.svelte:110-172`
- Shimmer keyframes: `src/app.css:188-229`
- Tab system: `src/lib/components/chat/ChatControls.svelte:2, 68-107`
- Document panel: `src/lib/components/chat/Document.svelte`
- Document extraction: `src/lib/components/chat/Chat.svelte:1099-1176`
- Citation rendering: `src/lib/components/chat/Messages/Markdown/Source.svelte:41-53`, `SourceToken.svelte:42-46`
- Menu reopen buttons: `src/lib/components/layout/Navbar/Menu.svelte:316-350`
