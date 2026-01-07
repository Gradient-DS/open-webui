---
date: 2026-01-07T16:00:00+01:00
researcher: Claude
git_commit: 86886d81c6671321588f28813e4b494337c0c27b
branch: main
repository: open-webui
topic: "Citation Format Parsing for Multiple AI Model Outputs"
tags: [research, codebase, citations, parsing, marked-js, rag]
status: complete
last_updated: 2026-01-07
last_updated_by: Claude
---

# Research: Citation Format Parsing for Multiple AI Model Outputs

**Date**: 2026-01-07T16:00:00+01:00
**Researcher**: Claude
**Git Commit**: 86886d81c6671321588f28813e4b494337c0c27b
**Branch**: main
**Repository**: open-webui

## Research Question

How can we handle multiple citation formats from different AI models? Specifically, models like `openai/gpt-oss-120b` output Japanese bracket citations with extended metadata like `【3†L1-L4】` instead of the standard `[1]` format.

## Summary

**Key Finding**: Open WebUI already has a citation parser that handles both `[1]` and `【1】` formats, but it does NOT currently support the extended format `【3†L1-L4】` with dagger symbols and line references.

**Options**:
1. **Extend the existing regex** to capture extended formats (recommended)
2. **Pre-process/normalize** citations before rendering
3. **Use an external library** (none found that handles this specific format)

## Detailed Findings

### Current Citation Parser Implementation

**File**: `src/lib/utils/marked/citation-extension.ts:1-57`

The current implementation uses a marked.js extension:

```typescript
// Current start trigger (line 8)
return src.search(/(?:\[(\d[\d,\s]*)\]|【(\d[\d,\s]*)】)/);

// Current tokenizer rule (line 17)
const rule = /^(?:\[(?:\d[\d,\s]*)\]|【(?:\d[\d,\s]*)】)+/;

// Current group extraction (line 24)
const groupRegex = /(?:\[([\d,\s]+)\]|【([\d,\s]+)】)/g;
```

**Currently Supported Formats**:
- `[1]`, `[2]`, `[1,2,3]` - Standard brackets
- `【1】`, `【1,2,3】` - Japanese lenticular brackets
- Adjacent combinations: `[1][2]`, `【1】【2】`, `[1]【2】`

**NOT Supported**:
- `【3†L1-L4】` - Extended format with dagger and line references
- `[doc1]`, `[source:1]` - Named references
- Perplexity URL arrays
- OpenAI `url_citation` annotations

### The Problem Format: `【3†L1-L4】`

This format appears to be from OpenAI's file search / retrieval system:
- `3` - The source/document number
- `†` (U+2020 DAGGER) - Separator indicating additional metadata
- `L1-L4` - Line range reference (lines 1-4 of the source)

Similar formats seen:
- `【1†source】` - Source name reference
- `【2†p.15】` - Page reference
- `【3†L1-L4】` - Line range reference

### Citation Formats by Platform

| Platform | Format | Example | Currently Supported |
|----------|--------|---------|---------------------|
| Default/Claude | Bracket numeric | `[1]`, `[2,3]` | Yes |
| CJK Models | Japanese brackets | `【1】`, `【2】` | Yes |
| OpenAI GPT-OSS | Extended metadata | `【3†L1-L4】` | **No** |
| OpenAI Responses API | URL annotations | `url_citation` object | No |
| Perplexity | Array indices | `[1]`, `[2]` + citations array | Yes (indices only) |
| Azure OpenAI | Doc references | `[doc1]`, `[doc2]` | No |

### Recommended Solution: Extend the Regex

Modify `citation-extension.ts` to handle extended formats:

```typescript
export function citationExtension() {
  return {
    name: 'citation',
    level: 'inline' as const,

    start(src: string) {
      // Extended: match [number], 【number】, and 【number†...】
      return src.search(/(?:\[(\d[\d,\s]*)\]|【(\d+)(?:†[^】]*)?)】/);
    },

    tokenizer(src: string) {
      // Avoid matching footnotes
      if (/^\[\^/.test(src)) return;

      // Extended rule: handles [1], [1,2], 【1】, 【1,2】, 【3†L1-L4】
      const rule = /^(?:\[(?:\d[\d,\s]*)\]|【(?:\d+)(?:†[^】]*)?】)+/;
      const match = rule.exec(src);
      if (!match) return;

      const raw = match[0];

      // Extended group extraction - captures number even with metadata suffix
      const groupRegex = /(?:\[([\d,\s]+)\]|【(\d+)(?:†[^】]*)?】)/g;
      const ids: number[] = [];
      let m: RegExpExecArray | null;

      while ((m = groupRegex.exec(raw))) {
        // m[1] is for [] brackets, m[2] is for 【】 brackets (just the number)
        const content = m[1] || m[2];
        if (content) {
          const parsed = content
            .split(',')
            .map((n) => parseInt(n.trim(), 10))
            .filter((n) => !isNaN(n));
          ids.push(...parsed);
        }
      }

      return {
        type: 'citation',
        raw,
        ids
      };
    },

    renderer(token: any) {
      return token.ids.join(',');
    }
  };
}
```

**Regex Breakdown**:
- `【(\d+)` - Match Japanese left bracket followed by one or more digits
- `(?:†[^】]*)?` - Optionally match dagger followed by any characters except closing bracket
- `】` - Match Japanese right bracket

### Alternative: Pre-processing Normalization

Add a function to normalize citations before markdown parsing:

```typescript
function normalizeCitations(content: string): string {
  // Convert extended Japanese brackets to simple format
  // 【3†L1-L4】 → 【3】
  // 【1†source】 → 【1】
  return content.replace(/【(\d+)†[^】]*】/g, '【$1】');
}
```

Apply in `Markdown.svelte` before tokenization.

### External Libraries Researched

| Library | Language | Use Case | Handles Our Format? |
|---------|----------|----------|---------------------|
| Citation.js | JS | BibTeX, RIS, CSL | No (academic formats) |
| eyecite | Python | Legal citations | No |
| refextract | Python | Scholarly references | No |
| marked.js extensions | JS | Custom inline tokens | Base for our solution |

**Conclusion**: No existing library handles the `【3†L1-L4】` format. Custom extension is required.

## Code References

- `src/lib/utils/marked/citation-extension.ts:1-57` - Current citation tokenizer
- `src/lib/components/chat/Messages/Markdown.svelte:43-50` - Extension registration
- `src/lib/components/chat/Messages/Markdown/SourceToken.svelte:42-79` - Citation rendering
- `backend/open_webui/config.py:2878-2902` - RAG template with citation instructions
- `backend/open_webui/utils/middleware.py:1573-1596` - Citation ID mapping

## Architecture Insights

The citation system has a clean separation:

1. **Backend** (`middleware.py`): Assigns numeric IDs to sources, wraps in `<source id="N">` tags
2. **RAG Template** (`config.py`): Instructs models to use `[id]` format
3. **Frontend Parser** (`citation-extension.ts`): Tokenizes bracket patterns
4. **Frontend Renderer** (`SourceToken.svelte`): Renders clickable citation buttons

The issue is that different models don't follow the RAG template instructions and output their preferred format instead.

## Recommendations

1. **Immediate Fix**: Extend `citation-extension.ts` regex to handle `【n†...】` format
2. **Robust Solution**: Add a citation normalization step that converts various formats to `[n]` before rendering
3. **Future Enhancement**: Consider model-specific citation format configuration

## Open Questions

1. Are there other extended formats from `openai/gpt-oss-120b` we should handle?
2. Should we preserve the metadata (like `L1-L4`) for display purposes?
3. Would model-specific citation formatters be valuable?
