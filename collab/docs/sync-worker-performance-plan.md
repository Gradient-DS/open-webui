# Sync Worker Performance Optimizations — Implementation Plan

## Overview

The cloud sync worker (Google Drive / OneDrive) processes files sequentially within each semaphore slot: download → S3 upload → content extraction → embedding into per-file collection → re-embedding into KB collection. For large folders (1700+ files), this results in ~14 minute sync times even at `FILE_PROCESSING_MAX_CONCURRENT=20`.

This plan implements three phases of optimization that reduce sync time to ~3-4 minutes by eliminating redundant work, parallelizing I/O, and pipelining stages.

## Current State Analysis

### Per-file pipeline (`base_worker.py:453-691`)

Each file goes through these sequential steps inside a single semaphore slot:

1. **Download** from cloud API (~1s) — `_download_file_content()`
2. **Hash check** — SHA-256, skip if unchanged
3. **S3 upload** (~0.5s) — `Storage.upload_file()`
4. **File record** create/update in DB
5. **Content extraction + embedding** (~1-2s) — `process_file(file_id=X)` via `asyncio.to_thread`
   - Loads file from storage, extracts text via Loader/external pipeline
   - Splits into chunks, generates embeddings, inserts into `file-{X}` collection
6. **Copy to KB collection** (~1-2s) — `process_file(file_id=X, collection_name=KB_ID)` via `asyncio.to_thread`
   - Queries `file-{X}` collection to get documents (NOT vectors)
   - Re-splits, **re-embeds** (generates embeddings again), inserts into KB collection
7. **KB association** in DB
8. **Cross-KB vector propagation** — for files in multiple KBs

**Total: 3-6s per file, ~14 min for 1700 files at concurrency 20.**

### Key discoveries

- **Step 6 re-embeds** — `process_file` Path B (`retrieval.py:1671-1756`) queries the per-file collection for document text, then `save_docs_to_vector_db` re-generates embeddings. It does NOT copy vectors. This is the single biggest waste.
- **Folder BFS is sequential** — `_collect_folder_files_full()` (`google_drive/sync_worker.py:342-383`) calls `list_folder_children()` one folder at a time. For nested structures with many subfolders, this adds minutes of discovery time before processing even starts.
- **Download and processing share one semaphore** — Downloads are I/O-bound (waiting on network), processing is compute/embed-bound. They compete for the same slots.
- **`process_files_batch`** endpoint exists (`retrieval.py:2799-2897`) but the sync worker doesn't use it.

## Desired End State

After all three phases:

1. Folder discovery runs in parallel (bounded by rate-limit-safe semaphore)
2. Downloads run at 3× the concurrency of processing, keeping the processing pool saturated
3. Each file is embedded **once** — directly into both the KB collection and per-file collection from the same embedding result
4. The double `process_file` call is eliminated

### Verification

- Sync 1700 files from a Google Drive folder completes in < 5 minutes (at concurrency 20)
- All files appear in the KB with correct vectors (search returns relevant results)
- Per-file collections (`file-{id}`) are populated for the file viewer UI
- Incremental sync (hash-match path) still works correctly
- Cross-KB vector propagation still works
- Cancellation still works at all pipeline stages
- Error isolation: individual file failures don't affect other files

## What We're NOT Doing

- **Batch accumulation across files** — Accumulating documents from N files into a single `save_docs_to_vector_db` call would further amortize embedding overhead, but adds complexity around partial failure tracking and per-file status reporting. Phase 3 already eliminates the redundant re-embedding.
- **Parallel incremental sync** — The Changes API (`get_changes`) is inherently sequential (token-chained pagination). Only full BFS benefits from parallelization.
- **Changes to the external pipeline** — The doc-processor and LiteLLM services are not bottlenecks.
- **OneDrive BFS parallelization** — OneDrive uses delta queries, not folder-by-folder BFS. The parallel BFS change only applies to Google Drive's full sync path.

## Implementation Approach

Three independent phases, each deployable and testable on its own. Phase 2 refactors `_process_file_info()` which Phase 3 also modifies, so Phase 3 builds on Phase 2's structure.

---

## Phase 1: Parallel BFS Folder Collection

### Overview

Replace the sequential BFS in `_collect_folder_files_full()` with a level-by-level concurrent traversal. At each depth level, all folder listings run in parallel (bounded by a semaphore to respect Google Drive API rate limits).

### Changes Required

#### 1. Google Drive sync worker — parallel BFS

**File**: `backend/open_webui/services/google_drive/sync_worker.py`

**Changes**: Rewrite `_collect_folder_files_full()` (lines 342-383)

