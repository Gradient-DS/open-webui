---
date: 2026-02-16T15:00:00+01:00
researcher: claude
git_commit: bcd4a67876bd5b4450d45bbd9971bfe01fc0b229
branch: feat/sync-improvements
repository: open-webui
topic: "Knowledge Base Empty State Cards - Upload Options UI"
tags: [research, codebase, knowledge-base, frontend, empty-state, ux]
status: complete
last_updated: 2026-02-16
last_updated_by: claude
---

# Research: Knowledge Base Empty State Cards

**Date**: 2026-02-16T15:00:00+01:00
**Researcher**: claude
**Git Commit**: bcd4a67876bd5b4450d45bbd9971bfe01fc0b229
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

When a knowledge base is empty, replace the minimal "No content found" text with big clickable cards for each upload option. For single-option KBs (e.g. OneDrive), auto-open the picker on creation. For multi-option KBs, show a responsive grid of cards. Once files exist, show the existing file list UI unchanged.

## Summary

This is a **frontend-only change** touching primarily `KnowledgeBase.svelte` (the empty state branch at lines 1724-1729) and potentially a new `EmptyStateCards.svelte` component. The change is well-isolated: the existing `{:else}` branch after `{#if fileItems.length > 0}` is the single insertion point. The existing `AddContentMenu` dropdown and `+` button remain untouched (they continue to work when files exist). Minimal upstream conflict risk since only the empty state area changes.

## Detailed Findings

### Current Empty State (the problem)

**File:** `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1724-1729`

When `fileItems.length === 0`, the current UI shows:
```html
<div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
    <div>{$i18n.t('No content found')}</div>
</div>
```
This is a single line of small gray text. No call to action, no visual hint about what to do next.

### How Upload Options Are Currently Determined

The available options depend on `knowledge.type`:

| KB Type | Available Actions | Current UI (top-right) |
|---------|-------------------|----------------------|
| `local` (or unset) | Upload files, Upload directory, Add webpage, Add text content | `AddContentMenu` dropdown (4 items) |
| `onedrive` | Sync from OneDrive | Single `+` button → `oneDriveSyncHandler()` |

**Key branching logic** at `KnowledgeBase.svelte:1560-1597`:
- `knowledge?.type === 'onedrive'` → single sync button
- else → `AddContentMenu` dropdown with 4 options

### Auto-Start Sync on Creation (already exists for OneDrive)

**File:** `KnowledgeBase.svelte:1262-1270`

OneDrive KBs already auto-open the item picker on creation via the `?start_onedrive_sync=true` URL parameter. This means the "single option → auto-open" behavior already works for OneDrive. If the user closes the picker without selecting, they currently see the empty "No content found" text.

### AddContentMenu Options (for local KBs)

**File:** `src/lib/components/workspace/Knowledge/KnowledgeBase/AddContentMenu.svelte`

The dropdown has these items:
1. **Upload files** (`ArrowUpCircle` icon) → triggers hidden file input
2. **Upload directory** (`FolderOpen` icon) → triggers directory upload handler
3. **Add webpage** (`GlobeAlt` icon) → opens URL modal
4. **Add text content** (`BarsArrowUp` icon) → opens text content modal
5. **Sync from OneDrive** (`CloudArrowUp` icon) → conditional, only if `onOneDriveSync` prop is non-null

### Existing Icons Available

All icons used in `AddContentMenu` are already imported as Svelte components:
- `ArrowUpCircle` from `$lib/components/icons/ArrowUpCircle.svelte`
- `FolderOpen` from `$lib/components/icons/FolderOpen.svelte`
- `GlobeAlt` from `$lib/components/icons/GlobeAlt.svelte`
- `BarsArrowUp` from `$lib/components/icons/BarsArrowUp.svelte`
- `CloudArrowUp` from `$lib/components/icons/CloudArrowUp.svelte`
- `OneDrive` from `$lib/components/icons/OneDrive.svelte`

### Existing UI Patterns to Model After

**Dashed border upload buttons** (e.g. `AddUserModal.svelte:252`):
```html
<button class="w-full text-sm font-medium py-3 bg-transparent hover:bg-gray-100
    border border-dashed dark:border-gray-850 dark:hover:bg-gray-850
    text-center rounded-xl">
```

**Grid layouts** used in workspace pages:
- `grid grid-cols-1 lg:grid-cols-2 gap-2` (Knowledge list)
- `gap-2.5 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3` (Notes)

## Proposed Implementation

### Option Count Logic

