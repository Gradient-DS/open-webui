---
date: 2026-04-10T09:34:00+02:00
researcher: Claude
git_commit: 9ab05599354f1df089058126f0fdf279d8bda99f
branch: feat/proprietary-warnings
repository: open-webui
topic: 'Make cloud file selection appear instantly in chat input like local files'
tags: [research, codebase, google-drive, onedrive, file-upload, UX, chat-input]
status: complete
last_updated: 2026-04-10
last_updated_by: Claude
---

# Research: Instant Cloud File Picker Feedback in Chat Input

**Date**: 2026-04-10T09:34:00+02:00
**Researcher**: Claude
**Git Commit**: 9ab05599
**Branch**: feat/proprietary-warnings
**Repository**: open-webui

## Research Question

When selecting a file from Google Drive or OneDrive, there's a 1-2 second delay before it appears in the chat input. Can we make this instant, like local file selection?

## Summary

The delay is caused by the cloud file being **downloaded from the provider's API before any UI feedback is shown**. Local files skip this step (they're already in memory), so the placeholder appears instantly. The fix is to show a loading placeholder immediately when the user picks a file in the cloud picker, then download and upload in the background.

## Root Cause Analysis

### Local File Flow (instant)

```
User picks file in OS dialog
    → uploadFileHandler() called immediately (file already in memory)
    → Placeholder with status:'uploading' added to files[] → APPEARS IN UI
    → Upload to backend happens in background
    → Socket.IO status event → spinner stops
```

The key: `uploadFileHandler` (`MessageInput.svelte:637-657`) creates a placeholder **before** any network call. The file object is already available from the OS file picker.

### Cloud File Flow (delayed)

```
User picks file in cloud picker UI
    → BLOCKING: File downloaded from Google/OneDrive API (1-2+ seconds)
    → Handler receives blob, wraps in File object
    → uploadFileHandler() called → Placeholder added → APPEARS IN UI
    → Upload to backend happens in background
```

The delay is between the user clicking "Select" in the picker and the placeholder appearing. The entire download must complete before `uploadFileHandler` is ever called.

## Detailed Findings

### Google Drive: `createPicker()` blocks on download

`src/lib/utils/google-drive-picker.ts:213-265`

The picker callback fires when the user selects a file (line 213). At this point, file metadata is available:

- `fileName` (line 218)
- `mimeType` (line 219)
- `fileId` (line 217)

But the function then downloads the file (lines 249-258) and only resolves the promise after the blob is ready (line 259). The handler in `MessageInput.svelte:541-560` awaits this entire process before calling `uploadFileHandler`.

### OneDrive: `pickAndDownloadFilesModal()` blocks on download

`src/lib/utils/onedrive-file-picker.ts:978-1001`

The picker modal returns file items with names at line 981. But then all files are downloaded in parallel (lines 988-998) before the function returns. The handler in `MessageInput.svelte:562-578` awaits all downloads before calling `uploadFileHandler`.

### The shared upload handler creates instant feedback

`src/lib/components/chat/MessageInput.svelte:637-657`

```js
const fileItem = {
	type: 'file',
	name: file.name,
	status: 'uploading', // Shows loading spinner
	size: file.size
	// ...
};
files = [...files, fileItem]; // Line 657 — instant UI update
```

This is the mechanism we want to trigger **before** the cloud download.

## Proposed Solution

### Approach: `onFileSelected` callback

Add an `onFileSelected` callback parameter to both picker functions. When the user picks a file in the picker UI (before download), call this callback with file metadata. The handler uses this to create a placeholder immediately.

### Google Drive Changes

**`src/lib/utils/google-drive-picker.ts`** — Add `onFileSelected` callback:

```typescript
export const createPicker = (options?: {
    onFileSelected?: (metadata: { name: string; mimeType: string }) => void;
}) => {
    return new Promise(async (resolve, reject) => {
        // ... existing init code ...
        .setCallback(async (data: any) => {
            if (data[...ACTION] === ...PICKED) {
                const doc = data[...DOCUMENTS][0];
                const fileName = doc[...NAME];
                const mimeType = doc[...MIME_TYPE];

                // NEW: Immediate feedback before download
                let effectiveName = fileName;
                // ... existing name/extension logic ...
                options?.onFileSelected?.({ name: effectiveName, mimeType });

                // Existing download logic continues...
                const response = await fetch(downloadUrl, ...);
                const blob = await response.blob();
                resolve({ id, name: effectiveName, blob, ... });
            }
        })
    });
};
```

**`src/lib/components/chat/MessageInput.svelte`** — Google Drive handler:

```javascript
const googleDriveHandler = async () => {
    let tempItemId: string | null = null;
    try {
        const fileData = await createPicker({
            onFileSelected: ({ name }) => {
                // Create placeholder immediately
                tempItemId = crypto.randomUUID();
                const fileItem = {
                    type: 'file', file: '', id: null, url: '', name,
                    collection_name: '', status: 'uploading', size: 0,
                    error: '', itemId: tempItemId
                };
                files = [...files, fileItem];
            }
        });
        if (fileData) {
            const file = new File([fileData.blob], fileData.name, {
                type: fileData.blob.type
            });
            // Find and update existing placeholder instead of creating new one
            if (tempItemId) {
                const idx = files.findIndex(f => f.itemId === tempItemId);
                if (idx !== -1) {
                    // Remove placeholder, let uploadFileHandler create the real one
                    files = files.filter(f => f.itemId !== tempItemId);
                }
            }
            await uploadFileHandler(file);
        } else if (tempItemId) {
            // User cancelled after initial selection (unlikely but safe)
            files = files.filter(f => f.itemId !== tempItemId);
        }
    } catch (error) {
        if (tempItemId) {
            files = files.filter(f => f.itemId !== tempItemId);
        }
        // ... existing error handling ...
    }
};
```

