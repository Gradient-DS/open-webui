# Unified + Menu with Pinning System — Implementation Plan

## Overview

Merge the `+` (InputMenu) and integrations (IntegrationsMenu) dropdowns in the chat input bar into a single wider dropdown with three sections: **Attach Context**, **Attach Database**, and **Attach Capability**. Each item gets a pin/unpin toggle. Pinned items appear as icon buttons in the bottom bar. Capabilities show as gray icons when pinned-but-inactive, sky-blue when active. OneDrive is simplified to work-only (no sub-page). Webpage icon changes from `GlobeAlt` to `Link`.

## Current State Analysis

Two separate dropdowns in `MessageInput.svelte`:

1. **InputMenu** (`src/lib/components/chat/MessageInput/InputMenu.svelte`): 8 context items with tab-based sub-navigation for Knowledge, Notes, Chats, and OneDrive sub-pages. Uses `bits-ui` DropdownMenu via custom `Dropdown` wrapper. Width: `max-w-70`, height: `max-h-72`.

2. **IntegrationsMenu** (`src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`): Capability toggles (Web Search, Image Gen, Code Interpreter), filter toggles, and tools with a sub-page. Same dropdown pattern. All toggle states use two-way `bind:` from Chat.svelte.

3. **Bottom bar** (`MessageInput.svelte:1486-1731`): Renders both menus with a `1px` divider between them, followed by active capability pills (sky-blue with XMark on hover).

### Key Discoveries:
- `pinnedModels` pattern at `ModelSelector.svelte:28-39` is the template for pinning
- Settings persist via `updateUserSettings(token, { ui: $settings })` → backend JSON column
- Both menus use identical `Dropdown`/`DropdownMenu` component patterns
- Capability states flow: `Chat.svelte` → `bind:` → `MessageInput.svelte` → `bind:` → `IntegrationsMenu.svelte`
- IntegrationsMenu lazy-loads tools on first open via `init()` function
- Tools sub-page has OAuth authentication handling that must be preserved
- `isFeatureEnabled()` from `src/lib/utils/features.ts` gates features via config flags

## Desired End State

A single `+` button opens a wider dropdown with three visually separated sections. Each item has a pin icon on its right side. Pinned items appear in the bottom bar as compact icon buttons. The IntegrationsMenu component is deleted. The bottom bar no longer has a divider or separate integrations button.

### Verification:
- The `+` dropdown shows all context + database + capability items in three sections
- Pin/unpin persists across page reloads (stored in user settings)
- Pinned context items appear as clickable icon buttons in the bottom bar
- Pinned capabilities appear as gray icon buttons; clicking activates them (turns sky-blue)
- Active capabilities show as sky-blue icons (same visual as current pills)
- All feature gating and permission checks are preserved
- Tools sub-page navigation and OAuth flow still work
- OneDrive is a single-click item (work-only, no sub-page)
- Webpage uses Link icon, not GlobeAlt

## What We're NOT Doing

- No backend changes (pinning is frontend-only, persisted via existing user settings)
- No maximum pinned items limit
- No drag-to-reorder for pinned items
- No per-chat pin state (global user preference only)
- No changes to Knowledge/Notes/Chats sub-page components
- No changes to capability state management in Chat.svelte
- No changes to the AttachWebpageModal component

## Implementation Approach

Extend `InputMenu.svelte` to absorb IntegrationsMenu's functionality rather than creating a new component. This avoids duplicating dropdown boilerplate and preserves the existing sub-page navigation pattern. The IntegrationsMenu is then deleted.

---

## Phase 1: Add `pinnedInputItems` to Settings

### Overview
Add the new settings field and nothing else. This is the foundation for all other changes.

### Changes Required:

#### 1. Settings Type
**File**: `src/lib/stores/index.ts`
**Line 157** (after `pinnedModels`):

Add:
```typescript
pinnedInputItems?: string[];
```

This follows the same pattern as `pinnedModels` (line 156). The array stores string IDs like `'web_search'`, `'upload_files'`, `'knowledge'`, etc.

