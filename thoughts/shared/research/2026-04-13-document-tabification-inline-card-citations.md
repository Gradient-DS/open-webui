---
date: 2026-04-13T21:04:54+0200
researcher: Lex Lubbers (with Claude Opus 4.6)
git_commit: 95e12cbb14754632ef47926ce21edf605f74583a
branch: feat/doc-writer
repository: open-webui
topic: 'Document feature polish: tab-ify sidebar, Claude-style inline card, clickable citations'
tags:
  [research, document-writer, chat-controls, tabs, citations, markdown-tokens, inline-card, shimmer]
status: complete
last_updated: 2026-04-13
last_updated_by: Lex Lubbers
last_updated_note: 'Revised Issue 1 (X closes sidebar, persist tab selection) and Issue 3 (wiring parity, not data renumbering) after user feedback'
---

# Research: Document feature polish — tab-ify sidebar, inline card, clickable citations

**Date**: 2026-04-13T21:04:54+0200
**Researcher**: Lex Lubbers (with Claude Opus 4.6)
**Git Commit**: 95e12cbb14754632ef47926ce21edf605f74583a
**Branch**: feat/doc-writer
**Repository**: open-webui

## Research Question

After landing the Document Writer feature, three UX issues need to be addressed:

1. **Tab-ify the Document in the Chat Controls sidebar.** Currently, the Document panel is an **override** of the Chat Controls sidebar — when you open Document and then close it with X, the sidebar closes. Clicking Chat Controls again shows Controls (not Document). We want Document to be a _tab_ inside the sidebar alongside Controls (the user suggested "add a tab for document / content").
2. **Replace the inline document dropdown with a Claude-style card.** Currently the assistant's `<details type="document">` block renders as a generic expandable `<details>` that shows the full document markdown again — duplicating what's in the side panel. We want a compact card (document icon + title, with pulsating text while streaming) that clicks through to open the side panel.
3. **Make inline source pills clickable and ensure the Citations footer contains every referenced source.** In the document panel, (a) the `[N]` citation pills in the body are _not_ clickable (do not open the source preview modal), and (b) the Citations footer at the bottom does not include all the sources that the inline pills reference.

## Summary

### Issue 1 — Tab architecture already exists; Document just needs to be migrated into it

`ChatControls.svelte` (`src/lib/components/chat/ChatControls.svelte`) **already has a working tab system** for Controls / Files / Overview, with per-tab visibility flags, a module-level `savedTab` that persists across remounts, and auto-switch logic. Document is routed _around_ this system as a full-panel override via an `{:else if $showDocument}<Document />` branch (`ChatControls.svelte:290-295` mobile, `:438-443` desktop). The fix is to **move Document into the tab system** — add a Document tab button, a visibility flag, route `showDocument` to set `activeTab='document'` instead of hijacking the whole panel, and change the Document X-button to switch away from the tab rather than close the panel.

### Issue 2 — A Claude-style card needs a new `type="document"` branch in `MarkdownTokens.svelte`

`<details type="document">` currently has **no special handling** — it falls through the generic `Collapsible` default (`MarkdownTokens.svelte:434-455`), which recursively re-renders the document markdown. The right pattern to copy is `ToolCallDisplay.svelte:110-172` (cursor-pointer row, leading status icon, flex-1 label with `shimmer` class when `attributes.done !== 'true' && !messageDone`). The shimmer animation is already defined globally in `src/app.css:188-229`. We add a new `attributes?.type === 'document'` branch in `MarkdownTokens.svelte` (both standalone at `:426` and consecutive-group at `:379`) that renders a `DocumentCard` component — no recursive content rendering — whose click handler sets `showDocument=true` + `showControls=true` and (ideally) selects the specific document index.

### Issue 3 — Two independent bugs, one hiding the other

**(a) Pills not clickable:** `Source.svelte:41` gates the pill button on `{#if title !== 'N/A'}`. When `sourceIds[id - 1]` is `undefined`, `title` is `undefined` and **no button is rendered at all** — there is nothing to click. This happens when a document's `[N]` numbering references sources whose count exceeds `current.sourceIds.length`. **(b) Footer missing sources:** `Chat.svelte:1166-1170` attaches `message.sources` verbatim to every document extracted from that message, with **no deduplication and no reconciliation** with numbering. If the model numbered citations globally across the conversation (but `message.sources` only contains this turn's retrieval), higher numbers won't resolve. Both bugs stem from the assumption that document numbering aligns with _this_ message's sources — which is not guaranteed by any upstream mechanism.

