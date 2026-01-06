# Feature Flag Wrapper Implementation Plan

## Overview

Implement a tier-based feature flag system using `FEATURE_*` environment variables that control feature visibility for **both admins and users**. This differs from the existing permission system which admins bypass - these feature flags apply universally regardless of role.

## Current State Analysis

Open WebUI has two types of configuration:
1. **Environment-only flags** (e.g., `ENABLE_ADMIN_EXPORT`) - static, restart required
2. **PersistentConfig flags** (e.g., `ENABLE_FOLDERS`) - runtime modifiable via admin panel

For SaaS tier control, we need **environment-only flags** that cannot be overridden by tenant admins.

### Key Discoveries:
- Feature flags exposed via `GET /api/config` at `main.py:1846-1969`
- Frontend consumes via `config` store at `src/lib/stores/index.ts:15`
- Admin bypass pattern: `$user?.role === 'admin' || ($user?.permissions?.X ?? true)`
- Testing: Cypress E2E (`cypress/e2e/`), Vitest frontend, pytest backend

## Desired End State

After implementation:
1. Setting `FEATURE_CHAT_CONTROLS=False` hides chat controls for **everyone** (admins included)
2. Setting `FEATURE_CAPTURE=False` hides the capture button for **everyone**
3. Setting `FEATURE_ARTIFACTS=False` disables artifact rendering for **everyone**
4. Setting `FEATURE_PLAYGROUND=False` hides playground access for **everyone**
5. Setting `FEATURE_CHAT_OVERVIEW=False` hides chat overview for **everyone**

### Verification:
- All features visible when env vars are `True` (default)
- Features hidden when env vars are `False`
- Admin users cannot see or access disabled features
- Cypress E2E tests pass for feature visibility

## What We're NOT Doing

- NOT making these configurable via admin panel (intentionally env-only for SaaS tier control)
- NOT changing existing permission system behavior
- NOT adding feature flag for workspace (covered in separate ticket)

## Implementation Approach

**Two-layer protection:**
1. **Backend**: Create a FastAPI dependency (`require_feature`) that returns 403 when features are disabled
2. **Frontend**: Create a utility (`src/lib/utils/features.ts`) for UI visibility checks

This provides:
- Single point of change for feature logic (both layers)
- Proper HTTP 403 responses for disabled features
- Clean UI hiding for disabled features
- Easier rebasing when upstream updates
- Testability

**Note on current features:** Most of the 5 initial features (chat controls, capture, artifacts, chat overview) don't have dedicated backend routes - they use shared APIs like `/api/chat/completions`. The playground route protection will be added via the page layout. The backend dependency infrastructure is added for future features with dedicated routes.

## Phase 1: Backend Feature Flags

### Overview
Add environment variable definitions and expose via API.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: After line ~1597 (after ENABLE_ADMIN_* section)

```python
####################################
# Feature Flags (SaaS Tier Control)
####################################

FEATURE_CHAT_CONTROLS = os.environ.get("FEATURE_CHAT_CONTROLS", "True").lower() == "true"
FEATURE_CAPTURE = os.environ.get("FEATURE_CAPTURE", "True").lower() == "true"
FEATURE_ARTIFACTS = os.environ.get("FEATURE_ARTIFACTS", "True").lower() == "true"
FEATURE_PLAYGROUND = os.environ.get("FEATURE_PLAYGROUND", "True").lower() == "true"
FEATURE_CHAT_OVERVIEW = os.environ.get("FEATURE_CHAT_OVERVIEW", "True").lower() == "true"
```

#### 2. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`
**Location**: Inside `get_app_config()` function, add to the `features` dict in the authenticated user section (~line 1904-1920)

