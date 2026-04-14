# Acceptance Modal Implementation Plan

## Overview

Add a configurable acceptance modal (blocking overlay) that users must accept on first login. Admin-configurable via Admin > Settings > General. Uses content-hash tracking so changing the terms triggers re-acceptance for all users.

## Current State Analysis

- **No acceptance/terms modal exists** in the codebase
- The closest pattern is `AccountPending` overlay (`src/lib/components/layout/Overlay/AccountPending.svelte`) — a full-screen blocking overlay with admin-customizable title/content
- Admin config follows a well-established pattern: `PersistentConfig` in `config.py` → `app.state.config` in `main.py` → admin API endpoints in `routers/auths.py` → frontend admin UI in `Settings/General.svelte`
- User settings are stored as a JSON column (`User.settings`) with `extra="allow"`, allowing new fields without migrations

### Key Discoveries:

- `PersistentConfig` pattern: `config.py:165-222` — env var + database-backed config
- Admin config GET/POST: `routers/auths.py:928-1039` — `AdminConfig` Pydantic model + GET/POST handlers
- `/api/config` features: `main.py:2109-2114` — `ui` section exposes settings to frontend
- `AccountPending` overlay: `src/lib/components/layout/Overlay/AccountPending.svelte` — design pattern to follow
- `User.settings` JSON column: `models/users.py:69` — stores arbitrary user settings
- `updateUserSettings`: `src/lib/apis/users/index.ts:273` — frontend API for updating user settings

## Desired End State

- Admin can enable/disable an acceptance modal with custom title, content (markdown), and button text
- When enabled, active users see a blocking overlay on login that they must accept
- Acceptance is tracked per-user via a hash of the modal content
- If admin changes the modal content, all users must re-accept
- Admin UI controls live in Admin > Settings > General, near the existing "Pending User Overlay" section

### Verification:

1. Enable the modal in admin settings with custom title/content
2. Log in as a regular user — blocking overlay appears
3. Click accept — overlay dismissed, user can use the app
4. Refresh — overlay does not reappear
5. Admin changes the content — user sees overlay again on next page load
6. Disable the modal — no users see it, regardless of acceptance state

## What We're NOT Doing

- No separate database table or migration — we use the existing `User.settings` JSON column
- No per-user admin visibility of who has/hasn't accepted (can be added later)
- No versioning or history of acceptance modal content
- No rich text editor in admin — plain textarea with markdown support (same as pending user overlay)

## Implementation Approach

Follow the exact same patterns as `PENDING_USER_OVERLAY_TITLE`/`PENDING_USER_OVERLAY_CONTENT` for the backend config, and model the frontend overlay after `AccountPending.svelte`.

Track acceptance via a SHA-256 hash of `title + content` stored in `user.settings.acceptance_hash`. On the frontend, compute the hash of the current modal content and compare with the stored hash. If they don't match (or no hash exists), show the overlay.

## Phase 1: Backend — Config & API

### Overview

Add PersistentConfig settings and expose them through the admin config API and the public `/api/config` endpoint.

### Changes Required:

#### 1. Add PersistentConfig declarations

**File**: `backend/open_webui/config.py` (after line 1248, near `PENDING_USER_OVERLAY_CONTENT`)

```python
ENABLE_ACCEPTANCE_MODAL = PersistentConfig(
    "ENABLE_ACCEPTANCE_MODAL",
    "ui.enable_acceptance_modal",
    os.environ.get("ENABLE_ACCEPTANCE_MODAL", "False").lower() == "true",
)

ACCEPTANCE_MODAL_TITLE = PersistentConfig(
    "ACCEPTANCE_MODAL_TITLE",
    "ui.acceptance_modal_title",
    os.environ.get("ACCEPTANCE_MODAL_TITLE", ""),
)

ACCEPTANCE_MODAL_CONTENT = PersistentConfig(
    "ACCEPTANCE_MODAL_CONTENT",
    "ui.acceptance_modal_content",
    os.environ.get("ACCEPTANCE_MODAL_CONTENT", ""),
)

ACCEPTANCE_MODAL_BUTTON_TEXT = PersistentConfig(
    "ACCEPTANCE_MODAL_BUTTON_TEXT",
    "ui.acceptance_modal_button_text",
    os.environ.get("ACCEPTANCE_MODAL_BUTTON_TEXT", ""),
)
```

#### 2. Register on app.state.config

**File**: `backend/open_webui/main.py`

Add imports (in the import block from `config.py`, around line 388):

```python
ENABLE_ACCEPTANCE_MODAL,
ACCEPTANCE_MODAL_TITLE,
ACCEPTANCE_MODAL_CONTENT,
ACCEPTANCE_MODAL_BUTTON_TEXT,
```

