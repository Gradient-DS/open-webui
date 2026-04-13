# Push Integration: Metadata, OpenAPI Spec, Curl Example & Validation

## Overview

Enhance the push integration with four improvements in one pass:

1. Update the curl example to showcase all metadata fields
2. Add a scoped OpenAPI spec download (integration endpoints only) in the admin panel
3. Propagate custom metadata to vector store chunks for RAG filtering
4. Add server-side validation of `custom_metadata_fields` defined in provider config

## Current State Analysis

### What Exists

- **Metadata fields already supported**: Both `IngestCollection` and `IngestDocumentBase` have `metadata: dict`, `tags: list[str]`, and typed fields like `content_type`, `language`, `author`, `modified_at` (`integrations.py:29-51`)
- **Metadata stored in DB**: File records store `provider_metadata` in `file.meta` JSON column (`integrations.py:145-155`). KB records store it in `knowledge.meta.integration.provider_metadata` (`integrations.py:121-131`)
- **Metadata NOT in vector store**: `_build_base_metadata()` at `integrations.py:225-237` only includes `name`, `source`, `file_id`, `created_by`, `author`, `language`, `source_provider`
- **Curl example is minimal**: Only shows `source_id`, `name`, `data_type`, `access_control`, `filename`, `text`, `title` (`IntegrationProviders.svelte:299`)
- **OpenAPI available in dev only**: `main.py:880-886` gates `/openapi.json` behind `ENV == "dev"`
- **`custom_metadata_fields`** defined in provider config UI but never validated server-side
- **Vector metadata processing**: `process_metadata()` in `retrieval/vector/utils.py:14-28` converts `list` and `dict` values to strings — so nested `metadata` dict values will be stringified

### Key Discoveries

- The integration router is mounted with tag `"integrations"` at `main.py:1769-1771` — we can use this tag to filter the OpenAPI spec
- `process_metadata()` converts dicts/lists to strings, so we should flatten `doc.metadata` into individual keys with a prefix (e.g., `meta_<key>`) rather than nesting
- Vector DB filter support in `search()` is limited (only ChromaDB and pgvector), but storing metadata on chunks is still valuable for display and future filtering
- `custom_metadata_fields` config is already persisted in `INTEGRATION_PROVIDERS` and accessible via `provider_config` in the endpoint

## Desired End State

1. **Curl example** shows all available fields including `metadata`, `content_type`, `tags`, `author`
2. **OpenAPI spec** for integration endpoints only is downloadable from the admin panel as a link below the curl example
3. **Vector chunks** include `content_type` and flattened `metadata` keys so they're stored alongside chunks for future filtering
4. **Server-side validation** rejects pushes that are missing `required` custom metadata fields defined in the provider config

### Verification

- Curl example visually shows metadata fields in admin panel
- GET `/api/v1/integrations/openapi.json` returns a valid OpenAPI spec with only integration routes
- Download link appears below curl example and triggers a file download
- After pushing a document with metadata, vector DB chunks contain `content_type` and `meta_*` keys
- Pushing without required custom metadata fields returns 400 error

## What We're NOT Doing