```python
async def _collect_folder_files_full(self, source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
    """Full listing of all files in a folder (initial sync)."""
    folder_id = source['item_id']

    # Get start page token BEFORE listing (captures changes during listing)
    new_page_token = await self._client.get_start_page_token()

    # Build folder map for path resolution
    folder_map: Dict[str, str] = {folder_id: ''}
    files_to_process = []

    # Concurrent BFS: list all folders at the same depth level in parallel
    max_concurrent_listings = 10  # Respect Google Drive API rate limits
    listing_semaphore = asyncio.Semaphore(max_concurrent_listings)

    async def _list_folder(fid: str, parent_path: str):
        async with listing_semaphore:
            items = await self._client.list_folder_children(fid)
        return items, parent_path

    current_level = [(folder_id, '')]

    while current_level:
        # List all folders at this depth level concurrently
        tasks = [_list_folder(fid, path) for fid, path in current_level]
        results = await asyncio.gather(*tasks)

        next_level = []
        for items, parent_path in results:
            for item in items:
                item_name = item.get('name', 'unknown')
                item_path = f'{parent_path}/{item_name}' if parent_path else item_name
                mime_type = item.get('mimeType', '')

                if mime_type == 'application/vnd.google-apps.folder':
                    folder_map[item['id']] = item_path
                    next_level.append((item['id'], item_path))
                elif self._is_supported_file(item):
                    effective_name = self._get_effective_filename(item)
                    files_to_process.append(
                        {
                            'item': item,
                            'source_type': 'folder',
                            'source_item_id': source['item_id'],
                            'name': effective_name,
                            'relative_path': (f'{parent_path}/{effective_name}' if parent_path else effective_name),
                        }
                    )

        current_level = next_level

    # Persist folder map and page token
    source['folder_map'] = folder_map
    source['page_token'] = new_page_token

    return files_to_process, 0
```

**Key design decisions:**

- `max_concurrent_listings = 10` — Google Drive API has a 1000 queries/100s rate limit per user. With 10 concurrent requests and ~0.5s per paginated listing, we stay well under the limit.
- Level-by-level (not fire-and-forget) because subfolder discovery at depth N feeds the queue at depth N+1. But all folders at the same depth are independent.
- The `asyncio` import is already present in the file.

### Success Criteria

#### Automated Verification:

- [x] `npm run build` succeeds
- [ ] Backend starts without errors: `open-webui dev`
- [ ] Existing sync tests pass (if any)

#### Manual Verification:

- [ ] Sync a Google Drive folder with nested subfolders — all files discovered correctly
- [ ] Folder map persisted correctly (incremental sync works after)
- [ ] Rate limiting not triggered (no 429 errors in logs)
- [ ] Flat folders (no nesting) still work — regression check

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Two-Phase Download/Process Pipeline

### Overview

Split `_process_file_info()` into two methods with separate concurrency controls:

- **Download phase**: Download from cloud + hash check + S3 upload + file record — I/O bound, high concurrency
- **Process phase**: Content extraction + embedding + KB association — compute/embed bound, normal concurrency

### Changes Required

#### 1. New config: download concurrency multiplier

**File**: `backend/open_webui/config.py`

Add after `FILE_PROCESSING_MAX_CONCURRENT` (line ~3135):

```python
FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER = int(
    os.environ.get('FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER', '3')
)
```

No need for `PersistentConfig` — this is a tuning knob, not user-facing.

#### 2. Split `_process_file_info` into two phases

**File**: `backend/open_webui/services/sync/base_worker.py`

Refactor `_process_file_info()` (lines 453-691) into two methods.

**New method: `_download_and_store()`** — Returns a `PreparedFile` dataclass or `FailedFile`:

```python
@dataclass
class PreparedFile:
    """File that has been downloaded and stored, ready for content extraction."""
    file_id: str
    file_info: Dict[str, Any]
    name: str
    content_hash: str
    is_new: bool  # True if newly downloaded, False if hash-matched


async def _download_and_store(self, file_info: Dict[str, Any]) -> Union[PreparedFile, FailedFile, None]:
    """Phase 1: Download from cloud, check hash, upload to S3, create file record.

    Returns:
        PreparedFile if file needs processing
        None if file is unchanged (hash match, vectors verified)
        FailedFile on error
    """
    item = file_info['item']
    item_id = item['id']
    name = file_info['name']
    source_item_id = file_info.get('source_item_id')
    relative_path = file_info.get('relative_path', name)

    log.info(f'Downloading file: {name} (id: {item_id})')

    if self._check_cancelled():
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message='Sync cancelled by user',
        )

    await emit_file_processing(
        self.event_prefix,
        user_id=self.user_id,
        knowledge_id=self.knowledge_id,
        file_info={
            'item_id': item_id,
            'name': name,
            'size': item.get('size', 0),
            'source_item_id': source_item_id,
            'relative_path': relative_path,
        },
    )

    # Download file content
    try:
        content = await self._download_file_content(file_info)
    except Exception as e:
        log.warning(f'Failed to download file {name}: {e}')
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.DOWNLOAD_ERROR.value,
            error_message=f'Download failed: {str(e)[:80]}',
        )

    if not content or len(content) == 0:
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.EMPTY_CONTENT.value,
            error_message='File is empty',
        )

    # Hash-based change detection
    content_hash = hashlib.sha256(content).hexdigest()
    file_id = f'{self.file_id_prefix}{item_id}'
    existing = Files.get_file_by_id(file_id)

    if existing and existing.hash == content_hash:
        log.info(f'File {file_id} unchanged (hash match), ensuring KB association')

        new_relative_path = file_info.get('relative_path')
        existing_meta = existing.meta or {}
        if new_relative_path and existing_meta.get('relative_path') != new_relative_path:
            existing_meta['relative_path'] = new_relative_path
            Files.update_file_by_id(file_id, FileUpdateForm(meta=existing_meta))

        Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)
        result = await self._ensure_vectors_in_kb(file_id)
        if result:
            if result.error_type == SyncErrorType.EMPTY_CONTENT.value:
                return None  # Skip, not failure
            log.warning(f'File {file_id} vectors missing, will re-process')
            # Fall through to re-process
        else:
            file_record = Files.get_file_by_id(file_id)
            if file_record:
                await emit_file_added(
                    self.event_prefix,
                    user_id=self.user_id,
                    knowledge_id=self.knowledge_id,
                    file_data={
                        'id': file_record.id,
                        'filename': file_record.filename,
                        'meta': file_record.meta,
                        'created_at': file_record.created_at,
                        'updated_at': file_record.updated_at,
                    },
                )
            return None  # Success, no processing needed

    # Upload to storage
    temp_filename = f'{file_id}_{name}'
    try:
        storage_headers = {
            'OpenWebUI-User-Id': self.user_id,
            'OpenWebUI-File-Id': file_id,
        }
        storage_headers.update(self._get_provider_storage_headers(item_id))

        contents, file_path = Storage.upload_file(
            io.BytesIO(content),
            temp_filename,
            storage_headers,
        )
    except Exception as e:
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message=f'Storage upload failed: {str(e)[:80]}',
        )

    # Create/update file record
    try:
        content_type = self._get_content_type(name)
        file_meta = self._get_provider_file_meta(
            item_id=item_id,
            source_item_id=source_item_id,
            relative_path=relative_path,
            name=name,
            content_type=content_type,
            size=len(content),
            file_info=file_info,
        )

        if existing:
            Files.update_file_by_id(
                file_id,
                FileUpdateForm(hash=content_hash, meta=file_meta),
            )
            Files.update_file_path_by_id(file_id, file_path)
        else:
            file_form = FileForm(
                id=file_id,
                filename=name,
                path=file_path,
                hash=content_hash,
                data={},
                meta=file_meta,
            )
            Files.insert_new_file(self.user_id, file_form)

        return PreparedFile(
            file_id=file_id,
            file_info=file_info,
            name=name,
            content_hash=content_hash,
            is_new=not existing,
        )
    except Exception as e:
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message=str(e)[:100],
        )
```

