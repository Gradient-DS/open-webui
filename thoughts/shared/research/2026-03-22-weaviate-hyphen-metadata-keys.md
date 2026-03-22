---
date: 2026-03-22T14:00:00+01:00
researcher: Claude Code
git_commit: 09c8ac766a597c7c352c9d0965a866f1ddfd947c
branch: fix/retrieval-weaviate-hyphens
repository: open-webui
topic: "Weaviate rejects metadata property names containing hyphens"
tags: [research, codebase, weaviate, retrieval, metadata, pdf, vector-db]
status: complete
last_updated: 2026-03-22
last_updated_by: Claude Code
---

# Research: Weaviate Rejects Metadata Property Names Containing Hyphens

**Date**: 2026-03-22T14:00:00+01:00
**Researcher**: Claude Code
**Git Commit**: 09c8ac766a597c7c352c9d0965a866f1ddfd947c
**Branch**: fix/retrieval-weaviate-hyphens
**Repository**: open-webui

## Research Question

PDF metadata keys with hyphens (e.g., `pdfsettings-inchmargins`) cause Weaviate batch inserts to fail silently. Where in the codebase should sanitization be applied?

## Summary

**Confirmed**: Metadata keys flow from PDF loaders to Weaviate completely unsanitized. Collection names ARE sanitized (hyphens replaced with underscores at `weaviate.py:85`), but property/metadata keys are not sanitized anywhere. The fix should be Weaviate-specific since other vector DB backends (ChromaDB, pgvector, Qdrant) don't have this GraphQL identifier restriction.

## Detailed Findings

### Root Cause

Weaviate requires all property names to be valid GraphQL identifiers: `/[_A-Za-z][_0-9A-Za-z]{0,230}/`. PDF documents can contain arbitrary metadata keys from their `/Info` dictionary. When a PDF producer embeds keys with hyphens (e.g., `pdfsettings-inchmargins`, `pdfsettings-footerheight`), these flow through to Weaviate and cause the entire batch insert to fail.

### Metadata Flow (no sanitization of keys anywhere)

1. **PDF Loader** (`PyPDFParser.lazy_parse()`) extracts `/Info` dict, strips leading `/`, lowercases keys — but does NOT sanitize characters
2. **`filter_metadata()`** (`vector/utils.py:6-11`) — only removes keys in `KEYS_TO_EXCLUDE` list, doesn't touch key names
3. **`process_metadata()`** (`vector/utils.py:14-28`) — only converts value types (datetime/list/dict to strings), doesn't touch key names
4. **`_make_json_serializable()`** (`weaviate.py:35-46`) — only converts UUID/datetime values, doesn't touch key names
5. **`properties.update(clean_metadata)`** (`weaviate.py:178/200`) — merges unsanitized keys directly into Weaviate object properties

### Collection Name Sanitization Exists (but not for properties)

`_sanitize_collection_name()` at `weaviate.py:75-97` already does exactly the right thing for collection names:
- Replaces hyphens with underscores
- Strips non-alphanumeric/underscore characters
- Ensures name starts with a capital letter

No equivalent function exists for property names.

### Sources of Hyphenated Metadata Keys

| Loader | Hyphenated Keys | Source |
|--------|----------------|--------|
| PyPDFLoader (default) | Any custom PDF `/Info` key with hyphens | PDF producer software |
| TikaLoader | `Content-Type`, `X-Tika-PDFextractInlineImages` | Tika response headers |
| DoclingLoader | `Content-Type` | MIME type header |
| ExternalDocumentLoader | Any key from external API | External service |

### Silent Failure

The batch insert fails but Open WebUI logs "added N items" because the success logging happens before the batch is flushed. This is why users see "No sources found" when querying — the embeddings were generated but never actually stored.

## Recommended Fix

Add a `_sanitize_property_name()` function in `weaviate.py` (similar to `_sanitize_collection_name`) and apply it in `insert()` and `upsert()` when building the `properties` dict. This keeps the fix Weaviate-specific since other vector DBs don't have this restriction.

```python
def _sanitize_property_name(name: str) -> str:
    """Sanitize property name to be a valid Weaviate/GraphQL identifier."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    name = name.strip("_")
    if not name:
        return None  # skip this property
    if not name[0].isalpha() and name[0] != "_":
        name = "_" + name
    return name
```

Apply in both `insert()` and `upsert()` when processing metadata:
```python
clean_metadata = {
    _sanitize_property_name(k): v
    for k, v in clean_metadata.items()
    if _sanitize_property_name(k)
}
```

## Code References

- `backend/open_webui/retrieval/vector/dbs/weaviate.py:75-97` — `_sanitize_collection_name()` (model for the fix)
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:162-182` — `insert()` method (needs fix)
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:184-204` — `upsert()` method (needs fix)
- `backend/open_webui/retrieval/vector/utils.py:6-28` — `filter_metadata()` and `process_metadata()` (no key sanitization)
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:110-149` — `_create_collection()` pre-defined schema properties

## Architecture Insights

- The vector DB abstraction (`VectorDBBase`) is backend-agnostic — each adapter handles its own quirks
- Collection name sanitization is already Weaviate-specific, so property name sanitization should follow the same pattern
- The `process_metadata()` utility in `utils.py` is shared across all backends and should NOT be modified for this Weaviate-specific issue
- Weaviate uses auto-schema for properties not in the pre-defined list, which means any new property name must be a valid GraphQL identifier

## Open Questions

- Should we also add batch error logging to detect silent failures? Currently batch errors are swallowed.
- Should the pre-defined schema in `_create_collection()` be extended to include common PDF metadata keys (like `pdfsettings_*`) to avoid auto-schema type inference issues?