- Modifying the RAG query pipeline to support metadata filtering (that's a separate effort)
- Adding metadata filtering UI to the chat interface
- Changing how other (non-integration) documents store vector metadata
- Adding filter support to vector DB backends that don't support it (Weaviate, Qdrant, etc.)

## Implementation Approach

All four changes are independent and can be implemented in any order. We'll go backend-first (validation + OpenAPI endpoint + vector metadata), then frontend (curl example + download link).

---

## Phase 1: Backend — Vector Metadata Propagation

### Overview

Extend `_build_base_metadata()` to include `content_type`, `tags`, and flattened `doc.metadata` entries in vector chunk metadata.

### Changes Required

#### 1. Extend `_build_base_metadata()`

**File**: `backend/open_webui/routers/integrations.py`
**Changes**: Add `content_type`, `tags`, and flattened metadata dict entries

```python
def _build_base_metadata(
    doc: IngestDocumentBase, file_id: str, provider: str, user_id: str
) -> dict:
    """Build common metadata dict for LangChain Documents."""
    base = {
        "name": doc.title or doc.filename,
        "source": doc.source_url or doc.filename,
        "file_id": file_id,
        "created_by": user_id,
        "author": doc.author,
        "language": doc.language,
        "source_provider": provider,
        "content_type": doc.content_type,
        "tags": doc.tags,  # process_metadata() will convert list to string
    }
    # Flatten doc.metadata into prefixed keys to avoid collisions
    for key, value in doc.metadata.items():
        base[f"meta_{key}"] = value
    return base
```

Note: `process_metadata()` in `retrieval/vector/utils.py` will automatically convert `tags` (list) and any dict/datetime values to strings before vector DB storage. No changes needed there.

### Success Criteria

#### Automated Verification:

- [ ] Backend starts without errors: `open-webui dev`
- [ ] Linting passes: `npm run format:backend`

#### Manual Verification:

- [ ] Push a document with `metadata: {"department": "eng", "version": "1.2"}` and `content_type: "application/pdf"` via curl
- [ ] Query the vector DB collection and verify chunks contain `content_type`, `tags`, `meta_department`, `meta_version` keys

---

## Phase 2: Backend — Custom Metadata Validation

### Overview

Validate that documents include all `required` custom metadata fields defined in the provider config.

### Changes Required

#### 1. Add validation helper

**File**: `backend/open_webui/routers/integrations.py`
**Changes**: Add function after `get_integration_provider()` (after line 94)

```python
def _validate_custom_metadata(doc: IngestDocumentBase, provider_config: dict):
    """Validate that required custom metadata fields are present in doc.metadata."""
    custom_fields = provider_config.get("custom_metadata_fields", [])
    missing = []
    for field in custom_fields:
        if field.get("required") and field.get("key") not in doc.metadata:
            missing.append(field["key"])
    if missing:
        raise HTTPException(
            400,
            f"Document '{doc.source_id}' is missing required metadata fields: {', '.join(missing)}",
        )
```

#### 2. Call validation in the processing dispatch

**File**: `backend/open_webui/routers/integrations.py`
**Changes**: Add validation call after each document is parsed, before processing. In all three `data_type` blocks (`parsed_text` at ~line 535, `chunked_text` at ~line 553, `full_documents` at ~line 577):

```python
# After: doc = ParsedTextDocument(**raw_doc)  (or ChunkedTextDocument or FullDocument)
_validate_custom_metadata(doc, provider_config)
```

### Success Criteria

#### Automated Verification:

- [ ] Backend starts without errors: `open-webui dev`
- [ ] Linting passes: `npm run format:backend`

#### Manual Verification:

- [ ] Configure a provider with a required custom metadata field `department` (key: "department", required: true)
- [ ] Push a document WITHOUT `metadata.department` → expect 400 error with clear message
- [ ] Push a document WITH `metadata.department` → expect success
- [ ] Push a document to a provider with NO custom fields → expect success (no validation)

---

## Phase 3: Backend — Scoped OpenAPI Spec Endpoint

### Overview

Add a dedicated endpoint that returns an OpenAPI spec containing only the integration routes. This runs regardless of `ENV` setting.

### Changes Required

#### 1. Add OpenAPI spec endpoint to integrations router

**File**: `backend/open_webui/routers/integrations.py`
**Changes**: Add new endpoint at the bottom of the file (after the delete endpoints). Import `Request` is already present.

```python
@router.get("/openapi.json")
def get_integration_openapi(request: Request, user=Depends(get_verified_user)):
    """Return OpenAPI spec scoped to integration endpoints only."""
    full_spec = request.app.openapi()

    # Filter paths to only integration endpoints
    integration_prefix = "/api/v1/integrations"
    filtered_paths = {
        path: ops
        for path, ops in full_spec.get("paths", {}).items()
        if path.startswith(integration_prefix) and path != f"{integration_prefix}/openapi.json"
    }

    # Build scoped spec
    scoped_spec = {
        "openapi": full_spec.get("openapi", "3.1.0"),
        "info": {
            "title": "Open WebUI — Integration API",
            "version": full_spec.get("info", {}).get("version", "1.0.0"),
            "description": "API specification for the Open WebUI push integration endpoints.",
        },
        "paths": filtered_paths,
    }

    # Include only referenced schemas
    all_schemas = full_spec.get("components", {}).get("schemas", {})
    if all_schemas:
        # Collect all $ref'd schema names from filtered paths
        import re
        refs = set()
        def _collect_refs(obj):
            if isinstance(obj, dict):
                if "$ref" in obj:
                    ref = obj["$ref"]
                    # Extract schema name from "#/components/schemas/SchemaName"
                    if ref.startswith("#/components/schemas/"):
                        refs.add(ref.split("/")[-1])
                for v in obj.values():
                    _collect_refs(v)
            elif isinstance(obj, list):
                for item in obj:
                    _collect_refs(item)

        _collect_refs(filtered_paths)

        # Also collect transitive refs from the schemas themselves
        changed = True
        while changed:
            changed = False
            for name in list(refs):
                if name in all_schemas:
                    before = len(refs)
                    _collect_refs(all_schemas[name])
                    if len(refs) > before:
                        changed = True

        if refs:
            scoped_spec["components"] = {
                "schemas": {
                    name: schema
                    for name, schema in all_schemas.items()
                    if name in refs
                }
            }

    return scoped_spec
```

Note: The `re` import at the top of the function is unnecessary (leftover from an earlier draft) — remove it. The `_collect_refs` approach uses only dict/list traversal.

### Success Criteria

#### Automated Verification:

- [ ] Backend starts without errors: `open-webui dev`
- [ ] Linting passes: `npm run format:backend`
- [ ] `curl -H "Authorization: Bearer <token>" http://localhost:8080/api/v1/integrations/openapi.json` returns valid JSON with only integration paths

#### Manual Verification:

- [ ] Response contains only `/api/v1/integrations/ingest`, `/api/v1/integrations/collections/{source_id}`, and `/api/v1/integrations/collections/{source_id}/documents/{document_source_id}` paths (NOT the `/openapi.json` endpoint itself)
- [ ] Response includes referenced schemas (IngestForm, IngestCollection, etc.)
- [ ] Works in both dev and production ENV settings

**Implementation Note**: After completing phases 1-3 and all automated verification passes, pause here for manual confirmation before proceeding to Phase 4.

---

## Phase 4: Frontend — Enhanced Curl Example & OpenAPI Download Link

### Overview

Update the curl example to show metadata fields. Add a download link for the scoped OpenAPI spec below the curl example.

### Changes Required

#### 1. Update curl example data

**File**: `src/lib/components/admin/Settings/IntegrationProviders.svelte`
**Changes**: Replace the `exampleData` `@const` at line 299

Replace the current line 299:

```svelte
{@const exampleData = JSON.stringify(
	{
		collection: {
			source_id: 'my-collection-123',
			name: 'My Collection',
			data_type: 'parsed_text',
			access_control: null
		},
		documents: [
			{
				source_id: 'doc-1',
				filename: 'example.txt',
				text: 'Document content here...',
				title: 'Example Document'
			}
		]
	},
	null,
	2
)}
```

With:

```svelte
{@const exampleData = JSON.stringify(
	{
		collection: {
			source_id: 'my-collection-123',
			name: 'My Collection',
			data_type: 'parsed_text',
			access_control: null,
			metadata: { department: 'engineering' },
			tags: ['project-x']
		},
		documents: [
			{
				source_id: 'doc-1',
				filename: 'example.txt',
				content_type: 'text/plain',
				text: 'Document content here...',
				title: 'Example Document',
				metadata: { version: '1.2', status: 'approved' },
				tags: ['documentation'],
				author: 'Jane Doe'
			}
		]
	},
	null,
	2
)}
```

#### 2. Add OpenAPI download link below curl example

**File**: `src/lib/components/admin/Settings/IntegrationProviders.svelte`
**Changes**: Add a download link after the access_control annotation (after line 313, before the closing `</div>`)

```svelte
<div class="text-gray-400 mt-1 text-[10px]">
	access_control: null = public, {'{}'} = private, {'{"read": {"group_ids": [...]}}'} = custom
</div>
<div class="mt-3 pt-2 border-t border-gray-200 dark:border-gray-700">
	<a
		href="{window.location.origin}/api/v1/integrations/openapi.json"
		download="integration-openapi.json"
		class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 underline"
	>
		{$i18n.t('Download OpenAPI Specification')}
	</a>
</div>
```

Note: The `download` attribute triggers a file download. The endpoint requires auth, so this `<a>` link won't work directly since it doesn't send the auth header. We need to use a click handler instead:

```svelte
<div class="mt-3 pt-2 border-t border-gray-200 dark:border-gray-700">
	<button
		class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 underline"
		type="button"
		on:click={async () => {
			try {
				const res = await fetch(`${window.location.origin}/api/v1/integrations/openapi.json`, {
					headers: { Authorization: `Bearer ${localStorage.token}` }
				});
				if (!res.ok) throw new Error('Failed to fetch');
				const blob = await res.blob();
				const url = URL.createObjectURL(blob);
				const a = document.createElement('a');
				a.href = url;
				a.download = 'integration-openapi.json';
				a.click();
				URL.revokeObjectURL(url);
			} catch (err) {
				toast.error('Failed to download OpenAPI spec');
			}
		}}
	>
		{$i18n.t('Download OpenAPI Specification')}
	</button>
</div>
```

#### 3. Add i18n keys

**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add key (only if not already present):

```json
"Download OpenAPI Specification": ""
```

### Success Criteria

#### Automated Verification:

- [ ] Frontend builds: `npm run build`
- [ ] Lint passes: `npm run lint:frontend`

#### Manual Verification:

- [ ] Open admin panel → Integrations → click "API" on a provider
- [ ] Curl example now shows `metadata`, `content_type`, `tags`, `author` fields
- [ ] "Download OpenAPI Specification" link appears below the curl example
- [ ] Clicking the link downloads a JSON file named `integration-openapi.json`
- [ ] The downloaded JSON contains only integration endpoint paths, not the full API

---

## Testing Strategy

### Manual Testing Steps

1. **Create a provider** with custom metadata fields: `filetype` (required), `department` (optional)
2. **Push without filetype** → expect 400 with message about missing `filetype`
3. **Push with all fields** including `metadata: { "filetype": "pdf", "department": "legal" }` → expect 200
4. **Verify vector DB** contains `content_type`, `meta_filetype`, `meta_department` on chunks
5. **Check admin panel** curl example shows new fields
6. **Download OpenAPI spec** and verify it's valid and scoped

### Edge Cases

- Empty `metadata: {}` with no required custom fields → should pass
- `metadata` with extra keys not in `custom_metadata_fields` → should pass (custom fields define required minimum, not schema)
- Very long metadata values → `process_metadata()` handles them fine
- `metadata` values that are dicts/lists → `process_metadata()` converts to strings

## References

- Research: `thoughts/shared/research/2026-03-26-push-integration-metadata-and-openapi.md`
- Integration router: `backend/open_webui/routers/integrations.py`
- Admin UI: `src/lib/components/admin/Settings/IntegrationProviders.svelte`
- Vector metadata utils: `backend/open_webui/retrieval/vector/utils.py`
