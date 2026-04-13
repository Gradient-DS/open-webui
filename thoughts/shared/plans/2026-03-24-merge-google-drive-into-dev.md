# Merge Feat/google-drive-integration into dev — Implementation Plan

## Overview

Merge the Google Drive integration branch (which includes the shared sync abstraction layer) into `dev`, preserving dev's bug fixes (DB session management, access_grants format, frontend race conditions) while keeping the new abstraction architecture. Final phase produces a standalone integration cookbook.

## Current State Analysis

**This branch** (8 commits): Google Drive backend + frontend, `services/sync/` abstraction layer extracting shared logic from OneDrive into `BaseSyncWorker`, `SyncProvider`, shared router/scheduler/events/token_refresh.

**Dev branch** (14 commits): CI/CD workflows, OneDrive bug fixes (`get_db()` wrapping, `access_grants` format), KB deletion fixes, KnowledgeBase.svelte UI fixes (fetch race condition, batched toasts, parallel uploads), Black formatting, feature flags, security hardening.

### Key Discoveries:

- Dev's `sync_worker.py` has 3 critical fixes that must be ported to `base_worker.py`:
  1. `get_db()` context manager wrapping all `process_file` calls (`base_worker.py:344`, `base_worker.py:393-432`, `base_worker.py:656-666`)
  2. `access_grants` list format replacing `access_control` dict in `_sync_permissions` (affects both `onedrive/sync_worker.py` and `google_drive/sync_worker.py`)
  3. `type=knowledge.type` passed to `KnowledgeForm` in permission sync
- Dev's `KnowledgeBase.svelte` has substantial UX fixes (fetchId race guard, socket file:status handler, parallel uploads) that overlap with this branch's Google Drive UI additions
- Most OneDrive service files on dev only have Black formatting changes (no logic changes)

## Desired End State

After this plan:

1. `dev` branch has the full sync abstraction layer + Google Drive integration
2. All of dev's bug fixes are preserved and applied to the shared abstraction
3. Both OneDrive and Google Drive sync work correctly with the new `access_grants` format
4. Frontend has both Google Drive UI and dev's UX improvements
5. A standalone cookbook document exists for adding future integrations

### Verification:

- Both providers sync manually and on schedule
- `access_grants` format is used everywhere (not old `access_control` dict)
- `process_file` calls all use `get_db()` context managers
- Frontend doesn't flicker, uploads work in parallel
- `npm run build` succeeds
- Backend starts without import errors

## What We're NOT Doing

