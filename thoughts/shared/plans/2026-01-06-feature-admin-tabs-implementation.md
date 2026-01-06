# Feature Flags for Admin Panel Tabs Implementation Plan

## Overview

Implement three `FEATURE_*` environment variables that completely hide admin panel tabs (Evaluations, Functions, Settings) for ALL users including admins. When disabled, these flags:
- Hide the tab from the admin navigation bar
- Redirect direct URL access back to `/admin`
- Apply universally - no user (including admins) can access disabled sections

**Feature Flags:**
- `FEATURE_ADMIN_EVALUATIONS` - Controls the Evaluations admin tab
- `FEATURE_ADMIN_FUNCTIONS` - Controls the Functions admin tab
- `FEATURE_ADMIN_SETTINGS` - Controls the Settings admin tab

## Current State Analysis

The admin panel navigation is defined in `src/routes/(app)/admin/+layout.svelte`:
- Lines 76-81: Evaluations tab link (`/admin/evaluations`)
- Lines 83-88: Functions tab link (`/admin/functions`)
- Lines 90-95: Settings tab link (`/admin/settings`)

The entire admin panel already has a role check at lines 15-20 that redirects non-admins to `/`. The feature flags add an additional layer of control at the feature level.

### Key Discoveries:
- Existing feature flag pattern established in `backend/open_webui/utils/features.py`
- Feature flags exposed via `/api/config` at `backend/open_webui/main.py:1930-1938`
- `isFeatureEnabled()` function available in frontend at `src/lib/utils/features.ts`
- Test patterns established in both `backend/open_webui/test/util/test_features.py` and `src/lib/utils/features.test.ts`

## Desired End State

After implementation:
1. Setting `FEATURE_ADMIN_EVALUATIONS=False` hides the Evaluations tab for **everyone** (admins included)
2. Setting `FEATURE_ADMIN_FUNCTIONS=False` hides the Functions tab for **everyone**
3. Setting `FEATURE_ADMIN_SETTINGS=False` hides the Settings tab for **everyone**
4. Direct URL access to disabled sections redirects to `/admin`
5. Default value is `True` for all (features enabled)

### Verification:
- All admin tabs visible when env vars are `True` (default)
- Admin tabs hidden when respective env vars are `False`
- Direct URL navigation to disabled sections redirects to `/admin`
- All other admin functionality remains intact

## What We're NOT Doing

- NOT modifying or removing the underlying functionality (APIs still work)
- NOT affecting workspace-level settings
- NOT removing the code/components (just hiding UI access)
- NOT adding backend route protection (these are UI-only controls)
- NOT changing the existing permission system

---

## Phase 1: Backend Feature Flags

### Overview
Add `FEATURE_ADMIN_EVALUATIONS`, `FEATURE_ADMIN_FUNCTIONS`, and `FEATURE_ADMIN_SETTINGS` environment variables and expose them in the config API.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: After line 1611 (after existing FEATURE_* vars)

```python
FEATURE_ADMIN_EVALUATIONS = os.environ.get("FEATURE_ADMIN_EVALUATIONS", "True").lower() == "true"
FEATURE_ADMIN_FUNCTIONS = os.environ.get("FEATURE_ADMIN_FUNCTIONS", "True").lower() == "true"
FEATURE_ADMIN_SETTINGS = os.environ.get("FEATURE_ADMIN_SETTINGS", "True").lower() == "true"
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
    FEATURE_CHANGELOG,
    FEATURE_SYSTEM_PROMPT,
    FEATURE_ADMIN_EVALUATIONS,   # Add this
    FEATURE_ADMIN_FUNCTIONS,     # Add this
    FEATURE_ADMIN_SETTINGS,      # Add this
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
    "changelog",
    "system_prompt",
    "admin_evaluations",   # Add this
    "admin_functions",     # Add this
    "admin_settings",      # Add this
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
    "changelog": FEATURE_CHANGELOG,
    "system_prompt": FEATURE_SYSTEM_PROMPT,
    "admin_evaluations": FEATURE_ADMIN_EVALUATIONS,   # Add this
    "admin_functions": FEATURE_ADMIN_FUNCTIONS,       # Add this
    "admin_settings": FEATURE_ADMIN_SETTINGS,         # Add this
}
```

#### 3. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`

Add imports with other FEATURE_* imports:
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_ADMIN_EVALUATIONS,
    FEATURE_ADMIN_FUNCTIONS,
    FEATURE_ADMIN_SETTINGS,
)
```

Add to features dict in `get_app_config()` function (after line 1938):
```python
"feature_admin_evaluations": FEATURE_ADMIN_EVALUATIONS,
"feature_admin_functions": FEATURE_ADMIN_FUNCTIONS,
"feature_admin_settings": FEATURE_ADMIN_SETTINGS,
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] API returns new feature flags: `curl http://localhost:8080/api/config | jq '.features'`
- [x] All three feature flags default to `true`

#### Manual Verification:
- [ ] Set `FEATURE_ADMIN_EVALUATIONS=False` and restart - API returns `feature_admin_evaluations: false`

