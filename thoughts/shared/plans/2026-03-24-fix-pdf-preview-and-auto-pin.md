# Fix PDF Preview in Citations & Auto-Pin Agents Implementation Plan

## Overview

Two bugs from the upstream v0.8.9 merge need fixing:
1. **PDF preview in citations** - Custom tabbed Preview/Content interface in `CitationModal.svelte` was overwritten by upstream's text-only version
2. **Agent auto-pinning** - New agents should be auto-pinned to the sidebar for their creator

## Current State Analysis

### CitationModal (Bug 1)
The upstream merge (`c26ae48d6`) replaced our custom `CitationModal.svelte` which had:
- File type detection (`isPDF`, `isImage`, `isAudio`)
- Tabbed Preview/Content interface
- Inline iframe for PDF viewing, `<img>` for images, `<audio>` for audio files
- HEAD request to check file availability

The upstream replacement adds valuable improvements we want to keep:
- Markdown rendering via `Markdown` component (with `renderMarkdownInPreviews` setting)
- Text fragment URL generation (`getTextFragmentUrl`)
- Expandable long documents (`CONTENT_PREVIEW_LIMIT`, `expandedDocs` Set)
- `aria-label` on close button
- `settings` store import for `iframeSandboxAllowSameOrigin` and `renderMarkdownInPreviews`

### Agent Auto-Pin (Bug 3)
`src/routes/(app)/workspace/models/create/+page.svelte` creates agents without pinning them. The existing `pinModelHandler` pattern in `Models.svelte:214-225` shows how to pin: append to `$settings.pinnedModels` and call `updateUserSettings`.

## Desired End State

1. Clicking a citation source opens a modal with:
   - **Preview tab** (default for PDFs/images/audio) showing the file inline
   - **Content tab** showing extracted text chunks with Markdown rendering
   - Graceful fallback to Content tab when file is unavailable
2. Newly created agents are automatically pinned to the creator's sidebar

### Verification:
- Upload a PDF, chat with RAG citations, click a source badge → Preview tab shows PDF inline
- Create a new agent → it appears in the sidebar pinned models section
- Another user sees their own pins unchanged

## What We're NOT Doing

- Not fixing prompt template bug (user is researching migration separately)
- Not adding auto-pin for imported agents (only creation flow)
- Not changing the `embed_url` side-panel flow in `Citations.svelte` (different code path)
- Not upgrading PDFViewer component (the iframe approach works for citation preview)

## Implementation Approach

Merge the old Gradient-DS preview features into the current upstream `CitationModal.svelte`, keeping all upstream improvements. For auto-pin, add a small block after successful agent creation.

## Phase 1: Restore PDF Preview in CitationModal

### Overview
Re-add the tabbed Preview/Content interface to `CitationModal.svelte` while preserving upstream's Markdown rendering, text fragment URLs, and expandable documents.

### Changes Required:

#### 1. CitationModal.svelte
**File**: `src/lib/components/chat/Messages/Citations/CitationModal.svelte`
**Changes**: Re-add preview state/logic to script, add tab switcher and preview rendering to template

**Script section additions** (after `let mergedDocuments = [];`):

```svelte
let selectedTab = 'preview';
let previewAvailable = true;
```

**Reactive declarations to add** (after the existing `$: if (citation) { ... }` block):

```svelte
// File type detection from first document's metadata
$: fileName = mergedDocuments?.[0]?.metadata?.name ?? citation?.source?.name ?? '';
$: fileId = mergedDocuments?.[0]?.metadata?.file_id;

$: isPDF = fileName?.toLowerCase().endsWith('.pdf');
$: isImage = /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(fileName);
$: isAudio = /\.(mp3|wav|ogg|m4a|webm)$/i.test(fileName);
$: isPreviewable = fileId && (isPDF || isImage || isAudio);

// Compute minimum page number across all chunks for PDF navigation
$: minPage = (() => {
    const pages = (mergedDocuments ?? [])
        .filter((d) => Number.isInteger(d?.metadata?.page))
        .map((d) => d.metadata.page);
    return pages.length > 0 ? Math.min(...pages) + 1 : undefined;
})();

// Preview URL for iframe/img/audio
$: previewUrl = fileId
    ? `${WEBUI_API_BASE_URL}/files/${fileId}/content${isPDF && minPage ? `#page=${minPage}` : ''}`
    : '';

// Check if file is still available when modal opens
$: if (show && fileId) {
    previewAvailable = true;
    fetch(`${WEBUI_API_BASE_URL}/files/${fileId}/content`, { method: 'HEAD' })
        .then((res) => {
            if (!res.ok) {
                previewAvailable = false;
                selectedTab = 'content';
            }
        })
        .catch(() => {
            previewAvailable = false;
            selectedTab = 'content';
        });
}
```

**Also add `selectedTab = 'preview';` inside the `$: if (citation) { ... }` block** (after `expandedDocs = new Set();`).

