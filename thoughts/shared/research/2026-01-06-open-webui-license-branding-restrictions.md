---
date: 2026-01-06T12:00:00+01:00
researcher: Claude
git_commit: cde6c1f98802afcb06cfdfd6c116a44af73486fc
branch: feat/admin-config
repository: open-webui
topic: "Open WebUI License Branding Restrictions for 50+ Users"
tags: [research, license, branding, saas, open-webui]
status: complete
last_updated: 2026-01-06
last_updated_by: Claude
---

# Research: Open WebUI License Branding Restrictions for 50+ Users

**Date**: 2026-01-06T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: cde6c1f98802afcb06cfdfd6c116a44af73486fc
**Branch**: feat/admin-config
**Repository**: open-webui

## Research Question

What branding restrictions apply under the Open WebUI license for deployments with 50+ users? Specifically:
1. Can I remove/change the About section in settings and admin settings?
2. Can I place soev.ai branding in the About sections?
3. Can I add a footer with "powered by soev.ai"?
4. Are there designated places for custom branding?
5. Can I change the favicon and index.html title?
6. Can I customize the welcome message?

## Summary

**For 50+ users, the Open WebUI license (Clause 4) prohibits altering, removing, obscuring, or replacing any "Open WebUI" branding** unless you have:
- An enterprise license, OR
- Are an official contributor with merged code AND written permission

The license explicitly covers "the name, logo, or any visual, textual, or symbolic identifiers that distinguish the software and its interfaces."

### Quick Answers

| Question | Answer |
|----------|--------|
| 1. Remove/change About section? | **NO** - Protected branding |
| 2. Add soev.ai branding to About? | **YES** - Adding is allowed (not removing/replacing) |
| 3. Add "powered by soev.ai" footer? | **YES** - Additive branding is allowed |
| 4. Designated branding spots? | **YES** - See detailed findings below |
| 5. Change favicon/title? | **PARTIAL** - Built-in enforcement appends "(Open WebUI)" |
| 6. Custom welcome message? | **YES** - This is configurable and not protected branding |

## Detailed Findings

### License Analysis (LICENSE file)

**Clause 4 (Branding Restriction):**
> "...licensees are strictly prohibited from altering, removing, obscuring, or replacing any 'Open WebUI' branding, including but not limited to the name, logo, or any visual, textual, or symbolic identifiers..."

**Clause 5 (Exemptions):**
The branding restriction does NOT apply if:
- (i) ≤50 end users in any 30-day rolling period
- (ii) Official contributor with merged code AND written permission
- (iii) Enterprise license holder

### Built-in Branding Enforcement Mechanism

The backend enforces branding in `backend/open_webui/env.py:90-92`:

```python
WEBUI_NAME = os.environ.get("WEBUI_NAME", "Open WebUI")
if WEBUI_NAME != "Open WebUI":
    WEBUI_NAME += " (Open WebUI)"
```

**Impact**: If you set `WEBUI_NAME=soev.ai`, the displayed name becomes `soev.ai (Open WebUI)`. This is by design to maintain branding compliance.

### 1. About Section (Settings & Admin)

**Location**: `src/lib/components/chat/Settings/About.svelte`

Protected elements that CANNOT be removed/changed:
- Line 52: `{$WEBUI_NAME}` - Displays the app name
- Lines 60-73: Version display with GitHub link
- Lines 126-145: Discord/Twitter/GitHub badges (only shown without enterprise license)
- Lines 155-164: Copyright notice - "Copyright (c) {year} Open WebUI Inc."
- Lines 167-172: Creator attribution - "Created by Timothy J. Baek"

**Can add**: Additional branding elements alongside (not replacing) existing ones.

### 2. Adding soev.ai Branding

**Allowed locations for ADDITIVE branding:**

1. **License Metadata Section** (Line 115-123 in About.svelte)
   - If you have enterprise license, shows: `{organization_name} - {license_type} license`
   - Without enterprise: Shows Discord/GitHub badges instead

2. **Login Footer** (configurable via backend)
   - `$config?.metadata?.login_footer` - Renders markdown on login page
   - Location: `src/routes/auth/+page.svelte:559-564`

3. **Input Footer** (enterprise feature)
   - `$config?.license_metadata?.input_footer`
   - Location: `src/lib/components/chat/MessageInput.svelte:1876-1881`

### 3. Footer Implementation

**Current footers in the codebase:**

| Location | Type | Configurable? |
|----------|------|--------------|
| Login page footer | `$config?.metadata?.login_footer` | Yes - Admin configurable |
| Message input footer | `$config?.license_metadata?.input_footer` | Enterprise license only |
| About page copyright | Hardcoded | No - Protected branding |

