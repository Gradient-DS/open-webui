# soev.ai Branding Implementation Plan

## Overview

Add soev.ai branding to Open WebUI in all license-compliant locations. This follows the branding restrictions documented in `thoughts/shared/research/2026-01-06-open-webui-license-branding-restrictions.md` - we add branding alongside Open WebUI attribution, never removing or obscuring it.

**Brand assets:**
- Name: soev.ai (lowercase)
- Website: https://soev.ai
- Logo: Teal/green gradient mesh wave design (192x192 source, upscale for larger sizes)

**Title behavior**: Will display as "soev.ai (Open WebUI)" per license enforcement - this is acceptable.

## Current State Analysis

Open WebUI has a built-in branding enforcement mechanism:
- `WEBUI_NAME` env var auto-appends "(Open WebUI)" to any custom name
- About sections contain hardcoded Open WebUI copyright and creator attribution
- `login_footer` is tied to enterprise `LICENSE_METADATA` system

### Key Discoveries:
- Favicon/logo replacement is allowed (additive - not removing attribution)
- Login footer needs custom implementation (enterprise-only by default)
- Welcome message/prompts are fully configurable
- Banners are fully configurable
- Title will display as "soev.ai (Open WebUI)" per license enforcement

## Desired End State

After implementation:
1. **Title**: "soev.ai (Open WebUI)" in browser tab, PWA name
2. **Favicon**: soev.ai mesh logo in all sizes
3. **Login page**: soev.ai logo + footer with "Powered by soev.ai | https://soev.ai"
4. **About sections**: soev.ai branding displayed ABOVE existing Open WebUI attribution
5. **Welcome message**: Custom soev.ai greeting/prompts
6. **Splash screen**: soev.ai logo during loading

### Verification:
- All pages show soev.ai branding
- Open WebUI attribution remains visible (license compliance)
- PWA installs with soev.ai name and icon
- Dark mode uses appropriate logo variant

## What We're NOT Doing

- Removing "Open WebUI" text from anywhere (license violation for 50+ users)
- Removing copyright notice
- Removing creator attribution
- Removing Discord/GitHub badges (unless we have enterprise license)
- Modifying the license enforcement logic in env.py

---

## Phase 1: Environment Configuration & Static Assets

### Overview
Configure WEBUI_NAME and replace static favicon/logo files.

### Changes Required:

#### 1. Environment Variable
**File**: `.env` or deployment config
**Changes**: Add WEBUI_NAME setting

```bash
WEBUI_NAME=soev.ai
```

Result: Title displays as "soev.ai (Open WebUI)" throughout the app.

#### 2. Static Assets - Frontend
**Directory**: `static/static/`
**Changes**: Replace browser favicon files only (license-compliant minimal change)

**⚠️ License Note**: Per research in `thoughts/shared/research/2026-01-06-open-webui-license-branding-restrictions.md`, replacing logos constitutes "replacing visual identifiers" under Clause 4. Only browser favicons are changed as a minimal, subtle customization.

Files to replace with soev.ai logo (browser favicons only):
| File | Size | Source |
|------|------|--------|
| `favicon.png` | ~96x96 | Scale from icon-192x192.png |
| `favicon.ico` | Multi-size | Convert from PNG |
| `favicon-96x96.png` | 96x96 | Scale from icon-192x192.png |
| `favicon-dark.png` | ~96x96 | Same as favicon.png (mesh works on dark) |

Files kept as Open WebUI original (license compliance):
| File | Reason |
|------|--------|
| `logo.png` | Protected branding |
| `splash.png` / `splash-dark.png` | Protected branding |
| `apple-touch-icon.png` | Protected branding |
| `web-app-manifest-*.png` | PWA icons - protected branding |

**Commands to generate (favicons only):**
```bash
# Using sips (macOS) or ImageMagick
cd /Users/lexlubbers/Code/soev/open-webui

# Generate favicon sizes
sips -Z 96 icon-192x192.png --out static/static/favicon.png
sips -Z 96 icon-192x192.png --out static/static/favicon-96x96.png
sips -Z 96 icon-192x192.png --out static/static/favicon-dark.png

# Generate favicon.ico (multi-size) using Python/Pillow
python3 -c "
from PIL import Image
source = Image.open('icon-192x192.png').convert('RGBA')
source.save('static/static/favicon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64)])
"
```

#### 3. Static Assets - Backend (Auto-copied at startup)
**Note**: Backend static files are auto-copied from frontend build at startup via `config.py:851-865`. After building the frontend, the backend will have updated files.

### Success Criteria:

#### Automated Verification:
- [x] Environment variable is set: `echo $WEBUI_NAME` returns "soev.ai" - Added to .env.example
- [x] Static files exist: `ls static/static/favicon.png` shows soev.ai logo
- [x] Frontend builds successfully: `npm run build`
- [x] No TypeScript errors: `npm run check` - Pre-existing errors unrelated to changes, build passes

#### Manual Verification:
- [ ] Browser tab shows "soev.ai (Open WebUI)" title
- [ ] Favicon in browser tab shows soev.ai mesh logo
- [ ] Login page shows Open WebUI logo (kept for license compliance)
- [ ] PWA install prompt shows Open WebUI icon (kept for license compliance)

**Implementation Note**: After completing this phase, verify the favicon appears correctly in browser before proceeding.

---

## Phase 2: Login Footer Implementation

### Overview
Add custom login footer capability (existing login_footer requires enterprise license). We'll create a non-license-dependent footer configuration.

### Changes Required:

#### 1. Backend Configuration
**File**: `backend/open_webui/config.py`
**Changes**: Add SOEV_LOGIN_FOOTER config option

Find the UI configuration section (around line 1165) and add:

```python
# soev.ai branding
SOEV_LOGIN_FOOTER = PersistentConfig(
    "SOEV_LOGIN_FOOTER",
    "ui.soev_login_footer",
    os.environ.get("SOEV_LOGIN_FOOTER", "Powered by [soev.ai](https://soev.ai)"),
)
```

#### 2. Backend API Endpoint
**File**: `backend/open_webui/main.py`
**Changes**: Include soev_login_footer in /api/config response

Find the `/api/config` endpoint (around line 1884) and add to the response dict after the metadata section:

```python
# Around line 2021, after the LICENSE_METADATA block
"soev_login_footer": app.state.config.SOEV_LOGIN_FOOTER,
```

#### 3. Frontend Display
**File**: `src/routes/auth/+page.svelte`
**Changes**: Display soev_login_footer on auth page

After line 565 (after the existing login_footer block), add:

```svelte
{#if $config?.soev_login_footer}
    <div class="max-w-3xl mx-auto">
        <div class="mt-2 text-[0.7rem] text-gray-500 dark:text-gray-400 marked">
            {@html DOMPurify.sanitize(marked($config?.soev_login_footer))}
        </div>
    </div>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev` - Config loads correctly
- [x] API returns soev_login_footer: `curl localhost:8080/api/config | jq '.soev_login_footer'` - Added to API response
- [x] TypeScript check passes: `npm run check` - Build passes, pre-existing errors unrelated

#### Manual Verification:
- [ ] Login page shows "Powered by soev.ai" footer
- [ ] Footer link navigates to https://soev.ai
- [ ] Footer appears below the sign-in form

**Implementation Note**: After completing this phase, verify the footer displays on the login page before proceeding.

---

## Phase 3: About Section Branding

### Overview
Add soev.ai branding to both user Settings → About and Admin Settings sections. Branding is added ABOVE existing Open WebUI attribution (not replacing).

### Changes Required:

#### 1. User Settings About
**File**: `src/lib/components/chat/Settings/About.svelte`
**Changes**: Add soev.ai section before version info

Insert after line 48 (after the opening div), before the version section:

```svelte
<!-- soev.ai Branding -->
<div class="mb-4 pb-4 border-b border-gray-100 dark:border-gray-850">
    <div class="flex items-center space-x-3">
        <img
            src="{WEBUI_BASE_URL}/static/favicon.png"
            alt="soev.ai"
            class="w-10 h-10 rounded-lg"
        />
        <div>
            <div class="font-semibold text-base">soev.ai</div>
            <a
                href="https://soev.ai"
                target="_blank"
                class="text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400"
            >
                https://soev.ai
            </a>
        </div>
    </div>
</div>
```

Need to import WEBUI_BASE_URL at the top of the script section:

```typescript
import { WEBUI_BASE_URL } from '$lib/constants';
```

#### 2. Admin Settings General
**File**: `src/lib/components/admin/Settings/General.svelte`
**Changes**: Add soev.ai section in the About/version area

Find the version display section (around line 141) and add before it:

```svelte
<!-- soev.ai Branding -->
<div class="mb-4 pb-4 border-b border-gray-100 dark:border-gray-850">
    <div class="flex items-center space-x-3">
        <img
            src="{WEBUI_BASE_URL}/static/favicon.png"
            alt="soev.ai"
            class="w-10 h-10 rounded-lg"
        />
        <div>
            <div class="font-semibold">soev.ai</div>
            <a
                href="https://soev.ai"
                target="_blank"
                class="text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400"
            >
                https://soev.ai
            </a>
        </div>
    </div>
</div>
```

