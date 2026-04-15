# Remove Sync Batch Wait — Continuous Pipeline

## Overview

Remove the batch-and-wait loop from the cloud sync pipeline in `base_worker.py`. The current implementation groups files into batches and waits for the entire batch to finish before starting the next one. Since semaphores already control concurrency, the batch boundary is redundant and adds tail-latency waste (one slow file blocks the whole batch). Now that Weaviate runs on block storage instead of NFS, this wait is no longer needed as a safety valve.

Also bump the Open WebUI pod CPU limit from 1 to 2 cores, which increases the thread pool and allows more concurrent file processing.

## Current State Analysis

**File:** `backend/open_webui/services/sync/base_worker.py`
**Branch:** `feat/vink` @ `1d5f98208`

The `sync()` method (line 1063) processes files in a two-phase pipeline:

1. **Phase 1 — Download** (under `download_semaphore`, default 9 on 1-CPU)
2. **Phase 2 — Extract + Embed** (under `process_semaphore`, default 3 on 1-CPU)

Files are grouped into batches of `max_download_concurrent + max_process_concurrent` (12 on 1-CPU) and processed via `asyncio.gather`. The batch boundary at line 1371 means:

- Within a batch: good pipeline overlap via semaphores
- Between batches: hard wait — batch N+1 doesn't start until every file in batch N finishes

**Concurrency on 1-CPU pod (current):**

- `thread_pool_size` = min(32, 1+4) = 5
- `max_process_concurrent` = min(10, 3) = **3**
- `max_download_concurrent` = 3 × 3 = **9**
- `batch_size` = 12

**Concurrency on 2-CPU pod (after change):**

- `thread_pool_size` = min(32, 2+4) = 6
- `max_process_concurrent` = min(10, 4) = **4**
- `max_download_concurrent` = 4 × 3 = **12**
- No batch boundary

This applies to both OneDrive and Google Drive — both inherit from `BaseSyncWorker`.

## Desired End State

- All files are launched as concurrent tasks in a single `asyncio.gather` call
- Semaphores remain the sole concurrency control mechanism
- No batch-boundary tail latency
- Pod runs on 2 CPUs, giving 4 concurrent process slots instead of 3
- All existing features (cancellation, timeouts, progress reporting, error handling) preserved

### How to verify:

1. Sync a KB with 50+ files — observe continuous processing in logs (no "BATCH X/Y START/END" gaps)
2. Compare total sync time against pre-change baseline
3. Cancellation still works mid-sync
4. Per-file timeout (600s) still fires for stuck files

## What We're NOT Doing

- Not changing the semaphore values or concurrency multiplier
- Not changing the per-file timeout
- Not changing the download or process logic
- Not touching the provider-specific sync workers (OneDrive/Google Drive)

## Implementation Approach

Replace the batch loop with a single `asyncio.gather` over all files. Keep everything else identical: semaphores, timeouts, cancellation checks, progress reporting.

## Phase 1: Remove Batch Loop

### Overview

Replace the batch-for-loop with a single gather. Remove batch logging since it no longer applies.

### Changes Required:

#### 1. `backend/open_webui/services/sync/base_worker.py`

**Replace lines 1346–1391** (the batch loop and result collection):

**Current code:**

```python
# Process files in batches to avoid overwhelming the event loop
# and thread pool. Each batch runs with the existing semaphores
# for download/process concurrency within the batch.
batch_size = max_download_concurrent + max_process_concurrent
log.info(
    f'Starting pipeline processing of {len(all_files_to_process)} files '
    f'(thread pool: {thread_pool_size}, '
    f'download concurrency: {max_download_concurrent}, '
    f'process concurrency: {max_process_concurrent}, '
    f'batch size: {batch_size})'
)
start_time = time.time()

total_batches = (len(all_files_to_process) + batch_size - 1) // batch_size

for batch_start in range(0, len(all_files_to_process), batch_size):
    if cancelled or self._check_cancelled():
        cancelled = True
        break

    batch_num = batch_start // batch_size + 1
    batch = all_files_to_process[batch_start : batch_start + batch_size]
    log.info(f'>>> BATCH {batch_num}/{total_batches} START ({len(batch)} files, offset {batch_start})')
    batch_t0 = time.time()
    batch_tasks = [pipeline(file_info, batch_start + i) for i, file_info in enumerate(batch)]
    batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
    log.info(
        f'<<< BATCH {batch_num}/{total_batches} END '
        f'({time.time() - batch_t0:.1f}s, '
        f'processed={processed_count}, failed={failed_count})'
    )

    for result in batch_results:
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
```

