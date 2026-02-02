---
date: 2026-02-02T12:00:00+01:00
researcher: claude
git_commit: 7ee9dcefcaef467484bdd79c18b8b3b95db5f2b5
branch: feat/haute-prep
repository: Gradient-DS/open-webui
topic: "How is the welcome greeting banner configured and can it be customized per deployment?"
tags: [research, codebase, i18n, greeting, branding, customization, open-webui]
status: complete
last_updated: 2026-02-02
last_updated_by: claude
---

# Research: Welcome Greeting Customization

**Date**: 2026-02-02
**Researcher**: claude
**Git Commit**: 7ee9dcefcaef467484bdd79c18b8b3b95db5f2b5
**Branch**: feat/haute-prep
**Repository**: Gradient-DS/open-webui

## Research Question

How is the big model banner configured when opening a new chat? Can we customize it per deployment to say "Welkom bij <company>, <user>" — ideally without changing from the upstream Open WebUI repo?

## Summary

The "Hello, \<name\>" greeting is **not configurable** via admin UI or environment variables. It is a hardcoded i18n translation key rendered in two Svelte components. There is **no built-in mechanism** to inject a company name into the greeting without code changes. However, there are several workaround approaches with varying trade-offs.

## Detailed Findings

### How the Greeting Works

Two components render the greeting depending on the `landingPageMode` user setting:

- **`Placeholder.svelte`** (default centered mode) — `src/lib/components/chat/Placeholder.svelte:140-152`
- **`ChatPlaceholder.svelte`** (chat-style left-aligned mode) — `src/lib/components/chat/ChatPlaceholder.svelte:84-88`

Both use identical logic:

```svelte
{#if models[selectedModelIdx]?.name}
    {models[selectedModelIdx]?.name}
{:else}
    {$i18n.t('Hello, {{name}}', { name: $user?.name })}
{/if}
```

**Decision tree:**
1. If a model is selected and has a `.name` → model name is shown (e.g., "GPT-4o")
2. Otherwise → `"Hello, {{name}}"` is shown via i18n with user's display name

The subtitle "How can I help you today?" follows similar logic — it appears when the model has no `info.meta.description`.

### i18n System

- Translation files live in `src/lib/i18n/locales/{lang-code}/translation.json`
- Uses English text as the key (e.g., `"Hello, {{name}}"`)
- Dutch translation (`nl-NL/translation.json:865`): `"Hello, {{name}}": "Hallo, {{name}}"`
- `DEFAULT_LOCALE` env var / PersistentConfig (`backend/open_webui/config.py:1159-1162`) controls the default language per deployment
- The i18n system does NOT have access to deployment-specific variables like `WEBUI_NAME`

### Admin-Configurable Systems (NOT the greeting)

| System | What it controls | Supports user name? | Supports company name? |
|--------|-----------------|---------------------|----------------------|
| **Banners** (admin UI / `WEBUI_BANNERS` env) | Notification banners at top of chat | No | Yes (hardcoded in content) |
| **Default Prompt Suggestions** (admin UI / env) | Clickable suggestion cards below greeting | No | No |
| **Per-model metadata** (workspace editor) | Model name replaces greeting, description replaces subtitle | No | Yes (as model name) |
| **`WEBUI_NAME`** env var | Page titles, auth page, footer text | No | Yes |

None of these systems control the main "Hello, \<name\>" greeting text.

## Options for Customization

### Option 1: Modify Dutch Translation File (Minimal Code Change)

Edit `src/lib/i18n/locales/nl-NL/translation.json:865`:
```json
"Hello, {{name}}": "Welkom bij CompanyName, {{name}}"
```
Set env var: `DEFAULT_LOCALE=nl-NL`

- **Pros**: One-line JSON change, works with existing i18n infrastructure
- **Cons**: Company name hardcoded in translation file, technically a code change (but very minimal divergence from upstream)

### Option 2: Custom en-US Translation Override (Minimal Code Change)

Edit `src/lib/i18n/locales/en-US/translation.json:865`:
```json
"Hello, {{name}}": "Welkom bij CompanyName, {{name}}"
```

- **Pros**: Works without changing locale setting, one-line change
- **Cons**: Affects the English locale globally, same hardcoding issue

### Option 3: Per-Model Approach (No Code Changes)

1. Create a workspace model (proxy) named "Welkom bij CompanyName"
2. Set a description (e.g., "Hoe kan ik u vandaag helpen?")
3. Set it as the default model for users

- **Pros**: Zero code changes, fully admin-configurable
- **Cons**: Greeting shows model name instead of user name (loses personalization), changes the semantic meaning of the heading

### Option 4: Banner Approach (No Code Changes)

Add a banner via Admin > Settings > Interface > Banners with Markdown content like "Welkom bij **CompanyName**!"

- **Pros**: Zero code changes, supports Markdown, admin-configurable
- **Cons**: Different UX (notification-style banner at top, not the main hero greeting), no user name interpolation

### Option 5: Make Greeting Configurable (Code Change, Upstream-Friendly)

Add a new `PersistentConfig` (e.g., `GREETING_TEMPLATE`) that defaults to `"Hello, {{name}}"` and can be overridden via env var or admin UI. Modify Placeholder.svelte and ChatPlaceholder.svelte to read from this config instead of the hardcoded i18n key.

- **Pros**: Proper solution, could be contributed upstream, fully configurable per deployment
- **Cons**: Requires code changes to both frontend and backend, maintenance burden on fork

## Code References

- `src/lib/components/chat/Placeholder.svelte:140-152` — Default greeting rendering
- `src/lib/components/chat/ChatPlaceholder.svelte:84-88` — Chat-mode greeting rendering
- `src/lib/components/chat/Chat.svelte:2473` — Landing page mode routing
- `src/lib/i18n/locales/nl-NL/translation.json:865` — Dutch translation of greeting
- `src/lib/i18n/index.ts:40-68` — i18n initialization with locale detection
- `backend/open_webui/config.py:1159-1162` — `DEFAULT_LOCALE` configuration
- `backend/open_webui/config.py:1721-1737` — Banner model and configuration
- `backend/open_webui/env.py:90-92` — `WEBUI_NAME` environment variable
- `src/lib/components/admin/Settings/Interface.svelte` — Admin interface settings

## Architecture Insights

- The greeting system is tightly coupled to the i18n layer — there's no admin-facing "greeting text" config
- The i18n system uses static JSON files and doesn't support dynamic variables beyond what's passed in the `$i18n.t()` call
- `WEBUI_NAME` is available on the frontend via the `$WEBUI_NAME` store but is never used in the greeting components
- The banner system IS fully configurable but serves a different UX purpose (notifications vs. hero greeting)
- Per-model metadata provides the richest customization (name, description, suggestions) but replaces the greeting rather than augmenting it

## Open Questions

- Would the Open WebUI upstream project accept a PR to make the greeting template configurable?
- Should the company name be dynamic (from `WEBUI_NAME`) or static (hardcoded)?
- Is the per-model approach acceptable UX-wise despite losing user name personalization?
