---
date: 2026-04-13
researcher: Lex Lubbers (with Claude Opus 4.6)
git_commit: 95e12cbb14754632ef47926ce21edf605f74583a
branch: feat/doc-writer
repository: open-webui
topic: 'Document panel: reopen mechanism + Citations footer rendering'
tags: [research, document-writer, sidepanel, citations, prose, artifacts]
status: complete
last_updated: 2026-04-13
last_updated_by: Lex Lubbers
---

# Research: Document panel — reopen mechanism + Citations footer rendering

**Date**: 2026-04-13
**Researcher**: Lex Lubbers (with Claude Opus 4.6)
**Git Commit**: 95e12cbb14754632ef47926ce21edf605f74583a
**Branch**: feat/doc-writer
**Repository**: open-webui

## Research Question

Two issues surfaced after the Document Writer feature landed (phase 5):

1. **How do we open a document once we closed it?** Once the user clicks the X on the Document sidepanel, there is no visible affordance to reopen it. What is the established pattern in the codebase (Artifacts) and what do we need to add?
2. **The Citations source list at the bottom is not properly rendering inside the document panel** — the sources footer that I added inside `Document.svelte` looks corrupted compared to how the same `<Citations>` component renders under a normal chat message.

## Summary

**Issue 1 — reopening:** The Artifacts panel has a "re-open from menu" affordance that lives in the **chat-level navbar menu** (`src/lib/components/layout/Navbar/Menu.svelte:313-328`). It is conditionally rendered when `$artifactContents.length > 0` and sets `showControls` + `showArtifacts` to `true`. There is currently **no equivalent for the Document panel**. The fix is additive: mirror that block for `$documentContents`.

**Issue 2 — Citations footer rendering:** The `<Citations>` component was designed to render _outside_ any `prose` container. In `ResponseMessage.svelte:839-847` it sits in a bare `<div>`. In `Document.svelte:315-334` I placed it _inside_ a `prose dark:prose-invert` wrapper, which applies `@tailwindcss/typography` opinionated styles (margins, list styling, link colors, button spacing) to every descendant — including the `<button>`, `<div>`-with-`flex`, and nested `<img>` inside Citations. The fix is to either (a) move `<Citations>` out of the prose div, or (b) wrap it in a `not-prose` class provided by `@tailwindcss/typography`.

## Detailed Findings

### Issue 1 — Panel re-open mechanism

#### Where `showArtifacts.set(true)` is invoked

| Site                                         | File:line                                                     | Trigger                                                          |
| -------------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------- |
| Auto-detect HTML/SVG code blocks in markdown | `src/lib/components/chat/Messages/ContentRenderer.svelte:214` | Automatic — fires when Markdown emits an `html`/`svg` code token |
| Preview button inside a code block           | `src/lib/components/chat/Messages/ContentRenderer.svelte:222` | User click                                                       |
| **Chat menu re-open button**                 | `src/lib/components/layout/Navbar/Menu.svelte:320`            | User click — **this is the re-open affordance**                  |

The menu button (the third row above) is the template to copy. It appears in the hamburger menu at the top-right of a chat and is only rendered when `$artifactContents` contains one or more artifacts:

```svelte
{#if isFeatureEnabled('artifacts') && ($artifactContents ?? []).length > 0}
	<button
		id="chat-artifacts-button"
		on:click={async () => {
			await showControls.set(true);
			await showArtifacts.set(true);
			await showOverview.set(false);
			await showEmbeds.set(false);
		}}
	>
		<Cube className="size-4" strokeWidth="1.5" />
		<div>{$i18n.t('Artifacts')}</div>
	</button>
{/if}
```

(`src/lib/components/layout/Navbar/Menu.svelte:313-328`)

Immediately after the button, a `hr` divider is rendered _only if_ any of `chat_controls`, `chat_overview`, or `artifacts` is present (`Menu.svelte:330`). This means adding a Document button will need the same pattern so the divider logic still lines up.

#### Where `showDocument.set(true)` is invoked

| Site                                                                  | File:line                                                    | Trigger                                                                |
| --------------------------------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Auto-detect `<details type="document">` or `write_document` tool call | `src/lib/components/chat/Messages/ContentRenderer.svelte:67` | Automatic — one shot per message instance via `documentDetected` guard |

There is **no menu item for Document**. Menu.svelte does not import `showDocument` or `documentContents` (see imports at `Menu.svelte:13-26`).

#### Closing — and why reopen is needed

`Document.svelte` closes via the X button at line 248-252, calling `showControls.set(false)` and `showDocument.set(false)`. The `documentDetected` flag inside `ContentRenderer.svelte` is per-ContentRenderer-instance and gates re-fires, so once the user closes the panel for an already-streamed message, the auto-detect path will not re-open it for that same content. Without a manual re-open, the user has to navigate away and back (new chat + return) or hard refresh.