- Refactoring the frontend sync handlers into a shared abstraction (deferred — tracked in `thoughts/shared/plans/2026-03-24-cloud-sync-abstraction-refactor.md`)
- Changing the database schema or migrations
- Adding new providers (that's what the cookbook is for)
- Fixing pre-existing lint/check errors (~8000+ svelte-check, ~9600 lint)

## Implementation Approach

**Pre-merge fix-forward**: Apply dev's critical fixes to the abstraction layer BEFORE merging, which reduces the merge conflict surface. Then merge dev, resolve remaining textual conflicts, verify, and write the cookbook.

---

## Phase 1: Port dev's `get_db()` Fix to `base_worker.py`

### Overview

Dev discovered that `process_file` calls from background sync workers need an explicit DB session via `get_db()`. This branch's `base_worker.py` extracted those calls but didn't include the fix. We port it now.

### Changes Required:

#### 1. Add `get_db` import to `base_worker.py`

**File**: `backend/open_webui/services/sync/base_worker.py`
**Line**: Add to imports (around line 8)

```python
from open_webui.internal.db import get_db
```

#### 2. Wrap `_ensure_vectors_in_kb` with `get_db()`

**File**: `backend/open_webui/services/sync/base_worker.py`
**Lines**: 338-351

Change the direct `process_file()` call to use a DB session:

```python
# Before (current):
process_file(
    self._make_request(),
    ProcessFileForm(
        file_id=file_id,
        collection_name=self.knowledge_id,
    ),
    user=self._get_user(),
)

# After (with get_db):
with get_db() as db:
    process_file(
        self._make_request(),
        ProcessFileForm(
            file_id=file_id,
            collection_name=self.knowledge_id,
        ),
        user=self._get_user(),
        db=db,
    )
```

#### 3. Wrap `_process_file_via_api` with `get_db()`

**File**: `backend/open_webui/services/sync/base_worker.py`
**Lines**: 382-457

Replace the direct `process_file` calls with a wrapper that provides a DB session:

```python
async def _process_file_via_api(self, file_id: str, filename: str) -> Optional[FailedFile]:
    """Process file by calling the retrieval processing function directly."""
    from open_webui.routers.retrieval import process_file, ProcessFileForm
    from fastapi import HTTPException

    request = self._make_request()
    user = self._get_user()

    def _call_process_file(form_data):
        """Wrapper that provides a fresh DB session for direct process_file calls."""
        with get_db() as db:
            return process_file(request, form_data, user=user, db=db)

    try:
        # Step 1: Process file content
        try:
            await asyncio.to_thread(
                _call_process_file,
                ProcessFileForm(file_id=file_id),
            )
            # ... rest stays the same
```

Apply the same pattern to Step 2 (KB collection addition) — replace `process_file` with `_call_process_file`.

#### 4. Wrap vector propagation with `get_db()`

**File**: `backend/open_webui/services/sync/base_worker.py`
**Lines**: 656-666

```python
# Before:
await asyncio.to_thread(
    process_file,
    self._make_request(),
    ProcessFileForm(
        file_id=file_id,
        collection_name=kf.knowledge_id,
    ),
    user=self._get_user(),
)

# After:
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
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts without import errors: `cd backend && python -c "from open_webui.services.sync.base_worker import BaseSyncWorker; print('OK')"`
- [x] No syntax errors: `python -m py_compile backend/open_webui/services/sync/base_worker.py`

#### Manual Verification:

- [ ] Trigger a OneDrive sync — files process without DB session errors in logs
- [ ] Trigger a Google Drive sync — files process without DB session errors in logs

**Implementation Note**: After completing this phase, proceed to Phase 2.

---

## Phase 2: Port dev's `access_grants` Fix

### Overview

Dev changed the permission sync format from the old `access_control` dict to the new `access_grants` list format. Both `onedrive/sync_worker.py` and `google_drive/sync_worker.py` implement `_sync_permissions()` with the old format — update both.

### Changes Required:

#### 1. Update OneDrive `_sync_permissions`

**File**: `backend/open_webui/services/onedrive/sync_worker.py`

The OneDrive sync_worker on this branch already has `_sync_permissions()` using the old `access_control` dict. Update to match dev's `access_grants` format. Find the section that builds the `access_control` dict and replace with:

```python
# Build access_grants list (new upstream format)
access_grants = []
for user_id in permitted_user_ids:
    access_grants.append(
        {
            "principal_type": "user",
            "principal_id": user_id,
            "permission": "read",
        }
    )
access_grants.append(
    {
        "principal_type": "user",
        "principal_id": self.user_id,
        "permission": "write",
    }
)

Knowledges.update_knowledge_by_id(
    self.knowledge_id,
    KnowledgeForm(
        name=knowledge.name,
        description=knowledge.description,
        type=knowledge.type,
        access_grants=access_grants,
    ),
)
```

#### 2. Update Google Drive `_sync_permissions`

**File**: `backend/open_webui/services/google_drive/sync_worker.py`
**Lines**: 248-266

Same change — replace `access_control` dict with `access_grants` list:

```python
# Build access_grants list (new upstream format)
access_grants = []
for user_id in permitted_user_ids:
    access_grants.append(
        {
            "principal_type": "user",
            "principal_id": user_id,
            "permission": "read",
        }
    )
access_grants.append(
    {
        "principal_type": "user",
        "principal_id": self.user_id,
        "permission": "write",
    }
)

Knowledges.update_knowledge_by_id(
    self.knowledge_id,
    KnowledgeForm(
        name=knowledge.name,
        description=knowledge.description,
        type=knowledge.type,
        access_grants=access_grants,
    ),
)
```

### Success Criteria:

#### Automated Verification:

- [x] No syntax errors: `python -m py_compile backend/open_webui/services/onedrive/sync_worker.py && python -m py_compile backend/open_webui/services/google_drive/sync_worker.py`

#### Manual Verification:

- [ ] OneDrive folder sync creates proper `access_grants` on the knowledge base (check DB or API response)

**Implementation Note**: After completing this phase, proceed to Phase 3.

---

## Phase 3: Execute Git Merge

### Overview

With the critical fixes pre-applied, merge `dev` into this branch. The conflict surface should be smaller now.

### Steps:

```bash
# 1. Ensure working tree is clean
git status

# 2. Commit Phase 1+2 changes
git add -A
git commit -m "fix: port dev's get_db and access_grants fixes to sync abstraction layer"

# 3. Merge dev
git merge dev
```

### Expected Conflicts and Resolution:

#### `backend/open_webui/services/onedrive/sync_worker.py`

**Strategy**: Keep this branch's version (thin BaseSyncWorker subclass). Dev's changes are either:

- Already ported to `base_worker.py` (Phase 1) → discard dev's version
- `_sync_permissions` access_grants → already ported (Phase 2)
- Black formatting → accept formatting on our thin file

```bash
git checkout --ours backend/open_webui/services/onedrive/sync_worker.py
# Then manually apply any Black formatting if desired
```

#### `backend/open_webui/services/onedrive/auth.py`

**Strategy**: Accept dev's Black formatting. Our changes are structural (abstraction), dev's are formatting-only.

- Resolve by accepting both — the abstraction changes + formatting

#### `backend/open_webui/services/onedrive/provider.py`

**Strategy**: Keep ours (abstraction), accept dev's blank line formatting

#### `backend/open_webui/services/onedrive/scheduler.py`, `token_refresh.py`

**Strategy**: Keep ours (thin wrappers around shared scheduler/token_refresh). Dev only added formatting.

#### `backend/open_webui/services/sync/provider.py`

**Strategy**: Keep ours. Dev only added blank lines.

#### `backend/open_webui/config.py`

**Strategy**: Accept both — our Google Drive config + dev's feature flags + dev's formatting. These are in different sections of the file.

#### `backend/open_webui/main.py`

**Strategy**: Accept both — our Google Drive router registration + dev's feature flag exposure + dev's formatting.

#### `backend/open_webui/routers/knowledge.py`

**Strategy**: Accept both — our Google Drive type addition + dev's access_grants guard + dev's warning passthrough. These touch different parts of the file.

#### `backend/open_webui/routers/onedrive_sync.py`

**Strategy**: Keep ours (refactored to use shared router helpers). Dev's change was a single blank line removal.

#### `helm/` files

**Strategy**: Accept both — additive on both sides.

#### `src/lib/stores/index.ts`

**Strategy**: Accept both — our store + dev's 6 stores. Different lines.

#### `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

**Strategy**: This is the hardest conflict. See Phase 4.

### Success Criteria:

#### Automated Verification:

- [x] `git merge dev` completes (with or without conflicts)
- [x] All conflicts resolved: `git diff --check` shows no conflict markers
- [x] Merge committed

---

## Phase 4: Resolve Frontend Conflicts

### Overview

`KnowledgeBase.svelte` is the hardest merge because both branches made substantial changes. Dev rewrote the reactive fetch logic and added socket-driven file status handling. This branch added Google Drive UI.

### Strategy

Start with **this branch's version** (has Google Drive UI) and manually apply dev's specific fixes:

#### 1. Fetch race condition fix

Dev added a `fetchId` counter to discard stale responses. Find the data fetching section and apply:

```typescript
let fetchId = 0;

// In the fetch function:
const currentFetchId = ++fetchId;
const result = await fetchKnowledgeData(...);
if (currentFetchId !== fetchId) return; // stale response
```

#### 2. Socket.IO `file:status` handler

Dev added `handleFileStatus` and `_processFileStatus` functions that serialize file processing events via a promise queue. Files are added to KB when socket reports completion instead of immediately after upload. Add these alongside the existing Google Drive socket handlers.

#### 3. Batched success toasts

Dev added `showBatchedSuccessToast` that aggregates multiple file additions into one toast. Apply this to both OneDrive and Google Drive file addition flows.

#### 4. Parallel file uploads

Dev changed `inputFiles` processing from sequential `for...of` to `Promise.all`. Apply to the upload handler.

#### 5. Loading state

Dev changed from showing spinner while `fileItems === null` to waiting for a `loaded` flag. Apply this guard.

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` compiles successfully
- [x] No new TypeScript errors beyond pre-existing ones

#### Manual Verification:

- [ ] Open a local KB → file list loads without flicker
- [ ] Upload multiple files → parallel upload with batched toast
- [ ] Open a OneDrive KB → sync progress shows correctly
- [ ] Open a Google Drive KB → sync progress shows correctly
- [ ] File status updates appear in real-time via socket

**Implementation Note**: This phase requires the most careful manual work. Take time to understand both versions before merging.

---

## Phase 5: Verification and Cleanup

### Overview

Run the full build, verify both providers work, run Black formatting on changed files.

### Steps:

#### 1. Format Python files

```bash
cd backend
black open_webui/services/sync/ open_webui/services/onedrive/ open_webui/services/google_drive/ open_webui/routers/onedrive_sync.py open_webui/routers/google_drive_sync.py open_webui/routers/knowledge.py
```

#### 2. Build frontend

```bash
npm run build
```

#### 3. Start backend

```bash
open-webui dev
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] Backend starts without import errors
- [x] No Python syntax errors in changed files
- [x] Black formatting passes on changed files

