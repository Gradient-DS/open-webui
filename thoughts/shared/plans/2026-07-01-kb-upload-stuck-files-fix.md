# KB Upload "Stuck Spinning Forever" — Fix Implementation Plan

**Date:** 2026-07-01 **Branch:** `feat/track1-honest-completion-signal` **Repo:** `open-webui/`
**Related handoff:** `genai-utils/thoughts/shared/handoffs/2026-07-01-kb-upload-stuck-files-research-handoff.md`

## Overview

Direct KB uploads of certain files spin at "loading" forever until the user reloads the page (on reload they vanish). Investigation this session showed the handoff's original framing (native-path DB wedge) is **not** what's happening on the current branch. There are three independent causes, each with a small, targeted fix:

1. **Frontend never removes the optimistic row when an upload is rejected** → eternal spinner for *any* upload that fails at the HTTP layer (allow-list 400, size, network). This is the actual cause of the spinners in the screenshots.
2. **The allow-list rejects legitimate no-extension documents** (`ASB - Microsoft Entra admin center`, `Gradient ISMS beleid`, `High Level Design_ Microsoft 365`) because their resolved extension is `''`.
3. **A warren-supported file that parses to zero chunks** (scanned/text-less PDF, e.g. `DESIGN_PHILOSOPHY.pdf`) never gets a terminal signal back to OWUI, so it hangs until the 6-hour wall-clock timeout.

## Current State Analysis

### Cause 1 — dangling optimistic row (frontend)
`src/lib/components/workspace/Knowledge/KnowledgeBase.svelte` `uploadFileHandler` (lines 508-587):
- Line 544 optimistically prepends `fileItem` with `status: 'uploading'`, `id: null`, a generated `itemId`.
- Line 557 `uploadFile(...)`; on throw (e.g. allow-list `400 File type X is not allowed`) the inline `.catch` toasts and returns `null`.
- Line 562 `if (uploadedFile)` is false → the `else` at line 581 only toasts `Failed to upload file.` — **it never removes the row**. The outer `catch` (584) is the same.
- Result: the row stays `'uploading'` with `id: null` forever. No real file id → no `file:status` socket match; `armUploadStatusFallback` (579) was never called → no poller. Nothing can clear it.
- On reload, `init()` fetches server truth (only linked KB files) → the ghost disappears. That "disappear on reload" is the signature of a client-only optimistic row that was never persisted.
- The correct pattern already exists two branches up: line 574 (the `uploadedFile.error` branch) filters the row out, and `uploadWeb` filters at 497/502.

### Cause 2 — allow-list rejects empty extensions (backend)
`backend/open_webui/routers/files.py` (lines 314-335):
- `file_extension` is derived from the filename, then (if empty) from `mimetypes.guess_extension(content_type)`. For a genuinely extension-less upload with an unknown/`octet-stream` content type it stays `''`.
- Lines 327-335: when `process` is true and `ALLOWED_FILE_EXTENSIONS` is set, `if file_extension not in ALLOWED_FILE_EXTENSIONS:` → `400`. `''` is never in the list → **every no-extension file is rejected**, including legit documents.
- This is the *only* reject gate (`config.py:3722` defines `RAG_ALLOWED_FILE_EXTENSIONS`; `main.py:1440` binds it; `main.py:3485` only exposes it to the frontend — no second gate).

### Cause 3 — warren zero-chunk success never signals OWUI (backend + confirmed cross-repo root)
- genai-utils `document_processing/distributed/pipeline/workers/owui_ingest_worker.py:117-121`: when a doc yields no chunks it logs `No chunks … skipping OWUI ingest` and returns a **success** message (`_success_message(..., skipped=True)`). The warren job therefore reaches status `completed` and OWUI's `POST /ingest` is **never called** for that file.
- OWUI side: the file was set `'processing'` at submit (`retrieval.py:1691` via `route_file_to_pipeline`) and only `/ingest` flips it to `completed`. Since `/ingest` never arrives, it stays `'processing'`.
- `backend/open_webui/utils/doc_pipeline.py` `reconcile_action` (100-123) treats `job_status == 'completed'` as `'wait'`. So the only escape is `age_seconds > max_wall_clock_seconds`.
- `PIPELINE_JOB_MAX_WALL_CLOCK_SECONDS` default = **21600s (6h)** (`config.py:3951`); reconcile interval = 120s (`config.py:3942`). So a zero-chunk file hangs up to 6 hours before being marked `error`.
- No race exists: the warren job is marked `completed` only *after* the owui-ingest worker returns. So "job `completed` **and** file still `processing`" deterministically means the zero-chunk/skipped-ingest case — the reconciler already only inspects files still in `processing` (`Files.get_processing_files_with_pipeline_job`, `models/files.py:224`).

