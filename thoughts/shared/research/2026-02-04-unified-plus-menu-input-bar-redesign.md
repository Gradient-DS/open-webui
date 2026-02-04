---
date: 2026-02-04T19:15:00+01:00
researcher: claude
git_commit: 327a2ca4a6ff0554d4c57a59aa51731db8006196
branch: feat/simple-kb
repository: open-webui
topic: "Unified + menu with three sections (context, database, capability) and pinning system"
tags: [research, codebase, input-bar, capabilities, pinning, ui-redesign]
status: complete
last_updated: 2026-02-04
last_updated_by: claude
last_updated_note: "Added OneDrive simplification: work-only, no sub-list"
---

# Research: Unified + Menu with Pinning System for Input Bar

**Date**: 2026-02-04T19:15:00+01:00
**Researcher**: claude
**Git Commit**: 327a2ca4a6ff0554d4c57a59aa51731db8006196
**Branch**: feat/simple-kb
**Repository**: open-webui

## Research Question

Redesign the input bar to consolidate the `+` (InputMenu) and integrations (IntegrationsMenu) into a single wider `+` dropdown with three vertically separated sections: Attach Context, Attach Database, Attach Capability. Each option gets a pin/unpin toggle. Pinned items appear at the bottom of the input bar (like web search does now). Capabilities can be enabled by clicking (highlighted when active) and pinned for quick access (non-highlighted icon). The "Attach Webpage" icon should use a link icon instead of globe to avoid confusion with Web Search.

## Summary

The change is **moderate complexity, frontend-only** (~6-8 files to modify, 0-1 new files). No backend changes needed. The main work involves:

1. Merging `InputMenu.svelte` and `IntegrationsMenu.svelte` into a single unified dropdown
2. Adding a pinning system backed by user settings (same pattern as `pinnedModels`)
3. Reorganizing the bottom bar of `MessageInput.svelte` to show pinned items
4. Changing the Attach Webpage icon from `GlobeAlt` to `Link`

## Detailed Findings

### Current Architecture (What Exists)

#### Two Separate Menus in the Input Bar

The input bar bottom row (`MessageInput.svelte:1486-1731`) currently shows:

1. **`+` button** (PlusAlt icon) → opens `InputMenu.svelte`
   - Upload Files, Capture, Attach Webpage (globe icon), Attach Notes, Attach Knowledge, Reference Chats, Google Drive, OneDrive (has sub-page for personal vs work/school)

2. **Integrations button** (Component icon) → opens `IntegrationsMenu.svelte` (only shows if any capability/tool is available)
   - Tools (with sub-page), Toggle Filters, Web Search, Image Generation, Code Interpreter

3. **Active capability pills** rendered inline in `MessageInput.svelte:1609-1731`
   - Selected tools count badge
   - Filter pills
   - Web Search pill (sky blue when active)
   - Image Generation pill
   - Code Interpreter pill

#### Key Files

| File | Role | Lines of Interest |
|------|------|-------------------|
| `src/lib/components/chat/MessageInput.svelte` | Main orchestrator, renders both menus + active pills | 1486-1731 (bottom bar), 430-493 (capability detection) |
| `src/lib/components/chat/MessageInput/InputMenu.svelte` | `+` dropdown (context/files) | 110-561 (full component) |
| `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte` | Capabilities dropdown | 96-407 (full component) |
| `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte` | Knowledge sub-panel | Used inside InputMenu |
| `src/lib/components/chat/MessageInput/InputMenu/Notes.svelte` | Notes sub-panel | Used inside InputMenu |
| `src/lib/components/chat/MessageInput/InputMenu/Chats.svelte` | Chats sub-panel | Used inside InputMenu |
| `src/lib/components/chat/Placeholder.svelte` | Also renders MessageInput | 203-226 (binds same props) |
| `src/lib/stores/index.ts` | Settings type with `pinnedModels` pattern | Line 156 |

#### Existing Pinning Pattern (`pinnedModels`)

There's already an established pattern for pinning in `ModelSelector.svelte:28-39`:
- Stored in `$settings.pinnedModels` (array of IDs)
- Persisted via `updateUserSettings(localStorage.token, { ui: $settings })`
- Uses `Pin.svelte` and `PinSlash.svelte` icons

#### Available Icons

- `Link.svelte` - exists, can replace GlobeAlt for "Attach Webpage"
- `Pin.svelte` / `PinSlash.svelte` - exist, for pin/unpin affordance
- `GlobeAlt.svelte` - currently used for both Web Search and Attach Webpage (source of confusion)
- `Database.svelte` - already used for Knowledge
- `Terminal.svelte` - Code Interpreter
- `Photo.svelte` - Image Generation
- `Wrench.svelte` - Tools

### Proposed Three-Section Dropdown Structure