#### Manual Verification:

- [ ] Create a new OneDrive KB → pick folder → sync starts → files appear → permissions sync correctly
- [ ] Create a new Google Drive KB → pick folder → sync starts → files appear → permissions sync correctly
- [ ] Cancel an in-progress sync → status updates correctly
- [ ] Delete a KB → files cleaned up properly
- [ ] Background scheduler triggers for both providers (check logs after interval)
- [ ] Access grants on external KBs cannot be modified via UI (dev's security fix)
- [ ] Google Workspace files (Docs, Sheets) export correctly as docx/xlsx

**Implementation Note**: After completing verification, commit the merge result and proceed to Phase 6.

---

## Phase 6: Write External Integration Cookbook

### Overview

Create a standalone `dev_notes/` document with a step-by-step recipe for adding new external integrations (Topdesk, Confluence, Salesforce, Dropbox, etc.).

### Output File

**File**: `dev_notes/external-integration-cookbook.md`

### Content Structure

The cookbook should cover:

1. **Architecture overview** — diagram of the sync abstraction layer
2. **Prerequisites checklist** — what you need from the provider (OAuth creds, API docs, scopes)
3. **Step-by-step recipe** (12 steps):
   - Config variables
   - API client
   - Auth module
   - Token refresh
   - Sync worker (with all abstract methods explained)
   - Provider + wrappers
   - Factory registration
   - Router
   - main.py registration
   - Knowledge type
   - Frontend (API client, picker, KnowledgeBase.svelte, TypeSelector)
   - Helm/deployment config
4. **Provider-specific considerations** — table of how different provider types map to the abstraction
5. **Testing checklist** — what to verify for a new integration
6. **Reference implementations** — pointers to Google Drive (simpler) and OneDrive (more complex, with legacy migration)

### Content Source

Base the cookbook on the detailed recipe already written in `thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md` Section 4, but expand it with:

- More concrete code examples (not just pseudocode)
- Provider-specific gotchas table
- Frontend integration details
- Deployment/helm config steps

### Success Criteria:

#### Automated Verification:

- [x] File exists at `dev_notes/external-integration-cookbook.md`
- [x] Dev notes index updated

#### Manual Verification:

- [ ] A developer unfamiliar with the codebase can follow the cookbook to understand what's needed for a new integration
- [ ] All code references point to real files that exist

---

## Testing Strategy

### Per-Provider Manual Testing:

1. Create KB with provider type → verify creation flow
2. Authenticate via OAuth popup → verify token stored
3. Pick folder/files → verify sources saved to metadata
4. Trigger sync → verify files download, process, and appear in KB
5. Cancel sync mid-progress → verify cancellation
6. Re-sync → verify incremental (only changed files)
7. Remove source → verify files cleaned up
8. Revoke token → verify re-auth prompt
9. Background sync → verify scheduler triggers after interval
10. Permission sync → verify access_grants updated on KB

### Cross-Provider Testing:

- Create one OneDrive KB and one Google Drive KB simultaneously
- Sync both → verify no event/state collision
- Verify socket events are correctly prefixed (`onedrive:*` vs `googledrive:*`)

## References

- Research: `thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md`
- Refactor plan: `thoughts/shared/plans/2026-03-24-cloud-sync-abstraction-refactor.md`
- Typed KBs plan: `thoughts/shared/plans/2026-02-04-typed-knowledge-bases.md`
- Dev notes: `dev_notes/notes.md` (Gradient-DS custom features overview)