### Verified supporting facts
- `Files.set_status` dual-writes `data['status']` + `meta['status']` (`models/files.py:421-443`); `/files/{id}/process/status` reads `data['status']` (`files.py:621`).
- `emit_file_status` passes `status` straight to Socket.IO `file:status` (`services/files/events.py`); the frontend `_processFileStatus` acts **only** on `'completed'`/`'failed'` and ignores `'processing'`/`'error'` (`KnowledgeBase.svelte:1553-1566`).
- The branch already delivers the "honest completion signal" for warren **success** (`integrations.py` ~865/~1043 emit terminal status on `/ingest`) and for warren **failure/hang** (reconciler `fail`/`timeout`). Zero-chunk-success is the remaining uncovered warren terminal state.

## Desired End State

- Any upload rejected at the HTTP layer removes its spinner immediately (toast + row gone), no reload needed. **(Fix 1)**
- Extension-less documents pass the allow-list and proceed to processing; junk types (`svg/png/zip/dmg`) still fast-reject at upload. **(Fix 2)**
- A warren file that produces zero chunks is marked terminal within one reconcile tick (~120s) instead of hanging 6h, and the user learns it failed. **(Fix 3)**

## What We're NOT Doing

- **Not** adding a native-path age-based reconciler backstop (the native path already reaches terminal status; earlier handoff option R1a is unnecessary on this branch).
- **Not** adding content-type-based *routing* for no-extension files (Fix 2 is the minimal "empty extension passes" only). No-ext files continue on the native/external-loader path.
- **Not** changing warren/genai-utils (`owui_ingest_worker.py`) — Fix 3 is handled entirely OWUI-side via the reconciler. A prompt push-callback from warren on zero-chunk is a possible future enhancement but out of scope.
- **Not** changing how *failed* files are displayed in the KB list (they are still removed on `'failed'`, matching existing behavior). Making failed files persist visibly is a separate UX change.
- **Not** changing the 6h wall-clock cap for genuine *hangs* (unrelated tuning).
- **Not** retroactively cleaning already-stuck leftover rows in DB — most were client-only ghosts (gone on reload); any genuinely-`processing` warren leftovers are swept by the existing reconciler.

## Implementation Approach

Three independent phases, shippable separately, ordered by user impact. Fix 1 is the highest-impact and lowest-risk; Fix 3 is the only one touching pipeline logic.

---

## Phase 1: Remove the optimistic row on upload failure (frontend)

### Overview
Kill the eternal spinner for every failed upload by filtering the optimistic `fileItem` in both failure paths of `uploadFileHandler`.

### Changes Required