```typescript
function getUploadOptions(knowledgeType: string): UploadOption[] {
    if (knowledgeType === 'onedrive') {
        return [{ type: 'onedrive', label: 'Sync from OneDrive', icon: OneDrive }];
    }
    // Local KB - 4 options
    return [
        { type: 'files', label: 'Upload files', icon: ArrowUpCircle },
        { type: 'directory', label: 'Upload directory', icon: FolderOpen },
        { type: 'web', label: 'Add webpage', icon: GlobeAlt },
        { type: 'text', label: 'Add text content', icon: BarsArrowUp },
    ];
}
```

### Grid Layout Rules

| Count | Layout | CSS |
|-------|--------|-----|
| 1 | Single centered card (full width) | `grid grid-cols-1 max-w-md mx-auto` |
| 2 | 1 row × 2 | `grid grid-cols-2 gap-4` |
| 3 | 1 row × 3 | `grid grid-cols-3 gap-4` |
| 4 | 2 × 2 | `grid grid-cols-2 gap-4` |
| 5 | 3 + 2 | `grid grid-cols-3 gap-4` (last row auto-centers) |
| 6 | 3 + 3 | `grid grid-cols-3 gap-4` |

### Files to Change

1. **`KnowledgeBase.svelte`** (lines 1724-1729): Replace the `{:else}` branch with the new empty state cards component. This is the main change.

2. **New: `EmptyStateCards.svelte`** (optional, could inline): A new component in `src/lib/components/workspace/Knowledge/KnowledgeBase/` that renders the card grid. Props: `knowledgeType`, `onAction` callback.

3. **No changes needed to:** `AddContentMenu.svelte`, `Files.svelte`, `SourceGroupedFiles.svelte`, `CreateKnowledgeBase.svelte`, or any backend files.

### Exact Insertion Point

Replace `KnowledgeBase.svelte:1724-1729`:
```svelte
{:else}
    <div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
        <div>{$i18n.t('No content found')}</div>
    </div>
```

With:
```svelte
{:else}
    {#if knowledge?.write_access}
        <!-- New empty state cards grid -->
        <EmptyStateCards
            knowledgeType={knowledge?.type}
            onAction={(type) => { /* dispatch to same handlers as AddContentMenu */ }}
        />
    {:else}
        <div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
            <div>{$i18n.t('No content found')}</div>
        </div>
    {/if}
```

### Card Design (matching Open WebUI style)

Each card should be a clickable area with:
- `border border-dashed border-gray-200 dark:border-gray-700` (dashed border like the reference image)
- `rounded-2xl` (consistent with dropdown menus)
- `hover:bg-gray-50 dark:hover:bg-gray-850` (standard hover)
- `transition` for smooth hover
- Large centered icon (`size-8` or `size-10`)
- Label text below (`text-sm font-medium text-gray-600 dark:text-gray-400`)
- `p-8` for generous padding
- `cursor-pointer`

### Auto-Open Behavior

- **OneDrive (1 option)**: Already auto-opens via `?start_onedrive_sync=true`. If closed without selecting, shows the single card.
- **Local (4 options)**: No auto-open, shows 2×2 grid of cards.
- **Future types with 1 option**: Should auto-open that option on creation (extend `CreateKnowledgeBase.svelte` redirect logic).

### Upstream Conflict Risk

**Very low.** The change only touches:
- The `{:else}` branch at line 1724-1729 (a 6-line block) — unlikely to be modified upstream
- A new component file (no conflicts possible)
- No changes to existing component signatures or APIs

## Code References

- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1724-1729` — Current empty state (replacement target)
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1560-1597` — Type-based branching for add button
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1262-1270` — Auto-start OneDrive sync
- `src/lib/components/workspace/Knowledge/KnowledgeBase/AddContentMenu.svelte` — Current dropdown menu with option list
- `src/lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte:46-50` — Post-creation redirect logic
- `src/lib/components/admin/Users/UserList/AddUserModal.svelte:252` — Dashed border button pattern

## Open Questions

1. Should the empty state cards be shown even when `query` is non-empty (i.e., search returned no results on a non-empty KB)? Probably not — the cards should only show when `fileItemsTotal === 0` (truly empty KB), not when a search filter yields 0 results.
2. Should the card labels be translatable? Yes, reuse existing i18n keys like `'Upload files'`, `'Upload directory'`, `'Add webpage'`, `'Add text content'`, `'Sync from OneDrive'`.
3. For future KB types (e.g., SharePoint, Google Drive), should the card system be extensible? Yes — the option list should be data-driven based on KB type.