**New method: `_process_and_embed()`** — Handles extraction + embedding + KB association:

```python
async def _process_and_embed(self, prepared: PreparedFile) -> Optional[FailedFile]:
    """Phase 2: Extract content, embed, associate with KB.

    Returns None on success, FailedFile on error.
    """
    file_id = prepared.file_id
    name = prepared.name

    if self._check_cancelled():
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message='Sync cancelled by user',
        )

    # Process file via internal API call (extract + embed)
    failed = await self._process_file_via_api(file_id, name)
    if failed:
        return failed

    if self._check_cancelled():
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message='Sync cancelled by user',
        )

    Knowledges.add_file_to_knowledge_by_id(
        self.knowledge_id, file_id, self.user_id,
    )

    # Propagate updated vectors to other KBs
    try:
        knowledge_files = Knowledges.get_knowledge_files_by_file_id(file_id)
        for kf in knowledge_files:
            if kf.knowledge_id != self.knowledge_id:
                log.info(f'Propagating updated vectors for {file_id} to KB {kf.knowledge_id}')
                try:
                    VECTOR_DB_CLIENT.delete(
                        collection_name=kf.knowledge_id,
                        filter={'file_id': file_id},
                    )
                except Exception as e:
                    log.warning(f'Failed to remove old vectors from KB {kf.knowledge_id}: {e}')
                try:
                    from open_webui.routers.retrieval import process_file, ProcessFileForm

                    def _call_propagate(form_data):
                        with get_db() as db:
                            return process_file(
                                self._make_request(),
                                form_data,
                                user=self._get_user(),
                                db=db,
                            )

                    await asyncio.to_thread(
                        _call_propagate,
                        ProcessFileForm(
                            file_id=file_id,
                            collection_name=kf.knowledge_id,
                        ),
                    )
                except Exception as e:
                    log.warning(f'Failed to propagate vectors to KB {kf.knowledge_id}: {e}')
    except Exception as e:
        log.warning(f'Failed to propagate vector updates for {file_id}: {e}')

    # Emit file added event
    file_record = Files.get_file_by_id(file_id)
    if file_record:
        await emit_file_added(
            self.event_prefix,
            user_id=self.user_id,
            knowledge_id=self.knowledge_id,
            file_data={
                'id': file_record.id,
                'filename': file_record.filename,
                'meta': file_record.meta,
                'created_at': file_record.created_at,
                'updated_at': file_record.updated_at,
            },
        )

    return None
```

#### 3. Rewrite the parallel processing section in `sync()`

**File**: `backend/open_webui/services/sync/base_worker.py`

Replace lines 814-899 (the `asyncio.gather` section):