### OneDrive Changes

**`src/lib/utils/onedrive-file-picker.ts`** — Add `onFilesSelected` callback:

```typescript
export async function pickAndDownloadFilesModal(
    authorityType?: 'personal' | 'organizations',
    options?: {
        onFilesSelected?: (items: Array<{ name: string }>) => void;
    }
): Promise<Array<{ blob: Blob; name: string }>> {
    const pickerResult = await openOneDriveFilePickerModal(authorityType);
    if (!pickerResult?.items?.length) return [];

    // NEW: Immediate feedback before download
    options?.onFilesSelected?.(pickerResult.items.map(i => ({ name: i.name })));

    // Existing download logic continues...
    const downloadPromises = pickerResult.items.map(async (item) => { ... });
    return (await Promise.all(downloadPromises)).filter(Boolean);
}
```

**`src/lib/components/chat/MessageInput.svelte`** — OneDrive handler:

```javascript
const oneDriveHandler = async (authorityType) => {
    const tempItemIds: string[] = [];
    try {
        const filesData = await pickAndDownloadFilesModal(authorityType, {
            onFilesSelected: (items) => {
                for (const item of items) {
                    const tempItemId = crypto.randomUUID();
                    tempItemIds.push(tempItemId);
                    const fileItem = {
                        type: 'file', file: '', id: null, url: '', name: item.name,
                        collection_name: '', status: 'uploading', size: 0,
                        error: '', itemId: tempItemId
                    };
                    files = [...files, fileItem];
                }
            }
        });
        // Remove placeholders, let uploadFileHandler create real items
        files = files.filter(f => !tempItemIds.includes(f.itemId));
        if (filesData.length > 0) {
            for (const fileData of filesData) {
                const file = new File([fileData.blob], fileData.name, {
                    type: fileData.blob.type || 'application/octet-stream'
                });
                await uploadFileHandler(file);
            }
        }
    } catch (error) {
        files = files.filter(f => !tempItemIds.includes(f.itemId));
        // ... existing error handling ...
    }
};
```

### Alternative: Reuse placeholder (more elegant)

Instead of removing the placeholder and recreating via `uploadFileHandler`, we could modify `uploadFileHandler` to accept an existing `itemId` and update the existing placeholder rather than creating a new one. This avoids the brief visual flicker of remove+add.

Add an `existingItemId` parameter to `uploadFileHandler`:

```javascript
const uploadFileHandler = async (file, process = true, itemData = {}, existingItemId = null) => {
	// ... permission checks ...

	let fileItem;
	if (existingItemId) {
		const idx = files.findIndex((f) => f.itemId === existingItemId);
		if (idx !== -1) {
			fileItem = files[idx];
			fileItem.size = file.size;
			files = files; // trigger reactivity
		}
	}
	if (!fileItem) {
		fileItem = {
			/* existing creation logic */
		};
		files = [...files, fileItem];
	}

	// ... rest of upload logic unchanged, mutates fileItem in place ...
};
```

This is the cleaner approach — **recommended**.

## Architecture Insights

- The `uploadFileHandler` already creates instant placeholders — the infrastructure exists, it just needs to be triggered earlier in the cloud flow
- The callback approach is minimally invasive: picker functions gain an optional parameter, existing callers are unaffected
- The `itemId` field (UUID) already exists on file items for exactly this kind of pre-upload tracking — it was designed for this
- OneDrive downloads files in parallel (`Promise.all`) but the handler then uploads them sequentially (`for...of await`) — this is a separate optimization opportunity

## Code References

- `src/lib/components/chat/MessageInput.svelte:541-560` — Google Drive handler (blocks on createPicker)
- `src/lib/components/chat/MessageInput.svelte:562-578` — OneDrive handler (blocks on pickAndDownloadFilesModal)
- `src/lib/components/chat/MessageInput.svelte:637-657` — uploadFileHandler placeholder creation
- `src/lib/utils/google-drive-picker.ts:213-265` — Picker callback: has metadata at 218, downloads at 249-258
- `src/lib/utils/onedrive-file-picker.ts:978-1001` — pickAndDownloadFilesModal: has items at 981, downloads at 988-998
- `src/lib/components/chat/Chat.svelte:431-450` — Socket.IO file status handler

## Open Questions

1. **Error UX**: If the cloud download fails after showing the placeholder, should we show an error state on the file item or remove it with a toast? Currently `uploadFileHandler` removes failed items (line 703/707).
2. **Size display**: The placeholder will show `size: 0` since we don't know the file size until download completes. The `FileItem` component may or may not display size — worth checking if this looks odd.
3. **Google Workspace export**: For Google Docs/Sheets, the effective filename (with added extension like `.txt`, `.csv`) is computed before download. The `onFileSelected` callback should use the effective name, not the raw picker name. The proposed solution handles this.
