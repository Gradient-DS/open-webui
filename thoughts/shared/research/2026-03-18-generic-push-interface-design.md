---
date: 2026-03-18T10:30:00+01:00
researcher: Claude
git_commit: 7029d372c
branch: feat/octobox
repository: open-webui
topic: "Generic Push Interface Design — Supporting All Data Types and Future Pull Abstraction"
tags: [research, codebase, push-api, integrations, chunking, embeddings, rag, architecture]
status: complete
last_updated: 2026-03-18
last_updated_by: Claude
---

# Research: Generic Push Interface Design

**Date**: 2026-03-18T10:30:00+01:00
**Researcher**: Claude
**Git Commit**: 7029d372c
**Branch**: feat/octobox
**Repository**: open-webui

## Research Question

How should we extend the push ingest API to support all four data types (`parsed_text`, `chunked_text`, `chunked_embedded`, `full_documents`) in a way that's generic enough for any provider? How does push fit alongside the pull abstraction (OneDrive sync pattern) in the broader architecture?

## Summary

The current `POST /api/v1/integrations/ingest` endpoint handles only `parsed_text` (full document text → chunk → embed → store). We need three more data types, each skipping progressively more pipeline stages. The cleanest design uses **a single endpoint with a discriminated union schema** where the `data_type` field (already stored in provider config) determines which request shape is accepted and which pipeline stages are executed. This avoids endpoint proliferation and keeps the provider config as the source of truth.

For pull connectors, the existing `SyncProvider` ABC at `services/sync/provider.py` is a solid foundation. The key insight is that **push and pull are just different ingestion triggers** — once documents enter the system, they flow through the same pipeline stages. The provider config's `data_type` field already determines where in the pipeline data enters, whether pushed or pulled.

## Detailed Findings

### Current Pipeline Stages

```
┌─────────────┐    ┌──────────┐    ┌───────────┐    ┌──────────────┐
│ File Upload  │ →  │  Parse   │ →  │   Chunk   │ →  │   Embed      │ → Vector DB
│ (binary)     │    │ (Loader) │    │ (Splitter)│    │ (Model)      │
└─────────────┘    └──────────┘    └───────────┘    └──────────────┘
       ↑                ↑               ↑                 ↑
  full_documents   parsed_text    chunked_text     chunked_embedded
  (enters here)    (enters here)  (enters here)    (enters here)
```

Each data type skips earlier stages:

| Data Type | Parse | Chunk | Embed | Vector Insert |
|-----------|-------|-------|-------|---------------|
| `full_documents` | ✅ | ✅ | ✅ | ✅ |
| `parsed_text` | — | ✅ | ✅ | ✅ |
| `chunked_text` | — | — | ✅ | ✅ |
| `chunked_embedded` | — | — | — | ✅ |

### Existing Functions That Map to Each Stage

| Stage | Function | Location | Key Parameters |
|-------|----------|----------|----------------|
| Parse | `Loader.load()` | `retrieval/loaders/main.py:190` | `filename`, `content_type`, `file_path` |
| Chunk + Embed | `save_docs_to_vector_db()` | `routers/retrieval.py:1352` | `split=True/False`, `add=True` |
| Embed only | `save_docs_to_vector_db(split=False)` | same | `split=False` skips chunking |
| Direct insert | `save_embeddings_to_vector_db()` | `routers/retrieval.py:1276` | Accepts `{text, embedding, metadata}[]` |
| Full file flow | `process_file()` | `routers/retrieval.py:1569` | Upload → parse → chunk → embed → store |

### Recommended Schema Design: Discriminated Union

Rather than 4 separate endpoints, use a single `/ingest` endpoint with per-document `content` that varies by data type. The `data_type` is read from the provider config (already set by admin).

