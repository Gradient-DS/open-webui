---
date: 2026-03-26T14:00:00+02:00
researcher: Claude
git_commit: f55edd086eaa7d33329980f15d939154a06d8d21
branch: feat/external-base-agents
repository: open-webui
topic: 'Push integration metadata support and OpenAPI spec availability'
tags: [research, integrations, metadata, openapi, push-ingest, admin-panel]
status: complete
last_updated: 2026-03-26
last_updated_by: Claude
---

# Research: Push Integration Metadata Support and OpenAPI Spec Availability

**Date**: 2026-03-26T14:00:00+02:00
**Researcher**: Claude
**Git Commit**: f55edd086eaa7d33329980f15d939154a06d8d21
**Branch**: feat/external-base-agents
**Repository**: open-webui

## Research Question

Does the push integration already support metadata (filetype, custom key-value pairs)? Is this shown in the curl example? Can we expose the OpenAPI spec as a downloadable link in the admin panel?

## Summary

**Metadata is already fully supported** at both collection and document level via arbitrary `metadata: dict` fields and specific typed fields (`content_type`, `tags`, `language`, `author`, `modified_at`). However:

1. **The curl example does NOT show any metadata fields** — it only demonstrates the minimum required fields.
2. **Metadata is stored in the DB** (`file.meta.provider_metadata`) but **NOT propagated to vector store metadata** — so it cannot currently be used for RAG query filtering.
3. **OpenAPI spec** is available at `/openapi.json` in dev mode but disabled in production. No download link exists in the admin UI.
4. **`custom_metadata_fields`** exists as a provider config option in the admin UI (defining expected key/label/required), but is purely informational — it's not validated or enforced server-side.

## Detailed Findings

### 1. Existing Metadata Support in the API Schema

#### Collection-level metadata (`IngestCollection`, `integrations.py:29-38`)

| Field      | Type            | Default | Notes                         |
| ---------- | --------------- | ------- | ----------------------------- |
| `metadata` | `dict`          | `{}`    | **Arbitrary key-value pairs** |
| `tags`     | `list[str]`     | `[]`    | Tag list                      |
| `language` | `Optional[str]` | `None`  | Language code                 |

#### Document-level metadata (`IngestDocumentBase`, `integrations.py:41-51`)

| Field          | Type            | Default        | Notes                         |
| -------------- | --------------- | -------------- | ----------------------------- |
| `metadata`     | `dict`          | `{}`           | **Arbitrary key-value pairs** |
| `content_type` | `str`           | `"text/plain"` | MIME type (filetype)          |
| `tags`         | `list[str]`     | `[]`           | Tag list                      |
| `language`     | `Optional[str]` | `None`         | Language code                 |
| `author`       | `Optional[str]` | `None`         | Author name                   |
| `modified_at`  | `Optional[str]` | `None`         | Last modified timestamp       |
| `source_url`   | `Optional[str]` | `None`         | Source URL                    |

### 2. Where Metadata is Stored vs. Lost

**Stored in file record** (`integrations.py:145-155`):

```python
meta = {
    "name": doc.title or doc.filename,
    "content_type": doc.content_type,
    "source": provider,
    "source_id": doc.source_id,
    "source_url": doc.source_url,
    "language": doc.language,
    "author": doc.author,
    "tags": doc.tags,
    "provider_metadata": doc.metadata,  # arbitrary dict preserved here
}
```

**Stored in knowledge base** (`integrations.py:121-131`):

```python
meta = {
    "integration": {
        "provider": provider,
        "source_id": collection.source_id,
        "data_type": collection.data_type,
        "language": collection.language,
        "tags": collection.tags,
        "provider_metadata": collection.metadata,  # arbitrary dict preserved here
    }
}
```

**NOT stored in vector metadata** (`integrations.py:225-237`):

```python
# Only these fields make it into vector store chunks:
{
    "name": doc.title or doc.filename,
    "source": doc.source_url or doc.filename,
    "file_id": file_id,
    "created_by": user_id,
    "author": doc.author,
    "language": doc.language,
    "source_provider": provider,
}
```

