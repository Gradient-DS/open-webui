# Push/Ingest Integration: Extensible Multi-Provider Push API

## Overview

Build an extensible push/ingest API that allows external systems (Octobox, Neo, future partners) to push pre-processed documents into Open WebUI knowledge bases. Uses a provider registry pattern where each push client is a registered provider with its own slug, display config, and limits. The API endpoint is shared (`POST /api/v1/integrations/ingest`), with the provider determined by the authenticated service account.

## Current State Analysis

- **Knowledge bases** have a `type` column (`local`, `onedrive`) — we extend this with provider slugs (`octobox`, `neo`, etc.)
- **File IDs** are caller-supplied (`FileForm.id`) — deterministic `{provider}-{source_id}` IDs work naturally
- **API key auth** is fully built (`sk-` tokens, one per user, stored in `api_key` table) — we reuse this for service accounts
- **`save_docs_to_vector_db()`** handles chunking, embedding, vector storage with hash-based dedup
- **Admin settings** use a tab system with feature gating — we add an "Integrations" tab
- **KB list** has server-side filtering with `ViewSelector` dropdown — we add provider-based type filtering

### Key Discoveries:

- `ApiKey` model has unused `data` JSON field (models/users.py:124) — not needed, we use `user.info`
- `save_docs_to_vector_db()` has built-in hash dedup (routers/retrieval.py:1382-1396) — idempotent re-push
- `process_file()` expects files in storage — for push ingest we bypass it and call `save_docs_to_vector_db()` directly
- File `data` and `meta` updates use merge semantics (models/files.py:227,230) — safe for incremental updates
- Backend search already accepts a `type` query param (models/knowledge.py:244-246) — filtering infrastructure exists
- `ViewSelector` is hardcoded to 3 options — we'll add a separate `TypeSelector` dropdown
- Settings tab addition requires changes in 3 files: `features.ts`, `Settings.svelte`, and the new component

## Desired End State

1. **Admin can manage integration providers** via Admin > Settings > Integrations — add/edit/remove providers with name, slug, badge color, data type description, and file limits
2. **Admin can bind a user account as a provider's service account** — that user's existing API key authenticates ingest requests
3. **External systems can push documents** via `POST /api/v1/integrations/ingest` using their API key — documents are chunked, embedded, and stored in a provider-scoped knowledge base
4. **External systems can delete their own collections/documents** via scoped DELETE endpoints
5. **KB list shows provider badges** (e.g., blue "Octobox", green "Neo") and supports filtering by provider type
6. **KB detail pages** for push-provider KBs show appropriate UI (no "Add files" button, provider-specific empty state, file count with provider limits)
7. **Citations work automatically** — `source_url` flows to `metadata.source`, rendering as clickable links with favicons

### Verification:

- Admin can create a provider "Octobox" with slug `octobox` in the Integrations settings tab
- Admin can assign a user as the Octobox service account
- `POST /api/v1/integrations/ingest` with that user's API key creates a KB with `type=octobox` and ingests documents
- KB list shows "Octobox" badge with correct color
- Pushing same documents again is idempotent (hash dedup)
- `DELETE /api/v1/integrations/collections/{source_id}` only works for the authenticated provider's collections
- Adding a new provider requires zero code changes (just admin UI config)

## What We're NOT Doing

- **Async/queue-based processing** — all ingest is synchronous (HTTP response confirms completion)
- **Pre-chunked document support** — all documents are chunked by our pipeline (can add `pre_chunked` flag later)
- **Webhook callbacks** — not needed for sync processing
- **Provider-scoped access control** (auto-sharing with user groups) — deferred to future phase
- **Admin UI for creating user accounts** — admin uses existing user creation flow, then binds via Integrations tab
- **Rate limiting per provider** — rely on general API rate limiting for now
- **Provider-specific icons in citations** — citations use standard favicon rendering from `source_url`

## Implementation Approach

The implementation follows the existing patterns closely: PersistentConfig for provider registry, a new router for ingest/delete endpoints, and frontend changes mirroring the OneDrive type handling pattern. We bypass `process_file()` since push documents arrive as text (no file storage needed), going directly to `save_docs_to_vector_db()`.

---

## Phase 1: Backend — Provider Registry & Config

