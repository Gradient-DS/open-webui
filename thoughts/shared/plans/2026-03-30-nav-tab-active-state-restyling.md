---
date: 2026-03-30T12:35:00+02:00
researcher: Claude
git_commit: 13c8f7c62
branch: feat/logos
repository: open-webui
topic: "Replace gray-out inactive nav tabs with background-fill active indicator"
tags: [plan, ui, navigation, tabs, accessibility, tailwind]
status: draft
last_updated: 2026-03-30
last_updated_by: Claude
---

# Nav Tab Active State Restyling — Implementation Plan

## Overview

Replace the "gray out inactive items" pattern with "all items readable, active gets background fill" across ~12 components. Users found the current gray inactive items (`text-gray-300`) confusing — they look disabled rather than unselected.

## Current State Analysis

**The pattern used in ~12 components:**
- Active: empty string `''` (inherits parent text color — no visual indicator)
- Inactive: `text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white`

This creates a large contrast gap where inactive items are barely visible in light mode (`gray-300` on white) and blend into the background in dark mode (`gray-600`).

**SettingsModal.svelte** has an additional `highContrastMode` branch that already uses background fill (`bg-gray-200 dark:bg-gray-800` for active), confirming this approach works.

### Key Discoveries:
- All components use the exact same inactive string: `text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white`
- `gray-850` is a valid custom shade (defined in `src/tailwind.css`)
- `bg-gray-50` is already used in 15 places across the codebase
- FolderPlaceholder.svelte tabs are **commented out** — update for consistency but low priority

## Desired End State

All nav tabs/sidebar items across settings, admin, workspace, playground, and home use:
- **Active:** `bg-gray-100 dark:bg-gray-800` — subtle background fill, full text color
- **Inactive:** `text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850` — readable text, hover shows light fill

Both light and dark mode should feel natural. Inactive items are clearly readable (not disabled-looking) while the active item stands out with its background pill.

### Verification:
- `npm run build` compiles without errors
- Visual check in light mode: inactive items are dark gray, active has light gray fill
- Visual check in dark mode: inactive items are light gray, active has dark gray fill
- All 4 route layouts + admin sidebars + settings modal show consistent styling

## What We're NOT Doing

- Not creating a shared Svelte component for nav items (too many different element types: `<a>`, `<button>`, varying base classes)
- Not changing the border-bottom pattern used in AnalyticsModelModal, CitationModal, etc. (those are a different pattern)
- Not modifying the AppSidebar shape-change navigation (different pattern)
- Not touching ChannelItem sidebar highlighting (different pattern)

## Implementation Approach

Mechanical find-and-replace across all files. Two sub-patterns exist:

**Sub-pattern A — Route-level ribbons** (horizontal tab bars using `<a>` with `min-w-fit p-1.5`):
```
// Active (was: '')
'bg-gray-100 dark:bg-gray-800 rounded-lg'

// Inactive (was: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white')
'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850 rounded-lg'
```

Note: `rounded-lg` is added to ribbons since they don't have it in their base classes (unlike sidebars which already have `rounded-lg`).

**Sub-pattern B — Component sidebars** (already have `rounded-lg` in base classes):
```
// Active (was: '')
'bg-gray-100 dark:bg-gray-800'

// Inactive (was: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white')
'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'
```

**Sub-pattern C — SettingsModal** (simplify highContrastMode logic):
The new default styling already provides good contrast. Simplify the two-tier conditional so `highContrastMode` uses a slightly stronger fill (`bg-gray-200 dark:bg-gray-700` for active) rather than a completely different approach.

## Phase 1: Route-Level Layouts

### Overview
Update the 4 horizontal ribbon/tab bars in route layouts.

### Changes Required:

#### 1. Admin Layout
**File:** `src/routes/(app)/admin/+layout.svelte` (lines 82-125)
**Changes:** 5 `<a>` elements — replace active/inactive classes

```svelte
<!-- BEFORE -->
class="min-w-fit p-1.5 {$page.url.pathname.includes('/admin/users')
    ? ''
    : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition select-none"

<!-- AFTER -->
class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/admin/users')
    ? 'bg-gray-100 dark:bg-gray-800'
    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
```

#### 2. Workspace Layout
**File:** `src/routes/(app)/workspace/+layout.svelte` (lines 115-151)
**Changes:** 4 `<a>` elements — same pattern as admin

#### 3. Playground Layout
**File:** `src/routes/(app)/playground/+layout.svelte` (lines 67-93)
**Changes:** 3 `<a>` elements (Chat, Completions, Images) — same pattern

#### 4. Home Layout
**File:** `src/routes/(app)/home/+layout.svelte` (lines 52-62)
**Changes:** 2 `<a>` elements (Notes, Calendar) — same pattern

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Admin ribbon: active tab has background fill, inactive tabs are readable dark/light gray
- [ ] Workspace ribbon: same behavior
- [ ] Playground ribbon: same behavior
- [ ] Home ribbon: same behavior
- [ ] Both light and dark mode look correct for all ribbons

---

## Phase 2: Admin Component Sidebars

### Overview
Update the 3 admin sidebar components that use vertical tab navigation.

### Changes Required:

#### 1. Admin Settings Sidebar
**File:** `src/lib/components/admin/Settings.svelte` (line 350-353)
**Changes:** Dynamic tab list via `{#each}` — single class string to update

