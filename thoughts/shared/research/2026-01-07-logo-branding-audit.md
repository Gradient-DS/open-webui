---
date: 2026-01-07T17:45:00+01:00
researcher: Claude
git_commit: 1673a953ab0aef7b569af628a9b3d7712301b389
branch: feat/web-fast
repository: open-webui
topic: "Logo and Branding Audit - Restoring Original Open WebUI Logos"
tags: [research, codebase, logos, branding, static-files]
status: complete
last_updated: 2026-01-07
last_updated_by: Claude
---

# Research: Logo and Branding Audit

**Date**: 2026-01-07T17:45:00+01:00
**Researcher**: Claude
**Git Commit**: 1673a953ab0aef7b569af628a9b3d7712301b389
**Branch**: feat/web-fast
**Repository**: open-webui

## Research Question

Audit logo usage to restore original Open WebUI logos everywhere except footers and About sections, where the Gradient logo should appear.

## Summary

**Current Issue**: The `backend/open_webui/static/favicon.png` has been replaced with the Gradient wave logo, when it should be the original Open WebUI "oi" logo.

**Svelte components are correctly configured** - gradient-logo.png is already used in the right places (footers and About sections). The fix is purely about restoring the correct static files.

## Current State

### Logo Files

| Logo | Image | Description |
|------|-------|-------------|
| **Original Open WebUI** | "oi" black text | Should be used for all favicon.png, logo.png, splash.png |
| **Gradient Logo** | Blue-green wave | Should ONLY be used in footers and About sections |

### Static File Locations

**Backend Static** (`backend/open_webui/static/`):
- `favicon.png` - **WRONG**: Currently shows Gradient logo, should be "oi"
- `favicon-dark.png`, `favicon-96x96.png` - Still showing Gradient logo
- `logo.png`, `splash.png`, `splash-dark.png` - Need verification
- `apple-touch-icon.png`, `web-app-manifest-*.png` - Need verification

**Frontend Static** (`static/static/`):
- `favicon.png` - Correct: Shows "oi" logo
- `gradient-logo.png` - Correct: Gradient wave logo for footers
- Other files match backend directory

### Build Process Impact

From `backend/open_webui/config.py:851-857`, at startup the backend copies files FROM frontend build TO backend static:
```python
frontend_favicon = FRONTEND_BUILD_DIR / "static" / "favicon.png"
if frontend_favicon.exists():
    shutil.copyfile(frontend_favicon, STATIC_DIR / "favicon.png")
```

This means:
- In **dev mode**: Backend uses files from `backend/open_webui/static/`
- In **production build**: Files are copied from `build/static/` to backend

## Detailed Findings

### Gradient Logo Usage (CORRECT - No Changes Needed)

| File | Line | Context |
|------|------|---------|
| `src/routes/auth/+page.svelte` | 574 | Login footer - "Powered by soev.ai" |
| `src/lib/components/chat/Chat.svelte` | 2592 | Chat footer |
| `src/lib/components/chat/Settings/About.svelte` | 186 | Settings About section |
| `src/lib/components/admin/Settings/General.svelte` | 296 | Admin Settings footer |

### Open WebUI Logo Usage (Needs "oi" favicon.png)

| File | Line | Usage |
|------|------|-------|
| `src/routes/auth/+page.svelte` | 232, 593 | Main login page logo |
| `src/lib/components/layout/Sidebar.svelte` | 675, 871 | Sidebar icons |
| `src/lib/components/app/AppSidebar.svelte` | 53 | App sidebar |
| `src/lib/components/OnBoarding.svelte` | 49 | Onboarding logo |
| `src/lib/components/NotificationToast.svelte` | 88 | Toast notifications |
| `src/lib/components/chat/Messages/ProfileImage.svelte` | 5, 11 | Default profile |
| `src/lib/components/workspace/Models/ModelEditor.svelte` | 75, 462, 498 | Model editor |
| `src/routes/+layout.svelte` | 361, 825 | Browser favicon |
| `src/app.html` | 5, 9, 16, 19, 93, 141 | HTML head favicons and splash |

## Required Fix

### 1. Restore Original Open WebUI Logos from Upstream

```bash
# Restore backend static files from upstream
git checkout upstream/main -- backend/open_webui/static/favicon.png
git checkout upstream/main -- backend/open_webui/static/favicon-dark.png
git checkout upstream/main -- backend/open_webui/static/favicon-96x96.png
git checkout upstream/main -- backend/open_webui/static/logo.png
git checkout upstream/main -- backend/open_webui/static/splash.png
git checkout upstream/main -- backend/open_webui/static/splash-dark.png
git checkout upstream/main -- backend/open_webui/static/apple-touch-icon.png
git checkout upstream/main -- backend/open_webui/static/web-app-manifest-192x192.png
git checkout upstream/main -- backend/open_webui/static/web-app-manifest-512x512.png

# Restore frontend static files from upstream (for consistency)
git checkout upstream/main -- static/static/favicon.png
git checkout upstream/main -- static/static/favicon-dark.png
git checkout upstream/main -- static/static/favicon-96x96.png
git checkout upstream/main -- static/static/logo.png
git checkout upstream/main -- static/static/splash.png
git checkout upstream/main -- static/static/splash-dark.png
git checkout upstream/main -- static/static/apple-touch-icon.png
git checkout upstream/main -- static/static/web-app-manifest-192x192.png
git checkout upstream/main -- static/static/web-app-manifest-512x512.png
```

### 2. Ensure gradient-logo.png Exists in Both Locations

The Gradient logo must exist at:
- `static/static/gradient-logo.png` (for frontend dev)
- `backend/open_webui/static/gradient-logo.png` (for backend serving)

Currently it only exists in `static/static/`. Copy it to backend:
```bash
cp static/static/gradient-logo.png backend/open_webui/static/gradient-logo.png
```

### 3. No Svelte Changes Required

All component references are already correct:
- `favicon.png` references stay as-is (will get original "oi" logo)
- `gradient-logo.png` references in footers/About sections stay as-is

## File Structure After Fix

```
backend/open_webui/static/
├── favicon.png           # Original Open WebUI "oi" logo
├── favicon-dark.png      # Dark mode "oi" logo
├── favicon-96x96.png     # 96x96 "oi" logo
├── logo.png              # Original Open WebUI logo
├── splash.png            # Original splash screen
├── splash-dark.png       # Dark mode splash
├── gradient-logo.png     # Gradient wave (NEW - for footer serving)
└── ...

static/static/
├── favicon.png           # Original Open WebUI "oi" logo
├── gradient-logo.png     # Gradient wave (existing)
└── ...
```

## Code References

- `backend/open_webui/config.py:851-857` - Favicon copy at startup
- `backend/open_webui/main.py:2391` - Static file mount point
- `src/routes/auth/+page.svelte:574` - Login footer gradient logo
- `src/lib/components/chat/Chat.svelte:2592` - Chat footer gradient logo
- `src/lib/components/chat/Settings/About.svelte:186` - About gradient logo
- `src/lib/components/admin/Settings/General.svelte:296` - Admin About gradient logo

## Architecture Insights

1. **Two static directories**: Backend serves from `backend/open_webui/static/`, SvelteKit uses `static/static/`
2. **Build process**: Production copies files from frontend build to backend
3. **Logo reference pattern**: Components use `{WEBUI_BASE_URL}/static/filename.png`
4. **Gradient branding**: Already correctly scoped to footers and About sections in Svelte code