### Overview

Define the integration provider data model and expose it via admin config endpoints. Store providers in the existing PersistentConfig system (DB-backed, admin-editable at runtime).

### Changes Required:

#### 1. Provider Config Definition

**File**: `backend/open_webui/config.py`
**Changes**: Add `INTEGRATION_PROVIDERS` PersistentConfig near the end of the file (after other PersistentConfig definitions ~line 3085)

```python
INTEGRATION_PROVIDERS = PersistentConfig(
    "INTEGRATION_PROVIDERS",
    "integrations.providers",
    os.environ.get("INTEGRATION_PROVIDERS", "{}"),
)
```

The value is a JSON dict keyed by provider slug:

```json
{
	"octobox": {
		"name": "Octobox",
		"description": "Document pipeline integration",
		"badge_type": "info",
		"data_type": "documents",
		"data_type_description": "Full documents (PDF, DOCX, etc.) with extracted text. Documents are chunked and embedded by Open WebUI.",
		"max_files_per_kb": 500,
		"max_documents_per_request": 50,
		"service_account_id": "user-uuid-here"
	}
}
```

#### 2. Register Config in App State

**File**: `backend/open_webui/main.py`
**Changes**: Add to the config initialization block (~line 1060-1113)

```python
app.state.config.INTEGRATION_PROVIDERS = INTEGRATION_PROVIDERS
```

#### 3. Admin Config Endpoints

**File**: `backend/open_webui/routers/configs.py`
**Changes**: Add GET/POST endpoints for integration providers

```python
@router.get("/integrations")
async def get_integrations_config(request: Request, user=Depends(get_admin_user)):
    return {
        "providers": request.app.state.config.INTEGRATION_PROVIDERS,
    }

@router.post("/integrations")
async def set_integrations_config(
    request: Request,
    form_data: IntegrationsConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.INTEGRATION_PROVIDERS = form_data.providers
    # Update service account user.info for each provider
    for slug, provider in form_data.providers.items():
        if provider.get("service_account_id"):
            _bind_service_account(provider["service_account_id"], slug)
    return {"providers": request.app.state.config.INTEGRATION_PROVIDERS}
```

The `_bind_service_account` helper sets `user.info.integration_provider = slug` on the service account user record. When a provider is removed or its service account changes, the old user's `info.integration_provider` is cleared.

```python
class IntegrationsConfigForm(BaseModel):
    providers: dict
```

#### 4. Expose Provider Registry to Frontend

**File**: `backend/open_webui/main.py`
**Changes**: Add `integration_providers` to the `/api/config` response (around line 2094 where `features` are returned)

```python
"integration_providers": {
    slug: {"name": p["name"], "badge_type": p["badge_type"]}
    for slug, p in request.app.state.config.INTEGRATION_PROVIDERS.items()
}
```

This gives the frontend the display info it needs without exposing internal config (service account IDs, limits).

#### 5. Extend KB Type Validation

**File**: `backend/open_webui/routers/knowledge.py:189`
**Changes**: Accept provider slugs as valid KB types