### Success Criteria:

#### Automated Verification:
- [x] TypeScript type checking passes: `npm run check`
- [x] No linting errors: `npm run lint:frontend`

#### Manual Verification:
- [x] N/A (no user-visible changes)

---

## Phase 2: Extend InputMenu with Capability Section and Pin Buttons

### Overview
This is the main phase. Extend `InputMenu.svelte` to include all IntegrationsMenu functionality, add section headers, add pin/unpin buttons on every item, simplify OneDrive, and change webpage icon.

### Changes Required:

#### 1. Add New Props to InputMenu
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Add these new exported props (matching what IntegrationsMenu currently receives):

```javascript
// Capability toggle states (two-way binding from parent)
export let selectedToolIds = [];
export let selectedFilterIds = [];
export let webSearchEnabled = false;
export let imageGenerationEnabled = false;
export let codeInterpreterEnabled = false;

// Visibility flags (one-way)
export let showToolsButton = false;
export let showWebSearchButton = false;
export let showImageGenerationButton = false;
export let showCodeInterpreterButton = false;
export let toggleFilters = [];

// Valve handling
export let onShowValves = (e) => {};
export let closeOnOutsideClick = true;
```

#### 2. Add New Imports
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Add imports for icons and utilities used by capabilities:

```javascript
import { updateUserSettings } from '$lib/apis/users';
import { getTools } from '$lib/apis/tools';
import { getOAuthClientAuthorizationUrl } from '$lib/apis/configs';

import Link from '$lib/components/icons/Link.svelte';
import Pin from '$lib/components/icons/Pin.svelte';
import PinSlash from '$lib/components/icons/PinSlash.svelte';
import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
import Photo from '$lib/components/icons/Photo.svelte';
import Terminal from '$lib/components/icons/Terminal.svelte';
import Wrench from '$lib/components/icons/Wrench.svelte';
import Sparkles from '$lib/components/icons/Sparkles.svelte';
import Knobs from '$lib/components/icons/Knobs.svelte';
import Spinner from '$lib/components/common/Spinner.svelte';
import Switch from '$lib/components/common/Switch.svelte';

import { settings, tools as _tools, toolServers } from '$lib/stores';
```

Remove the currently-unused icon imports (`DocumentArrowUp`, `Note`, `ChatBubbleOval`, `Refresh`, `Agile`).

#### 3. Add Pin Handler and Tools Init Logic
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Add the pin handler (following the `pinnedModels` pattern from `ModelSelector.svelte:28-39`):

```javascript
const pinItemHandler = async (itemId) => {
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

Add tools initialization logic (ported from `IntegrationsMenu.svelte:53-93`):

```javascript
let tools = null;

$: if (show) {
    initTools();
}

const initTools = async () => {
    if ($_tools === null) {
        const res = await getTools(localStorage.token);
        _tools.set(res);
    }

    tools = ($_tools ?? []).reduce((acc, tool) => {
        acc[tool.id] = {
            name: tool.name,
            description: tool.meta?.description ?? '',
            enabled: selectedToolIds.includes(tool.id),
            ...tool
        };
        return acc;
    }, {});

    // Add direct tool servers
    ($toolServers ?? []).forEach((server, idx) => {
        if (server?.info) {
            const id = `direct_server:${idx}`;
            tools[id] = {
                name: server.info.title ?? server.url,
                description: server.info.description ?? '',
                enabled: selectedToolIds.includes(id),
                ...server
            };
        }
    });

    // Prune stale selections
    selectedToolIds = selectedToolIds.filter((id) => id in tools);
};
```

#### 4. Restructure the Dropdown Content with Three Sections
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

**Widen the dropdown**: Change `max-w-70` to `max-w-84` on the `DropdownMenu.Content` element (line 124). Increase `max-h-72` to `max-h-96` to accommodate more items.

**Root tab structure** (`tab === ''`):

```
Section 1: ATTACH CONTEXT header
  - Upload Files (📎 Clip icon)                    [pin]
  - Capture (📷 Camera icon)                       [pin]
  - Attach Webpage (🔗 Link icon — changed!)       [pin]
  - Attach Notes (📝 PageEdit icon, chevron)        [pin]
  - Reference Chats (🕐 ClockRotateRight, chevron)  [pin]
  - Google Drive (G logo)                           [pin]
  - Microsoft OneDrive (O logo — simplified!)       [pin]