```svelte
<!-- BEFORE -->
class="px-0.5 py-1 min-w-fit rounded-lg flex-1 lg:flex-none flex text-right transition select-none {selectedTab ===
tab.id
    ? ''
    : ' text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"

<!-- AFTER -->
class="px-0.5 py-1 min-w-fit rounded-lg flex-1 lg:flex-none flex text-right transition select-none {selectedTab ===
tab.id
    ? 'bg-gray-100 dark:bg-gray-800'
    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'}"
```

#### 2. Admin Users Sidebar
**File:** `src/lib/components/admin/Users.svelte` (lines 73-125)
**Changes:** 3 `<a>` elements (Overview, Groups, Pending Invites) — same pattern as Settings

#### 3. Admin Evaluations Sidebar
**File:** `src/lib/components/admin/Evaluations.svelte` (lines 61-89)
**Changes:** 2 `<a>` elements (Leaderboard, Feedback) — same pattern

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Admin Settings sidebar: active tab highlighted, all tabs readable
- [ ] Admin Users sidebar: same behavior
- [ ] Admin Evaluations sidebar: same behavior
- [ ] Vertical layout (desktop) and horizontal layout (mobile) both work

---

## Phase 3: Admin Modals

### Overview
Update the 3 modal components with internal tab navigation.

### Changes Required:

#### 1. EditGroupModal
**File:** `src/lib/components/admin/Users/Groups/EditGroupModal.svelte` (lines 136-186)
**Changes:** 3 `<button>` elements (General, Permissions, Users) — same inactive string replacement
Note: uses `max-w-fit w-fit` instead of `min-w-fit` but same active/inactive pattern.

#### 2. ModelSettingsModal
**File:** `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte` (lines 198-217)
**Changes:** 2 `<button>` elements (Defaults, Display)

#### 3. ManageModelsModal
**File:** `src/lib/components/admin/Settings/Models/ManageModelsModal.svelte` (lines 70-72)
**Changes:** 1 `<button>` element (Ollama) — plus the commented-out llama.cpp button for consistency

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] EditGroupModal: tabs show correctly with new styling
- [ ] ModelSettingsModal: tabs show correctly
- [ ] ManageModelsModal: single tab shows correctly

---

## Phase 4: User Settings Modal

### Overview
Update SettingsModal.svelte — the most complex case due to the `highContrastMode` two-tier conditional across ~10 tab buttons.

### Changes Required:

**File:** `src/lib/components/chat/SettingsModal.svelte` (lines 636-856)

**Simplify the conditional from 4-way to 2-way:**

```svelte
<!-- BEFORE (4-way conditional) -->
class={`px-0.5 md:px-2.5 py-1 min-w-fit rounded-xl flex-1 md:flex-none flex text-left transition
${
    selectedTab === 'general'
        ? ($settings?.highContrastMode ?? false)
            ? 'dark:bg-gray-800 bg-gray-200'
            : ''
        : ($settings?.highContrastMode ?? false)
            ? 'hover:bg-gray-200 dark:hover:bg-gray-800'
            : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'
}`}

<!-- AFTER (2-way conditional) -->
class={`px-0.5 md:px-2.5 py-1 min-w-fit rounded-xl flex-1 md:flex-none flex text-left transition
${
    selectedTab === 'general'
        ? 'bg-gray-100 dark:bg-gray-800'
        : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'
}`}
```

This removes the `highContrastMode` branching entirely — the new default already provides sufficient contrast through the background fill. The old `highContrastMode` was a workaround for exactly this problem.

**Also update the Admin Settings link** at line 866-868:
```svelte
<!-- BEFORE -->
class="... {$settings?.highContrastMode
    ? 'hover:bg-gray-200 dark:hover:bg-gray-800'
    : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"

<!-- AFTER -->
class="... text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850"
```

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Settings modal: all tabs readable, active tab has background fill
- [ ] Dark mode: same behavior
- [ ] Admin Settings link at bottom: styled as inactive, hover shows fill
- [ ] Verify `highContrastMode` toggle no longer affects tab styling (acceptable — the new default IS high contrast)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that the visual result is satisfactory before considering this done.

---

## Phase 5: Commented-Out Tabs (Optional)

### Overview
Update FolderPlaceholder.svelte tabs that are currently commented out, for consistency when they're eventually re-enabled.

**File:** `src/lib/components/chat/Placeholder/FolderPlaceholder.svelte` (lines 70-94)
**Changes:** 2 `<button>` elements inside `<!-- -->` comment block — update the inactive class string.

This is low priority since the code is commented out.

---

## Testing Strategy

### Visual Testing (primary):
All changes are purely visual (Tailwind class swaps). The main verification is visual inspection:
1. Navigate through every affected area in both light and dark mode
2. Verify active items have a visible background fill
3. Verify inactive items are readable (not grayed out)
4. Verify hover states show a subtle fill on inactive items
5. Check mobile (responsive) layouts for ribbon-style navs

### Build Verification:
- `npm run build` — confirms no syntax errors from class string changes

### No Unit/E2E Tests Needed:
These are cosmetic Tailwind class changes with no behavioral impact. No new tests required.

## References

- Research: `thoughts/shared/research/2026-03-30-nav-tab-active-state-restyling.md` (if created)
- Tailwind custom grays: `src/tailwind.css` (lines 5-18)
- Existing background-fill pattern: `src/lib/components/chat/Messages/Citations/CitationModal.svelte` (lines 188-199)
