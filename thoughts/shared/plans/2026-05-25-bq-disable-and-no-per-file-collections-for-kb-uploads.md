# Disable Weaviate BQ + Stop Per-File Collections on KB Uploads — Implementation Plan

## Overview

Two coupled fixes that together cut Weaviate collection-creation latency (the user-reported "~8 s per collection") without giving up the option to roll back if memory pressure returns:

1. **BQ env-var toggle, default OFF.** Today every `File_*`, `Web_search_*`, and `User_memory_*` collection is forced into the Weaviate `flat` index with binary quantization. Make that an `ENABLE_WEAVIATE_BQ_QUANTIZATION` env var (default `false`), so by default all collections get the Weaviate HNSW default and the option to flip BQ back on remains.
2. **Stop creating `file-<id>` collections when a file is uploaded into a KB.** Mirror the cloud-sync precedent (`thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md`, landed in `services/sync/base_worker.py:600-646`) for local KB uploads. The workspace KB upload UI already sends `metadata.knowledge_id` — wire the backend to act on it and embed directly into the KB collection, skipping `file-<id>` entirely. Keep `file-<id>` for one-off chat uploads (drag/drop into chat without a KB) — that's the only remaining producer/consumer.

## Current State Analysis

### BQ policy

- `backend/open_webui/retrieval/vector/dbs/weaviate.py:79-90` — `_FLAT_INDEX_PREFIXES = ('File_', 'Web_search_', 'User_memory_')` and `_build_vector_config()` returns BQ+flat for matching collections, default HNSW for everything else.
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:156` — `vector_config=_build_vector_config(collection_name)` is the single wire-up point inside `_create_collection`.
- `backend/open_webui/test/util/test_weaviate_index_policy.py` — locks the contract with three parametrised tests.
- No env-var or Helm plumbing exists today.

### Per-file collection producers

- `backend/open_webui/routers/retrieval.py:1649-1652` — `process_file()` defaults `collection_name` to `f'file-{file.id}'` when none is passed (the one-off upload path).
- `backend/open_webui/routers/files.py:127-132` — `process_uploaded_file()` calls `process_file(ProcessFileForm(file_id=...))` with no `collection_name`, so every uploaded file gets a `file-<id>` collection regardless of whether it's going into a KB.
- `backend/open_webui/routers/knowledge.py:733-744` — `/knowledge/{id}/file/add` then calls `process_file` again with `collection_name=kb.id`, which **re-reads** the chunks from `file-<id>` and writes them into the KB collection.

### Per-file collection consumers

- `backend/open_webui/retrieval/utils.py:1070` — RAG retrieval for chat-attached `type:'file'` items.
- `backend/open_webui/routers/retrieval.py:1683` — `process_file` cross-KB cache lookup (the one we'll drop).
- `backend/open_webui/tools/builtin.py:2110-2114` — built-in knowledge-search tool for `__model_knowledge__` files.

### Frontend signals already in place

- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:470-471` — the workspace KB upload UI already sends `metadata.knowledge_id` to `/files/`. The backend just doesn't act on it today.
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:500-503` — the workspace UI relies on the Socket.IO `file:status` event to fire a separate `addFileToKnowledgeBaseById` call after processing completes. We'll be able to remove that round-trip once the backend handles the link itself.
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:222` — `{#if item.type === 'local' || !item.type}` currently hides the per-file chevron for cloud KBs only. We extend the hide to all KB types.
- `backend/open_webui/models/knowledge.py:333-335` — `search_knowledge_files` already filters to `Knowledge.type == 'local'` (Phase 3 from the April cloud-sync plan landed). We tighten further to return no KB files.

### Key Discoveries

- The workspace KB UI **already** sends `metadata.knowledge_id` on every KB upload — the wiring problem is purely backend.
- `process_file`'s `elif form_data.collection_name:` branch (`retrieval.py:1679-1764`) has a three-step fallback: file-`<id>` cache → `existing_content` → disk load+extract. Dropping the first step (per resolved Q3) leaves a clean two-step fallback that already exists and is tested.
- Cloud sync's `_ensure_vectors_in_kb` at `services/sync/base_worker.py:600-646` is the template for the gradual-cleanup pattern (delete legacy `file-<id>` on next interaction).
- BQ disabling will increase Weaviate memory/disk footprint per vector — that's the explicit trade-off the user is accepting in exchange for getting rid of per-file collections (and their accompanying index overhead).

## Desired End State

- `ENABLE_WEAVIATE_BQ_QUANTIZATION` env var (default `false`). With it false, every collection — including `File_*` / `Web_search_*` / `User_memory_*` — uses Weaviate's HNSW default with no quantizer.
- Uploading a file into a local KB (via the workspace UI) creates exactly **one** Weaviate collection (the KB collection), not two. The file is embedded once, directly into the KB collection.
- Uploading a file into a chat (drag/drop, paste, picker — no `knowledge_id`) still creates a `file-<id>` collection. No regression.
- The chat input `+` menu shows no "show files" chevron for any KB type (local or cloud). Attaching a whole KB still works.
- The `#` slash-command file search returns no KB files. Searching by `#`-prefix only returns folders and KB collections.
- Adding an already-uploaded file to a KB (the `/knowledge/{id}/file/add` path) still works for files that have `data.content` populated; the dropped per-file cache is replaced by the existing `existing_content` fallback.
- Legacy `file-<id>` collections from before this change are cleaned up lazily on next interaction (KB add, file remove) — no migration script required.

