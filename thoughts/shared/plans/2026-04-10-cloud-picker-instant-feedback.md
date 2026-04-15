# Cloud File Picker — Instant Feedback Implementation Plan

## Overview

When a user selects a file from Google Drive or OneDrive, there's a 1-2 second delay before it appears in the chat input. Local files appear instantly because `uploadFileHandler` creates a placeholder immediately — but for cloud files, the entire download from the cloud API must complete before `uploadFileHandler` is ever called. This plan adds immediate placeholder feedback for cloud file selection.

## Current State Analysis

### Local file flow (instant)

1. User picks file → OS dialog returns `File` object (already in memory)
2. `uploadFileHandler` creates placeholder with `status: 'uploading'` → **appears in UI** (`MessageInput.svelte:637-657`)
3. Upload to backend in background
4. Socket.IO `file:status` event → spinner stops

### Cloud file flow (delayed)

1. User picks file in picker UI
2. **BLOCKING**: File downloaded from Google/OneDrive API (1-2s+)
3. Handler wraps blob in `File` object
4. `uploadFileHandler` called → placeholder appears → **appears in UI**
5. Upload to backend in background

### Key files

- `src/lib/components/chat/MessageInput.svelte:541-578` — Cloud handlers
- `src/lib/components/chat/MessageInput.svelte:626-738` — `uploadFileHandler`
- `src/lib/utils/google-drive-picker.ts:197-280` — `createPicker()` (metadata available at line 218, download at 249)
- `src/lib/utils/onedrive-file-picker.ts:978-1001` — `pickAndDownloadFilesModal()` (items available at 981, download at 988)

### Key Discoveries

- `FileItem.svelte` renders `loading=true` (spinner) when `status === 'uploading'` — the UI already supports this
- The `small` variant used in chat input doesn't prominently show file size, so `size: 0` in the placeholder won't look odd
- The `itemId` field (UUID) on file items was designed for pre-upload tracking — we can reuse it to link placeholder → real upload
- Google Workspace files get renamed (`.txt`, `.csv` extension added) before download — the `onFileSelected` callback must fire after this name computation

## Desired End State

When a user picks a file from Google Drive or OneDrive, a file chip with a loading spinner appears in the chat input **immediately** (within the same frame as the picker closing), identical to local file selection behavior. The spinner transitions to the uploaded state after the cloud download + backend upload complete.

### How to verify

1. Open a chat, click the `+` menu → Google Drive (or OneDrive)
2. Pick a file in the picker
3. The file chip should appear instantly with a spinner when the picker closes
4. After 1-3 seconds the spinner should stop (download + upload complete)
5. Compare with local file upload — should feel identical

## What We're NOT Doing

