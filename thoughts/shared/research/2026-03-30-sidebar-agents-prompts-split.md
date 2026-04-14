---
date: 2026-03-30T17:52:00+02:00
researcher: Claude
git_commit: cdcfcc8a2
branch: feat/logos
repository: Gradient-DS/open-webui
topic: 'Split Agents and Prompts into separate sidebar items (like Knowledge)'
tags: [research, codebase, sidebar, agents, prompts, knowledge, workspace, navigation]
status: complete
last_updated: 2026-03-30
last_updated_by: Claude
---

# Research: Split Agents and Prompts into Separate Sidebar Items

**Date**: 2026-03-30T17:52:00+02:00
**Researcher**: Claude
**Git Commit**: cdcfcc8a2
**Branch**: feat/logos
**Repository**: Gradient-DS/open-webui

## Research Question

What would it take to split "Agents & prompts" into separate sidebar items (like Knowledge), each with direct one-click access and no workspace tab ribbon? Choose logical icons different from the current squares-plus icon.

## Summary

The change is **minimal and well-isolated** — it touches only 2 files, both of which are already customized. The pattern is already established by Knowledge. The key insight: upstream's "Agents & prompts" sidebar link and workspace tab ribbon are a single upstream pattern. We replace the combined link with two individual links (following Knowledge's exact pattern) and extend the tab ribbon exclusion to also hide for agents/prompts routes.

**Files to change:**

1. `src/lib/components/layout/Sidebar.svelte` — replace one combined block with two separate blocks (2 locations: collapsed + expanded)
2. `src/routes/(app)/workspace/+layout.svelte` — extend Knowledge's ribbon-hiding condition to also cover models and prompts routes

**Estimated diff: ~80 lines changed across 2 files.**

## Detailed Findings

### Current State

The sidebar has a single "Agents & prompts" link (`href="/workspace"`) that requires 2 clicks to reach either section (sidebar → workspace → tab). Knowledge already has its own dedicated sidebar link (`href="/workspace/knowledge"`) with the ribbon suppressed.

**Current sidebar order (both collapsed and expanded):**

1. New Chat
2. Search
3. Notes (feature-flagged)
4. Knowledge (feature-flagged) — **direct link, no ribbon**
5. Agents & prompts (combined) — goes to `/workspace`, shows ribbon

### Target State

Replace item 5 with two separate items:

1. New Chat
2. Search
3. Notes
4. Knowledge — `/workspace/knowledge` (unchanged)
5. **Agents** — `/workspace/models` (new, direct link, no ribbon)
6. **Prompts** — `/workspace/prompts` (new, direct link, no ribbon)

The workspace ribbon (Tools, Skills tabs) would still show for `/workspace/tools` and `/workspace/skills` if users navigate there directly, but Agents and Prompts get the Knowledge treatment.

### Change 1: Sidebar.svelte

**File:** `src/lib/components/layout/Sidebar.svelte`

Replace the combined "Agents & prompts" block in **two locations**:

#### Collapsed sidebar (lines 820-855)

Replace the single block with two separate blocks, following the exact Knowledge pattern (lines 796-818):

```svelte
{#if isFeatureEnabled('models') && ($user?.role === 'admin' || $user?.permissions?.workspace?.models)}
	<div class="">
		<Tooltip content={$i18n.t('Agents')} placement="right">
			<a
				class=" cursor-pointer flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition group"
				href="/workspace/models"
				on:click={async (e) => {
					e.stopImmediatePropagation();
					e.preventDefault();
					goto('/workspace/models');
					itemClickHandler();
				}}
				draggable="false"
				aria-label={$i18n.t('Agents')}
			>
				<div class=" self-center flex items-center justify-center size-9">
					<Sparkles className="size-4.5" strokeWidth="2" />
				</div>
			</a>
		</Tooltip>
	</div>
{/if}

{#if isFeatureEnabled('prompts') && ($user?.role === 'admin' || $user?.permissions?.workspace?.prompts)}
	<div class="">
		<Tooltip content={$i18n.t('Prompts')} placement="right">
			<a
				class=" cursor-pointer flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition group"
				href="/workspace/prompts"
				on:click={async (e) => {
					e.stopImmediatePropagation();
					e.preventDefault();
					goto('/workspace/prompts');
					itemClickHandler();
				}}
				draggable="false"
				aria-label={$i18n.t('Prompts')}
			>
				<div class=" self-center flex items-center justify-center size-9">
					<CommandLine className="size-4.5" strokeWidth="2" />
				</div>
			</a>
		</Tooltip>
	</div>
{/if}
```