---

## Phase 2: Frontend Feature Types

### Overview
Add the three new features to the frontend feature checking system.

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
	| 'changelog'
	| 'system_prompt'
	| 'admin_evaluations'   // Add this
	| 'admin_functions'     // Add this
	| 'admin_settings';     // Add this
```

#### 2. TypeScript Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: In the Config type features section (after line 288)

Add to the features type:
```typescript
feature_admin_evaluations?: boolean;
feature_admin_functions?: boolean;
feature_admin_settings?: boolean;
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] No lint errors: `npm run lint:frontend`

---

## Phase 3: Admin Layout UI Update

### Overview
Hide the admin navigation tabs when their respective features are disabled, and redirect direct URL access.

### Changes Required:

#### 1. Admin Layout (`src/routes/(app)/admin/+layout.svelte`)

**File**: `src/routes/(app)/admin/+layout.svelte`

Add import at top of script (after line 5):
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Lines 76-81** - Wrap Evaluations tab:
```svelte
{#if isFeatureEnabled('admin_evaluations')}
<a
    class="min-w-fit p-1.5 {$page.url.pathname.includes('/admin/evaluations')
        ? ''
        : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
    href="/admin/evaluations">{$i18n.t('Evaluations')}</a
>
{/if}
```

**Lines 83-88** - Wrap Functions tab:
```svelte
{#if isFeatureEnabled('admin_functions')}
<a
    class="min-w-fit p-1.5 {$page.url.pathname.includes('/admin/functions')
        ? ''
        : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
    href="/admin/functions">{$i18n.t('Functions')}</a
>
{/if}
```

**Lines 90-95** - Wrap Settings tab:
```svelte
{#if isFeatureEnabled('admin_settings')}
<a
    class="min-w-fit p-1.5 {$page.url.pathname.includes('/admin/settings')
        ? ''
        : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
    href="/admin/settings">{$i18n.t('Settings')}</a
>
{/if}
```

Update the `onMount` to also check feature flags and redirect if needed (lines 15-20):
```typescript
onMount(async () => {
    if ($user?.role !== 'admin') {
        await goto('/');
        return;
    }

    // Redirect if trying to access disabled feature
    const pathname = $page.url.pathname;
    if (pathname.includes('/admin/evaluations') && !isFeatureEnabled('admin_evaluations')) {
        await goto('/');
        return;
    }
    if (pathname.includes('/admin/functions') && !isFeatureEnabled('admin_functions')) {
        await goto('/');
        return;
    }
    if (pathname.includes('/admin/settings') && !isFeatureEnabled('admin_settings')) {
        await goto('/');
        return;
    }

    loaded = true;
});
```

