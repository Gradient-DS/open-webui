# Feature Changelog Flag Implementation Plan

## Overview

Implement a `FEATURE_CHANGELOG` environment variable that completely disables the "What's New" changelog popup for ALL users including admins. When disabled, this flag:
- Prevents the changelog modal from appearing on login
- Hides the "Show What's New modal on login" toggle from user settings

## Current State Analysis

The "What's New" popup is controlled by:
- **User setting** `showChangelog` (defaults to `true`) - stored in user settings
- **Role check** - Only shows for admin users
- **Version comparison** - Shows when `settings.version !== config.version`

### Key Discoveries:
- Changelog modal triggered in `src/routes/(app)/+layout.svelte:261-262`
- User toggle in `src/lib/components/chat/Settings/Interface.svelte:576-579`
- Changelog modal component at `src/lib/components/ChangelogModal.svelte`
- Existing feature flag pattern established in `utils/features.py` and `utils/features.ts`

## Desired End State

After implementation:
1. Setting `FEATURE_CHANGELOG=False` hides the changelog popup for **everyone** (admins included)
2. Setting toggle hidden from user settings when feature is disabled
3. No changelog-related UI elements visible when disabled

### Verification:
- Changelog popup visible when `FEATURE_CHANGELOG=True` (default)
- Changelog popup never shows when `FEATURE_CHANGELOG=False`
- Admin users cannot see or toggle changelog when disabled
- Settings toggle hidden when disabled

## What We're NOT Doing

- NOT removing the changelog API endpoint (`/api/changelog`) - it can remain accessible
- NOT removing the ChangelogModal component code
- NOT changing how changelog content is parsed from CHANGELOG.md
- NOT modifying the version comparison logic (just wrapping it)

## Implementation Approach

**Frontend-only protection** (simpler than voice feature since there's no sensitive API to protect):
1. **Backend**: Add `FEATURE_CHANGELOG` config and expose in `/api/config`
2. **Frontend**: Use `isFeatureEnabled('changelog')` to hide UI elements

This follows the established pattern from the existing feature flag implementation.

---

## Phase 1: Backend Feature Flag

### Overview
Add `FEATURE_CHANGELOG` environment variable and expose it in the config API.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: In the Feature Flags section with other FEATURE_* vars (around line 1597)

```python
FEATURE_CHANGELOG = os.environ.get("FEATURE_CHANGELOG", "True").lower() == "true"
```

#### 2. Backend Feature Utility (`backend/open_webui/utils/features.py`)

**File**: `backend/open_webui/utils/features.py`
**Changes**: Add `changelog` to Feature type and FEATURE_FLAGS dict

Add import:
```python
from open_webui.config import (
    FEATURE_CHAT_CONTROLS,
    FEATURE_CAPTURE,
    FEATURE_ARTIFACTS,
    FEATURE_PLAYGROUND,
    FEATURE_CHAT_OVERVIEW,
    FEATURE_NOTES_AI_CONTROLS,
    FEATURE_VOICE,
    FEATURE_CHANGELOG,  # Add this
)
```

Update Feature type:
```python
Feature = Literal[
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
    "notes_ai_controls",
    "voice",
    "changelog",  # Add this
]
```

Update FEATURE_FLAGS dict:
```python
FEATURE_FLAGS: dict[Feature, bool] = {
    "chat_controls": FEATURE_CHAT_CONTROLS,
    "capture": FEATURE_CAPTURE,
    "artifacts": FEATURE_ARTIFACTS,
    "playground": FEATURE_PLAYGROUND,
    "chat_overview": FEATURE_CHAT_OVERVIEW,
    "notes_ai_controls": FEATURE_NOTES_AI_CONTROLS,
    "voice": FEATURE_VOICE,
    "changelog": FEATURE_CHANGELOG,  # Add this
}
```

#### 3. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`
**Location**: Add import and expose in `/api/config` response

Add to imports (with other config imports):
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_CHANGELOG,
)
```

Add to features dict in `get_app_config()` function (in the features section):
```python
"feature_changelog": FEATURE_CHANGELOG,
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] TypeScript compiles: `npm run check`
- [x] API returns `feature_changelog` in config: `curl http://localhost:8080/api/config | jq '.features.feature_changelog'`
- [x] Default value is `true`

#### Manual Verification:
- [ ] Set `FEATURE_CHANGELOG=False` and restart - API returns `feature_changelog: false`

---

## Phase 2: Frontend Feature Utility Updates

### Overview
Add `changelog` feature to the frontend feature checking system.

### Changes Required:

#### 1. Feature Utility (`src/lib/utils/features.ts`)

**File**: `src/lib/utils/features.ts`
**Location**: Update Feature type (line 4-11)

```typescript
export type Feature =
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'playground'
	| 'chat_overview'
	| 'notes_ai_controls'
	| 'voice'
	| 'changelog';  // Add this
```

#### 2. TypeScript Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: In the Config type features section (around line 286)

Add to the features type:
```typescript
feature_changelog?: boolean;
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] No lint errors: `npm run lint:frontend`

---

## Phase 3: Layout Component Update

### Overview
Hide the changelog modal when the feature is disabled.

### Changes Required:

#### 1. App Layout (`src/routes/(app)/+layout.svelte`)

**File**: `src/routes/(app)/+layout.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Changelog trigger logic** - Line ~261-263:

**Before:**
```typescript
if ($user?.role === 'admin' && ($settings?.showChangelog ?? true)) {
    showChangelog.set($settings?.version !== $config.version);
}
```

**After:**
```typescript
if (isFeatureEnabled('changelog') && $user?.role === 'admin' && ($settings?.showChangelog ?? true)) {
    showChangelog.set($settings?.version !== $config.version);
}
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_CHANGELOG=True`: changelog popup appears for admins on version change
- [ ] With `FEATURE_CHANGELOG=False`: changelog popup never appears

---

## Phase 4: Settings Component Update

### Overview
Hide the "Show What's New modal on login" toggle when the feature is disabled.

### Changes Required:

#### 1. Interface Settings (`src/lib/components/chat/Settings/Interface.svelte`)

**File**: `src/lib/components/chat/Settings/Interface.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Changelog toggle** - Around line 566-584:

Find the block that contains the "Show What's New modal on login" toggle and wrap it with a feature check:

**Before:**
```svelte
{#if $user?.role === 'admin'}
    <div>
        <div class=" py-0.5 flex w-full justify-between">
            <div id="whats-new-label" class=" self-center text-xs">
                {$i18n.t(`Show "What's New" modal on login`)}
            </div>

            <div class="flex items-center gap-2 p-1">
                <Switch
                    ariaLabelledbyId="whats-new-label"
                    tooltip={true}
                    bind:state={showChangelog}
                    on:change={() => {
                        saveSettings({ showChangelog });
                    }}
                />
            </div>
        </div>
    </div>
{/if}
```

**After:**
```svelte
{#if isFeatureEnabled('changelog') && $user?.role === 'admin'}
    <div>
        <div class=" py-0.5 flex w-full justify-between">
            <div id="whats-new-label" class=" self-center text-xs">
                {$i18n.t(`Show "What's New" modal on login`)}
            </div>

            <div class="flex items-center gap-2 p-1">
                <Switch
                    ariaLabelledbyId="whats-new-label"
                    tooltip={true}
                    bind:state={showChangelog}
                    on:change={() => {
                        saveSettings({ showChangelog });
                    }}
                />
            </div>
        </div>
    </div>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_CHANGELOG=True`: toggle visible in admin settings
- [ ] With `FEATURE_CHANGELOG=False`: toggle hidden from admin settings

---

## Phase 5: Tests

### Overview
Add tests for the changelog feature flag behavior.

### Changes Required:

#### 1. Backend Unit Tests (`backend/open_webui/test/util/test_features.py`)

Add test cases for changelog feature:

```python
class TestChangelogFeature:
    """Tests for changelog feature flag."""

    def test_changelog_enabled_by_default(self):
        """Changelog feature should be enabled by default."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"changelog": True}
        ):
            assert is_feature_enabled("changelog") is True

    def test_changelog_can_be_disabled(self):
        """Changelog feature should be disableable."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"changelog": False}
        ):
            assert is_feature_enabled("changelog") is False
```

#### 2. Frontend Unit Tests (`src/lib/utils/features.test.ts`)

Add test cases for changelog feature:

```typescript
describe('changelog feature', () => {
    it('returns true when changelog feature is enabled', () => {
        vi.mocked(get).mockReturnValue({
            features: {
                feature_changelog: true
            }
        });
        expect(isFeatureEnabled('changelog')).toBe(true);
    });

    it('returns false when changelog feature is disabled', () => {
        vi.mocked(get).mockReturnValue({
            features: {
                feature_changelog: false
            }
        });
        expect(isFeatureEnabled('changelog')).toBe(false);
    });

    it('returns true when changelog feature is undefined (default)', () => {
        vi.mocked(get).mockReturnValue({
            features: {}
        });
        expect(isFeatureEnabled('changelog')).toBe(true);
    });
});
```

### Success Criteria:

#### Automated Verification:
- [x] Backend tests pass: `pytest backend/open_webui/test/util/test_features.py -v`
- [x] Frontend tests pass: `npm run test:frontend`

---

## Testing Strategy

### Manual Testing Steps:
1. Start backend with default env → verify changelog popup appears for admins on version change
2. Set `FEATURE_CHANGELOG=False` → restart → verify:
   - Changelog popup never appears for any user
   - "Show What's New modal" toggle hidden in Settings → Interface
3. Set `FEATURE_CHANGELOG=True` → restart → verify features restored

---

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add 1 line | Low |
| `backend/open_webui/utils/features.py` | Add ~3 lines | Low |
| `backend/open_webui/main.py` | Add ~2 lines | Low |
| `src/lib/utils/features.ts` | Add 1 line | Low |
| `src/lib/stores/index.ts` | Add 1 line | Low |
| `src/routes/(app)/+layout.svelte` | Add 1 import + 1 condition | Low |
| `src/lib/components/chat/Settings/Interface.svelte` | Add 1 import + 1 condition | Low |
| `backend/open_webui/test/util/test_features.py` | Add ~15 lines | None |
| `src/lib/utils/features.test.ts` | Add ~20 lines | None |

**Total: ~9 files, ~45 LOC additions**

## References

- Voice feature flag plan: `thoughts/shared/plans/2026-01-06-feature-voice-flag-implementation.md`
- Feature utility: `src/lib/utils/features.ts` and `backend/open_webui/utils/features.py`
- Changelog modal trigger: `src/routes/(app)/+layout.svelte:261-263`
- Settings toggle: `src/lib/components/chat/Settings/Interface.svelte:566-584`
