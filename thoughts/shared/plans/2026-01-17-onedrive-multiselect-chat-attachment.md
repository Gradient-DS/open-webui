# OneDrive Multi-Select for Direct Chat Attachment Implementation Plan

## Overview

Enable users to select and attach multiple files from OneDrive when using the chat attachment feature. Currently users can only select one file at a time; this change allows selecting multiple files in a single picker session.

## Current State Analysis

The OneDrive file picker for chat attachment is limited to single-file selection:

1. **Picker Configuration** (`onedrive-file-picker.ts:553-585`): The `getFilePickerParams` function has no `selection` property, defaulting to single-select mode
2. **Result Handling** (`onedrive-file-picker.ts:907`): Only takes `pickerResult.items[0]`
3. **Upload Handler** (`MessageInput.svelte:1489-1503`): Expects single file result

### Key Discoveries:
- Microsoft OneDrive File Picker v8 SDK fully supports multi-select via `selection: { mode: 'multiple', enablePersistence: true }` - see research doc `thoughts/shared/research/2026-01-17-onedrive-multi-select-feasibility.md`
- The `downloadOneDriveFile` function already accepts individual file items and can be called multiple times
- Existing `uploadFileHandler` in MessageInput already handles individual files, so we can call it in a loop
- No backend changes required - existing upload flow works per-file

## Desired End State

Users can select multiple files from OneDrive in the chat attachment picker. All selected files are downloaded and uploaded as chat attachments in parallel.

### Verification:
1. Open OneDrive picker from chat input menu
2. Select multiple files (Ctrl/Cmd+click or shift+click)
3. All selected files appear as attachments in the chat input

## What We're NOT Doing

- Knowledge base sync multi-select (separate, more complex feature requiring backend changes)
- Folder selection for chat attachments
- Mixed file/folder selection
- Any backend API changes
- Maximum file count enforcement (already handled by existing `$config?.file?.max_count` check in `inputFilesHandler`)

## Implementation Approach

This is a frontend-only change with three modification points:
1. Add `selection` property to picker configuration
2. Create new function returning array of files
3. Update handler to process multiple files

---

## Phase 1: Update Picker Configuration and Types

### Overview
Add multi-select configuration to the file picker params and update TypeScript interfaces.

### Changes Required:

#### 1. Update `PickerParams` interface
**File**: `src/lib/utils/onedrive-file-picker.ts`
**Lines**: 198-215

Add optional `selection` property to the interface:

```typescript
interface PickerParams {
	sdk: string;
	entry: {
		oneDrive: Record<string, unknown>;
	};
	authentication: Record<string, unknown>;
	messaging: {
		origin: string;
		channelId: string;
	};
	search: {
		enabled: boolean;
	};
	selection?: {
		mode: 'single' | 'multiple' | 'pick';
		enablePersistence?: boolean;
		maximumCount?: number;
	};
	typesAndSources: {
		mode: string;
		pivots: Record<string, boolean>;
	};
}
```

#### 2. Add selection config to `getFilePickerParams`
**File**: `src/lib/utils/onedrive-file-picker.ts`
**Function**: `getFilePickerParams` (lines 553-585)

Add `selection` property to the params object:

```typescript
function getFilePickerParams(channelId: string): PickerParams {
	const config = OneDriveConfig.getInstance();

	const params: PickerParams = {
		sdk: '8.0',
		entry: {
			oneDrive: {}
		},
		authentication: {},
		messaging: {
			origin: window?.location?.origin || '',
			channelId
		},
		search: {
			enabled: true
		},
		selection: {
			mode: 'multiple',
			enablePersistence: true
		},
		typesAndSources: {
			mode: 'files',
			pivots: {
				oneDrive: true,
				recent: true,
				myOrganization: config.getAuthorityType() === 'organizations'
			}
		}
	};

	// For personal accounts, set files object in oneDrive
	if (config.getAuthorityType() !== 'organizations') {
		params.entry.oneDrive = { files: {} };
	}

	return params;
}
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compilation passes: `npm run check`
- [x] ESLint passes: `npm run lint:frontend`

#### Manual Verification:
- [ ] N/A for this phase (configuration only, tested in Phase 2)

---

## Phase 2: Create Multi-File Picker Function

### Overview
Create a new `pickAndDownloadFilesModal` function that returns an array of downloaded files instead of a single file.

### Changes Required:

#### 1. Create `pickAndDownloadFilesModal` function
**File**: `src/lib/utils/onedrive-file-picker.ts`
**Location**: After existing `pickAndDownloadFileModal` function (line 911)

```typescript
// Pick and download multiple files from OneDrive using modal (iframe version)
export async function pickAndDownloadFilesModal(
	authorityType?: 'personal' | 'organizations'
): Promise<Array<{ blob: Blob; name: string }>> {
	const pickerResult = await openOneDriveFilePickerModal(authorityType);

	if (!pickerResult || !pickerResult.items || pickerResult.items.length === 0) {
		return [];
	}

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

	// Filter out failed downloads
	return results.filter((result): result is { blob: Blob; name: string } => result !== null);
}
```

#### 2. Export the new function
**File**: `src/lib/utils/onedrive-file-picker.ts`
**Location**: The function is already exported via the `export async function` declaration

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compilation passes: `npm run check`
- [x] ESLint passes: `npm run lint:frontend`

#### Manual Verification:
- [ ] Open OneDrive picker, select multiple files, verify all download in browser console logs

---

## Phase 3: Update Upload Handler in MessageInput

### Overview
Update the `uploadOneDriveHandler` in MessageInput.svelte to use the new multi-file function and upload all selected files.

### Changes Required:

#### 1. Update import statement
**File**: `src/lib/components/chat/MessageInput.svelte`
**Line**: 17

Change import from:
```typescript
import { pickAndDownloadFileModal } from '$lib/utils/onedrive-file-picker';
```

To:
```typescript
import { pickAndDownloadFilesModal } from '$lib/utils/onedrive-file-picker';
```

#### 2. Update `uploadOneDriveHandler` callback
**File**: `src/lib/components/chat/MessageInput.svelte`
**Lines**: 1489-1503

Change from:
```typescript
uploadOneDriveHandler={async (authorityType) => {
	try {
		const fileData = await pickAndDownloadFileModal(authorityType);
		if (fileData) {
			const file = new File([fileData.blob], fileData.name, {
				type: fileData.blob.type || 'application/octet-stream'
			});
			await uploadFileHandler(file);
		} else {
			console.log('No file was selected from OneDrive');
		}
	} catch (error) {
		console.error('OneDrive Error:', error);
	}
}}
```

To:
```typescript
uploadOneDriveHandler={async (authorityType) => {
	try {
		const filesData = await pickAndDownloadFilesModal(authorityType);
		if (filesData.length > 0) {
			for (const fileData of filesData) {
				const file = new File([fileData.blob], fileData.name, {
					type: fileData.blob.type || 'application/octet-stream'
				});
				await uploadFileHandler(file);
			}
		} else {
			console.log('No files were selected from OneDrive');
		}
	} catch (error) {
		console.error('OneDrive Error:', error);
	}
}}
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compilation passes: `npm run check`
- [x] ESLint passes: `npm run lint:frontend`
- [x] Frontend builds successfully: `npm run build`

#### Manual Verification:
- [ ] Open chat, click + menu, select OneDrive (personal or work)
- [ ] In picker, select 3+ files using Ctrl/Cmd+click
- [ ] Click "Select" button in picker
- [ ] Verify all selected files appear as attachments in chat input
- [ ] Verify file count limit is respected (if configured)
- [ ] Verify partial failure handling: if one file fails to download, others still attach

---

## Testing Strategy

### Manual Testing Steps:
1. **Single file selection** - Verify existing behavior still works (selecting just one file)
2. **Multiple file selection** - Select 2-5 files, verify all attach
3. **Cancel with no selection** - Open picker, close without selecting, verify no errors
4. **Partial download failure** - Disconnect network mid-download, verify successful files still attach
5. **File count limit** - If `$config?.file?.max_count` is set, verify limit is enforced
6. **Both account types** - Test with personal OneDrive and work/school OneDrive

### Edge Cases:
- Empty selection (user clicks Select without choosing files)
- Very large files (existing size limits should apply)
- Mixed file types (PDFs, images, documents)

## Performance Considerations

- Files are downloaded in parallel using `Promise.all` for faster overall completion
- Each file upload happens sequentially to avoid overwhelming the backend
- Failed downloads are filtered out rather than failing the entire operation

## References

- Research document: `thoughts/shared/research/2026-01-17-onedrive-multi-select-feasibility.md`
- Microsoft OneDrive File Picker v8 documentation (selection options)
- Current single-file implementation: `onedrive-file-picker.ts:898-911`