```python
# --- Base document (shared fields) ---
class IngestDocumentBase(BaseModel):
    source_id: str
    filename: str
    content_type: str = "text/plain"
    title: Optional[str] = None
    source_url: Optional[str] = None
    language: Optional[str] = None
    author: Optional[str] = None
    modified_at: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}

# --- Data type: parsed_text (current behavior) ---
class ParsedTextDocument(IngestDocumentBase):
    text: str                    # Full document text → will be chunked + embedded

# --- Data type: chunked_text ---
class ChunkedTextDocument(IngestDocumentBase):
    chunks: list[str]            # Pre-chunked text segments → will be embedded

# --- Data type: chunked_embedded ---
class ChunkedEmbeddedChunk(BaseModel):
    text: str
    embedding: list[float]
    metadata: dict = {}          # Optional per-chunk metadata

class ChunkedEmbeddedDocument(IngestDocumentBase):
    chunks: list[ChunkedEmbeddedChunk]   # Pre-chunked + pre-embedded → direct insert

# --- Data type: full_documents (file upload) ---
# Uses a separate multipart endpoint (see below)
```

### Implementation Plan Per Data Type

#### 1. `chunked_text` (Small effort)

**What changes**: Accept `chunks: list[str]` instead of `text: str`. Each chunk becomes a separate `Document` object. Call `save_docs_to_vector_db(split=False)`.

**Pipeline entry point**: Skip chunking, run embedding + insert.

```python
def _process_chunked_text_document(request, knowledge_id, provider, doc, user_id):
    file_id = f"{provider}-{doc.source_id}"
    # ... same file record creation as parsed_text ...

    # Create one Document per chunk
    lc_docs = []
    for i, chunk_text in enumerate(doc.chunks):
        lc_docs.append(Document(
            page_content=chunk_text,
            metadata={
                "name": doc.title or doc.filename,
                "source": doc.source_url or doc.filename,
                "file_id": file_id,
                "created_by": user_id,
                "chunk_index": i,
                "source_provider": provider,
            },
        ))

    text_hash = hashlib.sha256(
        "".join(doc.chunks).encode()
    ).hexdigest()

    save_docs_to_vector_db(
        request=request,
        docs=lc_docs,
        collection_name=knowledge_id,
        metadata={"file_id": file_id, "name": doc.title or doc.filename, "hash": text_hash},
        add=True,
        split=False,  # <-- KEY: skip chunking
    )
```

**Key detail**: `save_docs_to_vector_db` with `split=False` still handles embedding, metadata assembly, sanitization, and vector insertion. It just skips the text splitter step (lines 1398-1466 of `retrieval.py` are bypassed).

**File record**: Store concatenated chunks as `data.content` for full-text display in the UI.

#### 2. `chunked_embedded` (Medium effort)

**What changes**: Accept `chunks: list[{text, embedding, metadata}]`. Bypass both chunking AND embedding. Use `save_embeddings_to_vector_db()` which already exists.

**Pipeline entry point**: Direct vector DB insert.

```python
def _process_chunked_embedded_document(request, knowledge_id, provider, doc, user_id):
    file_id = f"{provider}-{doc.source_id}"
    # ... same file record creation ...

    # Prepare chunks for direct vector insert
    chunks_for_db = []
    for i, chunk in enumerate(doc.chunks):
        chunk_metadata = {
            **(chunk.metadata or {}),
            "file_id": file_id,
            "name": doc.title or doc.filename,
            "source": doc.source_url or doc.filename,
            "created_by": user_id,
            "chunk_index": i,
            "source_provider": provider,
        }
        chunks_for_db.append({
            "text": chunk.text,
            "embedding": chunk.embedding,
            "metadata": chunk_metadata,
        })

    save_embeddings_to_vector_db(
        collection_name=knowledge_id,
        chunks=chunks_for_db,
        file_metadata={"file_id": file_id, "name": doc.title or doc.filename},
        add=True,
    )
```

**Critical consideration — embedding model consistency**: When accepting pre-computed embeddings, ALL vectors in a collection MUST use the same embedding model and dimensionality. Options:
1. **Trust the provider** — assume they use the correct model (simplest, suitable for internal integrations)
2. **Validate dimensions** — check that `len(embedding) == expected_dim` based on the configured model
3. **Store embedding config** — record which model produced the embeddings in chunk metadata for audit

Recommendation: validate dimensions (option 2) as a safety check. The dimension of the configured model is available from the embedding function config.