```python
# Process all files with two-phase pipeline
from open_webui.config import FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER

max_process_concurrent = FILE_PROCESSING_MAX_CONCURRENT.value
max_download_concurrent = max_process_concurrent * FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER
download_semaphore = asyncio.Semaphore(max_download_concurrent)
process_semaphore = asyncio.Semaphore(max_process_concurrent)
processed_count = already_synced
failed_count = 0
results_lock = asyncio.Lock()
cancelled = False

async def pipeline(file_info: Dict[str, Any], index: int) -> Optional[FailedFile]:
    nonlocal processed_count, failed_count, cancelled

    if cancelled or self._check_cancelled():
        cancelled = True
        return FailedFile(
            filename=file_info.get('name', 'unknown'),
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message='Sync cancelled by user',
        )

    try:
        # Phase 1: Download + store (high concurrency)
        async with download_semaphore:
            if cancelled or self._check_cancelled():
                cancelled = True
                return FailedFile(
                    filename=file_info.get('name', 'unknown'),
                    error_type=SyncErrorType.PROCESSING_ERROR.value,
                    error_message='Sync cancelled by user',
                )
            result = await self._download_and_store(file_info)

        # Handle download phase results
        if isinstance(result, FailedFile):
            async with results_lock:
                failed_count += 1
                await self._update_sync_status(
                    'syncing', processed_count + failed_count, total_files,
                    file_info.get('name', ''),
                    files_processed=processed_count, files_failed=failed_count,
                )
            return result

        if result is None:
            # Hash match — already handled, count as success
            async with results_lock:
                processed_count += 1
                await self._update_sync_status(
                    'syncing', processed_count + failed_count, total_files,
                    file_info.get('name', ''),
                    files_processed=processed_count, files_failed=failed_count,
                )
            return None

        # Phase 2: Process + embed (normal concurrency)
        async with process_semaphore:
            if cancelled or self._check_cancelled():
                cancelled = True
                return FailedFile(
                    filename=file_info.get('name', 'unknown'),
                    error_type=SyncErrorType.PROCESSING_ERROR.value,
                    error_message='Sync cancelled by user',
                )
            process_result = await self._process_and_embed(result)

        async with results_lock:
            if process_result is None:
                processed_count += 1
            else:
                failed_count += 1
            await self._update_sync_status(
                'syncing', processed_count + failed_count, total_files,
                file_info.get('name', ''),
                files_processed=processed_count, files_failed=failed_count,
            )
        return process_result

    except Exception as e:
        log.error(f'Error in pipeline for {file_info.get("name")}: {e}')
        async with results_lock:
            failed_count += 1
        return FailedFile(
            filename=file_info.get('name', 'unknown'),
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message=str(e)[:100],
        )

log.info(
    f'Starting pipeline processing of {len(all_files_to_process)} files '
    f'(download concurrency: {max_download_concurrent}, '
    f'process concurrency: {max_process_concurrent})'
)
start_time = time.time()

tasks = [pipeline(file_info, i) for i, file_info in enumerate(all_files_to_process)]
results = await asyncio.gather(*tasks, return_exceptions=True)

for result in results:
    if isinstance(result, Exception):
        log.error(f'Unexpected error during file processing: {result}')
        total_failed += 1
        failed_files.append(
            FailedFile(
                filename='unknown',
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(result)[:100],
            )
        )
    elif result is not None:
        failed_files.append(result)

total_processed = processed_count
total_failed = failed_count

processing_time = time.time() - start_time
log.info(
    f'Pipeline processing completed in {processing_time:.2f}s: '
    f'{total_processed} succeeded, {total_failed} failed'
)
```

**How the pipeline works:**

```
File 1:  [===download===]  [===process===]
File 2:  [===download===]  [===process===]
File 3:  [===download===]     [===process===]
File 4:     [===download===]     [===process===]
File 5:     [===download===]        [===process===]
File 6:        [===download===]        [===process===]
                                                        → time
```

- All 1700 coroutines are created immediately via `asyncio.gather`
- `download_semaphore` allows 60 concurrent downloads (at multiplier 3× with process concurrency 20)
- `process_semaphore` allows 20 concurrent process+embed operations
- A file moves from download → process as soon as both: download is done AND a process slot is free
- Hash-matched files skip the process phase entirely, freeing download slots faster

#### 4. Remove old `_process_file_info` method

After the refactor, `_process_file_info()` (lines 453-691) is replaced by `_download_and_store()` + `_process_and_embed()`. Delete it.

#### 5. Helm config (optional)

**File**: `helm/open-webui-tenant/values.yaml`

```yaml
fileDownloadConcurrencyMultiplier: '3'
```

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

```yaml
FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER:
  { { .Values.openWebui.config.fileDownloadConcurrencyMultiplier | quote } }
```

### Success Criteria

#### Automated Verification:

- [x] `npm run build` succeeds
- [ ] Backend starts without errors
- [ ] No regressions in existing sync behaviour

#### Manual Verification:

- [ ] Sync a large Google Drive folder (100+ files) — all files processed
- [ ] Verify download and process phases overlap in logs (download messages interleaved with embedding messages)
- [ ] Cancel a sync mid-flight — cancellation works in both download and process phases
- [ ] Hash-match files (re-sync without changes) skip quickly
- [ ] Error in one file doesn't affect others

**Implementation Note**: After completing this phase, pause for manual verification before proceeding to Phase 3.

---

## Phase 3: Direct-to-KB Embedding (Skip Double process_file)

### Overview

Replace the two sequential `process_file` calls in `_process_file_via_api()` with a single extraction + embedding step that writes vectors directly to the KB collection. Also populate the per-file collection from the same embedding result (vector copy, not re-embed).

