# Gradient Logo Footer Branding Implementation Plan

## Overview

Restore the original Open WebUI logo throughout the application and add a small Gradient logo with "Powered by soev.ai" text to specific footer locations.

## Current State Analysis

**Image Files:**
- `static/static/favicon.png` = Gradient mesh logo (currently displayed everywhere)
- `static/static/logo.png` = Original Open WebUI "OI" logo (exists but unused)
- `backend/open_webui/static/favicon.png` = Gradient mesh logo (served at `/static/favicon.png`)

**Footer Locations:**
- Login page: `src/routes/auth/+page.svelte:566-575` - "Powered by soev.ai" text link
- Chat page: `src/lib/components/chat/Chat.svelte:2584-2595` - "Powered by soev.ai" + disclaimer
- Settings About: `src/lib/components/chat/Settings/About.svelte:170-177` - After creator credit
- Admin General: `src/lib/components/admin/Settings/General.svelte:235-287` - After license section

## Desired End State

1. Open WebUI "OI" logo displayed everywhere in the app (sidebar, auth page, notifications, etc.)
2. Small Gradient logo (6x6) with "Powered by soev.ai" text in 4 footer locations
3. Browser favicon remains configurable (will show OI logo after this change)

### Verification:
- All in-app logos show Open WebUI "OI" design
- 4 footer locations show Gradient logo + text
- No broken image references

## What We're NOT Doing

- Changing the favicon HTML link tags (they will naturally use the restored OI logo)
- Modifying splash screens (already use correct `splash.png`)
- Creating dark mode variants of the Gradient logo
- Changing any other branding elements

## Implementation Approach

1. Preserve current Gradient logo as a new file before overwriting
2. Restore Open WebUI logo to favicon.png in both locations
3. Add Gradient logo + text to each footer location

---

## Phase 1: Restore Open WebUI Logo Assets

### Overview
Save the current Gradient logo to a new file, then copy the original Open WebUI logo over favicon.png.

### Changes Required:

#### 1. Save Gradient Logo
**Action**: Copy current `favicon.png` to `gradient-logo.png` before overwriting

```bash
# Frontend static
cp static/static/favicon.png static/static/gradient-logo.png

# Backend static
cp backend/open_webui/static/favicon.png backend/open_webui/static/gradient-logo.png
```

#### 2. Restore Open WebUI Logo to favicon.png
**Action**: Copy `logo.png` over `favicon.png`

```bash
# Frontend static
cp static/static/logo.png static/static/favicon.png

# Backend static
cp backend/open_webui/static/logo.png backend/open_webui/static/favicon.png
```

### Success Criteria:

#### Automated Verification:
- [x] File exists: `static/static/gradient-logo.png`
- [x] File exists: `backend/open_webui/static/gradient-logo.png`
- [x] `static/static/favicon.png` matches `static/static/logo.png`
- [x] `backend/open_webui/static/favicon.png` matches `backend/open_webui/static/logo.png`

#### Manual Verification:
- [ ] Open the app - all logos show Open WebUI "OI" design
- [ ] Browser tab favicon shows Open WebUI "OI" design
- [ ] Sidebar logo is Open WebUI "OI"
- [ ] Auth page logo is Open WebUI "OI"

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the logo restoration was successful before proceeding to the next phase.

---

## Phase 2: Add Gradient Logo to Login Page Footer

### Overview
Update the login page footer to show the Gradient logo alongside "Powered by soev.ai" text.

### Changes Required:

#### 1. Login Page Footer
**File**: `src/routes/auth/+page.svelte`
**Lines**: 566-575

**Current code:**
```svelte
<!-- soev.ai footer -->
<div class="max-w-3xl mx-auto">
    <a
        href="https://soev.ai"
        target="_blank"
        class="mt-2 text-[0.7rem] text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
    >
        {$i18n.t('Powered by soev.ai')}
    </a>
</div>
```

**New code:**
```svelte
<!-- soev.ai footer -->
<div class="max-w-3xl mx-auto">
    <a
        href="https://soev.ai"
        target="_blank"
        class="mt-2 flex items-center justify-center gap-1.5 text-[0.7rem] text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
    >
        <img
            src="{WEBUI_BASE_URL}/static/gradient-logo.png"
            alt="Gradient"
            class="size-6"
        />
        {$i18n.t('Powered by soev.ai')}
    </a>
</div>
```

### Success Criteria:

#### Automated Verification:
- [ ] No TypeScript errors: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

#### Manual Verification:
- [ ] Login page shows Gradient logo (6x6) next to "Powered by soev.ai"
- [ ] Logo and text are centered and aligned
- [ ] Clicking links to https://soev.ai in new tab
- [ ] Hover state works on entire link area

**Implementation Note**: After completing this phase, pause for manual verification before proceeding.

---

## Phase 3: Add Gradient Logo to Chat Page Footer

### Overview
Update the chat page footer to show the Gradient logo alongside "Powered by soev.ai" text.

### Changes Required:

#### 1. Chat Page Footer
**File**: `src/lib/components/chat/Chat.svelte`
**Lines**: 2584-2595

**Current code:**
```svelte
<!-- Footer -->
<div class="text-center text-xs text-gray-400 dark:text-gray-500 py-1">
    <a
        href="https://soev.ai"
        target="_blank"
        class="hover:text-gray-600 dark:hover:text-gray-400"
    >
        {$i18n.t('Powered by soev.ai')}
    </a>
    <span class="mx-1">·</span>
    <span>{$i18n.t('LLMs can make mistakes. Verify important information.')}</span>
</div>
```