## Detailed Findings

### Issue 1 — Chat Controls sidebar: tab infrastructure exists, Document lives outside it

#### Container: `ChatControls.svelte`

This is a PaneForge-based right panel mounted in `Chat.svelte:3300-3321`. It has **two parallel renderings** — a mobile `Drawer` and a desktop `Pane` — with the same if/else chain for special-panel overrides:

```
{#if $showCallOverlay}
    <CallOverlay />
{:else if $showEmbeds}
    <Embeds />
{:else if $showArtifacts}
    <Artifacts />
{:else if $showDocument}
    <Document />          ← the override that breaks tab behavior
{:else}
    <tab bar>             ← Controls / Files / Overview
    <tab body>
{/if}
```

Locations:

- Mobile branch: `src/lib/components/chat/ChatControls.svelte:276-379`
- Desktop branch: `src/lib/components/chat/ChatControls.svelte:426-531`
- Tab bar (desktop): `ChatControls.svelte:446-500`
- Tab body (desktop): `ChatControls.svelte:502-530`

#### The existing tab system we can extend

- `ChatControls.svelte:2` — module-level `savedTab: 'controls' | 'files' | 'overview'` (persists across remounts).
- `ChatControls.svelte:68-72` — `activeTab = savedTab` with reactive sync.
- `ChatControls.svelte:76-88` — per-tab visibility flags `showControlsTab`, `showFilesTab`, `showOverviewTab`; fallback picker when the current tab becomes hidden.
- `ChatControls.svelte:91-93` — auto-close pane if no tabs are visible.
- `ChatControls.svelte:96-107` — auto-switch to Files when `$showFileNavPath` or `$selectedTerminalId` fires (this is the pattern we want to mirror for Document).
- Tab buttons and body dispatch at `:302-334, 361-376` (mobile) and `:450-482, 509-529` (desktop).

**No generic `<Tabs>` primitive exists** in `$lib/components/common/`. The tab UI is hand-rolled `<button>`s inside ChatControls itself, so adding one more tab means copy-pasting the existing Controls-tab button pattern.

#### Why closing Document currently closes the whole panel

- `Document.svelte:253-263` — the X button sets both `showControls=false` AND `showDocument=false`, collapsing the entire sidebar.
- `Document.svelte:89-99` — when `documentContents` goes empty, same dual reset.
- `Chat.svelte:745-764` — the `showControls` subscription opens/collapses the pane; on close it also resets `showArtifacts` and `showEmbeds` — but NOT `showDocument` (intentional after the `feat/doc-writer` branch for the reopen-menu work).

#### Cross-panel interaction

- `layout/Navbar/Menu.svelte:321-327` (Artifacts) and `:339-345` (Document) enforce mutual exclusion by setting the other's store to `false`.
- `ContentRenderer.svelte:67-68` — streaming auto-open sets `showDocument=true` + `showControls=true`.
- `ChatControls.svelte:265` — `specialPanel = $showCallOverlay || $showArtifacts || $showDocument || $showEmbeds` is used for layout logic and would need to stop including `$showDocument` once Document lives inside the tab system.
- `ChatControls.svelte:252-262` — `closeHandler()` (triggered when chatId clears) resets all overlay stores **including `showDocument`**; this is fine to keep.
- `Chat.svelte:1286-1290` — new-chat flow resets `showDocument` and `documentContents`.

#### What needs to change (high level, no code)

1. Add `'document'` to `savedTab`'s union in `ChatControls.svelte:2`.
2. Add a `showDocumentTab` flag derived from `$documentContents?.length > 0 && isFeatureEnabled('document_writer')`, plus fallback behavior in `:76-88`.
3. Add a Document tab button in both tab-bar blocks and a body branch rendering `<Document embed={true} />` (mirroring how Controls is mounted with `embed={true}` to suppress its standalone header).
4. Remove the `{:else if $showDocument}<Document />` override at `:294-295` and `:442-443`.
5. Change the semantics of `showDocument`: instead of "replace panel", it means "auto-select Document tab". The watcher in `ContentRenderer.svelte:65-68` and the Menu button in `Menu.svelte:334-350` set `activeTab='document'` via a new exported method or a small store (currently `savedTab` is module-private).
6. Update `Document.svelte`'s X handler (`:253-259`, `:96-97`) to switch away from the Document tab rather than close the pane — leave `showControls` alone, fall back to the first visible sibling tab.
7. Update `Controls.svelte:34-47` — it already suppresses its header when `embed=true`; no change needed, but confirm Document has equivalent header-suppression semantics since it currently renders its own header/toolbar.

