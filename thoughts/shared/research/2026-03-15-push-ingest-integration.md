---
date: 2026-03-15T15:00:00+01:00
researcher: Claude
git_commit: db8b3acca67356114eb0e7c46709efedd6e4387f
branch: dev
repository: open-webui
topic: 'Push/Ingest Integration: Extensible multi-provider push API for external data ingestion'
tags:
  [
    research,
    codebase,
    push-integration,
    ingest,
    octobox,
    neo,
    knowledge-base,
    rag,
    api,
    provider-registry
  ]
status: complete
last_updated: 2026-03-15
last_updated_by: Claude
last_updated_note: 'Redesigned with extensible provider registry for multi-client push integrations'
---

# Research: Push/Ingest Integration for External Data Sources

**Date**: 2026-03-15T15:00:00+01:00
**Researcher**: Claude
**Git Commit**: db8b3acca67356114eb0e7c46709efedd6e4387f
**Branch**: dev
**Repository**: open-webui

## Research Question

How can we build an extensible push/ingest integration that supports multiple external clients (Octobox, Neo, future partners) with provider-specific metadata, nice origin display in the UI, and a single unified API endpoint?

## Summary

Instead of a single `"external"` type, we introduce an **Integration Provider Registry** — a lightweight data-driven pattern where each push client (Octobox, Neo, etc.) is a registered provider with its own `slug`, display name, icon, badge color, and metadata schema. The knowledge base `type` becomes the provider slug (e.g., `"octobox"`, `"neo"`). The API endpoint is shared (`POST /api/v1/integrations/ingest`), with the provider determined by the authenticated service account.

This approach:

- Requires **zero code changes** to add a new provider (just a config entry + service account)
- Shows provider-specific branding in the UI (badge, icon, label)
- Stores provider-specific metadata flexibly in the existing `meta` JSON column
- Keeps citation `source_url` links working naturally for each provider

## Detailed Design

### Integration Provider Registry

A simple dictionary mapping provider slugs to their display configuration. Stored in config, not a database table — it changes rarely and doesn't need CRUD.

```python
# backend/open_webui/config.py (or a dedicated integrations config)

INTEGRATION_PROVIDERS: dict[str, dict] = {
    "octobox": {
        "name": "Octobox",
        "description": "Document pipeline integration",
        "badge_type": "info",        # Maps to Badge component: info=blue, success=green, warning=yellow
        "icon": "puzzle-piece",      # Icon identifier for frontend
        "max_files_per_kb": 500,     # Override default 250 if needed
        "max_documents_per_request": 50,
    },
    "neo": {
        "name": "Neo",
        "description": "Legal knowledge integration",
        "badge_type": "success",     # Green badge
        "icon": "scale",             # Legal/balance icon
        "max_files_per_kb": 1000,
        "max_documents_per_request": 100,
    },
    # Adding a new provider = adding an entry here. No code changes.
}
```

#### Why a config dict, not a DB table

- Providers are configured by us (platform operators), not by end users
- Adding a provider also requires creating a service account + API key — already a manual step
- No CRUD UI needed
- Can be overridden via environment variable (JSON string) for deployment flexibility
- If we later need dynamic provider registration, migrating to a DB table is trivial

### Knowledge Base Type = Provider Slug

Instead of a generic `"external"` type, the knowledge base `type` is the provider slug itself:

```
knowledge.type = "octobox"   # Not "external"
knowledge.type = "neo"       # Each provider gets its own type
knowledge.type = "local"     # Existing
knowledge.type = "onedrive"  # Existing
```

**Why this is better than a single `"external"` type:**

- UI can show provider-specific badge/icon without parsing metadata
- `get_knowledge_bases_by_type("octobox")` returns only Octobox KBs — useful for provider-scoped operations
- Filtering/grouping by provider in the KB list is a simple `type` check
- The type column already exists, no migration needed
- Non-local behaviors (private access_control, file limits, orphan cleanup) apply to all non-local/non-onedrive types automatically if we change the validation to an allowlist

