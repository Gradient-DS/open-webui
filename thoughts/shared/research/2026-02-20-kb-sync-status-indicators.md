---
date: 2026-02-20T12:00:00+01:00
researcher: claude
git_commit: b439ebb4a7772afd6a6fc60cc39bc9cced7d651c
branch: feat/sync-improvements
repository: open-webui
topic: "Adding sync status indicators to KB overview cards and detail page"
tags: [research, codebase, knowledge-base, onedrive, sync, spinner, ui]
status: complete
last_updated: 2026-02-20
last_updated_by: claude
---

# Research: KB Sync Status Indicators

**Date**: 2026-02-20
**Researcher**: Claude
**Git Commit**: b439ebb4a7772afd6a6fc60cc39bc9cced7d651c
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

How to add sync status indicators (spinner/syncing badge) to:
1. The KB overview/list page cards for OneDrive KBs that are actively syncing
2. Potentially improve the detail page sync indicator (currently only file count)

## Summary

- **KB list page** (`Knowledge.svelte`): Cards are rendered inline in an `{#each}` loop. Each card shows type badge, name, timestamp, owner. **No sync indicator exists**.
- **The `meta` field (including `meta.onedrive_sync.status`) is already returned** by the search API endpoint, so the frontend already has access to sync status in the list view -- it just doesn't display it.
- **Socket.IO events** (`onedrive:sync:progress`) are emitted to `user:{user_id}` room with `knowledge_id` and `status` fields -- these can be listened to from the list page for real-time updates.
- **Spinner component** (`Spinner.svelte`) is already imported in `Knowledge.svelte`.
- **New translation keys needed**: "Syncing" (for badge or indicator text).

## Detailed Findings

### 1. KB List Page -- Current Card Structure

**File**: `src/lib/components/workspace/Knowledge.svelte` (lines 245-327)

Cards are rendered inline as `<button>` elements inside an `{#each items as item}` loop. The card layout:

```
┌──────────────────────────────────────────────────────┐
│ [OneDrive] badge    [Read Only] badge    [...] menu  │
│ Name                Updated 2h ago   By Owner        │
└──────────────────────────────────────────────────────┘
```

The top row (lines 263-292) has the type Badge on the left and ItemMenu on the right. The badge area (lines 264-277) is where a sync indicator could be added.

### 2. Sync Status Already Available in List Data

The search API returns `KnowledgeAccessResponse` which extends `KnowledgeModel` → includes `meta: Optional[dict]`. The `meta.onedrive_sync.status` field contains one of: `"idle"`, `"syncing"`, `"completed"`, `"completed_with_errors"`, `"failed"`, `"cancelled"`, `"access_revoked"`, `"file_limit_exceeded"`.

So `item.meta?.onedrive_sync?.status === 'syncing'` is already available in the list view without any backend changes.

### 3. Real-Time Updates via Socket.IO

The `onedrive:sync:progress` event (emitted from `backend/open_webui/services/onedrive/sync_events.py:108`) includes:

```python
{
    "knowledge_id": str,
    "status": str,  # "syncing", "completed", etc.
    "current": int,
    "total": int,
    "filename": str,
    ...
}
```

This event is emitted to room `user:{user_id}`, so it's available to any page the user has open. Currently only `KnowledgeBase.svelte` (detail page) listens for it.

### 4. Existing Spinner Import

`Knowledge.svelte` already imports `Spinner` at line 26:
```svelte
import Spinner from '../common/Spinner.svelte';
```

It's currently used for the page loading state and infinite scroll loader, but can be reused for per-card indicators.

### 5. Approach Options for KB Card Sync Indicator

#### Option A: Spinner next to OneDrive Badge
Add a small `Spinner` next to the "OneDrive" badge when `item.meta?.onedrive_sync?.status === 'syncing'`:

```svelte
{#if item?.type === 'onedrive'}
    <Badge type="info" content={$i18n.t('OneDrive')} />
    {#if item?.meta?.onedrive_sync?.status === 'syncing'}
        <Spinner className="size-3" />
    {/if}
{:else}
    <Badge type="muted" content={$i18n.t('Local')} />
{/if}
```

#### Option B: Replace Badge with "Syncing" Badge
When syncing, show a "Syncing" badge with a different color instead of (or alongside) the "OneDrive" badge:

```svelte
{#if item?.type === 'onedrive' && item?.meta?.onedrive_sync?.status === 'syncing'}
    <div class="flex items-center gap-1">
        <Badge type="info" content={$i18n.t('OneDrive')} />
        <Spinner className="size-3 text-blue-500" />
    </div>
{:else if item?.type === 'onedrive'}
    <Badge type="info" content={$i18n.t('OneDrive')} />
{:else}
    <Badge type="muted" content={$i18n.t('Local')} />
{/if}
```

#### Option C: Badge + Spinner + Tooltip with progress
Most informative but more complex. Show spinner with tooltip showing progress:

```svelte
{#if item?.type === 'onedrive'}
    <Badge type="info" content={$i18n.t('OneDrive')} />
    {#if item?.meta?.onedrive_sync?.status === 'syncing'}
        <Tooltip content={$i18n.t('Syncing...')}>
            <Spinner className="size-3 text-blue-500" />
        </Tooltip>
    {/if}
{/if}
```

### 6. Real-Time Updates for List Page

To make the spinner appear/disappear in real-time (without page refresh), the list page needs to listen for Socket.IO events:

```svelte
// In Knowledge.svelte onMount
$socket?.on('onedrive:sync:progress', (data) => {
    const { knowledge_id, status } = data;
    if (items) {
        items = items.map(item => {
            if (item.id === knowledge_id) {
                return {
                    ...item,
                    meta: {
                        ...item.meta,
                        onedrive_sync: {
                            ...item.meta?.onedrive_sync,
                            status
                        }
                    }
                };
            }
            return item;
        });
    }
});
```

Socket store is available via `import { socket } from '$lib/stores';` (already used in `KnowledgeBase.svelte`).

### 7. Translation Keys Needed

**New keys to add:**

| Key | en-US | nl-NL |
|-----|-------|-------|
| `"Syncing..."` | `""` | `"Synchroniseren..."` |

**Existing keys that may be relevant:**
- `"Starting sync..."` / `"Synchronisatie starten..."` (already exists)
- `"Sync progress"` / `"Synchronisatievoortgang"` (already exists)

### 8. Detail Page -- Current Sync Indicator

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte` (lines 1501-1525)

When syncing, the header shows file count as `{fileItemsTotal} / {progress_total}` in blue text. When there's no progress total yet, a small `Spinner className="size-3"` is shown. This is already functional.

The user's request about "a spinner on the folder that is being synced" refers to the **list page**, not the detail page.

## Code References

- `src/lib/components/workspace/Knowledge.svelte:245-327` - KB card rendering (list page)
- `src/lib/components/workspace/Knowledge.svelte:26` - Spinner import (already present)
- `src/lib/components/workspace/Knowledge.svelte:266-270` - Type badge rendering
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1469-1525` - Detail page sync indicators
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1306` - Socket.IO listener for sync events
- `src/lib/components/common/Spinner.svelte` - Spinner component (SVG, className prop)
- `src/lib/components/common/Badge.svelte` - Badge component (type: info/muted/success/warning/error)
- `backend/open_webui/services/onedrive/sync_events.py:77-129` - Socket.IO event emission
- `backend/open_webui/models/knowledge.py:85` - `meta` field on KnowledgeModel
- `src/lib/i18n/locales/en-US/translation.json` - English translations
- `src/lib/i18n/locales/nl-NL/translation.json` - Dutch translations

## Architecture Insights

- **No backend changes needed**: `meta.onedrive_sync.status` is already in the list response.
- **Socket.IO is the key for real-time**: The list page needs to subscribe to `onedrive:sync:progress` events to update card states without polling.
- **Cleanup on unmount**: Must remove Socket.IO listeners in `onDestroy`.
- **Cards are inline**: No separate card component to modify -- all changes go in `Knowledge.svelte`.
- **Stale sync detection**: Backend treats syncs older than 30 minutes as stale (can restart). The frontend should consider this -- if `status === 'syncing'` but `sync_started_at` is >30 min ago, it may be stale.

## Open Questions

1. Should the sync indicator show progress (e.g., "3/12") on the card, or just a spinner?
2. Should completed-with-errors state show a warning indicator on the card?
3. Should the card auto-refresh when sync completes (to update the "Updated" timestamp)?