The `doc.metadata` dict, `doc.tags`, `doc.content_type`, and `doc.modified_at` are **not propagated** to vector chunks. This means they cannot be used for filtering during RAG queries.

### 3. Curl Example (Current State)

Location: `src/lib/components/admin/Settings/IntegrationProviders.svelte:298-316`

Current example only shows:

```json
{
	"collection": {
		"source_id": "my-collection-123",
		"name": "My Collection",
		"data_type": "parsed_text",
		"access_control": null
	},
	"documents": [
		{
			"source_id": "doc-1",
			"filename": "example.txt",
			"text": "Document content here...",
			"title": "Example Document"
		}
	]
}
```

Missing from example: `metadata`, `content_type`, `tags`, `language`, `author`, `modified_at`, `source_url`, collection-level `metadata`/`tags`/`language`.

### 4. OpenAPI Spec Availability

**FastAPI configuration** (`main.py:880-886`):

```python
app = FastAPI(
    docs_url="/docs" if ENV == "dev" else None,
    openapi_url="/openapi.json" if ENV == "dev" else None,
)
```

- In dev mode: available at `/openapi.json` and `/docs` (Swagger UI)
- In production: both disabled
- Custom Swagger UI assets served from `/static/swagger-ui/` (`main.py:2972-2982`)

### 5. `custom_metadata_fields` in Admin UI

The provider config supports defining custom metadata field schemas (`IntegrationProviders.svelte:31`):

```typescript
custom_metadata_fields: [] as { key: string; label: string; required: boolean }[];
```

This is purely a UI config — the backend does not validate that pushed documents include the required custom fields. It's stored in `INTEGRATION_PROVIDERS` config but not referenced in `integrations.py`.

## Code References

- `backend/open_webui/routers/integrations.py:29-68` — Schema definitions (IngestCollection, IngestDocumentBase, document types)
- `backend/open_webui/routers/integrations.py:121-131` — Collection metadata storage
- `backend/open_webui/routers/integrations.py:145-155` — Document metadata storage in file record
- `backend/open_webui/routers/integrations.py:225-237` — Vector metadata builder (metadata NOT included)
- `backend/open_webui/main.py:880-886` — OpenAPI/docs endpoint config
- `src/lib/components/admin/Settings/IntegrationProviders.svelte:298-316` — Curl example display
- `src/lib/components/admin/Settings/IntegrationProviders.svelte:407-465` — Custom metadata fields UI (edit mode)

## Recommendations

### Curl Example Enhancement

Update the example at `IntegrationProviders.svelte:299` to include metadata fields:

```json
{
	"collection": {
		"source_id": "my-collection-123",
		"name": "My Collection",
		"data_type": "parsed_text",
		"access_control": null,
		"metadata": { "department": "engineering" },
		"tags": ["project-x"]
	},
	"documents": [
		{
			"source_id": "doc-1",
			"filename": "example.txt",
			"content_type": "text/plain",
			"text": "Document content here...",
			"title": "Example Document",
			"metadata": { "version": "1.2", "status": "approved" },
			"tags": ["documentation"],
			"author": "Jane Doe"
		}
	]
}
```

### OpenAPI Spec Download

Two options:

1. **Always-on endpoint**: Change `openapi_url` to always serve `/openapi.json` (remove `ENV == "dev"` guard), then add a download link in the admin panel.
2. **Scoped endpoint**: Create a dedicated admin-only route (e.g., `/api/v1/integrations/openapi.json`) that returns only the integration-related portion of the spec. This avoids exposing the full API surface.

### Vector Metadata Gap

If filtering by custom metadata during RAG queries is desired, `_build_base_metadata()` needs to be extended to include `doc.metadata` (or a subset of it) in the vector store chunks.

## Open Questions

1. Should `custom_metadata_fields` be validated server-side (reject pushes missing required fields)?
2. Should arbitrary metadata be propagated to vector store for RAG filtering, or is DB-only storage sufficient?
3. Should the OpenAPI download be the full spec or scoped to integration endpoints only?
4. Should OpenAPI always be available or gated behind admin authentication?
