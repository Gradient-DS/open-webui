# Configurable Greeting Template Implementation Plan

## Overview

Add a backend `PersistentConfig` for a customizable greeting template (e.g., "Welkom bij CompanyName, {{name}}") that replaces the hardcoded `"Hello, {{name}}"` i18n key in the new chat placeholder. This allows per-deployment greeting customization via environment variable, admin UI, or API — without modifying the upstream translation files.

## Current State Analysis

- The greeting "Hello, {{name}}" is rendered via `$i18n.t('Hello, {{name}}', { name: $user?.name })` in two components:
  - `src/lib/components/chat/Placeholder.svelte:151` (default centered mode)
  - `src/lib/components/chat/ChatPlaceholder.svelte:87` (chat-style left-aligned mode)
- When a model is selected and has a `.name`, the model name is shown instead of the greeting
- The subtitle "How can I help you today?" follows the same pattern at `Placeholder.svelte:218` and `ChatPlaceholder.svelte:120`
- Config values use `PersistentConfig` in `config.py` which auto-persists to DB via `AppConfig.__setattr__` (`config.py:251-256`)
- The frontend reads config from the `/api/config` endpoint into the `$config` Svelte store
- Banners and suggestions each have dedicated POST endpoints in `routers/configs.py`

### Key Discoveries:
- `AppConfig.__setattr__` (`config.py:251-256`) automatically saves to DB when you assign `app.state.config.X = value`
- The admin Interface settings page (`src/lib/components/admin/Settings/Interface.svelte:52-60`) calls separate API functions for task config, suggestions, and banners
- `WEBUI_NAME` is already available on the frontend but is not used in the greeting
- The `$config` store type is defined at `src/lib/stores/index.ts:255-308`

## Desired End State

- Admins can set a custom greeting template via:
  1. `GREETING_TEMPLATE` environment variable (initial seed)
  2. Admin Panel > Settings > Interface > UI section (persisted to DB)
- The template supports `{{name}}` interpolation for the user's display name
- When the template is empty (default), the existing i18n `"Hello, {{name}}"` behavior is preserved
- Both `Placeholder.svelte` and `ChatPlaceholder.svelte` respect the configured template
- Example: setting `"Welkom bij Acme Corp, {{name}}"` shows "Welkom bij Acme Corp, Lex"

### Verification:
1. Set `GREETING_TEMPLATE="Welkom bij TestCo, {{name}}"` in env, restart backend — greeting shows "Welkom bij TestCo, Lex"
2. Change it via admin UI, refresh — new greeting appears
3. Clear the field — falls back to the translated "Hello, Lex"
4. Select a model with a name — model name still shows (no change to that logic)

## What We're NOT Doing

- Not adding a configurable subtitle template (the "How can I help you today?" text) — this can be a follow-up
- Not adding per-model greeting templates — models already override the greeting with their name/description
- Not adding admin UI for editing translation files
- Not changing the existing i18n system or translation files

## Implementation Approach

Follow the exact pattern of existing `PersistentConfig` values like `PENDING_USER_OVERLAY_TITLE` and `WEBUI_BANNERS`:
1. Define `PersistentConfig` in `config.py`
2. Wire into `app.state.config` in `main.py`
3. Expose in `/api/config` response
4. Add a dedicated POST endpoint in `routers/configs.py`
5. Add frontend API client function
6. Update admin UI with a text input
7. Update both placeholder components to read from config

## Phase 1: Backend Configuration

### Overview
Add the `GREETING_TEMPLATE` PersistentConfig and expose it to the frontend via the existing config endpoint.

### Changes Required:

#### 1. Add PersistentConfig definition
**File**: `backend/open_webui/config.py`
**Location**: After `PENDING_USER_OVERLAY_CONTENT` (line ~1247)
**Changes**: Add new PersistentConfig

```python
GREETING_TEMPLATE = PersistentConfig(
    "GREETING_TEMPLATE",
    "ui.greeting_template",
    os.environ.get("GREETING_TEMPLATE", ""),
)
```

#### 2. Wire into app.state.config
**File**: `backend/open_webui/main.py`
**Location**: After `app.state.config.PENDING_USER_OVERLAY_TITLE = PENDING_USER_OVERLAY_TITLE` (line ~827)
**Changes**: Add import and assignment