#### Key references

- `src/lib/components/chat/ChatControls.svelte` (entire file, especially `:2, 68-107, 265-295, 361-376, 438-530`)
- `src/lib/components/chat/Chat.svelte:124, 745-764, 1286-1290, 3300-3321`
- `src/lib/components/chat/Controls/Controls.svelte:34-47`
- `src/lib/components/chat/Document.svelte:89-99, 253-263`
- `src/lib/components/chat/Messages/ContentRenderer.svelte:50-75`
- `src/lib/components/layout/Navbar/Menu.svelte:321-350`
- `src/lib/stores/index.ts:112-113`

### Issue 2 — Inline dropdown should become a Claude-style card

#### Current rendering of `<details type="document">`

The markdown parser emits a `details` token, and `MarkdownTokens.svelte` routes it through the generic `Collapsible` path (no `type="document"` branch exists):

- `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte:423-466` — the details handler.
  - `:426-433` — only `type === 'tool_calls'` gets a dedicated `ToolCallDisplay` branch.
  - `:434-455` — the **default path** used for `type="document"`: a `Collapsible` whose `<slot name="content">` recursively calls `<svelte:self>` on `marked.lexer(decode(token.text))`, re-rendering the full document inline.
- `:370-418` — same structure inside `ConsecutiveDetailsGroup`; both need the new branch.

Since `type="document"` is indistinguishable from a generic `<details>` to the renderer, you see the full markdown expanded (with optional click-to-collapse). That's the "double content" the user is seeing.

#### The right reference pattern: `ToolCallDisplay`

- `src/lib/components/common/ToolCallDisplay.svelte:110-172`
  - `:113-171` — cursor-pointer header row, leading status icon (Spinner while executing, CheckCircle when done, WrenchSolid default), flex-1 label with shimmer-wrapped `Markdown` title (`:140-161`), trailing Chevron.
  - `:120-122` — `shimmer` class applied conditionally.
- `src/lib/components/common/Collapsible.svelte:76-81` — same shimmer-on-header pattern, keyed on `attributes.done !== 'true' && !messageDone`.

A minimal `DocumentCard.svelte` would mirror the `ToolCallDisplay` header row but:

