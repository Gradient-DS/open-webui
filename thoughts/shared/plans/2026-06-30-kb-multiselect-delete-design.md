# Design — Knowledge Base multiselect delete (all KB types)

**Date:** 2026-06-30
**Author:** @lexlubbers (with Claude)
**Branch:** `feat/kb-multiselect-delete` (off `dev`)
**Status:** Approved design — ready for implementation plan

## Goal

In a Knowledge Base, let users select multiple rows and remove them in one action, instead of removing them one-by-one via the per-row `✕` (each of which is currently a separate, unconfirmed delete). This is concrete customer feedback (Dutch): *"Bij het beheer van kennisbanken kan het soms nodig zijn om in één keer een aantal bestanden te verwijderen. Dat kan nu echter enkel één voor één."*

Covers **all KB types** — local (flat list) and cloud (grouped + lazy tree).

Selection model: **hybrid** — visible checkboxes + select-all (discoverable, touch-safe) **plus** Cmd/Ctrl-toggle and Shift-range (fast for power users), a bulk action bar, and one confirm dialog. This mirrors the codebase's existing, proven multi-select implementation (`FileNav` / `FileEntryRow` / `BulkActionBar`).

**Core principle (what makes cloud tractable):** multiselect *batches the removal action a row already has*. A plain file batches its file-remove (`removeFileFromKnowledgeById`); a synced cloud source row batches its remove-source (`onRemoveSource`). (A row's kind is decided by whether it carries a `source_item_id`, not by where it sits in the tree.) No row gains a delete power it didn't have today — we just let you trigger several at once. The selection model stores, per selected item, the removal callback its own `✕` would invoke; the bulk handler simply replays them.

## Background / current state

- The KB file list is owned by `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`, which delegates row rendering to one of three children by KB type + a localStorage flag (**all three are in scope**):
  - `KnowledgeBase/Files.svelte` — flat list for local KBs (uses `VirtualList`).
  - `KnowledgeBase/SourceGroupedFiles.svelte` — cloud KBs, load-all grouped tree (loose files + files grouped under synced sources).
  - `KnowledgeBase/LazyKnowledgeTree.svelte` (+ recursive `LazyTreeNode.svelte`) — cloud KBs, lazy per-folder tree (folder contents fetched on demand).
- Each row exposes callbacks wired in the parent: row-body click → `onClick(fileId)` (opens an inline edit `Drawer` with a plain `<textarea>` + Save), and a per-row `✕`. The `✕` resolves to one of **two removal actions** depending on the row:
  - `onDelete(fileId)` → `deleteFileHandler` → `removeFileFromKnowledgeById(token, kbId, fileId)` (POST `/knowledge/{id}/file/remove`, removes the KB↔file association). Used for plain files (all local rows; cloud rows with no `source_item_id`).
  - `onRemoveSource(...)` → removes a whole **synced cloud source** (and stops syncing it). Used in cloud views for rows that carry a `source_item_id` (onedrive / google_drive / confluence). Files *nested inside* a synced folder have **no `✕` at all** today (they are managed by the source).
- Both removal paths have **no confirmation** today, and there is **no bulk endpoint** for either.
- **Reference pattern to port from:** the Terminal/code-interpreter file browser — `src/lib/components/chat/FileNav.svelte` (selection state: `selectedEntries: Set`, `lastClickedIndex`, `selectionMode`; `handleSelect` for shift-range + ctrl/meta-toggle; `bulkDelete` loop; Esc/click-outside clear), `FileNav/FileEntryRow.svelte` (hover/selected checkbox, modifier-click handling, touch long-press), and `FileNav/BulkActionBar.svelte` (count + actions bar, with a `__bulk__` sentinel through a shared `ConfirmDialog`).

## Scope

**In scope:** multiselect + bulk removal across **all three list views** (`Files.svelte`, `SourceGroupedFiles.svelte`, `LazyKnowledgeTree.svelte`/`LazyTreeNode.svelte`); checkbox + select-all + Cmd/Ctrl-toggle + Shift-range + touch long-press; a shared selection model + shared KB bulk action bar; one confirm dialog (with a mixed-selection breakdown); batching of **both** removal actions (file-remove and remove-source); en-US + nl-NL i18n.

**Selectable rows = rows that already have a `✕` today:** plain files (→ batch file-remove) and synced source roots (→ batch remove-source). **Not selectable:** files nested inside a synced folder (no `✕` today; the sync worker would re-add them anyway). Restricting selection to rows that already carry a removal action also means **only already-loaded rows are selectable**, which sidesteps the lazy tree's on-demand-loading problem (you can't select what isn't loaded, and the un-loadable nested rows are exactly the ones we exclude).

**Out of scope (v1):**
- A backend bulk-delete endpoint (v1 loops the existing per-row remove APIs client-side; a bulk endpoint is a later optimization if users remove hundreds at once).
- Per-file delete *inside* a synced folder (no affordance today + sync re-adds — meaningless to batch).
- The side-by-side "original | extracted content" viewer-on-click idea — tracked as its own separate spec.
- Changing the existing single-row `✕` behavior (stays immediate, unconfirmed).

## Design

### State & logic placement (additive, isolated, shared across views)

To avoid re-implementing selection in three list components, the selection logic is extracted into **one reusable model**, the bulk bar is **one shared component**, and the destructive dispatch lives **once** in the parent. Each list view just renders checkboxes on its selectable rows and calls into the model. Purely additive — no existing prop/handler is changed; existing `onClick` / `onDelete` / `onRemoveSource` stay as-is.

- **New `KnowledgeBase/selection` model** (reusable, unit-testable; e.g. `selection.svelte.ts` rune store or a small factory):
  - State: `selected: Map<id, SelectableItem>` where `SelectableItem = { id, label, kind: 'file' | 'source', payload }` and `payload` is exactly what that row's existing removal call needs. `lastClickedIndex`, `selectionMode` (touch).
  - Methods ported/adapted from `FileNav`: `toggle(item, index)`, `selectRange(items, index)`, `selectAll(items)`, `clear()`. Range/toggle operate on the **data array** (not DOM), so they work across `VirtualList` and the tree.
  - Storing the per-item `kind` + `payload` is what lets a single bulk handler replay each row's own removal action (file-remove vs remove-source) — the model never hard-codes which.
- **New shared `KnowledgeBase/KbBulkActionBar.svelte`** — the bulk bar UI (count + breakdown + actions), rendered once by the parent. KB-local; no dependency on the chat `FileNav` tree; no Download action.
- **Each list view** (`Files`, `SourceGroupedFiles`, `LazyKnowledgeTree`/`LazyTreeNode`): receives the selection model, renders a hover-reveal/persistent checkbox + selected-row tint on **selectable rows only** (plain files and synced source roots; nested files render no checkbox), and routes checkbox/modifier clicks to the model. For the recursive `LazyTreeNode`, the model is threaded down alongside the existing `expanded` / `nodeCache` shared state; only source-root nodes (not nested files) render a checkbox.
- **`KnowledgeBase.svelte`** owns the model instance + the single dispatch handler `bulkRemoveHandler()` (reuses the already-imported `ConfirmDialog`):
  - On confirm, iterate the selected items: `kind === 'file'` → `removeFileFromKnowledgeById(token, kbId, payload.fileId)`; `kind === 'source'` → the existing remove-source call (same one `onRemoveSource` uses). Sequential, best-effort (failures counted, not fatal). **No per-item `init()`** — one `init()` at the end.
  - Toast a breakdown, e.g. `"{ok} van {total} verwijderd"` (and, when the selection mixes kinds, the confirm dialog spells out "{f} bestanden, {s} bronnen").

### Selection interactions (Decision A — resolved)

- **Checkbox** on selectable rows: hidden by default, fades in on row hover; stays visible once the selection is non-empty. Header "Alles selecteren" toggles all currently-loaded selectable rows.
- **Plain click on row body → unchanged behavior** (file row opens the edit drawer; folder row expands/collapses). Selection on desktop is only via the checkbox or a modifier-click — we deliberately do **not** adopt FileNav's "once something is selected, plain click toggles," because that surprises desktop users who checked a box and then want to open/expand a different row.
- **Cmd/Ctrl + click** on a row → toggle that one row's selection (+ set `lastClickedIndex`).
- **Shift + click** → select the contiguous range from `lastClickedIndex` to the clicked row.
- **Touch:** long-press enters `selectionMode`; subsequent taps select (ported from `FileNav`). This is the only path where a plain tap selects rather than opens.
- **Esc** clears the selection.

### Bulk action bar (Decision B — resolved)

One shared **`KnowledgeBase/KbBulkActionBar.svelte`**, rendered once by the parent, styled like `BulkActionBar` but **without** the Download action and **without** depending on the chat `FileNav` component tree. Appears (sticky) only when the selection is non-empty:

```
{n} geselecteerd   ·   [ Alles ]   [ 🗑 Verwijderen ]   [ Deselecteren ]
```

- `[Alles]` → select-all toggle (over currently-loaded selectable rows), `[🗑 Verwijderen]` → opens confirm → `bulkRemoveHandler()`, `[Deselecteren]` → clear.
- Rationale for KB-local over reusing `FileNav/BulkActionBar`: keeps the KB panel decoupled from a chat-feature component, and we don't want the Download action here.

### Confirmation & removal mechanics (Decision C — resolved)

- **Bulk removal is confirmed**; the existing **single-row `✕` stays immediate/unconfirmed** (no added friction to the one-off path). Reuses the `ConfirmDialog` already imported in `KnowledgeBase.svelte`.
- Confirm copy adapts to the selection: a pure-file selection reads *"{n} bestanden verwijderen?"*; a selection that includes synced sources spells out the breakdown (*"{f} bestanden en {s} bronnen verwijderen?"*) and notes that removing a source stops its sync — because remove-source is more destructive than removing one file.
- On confirm, `bulkRemoveHandler` replays each selected item's **own** removal: `file` → `removeFileFromKnowledgeById`, `source` → the existing remove-source call. Sequential, best-effort (failures counted, not fatal, like `FileNav.bulkDelete`). One `init()` refresh at the end. No new backend endpoint in v1.

### i18n

All new user-facing strings added to **both** `src/lib/i18n/locales/en-US/translation.json` and `nl-NL/translation.json`, alphabetically sorted, per project rule. Strings: select-all label, "{{count}} geselecteerd", "Deselecteren", the file-only and mixed confirm dialog title/body (incl. the source-stops-sync note), and the "{{ok}} van {{total}} verwijderd" result toast.

## Open items for the implementation plan

- Confirm the exact `VirtualList` (in `Files.svelte`) and tree-node slot wiring needed to render the per-row checkbox + selected-row tint without disturbing existing row layout, the per-row `✕`, and the "Open file" button.
- Pin down precisely which rows in `SourceGroupedFiles` / `LazyTreeNode` are "source roots" (carry `source_item_id` → remove-source) vs plain files (→ file-remove), and the exact `onRemoveSource` payload to capture into the selection item.
- Confirm threading the selection model into the recursive `LazyTreeNode` (alongside `expanded`/`nodeCache`) without breaking its existing recursion/lazy-load.
- Exclude mid-upload `tempId` rows (and any row without a removal action) from selection.
- Decide whether select-all in the lazy tree selects only loaded rows (expected) and whether to surface that to the user.