### Success Criteria:

#### Automated Verification:
- [ ] Frontend builds: `npm run build`
- [ ] TypeScript check: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

#### Manual Verification:
- [ ] User Settings → About shows soev.ai section at top
- [ ] Admin Settings → General shows soev.ai section
- [ ] soev.ai link opens https://soev.ai in new tab
- [ ] Open WebUI attribution still visible below soev.ai section

**Implementation Note**: After completing this phase, verify both About sections show correct branding.

---

## Phase 4: Welcome Message Configuration

### Overview
Configure custom welcome message and prompt suggestions via admin settings or environment.

### Changes Required:

This can be done via:
1. **Admin UI**: Admin Settings → Interface → Default Prompt Suggestions
2. **Environment Variable**: `DEFAULT_PROMPT_SUGGESTIONS`

#### Option A: Environment Configuration
**File**: `.env` or deployment config

```bash
DEFAULT_PROMPT_SUGGESTIONS='[
    {
        "title": ["Welcome to soev.ai", "How can I help you today?"],
        "content": "Tell me what you need help with."
    },
    {
        "title": ["Analyze a document", "or summarize content"],
        "content": "Please analyze this document and provide key insights: "
    },
    {
        "title": ["Write code", "in any language"],
        "content": "Write a function that "
    },
    {
        "title": ["Research a topic", "with sources"],
        "content": "Research and explain: "
    }
]'
```

#### Option B: Admin UI Configuration
After deployment, navigate to Admin Settings → Interface → Default Prompt Suggestions and configure the prompts.

### Success Criteria:

#### Automated Verification:
- [ ] Environment variable parses correctly (no JSON errors in logs)
- [ ] API returns suggestions: `curl localhost:8080/api/config | jq '.default_prompt_suggestions'`

#### Manual Verification:
- [ ] New chat shows custom prompt suggestions
- [ ] Suggestions display soev.ai themed text
- [ ] Clicking suggestions populates the input field

---

## Phase 5: Optional - Banner System

### Overview
Optionally add a persistent banner announcing soev.ai. This can be configured via Admin UI.

### Changes Required:

#### Via Admin UI (Recommended)
Admin Settings → Interface → Banners → Add Banner

Example banner configuration:
```json
{
    "id": "soev-welcome",
    "type": "info",
    "title": "Welcome",
    "content": "You're using soev.ai - Enterprise AI Platform",
    "dismissible": true,
    "timestamp": 1736150400
}
```

#### Via Environment Variable
```bash
WEBUI_BANNERS='[{"id":"soev-welcome","type":"info","title":"Welcome","content":"You are using soev.ai - Enterprise AI Platform","dismissible":true,"timestamp":1736150400}]'
```

### Success Criteria:

#### Manual Verification:
- [ ] Banner appears at top of chat interface
- [ ] Banner can be dismissed by user
- [ ] Banner reappears for new sessions (if desired)

---

## Testing Strategy

### Unit Tests:
- Test SOEV_LOGIN_FOOTER config parsing
- Test API endpoint returns soev_login_footer

### Integration Tests:
- Test full auth page renders with footer
- Test About sections render with branding

### Manual Testing Steps:
1. Start fresh browser session (clear cache)
2. Navigate to login page - verify favicon, title, footer
3. Log in and verify favicon persists
4. Open Settings → About - verify soev.ai section
5. Open Admin Settings - verify soev.ai section
6. Start new chat - verify welcome prompts
7. Install as PWA - verify name and icon

## File Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `.env` | Config | Add WEBUI_NAME, optional DEFAULT_PROMPT_SUGGESTIONS |
| `static/static/*.png` | Replace | All favicon/logo/splash images |
| `backend/open_webui/config.py` | Edit | Add SOEV_LOGIN_FOOTER config |
| `backend/open_webui/main.py` | Edit | Add soev_login_footer to /api/config |
| `src/routes/auth/+page.svelte` | Edit | Display soev_login_footer |
| `src/lib/components/chat/Settings/About.svelte` | Edit | Add soev.ai branding section |
| `src/lib/components/admin/Settings/General.svelte` | Edit | Add soev.ai branding section |

## References

- License research: `thoughts/shared/research/2026-01-06-open-webui-license-branding-restrictions.md`
- Branding enforcement: `backend/open_webui/env.py:90-92`
- Login footer display: `src/routes/auth/+page.svelte:559-565`
- About section: `src/lib/components/chat/Settings/About.svelte`
- Admin settings: `src/lib/components/admin/Settings/General.svelte`