Add registration (after line 840, near `PENDING_USER_OVERLAY_TITLE`):

```python
app.state.config.ENABLE_ACCEPTANCE_MODAL = ENABLE_ACCEPTANCE_MODAL
app.state.config.ACCEPTANCE_MODAL_TITLE = ACCEPTANCE_MODAL_TITLE
app.state.config.ACCEPTANCE_MODAL_CONTENT = ACCEPTANCE_MODAL_CONTENT
app.state.config.ACCEPTANCE_MODAL_BUTTON_TEXT = ACCEPTANCE_MODAL_BUTTON_TEXT
```

Add to `/api/config` response `ui` section (after line 2113, inside the `"ui"` dict):

```python
"enable_acceptance_modal": app.state.config.ENABLE_ACCEPTANCE_MODAL,
"acceptance_modal_title": app.state.config.ACCEPTANCE_MODAL_TITLE,
"acceptance_modal_content": app.state.config.ACCEPTANCE_MODAL_CONTENT,
"acceptance_modal_button_text": app.state.config.ACCEPTANCE_MODAL_BUTTON_TEXT,
```

#### 3. Add to admin config endpoints

**File**: `backend/open_webui/routers/auths.py`

Add to `get_admin_config` response dict (after line 948):

```python
"ENABLE_ACCEPTANCE_MODAL": request.app.state.config.ENABLE_ACCEPTANCE_MODAL,
"ACCEPTANCE_MODAL_TITLE": request.app.state.config.ACCEPTANCE_MODAL_TITLE,
"ACCEPTANCE_MODAL_CONTENT": request.app.state.config.ACCEPTANCE_MODAL_CONTENT,
"ACCEPTANCE_MODAL_BUTTON_TEXT": request.app.state.config.ACCEPTANCE_MODAL_BUTTON_TEXT,
```

Add to `AdminConfig` Pydantic model (after line 970):

```python
ENABLE_ACCEPTANCE_MODAL: bool
ACCEPTANCE_MODAL_TITLE: Optional[str] = None
ACCEPTANCE_MODAL_CONTENT: Optional[str] = None
ACCEPTANCE_MODAL_BUTTON_TEXT: Optional[str] = None
```

Add to `update_admin_config` handler (after line 1018):

```python
request.app.state.config.ENABLE_ACCEPTANCE_MODAL = form_data.ENABLE_ACCEPTANCE_MODAL
request.app.state.config.ACCEPTANCE_MODAL_TITLE = form_data.ACCEPTANCE_MODAL_TITLE
request.app.state.config.ACCEPTANCE_MODAL_CONTENT = form_data.ACCEPTANCE_MODAL_CONTENT
request.app.state.config.ACCEPTANCE_MODAL_BUTTON_TEXT = form_data.ACCEPTANCE_MODAL_BUTTON_TEXT
```

Add to `update_admin_config` response dict (after line 1038):

