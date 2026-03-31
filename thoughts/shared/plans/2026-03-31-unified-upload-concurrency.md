# Unified Upload Concurrency Implementation Plan

## Overview

Replace 4 different file upload dispatch patterns in `KnowledgeBase.svelte` with a single `uploadFiles()` function that provides bounded concurrency (default 5) and optional progress tracking. This eliminates the sequential bottleneck in directory uploads while adding backpressure to the currently-unbounded file input and drag-and-drop paths.

## Current State Analysis

All four upload entry points call the same `uploadFileHandler` function but with wildly different concurrency:

| Path | Current Pattern | Concurrency |
|------|----------------|-------------|
| File input (line 1583) | `Promise.all(files.map(fn))` | Unbounded parallel |
| Drag-and-drop (line 1341) | Fire-and-forget, no `await` | Unbounded parallel |
| Directory modern (line 499) | `await` in `for await` loop | Sequential |
| Directory Firefox (line 565) | `await` in `for` loop | Sequential |

The downstream pipeline (Socket.IO `fileStatusQueue`, `_processFileStatus`, `addFileHandler`, `showBatchedSuccessToast`) is already concurrent-safe and requires no changes.

### Key Discoveries:
- `uploadFileHandler` (line 349) is the shared core — all paths call it identically
- `fileItems` prepending (line 385) is concurrent-safe — synchronous before any `await`
- `countFiles` (line 468) and `processDirectory` (line 485) both walk the same directory tree — redundant
- Drag-and-drop uses callback-based APIs (`item.file()`, `readEntries`) that need promisification
- Cloud sync workers already use bounded concurrency (`asyncio.Semaphore(5)` in `base_worker.py:814-898`) — proven pattern

**Research document:** `thoughts/shared/research/2026-03-31-file-upload-processing-pipeline.md`

## Desired End State

A single `uploadFiles(files, options?)` function used by all four paths:
- Bounded concurrency (default 5 simultaneous uploads)
- Optional progress callback for directory uploads
- No changes to `uploadFileHandler`, Socket.IO pipeline, or backend
- ~47 fewer lines of code

### Verification:
1. Directory upload of 20+ files completes significantly faster than before (concurrent, not sequential)
2. File input multi-select still works identically
3. Drag-and-drop of files and folders still works identically
4. Progress toast shows during directory uploads (numbers may jump — expected)
5. Socket.IO file status events still trigger KB association correctly
6. Batch success toast still fires when all files complete

## What We're NOT Doing

- No backend changes — backend already handles concurrent uploads with no rate limiting
- No changes to `uploadFileHandler` — it stays as-is
- No changes to Socket.IO pipeline (`fileStatusQueue`, `_processFileStatus`, `addFileHandler`, `showBatchedSuccessToast`)
- No new npm dependencies — semaphore is ~20 lines inline
- No extraction to `$lib/utils/` — keep local until reuse is needed
- No i18n changes — reusing existing `'Upload Progress'` translation key

## Implementation Approach

Separation of concerns: **collection** (getting File objects) is separated from **dispatch** (uploading them). Currently `processDirectory` does both interleaved. After: each path collects files into an array, then passes it to `uploadFiles`.

---

## Phase 1: Add `uploadFiles` Utility Function

### Overview
Add the core bounded-concurrency dispatcher as a local function in `KnowledgeBase.svelte`. This phase adds code only — no existing code is modified.

### Changes Required:

#### 1. Add `uploadFiles` function
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: After `uploadFileHandler` (after line 425), before `uploadDirectoryHandler` (line 427)