**Current flow (2 embeddings per file):**

```
process_file(file_id) → extract → split → EMBED → insert into file-{id}
process_file(file_id, collection_name) → query file-{id} → split → EMBED → insert into KB
```

**New flow (1 embedding per file):**

```
extract → split → EMBED → insert into KB collection
                       └→ insert same vectors into file-{id} collection
```

### Changes Required

#### 1. New method: `_extract_content()`

**File**: `backend/open_webui/services/sync/base_worker.py`

Add a method that extracts text content from a file using the same pipeline as `process_file` but returns the documents instead of embedding them:

```python
async def _extract_content(self, file_id: str) -> Optional[List[Document]]:
    """Extract text content from a file, returning Documents for embedding.

    Uses the same extraction pipeline as process_file (external pipeline with
    internal fallback), but returns the documents instead of embedding them.

    Returns None if no content could be extracted.
    """
    from open_webui.routers.retrieval import (
        save_docs_to_vector_db,
        Loader,
        filter_metadata,
    )
    from open_webui.routers.external_retrieval import (
        process_file_with_external_pipeline,
    )
    from open_webui.storage.provider import Storage

    request = self._make_request()
    user = self._get_user()

    def _extract_in_thread():
        with get_db() as db:
            if user.role == 'admin':
                file = Files.get_file_by_id(file_id, db=db)
            else:
                file = Files.get_file_by_id_and_user_id(file_id, user.id, db=db)

            if not file:
                raise ValueError(f'File {file_id} not found')

            file_path = file.path
            if not file_path:
                raise ValueError(f'File {file_id} has no path')

            file_path = Storage.get_file(file_path)

            loader = Loader(
                engine=request.app.state.config.CONTENT_EXTRACTION_ENGINE,
                user=user,
                EXTERNAL_DOCUMENT_LOADER_URL=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL,
                EXTERNAL_DOCUMENT_LOADER_API_KEY=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
                TIKA_SERVER_URL=request.app.state.config.TIKA_SERVER_URL,
                DOCLING_SERVER_URL=request.app.state.config.DOCLING_SERVER_URL,
                DOCLING_API_KEY=request.app.state.config.DOCLING_API_KEY,
                DOCLING_PARAMS=request.app.state.config.DOCLING_PARAMS,
                PDF_EXTRACT_IMAGES=request.app.state.config.PDF_EXTRACT_IMAGES,
                PDF_LOADER_MODE=request.app.state.config.PDF_LOADER_MODE,
                # Include all other Loader params from config...
                # (mirror what process_file passes to Loader)
                DATALAB_MARKER_API_KEY=request.app.state.config.DATALAB_MARKER_API_KEY,
                DATALAB_MARKER_API_BASE_URL=request.app.state.config.DATALAB_MARKER_API_BASE_URL,
                DATALAB_MARKER_ADDITIONAL_CONFIG=request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG,
                DATALAB_MARKER_SKIP_CACHE=request.app.state.config.DATALAB_MARKER_SKIP_CACHE,
                DATALAB_MARKER_FORCE_OCR=request.app.state.config.DATALAB_MARKER_FORCE_OCR,
                DATALAB_MARKER_PAGINATE=request.app.state.config.DATALAB_MARKER_PAGINATE,
                DATALAB_MARKER_STRIP_EXISTING_OCR=request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR,
                DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION=request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
                DATALAB_MARKER_FORMAT_LINES=request.app.state.config.DATALAB_MARKER_FORMAT_LINES,
                DATALAB_MARKER_USE_LLM=request.app.state.config.DATALAB_MARKER_USE_LLM,
                DATALAB_MARKER_OUTPUT_FORMAT=request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT,
                DOCUMENT_INTELLIGENCE_ENDPOINT=request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
                DOCUMENT_INTELLIGENCE_KEY=request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
                DOCUMENT_INTELLIGENCE_MODEL=request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL,
                MISTRAL_OCR_API_BASE_URL=request.app.state.config.MISTRAL_OCR_API_BASE_URL,
                MISTRAL_OCR_API_KEY=request.app.state.config.MISTRAL_OCR_API_KEY,
                MINERU_API_MODE=request.app.state.config.MINERU_API_MODE,
                MINERU_API_URL=request.app.state.config.MINERU_API_URL,
                MINERU_API_KEY=request.app.state.config.MINERU_API_KEY,
                MINERU_API_TIMEOUT=request.app.state.config.MINERU_API_TIMEOUT,
                MINERU_PARAMS=request.app.state.config.MINERU_PARAMS,
            )

            # Try external pipeline first
            external_pipeline_url = getattr(request.app.state.config, 'EXTERNAL_PIPELINE_URL', None)
            use_external = external_pipeline_url and external_pipeline_url.strip() != ''

            if use_external:
                try:
                    from open_webui.routers.external_retrieval import call_external_pipeline
                    docs = call_external_pipeline(
                        request=request,
                        file=file,
                        file_path=file_path,
                        loader_instance=loader,
                    )
                    # External pipeline returns pre-chunked docs
                    use_external = True
                except Exception as e:
                    log.warning(f'External pipeline failed for {file.filename}: {e}, falling back')
                    use_external = False

            if not use_external:
                docs = loader.load(file.filename, file.meta.get('content_type'), file_path)

            if not docs:
                return None, file, False

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

            text_content = ' '.join([doc.page_content for doc in docs])

            # Save extracted text to file record
            Files.update_file_data_by_id(file.id, {'content': text_content}, db=db)
            db.commit()

            return docs, file, not use_external  # needs_split=True for internal pipeline

    return await asyncio.to_thread(_extract_in_thread)
```