Add import at top of file:
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_CHAT_CONTROLS,
    FEATURE_CAPTURE,
    FEATURE_ARTIFACTS,
    FEATURE_PLAYGROUND,
    FEATURE_CHAT_OVERVIEW,
)
```

Add to features dict (inside the `if user is not None` block):
```python
"feature_chat_controls": FEATURE_CHAT_CONTROLS,
"feature_capture": FEATURE_CAPTURE,
"feature_artifacts": FEATURE_ARTIFACTS,
"feature_playground": FEATURE_PLAYGROUND,
"feature_chat_overview": FEATURE_CHAT_OVERVIEW,
```

#### 3. Backend Feature Utility (NEW: `backend/open_webui/utils/features.py`)

**File**: `backend/open_webui/utils/features.py` (new file)

```python
"""
Feature flag utilities for SaaS tier-based feature control.

These flags apply to ALL users including admins and cannot be overridden
via the admin panel. Use environment variables to control features per deployment.
"""

from typing import Literal
from fastapi import HTTPException, status

from open_webui.config import (
    FEATURE_CHAT_CONTROLS,
    FEATURE_CAPTURE,
    FEATURE_ARTIFACTS,
    FEATURE_PLAYGROUND,
    FEATURE_CHAT_OVERVIEW,
)

Feature = Literal[
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
]

FEATURE_FLAGS: dict[Feature, bool] = {
    "chat_controls": FEATURE_CHAT_CONTROLS,
    "capture": FEATURE_CAPTURE,
    "artifacts": FEATURE_ARTIFACTS,
    "playground": FEATURE_PLAYGROUND,
    "chat_overview": FEATURE_CHAT_OVERVIEW,
}


def is_feature_enabled(feature: Feature) -> bool:
    """
    Check if a feature is enabled globally.

    Args:
        feature: The feature identifier to check

    Returns:
        True if the feature is enabled, False otherwise
    """
    return FEATURE_FLAGS.get(feature, True)


def require_feature(feature: Feature):
    """
    FastAPI dependency that raises 403 if feature is disabled.

    Usage:
        @router.get("/some-endpoint")
        async def endpoint(
            user=Depends(get_current_user),
            _=Depends(require_feature("playground"))
        ):
            ...

    Args:
        feature: The feature identifier to require

    Returns:
        A dependency function that raises HTTPException if feature is disabled
    """
    def check_feature():
        if not is_feature_enabled(feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature '{feature}' is not available in your plan"
            )
        return True
    return check_feature
```

#### 4. Playground Route Protection (`src/routes/(app)/playground/+layout.svelte`)

**File**: `src/routes/(app)/playground/+layout.svelte`
**Location**: Add server-side check in load function

Since the playground is a SvelteKit route, we add protection at the layout level. The existing page components (`Chat.svelte`, `Completions.svelte`) already have client-side guards, but we enhance the layout to redirect immediately.

**Before** (current layout just renders slot):
```svelte
<script lang="ts">
    // existing imports
</script>

<!-- existing content -->
```

**After:**
```svelte
<script lang="ts">
    import { onMount } from 'svelte';
    import { goto } from '$app/navigation';
    import { config, user } from '$lib/stores';
    import { isFeatureEnabled } from '$lib/utils/features';

    // existing imports...

    onMount(() => {
        // Check feature flag - applies to everyone including admins
        if (!isFeatureEnabled('playground')) {
            goto('/');
            return;
        }

        // Check admin role (existing behavior)
        if ($user?.role !== 'admin') {
            goto('/');
            return;
        }
    });
</script>

<!-- existing content -->
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] API returns feature flags: `curl http://localhost:8080/api/config | jq '.features'`
- [x] All feature flags default to `true`

#### Manual Verification:
- [ ] Set `FEATURE_PLAYGROUND=False` and verify API returns `false`

---

## Phase 2: Frontend Feature Utility

### Overview
Create centralized feature checking utility for consistent feature flag consumption.

### Changes Required:

#### 1. Feature Utility (NEW: `src/lib/utils/features.ts`)

**File**: `src/lib/utils/features.ts` (new file)

