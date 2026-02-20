---
date: 2026-02-14T12:00:00+01:00
researcher: claude
git_commit: 88fb9d65424b996df9cef966ecea282012b03d82
branch: feat/sync-improvements
repository: open-webui
topic: "OneDrive file deduplication across users and background sync completeness"
tags: [research, codebase, onedrive, dedup, sync, vector-db]
status: complete
last_updated: 2026-02-14
last_updated_by: claude
---

# Research: OneDrive File Dedup Across Users & Sync Status

**Date**: 2026-02-14
**Git Commit**: 88fb9d65
**Branch**: feat/sync-improvements

## Research Question

When multiple users sync the same OneDrive file, are we loading, parsing, chunking, and embedding it each time? How does airweave handle this? Is our background sync up to spec?

## Summary

**Cross-user dedup is already implemented.** The second user syncing the same OneDrive file will still download it (to verify the hash), but if the hash matches the existing file record, parsing/chunking/embedding are skipped entirely. Vectors are copied from the per-file collection into the new user's KB collection. The background sync system is comprehensive with delta queries, cancellation, error handling, and token refresh.

## Detailed Findings

### Q1: Are we re-processing the same file for each user?

**No — there are 3 layers of deduplication already in place.**

**Layer 1: OneDrive-side hash (pre-download skip)**
- For single-file sources only (`sync_worker.py:296-316`)
- Compares OneDrive's `sha256Hash`/`quickXorHash` against stored `source.content_hash`
- If match + file record completed → **file skipped entirely, no download**

**Layer 2: Content hash after download (cross-user dedup)**
- `sync_worker.py:932-973`
- Computes `hashlib.sha256(content).hexdigest()` on downloaded bytes
- File ID is deterministic: `onedrive-{item_id}` — same OneDrive file = same ID regardless of user
- Lookup is global (not user-scoped): `Files.get_file_by_id(file_id)`
- If existing record has matching hash:
  - Creates `KnowledgeFile` association (line 942-943)
  - Copies vectors via `_ensure_vectors_in_kb()` (line 946) — no re-parsing/chunking/embedding
  - Returns immediately

**Layer 3: Text hash in vector DB**
- `retrieval.py:1382-1396`
- Before chunking/embedding, queries target collection for documents with same hash
- If found, returns True (idempotent skip)

**What still happens for each user:**
- Download (to compute hash for Layer 2 comparison)
- Vector copy from per-file collection → per-KB collection

### Q2: Vector Storage Architecture

Two-tier collection structure enables the dedup:

| Collection | Name Format | Purpose |
|-----------|------------|---------|
| Per-file | `file-onedrive-{item_id}` | Canonical vectors, shared across users |
| Per-KB | `{knowledge_base_uuid}` | Copies of vectors for that KB's files |

When a file updates, vectors propagate to ALL KBs referencing it (`sync_worker.py:1052-1087`).

### Q3: Airweave Comparison

Airweave directory not checked out locally. Patterns adopted from airweave into our implementation:

| Pattern | Origin | Our Implementation |
|---------|--------|--------------------|
| Token refresh with buffer | `platform/sync/token_manager.py` | `token_refresh.py:30-64` (5-min buffer) |
| 401 retry with token refresh | `platform/sources/onedrive.py` | `graph_client.py:35-104` (single retry) |
| Delta sync cursor persistence | `soev-rag/graph_client.py` | `sync_worker.py:246-260` + `_save_sources()` |

Novel additions beyond airweave patterns:
- Deterministic `onedrive-{item_id}` file IDs for cross-user dedup
- `KnowledgeFile` junction table for many-to-many KB↔file relationships
- `_ensure_vectors_in_kb()` for vector copy without re-embedding
- Parallel processing with `asyncio.Semaphore`

### Q4: Background Sync Status — Is It Up to Spec?

**Yes.** The system is comprehensive:

- **Trigger**: Scheduler (`scheduler.py`) runs on `ONEDRIVE_SYNC_INTERVAL_MINUTES` (default 60)
- **Incremental**: Delta queries for folder sources, hash comparison for file sources
- **Cancellation**: Cooperative polling — cancel endpoint sets status, worker checks before each file
- **Error handling**: 401 (token refresh), 404/403 (access revoked), 410 (delta expired → full resync), 429 (rate limit), 5xx (exponential backoff)
- **Token refresh**: Background syncs use `OneDriveTokenManager` with automatic refresh
- **Progress**: Dual channel — Socket.IO (push) + HTTP polling (fallback)
- **Permission sync**: OneDrive folder permissions mapped to Open WebUI users
- **Vector propagation**: Updated files propagate to all referencing KBs

## What's NOT Deduplicated

1. **Download**: The file is always downloaded for hash verification (Layer 2). Layer 1 skips downloads only for single-file sources.
2. **Different files with identical content**: Dedup keys on `onedrive-{item_id}`, not content hash alone. Two different OneDrive files with identical content are processed independently.
3. **Manual uploads vs OneDrive**: A file uploaded manually (`file-{uuid}`) and the same file synced from OneDrive (`onedrive-{item_id}`) are treated as separate files.

## Recommendation

The current dedup is solid for the same OneDrive file across users. The "Phase 3 dedup" from the typed-KB plan was about broader content-hash-based dedup (matching different files with identical content), which is a separate concern and can wait until scale demands it. The download overhead is minimal compared to embedding costs, and the current architecture correctly avoids redundant embedding.

## Code References

- `backend/open_webui/services/onedrive/sync_worker.py:932-973` — Core cross-user dedup logic
- `backend/open_webui/services/onedrive/sync_worker.py:1116-1149` — `_ensure_vectors_in_kb()`
- `backend/open_webui/services/onedrive/scheduler.py:59-75` — Background scheduler loop
- `backend/open_webui/services/onedrive/graph_client.py:35-104` — HTTP client with retry
- `backend/open_webui/services/onedrive/token_refresh.py:30-64` — Token refresh
- `backend/open_webui/routers/retrieval.py:1382-1396` — Vector DB hash dedup
- `backend/open_webui/models/files.py:20` — `hash` column on File model
- `backend/open_webui/models/knowledge.py:92-110` — KnowledgeFile junction table