<hr divider>

Section 2: ATTACH DATABASE header
  - Attach Knowledge (🗄️ Database icon, chevron)    [pin]

<hr divider>

Section 3: ATTACH CAPABILITY header
  - Tools (🔧 Wrench icon, count, chevron)          [pin]  [Switch]
  - {#each toggleFilters} (✨ Sparkles/custom icon)  [pin]  [Switch]
  - Web Search (🌐 GlobeAlt icon)                   [pin]  [Switch]
  - Image Generation (🖼️ Photo icon)                 [pin]  [Switch]
  - Code Interpreter (💻 Terminal icon)              [pin]  [Switch]
```

**Section header styling** (small uppercase text):
```html
<div class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide px-3 py-1.5">
    {$i18n.t('Attach Context')}
</div>
```

**Divider between sections**:
```html
<div class="my-1 border-t border-gray-100 dark:border-gray-800" />
```

**Pin button on each item** (added as the rightmost element in each row):
```svelte
<button
    class="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
    on:click|stopPropagation={() => pinItemHandler('item_id')}
    aria-label={($settings?.pinnedInputItems ?? []).includes('item_id') ? 'Unpin' : 'Pin'}
>
    {#if ($settings?.pinnedInputItems ?? []).includes('item_id')}
        <PinSlash className="size-3.5" />
    {:else}
        <Pin className="size-3.5" />
    {/if}
</button>
```

Use `on:click|stopPropagation` on pin buttons to prevent triggering the item's main action.

#### 5. Capability Items with Switch Toggles
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Capability items follow the IntegrationsMenu pattern — a clickable row with icon, label, and Switch toggle. The pin button sits between the label and the Switch:

```svelte
{#if showWebSearchButton}
    <button class="..." on:click={() => { webSearchEnabled = !webSearchEnabled; }}>
        <GlobeAlt className="size-4" />
        <span class="truncate">{$i18n.t('Web Search')}</span>
        <button on:click|stopPropagation={() => pinItemHandler('web_search')}>
            {#if ($settings?.pinnedInputItems ?? []).includes('web_search')}
                <PinSlash className="size-3.5" />
            {:else}
                <Pin className="size-3.5" />
            {/if}
        </button>
        <Switch state={webSearchEnabled} />
    </button>
{/if}
```

Same pattern for Image Generation (`'image_generation'`), Code Interpreter (`'code_interpreter'`), Tools navigation (`'tools'`), and each filter.

#### 6. OneDrive Simplification
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

**Remove**: The `tab === 'microsoft_onedrive'` sub-page branch (currently lines 512-557).

**Replace**: The OneDrive chevron button (currently lines 347-452) with a flat `DropdownMenu.Item`:

```svelte
{#if fileUploadEnabled && $config?.features?.enable_onedrive_integration && $config?.features?.enable_onedrive_business}
    <DropdownMenu.Item class="..." on:click={() => { uploadOneDriveHandler('organizations'); show = false; }}>
        <!-- OneDrive SVG icon (keep existing inline SVG) -->
        <span>{$i18n.t('Microsoft OneDrive')}</span>
        <button on:click|stopPropagation={() => pinItemHandler('onedrive')}>...</button>
    </DropdownMenu.Item>
{/if}
```

No more `enable_onedrive_personal` check needed. Label is just "Microsoft OneDrive".

#### 7. Webpage Icon Change
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Replace `<GlobeAlt />` with `<Link />` in the "Attach Webpage" menu item (currently line 208).

#### 8. Tools Sub-Page (Ported from IntegrationsMenu)
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Add a `tab === 'tools'` branch (same as `IntegrationsMenu.svelte:316-403`). This includes:
- Back button with "Tools" label and count badge
- Tool list with Switch toggles
- OAuth authentication check and redirect
- Valves buttons calling `onShowValves({ type: 'tool', id: toolId })`

#### 9. Pass `closeOnOutsideClick` to Dropdown
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

The `Dropdown` component needs to forward `closeOnOutsideClick` for the ValvesModal coordination pattern. Check if the `Dropdown` component supports this prop; if not, add it.

### Item ID Reference

| Item | Pin ID | Section |
|------|--------|---------|
| Upload Files | `upload_files` | Context |
| Capture | `capture` | Context |
| Attach Webpage | `attach_webpage` | Context |
| Attach Notes | `attach_notes` | Context |
| Reference Chats | `reference_chats` | Context |
| Google Drive | `google_drive` | Context |
| Microsoft OneDrive | `onedrive` | Context |
| Attach Knowledge | `knowledge` | Database |
| Tools (group) | `tools` | Capability |
| Web Search | `web_search` | Capability |
| Image Generation | `image_generation` | Capability |
| Code Interpreter | `code_interpreter` | Capability |
| Individual filters | `filter:{filter.id}` | Capability |

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript type checking passes: `npm run check`
- [ ] No linting errors: `npm run lint:frontend`
- [ ] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] The `+` dropdown opens with three labeled sections
- [ ] All context items appear and work (upload, capture, webpage, notes, knowledge, chats, drive, onedrive)
- [ ] OneDrive is a single click (no sub-page), opens work account picker directly
- [ ] Webpage icon is a link icon, not a globe
- [ ] Capability toggles (web search, image gen, code interpreter) work with Switch toggles
- [ ] Tools sub-page works with individual tool toggles and OAuth redirect
- [ ] Filter toggles appear and work
- [ ] Pin/unpin buttons toggle correctly on all items
- [ ] Pin state persists across page reload
- [ ] Dropdown scrolls properly with all items visible

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that the dropdown works correctly before proceeding to Phase 3.

---

## Phase 3: Update MessageInput.svelte Bottom Bar

### Overview
Remove IntegrationsMenu references, pass capability props to the unified InputMenu, and render pinned items as icon buttons in the bottom bar.

### Changes Required:

#### 1. Remove IntegrationsMenu Import and Rendering
**File**: `src/lib/components/chat/MessageInput.svelte`

- Remove import at line 84: `import IntegrationsMenu from './MessageInput/IntegrationsMenu.svelte';`
- Remove import at line 85: `import Component from '../icons/Component.svelte';`
- Remove the divider (lines 1550-1553)
- Remove the entire IntegrationsMenu block (lines 1555-1588)
- Remove the `{#if}` wrapping the divider/IntegrationsMenu (line 1550 and closing at 1588)

#### 2. Add New Imports
**File**: `src/lib/components/chat/MessageInput.svelte`

Add imports for icons used by pinned items:

```javascript
import Pin from '../icons/Pin.svelte';
import Link from '../icons/Link.svelte';
import Camera from '../icons/Camera.svelte';
import Clip from '../icons/Clip.svelte';
import Database from '../icons/Database.svelte';
import ClockRotateRight from '../icons/ClockRotateRight.svelte';
import PageEdit from '../icons/PageEdit.svelte';
```

Note: `GlobeAlt`, `Photo`, `Wrench`, `Sparkles`, `Terminal`, `XMark` are already imported.

#### 3. Pass Capability Props to InputMenu
**File**: `src/lib/components/chat/MessageInput.svelte`

Add these props to the `<InputMenu>` component (around lines 1488-1541):

```svelte
<InputMenu
    bind:files
    selectedModels={atSelectedModel ? [atSelectedModel.id] : selectedModels}
    {fileUploadCapableModels}
    {screenCaptureHandler}
    {inputFilesHandler}
    uploadFilesHandler={() => { filesInputElement.click(); }}
    uploadGoogleDriveHandler={...}
    uploadOneDriveHandler={...}
    {onUpload}
    onClose={...}

    <!-- NEW: capability props (previously on IntegrationsMenu) -->
    bind:selectedToolIds
    bind:selectedFilterIds
    bind:webSearchEnabled
    bind:imageGenerationEnabled
    bind:codeInterpreterEnabled
    {toggleFilters}
    {showToolsButton}
    {showWebSearchButton}
    {showImageGenerationButton}
    {showCodeInterpreterButton}
    closeOnOutsideClick={integrationsMenuCloseOnOutsideClick}
    onShowValves={(e) => {
        const { type, id } = e;
        selectedValvesType = type;
        selectedValvesItemId = id;
        showValvesModal = true;
        integrationsMenuCloseOnOutsideClick = false;
    }}
>
```

#### 4. Render Pinned Items in Bottom Bar
**File**: `src/lib/components/chat/MessageInput.svelte`

Replace the current capability pills section (lines 1609-1731) with a unified pinned items + active capabilities section.

The logic:

```svelte
<div class="ml-1 flex gap-1.5">
    <!-- Pinned context items (icon-only buttons) -->
    {#each ($settings?.pinnedInputItems ?? []) as itemId}
        {#if itemId === 'upload_files' && fileUploadEnabled}
            <Tooltip content={$i18n.t('Upload Files')} placement="top">
                <button class="pinned-icon-btn" on:click={() => filesInputElement.click()}>
                    <Clip className="size-4" />
                </button>
            </Tooltip>
        {:else if itemId === 'capture' && isFeatureEnabled('capture')}
            <Tooltip content={$i18n.t('Capture')} placement="top">
                <button class="pinned-icon-btn" on:click={screenCaptureHandler}>
                    <Camera className="size-4" />
                </button>
            </Tooltip>
        {:else if itemId === 'attach_webpage' && fileUploadEnabled}
            <Tooltip content={$i18n.t('Attach Webpage')} placement="top">
                <button class="pinned-icon-btn" on:click={() => { /* open webpage modal via InputMenu ref or event */ }}>
                    <Link className="size-4" />
                </button>
            </Tooltip>
        {:else if itemId === 'attach_notes' && ($config?.features?.enable_notes ?? false)}
            <Tooltip content={$i18n.t('Attach Notes')} placement="top">
                <button class="pinned-icon-btn" on:click={() => { /* open InputMenu to notes tab */ }}>
                    <PageEdit className="size-4" />
                </button>
            </Tooltip>
        {:else if itemId === 'knowledge' && isFeatureEnabled('knowledge')}
            <Tooltip content={$i18n.t('Attach Knowledge')} placement="top">
                <button class="pinned-icon-btn" on:click={() => { /* open InputMenu to knowledge tab */ }}>
                    <Database className="size-4" />
                </button>
            </Tooltip>
        {:else if itemId === 'reference_chats' && ($chats ?? []).length > 0}
            <Tooltip content={$i18n.t('Reference Chats')} placement="top">
                <button class="pinned-icon-btn" on:click={() => { /* open InputMenu to chats tab */ }}>
                    <ClockRotateRight className="size-4" />
                </button>
            </Tooltip>
        <!-- Pinned capability items (gray when inactive, sky-blue when active) -->
        {:else if itemId === 'web_search' && showWebSearchButton}
            <Tooltip content={$i18n.t('Web Search')} placement="top">
                <button
                    class="group p-[7px] rounded-full transition-colors duration-300 focus:outline-hidden {webSearchEnabled ? 'text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-600/10 border border-sky-200/40 dark:border-sky-500/20' : 'bg-transparent text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'}"
                    on:click={() => { webSearchEnabled = !webSearchEnabled; }}
                >
                    <GlobeAlt className="size-4" strokeWidth="1.75" />
                    {#if webSearchEnabled}
                        <div class="hidden group-hover:block"><XMark className="size-4" strokeWidth="1.75" /></div>
                    {/if}
                </button>
            </Tooltip>
        {:else if itemId === 'image_generation' && showImageGenerationButton}
            <!-- Same pattern as web_search but for imageGenerationEnabled -->
        {:else if itemId === 'code_interpreter' && showCodeInterpreterButton}
            <!-- Same pattern as web_search but for codeInterpreterEnabled -->
        {:else if itemId === 'tools' && showToolsButton}
            <!-- Show wrench icon, gray when no tools selected, highlight when tools selected -->
        {:else if itemId.startsWith('filter:') && toggleFilters.find(f => f.id === itemId.replace('filter:', ''))}
            <!-- Show filter icon, toggle on click -->
        {/if}
    {/each}

    <!-- Active capabilities NOT in pinned list (keep current pills for these) -->
    {#if (selectedToolIds ?? []).length > 0 && !($settings?.pinnedInputItems ?? []).includes('tools')}
        <!-- Current tools pill (lines 1610-1631) -->
    {/if}

    {#each selectedFilterIds as filterId}
        {#if !($settings?.pinnedInputItems ?? []).includes(`filter:${filterId}`)}
            <!-- Current filter pill (lines 1633-1668) -->
        {/if}
    {/each}

    {#if webSearchEnabled && !($settings?.pinnedInputItems ?? []).includes('web_search')}
        <!-- Current web search pill (lines 1670-1686) -->
    {/if}

    {#if imageGenerationEnabled && !($settings?.pinnedInputItems ?? []).includes('image_generation')}
        <!-- Current image gen pill (lines 1688-1704) -->
    {/if}

    {#if codeInterpreterEnabled && !($settings?.pinnedInputItems ?? []).includes('code_interpreter')}
        <!-- Current code interpreter pill (lines 1706-1730) -->
    {/if}
</div>
```

**The key behavior**: If a capability is pinned, its icon always shows (gray or sky-blue). If a capability is NOT pinned but IS active, the original pill shows as before. This avoids duplicate icons.

**Pinned context items** that need to open the InputMenu at a specific tab (Notes, Knowledge, Chats) need a mechanism to communicate with InputMenu. The simplest approach is to add an exported function or use `bind:this` on InputMenu and expose an `openTab(tab)` method:

```javascript
// In InputMenu.svelte, add:
export const openTab = (tabName) => {
    tab = tabName;
    show = true;
};
```

In MessageInput.svelte:
```svelte
let inputMenuRef;
<InputMenu bind:this={inputMenuRef} ...>
```

Then pinned context items can call `inputMenuRef.openTab('notes')` etc.

For "Upload Files" and "Capture" pinned icons, they call the same handlers directly (no need to open the menu).

For "Attach Webpage", expose a method or dispatch an event to open the AttachWebpageModal.

#### 5. Pinned Icon Button Styling
**File**: `src/lib/components/chat/MessageInput.svelte`

Shared class for pinned context icons (gray, no active state):
```css
"p-[7px] rounded-full bg-transparent text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors duration-300 focus:outline-hidden"
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript type checking passes: `npm run check`
- [ ] No linting errors: `npm run lint:frontend`
- [ ] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] Only one `+` button in the bottom bar (no integrations button)
- [ ] Pinned context items appear as icon buttons next to the `+` button
- [ ] Clicking pinned Upload Files opens file picker
- [ ] Clicking pinned Capture starts screen capture (desktop) or camera (mobile)
- [ ] Clicking pinned Webpage opens the webpage attachment modal
- [ ] Clicking pinned Notes/Knowledge/Chats opens the `+` menu at the correct sub-page
- [ ] Pinned capability icons show gray when inactive, sky-blue when active
- [ ] Clicking a pinned capability icon toggles it on/off
- [ ] Active capabilities NOT pinned still show as pills (no regressions)
- [ ] XMark hover-to-dismiss works on active capability icons
- [ ] No duplicate icons when a capability is both pinned and active

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before cleanup.

---

## Phase 4: Cleanup

### Overview
Remove the IntegrationsMenu component file and clean up any unused imports.

### Changes Required:

#### 1. Delete IntegrationsMenu
**File**: `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`

Delete this file entirely. All its functionality now lives in InputMenu.svelte.

#### 2. Clean Up Unused Imports in MessageInput.svelte
**File**: `src/lib/components/chat/MessageInput.svelte`

Remove:
- `import Component from '../icons/Component.svelte';` (was the integrations menu trigger icon)

Verify no other file imports IntegrationsMenu:
```bash
grep -r "IntegrationsMenu" src/
```

#### 3. Verify Placeholder.svelte
**File**: `src/lib/components/chat/Placeholder.svelte`

Placeholder.svelte renders MessageInput at line 203 with the same `bind:` props. Since we're not changing MessageInput's external API (just removing the separate IntegrationsMenu), Placeholder.svelte should work without changes. The new InputMenu props are passed internally by MessageInput, not from Placeholder.

**Wait** — MessageInput now needs `showToolsButton`, `showWebSearchButton`, etc. to pass to InputMenu, but these are computed internally inside MessageInput (not received from Placeholder). So no changes to Placeholder.svelte are needed.

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript type checking passes: `npm run check`
- [ ] No linting errors: `npm run lint:frontend`
- [ ] Build succeeds: `npm run build`
- [ ] `grep -r "IntegrationsMenu" src/` returns no results

#### Manual Verification:
- [ ] Full end-to-end test: open a chat, use the `+` menu, pin items, verify pins persist on reload
- [ ] Test all capability toggles work from both the dropdown and pinned icons
- [ ] Test on mobile viewport (dropdown height/scroll behavior)
- [ ] Test with different user roles (admin vs regular user) — feature gating preserved
- [ ] Test with no capabilities available — no empty capability section header shown

---

## Testing Strategy

### Manual Testing Steps:
1. Open a new chat, click `+` button — verify three sections with headers
2. Pin "Upload Files" — verify icon appears in bottom bar; click it — verify file picker opens
3. Pin "Web Search" — verify gray icon in bottom bar; click it — verify it turns sky-blue and web search activates
4. Reload page — verify all pinned items persist
5. Unpin items from the dropdown — verify icons disappear from bottom bar
6. Activate a non-pinned capability (e.g., Image Gen from dropdown) — verify sky-blue pill appears in bottom bar
7. Pin that capability while it's active — verify only one icon (sky-blue) in bottom bar, pill disappears
8. Test OneDrive — verify single click opens work picker (no sub-page)
9. Test tools sub-page — verify OAuth redirect works for unauthenticated tools
10. Test with `enable_web_search: false` config — verify web search doesn't appear in any section
11. Test mobile viewport — verify dropdown is scrollable and usable

## Performance Considerations

- Tools lazy-loading is preserved (only fetched on first dropdown open)
- Pin state reads from `$settings` store (already in memory, no extra API calls)
- Pin state writes are async but optimistic (UI updates immediately, then persists)
- No additional API calls for pinning — uses the same `updateUserSettings` endpoint

## References

- Research document: `thoughts/shared/research/2026-02-04-unified-plus-menu-input-bar-redesign.md`
- Current InputMenu: `src/lib/components/chat/MessageInput/InputMenu.svelte:110-561`
- Current IntegrationsMenu: `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte:96-407`
- MessageInput bottom bar: `src/lib/components/chat/MessageInput.svelte:1486-1731`
- Capability show* booleans: `src/lib/components/chat/MessageInput.svelte:435-493`
- Pin pattern: `src/lib/components/chat/ModelSelector.svelte:28-39`
- Settings type: `src/lib/stores/index.ts:155-217`
- Feature flags: `src/lib/utils/features.ts:27-35`
- Settings persistence: `src/lib/apis/users/index.ts:273-301`
