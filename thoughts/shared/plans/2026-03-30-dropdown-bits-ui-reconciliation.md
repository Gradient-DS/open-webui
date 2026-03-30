# Dropdown.svelte — Remove Orphaned bits-ui Usage

## Overview

Two files (`InputMenu.svelte`, `Knowledge.svelte`) use `DropdownMenu.Content` and `DropdownMenu.Item` from bits-ui **inside** the custom `Dropdown.svelte` wrapper. Since `Dropdown.svelte` doesn't provide a `DropdownMenu.Root` context, these bits-ui components crash at runtime. Fix: replace bits-ui components with plain HTML elements, matching upstream's approach.

## Current State Analysis

- **`Dropdown.svelte`**: Custom positioning/portal component — identical to upstream. Does NOT provide bits-ui context.
- **Upstream `InputMenu.svelte`**: Uses `Dropdown` wrapper + plain `<button>` elements + `<div>` for content container. No bits-ui.
- **Our `InputMenu.svelte`**: Uses `Dropdown` wrapper + `DropdownMenu.Content` + `DropdownMenu.Item` — crashes because no `DropdownMenu.Root`.
- **Our `Knowledge.svelte`**: Same broken pattern — `DropdownMenu.Content`/`DropdownMenu.Item` inside custom `Dropdown`.

### Key Discoveries:
- `Dropdown.svelte` is upstream's component, not ours — it exists in `upstream/dev` identically
- The bits-ui imports were likely left over from a pre-merge state where upstream used `DropdownMenu.Root`
- 5 `DropdownMenu.Item` instances in `InputMenu.svelte` (Upload Files, Capture, Webpage URL, Google Drive, OneDrive)
- 3 `DropdownMenu.Item` instances in `Knowledge.svelte` (Local KB, OneDrive KB, Google Drive KB)

## Desired End State

Both files use plain HTML (`<div>`, `<button>`) inside the custom `Dropdown` content slot, matching upstream's pattern. No bits-ui `DropdownMenu` imports remain. All custom functionality (feature flags, pin system, cloud integrations) preserved exactly.

### How to verify:
- `npm run build` succeeds
- InputMenu opens without runtime errors in browser console
- All menu items render and function (files, capture, webpage, notes, Google Drive, OneDrive, knowledge, chats, tools, toggles)
- Knowledge page "New Knowledge" dropdown renders with all KB type options
- Pin/unpin still works on all items

## What We're NOT Doing

