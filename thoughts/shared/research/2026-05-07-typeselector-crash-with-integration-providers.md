---
date: 2026-05-07T15:10:00+02:00
researcher: Lex Lubbers
git_commit: da45a7bddcffca504531807a004ae376fad2ed56
branch: dev
repository: open-webui
topic: "KB workspace page white-screens with TypeError in TypeSelector when integration_providers is configured"
tags: [research, codebase, typeselector, bits-ui, integration-providers, knowledge-base]
status: complete
last_updated: 2026-05-07
last_updated_by: Lex Lubbers
---

# Research: KB workspace page white-screens with TypeError in TypeSelector when `integration_providers` is configured

**Date**: 2026-05-07T15:10:00+02:00
**Researcher**: Lex Lubbers
**Git Commit**: da45a7bddcffca504531807a004ae376fad2ed56
**Branch**: dev
**Repository**: open-webui

## Research Question

A client deployment using custom ingestion slugs (`INTEGRATION_PROVIDERS`) sees `/workspace/knowledge` never finish loading. DevTools shows:

```
batch.js:249 Uncaught TypeError: (void 0) is not a function
    at children (TypeSelector.svelte:45:36)
```

The page renders fine on deployments without custom ingestion slugs configured. What is causing this, and why is it gated on the `integration_providers` configuration?

## Summary

**Root cause**: `src/lib/components/workspace/common/TypeSelector.svelte` uses the deprecated **bits-ui v1** Select API, but the project has **bits-ui v2.16.3** installed. In v2, `Select.Value` was removed entirely — the value is meant to be rendered manually inside `Select.Trigger`'s children snippet. Because the Svelte template references `<Select.Value …/>` (a property that does not exist on the `Select` namespace export), Svelte 5 calls `undefined` as a component constructor, which throws `(void 0) is not a function`. The error originates from inside the `Select.Trigger`'s `children` snippet — that's why the stack frame is named `children` and the source-map points to the `Select.Value` block on lines 43-46 of TypeSelector.svelte.

**Why only with `integration_providers`**: `Knowledge.svelte:358` wraps `<TypeSelector>` in `{#if Object.keys($config?.integration_providers ?? {}).length > 0}`. When no providers are configured, the broken component never mounts. As soon as any custom ingestion slug is added, the component mounts and crashes the entire Knowledge page render.

**Fix path**: The codebase already has a custom wrapper `src/lib/components/common/Select.svelte` (used by `ViewSelector.svelte` and ~20 other places) that exposes a slot-based API and works fine with the project's Svelte 5 + bits-ui v2 stack. Migrate `TypeSelector` to that wrapper to match `ViewSelector` (the sibling component sitting directly next to it in the same toolbar — see `Knowledge.svelte:349-365`). This is also a one-file change and avoids any direct bits-ui API surface.

The existing fallback `provider?.name ?? slug` (TypeSelector lines 22-27) is an unrelated, earlier defensive fix and is **not** the source of this crash — `name` is always present anyway, because `main.py:2706` accesses `p['name']` with a hard subscript and would 500 the `/api/config` call before any `null` ever reached the frontend.

## Detailed Findings

### The component using the wrong API

**File**: `src/lib/components/workspace/common/TypeSelector.svelte`

```svelte
<script lang="ts">
    import { Select } from 'bits-ui';
    …
    $: items = [
        { value: '', label: $i18n.t('All Types') },
        { value: 'local', label: $i18n.t('Local') },
        ...($config?.features?.enable_onedrive_integration
            ? [{ value: 'onedrive', label: 'OneDrive' }]
            : []),
        ...Object.entries($config?.integration_providers ?? {}).map(([slug, provider]) => ({
            value: slug,
            label: ((provider as { name?: string } | null)?.name ?? slug) as string
        }))
    ];
</script>

<Select.Root
    selected={items.find((item) => item.value === value)}     <!-- v1 API -->
    {items}
    onSelectedChange={(selectedItem) => {                      <!-- v1 API -->
        value = selectedItem.value;
        onChange(value);
    }}
>
    <Select.Trigger …>
        <Select.Value                                          <!-- DOES NOT EXIST IN v2 -->
            class="…"
            placeholder={$i18n.t('All Types')}
        />
        <ChevronDown … />
    </Select.Trigger>

    <Select.Content …>
        {#each items as item}
            <Select.Item value={item.value} label={item.label}>
                {item.label}
                …
            </Select.Item>
        {/each}
    </Select.Content>
</Select.Root>
```