**Hash-based dedup**: `save_embeddings_to_vector_db()` does NOT have hash dedup built in (unlike `save_docs_to_vector_db()`). For idempotent re-push, we should either:
- Add hash-based dedup to `save_embeddings_to_vector_db()`, OR
- Delete existing vectors for the file_id before inserting (simpler: `VECTOR_DB_CLIENT.delete(collection_name, filter={"file_id": file_id})`)

The delete-then-insert approach is simpler and consistent with the "update" path in `_process_ingest_document`.

#### 3. `full_documents` (Large effort)

**What changes**: Accept binary file uploads via multipart form data. Route through the existing `process_file()` pipeline (Loader → chunk → embed → store).

**Separate endpoint recommended**: Multipart uploads don't mix well with JSON bodies. Use:
```
POST /api/v1/integrations/upload
Content-Type: multipart/form-data

file: <binary>
source_id: <string>
collection_source_id: <string>
filename: <string>
metadata: <JSON string>
```

**Implementation approach**: Reuse `process_file()` from `retrieval.py` which already handles the full pipeline:
1. Save the uploaded file to storage via `Storage.save_file()`
2. Create a `File` record with the deterministic ID `{provider}-{source_id}`
3. Call `process_file()` with `file_id` and `collection_name=knowledge_id`
4. `process_file` handles: Loader dispatch → text extraction → chunking → embedding → vector insert

**Key complexity**:
- File storage management (S3/local, path construction)
- Content type detection (from file extension or `Content-Type` header)
- Large file handling (streaming upload, memory constraints)
- The existing `process_file()` endpoint expects a `File` record to already exist — so we need to create one first

**Suggested phased approach**:
- Phase A: Single file upload per request (simplest)
- Phase B: Batch upload (multiple files in one request)

### Making the Interface Generic

The provider config already has a `data_type` field. The ingest endpoint should:

1. **Read `data_type` from provider config** (not from the request body — providers always push the same type)
2. **Validate the request body** against the expected schema for that data type
3. **Dispatch to the correct processing function**

```python
@router.post("/ingest")
def ingest_documents(request, form_data: IngestForm, user=Depends(get_verified_user)):
    provider, provider_config = get_integration_provider(request, user)
    data_type = provider_config.get("data_type", "parsed_text")

    # Validate and dispatch per data_type
    if data_type == "parsed_text":
        # Current behavior — each doc has .text field
        process_fn = _process_parsed_text_document
    elif data_type == "chunked_text":
        # Each doc has .chunks: list[str]
        process_fn = _process_chunked_text_document
    elif data_type == "chunked_embedded":
        # Each doc has .chunks: list[{text, embedding}]
        process_fn = _process_chunked_embedded_document
    else:
        raise HTTPException(400, f"Unsupported data_type: {data_type}")

    # ... rest of validation, KB lookup, processing loop ...
```

**Schema validation**: Use Pydantic's `model_validator` or discriminated unions to validate the document structure matches the data_type. One approach: accept a flexible `IngestDocument` with optional fields, then validate in the dispatch:

```python
class IngestDocument(BaseModel):
    source_id: str
    filename: str
    content_type: str = "text/plain"
    title: Optional[str] = None
    source_url: Optional[str] = None
    # ... shared fields ...

    # Data-type-specific fields (only one should be set)
    text: Optional[str] = None                    # for parsed_text
    chunks: Optional[list] = None                 # for chunked_text or chunked_embedded
```

Then validate in the dispatch that the correct fields are present.

### Architecture: Push vs Pull