#### 2. New method: `_embed_to_collections()`

**File**: `backend/open_webui/services/sync/base_worker.py`

Embeds documents once and inserts vectors into both the KB collection and the per-file collection:

```python
async def _embed_to_collections(
    self,
    docs: List[Document],
    file_id: str,
    file_hash: str,
    filename: str,
    needs_split: bool = True,
) -> bool:
    """Embed documents once and insert into both KB and per-file collections.

    This replaces the double process_file call by generating embeddings once
    and writing the resulting vectors to both collections.
    """
    from open_webui.routers.retrieval import save_docs_to_vector_db

    request = self._make_request()
    user = self._get_user()

    metadata = {
        'file_id': file_id,
        'name': filename,
        'hash': file_hash,
    }

    # Embed + insert into KB collection (the primary target)
    def _save_to_kb():
        return save_docs_to_vector_db(
            request,
            docs=docs,
            collection_name=self.knowledge_id,
            metadata=metadata,
            add=True,
            split=needs_split,
            user=user,
        )

    result = await asyncio.to_thread(_save_to_kb)
    if not result:
        return False

    # Also save to per-file collection (needed for file viewer UI and cross-KB propagation)
    def _save_to_file_collection():
        return save_docs_to_vector_db(
            request,
            docs=docs,
            collection_name=f'file-{file_id}',
            metadata=metadata,
            overwrite=True,
            split=needs_split,
            user=user,
        )

    await asyncio.to_thread(_save_to_file_collection)

    # Update file metadata
    with get_db() as session:
        Files.update_file_metadata_by_id(
            file_id,
            {'collection_name': f'file-{file_id}'},
            db=session,
        )
        Files.update_file_data_by_id(file_id, {'status': 'completed'}, db=session)
        Files.update_file_hash_by_id(file_id, file_hash, db=session)

    return True
```

**Trade-off note**: This still embeds twice (once for KB, once for per-file). To truly embed once, we'd need to capture the generated embeddings from `save_docs_to_vector_db` and reuse them for the second insert. That requires modifying `save_docs_to_vector_db` to return the embedding vectors — a deeper change to a function that's used across the codebase.

**Better approach — embed once, insert twice**: Modify the method to call the embedding function directly, then use `VECTOR_DB_CLIENT.insert()` for both collections with the same vectors:

```python
async def _embed_to_collections(
    self,
    docs: List[Document],
    file_id: str,
    file_hash: str,
    filename: str,
    needs_split: bool = True,
) -> bool:
    """Embed documents once and insert vectors into both KB and per-file collections."""
    from open_webui.routers.retrieval import (
        get_embedding_function,
        sanitize_text_for_db,
        RAG_EMBEDDING_CONTENT_PREFIX,
        RAG_EMBEDDING_TIMEOUT,
    )
    from langchain_core.documents import Document as LCDocument
    from langchain.text_splitter import RecursiveCharacterTextSplitter, TokenTextSplitter

    request = self._make_request()
    user = self._get_user()

    metadata = {
        'file_id': file_id,
        'name': filename,
        'hash': file_hash,
    }

    def _split_and_embed():
        """Split documents and generate embeddings (runs in thread)."""
        working_docs = list(docs)

        # Split if needed (internal pipeline; external pipeline pre-chunks)
        if needs_split:
            if request.app.state.config.TEXT_SPLITTER in ['', 'character']:
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=request.app.state.config.CHUNK_SIZE,
                    chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                    add_start_index=True,
                )
                working_docs = splitter.split_documents(working_docs)
            elif request.app.state.config.TEXT_SPLITTER == 'token':
                splitter = TokenTextSplitter(
                    encoding_name=str(request.app.state.config.TIKTOKEN_ENCODING_NAME),
                    chunk_size=request.app.state.config.CHUNK_SIZE,
                    chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                    add_start_index=True,
                )
                working_docs = splitter.split_documents(working_docs)

        if not working_docs:
            return [], [], []

        texts = [sanitize_text_for_db(doc.page_content) for doc in working_docs]
        metadatas = [
            {
                **doc.metadata,
                **metadata,
                'embedding_config': {
                    'engine': request.app.state.config.RAG_EMBEDDING_ENGINE,
                    'model': request.app.state.config.RAG_EMBEDDING_MODEL,
                },
            }
            for doc in working_docs
        ]

        # Generate embeddings via main event loop
        embedding_function = get_embedding_function(
            request.app.state.config.RAG_EMBEDDING_ENGINE,
            request.app.state.config.RAG_EMBEDDING_MODEL,
            request.app.state.ef,
            (
                request.app.state.config.RAG_OPENAI_API_BASE_URL
                if request.app.state.config.RAG_EMBEDDING_ENGINE == 'openai'
                else (
                    request.app.state.config.RAG_OLLAMA_BASE_URL
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == 'ollama'
                    else request.app.state.config.RAG_AZURE_OPENAI_BASE_URL
                )
            ),
            (
                request.app.state.config.RAG_OPENAI_API_KEY
                if request.app.state.config.RAG_EMBEDDING_ENGINE == 'openai'
                else (
                    request.app.state.config.RAG_OLLAMA_API_KEY
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == 'ollama'
                    else request.app.state.config.RAG_AZURE_OPENAI_API_KEY
                )
            ),
            request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
            enable_async=request.app.state.config.ENABLE_ASYNC_EMBEDDING,
            concurrent_requests=request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
        )

        future = asyncio.run_coroutine_threadsafe(
            embedding_function(
                list(map(lambda x: x.replace('\n', ' '), texts)),
                prefix=RAG_EMBEDDING_CONTENT_PREFIX,
                user=user,
            ),
            request.app.state.main_loop,
        )
        embeddings = future.result(timeout=RAG_EMBEDDING_TIMEOUT)

        return texts, metadatas, embeddings

    texts, metadatas, embeddings = await asyncio.to_thread(_split_and_embed)

    if not texts:
        log.warning(f'No text content extracted from {filename}')
        with get_db() as session:
            Files.update_file_metadata_by_id(file_id, {'collection_name': f'file-{file_id}'}, db=session)
            Files.update_file_data_by_id(file_id, {'status': 'completed'}, db=session)
            Files.update_file_hash_by_id(file_id, file_hash, db=session)
        return True

    # Build vector items once
    import uuid
    items_kb = [
        {
            'id': str(uuid.uuid4()),
            'text': text,
            'vector': embeddings[idx],
            'metadata': metadatas[idx],
        }
        for idx, text in enumerate(texts)
    ]

    # Separate UUIDs for the per-file collection (vector DBs may require unique IDs)
    items_file = [
        {
            'id': str(uuid.uuid4()),
            'text': text,
            'vector': embeddings[idx],
            'metadata': metadatas[idx],
        }
        for idx, text in enumerate(texts)
    ]

    # Insert into KB collection
    VECTOR_DB_CLIENT.insert(collection_name=self.knowledge_id, items=items_kb)
    log.info(f'Inserted {len(items_kb)} vectors into KB {self.knowledge_id} for {file_id}')

    # Insert into per-file collection (overwrite if exists)
    file_collection = f'file-{file_id}'
    if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
        VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
    VECTOR_DB_CLIENT.insert(collection_name=file_collection, items=items_file)
    log.info(f'Inserted {len(items_file)} vectors into {file_collection}')

    # Update file metadata
    with get_db() as session:
        Files.update_file_metadata_by_id(file_id, {'collection_name': file_collection}, db=session)
        Files.update_file_data_by_id(file_id, {'status': 'completed'}, db=session)
        Files.update_file_hash_by_id(file_id, file_hash, db=session)

    return True
```

#### 3. Update `_process_and_embed()` to use the new methods

**File**: `backend/open_webui/services/sync/base_worker.py`

Replace the `_process_file_via_api()` call in `_process_and_embed()` (from Phase 2):

```python
async def _process_and_embed(self, prepared: PreparedFile) -> Optional[FailedFile]:
    """Phase 2: Extract content, embed once, insert into KB + per-file collections."""
    file_id = prepared.file_id
    name = prepared.name

    if self._check_cancelled():
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message='Sync cancelled by user',
        )

    try:
        # Extract content (loader / external pipeline)
        result = await self._extract_content(file_id)
        if result is None:
            log.debug(f'File {file_id} has no extractable content')
            return None

        docs, file_record, needs_split = result

        if not docs or not any(doc.page_content.strip() for doc in docs):
            log.debug(f'File {file_id} has no text content')
            return None

        if self._check_cancelled():
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Sync cancelled by user',
            )

        # Embed once → insert into both KB and per-file collections
        success = await self._embed_to_collections(
            docs=docs,
            file_id=file_id,
            file_hash=prepared.content_hash,
            filename=name,
            needs_split=needs_split,
        )

        if not success:
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Failed to save vectors',
            )

    except Exception as e:
        log.warning(f'Error processing file {file_id} ({name}): {e}')
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message=str(e)[:100],
        )

    if self._check_cancelled():
        return FailedFile(
            filename=name,
            error_type=SyncErrorType.PROCESSING_ERROR.value,
            error_message='Sync cancelled by user',
        )

    # KB association
    Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)

    # Cross-KB vector propagation (still uses process_file for other KBs)
    try:
        knowledge_files = Knowledges.get_knowledge_files_by_file_id(file_id)
        for kf in knowledge_files:
            if kf.knowledge_id != self.knowledge_id:
                log.info(f'Propagating vectors for {file_id} to KB {kf.knowledge_id}')
                try:
                    VECTOR_DB_CLIENT.delete(
                        collection_name=kf.knowledge_id,
                        filter={'file_id': file_id},
                    )
                except Exception as e:
                    log.warning(f'Failed to remove old vectors from KB {kf.knowledge_id}: {e}')
                try:
                    from open_webui.routers.retrieval import process_file, ProcessFileForm

                    def _call_propagate(form_data):
                        with get_db() as db:
                            return process_file(
                                self._make_request(), form_data, user=self._get_user(), db=db,
                            )

                    await asyncio.to_thread(
                        _call_propagate,
                        ProcessFileForm(file_id=file_id, collection_name=kf.knowledge_id),
                    )
                except Exception as e:
                    log.warning(f'Failed to propagate vectors to KB {kf.knowledge_id}: {e}')
    except Exception as e:
        log.warning(f'Failed to propagate vector updates for {file_id}: {e}')

    # Emit file added event
    file_record = Files.get_file_by_id(file_id)
    if file_record:
        await emit_file_added(
            self.event_prefix,
            user_id=self.user_id,
            knowledge_id=self.knowledge_id,
            file_data={
                'id': file_record.id,
                'filename': file_record.filename,
                'meta': file_record.meta,
                'created_at': file_record.created_at,
                'updated_at': file_record.updated_at,
            },
        )

    return None
```