```python
"ENABLE_ACCEPTANCE_MODAL": request.app.state.config.ENABLE_ACCEPTANCE_MODAL,
"ACCEPTANCE_MODAL_TITLE": request.app.state.config.ACCEPTANCE_MODAL_TITLE,
"ACCEPTANCE_MODAL_CONTENT": request.app.state.config.ACCEPTANCE_MODAL_CONTENT,
"ACCEPTANCE_MODAL_BUTTON_TEXT": request.app.state.config.ACCEPTANCE_MODAL_BUTTON_TEXT,
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts without errors: `open-webui dev`
- [ ] `GET /api/v1/auths/admin/config` returns the new fields
- [ ] `POST /api/v1/auths/admin/config` accepts and persists the new fields
- [ ] `GET /api/config` includes acceptance modal settings in the `ui` section

#### Manual Verification:

- [ ] Restart the server — settings persist across restarts
- [ ] Set env vars `ENABLE_ACCEPTANCE_MODAL=true` etc. — they serve as defaults

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 2: Frontend — AcceptanceModal Component

### Overview

Create the blocking overlay component, closely following the `AccountPending.svelte` design pattern.

### Changes Required:

#### 1. Create AcceptanceModal overlay

**File**: `src/lib/components/layout/Overlay/AcceptanceModal.svelte` (new file)

```svelte
<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import { getContext } from 'svelte';
	import { config, user, settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';

	const i18n = getContext('i18n');

	export let show = false;

	const getAcceptanceHash = async (title: string, content: string) => {
		const text = `${title}:${content}`;
		const encoder = new TextEncoder();
		const data = encoder.encode(text);
		const hashBuffer = await crypto.subtle.digest('SHA-256', data);
		const hashArray = Array.from(new Uint8Array(hashBuffer));
		return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
	};

	const acceptHandler = async () => {
		const hash = await getAcceptanceHash(
			$config?.ui?.acceptance_modal_title ?? '',
			$config?.ui?.acceptance_modal_content ?? ''
		);

		await settings.set({ ...$settings, acceptance_hash: hash });
		await updateUserSettings(localStorage.token, { ui: $settings });
		show = false;
	};
</script>

{#if show}
	<div class="fixed w-full h-full flex z-999">
		<div
			class="absolute w-full h-full backdrop-blur-lg bg-white/10 dark:bg-gray-900/50 flex justify-center"
		>
			<div class="m-auto pb-10 flex flex-col justify-center">
				<div class="max-w-md">
					<div
						class="text-center dark:text-white text-2xl font-medium z-50"
						style="white-space: pre-wrap;"
					>
						{#if ($config?.ui?.acceptance_modal_title ?? '').trim() !== ''}
							{$config.ui.acceptance_modal_title}
						{:else}
							{$i18n.t('Terms of Use')}
						{/if}
					</div>

					<div
						class="mt-4 text-center text-sm dark:text-gray-200 w-full"
						style="white-space: pre-wrap;"
					>
						{#if ($config?.ui?.acceptance_modal_content ?? '').trim() !== ''}
							{@html marked.parse(
								DOMPurify.sanitize(
									($config?.ui?.acceptance_modal_content ?? '').replace(/\n/g, '<br>')
								)
							)}
						{:else}
							{$i18n.t('Please accept the terms of use to continue.')}
						{/if}
					</div>

					<div class="mt-6 mx-auto relative group w-fit">
						<button
							class="relative z-20 flex px-5 py-2 rounded-full bg-black dark:bg-white text-white dark:text-black hover:bg-gray-900 dark:hover:bg-gray-100 transition font-medium text-sm"
							on:click={acceptHandler}
						>
							{#if ($config?.ui?.acceptance_modal_button_text ?? '').trim() !== ''}
								{$config.ui.acceptance_modal_button_text}
							{:else}
								{$i18n.t('I Accept')}
							{/if}
						</button>
					</div>
				</div>
			</div>
		</div>
	</div>
{/if}
```

### Success Criteria:

#### Automated Verification:

- [x] No TypeScript errors: `npm run check` (no new errors beyond pre-existing)
- [x] Frontend builds: `npm run build`

#### Manual Verification:

- [ ] Component renders correctly (will test in Phase 4 integration)

**Implementation Note**: After completing this phase, proceed directly to Phase 3.

---

## Phase 3: Frontend — Admin UI Controls

### Overview

Add acceptance modal configuration controls to Admin > Settings > General, grouped near the existing "Pending User Overlay" section.

### Changes Required:

#### 1. Add admin controls

**File**: `src/lib/components/admin/Settings/General.svelte`

After the "Pending User Overlay Content" textarea block (after line 377), add:

```svelte
<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

<div class="mb-2.5 flex w-full justify-between pr-2">
	<div class="self-center text-xs font-medium">
		{$i18n.t('Enable Acceptance Modal')}
	</div>
	<Switch bind:state={adminConfig.ENABLE_ACCEPTANCE_MODAL} />
</div>

{#if adminConfig.ENABLE_ACCEPTANCE_MODAL}
	<div class="mb-2.5">
		<div class="self-center text-xs font-medium mb-2">
			{$i18n.t('Acceptance Modal Title')}
		</div>
		<Textarea
			placeholder={$i18n.t('Enter a title for the acceptance modal. Leave empty for default.')}
			bind:value={adminConfig.ACCEPTANCE_MODAL_TITLE}
		/>
	</div>

	<div class="mb-2.5">
		<div class="self-center text-xs font-medium mb-2">
			{$i18n.t('Acceptance Modal Content')}
		</div>
		<Textarea
			placeholder={$i18n.t(
				'Enter content for the acceptance modal. Supports markdown. Leave empty for default.'
			)}
			bind:value={adminConfig.ACCEPTANCE_MODAL_CONTENT}
		/>
	</div>

	<div class="mb-2.5">
		<div class="self-center text-xs font-medium mb-2">
			{$i18n.t('Acceptance Modal Button Text')}
		</div>
		<Textarea
			placeholder={$i18n.t('Enter button text for the acceptance modal. Leave empty for default.')}
			bind:value={adminConfig.ACCEPTANCE_MODAL_BUTTON_TEXT}
		/>
	</div>
{/if}
```

### Success Criteria:

#### Automated Verification:

- [x] Frontend builds: `npm run build`

#### Manual Verification:

- [ ] Admin > Settings > General shows the new "Enable Acceptance Modal" toggle
- [ ] Toggle reveals title/content/button text fields when enabled
- [ ] Saving persists the settings (refresh and verify they reload)
- [ ] Fields are hidden when toggle is off

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 4: Frontend — Layout Integration

### Overview

Mount the AcceptanceModal in the app layout, showing it when enabled and the user hasn't accepted the current terms.

### Changes Required:

#### 1. Integrate in app layout

**File**: `src/routes/(app)/+layout.svelte`

Add import (near line 44, after `AccountPending` import):

```svelte
import AcceptanceModal from '$lib/components/layout/Overlay/AcceptanceModal.svelte';
```

Add state variable and hash computation in the `<script>` block (inside `onMount`, after the changelog check around line 264):

```svelte
// Acceptance modal check
let showAcceptanceModal = false;

const checkAcceptanceModal = async () => {
	if (!$config?.ui?.enable_acceptance_modal) return;

	const title = $config?.ui?.acceptance_modal_title ?? '';
	const content = $config?.ui?.acceptance_modal_content ?? '';
	const text = `${title}:${content}`;
	const encoder = new TextEncoder();
	const data = encoder.encode(text);
	const hashBuffer = await crypto.subtle.digest('SHA-256', data);
	const hashArray = Array.from(new Uint8Array(hashBuffer));
	const currentHash = hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');

	const userHash = $settings?.acceptance_hash ?? '';
	if (currentHash !== userHash) {
		showAcceptanceModal = true;
	}
};
```

Call `checkAcceptanceModal()` inside `onMount` after the settings are loaded.

Add the overlay in the template (after the `AccountPending` block, inside the `{:else}` branch around line 327):

```svelte
{#if showAcceptanceModal}
	<AcceptanceModal
		show={showAcceptanceModal}
		on:accepted={() => {
			showAcceptanceModal = false;
		}}
	/>
{:else}
	<!-- existing content (localDBChats migration, Sidebar, main slot, etc.) -->
{/if}
```

Note: The exact integration will depend on the reactive pattern. The `show` prop on AcceptanceModal already handles dismissal by calling `show = false` after updating settings. We may need to use `bind:show` instead and restructure slightly. The key is:

- Show the overlay **after** AccountPending check (only for active users)
- Block all other content while overlay is visible
- After acceptance, the overlay hides and the normal app renders

### Success Criteria:

#### Automated Verification:

- [x] Frontend builds: `npm run build`
- [x] No new TypeScript errors: `npm run check`

#### Manual Verification:

- [ ] Enable acceptance modal in admin, set title/content
- [ ] Log in as regular user — blocking overlay appears with custom title/content/button
- [ ] Cannot interact with the app behind the overlay
- [ ] Click accept — overlay dismisses, app is usable
- [ ] Refresh page — overlay does NOT reappear
- [ ] Admin changes content — overlay reappears on next page load
- [ ] Admin disables modal — no overlay shown regardless of acceptance state
- [ ] Admin user also sees the overlay when enabled (not just regular users)

**Implementation Note**: After completing this phase and all verification passes, the feature is complete.

---

## Testing Strategy

### Manual Testing Steps:

1. Start with modal disabled — verify no overlay appears for any user
2. Enable modal with custom title "Welcome", content "Please accept our terms", button "I Agree"
3. Log in as non-admin user — verify blocking overlay with custom text
4. Accept — verify overlay dismisses and doesn't return on refresh
5. As admin, change content to "Updated terms" — verify overlay reappears for the user
6. User accepts again — verify it sticks
7. Disable modal — verify no overlay for anyone
8. Re-enable with same content — user who previously accepted should NOT see it (hash matches)
9. Test with empty title/content/button — verify defaults render ("Terms of Use", "Please accept...", "I Accept")

### Edge Cases:

- User with no settings at all (new user) — should see overlay when enabled
- Admin changes only the title but not content — hash changes, re-acceptance required
- Modal enabled but title and content are both empty — shows defaults, still trackable via hash

## Performance Considerations

- SHA-256 hash computation via `crypto.subtle.digest` is async but essentially instant for short strings
- Hash is only computed once on page load (in `onMount`), not on every render
- No additional API calls beyond the existing settings save on accept

## References

- AccountPending overlay pattern: `src/lib/components/layout/Overlay/AccountPending.svelte`
- PersistentConfig pattern: `backend/open_webui/config.py:1238-1248`
- Admin config API: `backend/open_webui/routers/auths.py:928-1039`
- `/api/config` UI section: `backend/open_webui/main.py:2109-2114`
- User settings update: `src/lib/apis/users/index.ts:273`
- App layout overlay mounting: `src/routes/(app)/+layout.svelte:325-327`
