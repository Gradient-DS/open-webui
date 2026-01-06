# Feature System Prompt Flag Implementation Plan

## Overview

Implement a `FEATURE_SYSTEM_PROMPT` environment variable that completely disables the custom system prompt and advanced parameters settings for ALL users including admins. When disabled, this flag:
- Hides the horizontal line separator from user settings (Settings → General)
- Hides the "System Prompt" section from user settings (Settings → General)
- Hides the "Advanced Parameters" section from user settings (Settings → General)
- Hides the "System Prompt" collapsible from chat controls panel
- Hides the "Advanced Params" collapsible from chat controls panel

**Important**: This is a feature flag, not a permission. When disabled, NO user (including admins) can set a custom system prompt or advanced parameters via the UI.

## Current State Analysis

The system prompt and advanced parameters settings appear in two UI locations:

1. **User Settings (General.svelte)** - Lines 279-315
   - Line 280: `<hr>` separator
   - Lines 282-293: "System Prompt" textarea
   - Lines 296-315: "Advanced Parameters" section with Show/Hide toggle
   - Both have permission checks that only apply to non-admin users

2. **Chat Controls (Controls.svelte)** - Lines 77-99
   - Lines 77-92: "System Prompt" collapsible section for per-chat override
   - Lines 94-99: "Advanced Params" collapsible section
   - Both have permission checks that only apply to non-admin users

### Key Discoveries:
- Existing feature flag pattern established in `utils/features.py` and `utils/features.ts`
- Feature flags exposed via `/api/config` at `main.py`
- `isFeatureEnabled()` function available in frontend
- The permission system is separate - it controls access for non-admin users; the feature flag is higher level

## Desired End State

After implementation:
1. Setting `FEATURE_SYSTEM_PROMPT=False` hides the system prompt AND advanced parameters for **everyone** (admins included)
2. Default value is `True` (features enabled)
3. No system prompt or advanced parameters UI visible anywhere when disabled

### Verification:
- System prompt and advanced parameters sections visible when `FEATURE_SYSTEM_PROMPT=True` (default)
- System prompt and advanced parameters sections hidden when `FEATURE_SYSTEM_PROMPT=False`
- The horizontal line separator is also hidden
- All other settings remain functional

## What We're NOT Doing

- NOT blocking any backend API (system prompt is just a user preference stored in settings)
- NOT preventing models from having system prompts (model-level system prompts in workspace still work)
- NOT removing the system prompt logic from the codebase
- NOT affecting folder-level system prompts
- NOT modifying the permission system

---

## Phase 1: Backend Feature Flag

### Overview
Add `FEATURE_SYSTEM_PROMPT` environment variable and expose it in the config API.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: In the Feature Flags section with other FEATURE_* vars (after FEATURE_VOICE around line 1609)

```python
FEATURE_SYSTEM_PROMPT = os.environ.get("FEATURE_SYSTEM_PROMPT", "True").lower() == "true"
```

#### 2. Backend Feature Utility (`backend/open_webui/utils/features.py`)

**File**: `backend/open_webui/utils/features.py`

Update imports to include:
```python
from open_webui.config import (
    FEATURE_CHAT_CONTROLS,
    FEATURE_CAPTURE,
    FEATURE_ARTIFACTS,
    FEATURE_PLAYGROUND,
    FEATURE_CHAT_OVERVIEW,
    FEATURE_NOTES_AI_CONTROLS,
    FEATURE_VOICE,
    FEATURE_SYSTEM_PROMPT,  # Add this
)
```

Update Feature type to include:
```python
Feature = Literal[
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
    "notes_ai_controls",
    "voice",
    "system_prompt",  # Add this
]
```

Update FEATURE_FLAGS dict to include:
```python
FEATURE_FLAGS: dict[Feature, bool] = {
    "chat_controls": FEATURE_CHAT_CONTROLS,
    "capture": FEATURE_CAPTURE,
    "artifacts": FEATURE_ARTIFACTS,
    "playground": FEATURE_PLAYGROUND,
    "chat_overview": FEATURE_CHAT_OVERVIEW,
    "notes_ai_controls": FEATURE_NOTES_AI_CONTROLS,
    "voice": FEATURE_VOICE,
    "system_prompt": FEATURE_SYSTEM_PROMPT,  # Add this
}
```

#### 3. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`

Add import with other FEATURE_* imports:
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_SYSTEM_PROMPT,
)
```

Add to features dict in `get_app_config()` function (in the features section around line 1932):
```python
"feature_system_prompt": FEATURE_SYSTEM_PROMPT,
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] API returns `feature_system_prompt` in config: `curl http://localhost:8080/api/config | jq '.features.feature_system_prompt'`
- [ ] Default value is `true`