#### Expanded sidebar (lines 1072-1104)

Same pattern, following Knowledge's expanded block (lines 1051-1070):

```svelte
{#if isFeatureEnabled('models') && ($user?.role === 'admin' || $user?.permissions?.workspace?.models)}
	<div class="px-[0.4375rem] flex justify-center text-gray-800 dark:text-gray-200">
		<a
			id="sidebar-agents-button"
			class="grow flex items-center space-x-3 rounded-2xl px-2.5 py-2 hover:bg-gray-100 dark:hover:bg-gray-900 transition"
			href="/workspace/models"
			on:click={itemClickHandler}
			draggable="false"
			aria-label={$i18n.t('Agents')}
		>
			<div class="self-center">
				<Sparkles className="size-4.5" strokeWidth="2" />
			</div>
			<div class="flex self-center translate-y-[0.5px]">
				<div class=" self-center text-sm font-primary">{$i18n.t('Agents')}</div>
			</div>
		</a>
	</div>
{/if}

{#if isFeatureEnabled('prompts') && ($user?.role === 'admin' || $user?.permissions?.workspace?.prompts)}
	<div class="px-[0.4375rem] flex justify-center text-gray-800 dark:text-gray-200">
		<a
			id="sidebar-prompts-button"
			class="grow flex items-center space-x-3 rounded-2xl px-2.5 py-2 hover:bg-gray-100 dark:hover:bg-gray-900 transition"
			href="/workspace/prompts"
			on:click={itemClickHandler}
			draggable="false"
			aria-label={$i18n.t('Prompts')}
		>
			<div class="self-center">
				<CommandLine className="size-4.5" strokeWidth="2" />
			</div>
			<div class="flex self-center translate-y-[0.5px]">
				<div class=" self-center text-sm font-primary">{$i18n.t('Prompts')}</div>
			</div>
		</a>
	</div>
{/if}
```

#### Import additions

Add to the imports at the top of `Sidebar.svelte`:

```svelte
import Sparkles from '../icons/Sparkles.svelte'; import CommandLine from
'../icons/CommandLine.svelte';
```

### Change 2: Workspace Layout — Hide Ribbon

**File:** `src/routes/(app)/workspace/+layout.svelte`

**Line 85** — extend the condition to also hide the ribbon for models and prompts:

```svelte
// Before (only hides for knowledge):
{#if !$page.url.pathname.includes('/workspace/knowledge')}

// After (hides for knowledge, models, and prompts):
{#if !$page.url.pathname.includes('/workspace/knowledge') && !$page.url.pathname.includes('/workspace/models') && !$page.url.pathname.includes('/workspace/prompts')}
```

This means Tools and Skills will still show the ribbon if users navigate to them through other means (URL, admin panel), but Agents and Prompts get the clean, ribbon-free Knowledge treatment.

### Icon Recommendations

Available icons in `src/lib/components/icons/` that fit semantically:

**For Agents:**
| Icon | Rationale | Visual |
|------|-----------|--------|
| **`Sparkles`** | AI/magic connotation, widely understood as "AI-powered" | ✨ Three sparkle stars |
| `Cube` | Abstract "entity" / building block | 3D cube |
| `Bolt` | Power, automation | Lightning bolt |
| `UserBadgeCheck` | Verified agent/persona | User with checkmark |