#### Recommended change (for Issue 1)

Additive: in `src/lib/components/layout/Navbar/Menu.svelte`

1. Extend the store imports (around line 13-26) to include `showDocument, documentContents`.
2. Add an icon import — the `Cube` icon is already used for Artifacts; pick a document icon from `$lib/components/icons/` (e.g., `DocumentText.svelte`). If absent, reuse an existing one to keep the change minimal.
3. Insert a sibling button right after the Artifacts block (after line 328):

```svelte
{#if isFeatureEnabled('document_writer') && ($documentContents ?? []).length > 0}
	<button
		id="chat-document-button"
		class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl select-none w-full"
		on:click={async () => {
			await showControls.set(true);
			await showDocument.set(true);
			await showArtifacts.set(false);
			await showOverview.set(false);
			await showEmbeds.set(false);
		}}
	>
		<DocumentText className="size-4" strokeWidth="1.5" />
		<div>{$i18n.t('Document')}</div>
	</button>
{/if}
```

4. Update the divider condition at `Menu.svelte:330` to include the new condition, so the separator hairline still appears when either Artifacts _or_ Document is present:

```svelte
{#if ($mobile && isFeatureEnabled('chat_controls') && ...) || isFeatureEnabled('chat_overview') || (isFeatureEnabled('artifacts') && ($artifactContents ?? []).length > 0) || (isFeatureEnabled('document_writer') && ($documentContents ?? []).length > 0)}
    <hr class="border-gray-50/30 dark:border-gray-800/30 my-1" />
{/if}
```

5. i18n — add `"Document"` to `en-US/translation.json` and `nl-NL/translation.json` (Dutch: `"Document"` — same spelling, no translation needed). Run `npm run i18n:parse`.

### Issue 2 — Citations footer rendering

#### How Citations is placed under a normal chat message

In `src/lib/components/chat/Messages/ResponseMessage.svelte:835-847` the component lives in a plain sibling `<div>` after the error block and before `CodeExecutions`:

```svelte
{#if (message?.sources || message?.citations) && (model?.info?.meta?.capabilities?.citations ?? true)}
	<Citations
		bind:this={citationsElement}
		id={message?.id}
		{chatId}
		sources={message?.sources ?? message?.citations}
		{readOnly}
	/>
{/if}
```

Tracing upward: the enclosing container is a regular flex column with no `prose` class. ResponseMessage does _not_ wrap the message body in Tailwind typography — it relies on `MarkdownTokens.svelte` to style each block individually.

#### What Citations renders

`src/lib/components/chat/Messages/Citations.svelte:163-230` — two blocks:

- **Collapsed state** (always on): a small `<button>` with class `"text-xs font-medium text-gray-600 dark:text-gray-300 px-3.5 h-8 rounded-full hover:bg-gray-100 … border border-gray-50"` that toggles the list open. When URL citations exist, up to three favicons are stacked to the left.
- **Expanded state** (`showCitations`): a vertical flex list of source cards, each a `<button>` with a numbered badge + `decodeString(citation.source.name)`.

Both blocks use utility classes (`flex`, `gap-1`, `items-center`, `text-xs`) that are tuned for a _non_-prose parent.

#### Why placing Citations inside `prose` corrupts it

The document body wrapper I added at `Document.svelte:316`:

```svelte
<div class="max-w-3xl w-full mx-auto px-6 py-6 prose dark:prose-invert">
	<Markdown ... />
	{#if (current.sources ?? []).length > 0}
		<Citations ... />
	{/if}
</div>
```

`@tailwindcss/typography` applies many descendant selectors to elements inside `.prose`:

- `.prose button` → not directly targeted by default, but the `.prose div` or `.prose img` _are_ — the favicon stack (`Citations.svelte:179-186`) gets `margin-top`, `margin-bottom`, and borders altered by `.prose img`
- `.prose a`, `.prose h1-h6`, `.prose ul`, `.prose ol`, `.prose p` — set margins, bullet styles
- `.prose :where(...)` rules — override `text-sm`, `font-weight`, spacing
- The `py-1 -mx-0.5 w-full flex gap-1 items-center flex-wrap` outer wrapper (`Citations.svelte:165`) collides with `.prose` block-spacing rules

This is why the "N Sources" chip either (a) loses its rounded-full pill shape, (b) wraps with extra vertical margin, (c) has the favicon stack pushed onto its own line, or (d) the expanded list cards lose their small font.

This is also consistent with the user screenshot (`Image #7`): the sources list reads like bare text rather than a pill/chip row.

#### Recommended fix (for Issue 2)

Two options; both are one-liners:

**Option A — move Citations outside the prose container** (cleaner separation, matches ResponseMessage pattern):