**New code:**
```svelte
<!-- Footer -->
<div class="text-center text-xs text-gray-400 dark:text-gray-500 py-1">
    <a
        href="https://soev.ai"
        target="_blank"
        class="inline-flex items-center gap-1 hover:text-gray-600 dark:hover:text-gray-400"
    >
        <img
            src="{WEBUI_BASE_URL}/static/gradient-logo.png"
            alt="Gradient"
            class="size-6"
        />
        {$i18n.t('Powered by soev.ai')}
    </a>
    <span class="mx-1">·</span>
    <span>{$i18n.t('LLMs can make mistakes. Verify important information.')}</span>
</div>
```

### Success Criteria:

#### Automated Verification:
- [ ] No TypeScript errors: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

#### Manual Verification:
- [ ] Chat page footer shows Gradient logo (6x6) next to "Powered by soev.ai"
- [ ] Logo aligns properly with text baseline
- [ ] Disclaimer text still visible after the dot separator
- [ ] Clicking logo+text links to https://soev.ai

**Implementation Note**: After completing this phase, pause for manual verification before proceeding.

---

## Phase 4: Add Gradient Logo to Settings About Page

### Overview
Add Gradient logo with "Powered by soev.ai" to the Settings About tab, after the creator credit.

### Changes Required:

#### 1. Settings About Page
**File**: `src/lib/components/chat/Settings/About.svelte`
**Lines**: After line 177 (after "Created by Timothy J. Baek" section)

**Add after line 177:**
```svelte
	<div class="mt-4 pt-3 border-t border-gray-100/30 dark:border-gray-850/30">
		<a
			href="https://soev.ai"
			target="_blank"
			class="inline-flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400"
		>
			<img
				src="{WEBUI_BASE_URL}/static/gradient-logo.png"
				alt="Gradient"
				class="size-6"
			/>
			{$i18n.t('Powered by soev.ai')}
		</a>
	</div>
```

**Also add import at top of script section (if not already present):**
Check if `WEBUI_BASE_URL` is imported. If not, add:
```typescript
import { WEBUI_BASE_URL } from '$lib/constants';
```

### Success Criteria:

#### Automated Verification:
- [ ] No TypeScript errors: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

#### Manual Verification:
- [ ] Settings > About tab shows Gradient logo at bottom
- [ ] Logo appears after "Created by Timothy J. Baek" with separator line
- [ ] Clicking links to https://soev.ai

**Implementation Note**: After completing this phase, pause for manual verification before proceeding.

---

## Phase 5: Add Gradient Logo to Admin General Settings

### Overview
Add Gradient logo with "Powered by soev.ai" to the Admin General Settings page, after the License section.

### Changes Required:

#### 1. Admin General Settings
**File**: `src/lib/components/admin/Settings/General.svelte`
**Lines**: After line 287 (after the License section closing `</div>`)

**Add after line 287:**
```svelte
				<div class="mb-2.5">
					<a
						href="https://soev.ai"
						target="_blank"
						class="inline-flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400"
					>
						<img
							src="{WEBUI_BASE_URL}/static/gradient-logo.png"
							alt="Gradient"
							class="size-6"
						/>
						{$i18n.t('Powered by soev.ai')}
					</a>
				</div>
```

**Also verify import exists:**
Check if `WEBUI_BASE_URL` is imported from `$lib/constants`. Add if missing.

### Success Criteria:

#### Automated Verification:
- [ ] No TypeScript errors: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

#### Manual Verification:
- [ ] Admin Settings > General shows Gradient logo after License section
- [ ] Logo is properly sized (6x6) and aligned with text
- [ ] Clicking links to https://soev.ai

**Implementation Note**: After completing this phase, pause for final manual verification.

---

## Testing Strategy

### Unit Tests:
- No new unit tests required (visual/branding changes only)

### Manual Testing Steps:
1. Clear browser cache and hard refresh
2. Check login page - OI logo in corner, Gradient logo in footer
3. Log in and check sidebar - OI logo displayed
4. Check chat page footer - Gradient logo + text + disclaimer
5. Open Settings > About - Gradient logo at bottom
6. Open Admin > Settings > General - Gradient logo after License section
7. Check browser tab - shows OI favicon
8. Test in incognito mode to verify no caching issues

## Files Changed Summary

| File | Change |
|------|--------|
| `static/static/gradient-logo.png` | NEW - Gradient logo asset |
| `backend/open_webui/static/gradient-logo.png` | NEW - Gradient logo asset |
| `static/static/favicon.png` | REPLACED - Now Open WebUI logo |
| `backend/open_webui/static/favicon.png` | REPLACED - Now Open WebUI logo |
| `src/routes/auth/+page.svelte` | MODIFIED - Add logo to footer |
| `src/lib/components/chat/Chat.svelte` | MODIFIED - Add logo to footer |
| `src/lib/components/chat/Settings/About.svelte` | MODIFIED - Add logo section |
| `src/lib/components/admin/Settings/General.svelte` | MODIFIED - Add logo section |

## References

- Research document: `thoughts/shared/research/2026-01-07-favicon-vs-logo-separation.md`
- Current Gradient logo: `static/static/favicon.png` (before restoration)
- Open WebUI logo: `static/static/logo.png`
