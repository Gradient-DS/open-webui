---
date: 2026-01-07T10:30:00+01:00
researcher: Claude
git_commit: 15128d02098947eb57681cd264e350920a9be88e
branch: main
repository: open-webui
topic: "Separating favicon from in-app logos to use Gradient favicon only in browser tab"
tags: [research, codebase, branding, favicon, logo, gradient]
status: complete
last_updated: 2026-01-07
last_updated_by: Claude
---

# Research: Favicon vs Logo Separation in Open WebUI

**Date**: 2026-01-07T10:30:00+01:00
**Researcher**: Claude
**Git Commit**: 15128d02098947eb57681cd264e350920a9be88e
**Branch**: main
**Repository**: open-webui

## Research Question

How can we use the Gradient logo only as the browser tab favicon while displaying the original Open WebUI logos everywhere else in the application?

## Summary

**Root Cause:** Open WebUI uses `favicon.png` for BOTH the browser tab favicon AND all in-app logo displays. There is no separation between these concepts in the codebase.

**Current State:**
- `backend/open_webui/static/favicon.png` = Gradient logo (this is what gets served)
- `static/static/logo.png` = Original Open WebUI "OI" logo (NOT used by components)
- All Svelte components reference `/static/favicon.png` for in-app logos

**Solution:** Modify Svelte components to use `logo.png` for in-app displays while keeping `favicon.png` (Gradient) for browser tab only.

## Detailed Findings

### Current Image File State

| File Path | Current Image | Purpose |
|-----------|---------------|---------|
| `static/static/favicon.png` | Gradient mesh | Source for favicon |
| `backend/open_webui/static/favicon.png` | Gradient mesh | **Served at /static/favicon.png** |
| `static/static/logo.png` | Open WebUI "OI" | **Available but NOT used by components** |
| `backend/open_webui/static/logo.png` | Open WebUI "OI" | Served at /static/logo.png |
| `static/static/splash.png` | Open WebUI "OI" | Splash screen (light mode) |
| `static/static/splash-dark.png` | Open WebUI "OI" | Splash screen (dark mode) |
| `static/static/apple-touch-icon.png` | Open WebUI "OI" | iOS home screen icon |
| `static/static/web-app-manifest-*.png` | Open WebUI "OI" | PWA install icons |

### Favicon Configuration (Keep As-Is)

These should continue using `favicon.png` (Gradient logo):

| File | Line | Code |
|------|------|------|
| `src/app.html` | 5 | `<link rel="icon" type="image/png" href="/static/favicon.png" />` |
| `src/app.html` | 8 | `<link rel="icon" type="image/png" href="/static/favicon-96x96.png" />` |
| `src/app.html` | 11 | `<link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />` |
| `src/app.html` | 14 | `<link rel="shortcut icon" href="/static/favicon.ico" />` |
| `src/routes/+layout.svelte` | 825 | `<link rel="icon" href="{WEBUI_BASE_URL}/static/favicon.png" />` |

### In-App Logo Usage (Needs Change)

These components currently use `favicon.png` but should use `logo.png`:

#### 1. Sidebar Component
**File:** `src/lib/components/layout/Sidebar.svelte`

**Line 674-678 (collapsed state):**
```svelte
<img src="{WEBUI_BASE_URL}/static/favicon.png"
     class="sidebar-new-chat-icon size-6 rounded-full group-hover:hidden" alt="" />
```

**Line 869-874 (expanded state):**
```svelte
<img crossorigin="anonymous" src="{WEBUI_BASE_URL}/static/favicon.png"
     class="sidebar-new-chat-icon size-6 rounded-full" alt="" />
```

#### 2. Authentication Page
**File:** `src/routes/auth/+page.svelte`

**Lines 133-154 (dark mode handler):**
```javascript
// Currently checks for favicon-dark.png, should check for logo-dark.png
darkImage.src = `${WEBUI_BASE_URL}/static/favicon-dark.png`;
```

**Lines 227-237 (center logo):**
```svelte
<img id="logo" crossorigin="anonymous" src="{WEBUI_BASE_URL}/static/favicon.png"
     class="size-24 rounded-full" alt="" />
```