#### 4. Remove `_process_file_via_api()`

The old `_process_file_via_api()` method (lines 379-451) is no longer called. Delete it.

#### 5. Handle external pipeline integration

**File**: `backend/open_webui/routers/external_retrieval.py`

The `call_external_pipeline` function (line 247) currently returns chunked `Document` objects after calling the external `/chunk` endpoint. The `_extract_content` method needs to call this function. Check if `call_external_pipeline` can be called without `save_docs_to_vector_db_func` — it currently receives it as a parameter but we don't want it to embed.

Looking at the code, `call_external_pipeline` (line ~144) does:

1. Load file via Loader
2. Send to external pipeline's `/chunk` endpoint
3. Return chunked documents

This is already what we need — it returns documents without embedding. The `process_file_with_external_pipeline` function (line 195) is the one that calls both `call_external_pipeline` AND `save_docs_to_vector_db`. We only need the former.

### Success Criteria

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] Python syntax check passes
- [ ] Backend starts without errors
- [ ] No regressions in existing sync behaviour

#### Manual Verification:

- [ ] Sync a Google Drive folder — files are searchable in the KB
- [ ] Verify per-file collections exist (file viewer UI works)
- [ ] Verify embedding is called once per file (check logs: should see one "generating embeddings" per file, not two)
- [ ] Re-sync (hash match path) still works
- [ ] File in multiple KBs: cross-KB propagation works
- [ ] External pipeline integration still works (if configured)
- [ ] Cancellation works during extraction and embedding phases

**Implementation Note**: This phase is the most complex and has the most integration points. Test thoroughly before deploying.

---

## Performance Projections

| Scenario                                 | Current | Phase 1                         | + Phase 2 | + Phase 3 |
| ---------------------------------------- | ------- | ------------------------------- | --------- | --------- |
| 1700 files, flat folder, concurrent=20   | ~14 min | ~14 min (no nesting)            | ~7 min    | ~4 min    |
| 1700 files, 50 subfolders, concurrent=20 | ~15 min | ~14 min (saves ~1min discovery) | ~8 min    | ~4.5 min  |
| 1700 files, flat folder, concurrent=30   | ~9 min  | ~9 min                          | ~5 min    | ~3 min    |
| 250 files, re-sync (all hash match)      | ~2 min  | ~2 min                          | ~30s      | ~30s      |

Assumptions: 1s download, 0.5s S3 upload, 0.5s extraction, 1.5s embedding, 1.5s re-embedding (Phase 3 eliminates this).

## Testing Strategy

### Unit Tests:

- `_download_and_store()` with mock cloud client: verify hash-match skip, S3 upload, file record creation
- `_process_and_embed()` with mock Loader + vector DB: verify single embedding call, dual collection insert
- Parallel BFS with mock `list_folder_children`: verify all files discovered, folder map correct

### Integration Tests:

- Full sync cycle with a test Google Drive folder
- Cancel mid-sync — verify partial results are clean
- Re-sync same folder — verify hash-match path works
- Sync folder with unsupported file types — verify they're skipped

### Manual Testing Steps:

1. Deploy to staging with `FILE_PROCESSING_MAX_CONCURRENT=20`
2. Sync the Vink Bouw KB (1700 files) from Google Drive
3. Verify all files appear in KB, search returns relevant results
4. Check timing in logs — confirm < 5 minute total
5. Re-sync the same KB — should complete in < 1 minute (all hash matches)
6. Test with OneDrive KB to ensure no regression (OneDrive uses same base_worker pipeline)

## Migration Notes

- No database migrations required
- No breaking changes to the sync metadata format
- The `PreparedFile` dataclass is internal to `base_worker.py`
- Helm config change (`FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER`) is optional — defaults to 3
- Existing KBs will benefit immediately on next sync cycle

## References

- `backend/open_webui/services/sync/base_worker.py` — Core sync orchestration
- `backend/open_webui/services/google_drive/sync_worker.py` — Google Drive BFS
- `backend/open_webui/routers/retrieval.py:1405-1612` — `save_docs_to_vector_db`
- `backend/open_webui/routers/retrieval.py:1621-1989` — `process_file`
- `backend/open_webui/routers/retrieval.py:2799-2897` — `process_files_batch` (reference for batch pattern)
- `backend/open_webui/routers/external_retrieval.py` — External pipeline integration
- `backend/open_webui/retrieval/utils.py:775-856` — Embedding function internals
- `helm/open-webui-tenant/values.yaml` — Helm config defaults