#### Type validation change

**File**: `backend/open_webui/routers/knowledge.py:189`

Current:

```python
if form_data.type and form_data.type not in ("local", "onedrive"):
    raise HTTPException(400, "Invalid knowledge base type")
```

Change to:

```python
ALLOWED_KB_TYPES = {"local", "onedrive"} | set(INTEGRATION_PROVIDERS.keys())
if form_data.type and form_data.type not in ALLOWED_KB_TYPES:
    raise HTTPException(400, "Invalid knowledge base type")
```

#### Non-local behavior generalization

Currently, the code checks `knowledge.type != "local"` or `knowledge.type == "onedrive"` in various places. For push providers, we need to generalize:

| Current check                                                        | Generalized check                                                                    |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `type == "onedrive"` (for OneDrive-specific sync UI)                 | Keep as-is — OneDrive has unique sync behavior                                       |
| `type != "local"` (for 250-file limit, private access_control, etc.) | Keep as-is — all push providers are non-local                                        |
| `type in INTEGRATION_PROVIDERS`                                      | New check for push-provider-specific behavior (e.g., showing "Ingested via X" badge) |

### Service Account ↔ Provider Binding

Each push provider gets a dedicated service account. The binding between a service account and its provider is stored in the user's metadata (or a simple lookup):

```python
# Option A: Store provider slug in user metadata (simplest)
# The service account user has meta.integration_provider = "octobox"

# Option B: Lookup table in config
INTEGRATION_SERVICE_ACCOUNTS: dict[str, str] = {
    "user_id_of_octobox_service_account": "octobox",
    "user_id_of_neo_service_account": "neo",
}
```

**Recommendation**: Option A. When creating the service account, set `meta.integration_provider = "octobox"`. The ingest endpoint reads this to determine the provider. This means the provider is implicit from the API key — Octobox doesn't need to specify `"provider": "octobox"` in every request.

### Metadata Storage Pattern

#### Knowledge base level (`knowledge.meta`)

```json
{
  "integration": {
    "provider": "octobox",
    "source_id": "octobox-collection-456",
    "language": "nl",
    "tags": ["gemeente-amsterdam", "privacy"],
    "provider_metadata": {
      // Free-form, provider-specific. Octobox might store:
      "pipeline_version": "2.1",
      "organization": "Gemeente Amsterdam"
      // Neo might store:
      "jurisdiction": "Netherlands",
      "legal_domain": "privacy"
    }
  }
}
```

The `integration` key is a convention. The `provider` field is redundant with `knowledge.type` but useful for meta-level queries. The `provider_metadata` sub-object holds anything provider-specific without polluting the top level.

#### File level (`file.meta`)

```json
{
	"name": "Privacybeleid 2026",
	"content_type": "application/pdf",
	"source": "octobox",
	"source_id": "octobox-doc-12345",
	"source_url": "https://docs.example.com/privacybeleid-2026",
	"language": "nl",
	"author": "Juridische Zaken",
	"tags": ["beleid", "privacy"],
	"provider_metadata": {
		// Anything Octobox-specific that doesn't fit standard fields
	}
}
```

#### Vector chunk metadata (stored in Weaviate)

The metadata attached to each chunk in the vector DB determines what shows up in citations:

```python
document = Document(
    page_content=doc.text,
    metadata={
        # Standard fields (used by citation rendering)
        "name": doc.title or doc.filename,        # → CitationModal title
        "source": doc.source_url or doc.filename,  # → citation grouping ID + link
        "file_id": file_id,                        # → file preview/download link
        "created_by": user_id,                     # → attribution
        # Provider-specific fields (stored but not used by core UI)
        "author": doc.author,
        "language": doc.language,
        "source_provider": provider_slug,           # → enables provider-aware rendering later
    },
)
```