```
┌─────────────────────────────────────┐
│  ATTACH CONTEXT                     │
│  ┌──────────────────────────┐  📌   │
│  │ 📎 Upload Files          │       │
│  │ 📷 Capture               │  📌   │
│  │ 🔗 Attach Webpage        │  📌   │  ← Link icon, not globe
│  │ 📝 Attach Notes       >  │  📌   │
│  │ 💬 Reference Chats    >  │  📌   │
│  │  G  Google Drive         │  📌   │
│  │  O  Microsoft OneDrive   │  📌   │  ← Direct action, no sub-page (work-only)
│  ├──────────────────────────┤───────│
│  ATTACH DATABASE                    │
│  │ 🗄️ Attach Knowledge   >  │  📌   │
│  ├──────────────────────────┤───────│
│  ATTACH CAPABILITY                  │
│  │ 🔧 Tools (N)          >  │  📌   │
│  │ ✨ Filters...            │  📌   │
│  │ 🌐 Web Search            │  📌   │
│  │ 🖼️ Image Generation      │  📌   │
│  │ 💻 Code Interpreter      │  📌   │
│  └──────────────────────────┘       │
└─────────────────────────────────────┘
```

### OneDrive Simplification

**Current behavior:** OneDrive has a chevron sub-page (`tab = 'microsoft_onedrive'`) that shows two options:
- "Microsoft OneDrive (personal)" — calls `uploadOneDriveHandler('personal')`
- "Microsoft OneDrive (work/school)" — calls `uploadOneDriveHandler('organizations')`

This is gated by `$config?.features?.enable_onedrive_integration` plus `enable_onedrive_personal` and `enable_onedrive_business` sub-flags (`InputMenu.svelte:347-557`).

