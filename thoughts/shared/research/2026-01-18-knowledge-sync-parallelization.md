---
date: 2026-01-18T12:00:00+01:00
researcher: Claude
git_commit: e662a9946c75500b14dc7ed0e3486f064556caa4
branch: feat/onedrive
repository: open-webui
topic: "Parallelizing Knowledge Base Document Sync for All Sources"
tags: [research, codebase, parallelization, knowledge-base, onedrive, sync]
status: complete
last_updated: 2026-01-18
last_updated_by: Claude
---

# Research: Parallelizing Knowledge Base Document Sync for All Sources

**Date**: 2026-01-18T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: e662a9946c75500b14dc7ed0e3486f064556caa4
**Branch**: feat/onedrive
**Repository**: open-webui

## Research Question

How can we parallelize knowledge base document syncing for all sources (filesystem files/folders and OneDrive) instead of processing documents one-by-one?

## Summary

The current implementation processes files **sequentially** in both the OneDrive sync worker and the standard file upload flow. The codebase already has robust parallelization patterns (`asyncio.gather`, `asyncio.Semaphore`, `ThreadPoolExecutor`) that can be applied to achieve significant speedups. The key insight is that document processing has three independent phases that can be parallelized:

1. **Download/Collection** - Getting file content (OneDrive API or filesystem)
2. **Content Extraction** - Parsing documents (loaders)
3. **Vector DB Insertion** - Embedding and storing

The recommended approach is to implement a **unified parallel processor** that works identically for all sources by introducing a common abstraction layer.

## Detailed Findings

### Current Implementation: Sequential Processing

#### OneDrive Sync Worker (`sync_worker.py:446-481`)

Files are processed one-by-one in a sequential loop:

```python
# Process all files
for i, file_info in enumerate(all_files_to_process):
    await self._update_sync_status("syncing", i + 1, total_files, file_info["name"])
    result = await self._process_file_info(file_info)  # Sequential await
    if result is None:
        total_processed += 1
    else:
        total_failed += 1
        failed_files.append(result)
```

Each file goes through two API calls sequentially (`sync_worker.py:690-824`):
1. Extract content: `POST /api/v1/retrieval/process/file` (no collection_name)
2. Add to KB: `POST /api/v1/retrieval/process/file` (with collection_name)

#### Standard File Upload (`files.py:155-271`)

Files uploaded via HTTP are processed via `BackgroundTasks`, but each file is handled independently without batching.

#### Knowledge Base Reindex (`knowledge.py:214-254`)

```python
for file in files:
    await run_in_threadpool(
        process_file,
        request,
        ProcessFileForm(file_id=file.id, collection_name=knowledge_base.id),
        user=user,
    )
```

### Existing Parallelization Patterns

The codebase has several patterns we can leverage:

#### 1. asyncio.gather with Semaphore (`retrieval.py:2249-2279`)

Used for web search - configurable concurrency control:

```python
if concurrent_limit:
    semaphore = asyncio.Semaphore(concurrent_limit)

    async def search_with_limit(query):
        async with semaphore:
            return await run_in_threadpool(search_web, ...)

    search_tasks = [search_with_limit(query) for query in queries]
else:
    search_tasks = [...]  # Unlimited

search_results = await asyncio.gather(*search_tasks)
```

#### 2. Mistral Loader Batch Processing (`retrieval/loaders/mistral.py:703-734`)

Perfect example of controlled parallel document processing:

```python
@staticmethod
async def load_multiple_async(
    loaders: List["MistralLoader"],
    max_concurrent: int = 5,
) -> List[List[Document]]:
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_with_semaphore(loader):
        async with semaphore:
            return await loader.load_async()

    tasks = [process_with_semaphore(loader) for loader in loaders]
    results = await asyncio.gather(*tasks, return_exceptions=True)
```

#### 3. Batch Vector DB Insertion (`retrieval.py:2678-2688`)

Already exists - collects all docs and inserts in one call:

```python
# Save all documents in one batch
if all_docs:
    await run_in_threadpool(
        save_docs_to_vector_db,
        request,
        all_docs,
        collection_name,
        add=True,
        user=user,
    )
```

### Bottlenecks Identified

| Phase | Current | Bottleneck |
|-------|---------|------------|
| Download | Sequential | OneDrive API allows parallel requests |
| Content Extraction | Sequential | CPU-bound but parallelizable |
| Embedding Generation | Batched internally | Already efficient |
| Vector DB Insert | Per-file | Could batch across files |

## Proposed Implementation

### Architecture: Unified Parallel Document Processor

Create a source-agnostic processor that handles both OneDrive and filesystem sources:

```python
# backend/open_webui/services/document_sync/parallel_processor.py

@dataclass
class DocumentSource:
    """Abstract representation of a document source."""
    id: str
    name: str
    source_type: str  # "onedrive" | "filesystem" | "upload"
    fetch_content: Callable[[], Awaitable[bytes]]  # How to get content
    metadata: Dict[str, Any]


class ParallelDocumentProcessor:
    def __init__(
        self,
        knowledge_id: str,
        user_id: str,
        max_concurrent_downloads: int = 5,
        max_concurrent_processing: int = 3,
        event_emitter: Optional[Callable] = None,
    ):
        self.knowledge_id = knowledge_id
        self.user_id = user_id
        self.download_semaphore = asyncio.Semaphore(max_concurrent_downloads)
        self.processing_semaphore = asyncio.Semaphore(max_concurrent_processing)
        self.event_emitter = event_emitter

    async def process_sources(
        self, sources: List[DocumentSource]
    ) -> ProcessingResult:
        """Process all sources in parallel with controlled concurrency."""

        # Phase 1: Download all content in parallel
        download_tasks = [
            self._download_with_limit(source)
            for source in sources
        ]
        downloaded = await asyncio.gather(*download_tasks, return_exceptions=True)

        # Phase 2: Extract content in parallel
        extraction_tasks = [
            self._extract_with_limit(item)
            for item in downloaded if not isinstance(item, Exception)
        ]
        extracted = await asyncio.gather(*extraction_tasks, return_exceptions=True)

        # Phase 3: Batch insert to vector DB
        all_docs = []
        for result in extracted:
            if not isinstance(result, Exception):
                all_docs.extend(result.documents)

        await self._batch_insert(all_docs)

        return ProcessingResult(...)

    async def _download_with_limit(self, source: DocumentSource):
        async with self.download_semaphore:
            return await source.fetch_content()

    async def _extract_with_limit(self, downloaded_item):
        async with self.processing_semaphore:
            return await self._extract_content(downloaded_item)
```

### OneDrive Integration

Update `OneDriveSyncWorker` to use the unified processor:

```python
# sync_worker.py - Updated sync method

async def sync(self) -> Dict[str, Any]:
    # ... setup ...

    # Collect sources (same as current)
    all_files_to_process = []
    for source in self.sources:
        if source.get("type") == "folder":
            files, deleted = await self._collect_folder_files(source)
            all_files_to_process.extend(files)
        else:
            file_info = await self._collect_single_file(source)
            if file_info:
                all_files_to_process.append(file_info)

    # Convert to DocumentSource objects
    document_sources = [
        DocumentSource(
            id=f"onedrive-{info['item']['id']}",
            name=info['name'],
            source_type="onedrive",
            fetch_content=lambda i=info: self._client.download_file(
                i['drive_id'], i['item']['id']
            ),
            metadata={
                "drive_id": info['drive_id'],
                "item_id": info['item']['id'],
            }
        )
        for info in all_files_to_process
    ]

    # Process in parallel using unified processor
    processor = ParallelDocumentProcessor(
        knowledge_id=self.knowledge_id,
        user_id=self.user_id,
        max_concurrent_downloads=5,
        max_concurrent_processing=3,
        event_emitter=self._update_sync_status,
    )

    result = await processor.process_sources(document_sources)
    return result
```

### Filesystem/Upload Integration

Create filesystem source adapter:

```python
# backend/open_webui/services/document_sync/sources/filesystem.py

async def create_filesystem_sources(
    file_paths: List[str],
    user_id: str
) -> List[DocumentSource]:
    """Create DocumentSource objects from filesystem paths."""
    sources = []

    for path in file_paths:
        async def fetch(p=path):
            async with aiofiles.open(p, 'rb') as f:
                return await f.read()

        sources.append(DocumentSource(
            id=str(uuid.uuid4()),
            name=os.path.basename(path),
            source_type="filesystem",
            fetch_content=fetch,
            metadata={"path": path}
        ))

    return sources
```

### Configuration

Add configurable concurrency limits:

```python
# config.py

DOCUMENT_SYNC_MAX_CONCURRENT_DOWNLOADS = PersistentConfig(
    "DOCUMENT_SYNC_MAX_CONCURRENT_DOWNLOADS",
    "document_sync.max_concurrent_downloads",
    int(os.getenv("DOCUMENT_SYNC_MAX_CONCURRENT_DOWNLOADS", "5")),
)

DOCUMENT_SYNC_MAX_CONCURRENT_PROCESSING = PersistentConfig(
    "DOCUMENT_SYNC_MAX_CONCURRENT_PROCESSING",
    "document_sync.max_concurrent_processing",
    int(os.getenv("DOCUMENT_SYNC_MAX_CONCURRENT_PROCESSING", "3")),
)
```

### Progress Reporting

Maintain real-time progress updates during parallel processing:

```python
async def process_sources(self, sources: List[DocumentSource]):
    total = len(sources)
    completed = 0

    async def track_progress(coro, source):
        nonlocal completed
        result = await coro
        completed += 1
        await self._emit_progress(completed, total, source.name)
        return result

    tasks = [
        track_progress(self._process_source(s), s)
        for s in sources
    ]

    return await asyncio.gather(*tasks, return_exceptions=True)
```

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:446-481` - Current sequential loop
- `backend/open_webui/services/onedrive/sync_worker.py:560-688` - Per-file processing
- `backend/open_webui/routers/retrieval.py:2249-2279` - Semaphore pattern for web search
- `backend/open_webui/retrieval/loaders/mistral.py:703-734` - `load_multiple_async` example
- `backend/open_webui/routers/retrieval.py:2625-2705` - Batch processing endpoint
- `backend/open_webui/routers/retrieval.py:1352-1557` - `save_docs_to_vector_db` function
- `backend/open_webui/routers/files.py:155-271` - File upload handler
- `backend/open_webui/config.py:2458-2490` - OneDrive configuration

## Architecture Insights

### Why Unified Processing?

1. **Code Reuse**: Same parallel logic for all sources
2. **Consistent Behavior**: Same concurrency limits, error handling, progress reporting
3. **Easier Testing**: Test the processor independently of sources
4. **Future-Proof**: Easy to add new sources (Google Drive, Dropbox, S3)

### Recommended Concurrency Limits

| Phase | Recommended Limit | Rationale |
|-------|------------------|-----------|
| Downloads | 5-10 | API rate limits, network bandwidth |
| Content Extraction | 2-4 | CPU-intensive, memory usage |
| Vector DB Insert | 1 (batched) | Single batch is most efficient |

### Error Handling Strategy

- Use `return_exceptions=True` with `asyncio.gather` to continue on failures
- Collect failed files separately for reporting
- Allow partial success (process what we can, report failures)

## Open Questions

1. **Cancellation Handling**: How should we handle cancellation during parallel processing? Current implementation checks between files, but with parallel processing we need a different approach (e.g., cancellation tokens).

2. **Memory Management**: With parallel downloads, we may have multiple file contents in memory. Should we stream directly to storage instead of buffering?

3. **Progress Granularity**: With parallel processing, progress is non-linear. Should we report "X/Y completed" or "X% overall"?

4. **Embedding API Rate Limits**: If using external embedding APIs (OpenAI), we may need to add rate limiting at the embedding level as well.

5. **Database Connection Pooling**: With parallel processing, we may need more database connections. Is the current pool size adequate?
