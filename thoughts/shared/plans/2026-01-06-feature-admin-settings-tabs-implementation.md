# Feature Admin Settings Tabs Implementation Plan

## Overview

Implement two feature flags to control admin panel settings visibility:
1. `FEATURE_ADMIN_SETTINGS` - Boolean flag to enable/disable the entire admin settings section
2. `FEATURE_ADMIN_SETTINGS_TABS` - Comma-separated list of allowed settings tabs

This allows SaaS deployments to customize which admin settings categories are accessible.

## Current State Analysis

### Admin Settings Structure

The admin settings navigation is implemented in `src/lib/components/admin/Settings.svelte`:
- 13 tabs defined (lines 36-52): `general`, `connections`, `models`, `evaluations`, `tools`, `documents`, `web`, `code-execution`, `interface`, `audio`, `images`, `pipelines`, `db`
- Audio tab already has conditional rendering based on `isFeatureEnabled('voice')` (line 321)
- Routes use pattern `/admin/settings/{tab}`

### Key Files:
- `backend/open_webui/config.py:1600-1615` - Feature flag definitions
- `backend/open_webui/main.py:1933-1946` - API exposure in `get_app_config()`
- `src/lib/utils/features.ts` - Frontend feature checking utility
- `src/lib/stores/index.ts:279-292` - Config type definitions
- `src/routes/(app)/admin/settings/[tab]/+page.svelte` - Tab route with existing audio guard

## Desired End State

After implementation:

1. `FEATURE_ADMIN_SETTINGS=False`:
   - Settings link hidden from admin navigation
   - Direct navigation to `/admin/settings/*` redirects to `/admin`

2. `FEATURE_ADMIN_SETTINGS_TABS=general,connections,interface`:
   - Only General, Connections, and Interface tabs visible
   - Other tabs hidden from navigation
   - Direct URL access to hidden tabs redirects to `/admin/settings` (first available tab)

3. `FEATURE_ADMIN_SETTINGS_TABS=` (empty/unset):
   - All tabs visible (default behavior)

4. Audio tab visibility requires BOTH:
   - `FEATURE_VOICE=True`
   - `audio` in `FEATURE_ADMIN_SETTINGS_TABS` (or tabs list empty)

### Verification:
- Default config: all tabs visible
- With `FEATURE_ADMIN_SETTINGS=False`: settings link hidden, routes redirect
- With `FEATURE_ADMIN_SETTINGS_TABS=general,connections`: only those tabs visible
- Direct URL to hidden tab redirects to first available tab

## What We're NOT Doing

- NOT adding backend route protection (admin settings are already admin-only)
- NOT changing any individual settings panel functionality
- NOT affecting any other admin sections (Users, Evaluations, Functions)

---

## Phase 1: Backend Feature Flags

### Overview
Add the two new environment variables and expose them via the config API.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: After line 1615 (after FEATURE_TOOLS)

```python
FEATURE_ADMIN_SETTINGS = os.environ.get("FEATURE_ADMIN_SETTINGS", "True").lower() == "true"
FEATURE_ADMIN_SETTINGS_TABS = [
    tab.strip()
    for tab in os.environ.get("FEATURE_ADMIN_SETTINGS_TABS", "").split(",")
    if tab.strip()
]
```

#### 2. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`
**Location**: Add import around line 421 with other FEATURE_* imports

```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_ADMIN_SETTINGS,
    FEATURE_ADMIN_SETTINGS_TABS,
)
```

**Location**: Add to features dict after line 1946 (after `feature_tools`)

```python
"feature_admin_settings": FEATURE_ADMIN_SETTINGS,
"feature_admin_settings_tabs": FEATURE_ADMIN_SETTINGS_TABS,
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] API returns new feature flags: `curl http://localhost:8080/api/config | jq '.features.feature_admin_settings, .features.feature_admin_settings_tabs'`
- [ ] Default values: `feature_admin_settings: true`, `feature_admin_settings_tabs: []`

#### Manual Verification:
- [ ] Set `FEATURE_ADMIN_SETTINGS=False` and verify API returns `false`
- [ ] Set `FEATURE_ADMIN_SETTINGS_TABS=general,connections` and verify API returns `["general", "connections"]`

---

## Phase 2: Frontend Types & Utilities

### Overview
Add TypeScript types and helper functions for checking admin settings tab visibility.

### Changes Required:

#### 1. Config Store Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: After line 292 (after `feature_tools?: boolean;`)

```typescript
feature_admin_settings?: boolean;
feature_admin_settings_tabs?: string[];
```

#### 2. Feature Utility (`src/lib/utils/features.ts`)

**File**: `src/lib/utils/features.ts`
**Location**: After the existing `hasFeatureAccess` function (after line 75)