**New behavior:** Since only work accounts are used:
- Remove the sub-page entirely (delete the `tab === 'microsoft_onedrive'` branch)
- Make OneDrive a single direct-click item that calls `uploadOneDriveHandler('organizations')` immediately
- Rename label from "Microsoft OneDrive" to "Microsoft OneDrive" (drop the "(work/school)" suffix since it's the only option)
- Only gate on `enable_onedrive_integration` and `enable_onedrive_business` (can ignore `enable_onedrive_personal`)

**Code to remove/simplify in `InputMenu.svelte`:**
- Lines 347-452: Replace the chevron button + sub-page trigger with a direct `DropdownMenu.Item` that calls `uploadOneDriveHandler('organizations')`
- Lines 512-557: Delete the entire `tab === 'microsoft_onedrive'` branch

### Implementation Plan

#### 1. Add `pinnedInputItems` to Settings type

**File:** `src/lib/stores/index.ts`

Add to `Settings` type:
```typescript
pinnedInputItems?: string[]; // e.g. ['web_search', 'attach_webpage', 'knowledge', 'code_interpreter']
```

#### 2. Merge InputMenu + IntegrationsMenu into a single UnifiedInputMenu

**Option A (recommended):** Extend `InputMenu.svelte` to include capabilities section
- Add all IntegrationsMenu props (`webSearchEnabled`, `imageGenerationEnabled`, etc.)
- Add three sections with `<hr>` separators
- Add pin/unpin button on each item row
- Move "Attach Webpage" to use `Link` icon instead of `GlobeAlt`
- Increase dropdown width from `max-w-70` to `max-w-80` or `max-w-84`

**Option B:** Create new `UnifiedInputMenu.svelte` that composes both
- More modular but duplicates some logic

#### 3. Update MessageInput.svelte bottom bar

**File:** `src/lib/components/chat/MessageInput.svelte`

Changes needed:
- Remove the separate IntegrationsMenu rendering (lines 1550-1588)
- Remove the divider between the two menus (line 1551-1553)
- Pass all capability props to the merged InputMenu
- Update the pinned items area (lines 1609-1731) to show:
  - **Pinned context items**: as clickable icons (e.g., link icon for webpage, database icon for knowledge)
  - **Pinned capabilities**: as non-highlighted icons when inactive, highlighted when active
  - **Active capabilities**: as highlighted pills (same as current behavior)

#### 4. Pin/Unpin Logic

```typescript
const pinItemHandler = async (itemId: string) => {
    let pinnedItems = $settings?.pinnedInputItems ?? [];
    if (pinnedItems.includes(itemId)) {
        pinnedItems = pinnedItems.filter((id) => id !== itemId);
    } else {
        pinnedItems = [...new Set([...pinnedItems, itemId])];
    }
    settings.set({ ...$settings, pinnedInputItems: pinnedItems });
    await updateUserSettings(localStorage.token, { ui: $settings });
};
```

Item IDs for pinning:
- Context: `upload_files`, `capture`, `attach_webpage`, `attach_notes`, `reference_chats`, `google_drive`, `onedrive`
- Database: `knowledge`
- Capabilities: `web_search`, `image_generation`, `code_interpreter`, `tools`, and individual tool/filter IDs

#### 5. Pinned Items in Bottom Bar Behavior

**For context/database items (pinned):**
- Show as small icon buttons in the bottom bar
- Clicking triggers the same action as the dropdown (e.g., opens file picker, opens webpage modal, opens knowledge sub-panel)

**For capabilities (pinned but not active):**
- Show as non-highlighted (gray) icon buttons
- Clicking activates the capability (toggles to highlighted/sky-blue)

**For capabilities (active, whether pinned or not):**
- Show as highlighted sky-blue pill (current behavior)
- Clicking deactivates

### Files to Modify

1. **`src/lib/stores/index.ts`** - Add `pinnedInputItems` to Settings type (~2 lines)
2. **`src/lib/components/chat/MessageInput/InputMenu.svelte`** - Major restructure: add capability section, pin buttons, section dividers, wider dropdown, Link icon for webpage (~150-200 lines changed)
3. **`src/lib/components/chat/MessageInput.svelte`** - Remove IntegrationsMenu, pass capability props to InputMenu, update pinned items bar rendering (~80-100 lines changed)
4. **`src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`** - Can be deleted or kept as import (capability items move to InputMenu)
5. **`src/lib/components/chat/Placeholder.svelte`** - Update if IntegrationsMenu props change (minimal, since MessageInput handles it internally)

### Risks and Considerations

1. **Dropdown height**: Combining everything into one dropdown may make it tall. The current `max-h-72` (288px) may need adjustment, or the dropdown should scroll well.
2. **Mobile**: On mobile (`$mobile`), the dropdown behavior may need special handling for the increased number of items.
3. **Feature gating**: Each section item is conditionally shown based on config flags, user permissions, and model capabilities. This logic currently lives in both InputMenu and IntegrationsMenu and needs careful merging.
4. **Tools sub-page**: The tools list can be long. The existing chevron-based sub-page navigation pattern should be preserved.
5. **Session persistence**: Capability toggle states already persist to sessionStorage via the draft system. Pin state persists to user settings (backend). These are independent and should remain so.
6. **No backend changes**: Pinning is purely a UI preference stored in user settings, same as `pinnedModels`.

## Code References

- `src/lib/components/chat/MessageInput.svelte:1486-1731` - Bottom bar with menus and active capability pills
- `src/lib/components/chat/MessageInput.svelte:430-493` - Capability model detection and show* booleans
- `src/lib/components/chat/MessageInput/InputMenu.svelte:110-561` - Current `+` dropdown
- `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte:96-407` - Current integrations dropdown
- `src/lib/components/chat/ModelSelector.svelte:28-39` - Existing pin/unpin pattern
- `src/lib/stores/index.ts:156` - Settings type (pinnedModels)
- `src/lib/components/icons/Link.svelte` - Link icon (for webpage attachment)
- `src/lib/components/icons/Pin.svelte` - Pin icon
- `src/lib/components/icons/PinSlash.svelte` - Unpin icon

## Architecture Insights

- The two-menu architecture (InputMenu + IntegrationsMenu) was originally designed to separate "content attachment" from "capability toggling." Merging them into a single menu is straightforward because both use the same `Dropdown`/`DropdownMenu` component pattern from `bits-ui`.
- The pinning system can reuse the exact same persistence pattern as `pinnedModels` - store an array of string IDs in `$settings`, persist via `updateUserSettings()`.
- Capabilities use a two-state model: **pinned** (persistent preference) vs **active** (per-session state). Pinned = quick access, Active = actually enabled for the current chat. This is a new distinction that doesn't exist in the current codebase.
- The bottom bar of the input already renders conditional pills for active capabilities. Extending it to also show pinned-but-inactive items as gray icons is a natural extension of the same pattern.

## Follow-up Research 2026-02-04T19:30+01:00

### OneDrive: Work-Only, No Sub-Page

**Decision:** OneDrive should be simplified to work-only with no sub-list.

**Current code** (`InputMenu.svelte:347-557`):
- Lines 347-452: A `<button>` with chevron that sets `tab = 'microsoft_onedrive'`
- Lines 512-557: The `microsoft_onedrive` tab branch renders two `DropdownMenu.Item`s: personal and work/school

**Changes needed:**
- Replace the chevron button (lines 347-452) with a flat `DropdownMenu.Item` that directly calls `uploadOneDriveHandler('organizations')`
- Delete the `tab === 'microsoft_onedrive'` branch entirely (lines 512-557)
- Label becomes just "Microsoft OneDrive" (no "(work/school)" suffix)
- Gate on `$config?.features?.enable_onedrive_integration && $config?.features?.enable_onedrive_business` only
- The `uploadOneDriveHandler` prop in `MessageInput.svelte` already accepts an `authorityType` parameter, so passing `'organizations'` directly works without any changes upstream

This also slightly simplifies the dropdown by removing one sub-page navigation, making the menu feel more direct.

## Open Questions

1. Should pinned context items (like "Upload Files") show as icons only, or icons with labels in the bottom bar?
2. Should there be a maximum number of pinned items to prevent the bottom bar from becoming cluttered?
3. Should the pin state be per-user-global or per-chat? (Recommendation: per-user-global, same as pinnedModels)
4. When a capability is both pinned AND active, should it show one icon (highlighted) or two (pinned gray + active highlighted)?
   - Recommendation: One icon - when active it's highlighted, when inactive but pinned it's gray. No duplication.
