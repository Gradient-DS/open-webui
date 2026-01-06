# Feature Notes AI Controls Implementation Plan

## Overview

Implement a single feature flag `FEATURE_NOTES_AI_CONTROLS` that disables the Chat and Controls panels (and their UI buttons) in the notes editor for all users. This follows the same pattern as the existing tier-based feature flags in `thoughts/shared/plans/2026-01-06-feature-flag-wrapper-implementation.md`.

## Current State Analysis

The NoteEditor (`src/lib/components/notes/NoteEditor.svelte`) contains AI-related functionality:

1. **Chat button** (lines 990-1006) - Opens the chat panel for AI conversations
2. **Controls button** (lines 1008-1024) - Opens the controls panel for model selection
3. **Chat panel** (lines 1371-1391) - AI chat interface within notes
4. **Controls panel** (lines 1392-1400) - Model selection and file management
5. **AI floating menu** (lines 1293-1307) - Contains "Chat" and "Edit" options

### Key Discoveries:
- The existing feature flag system is defined in `backend/open_webui/config.py` and exposed via `GET /api/config`
- Frontend utility exists at `src/lib/utils/features.ts` for checking feature flags
- The NoteEditor only renders Chat/Controls buttons when `note?.write_access` is true (line 961-1025)
- The Chat and Controls panels are rendered conditionally based on `selectedPanel` state

## Desired End State

After implementation:
1. Setting `FEATURE_NOTES_AI_CONTROLS=False` hides the Chat button in the NoteEditor header
2. Setting `FEATURE_NOTES_AI_CONTROLS=False` hides the Controls button in the NoteEditor header
3. Setting `FEATURE_NOTES_AI_CONTROLS=False` prevents the Chat/Controls panels from being displayed
4. Setting `FEATURE_NOTES_AI_CONTROLS=False` hides the AI floating menu (sparkles button)
5. These restrictions apply to **all users including admins**

### Verification:
- With defaults (`FEATURE_NOTES_AI_CONTROLS=True`): all AI controls visible and functional
- With `FEATURE_NOTES_AI_CONTROLS=False`: AI controls hidden for everyone
- Basic note editing (title, content, files via drag-drop) still works when disabled

## What We're NOT Doing

- NOT disabling the entire notes feature (that's `ENABLE_NOTES`)
- NOT changing user permission behavior
- NOT adding multiple feature flags for individual components
- NOT adding admin panel configuration (env-only for SaaS tier control)

## Implementation Approach

**Single feature flag** that controls all AI-related functionality in the notes editor:
- Backend: Add `FEATURE_NOTES_AI_CONTROLS` environment variable
- Frontend: Use existing `isFeatureEnabled()` utility to conditionally render components

This is simpler than the main feature flag implementation because:
- All changes are in a single component (`NoteEditor.svelte`)
- No dedicated backend routes to protect (notes use shared APIs)
- No additional utility functions needed (reuse existing `features.ts`)

---

## Phase 1: Backend Configuration

### Overview
Add the feature flag environment variable and expose via API.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: After the existing FEATURE_* definitions (add after `FEATURE_CHAT_OVERVIEW`)

```python
FEATURE_NOTES_AI_CONTROLS = os.environ.get("FEATURE_NOTES_AI_CONTROLS", "True").lower() == "true"
```

#### 2. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`
**Location**: Add import and expose in `get_app_config()` features dict

Add to imports:
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_NOTES_AI_CONTROLS,
)
```

Add to features dict (inside the authenticated user section):
```python
"feature_notes_ai_controls": FEATURE_NOTES_AI_CONTROLS,
```

#### 3. Backend Feature Utility (`backend/open_webui/utils/features.py`)

**File**: `backend/open_webui/utils/features.py`
**Location**: Add to existing Feature type and FEATURE_FLAGS dict

Update the Feature Literal:
```python
Feature = Literal[
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
    "notes_ai_controls",  # Add this
]
```

Update FEATURE_FLAGS dict:
```python
FEATURE_FLAGS: dict[Feature, bool] = {
    # ... existing entries ...
    "notes_ai_controls": FEATURE_NOTES_AI_CONTROLS,
}
```

Add import:
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_NOTES_AI_CONTROLS,
)
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] API returns new feature flag: `curl http://localhost:8080/api/config | jq '.features.feature_notes_ai_controls'`
- [ ] Feature flag defaults to `true`