**Adding a global footer**: You would need to modify layout files, but this is ALLOWED as long as Open WebUI branding is preserved. A "Powered by soev.ai" footer that doesn't obscure existing branding is compliant.

### 4. Designated Branding Spots

**Built-in customization points:**

1. **WEBUI_NAME** env variable
   - Set `WEBUI_NAME=soev.ai` → displays "soev.ai (Open WebUI)"
   - Used in page titles, meta tags, login page

2. **Login footer** via admin settings
   - Can add markdown/HTML content
   - Appears on login/auth page

3. **Banners** system
   - `src/lib/components/common/Banner.svelte`
   - Admin-configurable announcement banners
   - Good place for organizational branding

4. **PWA Manifest** - External URL option
   - `EXTERNAL_PWA_MANIFEST_URL` env variable
   - Can customize PWA appearance while keeping web app branded

### 5. Favicon and Title

**Favicon locations:**
- `static/static/favicon.png` (and .ico, .svg variants)
- `static/static/logo.png`
- `static/static/splash.png` / `splash-dark.png`
- `backend/open_webui/static/` (backend copies)

**Title configuration:**
- `src/app.html:106` - Static fallback: `<title>Open WebUI</title>`
- Dynamic title via `$WEBUI_NAME` store in svelte:head components

**Restriction**: Changing favicon/logo would constitute "replacing visual identifiers" under Clause 4. The automatic appending of "(Open WebUI)" to custom names is intentional enforcement.

### 6. Welcome Message

**Configurable - NOT protected branding!**

Welcome messages are NOT part of "Open WebUI" branding and can be freely customized:

1. **Default Prompt Suggestions**
   - Admin Settings → Interface → Default Prompt Suggestions
   - API: `POST /configs/suggestions`
   - Component: `src/lib/components/admin/Settings/Interface.svelte:470`

2. **Placeholder Text**
   - Default: "How can I help you today?" (translatable)
   - `src/lib/components/chat/ChatPlaceholder.svelte:120`
   - `src/lib/components/chat/Placeholder.svelte:218`

3. **Landing Page Mode**
   - User setting in Interface settings
   - Toggle between default view and chat view

4. **Banners**
   - Can add welcome banners via admin interface

## Code References

- `LICENSE` - Main license with branding clauses
- `backend/open_webui/env.py:90-92` - WEBUI_NAME enforcement
- `src/lib/components/chat/Settings/About.svelte` - About section with protected branding
- `src/routes/auth/+page.svelte:559-564` - Login footer (configurable)
- `src/lib/components/chat/MessageInput.svelte:1876-1881` - Input footer (enterprise)
- `src/lib/constants.ts:4` - `APP_NAME = 'Open WebUI'`
- `src/lib/stores/index.ts:10` - `WEBUI_NAME` store
- `src/app.html:106` - Static title

## Architecture Insights

The branding system is designed with multiple layers:
1. **Constants** - Hardcoded `APP_NAME`
2. **Environment** - `WEBUI_NAME` with automatic suffix
3. **Config** - Runtime configuration from backend
4. **License metadata** - Enterprise customization options

This architecture ensures Open WebUI attribution is maintained while allowing additive customization.

## Recommendations for soev.ai Deployment

### Compliant Customizations (ALLOWED)

1. **Set WEBUI_NAME environment variable**
   ```env
   WEBUI_NAME=soev.ai
   ```
   Result: "soev.ai (Open WebUI)" throughout the app

2. **Add login footer via admin settings**
   - Configure markdown/HTML with soev.ai branding
   - Example: "Powered by soev.ai | Enterprise AI Platform"

3. **Use banner system**
   - Add organizational announcements/branding

4. **Customize welcome prompts**
   - Change default prompt suggestions
   - Modify placeholder text

5. **Add custom footer component**
   - Create a footer that adds "Powered by soev.ai" WITHOUT removing existing attribution

### Non-Compliant Changes (NOT ALLOWED for 50+ users)

1. Removing "Open WebUI" from the About section
2. Replacing favicon/logo with soev.ai assets
3. Removing copyright notice
4. Removing GitHub/Discord badges
5. Changing `APP_NAME` constant to remove Open WebUI

### Enterprise License Path

For full rebranding (removing Open WebUI branding entirely), you would need to:
1. Purchase an enterprise license from Open WebUI Inc.
2. This unlocks `license_metadata` features including custom branding

## Open Questions

1. What is the process/cost for obtaining an enterprise license?
2. Does contributing code to open-webui qualify for branding exemption?
3. Are there any negotiable terms for startups/small businesses?

## Related Research

- `thoughts/shared/research/2026-01-06-env-based-feature-control-saas.md` - Feature control for SaaS
- `thoughts/shared/research/2026-01-06-admin-capability-restrictions-workspace.md` - Admin restrictions