```python
# Current:
if form_data.type and form_data.type not in ("local", "onedrive"):
    raise HTTPException(400, "Invalid knowledge base type")

# New:
from open_webui.config import INTEGRATION_PROVIDERS
ALLOWED_KB_TYPES = {"local", "onedrive"} | set(INTEGRATION_PROVIDERS.value.keys())
if form_data.type and form_data.type not in ALLOWED_KB_TYPES:
    raise HTTPException(400, "Invalid knowledge base type")
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts without errors: `open-webui dev`
- [x] `GET /api/v1/configs/integrations` returns `{"providers": {}}` for admin user
- [x] `POST /api/v1/configs/integrations` saves and returns provider config
- [x] `GET /api/config` includes `integration_providers` key
- [x] Creating a KB with `type="octobox"` succeeds (after registering the provider)
- [x] Creating a KB with `type="invalid"` returns 400

#### Manual Verification:

- [ ] Provider config persists across server restarts (PersistentConfig in DB)
- [ ] Service account binding updates `user.info.integration_provider` correctly

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Backend — Ingest & Delete Endpoints

### Overview

Create the core `POST /ingest` endpoint that accepts documents from external systems, creates/finds the knowledge base, creates file records, and stores embeddings in the vector DB. Add scoped delete endpoints for collections and individual documents.

### Changes Required:

#### 1. New Integrations Router

**File**: `backend/open_webui/routers/integrations.py` (new file)

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from open_webui.utils.auth import get_verified_user
from open_webui.config import INTEGRATION_PROVIDERS
from open_webui.models.knowledge import Knowledges, KnowledgeForm
from open_webui.models.files import Files, FileForm
from open_webui.routers.retrieval import save_docs_to_vector_db
from langchain_core.documents import Document
import hashlib
import time

router = APIRouter()


# --- Pydantic Models ---

class IngestCollection(BaseModel):
    source_id: str
    name: str
    description: str = ""
    language: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}

class IngestDocument(BaseModel):
    source_id: str
    filename: str
    content_type: str = "text/plain"
    text: str
    title: Optional[str] = None
    source_url: Optional[str] = None
    language: Optional[str] = None
    author: Optional[str] = None
    modified_at: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}

class IngestForm(BaseModel):
    collection: IngestCollection
    documents: list[IngestDocument]


# --- Helper Functions ---

def get_integration_provider(request: Request, user) -> tuple[str, dict]:
    """Resolve the integration provider from the authenticated service account."""
    provider_slug = (user.info or {}).get("integration_provider")
    if not provider_slug:
        raise HTTPException(
            status_code=403,
            detail="This account is not configured as an integration service account",
        )
    providers = request.app.state.config.INTEGRATION_PROVIDERS
    provider_config = providers.get(provider_slug)
    if not provider_config:
        raise HTTPException(
            status_code=403,
            detail=f"Integration provider '{provider_slug}' is not registered",
        )
    return provider_slug, provider_config


def _find_kb_by_source_id(provider: str, source_id: str):
    """Find a knowledge base by provider slug + external source_id."""
    kbs = Knowledges.get_knowledge_bases_by_type(provider)
    for kb in kbs:
        meta = kb.meta or {}
        if meta.get("integration", {}).get("source_id") == source_id:
            return kb
    return None


def _create_kb_for_provider(provider: str, provider_config: dict, collection: IngestCollection, user_id: str):
    """Create a new knowledge base for a push provider."""
    form = KnowledgeForm(
        name=collection.name,
        description=collection.description,
        type=provider,
        access_control={},  # Private — same as OneDrive
    )
    knowledge = Knowledges.insert_new_knowledge(user_id, form)
    # Set integration metadata
    meta = {
        "integration": {
            "provider": provider,
            "source_id": collection.source_id,
            "language": collection.language,
            "tags": collection.tags,
            "provider_metadata": collection.metadata,
        }
    }
    Knowledges.update_knowledge_meta_by_id(knowledge.id, meta)
    return Knowledges.get_knowledge_by_id(knowledge.id)


def _process_ingest_document(
    request: Request,
    knowledge_id: str,
    provider: str,
    doc: IngestDocument,
    user_id: str,
) -> dict:
    """Process a single document: create file record, chunk, embed, store."""
    file_id = f"{provider}-{doc.source_id}"
    status = "created"

    # Check if file already exists (upsert)
    existing_file = Files.get_file_by_id(file_id)
    if existing_file:
        status = "updated"
        # Update file metadata
        Files.update_file_metadata_by_id(file_id, {
            "name": doc.title or doc.filename,
            "content_type": doc.content_type,
            "source": provider,
            "source_id": doc.source_id,
            "source_url": doc.source_url,
            "language": doc.language,
            "author": doc.author,
            "tags": doc.tags,
            "provider_metadata": doc.metadata,
        })
        Files.update_file_data_by_id(file_id, {"content": doc.text})
    else:
        # Create new file record
        text_hash = hashlib.sha256(doc.text.encode()).hexdigest()
        file_form = FileForm(
            id=file_id,
            filename=doc.filename,
            hash=text_hash,
            path="",  # No physical file
            data={"content": doc.text},
            meta={
                "name": doc.title or doc.filename,
                "content_type": doc.content_type,
                "source": provider,
                "source_id": doc.source_id,
                "source_url": doc.source_url,
                "language": doc.language,
                "author": doc.author,
                "tags": doc.tags,
                "provider_metadata": doc.metadata,
            },
        )
        Files.insert_new_file(user_id, file_form)

        # Link file to knowledge base
        Knowledges.add_file_to_knowledge_by_id(knowledge_id, file_id, user_id)

    # Create langchain Document for vector storage
    text_hash = hashlib.sha256(doc.text.encode()).hexdigest()
    lc_doc = Document(
        page_content=doc.text,
        metadata={
            "name": doc.title or doc.filename,
            "source": doc.source_url or doc.filename,
            "file_id": file_id,
            "created_by": user_id,
            "author": doc.author,
            "language": doc.language,
            "source_provider": provider,
        },
    )

    # Store in vector DB (chunking + embedding)
    # collection_name = knowledge_id (same pattern as KB file processing)
    try:
        save_docs_to_vector_db(
            request=request,
            docs=[lc_doc],
            collection_name=knowledge_id,
            metadata={
                "file_id": file_id,
                "name": doc.title or doc.filename,
                "hash": text_hash,
            },
            add=True,  # Append to existing collection
        )
        # Update file status
        Files.update_file_data_by_id(file_id, {"status": "completed"})
    except Exception as e:
        Files.update_file_data_by_id(file_id, {"status": "error", "error": str(e)})
        return {"source_id": doc.source_id, "file_id": file_id, "status": "error", "error": str(e)}

    return {"source_id": doc.source_id, "file_id": file_id, "status": status}


# --- Endpoints ---

@router.post("/ingest")
def ingest_documents(
    request: Request,
    form_data: IngestForm,
    user=Depends(get_verified_user),
):
    provider, provider_config = get_integration_provider(request, user)

    # Validate batch size
    max_per_request = provider_config.get("max_documents_per_request", 50)
    if len(form_data.documents) > max_per_request:
        raise HTTPException(400, f"Too many documents. Maximum {max_per_request} per request.")

    # Find or create KB
    knowledge = _find_kb_by_source_id(provider, form_data.collection.source_id)
    if not knowledge:
        knowledge = _create_kb_for_provider(provider, provider_config, form_data.collection, user.id)

    # Check file limit
    max_files = provider_config.get("max_files_per_kb", 250)
    current_files = Knowledges.get_knowledge_file_ids_by_id(knowledge.id)
    new_doc_ids = {f"{provider}-{doc.source_id}" for doc in form_data.documents}
    existing_ids = set(current_files) if current_files else set()
    net_new = len(new_doc_ids - existing_ids)
    if len(existing_ids) + net_new > max_files:
        raise HTTPException(400, f"Would exceed {max_files} file limit for this knowledge base.")

    # Process documents
    results = []
    created = updated = skipped = errors = 0
    for doc in form_data.documents:
        result = _process_ingest_document(
            request=request,
            knowledge_id=knowledge.id,
            provider=provider,
            doc=doc,
            user_id=user.id,
        )
        results.append(result)
        if result["status"] == "created":
            created += 1
        elif result["status"] == "updated":
            updated += 1
        elif result["status"] == "skipped":
            skipped += 1
        elif result["status"] == "error":
            errors += 1

    return {
        "knowledge_id": knowledge.id,
        "collection_source_id": form_data.collection.source_id,
        "provider": provider,
        "total": len(form_data.documents),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "documents": results,
    }


@router.delete("/collections/{source_id}")
def delete_collection(
    request: Request,
    source_id: str,
    user=Depends(get_verified_user),
):
    provider, _ = get_integration_provider(request, user)

    knowledge = _find_kb_by_source_id(provider, source_id)
    if not knowledge:
        raise HTTPException(404, f"Collection '{source_id}' not found for provider '{provider}'")

    # Verify ownership: KB type must match provider
    if knowledge.type != provider:
        raise HTTPException(403, "Cannot delete collections belonging to another provider")

    # Remove all files and vector data
    file_ids = Knowledges.get_knowledge_file_ids_by_id(knowledge.id)
    for file_id in (file_ids or []):
        # Delete from vector DB
        try:
            from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
            VECTOR_DB_CLIENT.delete(
                collection_name=knowledge.id,
                filter={"file_id": file_id},
            )
        except Exception:
            pass
        # Delete file record
        Files.delete_file_by_id(file_id)

    # Soft-delete the knowledge base
    Knowledges.soft_delete_by_id(knowledge.id)

    return {"status": "deleted", "source_id": source_id, "provider": provider}


@router.delete("/collections/{source_id}/documents/{document_source_id}")
def delete_document(
    request: Request,
    source_id: str,
    document_source_id: str,
    user=Depends(get_verified_user),
):
    provider, _ = get_integration_provider(request, user)

    knowledge = _find_kb_by_source_id(provider, source_id)
    if not knowledge:
        raise HTTPException(404, f"Collection '{source_id}' not found for provider '{provider}'")

    if knowledge.type != provider:
        raise HTTPException(403, "Cannot delete documents from another provider's collection")

    file_id = f"{provider}-{document_source_id}"
    file = Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(404, f"Document '{document_source_id}' not found")

    # Remove from vector DB
    try:
        from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id,
            filter={"file_id": file_id},
        )
    except Exception:
        pass

    # Remove KnowledgeFile link
    Knowledges.remove_file_from_knowledge_by_id(knowledge.id, file_id)

    # Delete file record (no other KBs reference it — provider files are exclusive)
    Files.delete_file_by_id(file_id)

    return {"status": "deleted", "source_id": source_id, "document_source_id": document_source_id, "provider": provider}
```