#### Manual Verification:
- [ ] Set `FEATURE_SYSTEM_PROMPT=False` and restart - API returns `feature_system_prompt: false`

---

## Phase 2: Frontend Feature Types

### Overview
Add `system_prompt` feature to the frontend feature checking system.

### Changes Required:

#### 1. Feature Utility (`src/lib/utils/features.ts`)

**File**: `src/lib/utils/features.ts`
**Location**: Update Feature type

```typescript
export type Feature =
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'playground'
	| 'chat_overview'
	| 'notes_ai_controls'
	| 'voice'
	| 'system_prompt';  // Add this
```

#### 2. TypeScript Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: In the Config type features section

Add to the features type:
```typescript
feature_system_prompt?: boolean;
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

---

## Phase 3: User Settings UI Update

### Overview
Hide the "System Prompt" section, "Advanced Parameters" section, and the horizontal line separator in user settings when the feature is disabled.

### Changes Required:

#### 1. General Settings (`src/lib/components/chat/Settings/General.svelte`)

**File**: `src/lib/components/chat/Settings/General.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Lines 279-315** - Wrap both sections with a single feature flag check:

**Before (lines 279-315):**
```svelte
{#if $user?.role === 'admin' || (($user?.permissions.chat?.controls ?? true) && ($user?.permissions.chat?.system_prompt ?? true))}
    <hr class="border-gray-100/30 dark:border-gray-850/30 my-3" />
    <div>
        <div class=" my-2.5 text-sm font-medium">{$i18n.t('System Prompt')}</div>
        <Textarea ... />
    </div>
{/if}

{#if $user?.role === 'admin' || (($user?.permissions.chat?.controls ?? true) && ($user?.permissions.chat?.params ?? true))}
    <div class="mt-2 space-y-3 pr-1.5">
        <!-- Advanced Parameters content -->
    </div>
{/if}
```

**After:**
```svelte
{#if isFeatureEnabled('system_prompt')}
    {#if $user?.role === 'admin' || (($user?.permissions.chat?.controls ?? true) && ($user?.permissions.chat?.system_prompt ?? true))}
        <hr class="border-gray-100/30 dark:border-gray-850/30 my-3" />
        <div>
            <div class=" my-2.5 text-sm font-medium">{$i18n.t('System Prompt')}</div>
            <Textarea ... />
        </div>
    {/if}

    {#if $user?.role === 'admin' || (($user?.permissions.chat?.controls ?? true) && ($user?.permissions.chat?.params ?? true))}
        <div class="mt-2 space-y-3 pr-1.5">
            <!-- Advanced Parameters content -->
        </div>
    {/if}
{/if}
```

This wraps both sections in a single `{#if isFeatureEnabled('system_prompt')}` block, so when the feature is disabled, both the System Prompt, Advanced Parameters, AND the horizontal line are hidden together.

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_SYSTEM_PROMPT=True`: "System Prompt" and "Advanced Parameters" sections visible in Settings → General
- [ ] With `FEATURE_SYSTEM_PROMPT=False`: Both sections AND the horizontal line hidden from Settings → General

---

## Phase 4: Chat Controls UI Update

### Overview
Hide the "System Prompt" collapsible, "Advanced Params" collapsible, and the horizontal line separator in chat controls when the feature is disabled.

### Changes Required:

#### 1. Chat Controls (`src/lib/components/chat/Controls/Controls.svelte`)

**File**: `src/lib/components/chat/Controls/Controls.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Lines 77-102** - Wrap both sections with a single feature flag check:

**Before (lines 77-102):**
```svelte
{#if $user?.role === 'admin' || ($user?.permissions.chat?.system_prompt ?? true)}
    <Collapsible title={$i18n.t('System Prompt')} open={true} buttonClassName="w-full">
        <div class="" slot="content">
            <textarea ... />
        </div>
    </Collapsible>

    <hr class="my-2 border-gray-50 dark:border-gray-700/10" />
{/if}

{#if $user?.role === 'admin' || ($user?.permissions.chat?.params ?? true)}
    <Collapsible title={$i18n.t('Advanced Params')} open={true} buttonClassName="w-full">
        <div class="text-sm mt-1.5" slot="content">
            <div>
                <AdvancedParams admin={$user?.role === 'admin'} custom={true} bind:params />
            </div>
        </div>
    </Collapsible>
{/if}
```

**After:**
```svelte
{#if isFeatureEnabled('system_prompt')}
    {#if $user?.role === 'admin' || ($user?.permissions.chat?.system_prompt ?? true)}
        <Collapsible title={$i18n.t('System Prompt')} open={true} buttonClassName="w-full">
            <div class="" slot="content">
                <textarea ... />
            </div>
        </Collapsible>

        <hr class="my-2 border-gray-50 dark:border-gray-700/10" />
    {/if}

    {#if $user?.role === 'admin' || ($user?.permissions.chat?.params ?? true)}
        <Collapsible title={$i18n.t('Advanced Params')} open={true} buttonClassName="w-full">
            <div class="text-sm mt-1.5" slot="content">
                <div>
                    <AdvancedParams admin={$user?.role === 'admin'} custom={true} bind:params />
                </div>
            </div>
        </Collapsible>
    {/if}
{/if}
```