```typescript
// Uploads multiple files with bounded concurrency.
// All paths (file input, drag-drop, directory) route through this.
const uploadFiles = async (
    files: File[],
    options: { concurrency?: number; onProgress?: (completed: number, total: number) => void } = {}
) => {
    const { concurrency = 5, onProgress } = options;
    const total = files.length;
    if (total === 0) return;

    let completed = 0;
    const executing: Set<Promise<void>> = new Set();

    for (const file of files) {
        const task = uploadFileHandler(file).then(() => {
            completed++;
            executing.delete(task);
            onProgress?.(completed, total);
        });
        executing.add(task);

        if (executing.size >= concurrency) {
            await Promise.race(executing);
        }
    }

    await Promise.all(executing);
};
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript check passes: `npm run check` (no new errors beyond pre-existing baseline)
- [x] Build succeeds: `npm run build`

#### Manual Verification:
- [x] No regressions — existing upload paths still work (function is added but not yet wired)

**Implementation Note**: This is purely additive. Proceed to Phase 2 immediately.

---

## Phase 2: Refactor Directory Uploads

### Overview
Replace the sequential `processDirectory` + `countFiles` dual-walk with a single `collectDirectoryFiles` (collect-only) + `uploadFiles` (dispatch). This is the main performance improvement — directory uploads become concurrent.

### Changes Required:

#### 1. Replace `countFiles` and `processDirectory` with `collectDirectoryFiles`
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**What changes**: Remove `countFiles` (lines 468-482) and `processDirectory` (lines 485-509). Replace with a single collect function.

```typescript
// Recursively collects all non-hidden files from a directory handle
async function collectDirectoryFiles(
    dirHandle: FileSystemDirectoryHandle,
    path = ''
): Promise<File[]> {
    const files: File[] = [];
    for await (const entry of dirHandle.values()) {
        if (entry.name.startsWith('.')) continue;
        const entryPath = path ? `${path}/${entry.name}` : entry.name;
        if (hasHiddenFolder(entryPath)) continue;

        if (entry.kind === 'file') {
            const file = await entry.getFile();
            files.push(new File([file], entryPath, { type: file.type }));
        } else if (entry.kind === 'directory') {
            files.push(...(await collectDirectoryFiles(entry, entryPath)));
        }
    }
    return files;
}
```

#### 2. Rewrite `handleModernBrowserUpload`
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**What changes**: Replace the current implementation (lines 450-519) with collect + dispatch.

```typescript
const handleModernBrowserUpload = async () => {
    const dirHandle = await window.showDirectoryPicker();
    const files = await collectDirectoryFiles(dirHandle);

    if (files.length === 0) {
        console.log('No files to upload.');
        return;
    }

    toast.info(
        $i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
            uploadedFiles: 0,
            totalFiles: files.length,
            percentage: '0.00'
        })
    );

    await uploadFiles(files, {
        onProgress: (done, total) => {
            const percentage = ((done / total) * 100).toFixed(2);
            toast.info(
                $i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
                    uploadedFiles: done,
                    totalFiles: total,
                    percentage
                })
            );
        }
    });
};
```

#### 3. Rewrite `handleFirefoxUpload`
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**What changes**: Replace the sequential loop (lines 522-587) with collect + dispatch.

```typescript
const handleFirefoxUpload = async () => {
    return new Promise<void>((resolve, reject) => {
        const input = document.createElement('input');
        input.type = 'file';
        input.webkitdirectory = true;
        input.directory = true;
        input.multiple = true;
        input.style.display = 'none';
        document.body.appendChild(input);

        input.onchange = async () => {
            try {
                const files = Array.from(input.files)
                    .filter((file) => !hasHiddenFolder(file.webkitRelativePath))
                    .filter((file) => !file.name.startsWith('.'))
                    .map((file) => {
                        const relativePath = file.webkitRelativePath || file.name;
                        return new File([file], relativePath, { type: file.type });
                    });

                if (files.length > 0) {
                    toast.info(
                        $i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
                            uploadedFiles: 0,
                            totalFiles: files.length,
                            percentage: '0.00'
                        })
                    );

                    await uploadFiles(files, {
                        onProgress: (done, total) => {
                            const percentage = ((done / total) * 100).toFixed(2);
                            toast.info(
                                $i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
                                    uploadedFiles: done,
                                    totalFiles: total,
                                    percentage
                                })
                            );
                        }
                    });
                }

                document.body.removeChild(input);
                resolve();
            } catch (error) {
                reject(error);
            }
        };

        input.onerror = (error) => {
            document.body.removeChild(input);
            reject(error);
        };

        input.click();
    });
};
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript check passes: `npm run check`
- [x] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] Directory upload (Chrome/Edge): Select a folder with 10+ files — uploads start concurrently, progress toast updates as files complete
- [ ] Directory upload (Firefox): Same test — files upload concurrently
- [ ] Empty directory: Shows no error, just logs "No files to upload"
- [ ] Hidden files/folders: Still filtered out (files starting with `.`, folders like `.git`)
- [ ] Socket.IO status events still trigger correctly — files transition from "uploading" to "uploaded"
- [ ] Batch success toast appears when all files finish

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual testing. This is the highest-risk phase since it changes the user-facing upload behavior.

---

## Phase 3: Refactor File Input

### Overview
Replace the unbounded `Promise.all` in the file input handler with `uploadFiles`. Minimal change — adds bounded concurrency to multi-file select.

### Changes Required:

#### 1. Update file input `on:change` handler
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**What changes**: Replace `Promise.all(sortedFiles.map(...))` (line 1583) with `uploadFiles(sortedFiles)`.

Before (lines 1578-1583):
```js
on:change={async () => {
    if (inputFiles && inputFiles.length > 0) {
        const sortedFiles = Array.from(inputFiles).sort((a, b) =>
            b.name.localeCompare(a.name)
        );
        await Promise.all(sortedFiles.map((file) => uploadFileHandler(file)));
```

After:
```js
on:change={async () => {
    if (inputFiles && inputFiles.length > 0) {
        const sortedFiles = Array.from(inputFiles).sort((a, b) =>
            b.name.localeCompare(a.name)
        );
        await uploadFiles(sortedFiles);
```