#### 2. Register the Router

**File**: `backend/open_webui/main.py`
**Changes**: Import and mount the integrations router (after knowledge router ~line 1572)

```python
from open_webui.routers import integrations
# ...
app.include_router(integrations.router, prefix="/api/v1/integrations", tags=["integrations"])
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts without errors: `open-webui dev`
- [x] `POST /api/v1/integrations/ingest` with valid API key creates KB + files + embeddings
- [x] Same POST is idempotent (re-push same docs → `updated` status, no duplicates)
- [x] `POST /api/v1/integrations/ingest` with non-service-account API key returns 403
- [x] `POST /api/v1/integrations/ingest` exceeding `max_documents_per_request` returns 400
- [x] `POST /api/v1/integrations/ingest` exceeding `max_files_per_kb` returns 400
- [x] `DELETE /api/v1/integrations/collections/{source_id}` soft-deletes KB and removes files
- [x] `DELETE /api/v1/integrations/collections/{source_id}` returns 404 for wrong provider's collection
- [x] `DELETE /api/v1/integrations/collections/{source_id}/documents/{doc_id}` removes single document
- [x] Created KB has `type="{provider_slug}"` and `access_control={}`
- [x] File records have deterministic IDs (`{provider}-{source_id}`)
- [x] Vector chunks have correct metadata (`name`, `source`, `file_id`, `source_provider`)

#### Manual Verification:

- [ ] Ingested documents appear in the KB detail page file list
- [ ] Citations from ingested documents show clickable `source_url` links with favicons
- [ ] After document deletion, RAG queries no longer return that document's chunks

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Admin Panel — Integrations Settings Tab

### Overview

Add an "Integrations" sub-tab under Admin > Settings where admins can manage integration providers: add/edit/remove providers, configure display settings, bind service accounts, and view API usage examples.

### Changes Required:

#### 1. Add Tab Slug to Feature Registry

**File**: `src/lib/utils/features.ts:89-105`
**Changes**: Add `'integrations'` to the `ADMIN_SETTINGS_TABS` array

```typescript
export const ADMIN_SETTINGS_TABS = [
	'general',
	'connections',
	'models',
	'evaluations',
	'tools',
	'documents',
	'web',
	'code-execution',
	'interface',
	'audio',
	'images',
	'pipelines',
	'db',
	'acceptance',
	'email',
	'integrations'
] as const;
```

#### 2. Frontend API Client

**File**: `src/lib/apis/configs/index.ts`
**Changes**: Add `getIntegrationsConfig` and `setIntegrationsConfig` functions

```typescript
export const getIntegrationsConfig = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/integrations`, {
		method: 'GET',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
	});
	if (!res.ok) throw await res.json();
	return res.json();
};

export const setIntegrationsConfig = async (token: string, config: object) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/integrations`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
		body: JSON.stringify(config)
	});
	if (!res.ok) throw await res.json();
	return res.json();
};
```

#### 3. Integrations Settings Component

**File**: `src/lib/components/admin/Settings/Integrations.svelte` (new file)

This component allows admins to:

- **List registered providers** in a table (name, slug, badge preview, data type, service account, file limit)
- **Add a new provider** via a form: slug (auto-generated from name), display name, description, badge type (dropdown: info/success/warning/error/muted), data type (dropdown: documents/parsed-text/pre-chunked), data type description (textarea), max files per KB, max documents per request
- **Edit an existing provider** — same form, pre-populated
- **Remove a provider** — with confirmation dialog
- **Bind service account** — user search/select dropdown (searches existing users)
- **View API examples** — expandable section per provider showing curl examples with the provider's configuration:

```
POST /api/v1/integrations/ingest
Authorization: Bearer sk-xxxxx

