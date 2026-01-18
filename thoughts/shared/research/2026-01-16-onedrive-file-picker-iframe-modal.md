---
date: 2026-01-16T12:00:00+01:00
researcher: Claude
git_commit: 4248118b6d93f135979ca7094edf5f44a3f53494
branch: feat/onedrive
repository: Gradient-DS/open-webui
topic: "OneDrive File Picker - Iframe Modal vs Popup Window"
tags: [research, codebase, onedrive, file-picker, iframe, modal]
status: complete
last_updated: 2026-01-16
last_updated_by: Claude
---

# Research: OneDrive File Picker - Iframe Modal vs Popup Window

**Date**: 2026-01-16T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: 4248118b6d93f135979ca7094edf5f44a3f53494
**Branch**: feat/onedrive
**Repository**: Gradient-DS/open-webui

## Research Question

Can the OneDrive Business file picker in the chat input box use an iframe modal (like the collections/knowledge base integration) instead of opening a new tab/popup?

## Summary

**Yes, this is absolutely feasible.** The codebase already has two different picker implementations in `src/lib/utils/onedrive-file-picker.ts`:

| Function | Display Mode | Use Case |
|----------|-------------|----------|
| `openOneDrivePicker()` | **Popup window** (`window.open`) | Chat input file attachments |
| `openOneDriveFolderPicker()` | **Iframe modal** | Knowledge base folder sync |

The iframe modal approach is already implemented for folders. Creating a file picker with the same iframe modal approach requires:
1. Creating a new `openOneDriveFilePickerModal()` function (or modifying `openOneDrivePicker`)
2. Updating `MessageInput.svelte` to use the new function

## Detailed Findings

### Current Chat Input Implementation (Popup)

**Entry Point**: `src/lib/components/chat/MessageInput.svelte:1489-1503`

```typescript
uploadOneDriveHandler={async (authorityType) => {
    const fileData = await pickAndDownloadFile(authorityType);
    // ... creates File object and uploads
}}
```

**Picker Function**: `src/lib/utils/onedrive-file-picker.ts:356-507`

The `openOneDrivePicker()` function uses `window.open()`:

```typescript
// Line 468
pickerWindow = window.open('', 'OneDrivePicker', 'width=800,height=600');
```

### Current Knowledge Base Implementation (Iframe Modal)

**Entry Point**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:527`

```typescript
const folder = await openOneDriveFolderPicker('organizations');
```

**Picker Function**: `src/lib/utils/onedrive-file-picker.ts:526-846`

The `openOneDriveFolderPicker()` creates a modal overlay with embedded iframe:

```typescript
// Lines 552-566 - Modal overlay with backdrop blur
const modalOverlay = document.createElement('div');
modalOverlay.style.cssText = `
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: rgba(0, 0, 0, 0.5);
    z-index: 10000;
    display: flex;
    justify-content: center;
    align-items: center;
    backdrop-filter: blur(2px);
`;

// Lines 670-677 - Iframe element
pickerIframe = document.createElement('iframe');
pickerIframe.name = iframeName;
pickerIframe.style.cssText = `width: 100%; height: 100%; border: none;`;
```

### Key Differences Between Implementations

| Aspect | Popup (`openOneDrivePicker`) | Iframe Modal (`openOneDriveFolderPicker`) |
|--------|------------------------------|-------------------------------------------|
| Display | Separate browser window | Embedded in page overlay |
| Size | 800x600 fixed | 90% width (max 1000px), 85% height (max 700px) |
| Token refresh | Popup allowed | Silent-only (no popup in iframe) |
| Close mechanisms | Window close only | Close button, Escape key, click outside |
| Picker params mode | `'files'` | `'folders'` |
| UX | Tab switching required | In-page experience |

### Implementation Approach

To add an iframe modal file picker:

**Option A: Create new function** (Recommended)
- Create `openOneDriveFilePickerModal()` by copying `openOneDriveFolderPicker()`
- Change `getFolderPickerParams()` to `getPickerParams()` (mode: 'files')
- Update return type from `FolderPickerResult` to file selection result

**Option B: Add parameter to existing function**
- Add `displayMode: 'popup' | 'modal'` parameter to `openOneDrivePicker()`
- Conditionally create popup or modal based on parameter

### Code Changes Required

1. **`src/lib/utils/onedrive-file-picker.ts`**:
   - Create `openOneDriveFilePickerModal()` function (~320 lines, mostly copied from folder picker)
   - Export the new function

2. **`src/lib/components/chat/MessageInput.svelte`**:
   - Import `openOneDriveFilePickerModal` instead of `pickAndDownloadFile`
   - Update `uploadOneDriveHandler` to use modal picker
   - Handle the file download after selection

### Token Handling Consideration

The iframe implementation uses `getTokenSilent()` because popup authentication doesn't work from within iframes. If the user's token expires mid-session:

- **Popup approach**: Can trigger `loginPopup()` for re-auth
- **Iframe approach**: Must handle silently or show error

The folder picker handles this at lines 766-787:
```typescript
case 'authenticate': {
    const newToken = await getTokenSilent(resource, authorityType);
    if (newToken) {
        channelPort?.postMessage({ type: 'result', result: newToken });
    } else {
        // Silent acquisition failed - user may need to re-auth
        channelPort?.postMessage({ type: 'result', result: null });
    }
}
```

## Code References

- `src/lib/utils/onedrive-file-picker.ts:356-507` - Popup file picker implementation
- `src/lib/utils/onedrive-file-picker.ts:526-846` - Iframe folder picker implementation
- `src/lib/utils/onedrive-file-picker.ts:233-266` - File picker params (mode: 'files')
- `src/lib/utils/onedrive-file-picker.ts:269-301` - Folder picker params (mode: 'folders')
- `src/lib/components/chat/MessageInput.svelte:1489-1503` - Chat upload handler
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:522-557` - Knowledge sync handler

## Architecture Insights

The OneDrive picker utility follows a **strategy pattern** where:
- Common authentication/token handling is shared
- Display mechanism (popup vs iframe) varies by use case
- Microsoft's FilePicker.aspx works identically in both contexts

The iframe modal provides better UX because:
- No tab switching
- Consistent with other modals in the app
- Close via Escape key or click-outside
- Loading state with spinner

## Open Questions

1. Should we deprecate the popup implementation entirely, or keep both options?
2. Should token refresh failure in iframe context trigger a toast notification prompting the user to re-authenticate?
3. Is there a preference for Option A (new function) vs Option B (parameter)?