**For Prompts:**
| Icon | Rationale | Visual |
|------|-----------|--------|
| **`CommandLine`** | Prompt = command, clear metaphor | `>_` terminal prompt |
| `DocumentPage` | Template/document | Page with lines |
| `ChatBubbleOval` | Conversation prompt | Speech bubble |
| `PencilSquare` | Editing/composing | Already used for "New Chat" |

**Recommended combination: `Sparkles` for Agents + `CommandLine` for Prompts.** These are distinct from each other, from `BookOpen` (Knowledge), from `Note` (Notes), and from `PencilSquare` (New Chat). They communicate the right concepts at a glance.

### Upstream Merge Impact

**Low conflict risk.** Here's why:

1. **Sidebar.svelte** — we are _replacing_ the "Agents & prompts" block (lines 820-855 and 1072-1104). If upstream modifies this block, we'll get a clean merge conflict on exactly those lines — easy to resolve by re-applying our split pattern. If upstream adds new items above or below, git auto-merges cleanly.

2. **Workspace +layout.svelte** — we only extend the condition on line 85. If upstream changes this condition (e.g., adds another exclusion), it's a one-line merge conflict.

3. **No new files, no route changes** — we reuse existing routes (`/workspace/models`, `/workspace/prompts`), existing icon components, and existing feature flags. Nothing new to maintain.

4. **No backend changes** — pure frontend, no API or config changes needed.

**Merge strategy:** Both files are already customized in our fork (Knowledge pattern was added by us). The merge surface is small and the pattern is clear — future upstream changes to the sidebar or workspace layout will conflict at most on the specific blocks we replace.

### Edge Cases

1. **Tools & Skills access** — with the combined link removed, users need another way to reach `/workspace/tools` and `/workspace/skills`. They are still accessible via:
   - Direct URL
   - The tab ribbon (which still shows for those routes)
   - If we want sidebar items for these too, the same pattern applies

2. **Workspace redirect page** — `/workspace/+page.svelte` currently redirects to the first available section. With our change, nobody navigates to `/workspace` anymore (no sidebar link points there), so this redirect page becomes vestigial but harmless. No need to change it.

3. **Page title** — The workspace layout sets `<title>` to "Agents & prompts" (line 75). This shows for all workspace pages. Consider conditionalizing it per sub-route, or just leaving it as-is since it's a browser tab title that users rarely notice.

## Code References

- `src/lib/components/layout/Sidebar.svelte:796-818` — Knowledge sidebar pattern (collapsed)
- `src/lib/components/layout/Sidebar.svelte:820-855` — Current "Agents & prompts" block to replace (collapsed)
- `src/lib/components/layout/Sidebar.svelte:1051-1070` — Knowledge sidebar pattern (expanded)
- `src/lib/components/layout/Sidebar.svelte:1072-1104` — Current "Agents & prompts" block to replace (expanded)
- `src/routes/(app)/workspace/+layout.svelte:85` — Ribbon exclusion condition
- `src/lib/components/icons/Sparkles.svelte` — Recommended Agents icon
- `src/lib/components/icons/CommandLine.svelte` — Recommended Prompts icon
- `src/lib/components/icons/BookOpen.svelte` — Knowledge icon (reference)
- `src/lib/utils/features.ts:33-43` — `isFeatureEnabled()` function

## Architecture Insights

The Knowledge sidebar pattern is a clean, minimal customization:

- **Sidebar:** separate `{#if}` block with its own feature flag check, icon, label, and direct link
- **Layout:** one-line condition extension to hide the ribbon
- **No new routes, components, or config**

Replicating it for Agents and Prompts follows the exact same pattern, keeping the change small, predictable, and easy to reconcile with upstream.

## Open Questions

1. **Tools sidebar item?** — Should Tools also get its own sidebar entry? Currently it would only be accessible via URL or ribbon. If so, what icon? (`Wrench` or `WrenchAlt` would work.)
2. **Icon preference** — Does the user prefer `Sparkles`/`CommandLine` or different icons from the available set?
3. **Page title** — Should the browser tab title be changed per route (e.g., "Agents" vs "Prompts") or kept as "Agents & prompts"?