- Replace `WrenchSolid` with a document icon (e.g., `$lib/components/icons/Document.svelte`).
- Drop the chevron + expandable body; the card is a flat clickable row, not a collapsible.
- Click handler: `showDocument.set(true)` + `showControls.set(true)` (soon: set active tab, see Issue 1).
- Title: prefer `token?.attributes?.title` (the extractor's regex capture; see `Chat.svelte:1105` for how titles are populated in `documentContents`), fall back to `token.summary`.

#### Pulsating/shimmer effect — already wired

- `src/app.css:188-229` — `@keyframes shimmer` plus `.shimmer` (light) and `:global(.dark) .shimmer` (dark).
- Animation timing: 1.5s, `cubic-bezier(0.7, 0, 1, 0.4) infinite`, gradient-on-text.
- Condition for in-progress: `attributes?.done !== 'true' && !messageDone`. The `messageDone` prop is already threaded through `MarkdownTokens` (see `:373, 392, 415, 439, 462`), so the new branch inherits it for free.

#### Mapping click → open-this-specific-document

The side panel today uses `selectedContentIdx = 0` on reset and advances as documents arrive (`Document.svelte:89-107`). Extraction order in `Chat.svelte:1099-1139` walks the message content once with two regexes (XML `<details type="document">` at `:1104-1115`, then `write_document` tool-call at `:1118-1136`). For a card to open a specific version, it needs an index — derivable by counting preceding `type="document"` matches within the containing message, matching the extractor's order. Options:

- **Simple**: the card doesn't pick an index; it just opens the panel, which defaults to the latest version. This is consistent with the current one-shot auto-open behavior and is probably enough for v1.
- **Full**: pass the document's message-local index as a prop, wire a small `selectedDocumentIdx` store the card can set before opening.

Simple is likely fine — the user flow is usually "one document per message".

#### Artifacts inline, for contrast

Artifacts has no dedicated inline component. HTML/SVG code blocks render as ordinary code blocks; `ContentRenderer.svelte:203-217` hooks `onUpdate` from Markdown to call `showArtifacts.set(true)` + `showControls.set(true)` when it sees html/svg/xml-svg. There is no icon+title card pattern for Artifacts. The Document feature has the opportunity to set a better precedent here.

#### Key references

- `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte:370-466`
- `src/lib/components/common/ToolCallDisplay.svelte:90-172`
- `src/lib/components/common/Collapsible.svelte:63-129`
- `src/lib/components/chat/Messages/ContentRenderer.svelte:50-75, 203-217`
- `src/lib/components/chat/Chat.svelte:1099-1176`
- `src/app.css:188-229`
- `src/lib/components/icons/Document.svelte` (already imported into Menu.svelte)

### Issue 3 — Inline source pills & Citations footer

#### The inline pill pipeline

1. Markdown receives `sources` (or `sourceIds`) as props.
2. `Markdown.svelte:28, 35, 105, 108` — forwards `sourceIds` and `onSourceClick` to `MarkdownTokens`.
3. `MarkdownInlineTokens.svelte:134-135` — renders `<SourceToken>` only when `sourceIds.length > 0`.
4. `SourceToken.svelte:42-46` — for a single-id citation token: `<Source id={identifier} title={sourceIds[id - 1]} onClick={onSourceClick} />`. The `id - 1` comes from `citation-extension.ts:42-46` parsing the literal integer inside `[N]`.
5. `Source.svelte:41-53` — **the button is rendered only when `title !== 'N/A'`.** If `sourceIds[id - 1]` is `undefined` or `'N/A'`, **no button is rendered at all** — the pill is simply missing from the DOM.

That last point is the hidden root cause of "pills not clickable": the user may think they're clicking a pill that's just styled dead, but in fact the pill was never rendered.

#### How Document.svelte wires citations — compared to ResponseMessage

- ResponseMessage (canonical, working): `ResponseMessage.svelte:800, 815-821, 840-846` — `Markdown` receives `sources={message.sources}`; `ContentRenderer.svelte:47-96` derives `sourceIds` from that; the same `message.sources` feeds `<Citations>` bound via `bind:this={citationsElement}`. **`sourceIds` and footer `sources` share one source of truth.**
- Document (current): `Document.svelte:275-302` — `current.sourceIds` (for Markdown) and `current.sources` (for Citations) both come from `documentContents` entries populated in `Chat.svelte`, not directly from the message's `sources`. They are _intended_ to share a source of truth, but the extraction logic doesn't guarantee alignment with the numbering the model emitted.

#### `Citations.showSourceModal` — fail-silent on unknown id

- `src/lib/components/chat/Messages/Citations.svelte:28-74` — `showSourceModal(sourceId: string|number)`.
- Parses `sourceId` to `index = N - 1`, looks up `citations[index]` (built reactively at `:100-140` from flattened `sources[].document[]`).
- **If `citations[index]` is falsy, the entire body is skipped silently** — no modal, no error, nothing visible. So even if the pill _were_ rendered, clicking would no-op for out-of-range ids.

This is fail-silent behavior, which compounds the first bug: when things go wrong, the user sees nothing — not even an empty modal or a warning — making the whole "pills are clickable" claim look broken even when `citationsElement` is bound correctly.

#### How `current.sources` and `current.sourceIds` are collected

- `src/lib/components/chat/Chat.svelte:1141-1157` — `sourceIdsFromMessage(message)` walks `message.sources[].document[]`, deduped (same algorithm as `ContentRenderer.svelte:77-97`).
- `src/lib/components/chat/Chat.svelte:1159-1176` — `getDocuments()` iterates all assistant messages; for each, extracts `<details type="document">` / `write_document` tool blocks (`:1100-1138`), and attaches `sources = message.sources ?? []` and `sourceIds = sourceIdsFromMessage(message)` to **every** doc extracted from that message.
- `Chat.svelte:1175` — `documentContents.set(docs)`.

#### Why the footer is missing sources

The extraction passes the **entire `message.sources`** to every document extracted from that message — **no filtering to `[N]` references actually present in the document markdown**. That's the opposite failure mode to what the user reports, _unless_: the `[N]` numbers in the document markdown reference sources the model saw in an earlier turn (e.g., a prior RAG call), which were never attached as `message.sources` on _this_ message. In that case, `current.sources` and `current.sourceIds` are a **subset** of what the model is referencing.

Specifically:

- The model generates numbering based on whatever context it sees (may span turns, may reorder).
- `message.sources` is populated by the middleware only for that turn's retrieval.
- The two arrays have no shared contract about ordering or completeness.

#### Citation numbering convention — there is no renumbering

- `src/lib/utils/marked/citation-extension.ts:42` — the parser takes the literal integer inside `[N]`.
- `SourceToken.svelte:46` — indexes `sourceIds[id - 1]` with that integer directly.
- `Citations.svelte:32-41` — `showSourceModal(parseInt(id) - 1)` also uses it directly.
- There is no component that reassigns numbers based on presence in the document. If `id > sourceIds.length`, the pill vanishes (Source.svelte:41), and the modal no-ops (Citations.svelte).

#### Root-cause synthesis for Issue 3

- **"Pills not clickable"**: the pill is missing from the DOM because `sourceIds[id - 1]` is `undefined` / `'N/A'`. Visible only for ids within range.
- **"Footer doesn't contain all sources"**: `current.sources` is set to `message.sources` verbatim, which may not include sources from other turns that the model is still referencing. Worse: the model may be emitting numbers that don't correspond to any source this turn produced.
- **Both symptoms share a cause**: there is no authoritative "these are the sources this document cites" list derived _from the document markdown itself_. The model invents numbering; we pass through the message's raw retrieval; the two can diverge silently.

#### Potential fixes (directions only, not prescriptions)

1. **Renumber at extraction time**: parse the document markdown for `[N]` citations, collect the unique set of `message.sources` actually referenced (by index), rewrite the markdown to use 1-based contiguous numbering, and set `current.sources` to the filtered+reordered list. This aligns inline numbering with the footer.
2. **Promote sources to chat-level, not message-level**: if a document can reference sources from prior turns, the extractor must consult a conversation-level source registry. This is a larger refactor.
3. **Surface the error**: in the short term, log when `sourceIds[id - 1]` is undefined inside `SourceToken.svelte` (or render a visible "?" pill) so users and developers notice missing sources instead of them silently disappearing.

Option 1 is the most contained change and fixes both symptoms. Option 3 is a cheap diagnostic that pairs well with it.

#### Key references

- `src/lib/components/chat/Document.svelte:275-302`
- `src/lib/components/chat/Chat.svelte:1099-1176`
- `src/lib/components/chat/Messages/Markdown.svelte:28, 35, 105, 108`
- `src/lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte:134-135`
- `src/lib/components/chat/Messages/Markdown/SourceToken.svelte:42-46`
- `src/lib/components/chat/Messages/Markdown/Source.svelte:41-53`
- `src/lib/components/chat/Messages/Citations.svelte:28-74, 100-140`
- `src/lib/components/chat/Messages/ContentRenderer.svelte:47, 77-97, 190-200`
- `src/lib/components/chat/Messages/ResponseMessage.svelte:800, 815-821, 840-846`
- `src/lib/utils/marked/citation-extension.ts:42-46`

## Code References

- `src/lib/components/chat/ChatControls.svelte:2, 68-107, 265-295, 361-376, 438-530` — tab system, special-panel overrides, close handler
- `src/lib/components/chat/Document.svelte:89-99, 253-263, 275-302` — close behavior + citations placement
- `src/lib/components/chat/Chat.svelte:1099-1176` — `extractDocumentsFromMessage`, `sourceIdsFromMessage`, `getDocuments`, `documentContents.set`
- `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte:370-466` — where the new `type="document"` card branch belongs
- `src/lib/components/common/ToolCallDisplay.svelte:110-172` — reference pattern for the card
- `src/lib/components/common/Collapsible.svelte:63-129` — shimmer-on-header pattern
- `src/lib/components/chat/Messages/Markdown/Source.svelte:41-53` — the `title !== 'N/A'` gate that hides pills
- `src/lib/components/chat/Messages/Citations.svelte:28-74` — `showSourceModal` silent no-op on unknown id
- `src/app.css:188-229` — shimmer keyframes

## Architecture Insights

- **Tab infrastructure already exists in ChatControls.** Document is the anomaly — Artifacts/Embeds/CallOverlay really are full-panel overrides (different layout/interaction models), but Document is just another content view that should live inside the tab bar. Migrating it is a simplification, not a complication.
- **The inline-message "card that opens a panel" pattern is not yet formalized.** Artifacts doesn't have one; it uses the raw code block as its card. Introducing `DocumentCard.svelte` establishes a pattern that future inline-artifact features (e.g., tool outputs, artifacts, diagrams) could reuse. Consider naming it to reflect that generality (`PanelOpenerCard.svelte`?) — though doing so is scope creep for this ticket.
- **Citation numbering is an unowned contract.** The model emits `[N]`; the frontend trusts it implicitly. There is no middleware that validates numbering against attached sources, and no frontend component that filters/renumbers. The correct location to do the reconciliation is at **extraction time** (`Chat.svelte:1099-1176`), because that's where the document's markdown and the message's sources meet. A helper like `reconcileDocumentSources(markdown, messageSources) → { markdown, sources, sourceIds }` would isolate the concern.
- **Fail-silent is load-bearing but painful.** `Source.svelte` hides the pill; `Citations.svelte` no-ops the modal. Neither logs. When numbering breaks, the feature just quietly falls apart. Worth adding at least a dev-mode warning.

## Historical Context (from thoughts/)

- `thoughts/shared/plans/2026-04-13-markdown-document-artifact-feature.md` — the original phased plan for Document Writer. Document panel is designed as a full-panel override, not a tab (lines 500, 620, 642). Citations inside the panel are not mentioned in the plan — they were added post-plan during Phase 5 in response to a user request.
- `thoughts/shared/research/2026-04-13-markdown-document-artifact-feature.md` — original research, references Menu.svelte reopen path and artifact close behavior but not tab architecture.
- `thoughts/shared/research/2026-04-13-document-sidepanel-reopen-and-citations-footer.md` — yesterday's research on (a) adding the reopen-from-menu affordance (implemented, see `Menu.svelte:334-350`) and (b) moving Citations out of the `prose` container (done — see `Document.svelte:294-302` now has Citations in a non-prose wrapper). Tab-ification and clickability were explicitly not addressed there.

## Related Research

- `thoughts/shared/research/2026-04-13-markdown-document-artifact-feature.md`
- `thoughts/shared/plans/2026-04-13-markdown-document-artifact-feature.md`
- `thoughts/shared/research/2026-04-13-document-sidepanel-reopen-and-citations-footer.md`

## Revisions after user feedback (2026-04-13 21:13)

### Issue 1 — X should still close the sidepanel

**Revised direction:** the Document tab's X button should still close the whole sidebar (`showControls=false`), not just switch tabs. The tab-ification fix is really about the **reopen** path, not the close path.

Concrete mechanics:

- Keep `Document.svelte:253-263`'s X handler setting `showControls=false` (with or without also resetting `showDocument`).
- Add `savedTab='document'` persistence: when the user opens Document (via Menu reopen button, via ContentRenderer auto-detect, or by clicking the new inline card), set `activeTab='document'` in `ChatControls.svelte` and persist to `savedTab`.
- On sidebar reopen (user clicks Chat Controls), `activeTab` initializes from `savedTab` — so if the user last had Document, they land on Document.
- Fallback: if `savedTab==='document'` but `$documentContents.length === 0` (new chat, docs cleared), fall back to `'controls'` via the existing visibility-fallback picker at `ChatControls.svelte:76-88`.

The deeper fix from the original writeup still applies — Document lives in the tab system rather than as a `{:else if $showDocument}` override — but the X behavior stays as "close sidebar", which is the natural expectation. No tab-switch-on-close ceremony needed.

### Issue 3 — Data flow is symmetric; fix is wiring parity, not renumbering

**User correction (load-bearing):** the document's sources are the same JSON as the chat message's sources. If pills render and click correctly in the chat message, they should do so in the document too. The original writeup's "renumber at extraction time" theory was over-engineered.

Verifying the symmetry:

- `Chat.svelte:1141-1157` (`sourceIdsFromMessage`) — walks `message.sources[].document[]`, with the same flattening + dedup as `ContentRenderer.svelte:77-97` (`getSourceIds`). The only difference is the latter also sets `'N/A'` when `model.capabilities.citations === false`, which Source.svelte hides anyway. **Data-identical.**
- `Chat.svelte:1166-1170` — attaches the raw `message.sources` to each extracted document. `Document.svelte:300` passes `sources={current.sources}` to `<Citations>`. **Footer-identical.**
- `Document.svelte:280-292` — `sourceIds={current.sourceIds}` and an inline `onSourceClick` → `citationsElement?.showSourceModal(id)`. `ContentRenderer.svelte:199-200` + `ResponseMessage.svelte` pass the same prop pair. **Wiring-equivalent.**

So if pills work in chat and don't work in document, the bug is **one of**:

1. **Prose container interference** — Markdown renders _inside_ `prose dark:prose-invert` (`Document.svelte:274`). The user's yesterday-fix moved Citations out of prose, but Markdown (and therefore the `<button>` pills from `Source.svelte:42-52`) is still inside. Test: wrap the Markdown in `not-prose` (losing the pretty typography) or move the Markdown component into its own non-prose wrapper and apply prose to a grandchild that excludes the inline pills. A targeted repro: check if `.prose` styles descendant buttons through a `:not` chain or if `@tailwindcss/typography` applies `pointer-events`/`user-select` rules that affect inline-flow elements.
2. **Event propagation / lifecycle** — log whether `onSourceClick` fires at all. The handler already has a `console.log('[Document] source click', …)` at `Document.svelte:282-289`. If the log doesn't appear on click, it's DOM/event-level (prose, overlay div, or stacking context). If the log appears but no modal, it's `showSourceModal` (e.g., `citationsElement` null, or `citations[index]` not found).
3. **Simpler: swap `<Markdown>` for `<ContentRenderer>`** — ContentRenderer already owns `getSourceIds` + the `onSourceClick` plumbing. Using it in Document.svelte guarantees prop parity with chat messages and eliminates the duplicate `sourceIdsFromMessage` in Chat.svelte. Trade-off: ContentRenderer also pulls in FloatingButtons and artifact detection which Document doesn't want. Could take `{ floatingButtons: false, detectArtifacts: false }` as opt-outs.

Recommended next step: add the `console.log` repro (it's already there), click a pill, see if it fires. If it doesn't, Issue 3a is a prose/event problem. If it does, it's a Citations-side problem.

**On "footer doesn't use all sources":** worth double-checking whether the user is reading the _collapsed_ Citations chip (which shows "N sources" as a pill and expands to the full list — `Citations.svelte:163-230`) versus the _expanded_ list. If they saw only the collapsed chip and assumed it's the full list, there's no bug. Confirm with a screenshot after clicking the chip to expand.

## Open Questions

1. **Tab behavior when Document closes while Artifacts is also present** — if a chat produces both an artifact and a document, and a user is on the Document tab and clicks X, do we switch to the Artifacts tab (if it's also a tab, which it currently isn't) or to Controls? This depends on whether we also migrate Artifacts into the tab system or leave it as an override.
2. **Should the inline card be clickable to a specific document version?** The panel shows a `Version X of Y` navigator already (`Document.svelte:149-154`). Most chats will have one version per document, so defaulting to "latest" is probably fine — but if the user iterates on a document with multiple `write_document` calls in one assistant turn, they might want a card per version.
3. **Where should citation renumbering live** — a utility next to `sourceIdsFromMessage` in `Chat.svelte`, or a shared helper in `$lib/utils/marked/`? The latter is more discoverable but means extraction becomes more work per call.
4. **Should we add a dev-mode warning when `sourceIds[id - 1]` is undefined** in `SourceToken.svelte` to surface this class of bug earlier?
5. **Does the `document_writer` backend tool actually attach citations when it runs?** The extractor grabs `message.sources`, but if the tool execution path doesn't surface retrieval sources onto the message, the document panel will always look source-less for tool-call-path documents regardless of frontend fixes. Worth verifying in `backend/open_webui/tools/builtin.py:527` and the middleware path around `backend/open_webui/utils/middleware.py:2415`.