### Verification

- New file upload via KB workspace UI → exactly 1 Weaviate collection created (the KB UUID).
- New file upload via chat (drag/drop) → 1 `file-<id>` collection created.
- Chat input `+` menu → no chevron on any KB.
- `#` slash command → no files returned, only KBs and folders.
- RAG chat with full KB attached → still returns relevant chunks.
- `ENABLE_WEAVIATE_BQ_QUANTIZATION=true` re-enables BQ on the three prefixes — test asserts both branches.

## What We're NOT Doing

- **Not** changing the one-off chat upload path. Files uploaded outside a KB still go through `file-<id>` — that collection is their only home.
- **Not** writing a one-shot migration script for legacy `file-<id>` collections. Cleanup is gradual on next KB interaction (matches the April cloud-sync approach).
- **Not** restructuring `WeaviateClient` connection logic, batch sizes, or the connect_to_custom handshake. The 8-s cost is server-side; the only client lever we have here is the index policy.
- **Not** removing `_FLAT_INDEX_PREFIXES` outright. The constant stays as the list of prefixes that get BQ when the flag is on.
- **Not** changing how cloud-sync KBs work — `base_worker.py` already does the right thing.
- **Not** changing the response shape of `/knowledge/{id}/file/add`. It stays as a fallback for the "file already uploaded, now linking to KB" case.
- **Not** introducing a separate "knowledge_id" query param on `/files/` — the frontend already passes it inside `metadata`, so we read it from there.

## Implementation Approach

Work in this order: BQ toggle (smallest, immediate impact) → backend upload-into-KB wiring → frontend cleanup + KB-file chat-attach removal → legacy cleanup. Each phase deployable on its own.

---

## Phase 1: BQ Env-Var Toggle, Default OFF

### Overview

Add `ENABLE_WEAVIATE_BQ_QUANTIZATION` env var. When false (default), every collection uses Weaviate's HNSW default. When true, restore today's BQ+flat behavior for the three prefixes.

### Changes Required:

#### 1. `backend/open_webui/config.py` — Add env var

**File**: `backend/open_webui/config.py`
**Changes**: Add the flag next to the other Weaviate vars at lines 2657-2666.

```python
WEAVIATE_SKIP_INIT_CHECKS = os.environ.get('WEAVIATE_SKIP_INIT_CHECKS', 'false').lower() == 'true'
# Enable binary-quantization + flat index for File_*, Web_search_*, User_memory_*
# collections. Default off: HNSW for every collection. Flip on if Weaviate memory
# pressure returns; the trade-off is ~8s per collection creation on flip-on.
ENABLE_WEAVIATE_BQ_QUANTIZATION = (
    os.environ.get('ENABLE_WEAVIATE_BQ_QUANTIZATION', 'false').lower() == 'true'
)
```

#### 2. `backend/open_webui/retrieval/vector/dbs/weaviate.py` — Gate `_build_vector_config` on the flag

**File**: `backend/open_webui/retrieval/vector/dbs/weaviate.py`
**Changes**: Import the flag at line 20-29 and rewrite `_build_vector_config` at lines 82-90.

```python
from open_webui.config import (
    WEAVIATE_HTTP_HOST,
    WEAVIATE_GRPC_HOST,
    WEAVIATE_HTTP_PORT,
    WEAVIATE_GRPC_PORT,
    WEAVIATE_API_KEY,
    WEAVIATE_HTTP_SECURE,
    WEAVIATE_GRPC_SECURE,
    WEAVIATE_SKIP_INIT_CHECKS,
    ENABLE_WEAVIATE_BQ_QUANTIZATION,
)
```

```python
def _build_vector_config(sane_collection_name: str):
    """Pick the vector-index config for a class based on its name prefix.

    When ENABLE_WEAVIATE_BQ_QUANTIZATION is false (default), every collection
    falls through to the Weaviate HNSW default. When true, File_*, Web_search_*,
    and User_memory_* collections get the flat index with binary quantization.
    """
    if ENABLE_WEAVIATE_BQ_QUANTIZATION and sane_collection_name.startswith(_FLAT_INDEX_PREFIXES):
        return weaviate.classes.config.Configure.Vectors.self_provided(
            vector_index_config=weaviate.classes.config.Configure.VectorIndex.flat(
                quantizer=weaviate.classes.config.Configure.VectorIndex.Quantizer.bq()
            )
        )
    return weaviate.classes.config.Configure.Vectors.self_provided()
```