{
  "collection": {
    "source_id": "my-collection-123",
    "name": "My Collection",
    ...
  },
  "documents": [...]
}
```

The component follows the pattern of `Documents.svelte` (onMount loads data, save handler posts updates).

#### 4. Wire Into Settings Orchestrator

**File**: `src/lib/components/admin/Settings.svelte`
**Changes**: Three additions:

1. **Import** (top of file, ~line 10-28):

```svelte
import Integrations from './Settings/Integrations.svelte';
```

2. **Sidebar button** (in the sidebar section, after Email ~line 521):

```svelte
{#if isAdminSettingsTabEnabled('integrations')}
	<button class="..." on:click={() => goto('/admin/settings/integrations')}>
		<LinkIcon />
		<!-- or Puzzle icon -->
		<div>{$i18n.t('Integrations')}</div>
	</button>
{/if}
```

3. **Content panel** (in the content area, after Email ~line 623):

```svelte
{:else if selectedTab === 'integrations'}
    <Integrations
        saveHandler={() => {
            toast.success($i18n.t('Settings saved successfully!'));
        }}
    />
```

#### 5. Translations

**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add keys (alphabetically sorted):

```json
"Data Type": "",
"Data Type Description": "",
"Integration Provider": "",
"Integrations": "",
"Max Documents Per Request": "",
"Max Files Per Knowledge Base": "",
"No integration providers configured": "",
"Service Account": "",
```

### Success Criteria:

#### Automated Verification:

- [x] Frontend builds without errors: `npm run build`
- [x] No new TypeScript errors from the Integrations component

#### Manual Verification:

- [ ] "Integrations" tab appears in Admin > Settings sidebar
- [ ] Can add a new provider with all fields (name, slug, badge type, data type, limits)
- [ ] Provider appears in the list with correct badge preview
- [ ] Can edit an existing provider
- [ ] Can remove a provider (with confirmation)
- [ ] Can bind a user as a service account via user search
- [ ] API examples section shows correct curl commands
- [ ] Settings persist across page reloads
- [ ] Non-admin users cannot access the Integrations tab

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 4.

---

## Phase 4: Frontend — KB Provider Awareness

### Overview

Update the knowledge base list and detail pages to show provider-specific UI: badges, filtering, empty states, and appropriate controls for push-provider KBs.

### Changes Required:

#### 1. KB List — Provider Badges

**File**: `src/lib/components/workspace/Knowledge.svelte:293-302`
**Changes**: Add provider-aware badge rendering

```svelte
<!-- Current -->
{#if item?.type === 'onedrive'}
	<Badge type="info" content="OneDrive" />
{:else}
	<Badge type="muted" content="Local" />
{/if}

<!-- New -->
{#if item?.type === 'onedrive'}
	<Badge type="info" content="OneDrive" />
{:else if $config?.integration_providers?.[item?.type]}
	<Badge
		type={$config.integration_providers[item.type].badge_type}
		content={$config.integration_providers[item.type].name}
	/>
{:else}
	<Badge type="muted" content="Local" />
{/if}
```

#### 2. KB List — Type Filter Dropdown

**File**: `src/lib/components/workspace/Knowledge.svelte`
**Changes**: Add a type filter dropdown next to the existing ViewSelector (~line 258-265)

Add a `typeFilter` state variable (default `''` = all types). Add a `<select>` or custom dropdown with options:

- `""` — All
- `"local"` — Local
- `"onedrive"` — OneDrive (if applicable)
- One option per registered integration provider (from `$config.integration_providers`)

Pass `typeFilter` to the `searchKnowledgeBases` call as the `type` query parameter (the backend already supports this — `models/knowledge.py:244-246`).

Update the reactive statement to include `typeFilter`:

```svelte
$: if (loaded && query !== undefined && viewOption !== undefined && typeFilter !== undefined) {
    init();
}
```

Update `getItemsPage()` to pass `typeFilter`:

```svelte
const res = await searchKnowledgeBases(localStorage.token, query, viewOption, page, typeFilter);
```

**File**: `src/lib/apis/knowledge/index.ts`
**Changes**: Add `type` parameter to `searchKnowledgeBases`

```typescript
export const searchKnowledgeBases = async (
	token: string,
	query: string = '',
	viewOption: string = '',
	page: number = 1,
	type: string = ''
) => {
	// Add type to query params if non-empty
	if (type) searchParams.append('type', type);
	// ...
};
```

#### 3. KB Detail — Provider Badge

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1441-1445`
**Changes**: Same badge logic as the list page

```svelte
{#if knowledge?.type === 'onedrive'}
	<Badge type="info" content="OneDrive" />
{:else if $config?.integration_providers?.[knowledge?.type]}
	<Badge
		type={$config.integration_providers[knowledge.type].badge_type}
		content={$config.integration_providers[knowledge.type].name}
	/>
{:else}
	<Badge type="muted" content="Local" />
{/if}
```

#### 4. KB Detail — Hide Add Files for Push Providers

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1597-1636`
**Changes**: Add condition to hide add-content controls for push-provider KBs

The current logic:

```svelte
{#if knowledge?.write_access}
	{#if knowledge?.type === 'onedrive'}
		<!-- OneDrive sync button -->
	{:else}
		<!-- AddContentMenu -->
	{/if}
{/if}
```

Add a third branch:

```svelte
{#if knowledge?.write_access}
	{#if knowledge?.type === 'onedrive'}
		<!-- OneDrive sync button -->
	{:else if $config?.integration_providers?.[knowledge?.type]}
		<!-- No add button for push providers — files come via API -->
	{:else}
		<!-- AddContentMenu -->
	{/if}
{/if}
```

#### 5. KB Detail — File Count with Provider Limits

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1519-1524`
**Changes**: Use provider-specific `max_files_per_kb` instead of hardcoded 250

Currently for non-local KBs it shows `{fileItemsTotal} / 250 files`. We need to resolve the provider's limit. Since the full provider config (with limits) isn't exposed to the frontend via `/api/config`, we have two options:

**Option A** (simpler): Include `max_files_per_kb` in the `integration_providers` config response. Update the `/api/config` response to include it:

```python
"integration_providers": {
    slug: {"name": p["name"], "badge_type": p["badge_type"], "max_files_per_kb": p.get("max_files_per_kb", 250)}
    for slug, p in request.app.state.config.INTEGRATION_PROVIDERS.items()
}
```

Then in the frontend:

```svelte
{#if knowledge?.type !== 'local' && knowledge?.type}
	{@const maxFiles = $config?.integration_providers?.[knowledge.type]?.max_files_per_kb || 250}
	<Tooltip content={$i18n.t('Maximum {{count}} files per knowledge base', { count: maxFiles })}>
		{fileItemsTotal} / {maxFiles} files
	</Tooltip>
{:else}
	{fileItemsTotal} files
{/if}
```

#### 6. KB Detail — Empty State

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase/EmptyStateCards.svelte:23-59`
**Changes**: Add a case for push-provider KBs

In the `getOptions()` function:

```typescript
if (knowledgeType === 'onedrive') {
    return [{ type: 'onedrive', ... }];
} else if (integrationProviders?.[knowledgeType]) {
    // Push provider — no user actions, just informational
    return [{
        type: 'integration',
        title: $i18n.t('Ingested via {{name}} API', { name: integrationProviders[knowledgeType].name }),
        description: $i18n.t('Documents are pushed to this knowledge base via the integration API.'),
        icon: 'puzzle-piece',
    }];
} else {
    return [/* existing local options */];
}
```

The `integrationProviders` prop comes from the parent, sourced from `$config.integration_providers`.

Pass `integrationProviders` from `KnowledgeBase.svelte` → `EmptyStateCards.svelte`:

```svelte
<EmptyStateCards
    knowledgeType={knowledge?.type}
    integrationProviders={$config?.integration_providers}
    onAction={...}
/>
```

For the integration empty state card, the `onAction` callback is a no-op (the card is informational only).

#### 7. KB Detail — Access Control for Push Providers

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1536-1561`
**Changes**: Push provider KBs should show "Private" label (same as OneDrive)

Current logic shows access control button only for `knowledge?.type === 'local' || !knowledge?.type`. This already works correctly — push provider KBs (`type = "octobox"`) fall into the "Private" label branch at lines 1552-1556.

**No changes needed** — the existing logic handles this correctly since push provider types are neither `'local'` nor `undefined`.

### Success Criteria:

#### Automated Verification:

- [x] Frontend builds without errors: `npm run build`
- [x] No new TypeScript errors from modified components

#### Manual Verification:

- [ ] KB list shows correct provider badge (e.g., blue "Octobox") for push-provider KBs
- [ ] KB list type filter dropdown shows "All", "Local", "OneDrive", and each registered provider
- [ ] Filtering by provider type shows only those KBs
- [ ] KB detail page shows provider badge in header
- [ ] No "Add files" button shown for push-provider KBs
- [ ] File count shows `N / {max_files}` with correct provider limit
- [ ] Empty state shows "Ingested via {Provider} API" message
- [ ] Access control shows "Private" label (not editable)
- [ ] Local KBs are unaffected by all changes
- [ ] OneDrive KBs are unaffected by all changes

**Implementation Note**: After completing this phase, all features should be functional end-to-end.

---

## Testing Strategy

### Unit Tests:

- Provider config serialization/deserialization
- `_find_kb_by_source_id` with multiple providers
- File ID generation (`{provider}-{source_id}`)
- Provider validation (missing provider, invalid provider)
- Batch size validation

### Integration Tests:

- Full ingest flow: API key → provider resolution → KB creation → file creation → vector storage
- Idempotent re-push: same documents pushed twice → no duplicates
- Cross-provider isolation: Octobox can't see/delete Neo's collections
- Delete collection: KB soft-deleted, files removed, vector data cleaned
- Delete document: single file removed, KB intact

### Manual Testing Steps:

1. Create provider "Octobox" in Admin > Settings > Integrations
2. Create a user account, bind it as Octobox service account
3. Generate API key for that user
4. Push documents via `curl POST /api/v1/integrations/ingest`
5. Verify KB appears in list with "Octobox" badge
6. Verify files appear in KB detail page
7. Ask a question that should match ingested content — verify citations with clickable source URLs
8. Push same documents again — verify no duplicates
9. Delete a single document via API — verify it disappears
10. Delete the collection via API — verify KB is soft-deleted
11. Filter KB list by "Octobox" type — verify only Octobox KBs shown

## Migration Notes

- No database migration needed — `knowledge.type` column already exists as free-text
- Provider config stored in existing `config` table via PersistentConfig
- Service account binding uses existing `user.info` JSON column
- Existing KBs (`local`, `onedrive`) are completely unaffected

## References

- Research document: `thoughts/shared/research/2026-03-15-push-ingest-integration.md`
- Foundation research: `thoughts/shared/research/2026-03-06-external-data-pipeline-ingestion.md`
- Octobox integration email: `thoughts/shared/research/2026-03-06-octobox-integratie-email.md`
- Airweave comparison: `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md`
- Typed KBs plan: `thoughts/shared/plans/2026-02-04-typed-knowledge-bases.md`
- `save_docs_to_vector_db()`: `routers/retrieval.py:1352-1559`
- KB type validation: `routers/knowledge.py:189`
- File model (caller-supplied ID): `models/files.py:89-96`
- API key auth: `utils/auth.py:269-397`
- Admin settings tab system: `src/lib/utils/features.ts:89-105`, `src/lib/components/admin/Settings.svelte`