#### 1. `uploadFileHandler` failure branches
**File:** `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte` (~line 581 and ~584)
**Changes:** In the `else` (upload returned `null`) branch and the outer `catch`, remove the optimistic row by `itemId` (it never received a real `id`, so filter on `itemId`, mirroring `uploadWeb`'s 497/502).

```svelte
			} else {
				toast.error($i18n.t('Failed to upload file.'));
				fileItems = fileItems.filter((item) => item.itemId !== fileItem.itemId);
			}
		} catch (e) {
			toast.error(`${e}`);
			fileItems = fileItems.filter((item) => item.itemId !== fileItem.itemId);
		}
```

### Notes
- No new i18n strings (reuses existing `Failed to upload file.`).
- Leaves the success and `uploadedFile.error` branches untouched.

### Success Criteria

#### Automated Verification:
- [ ] Type check passes: `npm run check`
- [ ] Frontend lint passes: `npm run lint:frontend`

#### Manual Verification:
- [ ] Upload a `.dmg` (or any type not in the tenant allow-list) into a KB → toast appears **and the row disappears immediately** (no spinner, no reload).
- [ ] Upload an oversized file → same immediate cleanup.
- [ ] A valid upload still shows the spinner and resolves normally (not removed early).

**Implementation Note:** Pause for manual confirmation before Phase 2.

---

## Phase 2: Let empty-extension files pass the allow-list (backend)

### Overview
Stop the allow-list from rejecting genuinely extension-less documents, while keeping junk types rejected.

### Changes Required

#### 1. Allow-list gate skips empty extension
**File:** `backend/open_webui/routers/files.py` (~line 332)
**Changes:** Only reject when there is a non-empty extension to check.

```python
            if file_extension and file_extension not in request.app.state.config.ALLOWED_FILE_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(f'File type {file_extension} is not allowed'),
                )
```

### Notes
- `file_extension` is already `''` for no-extension files after the content-type fallback (files.py:316-325). A short comment should record *why* empty passes: extension-less legit docs (e.g. `ASB - Microsoft Entra admin center`, `Gradient ISMS beleid`) must be ingestable; the native/external loader path handles them.
- Junk (`svg/png/zip/dmg`) resolves to a real, non-allowed extension → still rejected (desired fast-fail).
- No i18n change.

### Success Criteria

#### Automated Verification:
- [ ] New/updated unit test passes: `cd backend && python -m pytest open_webui/test/util/test_files_completion_signal.py -q` (add an allow-list gate test here, or a new `test_files_allowlist.py`).
- [ ] Backend lint passes: `npm run lint:backend`

Test to add: with `ALLOWED_FILE_EXTENSIONS = ['pdf']` and `process=True`:
- extension-less filename + unknown content type → **no** `HTTPException` (passes the gate).
- `logo.svg` → raises `400 File type svg is not allowed`.

#### Manual Verification:
- [ ] With a restrictive tenant allow-list, upload a document with no extension (correct binary content, e.g. a DOCX renamed without `.docx`) → it is accepted and processes (native/external loader), not rejected.
- [ ] `.svg`/`.png`/`.zip` still get the "not allowed" toast and (with Phase 1) disappear immediately.

**Implementation Note:** Pause for manual confirmation before Phase 3.

---

## Phase 3: Fail warren zero-chunk documents fast (backend reconciler)

### Overview
Treat "warren job `completed` but file still `processing`" as a terminal empty result, so the file is marked failed within one reconcile tick instead of hanging up to 6h. The user learns it failed via the existing `'failed'` emit path.

### Changes Required

#### 1. `reconcile_action` recognises completed-but-unfulfilled jobs
**File:** `backend/open_webui/utils/doc_pipeline.py` (`reconcile_action`, ~line 119)
**Changes:** Add an `'empty'` action for `completed` jobs (the reconciler only ever passes files still in `processing`, so `completed` here means `/ingest` never landed — zero chunks). Place it before the age check.

```python
    if job_status in ('failed', 'partial'):
        return 'fail'
    if job_status == 'completed':
        # The reconciler only inspects files still 'processing'. A 'completed'
        # job whose file never reached a terminal status means /ingest was
        # skipped — warren parsed zero chunks (scanned/no-text doc). Mark it now
        # instead of waiting out the 6h wall-clock backstop.
        return 'empty'
    if age_seconds > max_wall_clock_seconds:
        return 'timeout'
    return 'wait'
```
Update the docstring to document the `'empty'` action and the "caller only passes still-processing files" invariant.

#### 2. Reconciler handles the `'empty'` action
**File:** `backend/open_webui/services/doc_pipeline_reconciler.py` (~lines 54-62)
**Changes:** Add a reason for `'empty'`; the existing `set_status('error') + emit_file_status('failed')` block already applies to any non-`wait` action.

```python
        if action == 'wait':
            continue

        if action == 'fail':
            reason = 'distributed doc-pipeline job reported failure'
        elif action == 'empty':
            reason = 'document produced no searchable content (empty, scanned, or non-text file)'
        else:
            reason = f'distributed doc-pipeline job did not complete within {cap}s'
```

### Notes
- Deliberate decision (per user intent "we want the user to know that a document actually failed"): the zero-chunk file is marked **`error`/`failed`**, diverging from the native zero-text path which marks `completed` with a "not searchable" warning (`retrieval.py:1965`). If we later prefer consistency, flip the `'empty'` branch to a completed-with-warning; the reconcile_action change is the same.
- No warren/genai-utils change required.
- No new i18n strings (reason is a backend error string, matching the existing reconciler reasons which are not localized).

### Success Criteria

#### Automated Verification:
- [ ] `reconcile_action` unit test covers `job_status='completed' → 'empty'`: `cd backend && python -m pytest open_webui/test/util/test_doc_pipeline.py -q`
- [ ] Reconciler test covers marking `error` + emitting `failed` for the `'empty'`/completed case: `cd backend && python -m pytest open_webui/test/util/test_pipeline_reconciler.py -q`
- [ ] Backend lint passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Upload a scanned/text-less PDF to a warren-routed KB → within ~one reconcile interval (~120s) the spinner clears and the file is reported failed (not a 6h hang).
- [ ] A normal PDF still ingests and shows `completed` (unaffected — its file leaves `processing` via `/ingest` before the reconciler ever sees it).
- [ ] A genuinely failed/hung warren job still marks `error` via the existing `fail`/`timeout` paths.

**Implementation Note:** Pause for manual confirmation; this is the only phase touching pipeline logic.

---

## Testing Strategy

### Unit Tests
- Fix 2: allow-list gate — empty extension passes; disallowed real extension rejects.
- Fix 3: `reconcile_action` (`completed → empty`, plus regression checks that `failed/partial → fail`, age>cap → `timeout`, running → `wait`); reconciler marks `error` + emits `failed` with the empty reason.

### Manual Testing (end-to-end on staging tenant)
1. `.dmg`/`.svg` → rejected, spinner clears immediately (Fix 1 + existing allow-list).
2. Extension-less real document → accepted + processed (Fix 2).
3. Scanned/text-less PDF → fails within ~120s with a clear reason (Fix 3).
4. Normal PDF/DOCX → still completes normally (no regressions).

## References
- Research handoff: `genai-utils/thoughts/shared/handoffs/2026-07-01-kb-upload-stuck-files-research-handoff.md`
- Frontend: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:508-587,1553-1566`
- Allow-list: `backend/open_webui/routers/files.py:314-335`; `config.py:3722`
- Reconciler: `backend/open_webui/utils/doc_pipeline.py:100-123`; `backend/open_webui/services/doc_pipeline_reconciler.py`
- Warren zero-chunk root: `genai-utils/document_processing/distributed/pipeline/workers/owui_ingest_worker.py:117-121`
