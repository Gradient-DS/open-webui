# KB Multiselect Delete — Iteration 1 (interaction polish + drag)

**Date:** 2026-06-30 · **Branch:** `feat/kb-multiselect-delete` · **Builds on:** the iter-0 design/plan in this folder.

Feedback from manual testing (with screenshots):
1. Bulk bar pops in on first select → **UI jumps**; after selecting it's awkward to keep selecting/deselecting.
2. Confirm dialog shows **"Delete 0 file(s) and 1 source(s)?"** on cloud KBs — counts selected *rows* by kind, not the files actually removed.
3. **Checkbox column not aligned** with the spinner/icon on non-selectable (uploading) rows.
4. Want: once selecting, **click anywhere on a row** toggles it (not only Cmd/Ctrl).
5. Want: **select-all/deselect-all as one (tri-state) checkbox** in a header row that's **always present** (so no jump).
6. Want: **drag to multiselect**.

User decisions: include drag now · show real file counts · select-all covers all currently-loaded selectable rows.

## Design

### Selection model (`selection.ts`)
- **Selection mode = `count > 0`** (drop the manual `_mode`/`enterSelectionMode`). The views' existing "in selection mode → select" branch then becomes click-to-toggle for free.
- **Unified ordered selectable list per view**, in visual order (sources-then-files). It is the basis for Shift-range, drag, and select-all — so source rows + file rows behave uniformly.
- **`available` registry:** active view calls `setAvailable(orderedItems)`. Model exposes `allSelected`, `indeterminate`, `toggleSelectAll()`.
- **Real file counts:** `sourceItem(id, name, fileCount=1)`; `SelectableItem` source variant gains `fileCount`. `breakdown` → `{ files, sources, totalFiles }` with `totalFiles = plainFiles + Σ source.fileCount`.
- **Drag-paint:** `pointerDown(item, ordered)` snapshots the current selection as a baseline + anchor; `pointerEnter(item)` sets selection to `baseline ∪ range(anchor..item)` and marks `didDrag`; `endDrag()` stops; `consumeDidDrag()` lets a row's click suppress the synthetic click after a drag. A press-without-move stays a normal click.

### `SelectCheckbox.svelte`
- New `selectable` prop. When false, render an **equal-size empty cell** (alignment fix). `visible` is driven by `selectionMode` (count>0) so every box shows once selecting.

### `KbSelectionHeader.svelte` (replaces `KbBulkActionBar`)
Always rendered when `write_access` and the list is selectable (hidden during lazy search / empty state), so the first select never shifts the list. Left: **tri-state select-all** checkbox → `toggleSelectAll()`. At `0 selected`: thin row, checkbox + muted "Select All". At `≥1`: checkbox + "{{n}} selected" + Delete + Deselect.

### Views (`Files`, `SourceGroupedFiles`, `LazyKnowledgeTree`, `LazyTreeNode`)
- Build the unified ordered list; `setAvailable` it; pass it as the `orderedItems` for range/drag.
- Reserve the checkbox column on all rows (`selectable`).
- File rows: click-to-toggle in selection mode (already implied by selectionMode=count>0), plus `on:pointerdown`/`on:pointerenter` for drag, and `select-none` to stop accidental text selection.
- Source/folder header rows: checkbox + modifier-click + drag select them, but **plain click still expands/collapses** (preserve folder navigation). They join `available`/range/drag via the unified list (lazy tree threads `orderedItems` into `LazyTreeNode`).

### `KnowledgeBase.svelte`
- Render the persistent `KbSelectionHeader` (replace the pop-in bar). Confirm dialog uses `breakdown.totalFiles`. Add `on:pointerup` → `selection.endDrag()`. Clear selection on `query` change so nothing strands when switching to search.

### Scope / non-goals
- Drag/range/select-all operate on each view's **loaded** selectable rows (lazy tree = root files + top-level sources; nested rows were never selectable).
- Touch entry points are the always-visible header select-all + drag (no long-press wiring).

## Tasks
- **T1** model rewrite + tests (node-testable): selectionMode=count>0, available/allSelected/indeterminate/toggleSelectAll, breakdown.totalFiles, drag, sourceItem.fileCount.
- **T2** `SelectCheckbox` selectable/spacer; new `KbSelectionHeader`; delete `KbBulkActionBar`.
- **T3** `Files.svelte`: unified list + setAvailable + reserve column + drag + select-none.
- **T4** `SourceGroupedFiles.svelte`: unified list (sources+loose) + same.
- **T5** `LazyKnowledgeTree.svelte` + `LazyTreeNode.svelte`: unified list + thread orderedItems + same.
- **T6** `KnowledgeBase.svelte`: persistent header, totalFiles confirm, pointerup end-drag, clear-on-query.
- **T7** build + targeted svelte-check + manual smoke.

Each task: `npm run build` (exit 0) + (T1) `npm run test:frontend`. No new svelte-check error classes in touched files.