This wraps both sections in a single `{#if isFeatureEnabled('system_prompt')}` block, so when the feature is disabled, both collapsibles AND the horizontal line are hidden together.

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_SYSTEM_PROMPT=True`: "System Prompt" and "Advanced Params" collapsibles visible in chat controls panel
- [ ] With `FEATURE_SYSTEM_PROMPT=False`: Both collapsibles AND the horizontal line hidden from chat controls panel

---

## Phase 5: Tests

### Overview
Add tests for the system prompt feature flag behavior.

### Changes Required:

#### 1. Backend Unit Tests (`backend/open_webui/test/util/test_features.py`)

Add test cases for system_prompt feature:

```python
class TestSystemPromptFeature:
    """Tests for system prompt feature flag."""

    def test_system_prompt_enabled_by_default(self):
        """System prompt feature should be enabled by default."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"system_prompt": True}
        ):
            assert is_feature_enabled("system_prompt") is True

    def test_system_prompt_can_be_disabled(self):
        """System prompt feature should be disableable."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"system_prompt": False}
        ):
            assert is_feature_enabled("system_prompt") is False
```

#### 2. Frontend Unit Tests (`src/lib/utils/features.test.ts`)

Add test cases for system_prompt feature:

```typescript
describe('system_prompt feature', () => {
    it('returns true when system_prompt feature is enabled', () => {
        vi.mocked(get).mockReturnValue({
            features: {
                feature_system_prompt: true
            }
        });
        expect(isFeatureEnabled('system_prompt')).toBe(true);
    });

    it('returns false when system_prompt feature is disabled', () => {
        vi.mocked(get).mockReturnValue({
            features: {
                feature_system_prompt: false
            }
        });
        expect(isFeatureEnabled('system_prompt')).toBe(false);
    });

    it('returns true when system_prompt feature is undefined (default)', () => {
        vi.mocked(get).mockReturnValue({
            features: {}
        });
        expect(isFeatureEnabled('system_prompt')).toBe(true);
    });
});
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend tests pass: `pytest backend/open_webui/test/util/test_features.py -v`
- [ ] Frontend tests pass: `npm run test:frontend`

---

## Testing Strategy

### Manual Testing Steps:
1. Start backend with default env → verify visible in Settings → General:
   - Horizontal line separator below "Notifications"
   - "System Prompt" textarea
   - "Advanced Parameters" section with Show/Hide toggle
2. Start backend with default env → verify visible in chat controls panel:
   - "System Prompt" collapsible
   - Horizontal line separator
   - "Advanced Params" collapsible
3. Set `FEATURE_SYSTEM_PROMPT=False` → restart → verify in Settings → General:
   - Horizontal line hidden
   - "System Prompt" hidden
   - "Advanced Parameters" hidden
   - This applies to admin users too
4. Set `FEATURE_SYSTEM_PROMPT=False` → verify in chat controls panel:
   - "System Prompt" collapsible hidden
   - Horizontal line hidden
   - "Advanced Params" collapsible hidden
5. Set `FEATURE_SYSTEM_PROMPT=True` → restart → verify all features restored

---

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add 1 line | Low |
| `backend/open_webui/utils/features.py` | Add ~3 lines | Low |
| `backend/open_webui/main.py` | Add ~2 lines | Low |
| `src/lib/utils/features.ts` | Add 1 line | Low |
| `src/lib/stores/index.ts` | Add 1 line | Low |
| `src/lib/components/chat/Settings/General.svelte` | Add 1 import + wrap 2 sections | Low |
| `src/lib/components/chat/Controls/Controls.svelte` | Add 1 import + wrap 2 sections | Low |
| `backend/open_webui/test/util/test_features.py` | Add ~15 lines | None |
| `src/lib/utils/features.test.ts` | Add ~20 lines | None |

**Total: ~9 files, ~50 LOC additions**

## References

- Workspace feature flags plan: `thoughts/shared/plans/2026-01-06-feature-workspace-flags-implementation.md`
- Changelog feature flag plan: `thoughts/shared/plans/2026-01-06-feature-changelog-flag-implementation.md`
- Feature utility: `src/lib/utils/features.ts` and `backend/open_webui/utils/features.py`
- User settings: `src/lib/components/chat/Settings/General.svelte:279-315`
- Chat controls: `src/lib/components/chat/Controls/Controls.svelte:77-102`