```typescript
import { get } from 'svelte/store';
import { config } from '$lib/stores';

export type Feature =
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'playground'
	| 'chat_overview';

/**
 * Check if a feature is enabled globally.
 * These flags apply to ALL users including admins.
 * Used for SaaS tier-based feature control.
 */
export function isFeatureEnabled(feature: Feature): boolean {
	const $config = get(config);
	const key = `feature_${feature}` as keyof typeof $config.features;
	// Default to true if not set (backwards compatibility)
	return $config?.features?.[key] ?? true;
}

/**
 * Check if user has access to a feature considering both:
 * 1. Global feature flag (tier-based, applies to everyone)
 * 2. User permission (role-based, admins bypass)
 *
 * @param feature - The feature to check
 * @param user - The user object with role and permissions
 * @param permissionPath - Optional dot-notation path to permission (e.g., 'chat.controls')
 */
export function hasFeatureAccess(
	feature: Feature,
	user: { role?: string; permissions?: Record<string, any> } | null,
	permissionPath?: string
): boolean {
	// First check: feature must be enabled globally
	if (!isFeatureEnabled(feature)) {
		return false;
	}

	// If no permission check needed, feature is accessible
	if (!permissionPath) {
		return true;
	}

	// Admin bypass for permissions (but NOT for feature flags)
	if (user?.role === 'admin') {
		return true;
	}

	// Check specific permission for non-admins
	const keys = permissionPath.split('.');
	let perms: any = user?.permissions;
	for (const key of keys) {
		perms = perms?.[key];
	}
	return Boolean(perms ?? true); // Default to true if permission not set
}
```

#### 2. TypeScript Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: Extend the `Config` type definition (around line 255-289)