**Lines 581-595 (corner logo):**
```svelte
<img id="logo" crossorigin="anonymous" src="{WEBUI_BASE_URL}/static/favicon.png"
     class="w-6 rounded-full" alt="" />
```

#### 3. Onboarding Component
**File:** `src/lib/components/OnBoarding.svelte`

**Lines 46-52:**
```svelte
<img id="logo" crossorigin="anonymous" src="{WEBUI_BASE_URL}/static/favicon.png"
     class="w-6 rounded-full" alt="logo" />
```

#### 4. Notification Toast
**File:** `src/lib/components/NotificationToast.svelte`

**Line 88:**
```svelte
<img src="{WEBUI_BASE_URL}/static/favicon.png" alt="favicon" class="size-6 rounded-full" />
```

#### 5. Browser Notifications
**File:** `src/routes/+layout.svelte`

**Lines 359-363:**
```javascript
new Notification(`${title} • Open WebUI`, {
    body: content,
    icon: `${WEBUI_BASE_URL}/static/favicon.png`  // Consider if this should be logo.png
});
```

#### 6. Chat Profile Image (Default)
**File:** `src/lib/components/chat/Messages/ProfileImage.svelte`

**Lines 5-21:**
```svelte
export let src = `${WEBUI_BASE_URL}/static/favicon.png`;
```

#### 7. Model Editor (Default Image)
**File:** `src/lib/components/workspace/Models/ModelEditor.svelte`

**Line 75 and 498:**
```javascript
profile_image_url: `${WEBUI_BASE_URL}/static/favicon.png`
```

#### 8. App Sidebar
**File:** `src/lib/components/app/AppSidebar.svelte`

**Lines 52-57:**
```svelte
<img src="{WEBUI_BASE_URL}/static/favicon.png"
     class="size-10 {selected === '' ? 'rounded-2xl' : 'rounded-full'}"
     alt="logo" draggable="false" />
```

Note: Lines 28-33 already correctly use `splash.png`:
```svelte
<img src="{WEBUI_BASE_URL}/static/splash.png" class="size-11 dark:invert p-0.5" />
```

## Code References

Files requiring changes (favicon.png → logo.png):
- `src/lib/components/layout/Sidebar.svelte:674,872` - Sidebar logo
- `src/routes/auth/+page.svelte:145,231,589` - Login page logo
- `src/lib/components/OnBoarding.svelte:48` - Onboarding logo
- `src/lib/components/NotificationToast.svelte:88` - Toast icon
- `src/lib/components/chat/Messages/ProfileImage.svelte:5` - Default AI profile
- `src/lib/components/workspace/Models/ModelEditor.svelte:75,498` - Model defaults
- `src/lib/components/app/AppSidebar.svelte:54` - App sidebar logo

Files to keep unchanged (browser favicon):
- `src/app.html:5-25` - HTML favicon link tags
- `src/routes/+layout.svelte:825` - Dynamic favicon link

## Recommended Implementation

### Step 1: Update Components to Use logo.png

Replace all in-app logo references from:
```svelte
src="{WEBUI_BASE_URL}/static/favicon.png"
```
To:
```svelte
src="{WEBUI_BASE_URL}/static/logo.png"
```

### Step 2: Create logo-dark.png (Optional)

For dark mode support, create `static/static/logo-dark.png` and update the dark mode handler in `auth/+page.svelte` and `OnBoarding.svelte`.

### Step 3: Consider Special Cases

- **Browser notifications** (`+layout.svelte:361`): Decide if notification icon should show Gradient or Open WebUI logo
- **Profile images** (`ProfileImage.svelte`): Default AI avatar - could be either logo
- **Model defaults** (`ModelEditor.svelte`): Custom model profile pictures

## Architecture Insights

Open WebUI's design uses a single file (`favicon.png`) for all branding, which makes customization difficult when you want different images for browser vs in-app. The existing `logo.png` file is underutilized - it exists but is never referenced by any component.

The `splash.png` and `splash-dark.png` files ARE used correctly for the splash screen, showing this separation is possible and precedented in the codebase.

## Open Questions

1. Should browser notifications show Gradient or Open WebUI logo?
2. Should AI chat profile pictures default to Gradient or Open WebUI?
3. Do we need a `logo-dark.png` variant for dark mode consistency?