- Not changing `Dropdown.svelte` itself (it's upstream-identical)
- Not touching `Selector.svelte` (it uses full `DropdownMenu.Root` → `Content` → `Item` chain correctly)
- Not changing any custom functionality or feature flags
- Not refactoring the pin system or menu structure

## Implementation Approach

Minimal, surgical replacements: swap bits-ui compound components with equivalent plain HTML elements that have the same CSS classes and event handlers. No structural changes.

## Phase 1: InputMenu.svelte

### Overview
Remove bits-ui dependency, replace `DropdownMenu.Content` with `<div>` and `DropdownMenu.Item` with `<button>`.

### Changes Required:

#### 1. Remove bits-ui imports
**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Remove line 2:
```diff
- import { DropdownMenu } from 'bits-ui';
```

Remove line 5 (`flyAndScale` import — only used by `DropdownMenu.Content`'s `transition` prop):
```diff
- import { flyAndScale } from '$lib/utils/transitions';
```

#### 2. Replace `<DropdownMenu.Content>` → `<div>`
**Lines 230-237** — replace the opening tag:
```diff
- <DropdownMenu.Content
-   class="w-full max-w-84 rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg max-h-96 overflow-y-auto overflow-x-hidden scrollbar-thin transition"
-   sideOffset={4}
-   alignOffset={-6}
-   side="bottom"
-   align="start"
-   transition={flyAndScale}
- >
+ <div
+   class="w-full max-w-84 rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg max-h-96 overflow-y-auto overflow-x-hidden scrollbar-thin transition"
+ >
```

**Line 1006** — replace closing tag:
```diff
- </DropdownMenu.Content>
+ </div>
```

Note: The `sideOffset`, `alignOffset`, `side`, `align`, and `transition` props are bits-ui specific — they're handled by the parent `Dropdown.svelte` already (positioning) and the content div's CSS (appearance). The `Dropdown` component's own `flyAndScale` transition handles the animation.

#### 3. Replace `<DropdownMenu.Item>` → `<button>` (5 instances)

Each `DropdownMenu.Item` becomes a `<button>` with the same classes and handlers. The only structural difference: add `type="button"` where missing.

**Upload Files** (lines 256, 281):
```diff
- <DropdownMenu.Item
+ <button
    class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer ..."
    ...
- </DropdownMenu.Item>
+ </button>
```

**Capture** (lines 294, 325):
```diff
- <DropdownMenu.Item
+ <button
    ...
- </DropdownMenu.Item>
+ </button>
```

**Webpage URL** (lines 339, 364):
```diff
- <DropdownMenu.Item
+ <button
    ...
- </DropdownMenu.Item>
+ </button>
```

**Google Drive** (lines 412, 457):
```diff
- <DropdownMenu.Item
+ <button
    ...
- </DropdownMenu.Item>
+ </button>
```

**OneDrive** (lines 462, 483):
```diff
- <DropdownMenu.Item
+ <button
    ...
- </DropdownMenu.Item>
+ </button>
```

For each replacement, add `w-full` to the class list to match the `<button>` items that are already plain HTML (e.g., Notes, Knowledge, Tools buttons at lines 378, 503, 588 all have `w-full`).

### Success Criteria:

#### Automated Verification:
- [ ] `npm run build` succeeds
- [ ] No `DropdownMenu` import remains in InputMenu.svelte: `grep -c "DropdownMenu" src/lib/components/chat/MessageInput/InputMenu.svelte` returns 0
- [ ] No `flyAndScale` import remains: `grep -c "flyAndScale" src/lib/components/chat/MessageInput/InputMenu.svelte` returns 0

#### Manual Verification:
- [ ] InputMenu opens without console errors
- [ ] All menu items render correctly
- [ ] Pin/unpin works
- [ ] Google Drive and OneDrive items appear when their feature flags are enabled
- [ ] Feature flag guards (`webpage_url`, `reference_chats`, `capture`) still hide/show items

**Implementation Note**: After completing this phase, pause for manual confirmation before proceeding.

---

## Phase 2: Knowledge.svelte

### Overview
Same pattern — remove bits-ui, replace with plain HTML.

### Changes Required:

#### 1. Remove imports
**File**: `src/lib/components/workspace/Knowledge.svelte`

Remove line 20-21:
```diff
- import { DropdownMenu } from 'bits-ui';
- import { flyAndScale } from '$lib/utils/transitions';
```

#### 2. Replace `<DropdownMenu.Content>` → `<div>`
**Lines 253-258** — replace opening tag:
```diff
- <DropdownMenu.Content
-   class="w-full max-w-[220px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg transition"
-   sideOffset={4}
-   side="bottom"
-   align="end"
-   transition={flyAndScale}
- >
+ <div
+   class="w-full max-w-[220px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg transition"
+ >
```

**Line 293** — replace closing tag:
```diff
- </DropdownMenu.Content>
+ </div>
```

#### 3. Replace `<DropdownMenu.Item>` → `<button>` (3 instances)

**Local KB** (lines 260, 268):
```diff
- <DropdownMenu.Item
+ <button
    class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
    ...
- </DropdownMenu.Item>
+ </button>
```

**OneDrive KB** (lines 271, 279):
```diff
- <DropdownMenu.Item
+ <button
    ...
- </DropdownMenu.Item>
+ </button>
```

**Google Drive KB** (lines 283, 291):
```diff
- <DropdownMenu.Item
+ <button
    ...
- </DropdownMenu.Item>
+ </button>
```

### Success Criteria:

#### Automated Verification:
- [ ] `npm run build` succeeds
- [ ] No `DropdownMenu` import remains: `grep -c "DropdownMenu" src/lib/components/workspace/Knowledge.svelte` returns 0

#### Manual Verification:
- [ ] "New Knowledge" dropdown opens on Knowledge page
- [ ] All KB type options render (Local, OneDrive, Google Drive)
- [ ] Clicking each option navigates to correct create URL with type param
- [ ] Feature flag guards still control OneDrive/Google Drive options

---

## Testing Strategy

### Automated:
- `npm run build` — confirms no compile errors
- Grep for orphaned `DropdownMenu` imports across all files using custom `Dropdown`

### Manual:
1. Open chat → click "+" button → verify all InputMenu items render and function
2. Navigate to Workspace → Knowledge → click "New Knowledge" → verify dropdown
3. Check browser console for runtime errors in both flows
4. Test pin/unpin on several InputMenu items

## References

- Upstream InputMenu: `git show upstream/dev:src/lib/components/chat/MessageInput/InputMenu.svelte`
- Upstream Dropdown: `git show upstream/dev:src/lib/components/common/Dropdown.svelte`
- Custom Dropdown: `src/lib/components/common/Dropdown.svelte`