**Key insight from the metadata flow research**: The `source` metadata field is critical — it serves as both the citation grouping key AND the clickable link in the citation modal. If we set `source` to `doc.source_url` (e.g., `https://docs.example.com/privacybeleid-2026`), the citation will automatically:

1. Group all chunks from the same URL
2. Show the URL as a clickable link (because it starts with `http`)
3. Display Google favicon for the domain

This means **Octobox `source_url` flows naturally to citations with zero frontend changes**.

### Frontend UI Design

#### KB List Page Badge

**File**: `src/lib/components/workspace/Knowledge.svelte:293-302`

Current logic:

```svelte
{#if item.type === 'onedrive'}
	<Badge type="info" content="OneDrive" />
{:else}
	<Badge type="muted" content="Local" />
{/if}
```

New logic using the provider registry:

```svelte
{#if item.type === 'onedrive'}
	<Badge type="info" content="OneDrive" />
{:else if integrationProviders[item.type]}
	<Badge
		type={integrationProviders[item.type].badge_type}
		content={integrationProviders[item.type].name}
	/>
{:else}
	<Badge type="muted" content="Local" />
{/if}
```

The `integrationProviders` object comes from the backend config, exposed via the existing `/api/v1/config` endpoint (which already serves feature flags). Add:

```python
# In the config response
"integration_providers": {
    slug: {"name": p["name"], "badge_type": p["badge_type"], "icon": p["icon"]}
    for slug, p in INTEGRATION_PROVIDERS.items()
}
```

#### KB Detail Page

For push-provider KBs:

- **Badge**: Provider name with provider-specific color
- **No "Add files" button** — files come via API only
- **File list**: Flat `Files.svelte` (no source-grouped tree — that's OneDrive-specific)
- **Empty state**: "Documents are ingested via the {Provider Name} API" with provider icon
- **File count**: `N / {max_files}` using the provider's `max_files_per_kb` config
- **Access control**: Same as OneDrive — locked to private, shows "Private" label

#### Citation Display

**No changes needed** for basic citations. The `source_url` field flows through to `metadata.source`, which the citation UI already renders as a clickable link with favicon.

For enhanced display, we could later add provider-aware rendering:

```svelte
<!-- Future enhancement: show provider icon next to citation -->
{#if citation.metadata?.source_provider && integrationProviders[citation.metadata.source_provider]}
	<ProviderIcon provider={citation.metadata.source_provider} />
{/if}
```

### API Design

#### Single unified endpoint

```
POST /api/v1/integrations/ingest
Authorization: Bearer sk-xxxxx
```

The provider is determined by the authenticated service account, NOT by a field in the request body. This means:

- Octobox can't accidentally write to Neo's collections
- No need for the client to specify their provider identity
- Provider scoping is enforced at the auth level

#### Request body (unchanged from Octobox spec)

```json
{
	"collection": {
		"source_id": "octobox-collection-456",
		"name": "Privacybeleid Gemeente Amsterdam",
		"description": "Alle beleidsdocumenten rondom privacy en AVG",
		"language": "nl",
		"tags": ["gemeente-amsterdam", "privacy", "avg"],
		"metadata": {}
	},
	"documents": [
		{
			"source_id": "octobox-doc-12345",
			"filename": "privacybeleid-2026.pdf",
			"content_type": "application/pdf",
			"text": "De volledige geparsede tekst...",
			"title": "Privacybeleid 2026",
			"source_url": "https://docs.example.com/privacybeleid-2026",
			"language": "nl",
			"author": "Juridische Zaken",
			"modified_at": "2026-02-15T10:30:00Z",
			"tags": ["beleid", "privacy"],
			"metadata": {}
		}
	]
}
```

The `metadata` fields at both collection and document level are free-form dicts — each provider puts whatever they need there. We don't enforce a schema per provider; we just store it.

#### Response

```json
{
	"knowledge_id": "uuid-of-kb",
	"collection_source_id": "octobox-collection-456",
	"provider": "octobox",
	"total": 1,
	"created": 1,
	"updated": 0,
	"skipped": 0,
	"errors": 0,
	"documents": [
		{
			"source_id": "octobox-doc-12345",
			"status": "created",
			"file_id": "octobox-octobox-doc-12345"
		}
	]
}
```

### File ID Convention

Deterministic file IDs per provider:

```
{provider_slug}-{document_source_id}
```

Examples:

- `octobox-octobox-doc-12345`
- `neo-wet-avg-2026`

This follows the OneDrive pattern (`onedrive-{item_id}`) and ensures:

- No collisions between providers
- Upsert/dedup via ID lookup
- Clear provenance from the ID alone

### KB Lookup by source_id

Collection `source_id` is scoped to `(provider, source_id)`:

```python
def _find_kb_by_source_id(provider: str, source_id: str) -> Optional[KnowledgeModel]:
    """Find a knowledge base by provider + external source_id."""
    kbs = Knowledges.get_knowledge_bases_by_type(provider)  # type = provider slug
    for kb in kbs:
        meta = kb.meta or {}
        if meta.get("integration", {}).get("source_id") == source_id:
            return kb
    return None
```

At low scale (< 100 KBs per provider) this is fine. For higher scale, add a `source_id` column or JSON index.

### Endpoint Implementation

```python
# backend/open_webui/routers/integrations.py

from fastapi import APIRouter, Depends, HTTPException, Request
from open_webui.utils.auth import get_verified_user
from open_webui.config import INTEGRATION_PROVIDERS

router = APIRouter()

def get_integration_provider(user) -> str:
    """Resolve the integration provider from the authenticated service account."""
    provider = (user.info or {}).get("integration_provider")
    if not provider or provider not in INTEGRATION_PROVIDERS:
        raise HTTPException(
            status_code=403,
            detail="This account is not configured as an integration service account"
        )
    return provider

@router.post("/ingest")
async def ingest_documents(
    request: Request,
    form_data: IngestForm,
    user=Depends(get_verified_user),
):
    provider = get_integration_provider(user)
    provider_config = INTEGRATION_PROVIDERS[provider]

    # Validate batch size
    if len(form_data.documents) > provider_config.get("max_documents_per_request", 50):
        raise HTTPException(400, "Too many documents in single request")

    # Find or create KB
    knowledge = _find_kb_by_source_id(provider, form_data.collection.source_id)
    if not knowledge:
        knowledge = _create_kb_for_provider(provider, form_data.collection, user.id)

    # Check file limit
    current_count = Knowledges.count_files_for_knowledge(knowledge.id)
    max_files = provider_config.get("max_files_per_kb", 250)
    if current_count + len(form_data.documents) > max_files:
        raise HTTPException(400, f"Would exceed {max_files} file limit")

    # Process documents
    results = []
    for doc in form_data.documents:
        result = _process_ingest_document(
            request=request,
            knowledge_id=knowledge.id,
            provider=provider,
            doc=doc,
            user_id=user.id,
        )
        results.append(result)

    return IngestResponse(...)
```

### Deletion Endpoints

```
DELETE /api/v1/integrations/collections/{source_id}
DELETE /api/v1/integrations/collections/{source_id}/documents/{document_source_id}
```

Both scoped to the authenticated provider. Collection delete soft-deletes the KB. Document delete removes the file from the KB collection + KnowledgeFile link, with orphan cleanup.

### Adding a New Provider (Checklist)

To onboard a new push integration client:

1. **Add to `INTEGRATION_PROVIDERS` config** — name, badge_type, icon, limits
2. **Create a service account** — user with role `user`, `meta.integration_provider = "{slug}"`
3. **Generate API key** — `POST /api/v1/auths/api_key` for the service account
4. **Share API key with client** — they use `Authorization: Bearer sk-xxxxx`
5. **Done** — no code deployment needed

### Existing Building Blocks (Reference)

| Component                  | Location                                            | How it fits                                 |
| -------------------------- | --------------------------------------------------- | ------------------------------------------- |
| `save_docs_to_vector_db()` | `routers/retrieval.py:1352`                         | Core: chunks, embeds, stores                |
| Knowledge type system      | `models/knowledge.py:47`                            | `type` = provider slug                      |
| File model                 | `models/files.py:16-31`                             | Deterministic IDs: `{provider}-{source_id}` |
| KnowledgeFile join         | `models/knowledge.py:94-112`                        | Links files to KBs                          |
| Non-local KB behaviors     | `routers/knowledge.py`                              | Private access, file limits, orphan cleanup |
| API key auth               | `utils/auth.py:269-364`                             | `sk-` tokens → user → provider              |
| Badge component            | `src/lib/components/common/Badge.svelte`            | info/success/warning/error/muted variants   |
| Citation rendering         | `src/lib/components/chat/Messages/Citations.svelte` | `metadata.source` → link + favicon          |
| Config endpoint            | Backend serves config to frontend                   | Expose `integration_providers` for UI       |

## Code References

- `backend/open_webui/routers/retrieval.py:1352-1551` — `save_docs_to_vector_db()` core function
- `backend/open_webui/routers/retrieval.py:1735-1746` — Document metadata construction pattern
- `backend/open_webui/routers/knowledge.py:189` — Type validation (extend with provider slugs)
- `backend/open_webui/routers/knowledge.py:445-519` — Add file to KB pattern
- `backend/open_webui/models/knowledge.py:47` — Knowledge type column
- `backend/open_webui/models/knowledge.py:353-363` — `get_knowledge_bases_by_type()`
- `backend/open_webui/models/files.py:16-31` — File model
- `backend/open_webui/utils/auth.py:269-364` — `get_current_user` auth dependency
- `backend/open_webui/main.py:1548-1597` — Router registration
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:98-137` — Weaviate schema (properties stored per chunk)
- `backend/open_webui/retrieval/vector/utils.py:6-28` — Metadata filtering before vector storage
- `backend/open_webui/utils/middleware.py:1599-1618` — Citation context string injection
- `src/lib/components/workspace/Knowledge.svelte:293-302` — KB type badge rendering
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1441-1444` — KB detail badge
- `src/lib/components/workspace/Knowledge/KnowledgeBase/EmptyStateCards.svelte:23-59` — Type-specific empty states
- `src/lib/components/chat/Messages/Citations.svelte:86-125` — Citation grouping (uses `metadata.source`)
- `src/lib/components/chat/Messages/Citations/CitationModal.svelte:106-143` — Citation modal (source URL → clickable link)

## Historical Context

- `thoughts/shared/research/2026-03-06-external-data-pipeline-ingestion.md` — Foundation research. Recommended push API approach.
- `thoughts/shared/research/2026-03-06-octobox-integratie-email.md` — Draft email to Octobox with agreed API schema.
- `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md` — Confirms custom implementation preferred over airweave.

## Open Questions

1. **Provider config storage**: Config dict vs environment variable vs admin UI? Start with config dict, consider admin UI later if non-technical staff need to manage providers.

2. **Provider-scoped access control**: Should a provider's KBs be automatically visible to a configurable set of users/groups? E.g., "all Octobox KBs are visible to the `gemeente-amsterdam` group". This could be a `default_access_control` field in the provider config.

3. **Webhook callbacks**: Should we notify the external system when ingestion completes/fails? For sync processing this is redundant (the HTTP response tells them). For future async processing, a callback URL in the provider config would be useful.

4. **Frontend filtering by provider**: The KB list currently filters by `All / Created by you / Shared with you`. Should we add provider-based filtering? E.g., a dropdown showing "All", "Local", "OneDrive", "Octobox", "Neo". Easy to implement since `type` = provider slug.

5. **`split=True` vs pre-chunked**: Octobox sends full document text (we chunk). If a future provider sends pre-chunked data, add a `pre_chunked: bool` field to the document model and pass `split=not pre_chunked` to `save_docs_to_vector_db()`.