```typescript
/**
 * All valid admin settings tab IDs
 */
export const ADMIN_SETTINGS_TABS = [
	'general',
	'connections',
	'models',
	'evaluations',
	'tools',
	'documents',
	'web',
	'code-execution',
	'interface',
	'audio',
	'images',
	'pipelines',
	'db'
] as const;

export type AdminSettingsTab = (typeof ADMIN_SETTINGS_TABS)[number];

/**
 * Check if admin settings section is enabled globally.
 */
export function isAdminSettingsEnabled(): boolean {
	const $config = get(config);
	return $config?.features?.feature_admin_settings ?? true;
}

/**
 * Check if a specific admin settings tab is enabled.
 * For the audio tab, also requires FEATURE_VOICE to be true.
 *
 * @param tab - The tab ID to check
 * @returns true if the tab should be visible
 */
export function isAdminSettingsTabEnabled(tab: AdminSettingsTab): boolean {
	// First check if admin settings is enabled at all
	if (!isAdminSettingsEnabled()) {
		return false;
	}

	const $config = get(config);
	const allowedTabs = $config?.features?.feature_admin_settings_tabs ?? [];

	// If no tabs specified, all tabs are allowed
	const tabAllowed = allowedTabs.length === 0 || allowedTabs.includes(tab);

	// Audio tab has additional requirement: FEATURE_VOICE must be true
	if (tab === 'audio') {
		return tabAllowed && isFeatureEnabled('voice');
	}

	return tabAllowed;
}

/**
 * Get the first available admin settings tab.
 * Used for redirecting when accessing a disabled tab.
 *
 * @returns The first enabled tab ID, or null if none available
 */
export function getFirstAvailableAdminSettingsTab(): AdminSettingsTab | null {
	for (const tab of ADMIN_SETTINGS_TABS) {
		if (isAdminSettingsTabEnabled(tab)) {
			return tab;
		}
	}
	return null;
}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

---

## Phase 3: Settings Component Updates

### Overview
Update the Settings.svelte component to conditionally render tabs based on feature flags.

### Changes Required:

#### 1. Settings Component (`src/lib/components/admin/Settings.svelte`)

**File**: `src/lib/components/admin/Settings.svelte`

**Update imports (line 8)**:

Change:
```svelte
import { isFeatureEnabled } from '$lib/utils/features';
```

To:
```svelte
import { isFeatureEnabled, isAdminSettingsTabEnabled, getFirstAvailableAdminSettingsTab, ADMIN_SETTINGS_TABS, type AdminSettingsTab } from '$lib/utils/features';
```

**Update tab validation reactive block (lines 33-53)**:

Replace the existing reactive block:
```javascript
$: {
    const pathParts = $page.url.pathname.split('/');
    const tabFromPath = pathParts[pathParts.length - 1];
    selectedTab = [
        'general',
        'connections',
        // ... etc
    ].includes(tabFromPath)
        ? tabFromPath
        : 'general';
}
```

With:
```javascript
$: {
    const pathParts = $page.url.pathname.split('/');
    const tabFromPath = pathParts[pathParts.length - 1] as AdminSettingsTab;

    // Check if the tab is valid and enabled
    if (ADMIN_SETTINGS_TABS.includes(tabFromPath) && isAdminSettingsTabEnabled(tabFromPath)) {
        selectedTab = tabFromPath;
    } else {
        // Redirect to first available tab
        const firstTab = getFirstAvailableAdminSettingsTab();
        if (firstTab && firstTab !== tabFromPath) {
            goto(`/admin/settings/${firstTab}`);
        }
        selectedTab = firstTab ?? 'general';
    }
}
```

**Wrap each tab button with feature check**:

For each button (General at line 89, Connections at line 116, etc.), wrap with:

```svelte
{#if isAdminSettingsTabEnabled('general')}
    <button id="general" ...>
        ...
    </button>
{/if}
```

**Tab buttons to wrap** (remove the existing `{#if isFeatureEnabled('voice')}` wrapper from audio):

| Tab | Lines | Wrap with |
|-----|-------|-----------|
| general | 89-114 | `{#if isAdminSettingsTabEnabled('general')}` |
| connections | 116-139 | `{#if isAdminSettingsTabEnabled('connections')}` |
| models | 141-166 | `{#if isAdminSettingsTabEnabled('models')}` |
| evaluations | 168-182 | `{#if isAdminSettingsTabEnabled('evaluations')}` |
| tools | 184-209 | `{#if isAdminSettingsTabEnabled('tools')}` |
| documents | 211-240 | `{#if isAdminSettingsTabEnabled('documents')}` |
| web | 242-265 | `{#if isAdminSettingsTabEnabled('web')}` |
| code-execution | 267-292 | `{#if isAdminSettingsTabEnabled('code-execution')}` |
| interface | 294-319 | `{#if isAdminSettingsTabEnabled('interface')}` |
| audio | 321-349 | `{#if isAdminSettingsTabEnabled('audio')}` (replace existing voice check) |
| images | 351-376 | `{#if isAdminSettingsTabEnabled('images')}` |
| pipelines | 378-407 | `{#if isAdminSettingsTabEnabled('pipelines')}` |
| db | 409-436 | `{#if isAdminSettingsTabEnabled('db')}` |

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With defaults: all tabs visible
- [ ] With `FEATURE_ADMIN_SETTINGS_TABS=general,connections`: only those tabs visible

---

## Phase 4: Route Guards

### Overview
Update route pages to redirect when accessing disabled tabs or when admin settings is disabled entirely.

### Changes Required:

#### 1. Tab Route Guard (`src/routes/(app)/admin/settings/[tab]/+page.svelte`)

**File**: `src/routes/(app)/admin/settings/[tab]/+page.svelte`

Replace entire file:
```svelte
<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { isAdminSettingsEnabled, isAdminSettingsTabEnabled, getFirstAvailableAdminSettingsTab, type AdminSettingsTab } from '$lib/utils/features';
	import Settings from '$lib/components/admin/Settings.svelte';

	onMount(() => {
		// Check if admin settings is disabled entirely
		if (!isAdminSettingsEnabled()) {
			goto('/admin');
			return;
		}

		// Check if the specific tab is disabled
		const tab = $page.params.tab as AdminSettingsTab;
		if (!isAdminSettingsTabEnabled(tab)) {
			const firstTab = getFirstAvailableAdminSettingsTab();
			if (firstTab) {
				goto(`/admin/settings/${firstTab}`);
			} else {
				goto('/admin');
			}
		}
	});
</script>

<Settings />
```

#### 2. Base Settings Route (`src/routes/(app)/admin/settings/+page.svelte`)

**File**: `src/routes/(app)/admin/settings/+page.svelte`

Replace entire file:
```svelte
<script>
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { isAdminSettingsEnabled, getFirstAvailableAdminSettingsTab } from '$lib/utils/features';
	import Settings from '$lib/components/admin/Settings.svelte';

	onMount(() => {
		// Check if admin settings is disabled entirely
		if (!isAdminSettingsEnabled()) {
			goto('/admin');
			return;
		}

		// Redirect to first available tab
		const firstTab = getFirstAvailableAdminSettingsTab();
		if (firstTab) {
			goto(`/admin/settings/${firstTab}`);
		} else {
			goto('/admin');
		}
	});
</script>

<Settings />
```

#### 3. Admin Layout - Hide Settings Link (`src/routes/(app)/admin/+layout.svelte`)

**File**: `src/routes/(app)/admin/+layout.svelte`

Add import at top of script:
```typescript
import { isAdminSettingsEnabled } from '$lib/utils/features';
```

**Location**: Wrap the Settings link (lines 90-95) with feature check:

```svelte
{#if isAdminSettingsEnabled()}
    <a
        class="..."
        href="/admin/settings"
    >
        ...Settings...
    </a>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] Direct navigation to `/admin/settings/pipelines` with `FEATURE_ADMIN_SETTINGS_TABS=general` redirects to `/admin/settings/general`
- [ ] Navigation to `/admin/settings` with `FEATURE_ADMIN_SETTINGS=False` redirects to `/admin`
- [ ] Settings link hidden in admin nav when `FEATURE_ADMIN_SETTINGS=False`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 5: Tests

### Overview
Add unit tests for the new feature utility functions.

### Changes Required:

#### 1. Frontend Unit Tests (`src/lib/utils/features.test.ts`)

**File**: `src/lib/utils/features.test.ts`
**Location**: Add after existing tests

```typescript
describe('admin settings features', () => {
	describe('isAdminSettingsEnabled', () => {
		it('returns true when feature_admin_settings is true', () => {
			vi.mocked(get).mockReturnValue({
				features: { feature_admin_settings: true }
			});
			expect(isAdminSettingsEnabled()).toBe(true);
		});

		it('returns false when feature_admin_settings is false', () => {
			vi.mocked(get).mockReturnValue({
				features: { feature_admin_settings: false }
			});
			expect(isAdminSettingsEnabled()).toBe(false);
		});

		it('returns true when feature_admin_settings is undefined (default)', () => {
			vi.mocked(get).mockReturnValue({
				features: {}
			});
			expect(isAdminSettingsEnabled()).toBe(true);
		});
	});

	describe('isAdminSettingsTabEnabled', () => {
		it('returns true for any tab when tabs list is empty', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: []
				}
			});
			expect(isAdminSettingsTabEnabled('general')).toBe(true);
			expect(isAdminSettingsTabEnabled('models')).toBe(true);
			expect(isAdminSettingsTabEnabled('pipelines')).toBe(true);
		});

		it('returns true only for tabs in the allowed list', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['general', 'connections']
				}
			});
			expect(isAdminSettingsTabEnabled('general')).toBe(true);
			expect(isAdminSettingsTabEnabled('connections')).toBe(true);
			expect(isAdminSettingsTabEnabled('models')).toBe(false);
			expect(isAdminSettingsTabEnabled('pipelines')).toBe(false);
		});

		it('returns false for all tabs when admin settings is disabled', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: false,
					feature_admin_settings_tabs: []
				}
			});
			expect(isAdminSettingsTabEnabled('general')).toBe(false);
			expect(isAdminSettingsTabEnabled('models')).toBe(false);
		});

		it('audio tab requires both feature_voice and being in tabs list', () => {
			// Voice enabled, audio in list
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['audio'],
					feature_voice: true
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(true);

			// Voice disabled, audio in list
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['audio'],
					feature_voice: false
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(false);

			// Voice enabled, audio not in list
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['general'],
					feature_voice: true
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(false);

			// Voice enabled, empty list (all tabs allowed)
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: [],
					feature_voice: true
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(true);
		});
	});

	describe('getFirstAvailableAdminSettingsTab', () => {
		it('returns general when all tabs enabled', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: [],
					feature_voice: true
				}
			});
			expect(getFirstAvailableAdminSettingsTab()).toBe('general');
		});

		it('returns first tab from allowed list', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['models', 'connections'],
					feature_voice: true
				}
			});
			// Should return 'connections' because it comes before 'models' in ADMIN_SETTINGS_TABS order
			expect(getFirstAvailableAdminSettingsTab()).toBe('connections');
		});

		it('returns null when admin settings disabled', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: false
				}
			});
			expect(getFirstAvailableAdminSettingsTab()).toBe(null);
		});
	});
});
```

### Success Criteria:

#### Automated Verification:
- [ ] Frontend tests pass: `npm run test:frontend`

---

## Phase 6: Documentation

### Overview
Update environment variable documentation.

### Changes Required:

#### 1. Update `.env.example` (if exists) or add to CLAUDE.md

```bash
# Admin Settings Feature Flags
FEATURE_ADMIN_SETTINGS=True           # Enable/disable entire admin settings section
FEATURE_ADMIN_SETTINGS_TABS=          # Comma-separated list of allowed tabs (empty = all)
                                      # Valid tabs: general,connections,models,evaluations,
                                      # tools,documents,web,code-execution,interface,
                                      # audio,images,pipelines,db