**Template changes** - Add tab switcher and preview rendering. The content area (currently starts at `<div class="flex flex-col md:flex-row w-full px-5 pb-5 md:space-x-4">`) becomes:

```svelte
<div class="flex flex-col w-full px-5 pb-5">
    <!-- Tab switcher: only shown for previewable file types with available files -->
    {#if isPreviewable && previewAvailable}
        <div class="flex gap-1 mb-3">
            <button
                class="px-3 py-1 text-xs font-medium rounded-lg transition {selectedTab === 'preview'
                    ? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
                    : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
                on:click={() => (selectedTab = 'preview')}
            >
                {$i18n.t('Preview')}
            </button>
            <button
                class="px-3 py-1 text-xs font-medium rounded-lg transition {selectedTab === 'content'
                    ? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
                    : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
                on:click={() => (selectedTab = 'content')}
            >
                {$i18n.t('Content')}
            </button>
        </div>
    {/if}

    <!-- Preview tab -->
    {#if isPreviewable && previewAvailable && selectedTab === 'preview'}
        {#if isPDF}
            <iframe
                title={fileName}
                src={previewUrl}
                class="w-full h-[70vh] border-0 rounded-lg"
            />
        {:else if isImage}
            <img
                src={previewUrl}
                alt={fileName}
                class="max-w-full max-h-[70vh] rounded-lg object-contain mx-auto"
            />
        {:else if isAudio}
            <audio
                src={previewUrl}
                class="w-full rounded-lg"
                controls
                playsinline
            />
        {/if}
    {:else}
        <!-- Content tab (upstream text view with Markdown) -->
        <div class="flex flex-col md:flex-row w-full md:space-x-4">
            <!-- ... existing upstream content rendering (mergedDocuments loop) ... -->
        </div>
    {/if}
</div>
```

### Success Criteria:

#### Automated Verification:
- [ ] `npm run build` completes successfully
- [ ] `npm run check` shows no new errors (existing ~8000 errors are pre-existing)

#### Manual Verification:
- [ ] Upload a PDF to a knowledge base, chat with RAG, click a citation → Preview tab shows PDF in iframe
- [ ] Click "Content" tab → shows extracted text chunks with Markdown rendering
- [ ] Citation for a non-file web source → no tab switcher, shows text content directly
- [ ] Citation for a deleted/unavailable file → falls back to Content tab automatically
- [ ] Image file citation → shows image in Preview tab
- [ ] Expandable long documents still work in Content tab
- [ ] Text fragment URLs still work for web sources

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Auto-Pin Agents for Creator

### Overview
After successfully creating a new agent, automatically pin it to the creating user's sidebar.

### Changes Required:

#### 1. Agent Creation Page
**File**: `src/routes/(app)/workspace/models/create/+page.svelte`
**Changes**: Import `updateUserSettings`, add auto-pin logic after successful creation

Add imports:
```svelte
import { updateUserSettings } from '$lib/apis/users';
```

Add auto-pin logic inside `onSubmit`, after `models.set(...)` succeeds (line 55-61) and before `toast.success`:

```js
// Auto-pin the newly created agent for the creator
const pinnedModels = $settings?.pinnedModels ?? [];
if (!pinnedModels.includes(modelInfo.id)) {
    const updatedPinned = [...new Set([...pinnedModels, modelInfo.id])];
    settings.set({ ...$settings, pinnedModels: updatedPinned });
    await updateUserSettings(localStorage.token, { ui: $settings });
}
```

This mirrors the existing `pinModelHandler` pattern from `Models.svelte:214-225`.

### Success Criteria:

#### Automated Verification:
- [ ] `npm run build` completes successfully

#### Manual Verification:
- [ ] Create a new agent → it appears in the sidebar "Models & agents" section
- [ ] The agent can be unpinned via the sidebar unpin button or 3-dot menu
- [ ] Creating an agent when sidebar section was previously hidden → section becomes visible
- [ ] Other users' pinned models are not affected
- [ ] Re-creating an agent with the same ID (after deletion) doesn't create duplicate pins

---

## Testing Strategy

### Manual Testing Steps:
1. **PDF Preview**: Upload PDF → Chat with RAG → Click citation badge → Verify Preview/Content tabs
2. **Image Preview**: Upload an image file → Chat → Click citation → Verify image preview
3. **Fallback**: Delete a file from storage → Click its citation → Verify auto-fallback to Content tab
4. **Auto-Pin**: Create new agent → Check sidebar → Verify it appears pinned
5. **Unpin**: Unpin the auto-pinned agent → Verify it disappears from sidebar

## References

- Research document: `thoughts/shared/research/2026-03-24-post-merge-bugs.md`
- Old CitationModal: `git show c26ae48d6^:src/lib/components/chat/Messages/Citations/CitationModal.svelte`
- Pin handler pattern: `src/lib/components/workspace/Models.svelte:214-225`
- Upstream merge commit: `c26ae48d6`
