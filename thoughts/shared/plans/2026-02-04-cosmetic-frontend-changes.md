# Cosmetic Frontend Changes Implementation Plan

## Overview

Five cosmetic changes to the Open WebUI frontend: rename "Models" tab to "Agents", rename "Workspace" to "Agents & prompts", move Knowledge out of workspace into its own sidebar item, and add pin-to-sidebar functionality in the agents tab (replacing the eye icon with a pin icon in the model picker too).

## Current State Analysis

- **Workspace** is a top-level sidebar item linking to `/workspace` with tabs: Models, Knowledge, Prompts, Tools
- **Models tab** uses `$i18n.t('Models')` in the workspace tab bar (`+layout.svelte:110`) and component header (`Models.svelte:257,331`)
- **Knowledge** lives exclusively as a workspace tab — no sidebar presence at all
- **Sidebar** has nav items: New Chat, Search, Notes (feature-gated), Workspace (compound feature check)
- **Model pinning** is a per-user client-side preference (`$settings.pinnedModels`) — completely separate from `model.meta.hidden` (server-side visibility)
- **Pin icon** (`Pin.svelte`) and **PinSlash icon** (`PinSlash.svelte`) already exist in the icons directory
- **BookOpen icon** (`BookOpen.svelte`) exists but is unused — good candidate for Knowledge sidebar icon
- **i18n** uses keys as English display text (empty values + `returnEmptyString: false`); Dutch translations have explicit values

### Key Discoveries:
- `Pin.svelte` and `PinSlash.svelte` already exist at `src/lib/components/icons/` — no need to create new icons
- `BookOpen.svelte` exists at `src/lib/components/icons/` and is unused — perfect for Knowledge sidebar icon
- The Notes sidebar item (`Sidebar.svelte:729-751` collapsed, `:964-982` expanded) provides the exact template for adding Knowledge
- `pinModelHandler` is defined in `ModelSelector.svelte:28-39` — needs to be replicated in `Models.svelte`
- The workspace visibility condition (`Sidebar.svelte:753,985`) includes `knowledge` in its OR chain — this needs cleanup after Knowledge moves to sidebar

## Desired End State

1. The workspace tab bar shows: **Agents** | Prompts | Tools (no Knowledge tab)
2. The sidebar link that was "Workspace" now reads **Agents & prompts** (EN) / **Agents & prompts** (NL)
3. **Knowledge** (Kennis) has its own sidebar item between Notes and Agents & prompts, with a BookOpen icon
4. Each model card in the agents tab has a **pin icon** to pin/unpin models to the sidebar
5. The model picker dropdown uses **Pin/PinSlash** icons instead of Eye/EyeSlash for the "Keep in Sidebar" action, relabeled to "Pin to Sidebar" / "Unpin from Sidebar"

### Verification:
- Switch language to Dutch → sidebar shows "Kennis" link, "Agents & prompts" link; workspace tabs show "Agents", "Prompts", "Gereedschappen"
- Switch language to English → sidebar shows "Knowledge" link, "Agents & prompts" link; workspace tabs show "Agents", "Prompts", "Tools"
- Click Knowledge in sidebar → navigates to `/workspace/knowledge`
- Pin a model in the agents tab → model appears in sidebar pinned models section
- Pin a model in the model picker → same pin icon, model appears in sidebar

## What We're NOT Doing