Single line change. Rest of the handler (cleanup of `inputFiles` and input element) stays identical.

### Success Criteria:

#### Automated Verification:
- [x] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] Multi-file select: Pick 5+ files — all upload and complete successfully
- [ ] Single file select: Still works normally

---

## Phase 4: Refactor Drag-and-Drop

### Overview
Replace the fire-and-forget `handleUploadingFileFolder` with a promisified `collectDroppedFiles` that gathers all File objects first, then routes through `uploadFiles`. This adds bounded concurrency to the previously-unbounded drag-and-drop path.

### Changes Required:

#### 1. Add `collectDroppedFiles` helper
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Location**: Near the other collect helpers (after `collectDirectoryFiles`)

```typescript
// Collects all File objects from a DataTransfer drop (files and recursive directories)
async function collectDroppedFiles(items: DataTransferItemList): Promise<File[]> {
    const files: File[] = [];

    const readEntry = (entry: FileSystemEntry): Promise<void> => {
        return new Promise((resolve) => {
            if (entry.isFile) {
                (entry as FileSystemFileEntry).file((file) => {
                    files.push(file);
                    resolve();
                });
            } else if (entry.isDirectory) {
                const reader = (entry as FileSystemDirectoryEntry).createReader();
                reader.readEntries(async (entries) => {
                    await Promise.all(entries.map(readEntry));
                    resolve();
                }, () => resolve());
            } else {
                resolve();
            }
        });
    };

    const entries: FileSystemEntry[] = [];
    for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry();
        if (entry) entries.push(entry);
    }

    await Promise.all(entries.map(readEntry));
    return files;
}
```

#### 2. Rewrite `onDrop` handler
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**What changes**: Replace `handleUploadingFileFolder` (lines 1341-1367) and its invocation (lines 1369-1379) with collect + dispatch.

```typescript
const onDrop = async (e) => {
    e.preventDefault();
    dragged = false;

    if (!knowledge?.write_access) {
        toast.error($i18n.t('You do not have permission to upload files to this knowledge base.'));
        return;
    }

    if ($config?.integration_providers?.[knowledge?.type]) {
        toast.error($i18n.t('Files for this knowledge base are managed via the integration API.'));
        return;
    }

    if (e.dataTransfer?.types?.includes('Files') && e.dataTransfer?.items) {
        const inputItems = e.dataTransfer.items;
        if (inputItems.length > 0) {
            const files = await collectDroppedFiles(inputItems);
            if (files.length > 0) {
                await uploadFiles(files);
            }
        } else {
            toast.error($i18n.t(`File not found.`));
        }
    }
};
```

This removes the entire `handleUploadingFileFolder` function.

### Success Criteria:

#### Automated Verification:
- [x] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] Drag single file onto KB — uploads successfully
- [ ] Drag multiple files onto KB — all upload successfully
- [ ] Drag folder onto KB — all files inside upload successfully (including nested folders)
- [ ] Drag folder with hidden files — hidden files are filtered (note: the current code doesn't filter hidden files on drag-drop either, so this is existing behavior)

---

## Testing Strategy

### Manual Testing Steps:
1. **Directory upload (Chrome)**: Select a folder with 15+ files including subfolders — verify concurrent uploads (multiple "uploading" items visible simultaneously), progress toast updates, all files appear in KB
2. **Directory upload (Firefox)**: Same test — verify Firefox fallback path works
3. **File input**: Select 10 files via file picker — verify all upload and complete
4. **Drag-and-drop files**: Drop 5 files — verify all upload
5. **Drag-and-drop folder**: Drop a folder — verify recursive file collection and upload
6. **Edge cases**: Empty folder, folder with only hidden files, single file drag, very large batch (50+ files)
7. **Error handling**: Upload a file that exceeds max size during a batch — verify other files still complete, error toast shows for the rejected file

### Regression Checks:
- Socket.IO `file:status` events still trigger KB association
- Batch success toast still fires with correct count
- File items display correctly in the UI during upload (status indicators)
- Integration provider KBs still reject manual uploads

## Performance Considerations

- Default concurrency of 5 matches cloud sync workers (`FILE_PROCESSING_MAX_CONCURRENT`)
- Browser HTTP/1.1 limit is ~6 connections per origin; HTTP/2 supports 100+ streams
- Backend spawns independent `BackgroundTask` per file — no server-side bottleneck
- Progress toast will show non-sequential completions (e.g., 2/10 → 5/10 → 7/10) — this is expected and acceptable

## References

- Research document: `thoughts/shared/research/2026-03-31-file-upload-processing-pipeline.md`
- Cloud sync semaphore pattern: `backend/open_webui/services/sync/base_worker.py:814-898`
- `FILE_PROCESSING_MAX_CONCURRENT` config: `backend/open_webui/config.py:3129-3133`