#### 3. `backend/open_webui/test/util/test_weaviate_index_policy.py` — Parametrise over the flag

**File**: `backend/open_webui/test/util/test_weaviate_index_policy.py`
**Changes**: Use `monkeypatch` to toggle `ENABLE_WEAVIATE_BQ_QUANTIZATION` and assert both branches.

```python
"""Tests for the Weaviate per-collection vector-index policy.

Per-file (`File_*`), per-web-search (`Web_search_*`), and per-user-memory
(`User_memory_*`) classes can opt into the `flat` index with binary
quantization via ENABLE_WEAVIATE_BQ_QUANTIZATION (default off — HNSW for
every collection). See thoughts/shared/plans/2026-05-25-bq-disable-and-no-per-file-collections-for-kb-uploads.md.
"""

import pytest

from open_webui.retrieval.vector.dbs import weaviate as weaviate_module
from open_webui.retrieval.vector.dbs.weaviate import (
    _FLAT_INDEX_PREFIXES,
    _build_vector_config,
)


def _serialized(sane_name: str) -> dict:
    return _build_vector_config(sane_name)._to_dict()


class TestVectorIndexPolicyBqOff:
    """Default behavior: ENABLE_WEAVIATE_BQ_QUANTIZATION is false."""

    @pytest.fixture(autouse=True)
    def _bq_off(self, monkeypatch):
        monkeypatch.setattr(weaviate_module, 'ENABLE_WEAVIATE_BQ_QUANTIZATION', False)

    @pytest.mark.parametrize(
        'sane_name',
        [
            'File_abc123',
            'File_550e8400_e29b_41d4_a716_446655440000',
            'Web_search_deadbeef',
            'User_memory_user_42',
            'Abc123def456',
            'KnowledgeBaseClass',
        ],
    )
    def test_all_classes_default_hnsw_when_disabled(self, sane_name: str) -> None:
        payload = _serialized(sane_name)
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})


class TestVectorIndexPolicyBqOn:
    """Opt-in behavior: ENABLE_WEAVIATE_BQ_QUANTIZATION is true."""

    @pytest.fixture(autouse=True)
    def _bq_on(self, monkeypatch):
        monkeypatch.setattr(weaviate_module, 'ENABLE_WEAVIATE_BQ_QUANTIZATION', True)

    @pytest.mark.parametrize(
        'sane_name',
        [
            'File_abc123',
            'File_550e8400_e29b_41d4_a716_446655440000',
            'Web_search_deadbeef',
            'User_memory_user_42',
        ],
    )
    def test_targeted_prefixes_get_flat_with_bq(self, sane_name: str) -> None:
        payload = _serialized(sane_name)
        assert payload['vectorIndexType'] == 'flat'
        assert payload['vectorIndexConfig'] == {'bq': {'enabled': True}}

    @pytest.mark.parametrize(
        'sane_name',
        [
            'Abc123def456',
            'KnowledgeBaseClass',
            'Filer_lookalike',
            'Web_lookalike',
            'User_other',
        ],
    )
    def test_other_classes_keep_default_hnsw(self, sane_name: str) -> None:
        payload = _serialized(sane_name)
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})


def test_prefix_list_matches_plan() -> None:
    assert _FLAT_INDEX_PREFIXES == ('File_', 'Web_search_', 'User_memory_')
```

#### 4. `helm/open-webui-tenant/templates/open-webui/configmap.yaml` — Wire flag into configmap

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
**Changes**: Add under the Weaviate section (after line 67).

```yaml
  WEAVIATE_WEB_SEARCH_TTL_MINUTES: {{ .Values.openWebui.config.weaviateWebSearchTtlMinutes | quote }}
  ENABLE_WEAVIATE_BQ_QUANTIZATION: {{ .Values.openWebui.config.enableWeaviateBqQuantization | default "false" | quote }}
```

#### 5. `helm/open-webui-tenant/values.yaml` — Expose default

**File**: `helm/open-webui-tenant/values.yaml`
**Changes**: Add to the `openWebui.config` block alongside the other Weaviate keys (search for `weaviateWebSearchTtlMinutes` to find the right spot).

```yaml
    weaviateWebSearchTtlMinutes: "1440"
    enableWeaviateBqQuantization: "false"
```

### Success Criteria:

#### Automated Verification:

- [x] Backend imports cleanly: `cd backend && python -c "from open_webui.config import ENABLE_WEAVIATE_BQ_QUANTIZATION; print(ENABLE_WEAVIATE_BQ_QUANTIZATION)"`
- [x] Index-policy tests pass with default-off and opt-in branches: `cd backend && pytest open_webui/test/util/test_weaviate_index_policy.py -v`
- [x] Lint passes: `npm run lint:backend`
- [x] Helm chart renders: `helm template helm/open-webui-tenant | grep ENABLE_WEAVIATE_BQ_QUANTIZATION`

#### Manual Verification:

- [ ] With the flag unset (default), upload a file to a KB → check Weaviate (`curl http://localhost:8080/v1/schema | jq '.classes[] | {class, vectorIndexType, vectorIndexConfig}'`) → confirm `File_*` collections show `vectorIndexType: 'hnsw'` and no `bq` config.
- [ ] Set `ENABLE_WEAVIATE_BQ_QUANTIZATION=true`, restart, upload a new file → confirm `File_*` for that file shows `vectorIndexType: 'flat'` with `bq.enabled: true`.
- [ ] Existing pre-flag collections are untouched (they keep whatever index they were created with). Schema migrations of existing collections are out of scope.

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Skip `file-<id>` Creation When Uploading Into a KB (Backend)

### Overview

Wire the existing `metadata.knowledge_id` signal (already sent by the workspace KB upload UI) through the upload pipeline so files destined for a KB embed directly into the KB collection, never creating a `file-<id>` collection. Drop the per-file cache lookup in `process_file`'s `elif` branch (per the user's Q3 answer). Update the built-in knowledge-search tool to fall back to a KB-collection query when `file-<id>` is missing.

### Changes Required:

#### 1. `backend/open_webui/routers/files.py` — Thread `knowledge_id` through the upload pipeline

**File**: `backend/open_webui/routers/files.py`
**Changes**: Read `knowledge_id` from `metadata`, verify write access, pass through to `process_uploaded_file` → `process_file` so the file embeds into the KB collection directly. Also link the file to the KB so the frontend no longer needs the separate `addFileToKnowledgeBaseById` round-trip.

```python
def process_uploaded_file(
    request,
    file,
    file_path,
    file_item,
    file_metadata,
    user,
    db: Optional[Session] = None,
    knowledge_id: Optional[str] = None,  # NEW
):
    def _process_handler(db_session):
        try:
            content_type = file.content_type
            # ... existing content-type detection unchanged ...

            # If destined for a KB, embed directly into the KB collection
            # — skip the file-<id> collection.
            collection_name = knowledge_id  # None for one-off uploads → process_file defaults to f'file-{id}'

            if content_type:
                # ... existing branches, but every process_file call now passes collection_name=collection_name
                stt_supported_content_types = getattr(
                    request.app.state.config, 'STT_SUPPORTED_CONTENT_TYPES', []
                )
                if strict_match_mime_type(stt_supported_content_types, content_type):
                    file_path_processed = Storage.get_file(file_path)
                    result = transcribe(request, file_path_processed, file_metadata, user)
                    process_file(
                        request,
                        ProcessFileForm(
                            file_id=file_item.id,
                            content=result.get('text', ''),
                            collection_name=collection_name,
                        ),
                        user=user,
                        db=db_session,
                    )
                elif (not content_type.startswith(('image/', 'video/'))) or (
                    request.app.state.config.CONTENT_EXTRACTION_ENGINE == 'external'
                ):
                    process_file(
                        request,
                        ProcessFileForm(
                            file_id=file_item.id,
                            collection_name=collection_name,
                        ),
                        user=user,
                        db=db_session,
                    )
                # ... unsupported branch unchanged
            else:
                process_file(
                    request,
                    ProcessFileForm(
                        file_id=file_item.id,
                        collection_name=collection_name,
                    ),
                    user=user,
                    db=db_session,
                )

            # If this upload was for a KB, link the file → KB so the
            # frontend doesn't need a separate /knowledge/{id}/file/add call.
            if knowledge_id:
                Knowledges.add_file_to_knowledge_by_id(
                    knowledge_id=knowledge_id,
                    file_id=file_item.id,
                    user_id=user.id,
                    db=db_session,
                )

            # ... existing Socket.IO emit unchanged
        except Exception as e:
            # ... unchanged
```

In `upload_file_handler` (around line 218 onward), extract the `knowledge_id` from the metadata blob and verify write access:

```python
def upload_file_handler(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[dict | str] = Form(None),
    process: bool = Query(True),
    process_in_background: bool = Query(True),
    user=Depends(get_verified_user),
    background_tasks: Optional[BackgroundTasks] = None,
    db: Optional[Session] = None,
):
    log.info(f'file.content_type: {file.content_type} {process}')

    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Invalid metadata format'),
            )
    file_metadata = metadata if metadata else {}

    # Extract and validate knowledge_id (workspace KB upload signal).
    # When set, the file embeds directly into the KB collection and is
    # auto-linked to the KB.
    knowledge_id = file_metadata.get('knowledge_id') if isinstance(file_metadata, dict) else None
    if knowledge_id:
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.access_grants import AccessGrants

        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id, db=db)
        if not knowledge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
        if (
            knowledge.user_id != user.id
            and not AccessGrants.has_access(
                user_id=user.id,
                resource_type='knowledge',
                resource_id=knowledge.id,
                permission='write',
                db=db,
            )
            and user.role != 'admin'
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

    # ... existing extension validation, storage upload, file insert unchanged ...

    # When invoking the background task, forward knowledge_id:
    if process:
        if process_in_background and background_tasks:
            background_tasks.add_task(
                process_uploaded_file,
                request,
                file,
                file_path,
                file_item,
                file_metadata,
                user,
                None,  # db
                knowledge_id,  # NEW
            )
        else:
            process_uploaded_file(
                request,
                file,
                file_path,
                file_item,
                file_metadata,
                user,
                db,
                knowledge_id,  # NEW
            )
```