- Not changing URL routes (e.g. `/workspace/models` stays as-is)
- Not renaming "Workspace" in the admin permissions panel (that's a separate concern)
- Not touching the Notes feature (user handles via `.env`)
- Not modifying the `model.meta.hidden` hide/show system — that stays as-is in the model menu
- Not updating other languages beyond EN and NL
- Not changing backend code — all changes are frontend-only

## Implementation Approach

Work in five phases, each independently testable. Phases 1-3 are pure label changes. Phase 4 is structural (moving Knowledge). Phase 5 adds new functionality (pin button). All phases are safe to do incrementally.

---

## Phase 1: i18n Translation Updates

### Overview
Add all new translation keys upfront so they're available when we change the templates.

### Changes Required:

#### 1. English translations
**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add new keys (alphabetically placed):

```json
"Agents": "",
"Agents & prompts": "",
"Pin to Sidebar": "",
"Unpin from Sidebar": "",
```

Note: `"Knowledge"` key already exists (line 966). No new key needed for the sidebar Knowledge item.

#### 2. Dutch translations
**File**: `src/lib/i18n/locales/nl-NL/translation.json`
**Changes**: Add new keys:

```json
"Agents": "Agents",
"Agents & prompts": "Agents & prompts",
"Pin to Sidebar": "Vastpinnen in zijbalk",
"Unpin from Sidebar": "Losmaken van zijbalk",
```

Note: `"Knowledge": "Kennis"` already exists (line 966).

### Success Criteria:

#### Automated Verification:
- [ ] `npm run check` passes (TypeScript)
- [ ] `npm run lint:frontend` passes
- [ ] Translation JSON files are valid JSON: `node -e "require('./src/lib/i18n/locales/en-US/translation.json')"` and same for nl-NL

#### Manual Verification:
- [ ] No visible changes yet (keys not used in templates yet)

---

## Phase 2: Rename "Models" Tab → "Agents"

### Overview
Change the display label of the Models workspace tab from "Models" to "Agents" in three places. URLs remain `/workspace/models`.

### Changes Required:

#### 1. Workspace tab bar label
**File**: `src/routes/(app)/workspace/+layout.svelte`
**Line 110**: Change `{$i18n.t('Models')}` → `{$i18n.t('Agents')}`

```svelte
href="/workspace/models">{$i18n.t('Agents')}</a
```

#### 2. Models component page title
**File**: `src/lib/components/workspace/Models.svelte`
**Lines 255-259**: Change page title

```svelte
<svelte:head>
	<title>
		{$i18n.t('Agents')} • {$WEBUI_NAME}
	</title>
</svelte:head>
```

#### 3. Models component header text
**File**: `src/lib/components/workspace/Models.svelte`
**Line 331**: Change header text

```svelte
{$i18n.t('Agents')}
```

### Success Criteria:

#### Automated Verification:
- [ ] `npm run check` passes
- [ ] `npm run lint:frontend` passes

#### Manual Verification:
- [ ] Workspace tab bar shows "Agents" (EN) or "Agents" (NL) instead of "Models"/"Modellen"
- [ ] Page title in browser tab shows "Agents" instead of "Models"
- [ ] Header inside the agents page shows "Agents" with count

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 3: Rename "Workspace" → "Agents & prompts"

### Overview
Change the sidebar label and workspace page title from "Workspace" to "Agents & prompts".

### Changes Required:

#### 1. Collapsed sidebar tooltip and aria-label
**File**: `src/lib/components/layout/Sidebar.svelte`
**Line 755**: Change tooltip content

```svelte
<Tooltip content={$i18n.t('Agents & prompts')} placement="right">
```

**Line 766**: Change aria-label

```svelte
aria-label={$i18n.t('Agents & prompts')}
```

#### 2. Expanded sidebar text and aria-label
**File**: `src/lib/components/layout/Sidebar.svelte`
**Line 993**: Change aria-label

```svelte
aria-label={$i18n.t('Agents & prompts')}
```

**Line 1013**: Change visible text

```svelte
<div class=" self-center text-sm font-primary">{$i18n.t('Agents & prompts')}</div>
```

#### 3. Workspace page title
**File**: `src/routes/(app)/workspace/+layout.svelte`
**Line 68**: Change page title

```svelte
{$i18n.t('Agents & prompts')} • {$WEBUI_NAME}
```

### Success Criteria:

#### Automated Verification:
- [ ] `npm run check` passes
- [ ] `npm run lint:frontend` passes

#### Manual Verification:
- [ ] Collapsed sidebar shows "Agents & prompts" tooltip on hover
- [ ] Expanded sidebar shows "Agents & prompts" text
- [ ] Browser tab shows "Agents & prompts" when on workspace pages
- [ ] Dutch: shows "Agents & prompts" everywhere

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 4: Move Knowledge to Sidebar

### Overview
Add Knowledge as its own sidebar navigation item (between Notes and Agents & prompts) and remove it from the workspace tab bar. Use the BookOpen icon.

### Changes Required:

#### 1. Add Knowledge to collapsed sidebar (icon strip)
**File**: `src/lib/components/layout/Sidebar.svelte`

Add import for BookOpen at the top of the script section (near other icon imports):
```svelte
import BookOpen from '$lib/components/icons/BookOpen.svelte';
```

After the Notes collapsed item (after line 750, before the Workspace `{#if}` at line 753), insert:

```svelte
{#if isFeatureEnabled('knowledge') && ($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)}
	<div class="">
		<Tooltip content={$i18n.t('Knowledge')} placement="right">
			<a
				class=" cursor-pointer flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition group"
				href="/workspace/knowledge"
				on:click={async (e) => {
					e.stopImmediatePropagation();
					e.preventDefault();

					goto('/workspace/knowledge');
					itemClickHandler();
				}}
				draggable="false"
				aria-label={$i18n.t('Knowledge')}
			>
				<div class=" self-center flex items-center justify-center size-9">
					<BookOpen className="size-4.5" strokeWidth="2" />
				</div>
			</a>
		</Tooltip>
	</div>
{/if}
```

#### 2. Add Knowledge to expanded sidebar
**File**: `src/lib/components/layout/Sidebar.svelte`

After the Notes expanded item (after line 982, before the Workspace `{#if}` at line 985), insert:

```svelte
{#if isFeatureEnabled('knowledge') && ($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)}
	<div class="px-[0.4375rem] flex justify-center text-gray-800 dark:text-gray-200">
		<a
			id="sidebar-knowledge-button"
			class="grow flex items-center space-x-3 rounded-2xl px-2.5 py-2 hover:bg-gray-100 dark:hover:bg-gray-900 transition"
			href="/workspace/knowledge"
			on:click={itemClickHandler}
			draggable="false"
			aria-label={$i18n.t('Knowledge')}
		>
			<div class="self-center">
				<BookOpen className="size-4.5" strokeWidth="2" />
			</div>

			<div class="flex self-center translate-y-[0.5px]">
				<div class=" self-center text-sm font-primary">{$i18n.t('Knowledge')}</div>
			</div>
		</a>
	</div>
{/if}
```

#### 3. Remove `knowledge` from Workspace visibility condition
**File**: `src/lib/components/layout/Sidebar.svelte`

**Collapsed sidebar (line 753)**: Remove `isFeatureEnabled('knowledge') ||` and `$user?.permissions?.workspace?.knowledge ||` from the condition:

Before:
```svelte
{#if (isFeatureEnabled('models') || isFeatureEnabled('knowledge') || isFeatureEnabled('prompts') || isFeatureEnabled('tools')) && ($user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools)}
```

After:
```svelte
{#if (isFeatureEnabled('models') || isFeatureEnabled('prompts') || isFeatureEnabled('tools')) && ($user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools)}
```

**Expanded sidebar (line 985)**: Same change — remove `knowledge` from both OR chains.

#### 4. Remove Knowledge tab from workspace tab bar
**File**: `src/routes/(app)/workspace/+layout.svelte`

Delete lines 114-123 (the entire Knowledge tab block):

```svelte
<!-- DELETE THIS BLOCK -->
{#if isFeatureEnabled('knowledge') && ($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)}
	<a
		class="min-w-fit p-1.5 {$page.url.pathname.includes('/workspace/knowledge')
			? ''
			: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
		href="/workspace/knowledge"
	>
		{$i18n.t('Knowledge')}
	</a>
{/if}
```

#### 5. Update workspace default redirect
**File**: `src/routes/(app)/workspace/+page.svelte`

Remove the knowledge redirect case (lines 14-18). After removal, the redirect chain will be: models → prompts → tools → home.

Before:
```svelte
} else if (
	isFeatureEnabled('knowledge') &&
	($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)
) {
	goto('/workspace/knowledge');
} else if (
```

After:
```svelte
} else if (
```

(The prompts case directly follows the models case.)

#### 6. Keep knowledge route guards
**Files**: The knowledge routes at `src/routes/(app)/workspace/knowledge/` stay unchanged. The `+layout.svelte` still checks the feature flag for `/knowledge` paths, so direct URL access still works and is properly guarded.

### Success Criteria:

#### Automated Verification:
- [ ] `npm run check` passes
- [ ] `npm run lint:frontend` passes

#### Manual Verification:
- [ ] Sidebar shows Knowledge item with book icon between Notes and Agents & prompts
- [ ] Collapsed sidebar shows book icon with "Knowledge" tooltip
- [ ] Clicking Knowledge in sidebar navigates to `/workspace/knowledge`
- [ ] Workspace tab bar no longer shows Knowledge tab
- [ ] `/workspace` redirects to `/workspace/models` (not knowledge)
- [ ] Direct URL `/workspace/knowledge` still works
- [ ] Dutch: sidebar shows "Kennis"

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 5: Add Pin to Sidebar in Agents Tab + Model Picker

### Overview
Add a pin/unpin button to each model card in the agents (workspace models) tab, and replace Eye/EyeSlash with Pin/PinSlash in the model picker dropdown.

### Changes Required:

#### 1. Add pin functionality to workspace Models component
**File**: `src/lib/components/workspace/Models.svelte`

Add imports at top of script (after existing imports around line 14):
```svelte
import { updateUserSettings } from '$lib/apis/users';
import Pin from '../icons/Pin.svelte';
import PinSlash from '../icons/PinSlash.svelte';
```

Add `pinModelHandler` function (after `hideModelHandler`, around line 191):
```typescript
const pinModelHandler = async (modelId) => {
	let pinnedModels = $settings?.pinnedModels ?? [];

	if (pinnedModels.includes(modelId)) {
		pinnedModels = pinnedModels.filter((id) => id !== modelId);
	} else {
		pinnedModels = [...new Set([...pinnedModels, modelId])];
	}

	settings.set({ ...$settings, pinnedModels: pinnedModels });
	await updateUserSettings(localStorage.token, { ui: $settings });
};
```

#### 2. Add pin button to each model card
**File**: `src/lib/components/workspace/Models.svelte`

Inside each model card, add a pin button before the existing action buttons. Insert it at the beginning of the `flex flex-row gap-0.5 items-center` div (line 494, before `{#if shiftKey}`):

```svelte
<Tooltip
	content={($settings?.pinnedModels ?? []).includes(model.id)
		? $i18n.t('Unpin from Sidebar')
		: $i18n.t('Pin to Sidebar')}
>
	<button
		class="self-center w-fit text-sm p-1.5 dark:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
		type="button"
		on:click={(e) => {
			e.stopPropagation();
			pinModelHandler(model.id);
		}}
	>
		{#if ($settings?.pinnedModels ?? []).includes(model.id)}
			<PinSlash className="size-4" />
		{:else}
			<Pin className="size-4" />
		{/if}
	</button>
</Tooltip>
```

#### 3. Replace Eye/EyeSlash with Pin/PinSlash in model picker
**File**: `src/lib/components/chat/ModelSelector/ModelItemMenu.svelte`

Replace Eye/EyeSlash imports (lines 8-10):

Before:
```svelte
import Eye from '$lib/components/icons/Eye.svelte';
import EyeSlash from '$lib/components/icons/EyeSlash.svelte';
```

After:
```svelte
import Pin from '$lib/components/icons/Pin.svelte';
import PinSlash from '$lib/components/icons/PinSlash.svelte';
```

Replace the icons in the dropdown item (lines 65-77):

Before:
```svelte
{#if ($settings?.pinnedModels ?? []).includes(model?.id)}
	<EyeSlash />
{:else}
	<Eye />
{/if}

<div class="flex items-center">
	{#if ($settings?.pinnedModels ?? []).includes(model?.id)}
		{$i18n.t('Hide from Sidebar')}
	{:else}
		{$i18n.t('Keep in Sidebar')}
	{/if}
</div>
```

After:
```svelte
{#if ($settings?.pinnedModels ?? []).includes(model?.id)}
	<PinSlash />
{:else}
	<Pin />
{/if}

<div class="flex items-center">
	{#if ($settings?.pinnedModels ?? []).includes(model?.id)}
		{$i18n.t('Unpin from Sidebar')}
	{:else}
		{$i18n.t('Pin to Sidebar')}
	{/if}
</div>
```

### Success Criteria:

#### Automated Verification:
- [ ] `npm run check` passes
- [ ] `npm run lint:frontend` passes

#### Manual Verification:
- [ ] Each model card in the agents tab shows a pin icon
- [ ] Clicking pin icon on an unpinned model → model appears in sidebar pinned models section
- [ ] Clicking pin icon on a pinned model → PinSlash icon shown, clicking removes from sidebar
- [ ] Model picker dropdown shows Pin icon with "Pin to Sidebar" for unpinned models
- [ ] Model picker dropdown shows PinSlash icon with "Unpin from Sidebar" for pinned models
- [ ] Dutch: shows "Vastpinnen in zijbalk" / "Losmaken van zijbalk"

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Testing Strategy

### Manual Testing Steps:
1. Start dev server: `npm run dev`
2. Log in as admin user
3. **EN check**: Verify sidebar shows "Knowledge" (book icon), "Agents & prompts" — workspace tabs show "Agents", "Prompts", "Tools"
4. **NL check**: Switch to Dutch in settings → sidebar shows "Kennis", "Agents & prompts" — workspace tabs show "Agents", "Prompts", "Gereedschappen"
5. **Knowledge navigation**: Click Knowledge in sidebar → goes to `/workspace/knowledge`
6. **Agents pin**: Go to agents tab → pin a model via pin icon → verify it appears in sidebar
7. **Model picker pin**: Open model picker → click "..." on a model → verify Pin icon and "Pin to Sidebar" label
8. **Unpin flow**: Unpin from agents tab and model picker — verify PinSlash icon and removal from sidebar

## Performance Considerations

None — all changes are cosmetic label swaps and one additional button per model card. No new API calls, no new data fetching.

## References

- Research document: `thoughts/shared/research/2026-02-04-openwebui-cosmetic-frontend-changes.md`
- Existing `pinModelHandler`: `src/lib/components/chat/ModelSelector.svelte:28-39`
- Pin icon: `src/lib/components/icons/Pin.svelte`
- PinSlash icon: `src/lib/components/icons/PinSlash.svelte`
- BookOpen icon: `src/lib/components/icons/BookOpen.svelte`
- Notes sidebar pattern: `src/lib/components/layout/Sidebar.svelte:729-751` (collapsed), `:964-982` (expanded)