#### Manual Verification:
- [ ] Set `FEATURE_NOTES_AI_CONTROLS=False` and verify API returns `false`

---

## Phase 2: Frontend Type Definition

### Overview
Update TypeScript types to include the new feature flag.

### Changes Required:

#### 1. Store Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: In the `features` object type definition (around line 255-289)

Add to the features type:
```typescript
feature_notes_ai_controls?: boolean;
```

#### 2. Feature Utility Type (`src/lib/utils/features.ts`)

**File**: `src/lib/utils/features.ts`
**Location**: Update the Feature type

```typescript
export type Feature =
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'playground'
	| 'chat_overview'
	| 'notes_ai_controls';  // Add this
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

#### Manual Verification:
- [ ] None required for this phase

---

## Phase 3: NoteEditor Component Update

### Overview
Update the NoteEditor to conditionally render AI controls based on the feature flag.

### Changes Required:

#### 1. NoteEditor.svelte

**File**: `src/lib/components/notes/NoteEditor.svelte`

**Add import** (after line 44):
```svelte
import { isFeatureEnabled } from '$lib/utils/features';
```

**Update Chat button** (lines 990-1006):

Before:
```svelte
<Tooltip placement="top" content={$i18n.t('Chat')} className="cursor-pointer">
    <button
        class="p-1.5 bg-transparent hover:bg-white/5 transition rounded-lg"
        ...
    >
        <ChatBubbleOval />
    </button>