(The exact wiring of `knowledge_id` into the background-task call depends on the current call site — look at `upload_file_handler`'s tail to confirm the `process_uploaded_file` invocation shape.)

#### 2. `backend/open_webui/routers/retrieval.py` — Drop the `file-<id>` cache lookup

**File**: `backend/open_webui/routers/retrieval.py`
**Changes**: In `process_file`, the `elif form_data.collection_name:` branch at lines 1679-1764 currently queries `file-<id>` first. Drop that query — fall straight through to `existing_content`, then disk load.

```python
elif form_data.collection_name:
    # Check if the file has already been processed and save the content
    # Usage: /knowledge/{id}/file/add, /knowledge/{id}/file/update,
    # and /files/ with knowledge_id (workspace KB upload)
    #
    # Previously read chunks from a per-file `file-{id}` cache collection.
    # We no longer create those for KB-bound files, so fall straight through
    # to the existing_content / disk-load path.
    existing_content = file.data.get('content', '')
    if existing_content:
        docs = [
            Document(
                page_content=existing_content,
                metadata={
                    **file.meta,
                    'name': file.filename,
                    'created_by': file.user_id,
                    'file_id': file.id,
                    'source': file.filename,
                },
            )
        ]
    else:
        # File hasn't been processed yet (e.g. background processing
        # hasn't completed). Load and extract content directly.
        file_path = file.path
        if file_path:
            file_path = Storage.get_file(file_path)
            loader = Loader(
                # ... unchanged loader kwargs ...
            )
            docs = loader.load(file.filename, file.meta.get('content_type'), file_path)
            docs = [
                Document(
                    page_content=doc.page_content,
                    metadata={
                        **filter_metadata(doc.metadata),
                        'name': file.filename,
                        'created_by': file.user_id,
                        'file_id': file.id,
                        'source': file.filename,
                    },
                )
                for doc in docs
            ]
        else:
            raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)

    text_content = ' '.join([doc.page_content for doc in docs]) if docs else file.data.get('content', '')
```

#### 3. `backend/open_webui/tools/builtin.py` — Fall back to KB-collection query for KB-uploaded files

**File**: `backend/open_webui/tools/builtin.py`
**Changes**: At lines 2110-2114, when `item_type == 'file'`, the tool currently always uses `f'file-{item_id}'`. Detect when that collection doesn't exist and fall back to the KB collection filtered by `file_id`.

```python
elif item_type == 'file':
    # Individual file as model knowledge.
    file = Files.get_file_by_id(item_id)
    if not file:
        continue

    # One-off chat-uploaded files still have a `file-{id}` collection.
    file_collection = f'file-{item_id}'
    if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
        collection_names.append(file_collection)
        continue

    # KB-uploaded files: look up the KB and query its collection filtered
    # by file_id. (Falls back to first KB that contains this file — for
    # __model_knowledge__ items there is typically one.)
    kb = Knowledges.get_knowledge_by_file_id(file_id=item_id)
    if kb and (
        user_role == 'admin'
        or kb.user_id == user_id
        or AccessGrants.has_access(
            user_id=user_id,
            resource_type='knowledge',
            resource_id=kb.id,
            permission='read',
            user_group_ids=set(user_group_ids),
        )
    ):
        # Use a synthetic single-item collection_names entry so query_collection
        # can apply the file_id filter downstream.
        collection_names.append({'collection_name': kb.id, 'file_id': item_id})
```

(`Knowledges.get_knowledge_by_file_id` does not exist yet — add it as a thin helper that queries the `KnowledgeFile` junction table for the first KB matching this file id.)

If `query_collection` doesn't accept the dict-with-filter shape today, the cleanest alternative is to query the KB collection directly here, append the results, and skip the `query_collection` loop for this entry. Implementer's call — match whichever pattern is least disruptive at the call site.

#### 4. `backend/open_webui/models/knowledge.py` — Add `get_knowledge_by_file_id` helper

**File**: `backend/open_webui/models/knowledge.py`
**Changes**: Add a small helper used by Phase 2 step 3. Place near the other lookup methods.

```python
def get_knowledge_by_file_id(
    self, file_id: str, db: Optional[Session] = None
) -> Optional[KnowledgeModel]:
    """Return the first KB that has this file linked via KnowledgeFile."""
    try:
        with get_db_context(db) as db:
            row = (
                db.query(Knowledge)
                .join(KnowledgeFile, Knowledge.id == KnowledgeFile.knowledge_id)
                .filter(KnowledgeFile.file_id == file_id)
                .filter(Knowledge.deleted_at.is_(None))
                .first()
            )
            return KnowledgeModel.model_validate(row) if row else None
    except Exception as e:
        print('get_knowledge_by_file_id error:', e)
        return None
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts cleanly: `open-webui dev` _(verified via import check — `from open_webui.routers.files import process_uploaded_file, upload_file_handler; from open_webui.routers.retrieval import process_file; from open_webui.models.knowledge import Knowledges; from open_webui.tools.builtin import query_knowledge_files` all OK)_
- [x] Backend lint passes: `npm run lint:backend` _(7.11/10, up from 7.10/10 in Phase 1)_
- [x] `process_file` has no `VECTOR_DB_CLIENT.query(collection_name=f'file-{file.id}'` reference remaining — verified via `grep -n "VECTOR_DB_CLIENT.query.*file-{" backend/open_webui/routers/retrieval.py` (empty), and the remaining `file-{file.id}` references are the default-name assignment (line 1652) and the delete in the `form_data.content` branch (line 1660), as the plan predicted.
- [ ] Test the upload-with-knowledge_id path: `pytest backend/open_webui/test -k "upload"` (add a new test if none exists). _Existing test files have pre-existing collection errors; no isolated upload test exists. Manual verification covers this._

#### Manual Verification:

- [ ] Upload a new file via the workspace KB UI (e.g. drag a PDF into a local KB) → exactly **1** Weaviate collection touched (the KB UUID). Verify with `curl http://localhost:8080/v1/schema | jq '.classes[].class' | grep -i <file_id_prefix>` returns nothing for `File_<id>`.
- [ ] Same file is queryable via chat (attach the KB as a whole) — RAG returns chunks from this file.
- [ ] Upload a one-off file via chat (drag into the chat input, NOT a KB) → `File_<id>` collection still created. RAG with that file as a chat-attached `type:'file'` still works.
- [ ] Add an already-uploaded one-off file to a KB via the workspace UI's "add existing file" affordance (if exposed) → file gets embedded into the KB collection via the `existing_content` fallback. No regression.
- [ ] Attach a file as `__model_knowledge__` in the model workspace and run a chat that triggers the built-in knowledge-search tool → the tool resolves to the correct collection (file-<id> for chat-uploaded, KB collection for KB-uploaded).
- [ ] Unauthorized user attempting upload-with-knowledge_id to a KB they don't have write access to → 403.

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Frontend — Hide KB-File Chat-Attach Across All KB Types

### Overview

Two coupled frontend changes: (a) remove the now-redundant separate `addFileToKnowledgeBaseById` call from the workspace KB upload flow (the backend handles linking now), (b) hide the per-file chevron in the chat `+` menu for **all** KB types. Then tighten `search_knowledge_files` to return no files at all — the `#` slash command will only surface KBs and folders.

### Changes Required:

#### 1. `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte` — Remove the chevron entirely

**File**: `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte`
**Changes**: At line 222, the chevron is currently gated to local-only KBs. Remove the chevron block (lines 222-242) and the file-drilldown block (lines 245-309 approximately). Only the KB-row click remains.

The cleanest delete is to drop the entire `{#if item.type === 'local' || !item.type}` chevron block plus the `{#if selectedItem && selectedItem.id === item.id}` drilldown — leaving just the KB-as-collection click handler at lines 177-220. Also remove the now-unused `selectedItem`, `selectedFileItems`, `searchKnowledgeFilesById` import, `initSelectedFileItems`, and `loadMoreSelectedFileItems` from the component.

Verify by searching the component for `selectedItem` after the edit — should return zero hits.

#### 2. `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte` — Drop file results from `#` results

**File**: `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte`
**Changes**: At lines 120-135, `getKnowledgeFileItems` calls `searchKnowledgeFiles`. After Phase 3 step 3 (backend) `searchKnowledgeFiles` will return nothing, but keeping the frontend call is wasted work and visually `fileItems` will always be empty. Remove the `getKnowledgeFileItems` function, the `fileItems` state, and any `'file'`-type rendering branches in this component.

The component should now only show folders and KB collections in the `#` results.

#### 3. `backend/open_webui/models/knowledge.py` — Return no files from `search_knowledge_files`

**File**: `backend/open_webui/models/knowledge.py`
**Changes**: At lines 333-335, the filter is `Knowledge.type == 'local'`. Replace with a no-results short-circuit, or remove the endpoint binding if no other caller depends on it. Minimal change:

```python
# All file-search results are suppressed — chat-attach by individual KB file
# is no longer offered. Whole-KB attach via search_knowledge_bases is the
# supported path.
return KnowledgeFileListResponse(items=[], total=0)
```

Place this at the top of the function body, before the query construction. Keep the rest of the function in place so we can revert easily if needed.

#### 4. `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte` — Remove separate KB-add call

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: At lines 500-503, the comment says "Socket.IO 'file:status' event will trigger [addFileHandler]". The backend now links files to the KB during upload (Phase 2 step 1), so this is redundant — calling `addFileToKnowledgeBaseById` again would re-trigger `process_file` and embed twice.

Locate the Socket.IO `file:status` handler that calls `addFileHandler` / `addFileToKnowledgeBaseById` and remove the call (or guard it to no-op when `knowledge_id` was sent during upload). Keep the polling fallback wiring; just elide the actual add call.

Search the file for `addFileToKnowledgeBaseById` and `addFileHandler` to find both call sites.

### Success Criteria:

#### Automated Verification:

- [x] Frontend type-check passes: `npm run check` _(10195 errors / 277 warnings — all pre-existing per the project memory; none in files I touched)_
- [~] Frontend lint passes: `npm run lint:frontend` _(ESLint crashes on a pre-existing `@typescript-eslint/no-unused-vars` plugin bug while linting `src/lib/components/chat/FileNav/FilePreview.svelte` — a file unrelated to this phase. Not caused by Phase 3 changes.)_
- [x] Frontend builds: `npm run build` _(✓ built in 52.30s)_
- [x] Backend lint passes: `npm run lint:backend` _(7.11/10, unchanged from Phase 2)_

#### Manual Verification:

- [ ] Chat input `+` menu → click "Knowledge database" → no chevron next to any KB row. Click a KB → attaches as `type:'collection'`. RAG returns chunks.
- [ ] Type `#` in chat input → only KBs and folders appear in results, no files.
- [ ] Workspace KB UI → upload a file → file appears in the KB's file list within seconds. Verify there is no double-embed (Weaviate object count should match chunk count, not 2× chunk count).
- [ ] Existing chats with `type:'file'` items (from before this change) still resolve — the `file-<id>` collection still exists for them.

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation before proceeding to Phase 4.

---

## Phase 4: Gradual Cleanup of Legacy `file-<id>` Collections

### Overview

After Phase 2, no new `file-<id>` collections are created for KB-uploaded files. But pre-existing tenants (Vink especially, with ~1300 KB files) still have one `file-<id>` per KB file. Clean these up lazily on next interaction. No one-shot migration script — match the cloud-sync precedent.

### Changes Required:

#### 1. `backend/open_webui/routers/knowledge.py` — Delete `file-<id>` after successful KB add

**File**: `backend/open_webui/routers/knowledge.py`
**Changes**: At lines 733-744 (`add_file_to_knowledge_by_id`), after the `process_file` call succeeds, delete the `file-<id>` collection if it exists. This handles "add already-uploaded file to KB" — the file-`<id>` was created by an earlier no-KB upload, and once the file is in a KB collection, the per-file copy is dead weight.

```python
# Add content to the vector database
warning = None
try:
    result = process_file(
        request,
        ProcessFileForm(file_id=form_data.file_id, collection_name=id),
        user=user,
        db=db,
    )

    if isinstance(result, dict) and result.get('warning'):
        warning = result['warning']

    # Add file to knowledge base
    Knowledges.add_file_to_knowledge_by_id(knowledge_id=id, file_id=form_data.file_id, user_id=user.id, db=db)

    # Gradual cleanup: now that vectors live in the KB collection, drop the
    # per-file collection if one was created by an earlier no-KB upload.
    file_collection = f'file-{form_data.file_id}'
    if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
        VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
        log.info(f'Cleaned up legacy per-file collection {file_collection}')
except Exception as e:
    log.debug(e)
    raise HTTPException(...)
```

Apply the same cleanup in `/knowledge/{id}/file/update` (around line 818-823) after the `process_file` call.

#### 2. `backend/open_webui/routers/files.py` — Cleanup on file remove from KB (already handled)

**File**: `backend/open_webui/routers/files.py`
**Changes**: Line 831 already does `VECTOR_DB_CLIENT.delete(collection_name=f'file-{id}')` on file deletion — no change. Verify it tolerates a missing collection (`weaviate.py:143-148 delete_collection` swallows errors — good).

### Success Criteria:

#### Automated Verification:

- [ ] Lint passes: `npm run lint:backend`
- [ ] Adding a file to a KB on a fresh tenant works end-to-end: `pytest backend/open_webui/test -k "knowledge"` (run existing tests for regressions).

#### Manual Verification:

- [ ] On a tenant with legacy `file-<id>` collections: trigger a no-op interaction (re-add a file to its KB, or update a file) → confirm the corresponding `file-<id>` collection is gone from Weaviate (`curl http://localhost:8080/v1/schema | jq '.classes[] | select(.class | startswith("File_"))'` should show progressively fewer entries over time).
- [ ] For tenants where no further interaction is expected on legacy files: leftover `file-<id>` collections are inert (no consumers after Phase 3) and represent storage waste only — document in the rollout notes.

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation. This is the last phase.

---

## Testing Strategy

### Unit Tests

- `test_weaviate_index_policy.py` — updated to cover both BQ-on and BQ-off branches via `monkeypatch`. Covers File_, Web_search_, User_memory_ prefixes plus near-miss negative cases.
- (Optional) New test for `Knowledges.get_knowledge_by_file_id` if the model layer has a test suite.

### Integration Tests

- Backend: a focused pytest exercising `POST /files/` with `metadata.knowledge_id` set, asserting only the KB collection is touched and the file is linked to the KB.
- Frontend: existing Cypress flows for KB upload should still pass (E2E in `cypress/e2e/`).

### Manual Testing Steps

1. **BQ off (default).** Restart with `ENABLE_WEAVIATE_BQ_QUANTIZATION` unset → upload a file to a KB → inspect schema, confirm KB collection uses HNSW, no `File_*` collection created. Time the upload — should be visibly faster than pre-change.
2. **BQ on (rollback).** Set `ENABLE_WEAVIATE_BQ_QUANTIZATION=true`, restart, upload another file → confirm BQ behavior restored for any new `File_*` (e.g. via a one-off chat upload, since KB uploads no longer create `File_*`).
3. **Workspace KB upload.** Drag 10 files into a local KB → 10 file:status events, 1 KB collection, 0 `File_*` collections.
4. **One-off chat upload.** Drag a file into a chat (no KB) → 1 `File_*` collection. Chat RAG works.
5. **Chat input `+` menu.** Open the Knowledge tab → no chevrons. Attach a KB → RAG works.
6. **`#` slash command.** Type `#` → only KBs/folders, no files.
7. **Add existing file to KB.** Use the workspace UI's "add existing file" affordance (or call the API directly) → file gets embedded into the KB collection from `existing_content`. No regression.
8. **`__model_knowledge__` tool.** Attach a file (one-off and KB-uploaded) to a model. Trigger the built-in knowledge-search tool → both resolve correctly.
9. **Cloud sync regression.** Sync an OneDrive/Google Drive KB → still works as before, single collection per KB (cloud-sync code path was already fine).
10. **Authorization.** Try to upload with `metadata.knowledge_id` to a KB you don't have write access to → 403.

## Performance Considerations

- **Memory implication of BQ-off.** Default HNSW + no quantization uses ~32× more vector memory per chunk than BQ-flat (32-bit float vs 1-bit quantized). Watch tenant Weaviate memory after rollout. The `ENABLE_WEAVIATE_BQ_QUANTIZATION=true` flip-back path is the explicit mitigation.
- **Re-embed cost on `existing_content` fallback.** Dropping the `file-<id>` cache lookup in `process_file`'s `elif` branch means cross-KB re-adds will re-chunk (not re-embed; the embed step runs either way). Negligible for typical files.
- **Sync worker unaffected.** `services/sync/base_worker.py` already follows this pattern (the April 2026 plan landed). No regression risk for cloud-sync.

## Migration Notes

- **No database migration.** All changes are behavior-only.
- **No one-shot script for legacy `file-<id>` collections.** Cleanup happens gradually on next KB-add / file-update interaction. For tenants where no further KB activity is expected, leftover `file-<id>` collections are inert storage waste — acceptable per the April cloud-sync precedent.
- **Vink rollout.** ~1300 legacy `file-<id>` collections expected to remain after Phase 4 lands. They'll clear as files are touched. If we need a faster purge, we can add a one-shot script later — out of scope for this plan.
- **Rolling back BQ.** Setting `ENABLE_WEAVIATE_BQ_QUANTIZATION=true` only affects **newly created** collections — existing collections keep their index from when they were first created. To re-quantize existing data, the collections must be recreated (re-sync or re-embed). Out of scope for this plan.

## References

- Research: `thoughts/shared/research/2026-05-25-bq-quantization-and-per-file-collections.md`
- Prior research / direct precedent: `thoughts/shared/research/2026-04-06-cloud-kb-single-collection.md`
- Prior plan / direct precedent: `thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md`
- BQ-introducing plan to supersede: `thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md`
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:74-90` — BQ policy (Phase 1)
- `backend/open_webui/test/util/test_weaviate_index_policy.py` — contract test (Phase 1)
- `backend/open_webui/config.py:2657-2666` — Weaviate env-var block (Phase 1)
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml:61-67` — Weaviate configmap block (Phase 1)
- `backend/open_webui/routers/files.py:185-205` — `/files/` upload entry (Phase 2)
- `backend/open_webui/routers/files.py:93-182` — `process_uploaded_file` (Phase 2)
- `backend/open_webui/routers/retrieval.py:1679-1764` — `process_file` elif branch (Phase 2)
- `backend/open_webui/tools/builtin.py:2110-2114` — built-in tool file resolution (Phase 2)
- `backend/open_webui/services/sync/base_worker.py:600-646` — cloud-sync `_ensure_vectors_in_kb` (Phase 2 template)
- `backend/open_webui/models/knowledge.py:315-381` — `search_knowledge_files` (Phase 3)
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:170-310` — `+` menu KB list + drilldown (Phase 3)
- `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte:90-200` — `#` slash command results (Phase 3)
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:432-510` — workspace KB upload handler (Phase 3)
- `backend/open_webui/routers/knowledge.py:733-744` — `/knowledge/{id}/file/add` (Phase 4 cleanup)
