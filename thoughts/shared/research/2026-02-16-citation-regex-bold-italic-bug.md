---
date: 2026-02-16T12:00:00+01:00
researcher: claude
git_commit: bcd4a67876bd5b4450d45bbd9971bfe01fc0b229
branch: feat/sync-improvements
repository: open-webui
topic: "Citation regex misses bold, italic, and dash-adjacent citations"
tags: [research, codebase, citations, markdown, regex, rendering-bug]
status: complete
last_updated: 2026-02-16
last_updated_by: claude
---

# Research: Citation Regex Misses Bold, Italic, and Dash-Adjacent Citations

**Date**: 2026-02-16T12:00:00+01:00
**Researcher**: claude
**Git Commit**: bcd4a67876bd5b4450d45bbd9971bfe01fc0b229
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question
When we try to regex citations by looking for [], we seem to miss them when they are bold, italic or followed by a dash. Where is the regex occurring and could we somehow make this more robust?

## Summary

The citation regex itself (`citation-extension.ts`) is **not the problem**. The Marked lexer correctly tokenizes citations inside bold/italic — it creates `strong > citation` and `em > citation` token trees as expected.

The actual bug is in **`MarkdownInlineTokens.svelte`**: when recursively rendering `strong`, `em`, `del`, and `link` tokens, the component does not pass the `sourceIds` prop to the recursive `<svelte:self>` call. Since `sourceIds` defaults to `[]`, the citation rendering branch checks `(sourceIds ?? []).length > 0`, evaluates to `false`, and falls through to a plain `TextToken` — displaying `[1]` as inert text instead of an interactive source button.

## Detailed Findings

### Citation Extension Regex (`citation-extension.ts`)

The regex pipeline is correct and handles:
- Standard: `[1]`, `[1,2]`, `[1][2,3][4]`
- CJK fullwidth: `【1】`, `【3†L1-L4】`
- Footnote guard: skips `[^1]` patterns

**start()** (line 8): Scans for first occurrence of `[digit...]` or `【digit...】`
```typescript
return src.search(/(?:\[(\d[\d,\s]*)\]|【(\d[\d,\s]*)(?:†[^】]*)?】)/);
```

**tokenizer()** (line 17): Matches one or more adjacent citation groups from start of remaining source
```typescript
const rule = /^(?:\[(?:\d[\d,\s]*)\]|【(?:\d[\d,\s]*)(?:†[^】]*)?】)+/;
```

### The Rendering Bug (`MarkdownInlineTokens.svelte`)

Lines 44-53 — recursive self-calls for formatting tokens are **missing `{sourceIds}`**:

```svelte
{:else if token.type === 'strong'}
    <strong><svelte:self id={`${id}-strong`} tokens={token.tokens} {onSourceClick} /></strong>
{:else if token.type === 'em'}
    <em><svelte:self id={`${id}-em`} tokens={token.tokens} {onSourceClick} /></em>
{:else if token.type === 'del'}
    <del><svelte:self id={`${id}-del`} tokens={token.tokens} {onSourceClick} /></del>
```

Line 37 (link) also missing:
```svelte
<svelte:self id={`${id}-a`} tokens={token.tokens} {onSourceClick} {done} />
```

Compare with `MarkdownTokens.svelte` which correctly passes `{sourceIds}` in all its `MarkdownInlineTokens` usages (lines 96-101, 148-153, 357-363, etc.).

### Why This Causes the Bug

1. Marked lexes `**[1]**` → `{ type: 'strong', tokens: [{ type: 'citation', ids: [1] }] }`
2. `MarkdownInlineTokens` renders the `strong` token, recursing into `<svelte:self>` for children
3. The recursive call omits `sourceIds`, so it defaults to `[]`
4. When the child component encounters the `citation` token, `(sourceIds ?? []).length > 0` is `false`
5. Falls through to `<TextToken>` — renders `[1]` as plain text

### Dash Issue (Needs Clarification)

The regex handles `[1]-text` correctly — it matches `[1]` and the dash becomes regular text. Possible edge cases:
- `[1-2]` would NOT match (regex expects `\d[\d,\s]*` — no dashes allowed)
- Marked's built-in link parser might consume `[1]` in certain contexts before the citation extension

## Code References

- `src/lib/utils/marked/citation-extension.ts:6-17` — The regex patterns (start + tokenizer)
- `src/lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte:44-53` — **THE BUG**: missing `{sourceIds}` in strong/em/del recursive calls
- `src/lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte:37` — Also missing `{sourceIds}` in link recursive call
- `src/lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte:77-82` — Citation rendering branch that checks sourceIds
- `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte:96-101` — Correct usage (passes sourceIds)
- `src/lib/components/chat/Messages/Markdown.svelte:45` — Extension registration
- `backend/open_webui/config.py:2978-3002` — RAG template instructing LLM to use `[id]` format
- `backend/open_webui/utils/middleware.py:1575-1596` — Backend citation_idx_map construction

## Architecture Insights

The citation system has a clean separation:
1. **Backend** assigns sequential IDs and wraps docs in `<source id="N">` XML
2. **RAG template** instructs LLM to produce `[N]` inline references
3. **Frontend** uses a custom Marked tokenizer extension to parse `[N]` into citation tokens
4. **Rendering** maps citation token IDs to source metadata for interactive buttons

The bug is purely in the rendering layer — the tokenization layer works correctly at all nesting levels.

## Fix

Add `{sourceIds}` to all `<svelte:self>` calls in `MarkdownInlineTokens.svelte`:
- Line 45: strong
- Line 47: em
- Line 53: del
- Line 37: link

## Open Questions

- What specific dash-adjacent pattern is failing? Need concrete LLM output examples to diagnose.
- Could Marked's link reference parser be consuming `[N]` before the citation extension in certain edge cases?