References:
- `src/lib/components/workspace/common/TypeSelector.svelte:1-72`

### Why this throws `(void 0) is not a function at children`

**bits-ui v2.16.3** is installed (`package.json` declares `"bits-ui": "^2.0.0"`, `package-lock.json` resolves it to `2.16.3`, and `node_modules/bits-ui/package.json` confirms `"version": "2.16.3"`).

The v2 Select export surface, from `node_modules/bits-ui/dist/bits/select/exports.d.ts`:

```ts
export { default as Root } from "./components/select.svelte";
export { default as Content } from "./components/select-content.svelte";
export { default as ContentStatic } from "./components/select-content-static.svelte";
export { default as Item } from "./components/select-item.svelte";
export { default as Group } from "./components/select-group.svelte";
export { default as GroupHeading } from "./components/select-group-heading.svelte";
export { default as Trigger } from "./components/select-trigger.svelte";
export { default as Portal } from "../utilities/portal/portal.svelte";
export { default as Viewport } from "./components/select-viewport.svelte";
export { default as ScrollUpButton } from "./components/select-scroll-up-button.svelte";
export { default as ScrollDownButton } from "./components/select-scroll-down-button.svelte";
```

There is **no `Value` export**. So `Select.Value` evaluates to `undefined` at runtime.

Inside `Select.Trigger`, the v2 component renders its slot via a snippet:

```svelte
<!-- node_modules/bits-ui/dist/bits/select/components/select-trigger.svelte -->
<button {...mergedProps}>
    {@render children?.()}
</button>
```

Svelte 5 compiles the parent's `<Select.Trigger> … <Select.Value …/> … </Select.Trigger>` block into a `children` snippet that, when rendered, instantiates `Select.Value`. Because `Select.Value` is `undefined`, you get `TypeError: (void 0) is not a function`. The stack frame is named `children` because that is the name of the snippet being executed, and the source-map points back to the `Select.Value` block on lines 43-46 of TypeSelector.svelte (column 36 lands inside the Select.Value markup region — the source-map granularity is approximate).

References:
- `node_modules/bits-ui/package.json` (version 2.16.3)
- `node_modules/bits-ui/dist/bits/select/exports.d.ts:1-12`
- `node_modules/bits-ui/dist/bits/select/components/select-trigger.svelte:30-38`
- `node_modules/bits-ui/dist/bits/select/components/select.svelte:78-80` (Root also renders via `{@render children?.()}`)
- `node_modules/bits-ui/dist/bits/select/components/select-item.svelte:40-46` (Item also renders via `{@render children?.()}`)

In addition to the missing `Select.Value`, several other props are wrong for v2 and would surface as warnings or runtime issues even if `Value` did exist:

| Used in TypeSelector | bits-ui v2 expects |
| -------------------- | ------------------ |
| `selected={…}` | `value={…}` (with `bind:value`) |
| `onSelectedChange={…}` | `onValueChange={…}` |
| (missing) | `type="single"` is required |
| Default slot content | Implicit `children` snippet (Svelte 5 auto-promotes default slot content, so this part still works) |

References:
- `node_modules/bits-ui/dist/bits/select/components/select.svelte:10-26` (v2 Root prop types)

### Why the page only crashes when `integration_providers` is set

`src/lib/components/workspace/Knowledge.svelte:358-365` only mounts the broken component when at least one provider is configured:

```svelte
<ViewSelector
    bind:value={viewOption}
    onChange={…}
/>

{#if Object.keys($config?.integration_providers ?? {}).length > 0}
    <TypeSelector
        bind:value={typeFilter}
        onChange={async () => { await tick(); }}
    />
{/if}
```

So:
- **No `INTEGRATION_PROVIDERS` configured** → the `{#if …}` guard is false → `<TypeSelector>` never mounts → no crash.
- **Any custom ingestion slug configured** (the client's "octobox" deployment) → `<TypeSelector>` mounts → `Select.Value` resolves to `undefined` → `(void 0) is not a function` thrown synchronously during render → the entire `+page.svelte` for `/workspace/knowledge` fails to render → page stays on the loading spinner.

References:
- `src/lib/components/workspace/Knowledge.svelte:35` (import)
- `src/lib/components/workspace/Knowledge.svelte:358-365` (gated mount)

### How `integration_providers` reaches the frontend

`backend/open_webui/main.py:2704-2711`:

```python
'integration_providers': {
    slug: {
        'name': p['name'],
        'badge_type': p.get('badge_type', 'info'),
        'max_files_per_kb': p.get('max_files_per_kb', 250),
    }
    for slug, p in (app.state.config.INTEGRATION_PROVIDERS or {}).items()
},
```

`name` is read with a hard subscript `p['name']`, so a misconfigured provider (no `name`) would 500 `/api/config`, not produce a frontend item with `label === undefined`. The defensive fallback at `TypeSelector.svelte:22-27` (`provider?.name ?? slug`) is from a previous fix and is unrelated to the current symptom — it stays correct, but it is not why the page is broken now. The crash is structural to the bits-ui v1→v2 mismatch and triggers regardless of what's in each provider object.

References:
- `backend/open_webui/main.py:2704-2711`

### The pattern that already works in this codebase

`src/lib/components/workspace/common/ViewSelector.svelte` is the sibling component rendered directly above `TypeSelector` in the same toolbar. It does **not** import bits-ui — it uses the project's own wrapper:

```svelte
<script lang="ts">
    import Select from '$lib/components/common/Select.svelte';
    …
</script>

<Select bind:value {items} {placeholder} … onChange={() => onChange(value)}>
    <svelte:fragment slot="trigger" let:selectedLabel> … </svelte:fragment>
    <svelte:fragment slot="item" let:item let:selected> … </svelte:fragment>
</Select>
```

The wrapper in `src/lib/components/common/Select.svelte` is a plain Svelte 4-style slot-based component built on a `<button>` + portal'd `<div>`, with no bits-ui dependency. It is used by ~20+ components across the codebase (model selector, admin tabs, view selectors, etc.) and works correctly with the current Svelte 5 + bits-ui v2 stack.

Migrating `TypeSelector` to this wrapper is the smallest, safest fix and brings it in line with `ViewSelector`. No new dependency surface, no bits-ui v2 migration risk for the rest of the codebase.

References:
- `src/lib/components/workspace/common/ViewSelector.svelte:1-44`
- `src/lib/components/common/Select.svelte:1-138`

### Sanity check: no other broken bits-ui Select usages

Grepping the entire `src/` tree for `from 'bits-ui'` plus `Select.Root|Select.Item` shows that `TypeSelector.svelte` is the **only** file using the bits-ui Select component directly. Other bits-ui imports (`DropdownMenu`, `LinkPreview`, `Switch`, `Pagination`) are different components with their own (independent) v2 migration status. No additional pages are at risk from this specific bug.

## Code References

- `src/lib/components/workspace/common/TypeSelector.svelte:1-72` — the broken component (v1 API on a v2 install)
- `src/lib/components/workspace/Knowledge.svelte:358-365` — `{#if integration_providers …}` gate explaining why the bug is config-conditional
- `backend/open_webui/main.py:2704-2711` — backend → `/api/config` shape for `integration_providers`
- `src/lib/components/workspace/common/ViewSelector.svelte:1-44` — working sibling, target pattern for the fix
- `src/lib/components/common/Select.svelte:1-138` — the project's own wrapper to migrate to
- `node_modules/bits-ui/dist/bits/select/exports.d.ts:1-12` — v2 export surface (no `Value`)
- `node_modules/bits-ui/dist/bits/select/components/select-trigger.svelte:30-38` — `{@render children?.()}` site of the failed call
- `node_modules/bits-ui/package.json` — installed version `2.16.3`

## Architecture Insights

- **Two parallel "Select" stacks live in this repo.** The native wrapper at `src/lib/components/common/Select.svelte` is the de facto standard (used by `ViewSelector` and many others). bits-ui's primitive Select is used in only one place — `TypeSelector.svelte` — and is locked to a deprecated v1 API. Future selector components should follow the wrapper pattern; reaching for bits-ui Select primitives here is a footgun until/unless someone migrates the rest of the codebase to the v2 API.
- **Render-time crashes in feature-flagged components are silent on default deployments.** The `{#if Object.keys($config?.integration_providers ?? {}).length > 0}` guard hid this regression from every deployment that hasn't enabled custom ingestion slugs. Smoke tests for the Knowledge workspace should ideally cover both `integration_providers={}` and `integration_providers={…some slug…}` shapes — otherwise client-only configurations (octobox here) become the canary.
- **bits-ui v2 is a hard breaking change from v1.** The `^2.0.0` constraint in `package.json` was likely picked up during an upstream merge, and any code that wasn't migrated at that time is still v1-shaped. Worth a quick sweep through any other `bits-ui` callers if more breakage shows up.

## Recommended Fix (next step, not done in this research)

Replace the body of `src/lib/components/workspace/common/TypeSelector.svelte` with a `ViewSelector`-style implementation that uses `$lib/components/common/Select.svelte`. Pseudocode:

```svelte
<script lang="ts">
    import { getContext } from 'svelte';
    import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
    import Check from '$lib/components/icons/Check.svelte';
    import Select from '$lib/components/common/Select.svelte';
    import { config } from '$lib/stores';

    const i18n = getContext('i18n');
    export let value = '';
    export let onChange: (value: string) => void = () => {};

    $: items = [
        { value: '', label: $i18n.t('All Types') },
        { value: 'local', label: $i18n.t('Local') },
        ...($config?.features?.enable_onedrive_integration
            ? [{ value: 'onedrive', label: 'OneDrive' }]
            : []),
        ...Object.entries($config?.integration_providers ?? {}).map(([slug, provider]) => ({
            value: slug,
            label: ((provider as { name?: string } | null)?.name ?? slug) as string
        }))
    ];
</script>

<Select
    bind:value
    {items}
    placeholder={$i18n.t('All Types')}
    triggerClass="relative w-full flex items-center gap-0.5 px-2.5 py-1.5 bg-gray-50 dark:bg-gray-850 rounded-xl"
    onChange={() => onChange(value)}
>
    <svelte:fragment slot="trigger" let:selectedLabel>
        <span class="inline-flex h-input px-0.5 w-full outline-hidden bg-transparent truncate placeholder-gray-400 focus:outline-hidden">
            {selectedLabel}
        </span>
        <ChevronDown className="size-3.5" strokeWidth="2.5" />
    </svelte:fragment>

    <svelte:fragment slot="item" let:item let:selected>
        {item.label}
        <div class="ml-auto {selected ? '' : 'invisible'}">
            <Check />
        </div>
    </svelte:fragment>
</Select>
```

This is a one-file change, ~50 lines, no behavior changes for the existing values (`''`, `'local'`, `'onedrive'`, dynamic slugs).

## Open Questions

- Should we sweep the rest of the codebase for any other lingering bits-ui v1 patterns now, or wait for them to surface? `TypeSelector` is the only bits-ui `Select` user, but other bits-ui primitives (`DropdownMenu`, `LinkPreview`, `Switch`, `Pagination`) haven't been audited against v2.
- Should the dev/CI smoke test set add a configuration variant with `INTEGRATION_PROVIDERS` populated, so this class of bug fails CI rather than reaching a client deployment?