**New code:**

```python
log.info(
    f'Starting pipeline processing of {len(all_files_to_process)} files '
    f'(thread pool: {thread_pool_size}, '
    f'download concurrency: {max_download_concurrent}, '
    f'process concurrency: {max_process_concurrent})'
)
start_time = time.time()

all_tasks = [pipeline(file_info, i) for i, file_info in enumerate(all_files_to_process)]
all_results = await asyncio.gather(*all_tasks, return_exceptions=True)

for result in all_results:
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
```

Also remove the now-unused import at line 1189:

```python
from open_webui.config import FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER
```

And the `batch_size` calculation that used it. The `max_download_concurrent` calculation changes to use the multiplier directly:

```python
max_download_concurrent = max_process_concurrent * 3  # download slots per process slot
```

Wait — `FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER` is still used for `max_download_concurrent`. Keep the import, just remove `batch_size`.

#### 2. Remove `batch_size` variable

The only use of `batch_size` was the batch loop. Remove the line:

```python
batch_size = max_download_concurrent + max_process_concurrent
```

### Success Criteria:

#### Automated Verification:

- [ ] Backend starts without errors: `open-webui dev`
- [ ] No Python syntax errors: `python -m py_compile backend/open_webui/services/sync/base_worker.py`

#### Manual Verification:

- [ ] Sync a KB with 20+ files — logs show continuous processing, no batch boundaries
- [ ] Sync completes successfully with correct file counts
- [ ] Cancel a sync mid-progress — cancellation still works
- [ ] A stuck file times out after 600s without blocking others

---

## Phase 2: Bump Pod CPU to 2 Cores

### Overview

Increase the Open WebUI pod CPU limit from 1000m to 2000m and request from 200m to 500m. This doubles the thread pool size (5→6), allowing 4 concurrent process tasks instead of 3.

### Changes Required:

#### 1. `helm/open-webui-tenant/values.yaml`

**Change** (lines 74-80):

```yaml
resources:
  requests:
    memory: '512Mi'
    cpu: '500m'
  limits:
    memory: '2Gi'
    cpu: '2000m'
```

### Success Criteria:

#### Automated Verification:

- [ ] `helm template` renders without errors

#### Manual Verification:

- [ ] Pod schedules and starts on the cluster with new limits
- [ ] Sync performance improves (measure sync time for a known KB)

---

## Testing Strategy

### Before/After Comparison:

1. Pick a KB with 30-50 files
2. Trigger a full re-sync (clear delta links) with current code — note total time
3. Deploy changes, repeat — note total time
4. Expect improvement proportional to the batch tail-latency elimination

### Edge Cases:

- KB with 1 file (no batching effect, should still work)
- KB at file limit (250) — ensure all files still process
- Mixed fast/slow files — verify slow files don't block fast ones

## Performance Considerations

The main risk of removing batching is memory: all file tasks exist simultaneously instead of batch-by-batch. Each task is lightweight (an asyncio coroutine + semaphore wait) — the actual heavy work (downloads, threads) is still bounded by semaphores. For 250 files (the max), this means ~250 coroutines instead of ~12, which is negligible.

## References

- `backend/open_webui/services/sync/base_worker.py` — lines 1189-1391 (current batch logic)
- `helm/open-webui-tenant/values.yaml` — lines 74-80 (resource limits)
- NFS → block storage migration was the original root cause of slow syncs