Add to the imports from `open_webui.config` (around line 380):
```python
GREETING_TEMPLATE,
```

Add after the PENDING_USER_OVERLAY lines:
```python
app.state.config.GREETING_TEMPLATE = GREETING_TEMPLATE
```

#### 3. Expose in /api/config response
**File**: `backend/open_webui/main.py`
**Location**: Inside the `"ui"` dict in the config response (line ~2066-2069)
**Changes**: Add `greeting_template` field

```python
"ui": {
    "pending_user_overlay_title": app.state.config.PENDING_USER_OVERLAY_TITLE,
    "pending_user_overlay_content": app.state.config.PENDING_USER_OVERLAY_CONTENT,
    "response_watermark": app.state.config.RESPONSE_WATERMARK,
    "greeting_template": app.state.config.GREETING_TEMPLATE,
},
```

#### 4. Add API endpoint to persist the setting
**File**: `backend/open_webui/routers/configs.py`
**Location**: After the banners endpoints (line ~537)
**Changes**: Add GET/POST endpoints for greeting template

```python
############################
# GreetingTemplate
############################


class SetGreetingTemplateForm(BaseModel):
    template: str


@router.post("/greeting_template")
async def set_greeting_template(
    request: Request,
    form_data: SetGreetingTemplateForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.GREETING_TEMPLATE = form_data.template
    return {"template": request.app.state.config.GREETING_TEMPLATE}


@router.get("/greeting_template")
async def get_greeting_template(
    request: Request,
    user=Depends(get_verified_user),
):
    return {"template": request.app.state.config.GREETING_TEMPLATE}
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] `GET /api/v1/configs/greeting_template` returns `{"template": ""}` by default
- [ ] `POST /api/v1/configs/greeting_template` with `{"template": "Welkom bij Test, {{name}}"}` persists the value
- [ ] `/api/config` response includes `ui.greeting_template` field
- [x] Backend lint passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Verify the config value appears in the `/api/config` JSON response via browser devtools
- [ ] Verify setting `GREETING_TEMPLATE` env var seeds the initial value

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Frontend - Read Config and Update Components

### Overview
Update the TypeScript config type and both placeholder components to use the greeting template when configured.

### Changes Required:

#### 1. Add to Config TypeScript type
**File**: `src/lib/stores/index.ts`
**Location**: Inside the `Config` type, in the `ui` section (line ~304-307)
**Changes**: Add `greeting_template` field

```typescript
ui?: {
    pending_user_overlay_title?: string;
    pending_user_overlay_description?: string;
    greeting_template?: string;
};
```

#### 2. Update Placeholder.svelte
**File**: `src/lib/components/chat/Placeholder.svelte`
**Location**: Line 150-152 (the `{:else}` branch of the greeting)
**Changes**: Check `$config?.ui?.greeting_template` before falling back to i18n

Replace:
```svelte
{:else}
    {$i18n.t('Hello, {{name}}', { name: $user?.name })}
{/if}
```

With:
```svelte
{:else}
    {$config?.ui?.greeting_template
        ? $config.ui.greeting_template.replace('{{name}}', $user?.name ?? '')
        : $i18n.t('Hello, {{name}}', { name: $user?.name })}
{/if}
```

#### 3. Update ChatPlaceholder.svelte
**File**: `src/lib/components/chat/ChatPlaceholder.svelte`
**Location**: Line 86-88 (the `{:else}` branch of the greeting)
**Changes**: Same logic as Placeholder.svelte

Replace:
```svelte
{:else}
    {$i18n.t('Hello, {{name}}', { name: $user?.name })}
{/if}
```

With:
```svelte
{:else}
    {$config?.ui?.greeting_template
        ? $config.ui.greeting_template.replace('{{name}}', $user?.name ?? '')
        : $i18n.t('Hello, {{name}}', { name: $user?.name })}
{/if}
```

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds without errors: `npm run build`
- [x] TypeScript check passes: `npm run check` (pre-existing errors only)
- [x] Frontend lint passes: `npm run lint:frontend` (pre-existing errors only)

#### Manual Verification:
- [ ] With no greeting template set: shows localized "Hello, <name>" as before
- [ ] After setting greeting template via API (curl or backend): custom greeting appears on new chat page
- [ ] Both landing page modes (default and chat) show the custom greeting
- [ ] Selecting a model with a name still shows model name (not the greeting)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Admin UI and Frontend API Client

### Overview
Add a text input for the greeting template in the admin Interface settings page, with an API client function to persist changes.

### Changes Required:

#### 1. Add API client function
**File**: `src/lib/apis/configs/index.ts`
**Location**: After the `setBanners` function (line ~450)
**Changes**: Add `setGreetingTemplate` and `getGreetingTemplate` functions

```typescript
export const getGreetingTemplate = async (token: string): Promise<string> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/greeting_template`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.template ?? '';
};

export const setGreetingTemplate = async (token: string, template: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/greeting_template`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			template: template
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.template ?? '';
};
```

#### 2. Add admin UI input
**File**: `src/lib/components/admin/Settings/Interface.svelte`
**Changes**:

Add import (top of script):
```typescript
import { getGreetingTemplate, setGreetingTemplate } from '$lib/apis/configs';
```

Add state variable (after `let banners: Banner[] = [];` at line 50):
```typescript
let greetingTemplate = '';
```

Add to `init()` function (after banners loading, around line 74):
```typescript
greetingTemplate = await getGreetingTemplate(localStorage.token);
```

Add to `updateInterfaceHandler()` (after the `updateBanners()` call, around line 57):
```typescript
await setGreetingTemplate(localStorage.token, greetingTemplate);
```

Add UI input in the "UI" section (after the Banners block, before PromptSuggestions, around line 468):
```svelte
<div class="mb-2.5">
    <div class="flex w-full justify-between mb-1">
        <div class="self-center text-xs font-medium">
            {$i18n.t('Greeting Template')}
        </div>
    </div>
    <Tooltip
        content={$i18n.t('Use {{name}} for the user\'s display name. Leave empty to use the default greeting.')}
        placement="top-start"
    >
        <input
            class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
            type="text"
            placeholder={$i18n.t('e.g. Welcome to Acme Corp, {{name}}')}
            bind:value={greetingTemplate}
        />
    </Tooltip>