Also add import for `page` from `$app/stores` if not already present (it's on line 6).

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With all features enabled (default): All three tabs visible
- [ ] With `FEATURE_ADMIN_EVALUATIONS=False`: Evaluations tab hidden
- [ ] With `FEATURE_ADMIN_FUNCTIONS=False`: Functions tab hidden
- [ ] With `FEATURE_ADMIN_SETTINGS=False`: Settings tab hidden
- [ ] Direct URL access to disabled sections redirects to `/`

---

## Phase 4: Tests

### Overview
Add tests for the three new feature flags.

### Changes Required:

#### 1. Backend Unit Tests (`backend/open_webui/test/util/test_features.py`)

Add test classes for each feature:

```python
class TestAdminEvaluationsFeature:
    """Tests for admin_evaluations feature flag."""

    def test_admin_evaluations_enabled_by_default(self):
        """Admin evaluations feature should be enabled by default."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_evaluations": True}
        ):
            assert is_feature_enabled("admin_evaluations") is True

    def test_admin_evaluations_can_be_disabled(self):
        """Admin evaluations feature should be disableable."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_evaluations": False}
        ):
            assert is_feature_enabled("admin_evaluations") is False

    def test_admin_evaluations_feature_is_registered(self):
        """Test that admin_evaluations feature is registered in FEATURE_FLAGS."""
        assert "admin_evaluations" in FEATURE_FLAGS

    def test_require_admin_evaluations_blocks_when_disabled(self):
        """Should raise 403 when admin_evaluations is disabled."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_evaluations": False},
        ):
            check = require_feature("admin_evaluations")
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestAdminFunctionsFeature:
    """Tests for admin_functions feature flag."""

    def test_admin_functions_enabled_by_default(self):
        """Admin functions feature should be enabled by default."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_functions": True}
        ):
            assert is_feature_enabled("admin_functions") is True

    def test_admin_functions_can_be_disabled(self):
        """Admin functions feature should be disableable."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_functions": False}
        ):
            assert is_feature_enabled("admin_functions") is False

    def test_admin_functions_feature_is_registered(self):
        """Test that admin_functions feature is registered in FEATURE_FLAGS."""
        assert "admin_functions" in FEATURE_FLAGS

    def test_require_admin_functions_blocks_when_disabled(self):
        """Should raise 403 when admin_functions is disabled."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_functions": False},
        ):
            check = require_feature("admin_functions")
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestAdminSettingsFeature:
    """Tests for admin_settings feature flag."""

    def test_admin_settings_enabled_by_default(self):
        """Admin settings feature should be enabled by default."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_settings": True}
        ):
            assert is_feature_enabled("admin_settings") is True

    def test_admin_settings_can_be_disabled(self):
        """Admin settings feature should be disableable."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_settings": False}
        ):
            assert is_feature_enabled("admin_settings") is False

    def test_admin_settings_feature_is_registered(self):
        """Test that admin_settings feature is registered in FEATURE_FLAGS."""
        assert "admin_settings" in FEATURE_FLAGS

    def test_require_admin_settings_blocks_when_disabled(self):
        """Should raise 403 when admin_settings is disabled."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"admin_settings": False},
        ):
            check = require_feature("admin_settings")
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403
```

Also update `TestAllFeatureFlags.test_expected_features_are_registered` to include the new features:
```python
expected_features = [
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
    "notes_ai_controls",
    "voice",
    "changelog",
    "system_prompt",
    "admin_evaluations",   # Add this
    "admin_functions",     # Add this
    "admin_settings",      # Add this
]
```

#### 2. Frontend Unit Tests (`src/lib/utils/features.test.ts`)

Add test cases for each new feature:

```typescript
describe('admin_evaluations feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when admin_evaluations feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_evaluations: true
			}
		});
		expect(isFeatureEnabled('admin_evaluations')).toBe(true);
	});

	it('returns false when admin_evaluations feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_evaluations: false
			}
		});
		expect(isFeatureEnabled('admin_evaluations')).toBe(false);
	});

	it('returns true when admin_evaluations feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('admin_evaluations')).toBe(true);
	});
});

describe('admin_functions feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when admin_functions feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_functions: true
			}
		});
		expect(isFeatureEnabled('admin_functions')).toBe(true);
	});

	it('returns false when admin_functions feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_functions: false
			}
		});
		expect(isFeatureEnabled('admin_functions')).toBe(false);
	});

	it('returns true when admin_functions feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('admin_functions')).toBe(true);
	});
});

describe('admin_settings feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when admin_settings feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_settings: true
			}
		});
		expect(isFeatureEnabled('admin_settings')).toBe(true);
	});

	it('returns false when admin_settings feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_settings: false
			}
		});
		expect(isFeatureEnabled('admin_settings')).toBe(false);
	});

	it('returns true when admin_settings feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('admin_settings')).toBe(true);
	});
});
```

Also update the `'all expected features are valid Feature types'` test:
```typescript
it('all expected features are valid Feature types', () => {
    const features: Feature[] = [
        'chat_controls',
        'capture',
        'artifacts',
        'playground',
        'chat_overview',
        'notes_ai_controls',
        'voice',
        'changelog',
        'system_prompt',
        'admin_evaluations',   // Add this
        'admin_functions',     // Add this
        'admin_settings'       // Add this
    ];

    features.forEach((feature) => {
        expect(typeof feature).toBe('string');
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
1. Start backend with default env → verify all three tabs visible in admin panel
2. Set `FEATURE_ADMIN_EVALUATIONS=False` → restart → verify Evaluations tab hidden
3. Set `FEATURE_ADMIN_EVALUATIONS=False` → try direct navigation to `/admin/evaluations` → verify redirect to `/`
4. Set `FEATURE_ADMIN_FUNCTIONS=False` → restart → verify Functions tab hidden
5. Set `FEATURE_ADMIN_FUNCTIONS=False` → try direct navigation to `/admin/functions/create` → verify redirect to `/`
6. Set `FEATURE_ADMIN_SETTINGS=False` → restart → verify Settings tab hidden
7. Set `FEATURE_ADMIN_SETTINGS=False` → try direct navigation to `/admin/settings/general` → verify redirect to `/`
8. Set all three to `True` → restart → verify all tabs restored

---

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add 3 lines | Low |
| `backend/open_webui/utils/features.py` | Add ~6 lines | Low |
| `backend/open_webui/main.py` | Add ~5 lines | Low |
| `src/lib/utils/features.ts` | Add 3 lines | Low |
| `src/lib/stores/index.ts` | Add 3 lines | Low |
| `src/routes/(app)/admin/+layout.svelte` | Add 1 import + wrap 3 tabs + update onMount | Low |
| `backend/open_webui/test/util/test_features.py` | Add ~60 lines | None |
| `src/lib/utils/features.test.ts` | Add ~65 lines | None |

**Total: ~8 files, ~150 LOC additions**

## References

- System prompt feature flag plan: `thoughts/shared/plans/2026-01-06-feature-system-prompt-flag-implementation.md`
- Feature flag wrapper plan: `thoughts/shared/plans/2026-01-06-feature-flag-wrapper-implementation.md`
- Feature utility: `src/lib/utils/features.ts` and `backend/open_webui/utils/features.py`
- Admin layout: `src/routes/(app)/admin/+layout.svelte:76-95`