```

### Success Criteria:

#### Manual Verification:
- [ ] Documentation is clear and accurate

---

## Testing Strategy

### Manual Testing Steps:

1. **Default state (all flags unset)**:
   - All 13 tabs visible (12 if FEATURE_VOICE=False)
   - Settings link visible in admin nav

2. **FEATURE_ADMIN_SETTINGS=False**:
   - Settings link hidden from admin nav
   - Direct navigation to `/admin/settings` redirects to `/admin`
   - Direct navigation to `/admin/settings/general` redirects to `/admin`

3. **FEATURE_ADMIN_SETTINGS_TABS=general,connections,interface**:
   - Only General, Connections, Interface tabs visible
   - Direct navigation to `/admin/settings/models` redirects to `/admin/settings/general`

4. **FEATURE_ADMIN_SETTINGS_TABS=audio** with **FEATURE_VOICE=False**:
   - No tabs visible (audio requires voice)
   - Redirects to `/admin`

5. **FEATURE_ADMIN_SETTINGS_TABS=audio,general** with **FEATURE_VOICE=True**:
   - Both General and Audio tabs visible

---

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add 5 lines | Low |
| `backend/open_webui/main.py` | Add 3 lines import + 2 lines exposure | Low |
| `src/lib/stores/index.ts` | Add 2 lines to Config type | Low |
| `src/lib/utils/features.ts` | Add ~50 lines (new functions) | Low |
| `src/lib/utils/features.test.ts` | Add ~100 lines tests | None |
| `src/lib/components/admin/Settings.svelte` | Modify imports + wrap 13 buttons | Low |
| `src/routes/(app)/admin/settings/+page.svelte` | Rewrite (~15 lines) | Low |
| `src/routes/(app)/admin/settings/[tab]/+page.svelte` | Rewrite (~25 lines) | Low |
| `src/routes/(app)/admin/+layout.svelte` | Add 1 import + wrap 1 link | Low |

**Total: ~9 files, ~200 LOC additions**

---

## References

- Feature flag wrapper plan: `thoughts/shared/plans/2026-01-06-feature-flag-wrapper-implementation.md`
- Workspace flags plan: `thoughts/shared/plans/2026-01-06-feature-workspace-flags-implementation.md`
- Existing feature utility: `src/lib/utils/features.ts`
- Admin settings component: `src/lib/components/admin/Settings.svelte`