</div>
```

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds without errors: `npm run build`
- [x] TypeScript check passes: `npm run check` (pre-existing errors only)
- [x] Frontend lint passes: `npm run lint:frontend` (pre-existing errors only)
- [x] Backend lint passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Admin panel > Settings > Interface > UI section shows "Greeting Template" text input
- [ ] Input has a helpful placeholder text and tooltip
- [ ] Typing a custom greeting and clicking Save persists the value
- [ ] Opening a new chat shows the custom greeting with the user's name interpolated
- [ ] Clearing the field and saving reverts to the default "Hello, <name>" greeting
- [ ] Non-admin users cannot access the setting

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Testing Strategy

### Manual Testing Steps:
1. Start with clean state (no GREETING_TEMPLATE env var, no DB value) — verify default "Hello, <name>" works
2. Set `GREETING_TEMPLATE="Welkom bij TestCo, {{name}}"` env var, restart — verify it seeds correctly
3. Change via admin UI to a different value — verify it overrides the env var value (PersistentConfig behavior)
4. Clear the field via admin UI — verify fallback to i18n greeting
5. Test with both `landingPageMode` settings (default and 'chat')
6. Test with a model selected that has a name — model name should still show
7. Test with `{{name}}` interpolation edge cases: user with no name set, user with special characters in name

## Performance Considerations

None. This adds a single string field to an already-loaded config object. No additional API calls are needed at chat render time since the config is loaded once at app initialization.

## Migration Notes

No migration needed. The `PersistentConfig` stores values in the existing config database table. The default empty string means all existing deployments behave identically to before.

## References

- Research: `thoughts/shared/research/2026-02-02-welcome-greeting-customization.md`
- Similar pattern: `PENDING_USER_OVERLAY_TITLE` in `config.py:1237-1241`
- Banners endpoint pattern: `routers/configs.py:520-536`
- AppConfig auto-save: `config.py:251-256`
