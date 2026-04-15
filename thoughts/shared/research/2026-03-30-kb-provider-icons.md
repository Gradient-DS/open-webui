---
date: 2026-03-30T09:40:00+02:00
researcher: Claude
git_commit: b0cfa6d9318ecc7bb7f733bceb07cbf4587d787a
branch: merge/upstream-260329
repository: open-webui
topic: 'KB provider-specific icons (OneDrive/Google Drive) in detail pane and selector dropdown'
tags: [research, codebase, knowledge-base, icons, onedrive, google-drive, ui]
status: complete
last_updated: 2026-03-30
last_updated_by: Claude
---

# Research: KB Provider Icons in Detail Pane and Selector Dropdown

**Date**: 2026-03-30T09:40:00+02:00
**Researcher**: Claude
**Git Commit**: b0cfa6d9318ecc7bb7f733bceb07cbf4587d787a
**Branch**: merge/upstream-260329
**Repository**: open-webui

## Research Question

Replace the generic KB icon with OneDrive/Google Drive logos in (1) the KB detail pane header and (2) the KB selector dropdown in chat input.

## Summary

The KB detail pane has no icon — only a text Badge for the type. The selector dropdowns use a generic `Database` icon for all KBs regardless of type. Both `OneDrive.svelte` and `GoogleDrive.svelte` icon components already exist and are imported in the detail pane component. The KB `type` field is available from the API but not currently used in the selector dropdowns.

## Detailed Findings

### 1. KB Detail Pane Header (`KnowledgeBase.svelte`)

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

- Header area: **lines 1618–1655**
- Current layout: back button (ChevronLeft) → name input → Badge (type text) → action buttons
- **No icon/logo** is rendered for the KB identity
- `OneDrive` and `GoogleDrive` icon components already imported at **lines 69–70**
- `activeProvider` derived state at **line 142** maps `knowledge.type` → CLOUD_PROVIDERS config
- CLOUD_PROVIDERS defined at **lines 97–123**: maps `'onedrive'` and `'google_drive'` to config objects with label, type, metaKey, configKey, etc.
- The icons are used elsewhere in the component (sync button area, lines 1714–1717) — demonstrates the pattern

**Change needed**: Add a provider icon before or alongside the KB name/Badge in the header section.

### 2. KB Selector Dropdown — InputMenu Variant

**File**: `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte`

- **Line 195**: Renders `<Database />` icon for every KB collection
- **Line 257**: Renders `<DocumentPage />` for individual files within expanded KB
- Fetches KB data via `getKnowledgeBases()` at **line 126–127**
- The `type` field is present in the response objects but **not used** in this component
- All KBs look identical regardless of type

**Change needed**: Conditionally render OneDrive/GoogleDrive icon instead of Database based on `item.type`.

### 3. KB Selector Dropdown — Commands Variant

**File**: `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte`

- **Line 187**: Same `Database` icon for all KB collections
- **Line 191**: `DocumentPage` for individual files
- Same pattern as InputMenu variant — `type` field not used

**Change needed**: Same conditional icon logic as InputMenu variant.

### 4. Existing Icon Components

- `src/lib/components/icons/OneDrive.svelte` — SVG icon, accepts `className` prop
- `src/lib/components/icons/GoogleDrive.svelte` — SVG icon, accepts `className` prop
- `src/lib/components/icons/Database.svelte` — Current generic icon used in selectors

### 5. KB List Page (for reference)

**File**: `src/lib/components/workspace/Knowledge.svelte`

- Lines 390–411: Uses text Badges for type (ONEDRIVE, GOOGLE DRIVE, LOKAAL) — no icons in badges
- Lines 29–30: Already imports OneDrive/GoogleDrive icons (used in creation dropdown)

## Code References

- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:69-70` — OneDrive/GoogleDrive imports
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:97-123` — CLOUD_PROVIDERS map
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:142` — activeProvider derived state
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1618-1655` — Header area to modify
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:195` — Database icon to replace
- `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte:187` — Database icon to replace

## Architecture Insights

- Icon rendering is done via conditional `{#if}` blocks checking type strings — no centralized icon registry
- The `CLOUD_PROVIDERS` map in `KnowledgeBase.svelte` could be extended with an `icon` property, but for only 2 providers the inline conditional approach is simpler and consistent with existing patterns
- Integration providers (custom type from `$config.integration_providers`) don't have icons — would need a different approach if icons are desired for those too
- The `type` field is returned by the KB API but the TypeScript type definition in `KnowledgeBase.svelte` (lines 158–168) doesn't include it — works at runtime but should be added for completeness

## Open Questions

- Should integration providers (custom external pipeline types) also get custom icons?
- Should the KB list page (`Knowledge.svelte`) also show icons alongside or instead of the text badges?
- Desired icon placement in the detail pane header: before the name, after the badge, or replacing the badge?