</Tooltip>
```

After:
```svelte
{#if isFeatureEnabled('notes_ai_controls')}
<Tooltip placement="top" content={$i18n.t('Chat')} className="cursor-pointer">
    <button
        class="p-1.5 bg-transparent hover:bg-white/5 transition rounded-lg"
        ...
    >
        <ChatBubbleOval />
    </button>
</Tooltip>
{/if}
```

**Update Controls button** (lines 1008-1024):

Before:
```svelte
<Tooltip placement="top" content={$i18n.t('Controls')} className="cursor-pointer">
    <button
        class="p-1.5 bg-transparent hover:bg-white/5 transition rounded-lg"
        ...
    >
        <AdjustmentsHorizontalOutline />
    </button>
</Tooltip>
```

After:
```svelte
{#if isFeatureEnabled('notes_ai_controls')}
<Tooltip placement="top" content={$i18n.t('Controls')} className="cursor-pointer">
    <button
        class="p-1.5 bg-transparent hover:bg-white/5 transition rounded-lg"
        ...
    >
        <AdjustmentsHorizontalOutline />
    </button>
</Tooltip>
{/if}
```

**Update AI floating menu** (lines 1278-1310):

Before:
```svelte
{:else}
    <div
        class="cursor-pointer flex gap-0.5 rounded-full border border-gray-50 dark:border-gray-850/30 dark:bg-gray-850 transition shadow-xl"
    >
        <Tooltip content={$i18n.t('AI')} placement="top">
            {#if editing}
                ...
            {:else}
                <AiMenu
                    onEdit={() => {
                        enhanceNoteHandler();
                    }}
                    onChat={() => {
                        showPanel = true;
                        selectedPanel = 'chat';
                    }}
                >
                    ...
                </AiMenu>
            {/if}
        </Tooltip>
    </div>
```

After:
```svelte
{:else}
    {#if isFeatureEnabled('notes_ai_controls')}
    <div
        class="cursor-pointer flex gap-0.5 rounded-full border border-gray-50 dark:border-gray-850/30 dark:bg-gray-850 transition shadow-xl"
    >
        <Tooltip content={$i18n.t('AI')} placement="top">
            {#if editing}
                ...
            {:else}
                <AiMenu
                    onEdit={() => {
                        enhanceNoteHandler();
                    }}
                    onChat={() => {
                        showPanel = true;
                        selectedPanel = 'chat';
                    }}
                >
                    ...
                </AiMenu>
            {/if}
        </Tooltip>
    </div>
    {/if}
```

**Update NotePanel visibility** (lines 1370-1402):

Before:
```svelte
<NotePanel bind:show={showPanel}>
    {#if selectedPanel === 'chat'}
        <Chat ... />
    {:else if selectedPanel === 'settings'}
        <Controls ... />
    {/if}
</NotePanel>
```

After:
```svelte
{#if isFeatureEnabled('notes_ai_controls')}
<NotePanel bind:show={showPanel}>
    {#if selectedPanel === 'chat'}
        <Chat ... />
    {:else if selectedPanel === 'settings'}
        <Controls ... />
    {/if}
</NotePanel>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`
- [ ] Frontend builds: `npm run build`
- [ ] Dev server starts: `npm run dev`

#### Manual Verification:
- [ ] With defaults: Chat, Controls buttons, AI menu, and panels all visible
- [ ] With `FEATURE_NOTES_AI_CONTROLS=False`: All AI controls hidden
- [ ] Basic note editing still works when AI controls are disabled
- [ ] Drag-drop file upload still works (opens settings panel only if feature enabled)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Edge Case Handling

### Overview
Handle edge cases where the panel might be open when the feature gets disabled.

### Changes Required:

#### 1. Initialize showPanel based on feature flag

**File**: `src/lib/components/notes/NoteEditor.svelte`
**Location**: Update the `showPanel` default and the file upload handler

The `showPanel` variable is initialized to `false` (line 126), which is correct. However, we should ensure the panel doesn't open when uploading files if the feature is disabled.

**Update uploadFileHandler** (around line 406-411):

Before:
```javascript
// open the settings panel if it is not open
selectedPanel = 'settings';

if (!showPanel) {
    showPanel = true;
}
```

After:
```javascript
// open the settings panel if it is not open (only if feature is enabled)
if (isFeatureEnabled('notes_ai_controls')) {
    selectedPanel = 'settings';

    if (!showPanel) {
        showPanel = true;
    }
}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] File upload via drag-drop works when AI controls disabled (panel stays closed)
- [ ] File upload via drag-drop works when AI controls enabled (panel opens)

---

## Phase 5: Documentation

### Overview
Update environment variable documentation.

### Changes Required:

#### 1. Update `.env.example` (if exists)

Add documentation for the new feature flag:
```bash
# Feature Flags (SaaS Tier Control)
# ...existing flags...
FEATURE_NOTES_AI_CONTROLS=True   # AI chat and controls panel in notes editor
```

### Success Criteria:

#### Automated Verification:
- [ ] None

#### Manual Verification:
- [ ] Documentation is clear and accurate

---

## Testing Strategy

### Manual Testing Steps:
1. Start backend with default env → verify all AI controls visible in notes
2. Set `FEATURE_NOTES_AI_CONTROLS=False` → restart → verify:
   - Chat button hidden in header
   - Controls button hidden in header
   - AI floating menu (sparkles) hidden
   - NotePanel not rendered
3. With feature disabled, verify basic note functionality:
   - Can edit note title
   - Can edit note content
   - Can drag-drop files (panel should not open)
   - Can undo/redo
   - Can access note menu (download, delete, copy link)

### Future Unit Tests:
If time permits, add tests to `src/lib/utils/features.test.ts` for the new feature type.

---

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add 1 line | Low |
| `backend/open_webui/main.py` | Add 2 lines (import + dict entry) | Low |
| `backend/open_webui/utils/features.py` | Add 3 lines (import + type + dict) | Low |
| `src/lib/stores/index.ts` | Add 1 line to Config type | Low |
| `src/lib/utils/features.ts` | Add 1 line to Feature type | Low |
| `src/lib/components/notes/NoteEditor.svelte` | Add 1 import + 4 conditional blocks | Low |

**Total: 6 files, ~15 LOC additions**

---

## References

- Existing feature flag plan: `thoughts/shared/plans/2026-01-06-feature-flag-wrapper-implementation.md`
- NoteEditor component: `src/lib/components/notes/NoteEditor.svelte`
- Feature utility: `src/lib/utils/features.ts`
- Backend config: `backend/open_webui/config.py`