```
┌─────────────────────────────────────────────────────────┐
│                    Open WebUI                           │
│                                                         │
│  ┌──────────────┐     ┌──────────────────────────────┐  │
│  │  Push API     │     │  Pull Scheduler              │  │
│  │  (REST)       │     │  (Background Workers)        │  │
│  │               │     │                              │  │
│  │  /ingest      │     │  OneDrive  Google  Salesforce│  │
│  │  /upload      │     │  Sync      Drive   ...       │  │
│  └──────┬───────┘     └──────────┬───────────────────┘  │
│         │                        │                       │
│         │    ┌───────────────┐   │                       │
│         └──→ │  Pipeline     │ ←─┘                       │
│              │               │                           │
│              │  Parse → Chunk → Embed → Insert           │
│              │  (skip stages based on data_type)          │
│              └───────┬───────┘                           │
│                      │                                   │
│              ┌───────▼───────┐                           │
│              │  Knowledge    │                           │
│              │  Base         │                           │
│              │  (type=slug)  │                           │
│              │  + Files      │                           │
│              │  + Vector DB  │                           │
│              └───────────────┘                           │
└─────────────────────────────────────────────────────────┘
```

**Push** (our API dictates the contract):
- External system calls our REST API
- We control the schema, authentication, rate limits
- Provider config determines data_type → pipeline entry point
- Authentication: service account JWT bound to provider slug

**Pull** (abstracted connectors):
- Open WebUI scheduler triggers periodic sync
- `SyncProvider` ABC defines the contract: `execute_sync()`, `get_provider_type()`, `get_token_manager()`
- Each connector implementation handles: OAuth, API calls, file download, delta detection
- Data enters the pipeline as files or documents → same pipeline as push

**Key architectural insight**: The `data_type` concept applies to pull too. A Google Drive connector would use `full_documents` (download files → parse → chunk → embed). A Salesforce connector might use `parsed_text` (fetch record text → chunk → embed). The pipeline stages are shared.

### Pull Abstraction: Making It a Recipe

The current `SyncProvider` at `services/sync/provider.py` is a good start but tightly coupled to the OneDrive implementation. For future connectors:

**What works well** (keep):
- `SyncProvider.execute_sync()` — clean async interface
- `TokenManager` — separate concern for OAuth lifecycle
- Factory function — lazy import pattern prevents circular deps
- Provider type string → KB type mapping

**What needs abstraction** (for Google Drive, Salesforce, etc.):
1. **Delta detection**: OneDrive uses Graph API delta tokens. Others need their own. This stays provider-specific.
2. **File download + processing**: Currently hardcoded in `OneDriveSyncWorker`. Should delegate to the shared pipeline.
3. **Sync state storage**: Currently `meta.onedrive_sync`. Needs a generic `meta.sync` structure.
4. **Socket events**: Currently `onedrive:sync:progress`. Should be generic `sync:progress` with provider field.
5. **Scheduler integration**: Currently OneDrive-specific in `main.py` lifespan. Should be generic sync scheduler.

**Future recipe for adding a pull connector**:
```
1. Create services/{provider}/ directory
2. Implement SyncProvider ABC (execute_sync fetches data from external API)
3. Implement TokenManager ABC (OAuth flow)
4. Add to factory functions in services/sync/provider.py
5. Add provider type to KB type validation
6. Register in generic sync scheduler (future)
```

This is future work — but the push API design should not conflict with it.

### Handling Updates for Each Data Type

When a document is re-pushed (same `source_id`):

| Data Type | Update Strategy |
|-----------|----------------|
| `parsed_text` | Hash compare → skip if unchanged. If changed: delete old vectors by `file_id`, re-chunk + re-embed |
| `chunked_text` | Hash compare → skip if unchanged. If changed: delete old vectors by `file_id`, re-embed new chunks |
| `chunked_embedded` | Delete old vectors by `file_id`, insert new vectors (no hash dedup — provider controls versioning) |
| `full_documents` | Hash compare on file content → skip if unchanged. If changed: re-process through full pipeline |

The delete-then-insert pattern for updates is cleanest:
```python
# For updates (file already exists)
VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={"file_id": file_id})
# Then insert new vectors through appropriate pipeline stage
```

This is already partially implemented — `_process_ingest_document` updates file records but doesn't delete old vectors before re-inserting. The hash-based dedup in `save_docs_to_vector_db` catches unchanged docs, but changed docs with the same file_id will create duplicate vectors. **This is a bug in the current implementation** that should be fixed.

### Request Size and Performance Considerations

