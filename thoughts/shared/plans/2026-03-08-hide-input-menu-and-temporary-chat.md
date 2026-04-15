# Hide Input Menu (+) Button & Temporary Chat Toggle

## Overview

Two UI cleanup tasks:

1. **Auto-hide the "+" (InputMenu) button** when all its menu items are disabled/hidden, plus add `FEATURE_INPUT_MENU` env var for explicit control
2. **Add `FEATURE_TEMPORARY_CHAT` env var** to hide the temporary chat toggle for all users including admins

## Current State Analysis

### Point 1: InputMenu ("+" button)

**File:** `src/lib/components/chat/MessageInput.svelte:1488-1548`

- `InputMenu` always renders regardless of whether any items are actionable
- Items and their visibility conditions:

| Item             | Visible When                                                               | Actionable When           |
| ---------------- | -------------------------------------------------------------------------- | ------------------------- |
| Upload Files     | Always                                                                     | `fileUploadEnabled`       |
| Capture          | `isFeatureEnabled('capture')`                                              | `fileUploadEnabled`       |
| Attach Webpage   | Always                                                                     | `fileUploadEnabled`       |
| Attach Notes     | `$config?.features?.enable_notes`                                          | Always (navigates to tab) |
| Attach Knowledge | `isFeatureEnabled('knowledge')`                                            | Always (navigates to tab) |
| Reference Chats  | `($chats ?? []).length > 0`                                                | Always (navigates to tab) |
| Google Drive     | `fileUploadEnabled && enable_google_drive_integration`                     | Always                    |
| OneDrive         | `fileUploadEnabled && enable_onedrive_integration && (personal\|business)` | Always                    |

- `fileUploadEnabled` = all selected models support files AND (user is admin OR has `chat.file_upload` permission)

### Point 2: Temporary Chat toggle

**File:** `src/lib/components/chat/Navbar.svelte:123`

- Current condition: `$user?.role === 'user' ? ($user?.permissions?.chat?.temporary ?? true) && !($user?.permissions?.chat?.temporary_enforced ?? false) : true`
- For admin users, the condition always evaluates to `true` — no way to hide it
- No `FEATURE_*` flag exists for this

### Key Discoveries:

- `FEATURE_*` flags are defined in `backend/open_webui/config.py:1620-1637` as env-only constants
- They're exposed via `/api/config` in `backend/open_webui/main.py:2008-2025`
- Frontend checks them via `isFeatureEnabled()` in `src/lib/utils/features.ts:27-35`
- The Feature type union is at `src/lib/utils/features.ts:4-20`

## What We're NOT Doing

- Not changing how individual menu items are gated (Upload Files, Knowledge, etc.)
- Not modifying user permission logic
- Not adding admin UI toggles (these are env-var-only feature flags)

## Implementation Approach

Follow existing `FEATURE_*` pattern exactly: env var → config.py constant → main.py `/api/config` response → frontend `isFeatureEnabled()` check.

## Phase 1: Add FEATURE_INPUT_MENU and FEATURE_TEMPORARY_CHAT env vars

### Changes Required:

#### 1. Backend config

**File:** `backend/open_webui/config.py`
**Changes:** Add two new feature flags after existing `FEATURE_*` block (~line 1637)

```python
FEATURE_INPUT_MENU = os.environ.get("FEATURE_INPUT_MENU", "True").lower() == "true"
FEATURE_TEMPORARY_CHAT = os.environ.get("FEATURE_TEMPORARY_CHAT", "True").lower() == "true"
```

#### 2. API config response

**File:** `backend/open_webui/main.py`
**Changes:** Add to features dict in `/api/config` endpoint (~line 2025)

```python
"feature_input_menu": FEATURE_INPUT_MENU,
"feature_temporary_chat": FEATURE_TEMPORARY_CHAT,
```

Also add the imports at the top where other `FEATURE_*` constants are imported.

#### 3. Frontend Feature type