Add these properties to the `features` object type:
```typescript
feature_chat_controls?: boolean;
feature_capture?: boolean;
feature_artifacts?: boolean;
feature_playground?: boolean;
feature_chat_overview?: boolean;
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] No lint errors: `npm run lint:frontend` (for new files)

#### Manual Verification:
- [ ] None required for this phase

---

## Phase 3: Component Updates

### Overview
Update each component to use the feature utility for visibility checks.

### Changes Required:

#### 1. Chat Controls (`src/lib/components/chat/Controls/Controls.svelte`)

**File**: `src/lib/components/chat/Controls/Controls.svelte`
**Location**: Line 33

**Before:**
```svelte
{#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
```

**After:**
```svelte
<script lang="ts">
// Add import at top of script
import { isFeatureEnabled } from '$lib/utils/features';
</script>

{#if isFeatureEnabled('chat_controls') && ($user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true))}
```

Also update the mobile menu check in `src/lib/components/layout/Navbar/Menu.svelte:316-330`:

**Before:**
```svelte
{#if $mobile && ($user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true))}
```

**After:**
```svelte
{#if $mobile && isFeatureEnabled('chat_controls') && ($user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true))}
```

#### 2. Capture Option (`src/lib/components/chat/MessageInput/InputMenu.svelte`)

**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`
**Location**: Line 164-184 (Capture menu item)

**Before:**
```svelte
<DropdownMenu.Item
    class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl {!fileUploadEnabled ? 'opacity-50' : ''}"
    on:click={() => {
```

**After:**
```svelte
<script>
// Add import at top of script
import { isFeatureEnabled } from '$lib/utils/features';
</script>

{#if isFeatureEnabled('capture')}
<DropdownMenu.Item
    class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl {!fileUploadEnabled ? 'opacity-50' : ''}"
    on:click={() => {
        // ... existing code ...
    }}
>
    <Camera />
    <div class="line-clamp-1">{$i18n.t('Capture')}</div>
</DropdownMenu.Item>
{/if}
```

#### 3. Artifacts Rendering

**File**: `src/lib/components/chat/Messages/ContentRenderer.svelte`
**Location**: Line 179-191 (auto-detection trigger)

**Before:**
```javascript
onUpdate={async (token) => {
    const { lang, text: code } = token;
    if (
        ($settings?.detectArtifacts ?? true) &&
```

**After:**
```svelte
<script>
// Add import at top of script
import { isFeatureEnabled } from '$lib/utils/features';
</script>

onUpdate={async (token) => {
    const { lang, text: code } = token;
    if (
        isFeatureEnabled('artifacts') &&
        ($settings?.detectArtifacts ?? true) &&
```

**File**: `src/lib/components/layout/Navbar/Menu.svelte`
**Location**: Line 346-359 (Artifacts menu item)

Wrap the artifacts menu item:
```svelte
{#if isFeatureEnabled('artifacts') && ($artifactContents ?? []).length > 0}
    <!-- existing Artifacts menu item -->
{/if}
```

**File**: `src/lib/components/chat/ChatControls.svelte`
**Location**: Lines 185-186 (mobile) and 275-276 (desktop)

Add feature check before rendering Artifacts component (both locations):
```svelte
{:else if $showArtifacts && isFeatureEnabled('artifacts')}
```

#### 4. Playground (`src/lib/components/layout/Sidebar/UserMenu.svelte`)

**File**: `src/lib/components/layout/Sidebar/UserMenu.svelte`
**Location**: Line 245-262

**Before:**
```svelte
{#if role === 'admin'}
    <DropdownMenu.Item
        as="a"
        href="/playground"
```

**After:**
```svelte
<script>
// Add import at top of script
import { isFeatureEnabled } from '$lib/utils/features';
</script>

{#if role === 'admin' && isFeatureEnabled('playground')}
    <DropdownMenu.Item
        as="a"
        href="/playground"
```

**File**: `src/lib/components/playground/Chat.svelte`
**Location**: Line 196-199

**Before:**
```javascript
onMount(async () => {
    if ($user?.role !== 'admin') {
        await goto('/');
    }
```

**After:**
```javascript
import { isFeatureEnabled } from '$lib/utils/features';

onMount(async () => {
    if ($user?.role !== 'admin' || !isFeatureEnabled('playground')) {
        await goto('/');
    }
```

**File**: `src/lib/components/playground/Completions.svelte`
**Location**: Line 106-109

Same change as Chat.svelte above.

#### 5. Chat Overview (`src/lib/components/layout/Navbar/Menu.svelte`)

**File**: `src/lib/components/layout/Navbar/Menu.svelte`
**Location**: Line 332-344

**Before:**
```svelte
<DropdownMenu.Item
    id="chat-overview-button"
    on:click={async () => {
```

**After:**
```svelte
{#if isFeatureEnabled('chat_overview')}
<DropdownMenu.Item
    id="chat-overview-button"
    on:click={async () => {
        // ... existing code ...
    }}
>
    <Map className=" size-4" strokeWidth="1.5" />
    <div class="flex items-center">{$i18n.t('Overview')}</div>
</DropdownMenu.Item>
{/if}
```

**File**: `src/lib/components/chat/ChatControls.svelte`
**Location**: Lines 187-199 (mobile) and 277-295 (desktop)

Add feature check:
```svelte
{:else if $showOverview && isFeatureEnabled('chat_overview')}
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] No lint errors: `npm run lint:frontend` (for new files)
- [x] Frontend builds: `npm run build`
- [ ] Dev server starts: `npm run dev`

#### Manual Verification:
- [ ] With defaults: all features visible and functional
- [ ] With `FEATURE_CHAT_CONTROLS=False`: controls hidden for admin AND user
- [ ] With `FEATURE_CAPTURE=False`: capture button hidden
- [ ] With `FEATURE_ARTIFACTS=False`: artifacts panel doesn't appear
- [ ] With `FEATURE_PLAYGROUND=False`: playground not accessible
- [ ] With `FEATURE_CHAT_OVERVIEW=False`: overview option hidden

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Tests

### Overview
Add Cypress E2E tests for feature flag behavior.

### Changes Required:

#### 1. Feature Flags Test File (NEW: `cypress/e2e/feature-flags.cy.ts`)

**File**: `cypress/e2e/feature-flags.cy.ts` (new file)

```typescript
describe('Feature Flags', () => {
	beforeEach(() => {
		cy.loginAdmin();
		cy.visit('/');
	});

	describe('Default behavior (all features enabled)', () => {
		it('should show chat controls button', () => {
			// Start a chat first
			cy.get('#chat-input').type('Hello{enter}');
			cy.get('#chat-controls-button', { timeout: 10000 }).should('exist');
		});

		it('should show capture option in input menu', () => {
			cy.get('#input-menu-button').click();
			cy.contains('Capture').should('exist');
		});

		it('should show playground link for admin', () => {
			cy.get('#user-menu-button').click();
			cy.contains('Playground').should('exist');
		});

		it('should show overview option in chat menu', () => {
			// Start a chat first
			cy.get('#chat-input').type('Hello{enter}');
			cy.wait(2000);
			cy.get('#chat-context-menu-button').click();
			cy.get('#chat-overview-button').should('exist');
		});
	});

	// Note: Testing with features disabled requires backend restart with env vars
	// These tests document expected behavior when features are disabled
	describe('Documentation: Expected behavior when features disabled', () => {
		it.skip('FEATURE_CHAT_CONTROLS=False should hide controls for everyone', () => {
			// Requires: FEATURE_CHAT_CONTROLS=False in backend env
			cy.get('#chat-controls-button').should('not.exist');
		});

		it.skip('FEATURE_CAPTURE=False should hide capture option', () => {
			// Requires: FEATURE_CAPTURE=False in backend env
			cy.get('#input-menu-button').click();
			cy.contains('Capture').should('not.exist');
		});

		it.skip('FEATURE_PLAYGROUND=False should hide playground for admin', () => {
			// Requires: FEATURE_PLAYGROUND=False in backend env
			cy.get('#user-menu-button').click();
			cy.contains('Playground').should('not.exist');
		});

		it.skip('FEATURE_CHAT_OVERVIEW=False should hide overview option', () => {
			// Requires: FEATURE_CHAT_OVERVIEW=False in backend env
			cy.get('#chat-context-menu-button').click();
			cy.get('#chat-overview-button').should('not.exist');
		});
	});
});
```

#### 2. Frontend Unit Tests (NEW: `src/lib/utils/features.test.ts`)

**File**: `src/lib/utils/features.test.ts` (new file)

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { isFeatureEnabled, hasFeatureAccess } from './features';
import { config } from '$lib/stores';
import { get } from 'svelte/store';

// Mock the stores
vi.mock('$lib/stores', () => ({
	config: {
		subscribe: vi.fn()
	}
}));

vi.mock('svelte/store', () => ({
	get: vi.fn()
}));

describe('features utility', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	describe('isFeatureEnabled', () => {
		it('returns true when feature flag is true', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_chat_controls: true
				}
			});
			expect(isFeatureEnabled('chat_controls')).toBe(true);
		});

		it('returns false when feature flag is false', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_chat_controls: false
				}
			});
			expect(isFeatureEnabled('chat_controls')).toBe(false);
		});

		it('returns true when feature flag is undefined (default)', () => {
			vi.mocked(get).mockReturnValue({
				features: {}
			});
			expect(isFeatureEnabled('chat_controls')).toBe(true);
		});

		it('returns true when config is undefined', () => {
			vi.mocked(get).mockReturnValue(undefined);
			expect(isFeatureEnabled('chat_controls')).toBe(true);
		});
	});

	describe('hasFeatureAccess', () => {
		it('returns false when feature is globally disabled', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_playground: false
				}
			});
			const adminUser = { role: 'admin', permissions: {} };
			expect(hasFeatureAccess('playground', adminUser)).toBe(false);
		});

		it('returns true for admin when feature enabled and no permission path', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_playground: true
				}
			});
			const adminUser = { role: 'admin', permissions: {} };
			expect(hasFeatureAccess('playground', adminUser)).toBe(true);
		});

		it('returns true for admin when feature enabled regardless of permission', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_chat_controls: true
				}
			});
			const adminUser = { role: 'admin', permissions: { chat: { controls: false } } };
			expect(hasFeatureAccess('chat_controls', adminUser, 'chat.controls')).toBe(true);
		});

		it('returns false for user when permission is false', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_chat_controls: true
				}
			});
			const regularUser = { role: 'user', permissions: { chat: { controls: false } } };
			expect(hasFeatureAccess('chat_controls', regularUser, 'chat.controls')).toBe(false);
		});

		it('returns true for user when permission is true', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_chat_controls: true
				}
			});
			const regularUser = { role: 'user', permissions: { chat: { controls: true } } };
			expect(hasFeatureAccess('chat_controls', regularUser, 'chat.controls')).toBe(true);
		});
	});
});
```

#### 3. Backend Unit Tests (NEW: `backend/open_webui/test/util/test_features.py`)

**File**: `backend/open_webui/test/util/test_features.py` (new file)

```python
"""Tests for feature flag utilities."""

import pytest
from unittest.mock import patch
from fastapi import HTTPException

from open_webui.utils.features import is_feature_enabled, require_feature


class TestIsFeatureEnabled:
    """Tests for is_feature_enabled function."""

    def test_returns_true_for_enabled_feature(self):
        """Feature enabled in config should return True."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"playground": True}
        ):
            assert is_feature_enabled("playground") is True

    def test_returns_false_for_disabled_feature(self):
        """Feature disabled in config should return False."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"playground": False}
        ):
            assert is_feature_enabled("playground") is False

    def test_returns_true_for_unknown_feature(self):
        """Unknown features should default to True for backwards compatibility."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {},
            clear=True
        ):
            # Using type: ignore since we're testing edge case
            assert is_feature_enabled("unknown_feature") is True  # type: ignore


class TestRequireFeature:
    """Tests for require_feature dependency."""

    def test_does_not_raise_when_feature_enabled(self):
        """Should not raise when feature is enabled."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"playground": True}
        ):
            check = require_feature("playground")
            result = check()
            assert result is True

    def test_raises_403_when_feature_disabled(self):
        """Should raise HTTPException 403 when feature is disabled."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"playground": False}
        ):
            check = require_feature("playground")
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403
            assert "playground" in exc_info.value.detail
            assert "not available" in exc_info.value.detail

    def test_error_message_includes_feature_name(self):
        """Error message should include the feature name for clarity."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"chat_controls": False}
        ):
            check = require_feature("chat_controls")
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert "chat_controls" in exc_info.value.detail
```

### Success Criteria:

#### Automated Verification:
- [ ] Vitest tests pass: `npm run test:frontend`
- [ ] pytest tests pass: `pytest backend/open_webui/test/util/test_features.py -v`
- [ ] Cypress tests pass (default features): `npm run cy:open` or `npx cypress run`

#### Manual Verification:
- [ ] Review test coverage is adequate

---

## Phase 5: Documentation

### Overview
Update environment variable documentation.

### Changes Required:

#### 1. Update `.env.example` (if exists)

Add documentation for new feature flags:
```bash
# Feature Flags (SaaS Tier Control)
# These flags control feature visibility for ALL users including admins.
# Use for tier-based feature gating in multi-tenant deployments.
FEATURE_CHAT_CONTROLS=True    # Chat controls panel (system prompt, params, valves)
FEATURE_CAPTURE=True          # Screen capture option in input menu
FEATURE_ARTIFACTS=True        # Artifact rendering for HTML/SVG code blocks
FEATURE_PLAYGROUND=True       # Admin playground for testing LLM interactions
FEATURE_CHAT_OVERVIEW=True    # Visual flow overview of chat history
```

### Success Criteria:

#### Automated Verification:
- [ ] None

#### Manual Verification:
- [ ] Documentation is clear and accurate

---

## Testing Strategy

### Backend Unit Tests:
- `backend/open_webui/test/util/test_features.py` - Tests `is_feature_enabled()` and `require_feature()` dependency
- Tests enabled/disabled feature behavior
- Tests 403 response when feature disabled
- Tests error message includes feature name

### Frontend Unit Tests:
- `src/lib/utils/features.test.ts` - Tests `isFeatureEnabled()` and `hasFeatureAccess()` functions
- Tests default behavior (undefined = true)
- Tests explicit true/false values
- Tests admin bypass for permissions but NOT for feature flags

### E2E Tests:
- `cypress/e2e/feature-flags.cy.ts` - Tests UI visibility of features
- Tests default behavior (all features visible)
- Skipped tests document expected behavior when features disabled

### Manual Testing Steps:
1. Start backend with default env → verify all features visible
2. Set `FEATURE_PLAYGROUND=False` → restart → verify playground hidden for admin
3. Set `FEATURE_PLAYGROUND=False` → try direct navigation to `/playground` → verify redirect to `/`
4. Set `FEATURE_CHAT_CONTROLS=False` → restart → verify controls hidden for all users
5. Set `FEATURE_CAPTURE=False` → restart → verify capture button hidden
6. Set `FEATURE_ARTIFACTS=False` → restart → verify artifacts don't auto-open
7. Set `FEATURE_CHAT_OVERVIEW=False` → restart → verify overview option hidden

### Future Route Protection (example):
When adding features with dedicated backend routes, use the `require_feature` dependency:

```python
from open_webui.utils.features import require_feature

@router.get("/api/v1/some-feature")
async def some_feature_endpoint(
    user=Depends(get_current_user),
    _=Depends(require_feature("some_feature"))
):
    # Returns 403 if FEATURE_SOME_FEATURE=False
    ...
```

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add ~10 lines | Low |
| `backend/open_webui/main.py` | Add ~10 lines to imports + config response | Low |
| `backend/open_webui/utils/features.py` | **New file** (~60 lines) | None |
| `backend/open_webui/test/util/test_features.py` | **New file** (~60 lines) | None |
| `src/lib/stores/index.ts` | Add ~5 lines to Config type | Low |
| `src/lib/utils/features.ts` | **New file** (~50 lines) | None |
| `src/lib/utils/features.test.ts` | **New file** (~80 lines) | None |
| `src/lib/components/chat/Controls/Controls.svelte` | Add 1 import + 1 condition | Low |
| `src/lib/components/chat/MessageInput/InputMenu.svelte` | Add 1 import + wrap 1 item | Low |
| `src/lib/components/chat/Messages/ContentRenderer.svelte` | Add 1 import + 1 condition | Low |
| `src/lib/components/chat/ChatControls.svelte` | Add 1 import + 2 conditions | Low |
| `src/lib/components/layout/Navbar/Menu.svelte` | Add 1 import + 3 conditions | Low |
| `src/lib/components/layout/Sidebar/UserMenu.svelte` | Add 1 import + 1 condition | Low |
| `src/lib/components/playground/Chat.svelte` | Add 1 import + 1 condition | Low |
| `src/lib/components/playground/Completions.svelte` | Add 1 import + 1 condition | Low |
| `src/routes/(app)/playground/+layout.svelte` | Add feature flag check | Low |
| `cypress/e2e/feature-flags.cy.ts` | **New file** (~70 lines) | None |

**Total: ~17 files, ~120 LOC additions**

## References

- Research document: `thoughts/shared/research/2026-01-06-env-based-feature-control-saas.md`
- Existing feature flag pattern: `backend/open_webui/config.py:1581-1615`
- Config API endpoint: `backend/open_webui/main.py:1846-1969`
- Cypress test patterns: `cypress/e2e/chat.cy.ts`