| Data Type | Typical Request Size | Latency Driver |
|-----------|---------------------|----------------|
| `parsed_text` | 10-100KB per doc | Chunking + Embedding |
| `chunked_text` | 10-100KB per doc | Embedding |
| `chunked_embedded` | 1-10MB per doc (vectors are large) | Vector DB insert |
| `full_documents` | 1-50MB per file | Parsing + Chunking + Embedding |

For `chunked_embedded`, consider:
- Larger `max_documents_per_request` limit since no embedding is needed
- Streaming/chunked transfer encoding for large payloads
- Batch vector insert (already handled by `VECTOR_DB_CLIENT.insert()`)

## Code References

- `backend/open_webui/routers/integrations.py` — Current push ingest API (complete file)
- `backend/open_webui/routers/retrieval.py:1276-1349` — `save_embeddings_to_vector_db()` for pre-computed embeddings
- `backend/open_webui/routers/retrieval.py:1352-1559` — `save_docs_to_vector_db()` with `split` parameter
- `backend/open_webui/routers/retrieval.py:1569-1840` — `process_file()` full pipeline
- `backend/open_webui/retrieval/vector/main.py:6-87` — `VectorDBBase` ABC, `VectorItem`, `SearchResult`
- `backend/open_webui/retrieval/loaders/main.py:184-250` — `Loader` class with engine dispatch
- `backend/open_webui/retrieval/utils.py:779-858` — `get_embedding_function()`
- `backend/open_webui/services/sync/provider.py` — `SyncProvider` and `TokenManager` ABCs
- `backend/open_webui/models/knowledge.py:42-72` — Knowledge model with `type` field

## Architecture Insights

1. **The pipeline is already modular** — `save_docs_to_vector_db(split=False)` and `save_embeddings_to_vector_db()` already provide the entry points for `chunked_text` and `chunked_embedded` respectively. We're not building new infrastructure, just exposing existing capabilities through the push API.

2. **Provider config's `data_type` is the router** — The admin-configured `data_type` per provider determines which pipeline stages are executed. This is elegant because it means the API schema validation and processing logic are determined by the provider, not per-request.

3. **File records are essential metadata** — Even for pre-embedded data, we must create `File` records to maintain the KB → Files → Vector DB relationship. The UI, file counts, deletion, and RAG query metadata all depend on this.

4. **Push and pull converge at the pipeline** — Both ingestion models ultimately need to: create/update file records, link files to KBs, and store vectors. The difference is just the trigger (REST call vs scheduler) and data source (request body vs external API).

5. **Vector update bug** — The current `_process_ingest_document` doesn't delete old vectors when updating a changed document. Hash dedup prevents re-insertion of identical content, but if content changes, old chunks remain alongside new ones. Fix: delete by `file_id` filter before re-inserting.

## Historical Context

- `thoughts/shared/research/2026-03-06-external-data-pipeline-ingestion.md` — Original research for Octobox push API, recommended the push approach
- `thoughts/shared/research/2026-03-15-push-ingest-integration.md` — Push ingest implementation research, defined provider registry pattern
- `thoughts/shared/plans/2026-03-15-push-ingest-integration.md` — Implementation plan for phases 1-4 (all complete)
- `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md` — Decision to build custom push API vs using Airweave

## Open Questions

1. **Should `data_type` be per-provider or per-request?** Current design is per-provider (admin sets it in config). Per-request would be more flexible but adds complexity. Recommendation: per-provider for now, as most external systems consistently produce one format.

2. **Embedding dimension validation for `chunked_embedded`** — Should we check vector dimensions against the configured embedding model? And should we reject if they don't match, or just warn?

3. **File upload endpoint path** — Should `full_documents` use `/ingest` with multipart or a separate `/upload` endpoint? Separate is cleaner but means two endpoints to document.

4. **Content storage for `chunked_embedded`** — Should we store chunk texts in `file.data.content`? If so, as concatenated text or as a JSON array of chunks? Concatenated is consistent with `parsed_text`; JSON array preserves chunk boundaries.

5. **Rate limiting per data type** — `chunked_embedded` requests are larger (contain vectors) but faster to process. Should rate limits differ by data type?