**File:** `src/lib/utils/features.ts`
**Changes:** Add to the Feature type union (~line 4-20)

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
	| 'models'
	| 'knowledge'
	| 'prompts'
	| 'tools'
	| 'admin_evaluations'
	| 'admin_functions'
	| 'admin_settings'
	| 'input_menu'
	| 'temporary_chat';
```

### Success Criteria:

#### Automated Verification:

- [ ] `npm run build` succeeds
- [ ] Backend starts without errors: `open-webui dev`

#### Manual Verification:

- [ ] `/api/config` response includes `feature_input_menu: true` and `feature_temporary_chat: true` by default
- [ ] Setting `FEATURE_INPUT_MENU=False` returns `feature_input_menu: false`
- [ ] Setting `FEATURE_TEMPORARY_CHAT=False` returns `feature_temporary_chat: false`

---

## Phase 2: Auto-hide InputMenu and apply FEATURE_INPUT_MENU

### Changes Required:

#### 1. InputMenu auto-hide logic

**File:** `src/lib/components/chat/MessageInput.svelte`
**Changes:** Wrap the `<InputMenu>` block (lines 1488-1548) with a condition that checks:

1. `FEATURE_INPUT_MENU` is enabled (global kill switch)
2. At least one menu item would be visible/actionable

```svelte
{#if isFeatureEnabled('input_menu') && showInputMenu}
	<InputMenu ...>...</InputMenu>
{/if}
```

The `showInputMenu` reactive variable should be computed based on whether any item is available:

```typescript
$: showInputMenu =
	fileUploadEnabled || // Upload Files, Capture, Webpage, Google Drive, OneDrive
	($config?.features?.enable_notes ?? false) || // Attach Notes
	isFeatureEnabled('knowledge') || // Attach Knowledge
	($chats ?? []).length > 0; // Reference Chats
```

Note: When `fileUploadEnabled` is true, Upload Files + Attach Webpage are always visible, so the menu has content. When `fileUploadEnabled` is false but Notes/Knowledge/Chats are available, those items are still navigable (they just appear grayed out but still clickable). This matches current behavior.

Import `isFeatureEnabled` at the top of the script block if not already imported.

#### 2. Apply FEATURE_TEMPORARY_CHAT

**File:** `src/lib/components/chat/Navbar.svelte`
**Changes:** Wrap the existing temporary chat condition (line 123) with the feature flag check:

```svelte
{#if isFeatureEnabled('temporary_chat') && ($user?.role === 'user' ? ($user?.permissions?.chat?.temporary ?? true) && !($user?.permissions?.chat?.temporary_enforced ?? false) : true)}
```

Import `isFeatureEnabled` from `$lib/utils/features` if not already imported.

### Success Criteria:

#### Automated Verification:

- [ ] `npm run build` succeeds
- [ ] `npm run check` doesn't introduce new errors beyond existing baseline

#### Manual Verification:

- [ ] Default behavior: "+" button and temporary chat toggle both visible (backwards compatible)
- [ ] `FEATURE_INPUT_MENU=False`: "+" button hidden for all users including admins
- [ ] `FEATURE_TEMPORARY_CHAT=False`: temporary chat toggle hidden for all users including admins
- [ ] With `USER_PERMISSIONS_CHAT_FILE_UPLOAD=False`, `FEATURE_KNOWLEDGE=False`, `ENABLE_NOTES=False`, and no chats: "+" button auto-hides
- [ ] With `USER_PERMISSIONS_CHAT_FILE_UPLOAD=False` but `FEATURE_KNOWLEDGE=True`: "+" button still visible (Knowledge item available)

## References

- Feature flag pattern: `backend/open_webui/config.py:1620-1637`
- Feature flag API: `backend/open_webui/main.py:2008-2025`
- Frontend feature check: `src/lib/utils/features.ts:27-35`
- InputMenu component: `src/lib/components/chat/MessageInput/InputMenu.svelte`
- InputMenu usage: `src/lib/components/chat/MessageInput.svelte:1488-1548`
- Temporary chat toggle: `src/lib/components/chat/Navbar.svelte:123`