```svelte
<div class="flex-1 w-full h-full overflow-y-auto">
	<div class="h-full flex flex-col">
		{#if contents.length > 0 && current}
			<div class="max-w-3xl w-full mx-auto px-6 py-6 prose dark:prose-invert">
				<Markdown ... />
			</div>
			{#if (current.sources ?? []).length > 0}
				<div class="max-w-3xl w-full mx-auto px-6 pb-6">
					<Citations
						bind:this={citationsElement}
						id={`document-${$chatId ?? 'preview'}-${selectedContentIdx}`}
						chatId={$chatId ?? ''}
						sources={current.sources}
					/>
				</div>
			{/if}
		{:else}
			...
		{/if}
	</div>
</div>
```

**Option B — wrap Citations in `not-prose`** (in-place, less structural churn):

```svelte
<div class="max-w-3xl w-full mx-auto px-6 py-6 prose dark:prose-invert">
	<Markdown ... />
	{#if (current.sources ?? []).length > 0}
		<div class="not-prose mt-4">
			<Citations ... />
		</div>
	{/if}
</div>
```

The `not-prose` class is a built-in utility from `@tailwindcss/typography` that disables prose rules for a subtree. It is the idiomatic escape hatch. Option B has the advantage of keeping Citations scrolling in lockstep with the document body.

I recommend **Option B** since it matches existing Tailwind conventions, is strictly additive, and preserves the single-scroll-container UX.

## Code References

- `src/lib/components/chat/Document.svelte:315-334` — my current placement of `<Citations>` inside the prose wrapper (the cause of Issue 2)
- `src/lib/components/chat/Messages/ResponseMessage.svelte:839-847` — canonical Citations placement outside any prose container
- `src/lib/components/chat/Messages/Citations.svelte:163-230` — Citations component template (collapsed chip + expanded list)
- `src/lib/components/layout/Navbar/Menu.svelte:313-328` — Artifacts re-open button (the pattern to copy for Issue 1)
- `src/lib/components/layout/Navbar/Menu.svelte:330` — divider condition that must be extended when the Document button is added
- `src/lib/components/chat/Messages/ContentRenderer.svelte:51-75` — per-message auto-open guard (`documentDetected`) — explains why auto-open does not re-fire after user-close
- `src/lib/components/chat/Document.svelte:248-252` — the X-close handler that triggered the "no way back" gap
- `src/lib/stores/index.ts:112-121` — `showDocument` + `documentContents` stores

## Architecture Insights

- **Re-open affordance as a Menu item, not a Floating button.** Open WebUI consistently uses the chat-level menu (top-right hamburger) for "open this side panel" entries when the panel can be closed. This keeps the message surface itself uncluttered, but costs discoverability. A future improvement could be a small floating-button style toggle pinned to the right edge when `documentContents.length > 0` — but that would be _new_ UX, not matching existing Artifacts behavior.
- **Prose containers are a local choice, not a global one.** ResponseMessage relies on `MarkdownTokens.svelte` to style each block via explicit Tailwind classes, avoiding `@tailwindcss/typography` entirely. Document.svelte uses `prose` _on purpose_ to get nice typography for the document body — but that choice must end at the document body's boundary. `not-prose` is the expected way to exempt a subtree.
- **`documentContents` being `null` vs `[]`.** The initial store value is `null`, Chat.svelte resets it to `[]` on new-chat, and the Menu condition uses `($documentContents ?? []).length > 0`. The null-coalescing is important so the button is hidden _both_ in a fresh chat and in chats with no document.

## Historical Context (from thoughts/)

- `thoughts/shared/plans/2026-04-13-markdown-document-artifact-feature.md` — the original phased plan. The "What We're NOT Doing" block does not list a re-open mechanism, so this is an outright gap rather than an intentional omission. The plan does not mention Citations inside the document panel at all — that was added post-plan in response to a user request during Phase 5.
- `thoughts/shared/research/2026-04-13-markdown-document-artifact-feature.md` — research that preceded the plan. It surveys Artifacts' close behavior and references the Menu.svelte re-open path, but the synthesis went into Document.svelte design rather than Menu.svelte parity.

## Related Research

- `thoughts/shared/research/2026-04-13-markdown-document-artifact-feature.md` — original feature research
- `thoughts/shared/plans/2026-04-13-markdown-document-artifact-feature.md` — implementation plan this doc supplements

## Open Questions

1. Should the Document button in the Menu also show a small badge / count when there are multiple documents in one conversation? Artifacts doesn't — matching it keeps parity.
2. Should we also handle the symmetric case for `<details type="document" done="false">` (in-progress streaming document tags) in `extractDocumentsFromMessage` — currently the regex matches any `type="document"` regardless of `done`, so a streaming-in-progress tag _will_ be scooped into `documentContents`. Is that desirable? (Probably yes — the panel updates live as the stream lands.)
3. Are there Playwright/Cypress tests touching Artifacts re-open that we should extend for Document? Phase 6 of the plan skipped E2E for v1.