- Changing the actual download/upload performance (that's a separate concern)
- Adding progress bars or download percentage indicators
- Changing error handling behavior — failed downloads still remove the file item with a toast
- Modifying the OneDrive multi-file sequential upload behavior (optimization opportunity but out of scope)

## Implementation Approach

Add an `onFileSelected` callback to both picker utility functions. When the user picks a file in the picker UI, the callback fires with file metadata (name) **before** the download starts. The handler uses this to create a placeholder in the `files` array. When the download completes, `uploadFileHandler` reuses the existing placeholder via a new `existingItemId` parameter instead of creating a duplicate.

## Phase 1: Add `existingItemId` to `uploadFileHandler`

### Overview

Enable `uploadFileHandler` to reuse a pre-created placeholder file item instead of always creating a new one. This is the foundation that both cloud handlers will use.

### Changes Required:

#### 1. `uploadFileHandler` — accept and reuse existing placeholder

**File**: `src/lib/components/chat/MessageInput.svelte`
**Lines**: 626-657

Add `existingItemId` parameter. If provided, find and update the existing placeholder instead of creating a new one.

```javascript
const uploadFileHandler = async (file, process = true, itemData = {}, existingItemId = null) => {
    if ($_user?.role !== 'admin' && !($_user?.permissions?.chat?.file_upload ?? true)) {
        toast.error($i18n.t('You do not have permission to upload files.'));
        // Clean up placeholder if it exists
        if (existingItemId) {
            files = files.filter((item) => item?.itemId !== existingItemId);
        }
        return null;
    }

    if (fileUploadCapableModels.length !== selectedModels.length) {
        toast.error($i18n.t('Model(s) do not support file upload'));
        if (existingItemId) {
            files = files.filter((item) => item?.itemId !== existingItemId);
        }
        return null;
    }

    let tempItemId;
    let fileItem;

    if (existingItemId) {
        // Reuse existing placeholder
        const idx = files.findIndex((f) => f.itemId === existingItemId);
        if (idx !== -1) {
            tempItemId = existingItemId;
            fileItem = files[idx];
            fileItem.size = file.size;
            files = files; // trigger reactivity
        }
    }

    if (!fileItem) {
        // Create new placeholder (original behavior)
        tempItemId = uuidv4();
        fileItem = {
            type: 'file',
            file: '',
            id: null,
            url: '',
            name: file.name,
            collection_name: '',
            status: 'uploading',
            size: file.size,
            error: '',
            itemId: tempItemId,
            ...itemData
        };

        if (fileItem.size == 0) {
            toast.error($i18n.t('You cannot upload an empty file.'));
            return null;
        }

        files = [...files, fileItem];
    }

    // ... rest of the function unchanged (lines 659-738) ...
```

Note: The empty file check (`size == 0`) is only applied to new placeholders (not existing ones), because cloud file placeholders legitimately start with `size: 0` until the download completes.

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` compiles successfully
- [ ] `npm run check` passes (or no new errors vs baseline)

#### Manual Verification:

- [ ] Local file upload still works identically (regression test)
- [ ] Existing cloud file upload still works (no `existingItemId` passed yet)

---

## Phase 2: Google Drive Instant Feedback

### Overview

Add `onFileSelected` callback to `createPicker()` and use it in the Google Drive handler to show a placeholder immediately.

### Changes Required:

#### 1. `createPicker()` — add `onFileSelected` callback

**File**: `src/lib/utils/google-drive-picker.ts`
**Lines**: 197-280

Add an options parameter with an `onFileSelected` callback. Call it after computing the effective filename but before downloading.

```typescript
interface PickerOptions {
	onFileSelected?: (metadata: { name: string }) => void;
}

export const createPicker = (options?: PickerOptions) => {
	return new Promise(async (resolve, reject) => {
		try {
			await initialize();
			const token = await getAuthToken();

			const picker = new google.picker.PickerBuilder()
				.enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
				.addView(
					new google.picker.DocsView()
						.setIncludeFolders(true)
						.setSelectFolderEnabled(false)
						.setParent('root')
				)
				.setOAuthToken(token)
				.setDeveloperKey(API_KEY)
				.setCallback(async (data: any) => {
					if (data[google.picker.Response.ACTION] === google.picker.Action.PICKED) {
						try {
							const doc = data[google.picker.Response.DOCUMENTS][0];
							const fileId = doc[google.picker.Document.ID];
							const fileName = doc[google.picker.Document.NAME];
							const mimeType = doc[google.picker.Document.MIME_TYPE];

							if (!fileId || !fileName) throw new Error('Required file details missing');

							let downloadUrl;
							let effectiveName = fileName;
							if (mimeType.includes('google-apps')) {
								// ... existing export format logic (unchanged) ...
								// effectiveName gets extension appended
							} else {
								downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`;
							}

							// NEW: Notify before download starts
							options?.onFileSelected?.({ name: effectiveName });

							const response = await fetch(downloadUrl, {
								headers: { Authorization: `Bearer ${token}`, Accept: '*/*' }
							});
							// ... rest unchanged ...
						} catch (error) {
							reject(error);
						}
					} else if (data[google.picker.Response.ACTION] === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			console.error('Google Drive Picker error:', error);
			reject(error);
		}
	});
};
```

#### 2. Google Drive handler — create placeholder via callback

**File**: `src/lib/components/chat/MessageInput.svelte`
**Lines**: 541-560

```javascript
const googleDriveHandler = async () => {
    let tempItemId: string | null = null;
    try {
        const fileData = await createPicker({
            onFileSelected: ({ name }) => {
                tempItemId = uuidv4();
                files = [...files, {
                    type: 'file',
                    file: '',
                    id: null,
                    url: '',
                    name,
                    collection_name: '',
                    status: 'uploading',
                    size: 0,
                    error: '',
                    itemId: tempItemId
                }];
            }
        });
        if (fileData) {
            const file = new File([fileData.blob], fileData.name, {
                type: fileData.blob.type
            });
            await uploadFileHandler(file, true, {}, tempItemId);
        } else if (tempItemId) {
            // User cancelled (shouldn't happen — cancel resolves null before callback)
            files = files.filter((f) => f.itemId !== tempItemId);
        }
    } catch (error) {
        if (tempItemId) {
            files = files.filter((f) => f.itemId !== tempItemId);
        }
        console.error('Google Drive Error:', error);
        toast.error(
            $i18n.t('Error accessing Google Drive: {{error}}', {
                error: error.message
            })
        );
    }
};
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` compiles successfully

#### Manual Verification:

- [ ] Pick a file from Google Drive → file chip with spinner appears **immediately** when picker closes
- [ ] After download + upload completes, spinner stops and file is usable
- [ ] Pick a Google Doc (Workspace file) → filename shows with correct extension (`.txt`, `.csv`)
- [ ] If download fails, placeholder is removed and error toast shown
- [ ] Cancel the picker → no placeholder remains

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: OneDrive Instant Feedback

### Overview

Add `onFilesSelected` callback to `pickAndDownloadFilesModal()` and use it in the OneDrive handler to show placeholders immediately.

### Changes Required:

#### 1. `pickAndDownloadFilesModal()` — add `onFilesSelected` callback

**File**: `src/lib/utils/onedrive-file-picker.ts`
**Lines**: 978-1001

```typescript
export async function pickAndDownloadFilesModal(
	authorityType?: 'personal' | 'organizations',
	options?: {
		onFilesSelected?: (items: Array<{ name: string }>) => void;
	}
): Promise<Array<{ blob: Blob; name: string }>> {
	const pickerResult = await openOneDriveFilePickerModal(authorityType);

	if (!pickerResult || !pickerResult.items || pickerResult.items.length === 0) {
		return [];
	}

	// NEW: Notify before downloads start
	options?.onFilesSelected?.(pickerResult.items.map((item) => ({ name: item.name })));

	// Download all selected files in parallel
	const downloadPromises = pickerResult.items.map(async (item) => {
		try {
			const blob = await downloadOneDriveFile(item, authorityType);
			return { blob, name: item.name };
		} catch (error) {
			console.error(`Failed to download file ${item.name}:`, error);
			return null;
		}
	});

	const results = await Promise.all(downloadPromises);
	return results.filter((result): result is { blob: Blob; name: string } => result !== null);
}
```

#### 2. OneDrive handler — create placeholders via callback

**File**: `src/lib/components/chat/MessageInput.svelte`
**Lines**: 562-578

```javascript
const oneDriveHandler = async (authorityType) => {
    const tempItemIds: string[] = [];
    try {
        const filesData = await pickAndDownloadFilesModal(authorityType, {
            onFilesSelected: (items) => {
                for (const item of items) {
                    const tempItemId = uuidv4();
                    tempItemIds.push(tempItemId);
                    files = [...files, {
                        type: 'file',
                        file: '',
                        id: null,
                        url: '',
                        name: item.name,
                        collection_name: '',
                        status: 'uploading',
                        size: 0,
                        error: '',
                        itemId: tempItemId
                    }];
                }
            }
        });
        if (filesData.length > 0) {
            for (let i = 0; i < filesData.length; i++) {
                const fileData = filesData[i];
                const file = new File([fileData.blob], fileData.name, {
                    type: fileData.blob.type || 'application/octet-stream'
                });
                // Match placeholder by name since download order may differ
                const matchingItemId = tempItemIds.find((id) => {
                    const item = files.find((f) => f.itemId === id);
                    return item && item.name === fileData.name;
                });
                await uploadFileHandler(file, true, {}, matchingItemId || null);
            }
            // Clean up any placeholders for files that failed to download
            // (filtered out by pickAndDownloadFilesModal)
            const downloadedNames = new Set(filesData.map((f) => f.name));
            files = files.filter((f) => {
                if (tempItemIds.includes(f.itemId) && !downloadedNames.has(f.name)) {
                    return false; // Remove placeholder for failed download
                }
                return true;
            });
        } else if (tempItemIds.length > 0) {
            // All downloads failed
            files = files.filter((f) => !tempItemIds.includes(f.itemId));
        }
    } catch (error) {
        // Clean up all placeholders on error
        if (tempItemIds.length > 0) {
            files = files.filter((f) => !tempItemIds.includes(f.itemId));
        }
        console.error('OneDrive Error:', error);
    }
};
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` compiles successfully

#### Manual Verification:

- [ ] Pick a file from OneDrive → file chip with spinner appears **immediately** when picker closes
- [ ] Pick multiple files → all file chips appear immediately
- [ ] After download + upload completes, spinners stop
- [ ] If one file fails to download, its placeholder is removed but others succeed
- [ ] Cancel the picker → no placeholders remain

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding.

---

## Testing Strategy

### Manual Testing Steps:

1. **Local file upload** — verify no regression (should be unchanged)
2. **Google Drive single file** — pick a native file (PDF, image) → instant placeholder → completes
3. **Google Drive Workspace file** — pick a Google Doc → placeholder shows `filename.txt` → completes
4. **Google Drive cancel** — open picker, cancel → no stale placeholders
5. **Google Drive auth failure** — test with expired token → placeholder cleaned up, error shown
6. **OneDrive single file** — pick one file → instant placeholder → completes
7. **OneDrive multiple files** — pick 3 files → 3 placeholders appear immediately → all complete
8. **OneDrive partial failure** — pick files where one is inaccessible → failed placeholder removed, others succeed
9. **OneDrive cancel** — open picker, cancel → no stale placeholders
10. **Dismiss during download** — click X on a cloud file placeholder while downloading → file removed cleanly

## Performance Considerations

- No performance impact — we're just creating a lightweight placeholder object earlier in the flow
- The actual download and upload timings are unchanged
- Minor memory overhead: one placeholder object exists during download (negligible)

## References

- Research: `thoughts/shared/research/2026-04-10-cloud-file-picker-instant-feedback.md`
- Google Drive picker: `src/lib/utils/google-drive-picker.ts`
- OneDrive picker: `src/lib/utils/onedrive-file-picker.ts`
- Upload handler: `src/lib/components/chat/MessageInput.svelte:626-738`
- File rendering: `src/lib/components/chat/MessageInput.svelte:1405-1425`
