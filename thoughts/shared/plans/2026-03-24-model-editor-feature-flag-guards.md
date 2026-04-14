# Model Editor & Admin Panel Feature Flag Guards — Implementation Plan

## Overview

The model builder (agents) and admin model settings show options (Tools, Skills, TTS Voice, Capabilities, Builtin Tools) unconditionally. These should be hidden when the corresponding feature is disabled in the deployment. Most flags already exist — we just need to check them in the frontend. One new flag (`FEATURE_BUILTIN_TOOLS`) needs to be created end-to-end and exposed in helm.

## Current State Analysis

- `ModelEditor.svelte` renders Tools, Skills, Knowledge, TTS Voice sections with no feature guards
- `Capabilities.svelte` shows all 10 capability checkboxes regardless of deployment config
- `BuiltinTools.svelte` shows all 9 tool checkboxes regardless of deployment config
- Both the workspace model builder and admin `ModelSettingsModal` use these same shared components
- No `FEATURE_BUILTIN_TOOLS` global flag exists anywhere in the codebase
- Helm is missing `featureSkills` and `enableMemories`

### Key Discoveries:

- Backend `get_builtin_tools()` (`utils/tools.py:403-564`) already gates each tool by global `ENABLE_*` flags — frontend just needs to match this
- Both surfaces (model builder + admin settings modal) share the same sub-components, so fixing the components fixes both
- `DEFAULT_CAPABILITIES` in `constants.ts:98-109` defaults everything to `true` — needs to respect config
- The `isFeatureEnabled()` utility (`src/lib/utils/features.ts:30`) already exists for `FEATURE_*` flags
- `$config.features.enable_*` is already the pattern used in `Chat.svelte` and `MessageInput.svelte`

## Desired End State

When a feature is disabled in the deployment:

1. Its capability checkbox is hidden in both the model builder and admin model settings
2. Its builtin tool checkbox is hidden
3. The Tools/Skills/Knowledge/TTS sections are hidden when the feature is globally off
4. `DEFAULT_CAPABILITIES` respects the config — disabled features default to `false` for new models
5. A new `FEATURE_BUILTIN_TOOLS` flag gates the entire builtin tools system
6. All flags are exposed in the helm chart

### How to verify:

- Set `FEATURE_TOOLS=false`, `FEATURE_SKILLS=false`, `FEATURE_VOICE=false`, `FEATURE_BUILTIN_TOOLS=false` in env
- Set `ENABLE_WEB_SEARCH=false`, `ENABLE_IMAGE_GENERATION=false` in env
- Open model builder — Tools, Skills sections should be gone; Web Search and Image Generation checkboxes should be hidden in both Capabilities and Builtin Tools; TTS Voice should be hidden; Builtin Tools capability checkbox should be hidden
- Open admin Settings → Models → Settings (gear icon) — same behavior in the capabilities section

## What We're NOT Doing

- Changing backend enforcement logic (already correct in `get_builtin_tools()`)
- Adding guards to the chat UI (already has them)
- Modifying how capabilities are stored on models (just hiding the UI)
- Adding `ENABLE_MEMORIES` to the admin panel toggle (just exposing in helm)

## Implementation Approach

Import the `config` store directly in shared sub-components and filter items reactively. This matches the existing codebase pattern used in `Chat.svelte`, `MessageInput.svelte`, etc. Because both surfaces share the same components, fixing the components fixes both the model builder and admin modal automatically.

For `DEFAULT_CAPABILITIES`, make it a function that reads from config and returns capabilities with disabled features set to `false`.

---

## Phase 1: Backend — Add `FEATURE_BUILTIN_TOOLS`

### Overview

Create the new global feature flag for builtin tools, following the exact same pattern as all other `FEATURE_*` flags.

### Changes Required:

#### 1. Add flag definition

**File**: `backend/open_webui/config.py`
**After line 1936** (after `FEATURE_TERMINAL_SERVERS`):

```python
FEATURE_BUILTIN_TOOLS = (
    os.environ.get("FEATURE_BUILTIN_TOOLS", "True").lower() == "true"
)
```

#### 2. Add to Feature type and FEATURE_FLAGS dict

**File**: `backend/open_webui/utils/features.py`

Add `"builtin_tools"` to the `Feature` literal type (line 53, before the closing bracket) and add to `FEATURE_FLAGS` dict (line 74, before closing brace):

```python
"builtin_tools": FEATURE_BUILTIN_TOOLS,
```

Import the new constant at the top of the file alongside existing imports.

#### 3. Expose in `/api/config`

**File**: `backend/open_webui/main.py`
**After line 2424** (after `"feature_skills": FEATURE_SKILLS,`):

```python
"feature_builtin_tools": FEATURE_BUILTIN_TOOLS,
```

Import `FEATURE_BUILTIN_TOOLS` from config at the top of the file (alongside existing `FEATURE_*` imports).

#### 4. Gate builtin tools injection

**File**: `backend/open_webui/utils/middleware.py`
**At line 2714-2716**, add the global feature check:

```python
builtin_tools_enabled = (
    model.get("info", {}).get("meta", {}).get("capabilities") or {}
).get("builtin_tools", True)
if (
    metadata.get("params", {}).get("function_calling") == "native"
    and builtin_tools_enabled
    and FEATURE_BUILTIN_TOOLS
):
```

Import `FEATURE_BUILTIN_TOOLS` from `open_webui.config` at the top.

### Success Criteria:

#### Automated Verification:

- [x] Backend starts without errors: `open-webui dev`
- [x] `GET /api/config` response includes `feature_builtin_tools` in `features` when authenticated
- [x] With `FEATURE_BUILTIN_TOOLS=false`, builtin tools are not injected into native FC requests

#### Manual Verification:

- [ ] Setting `FEATURE_BUILTIN_TOOLS=false` env var and restarting confirms the flag is read correctly

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to the next phase.

---

## Phase 2: Frontend — Guard Capabilities.svelte and BuiltinTools.svelte

### Overview

Make the shared sub-components config-aware so they hide options for disabled features. Since both the model builder and admin modal use these components, this fixes both surfaces at once.

### Changes Required:

#### 1. Capabilities.svelte — Filter by config

**File**: `src/lib/components/workspace/Models/Capabilities.svelte`

Import the config store and extend the `visibleCapabilities` filter:

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n = getContext('i18n');

	// Map capability keys to config feature flags
	const capabilityConfigGuards: Record<string, string> = {
		web_search: 'enable_web_search',
		image_generation: 'enable_image_generation',
		code_interpreter: 'enable_code_interpreter',
		builtin_tools: 'feature_builtin_tools'
	};

	const capabilityLabels = {
		// ... unchanged ...
	};

	export let capabilities = {};

	// Hide capabilities when:
	// - file_context: file_upload is disabled
	// - feature-gated capabilities: global config flag is off
	$: visibleCapabilities = Object.keys(capabilityLabels).filter((cap) => {
		if (cap === 'file_context' && !capabilities.file_upload) {
			return false;
		}
		const configKey = capabilityConfigGuards[cap];
		if (configKey && !$config?.features?.[configKey]) {
			return false;
		}
		return true;
	});
</script>
```

#### 2. BuiltinTools.svelte — Filter by config

**File**: `src/lib/components/workspace/Models/BuiltinTools.svelte`

Import the config store and filter the visible tools list:

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n = getContext('i18n');

	// Map tool keys to config feature flags
	const toolConfigGuards: Record<string, string> = {
		memory: 'enable_memories',
		notes: 'enable_notes',
		channels: 'enable_channels',
		knowledge: 'feature_knowledge',
		web_search: 'enable_web_search',
		image_generation: 'enable_image_generation',
		code_interpreter: 'enable_code_interpreter'
	};
	// time and chats have no guard — always available

	const toolLabels = {
		// ... unchanged ...
	};

	const allToolKeys = Object.keys(toolLabels);

	export let builtinTools: Record<string, boolean> = {};

	// Filter to only tools whose global feature is enabled
	$: visibleTools = allToolKeys.filter((tool) => {
		const configKey = toolConfigGuards[tool];
		if (configKey && !$config?.features?.[configKey]) {
			return false;
		}
		return true;
	});

	// Initialize missing keys to true (default enabled)
	$: {
		for (const tool of allToolKeys) {
			if (!(tool in builtinTools)) {
				builtinTools[tool] = true;
			}
		}
	}
</script>

<!-- In template, change {#each allTools as tool} to: -->
{#each visibleTools as tool}
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] `npm run test:frontend` passes

#### Manual Verification:

- [ ] With `ENABLE_WEB_SEARCH=false`: Web Search checkbox hidden in Capabilities AND Builtin Tools
- [ ] With `ENABLE_IMAGE_GENERATION=false`: Image Generation checkbox hidden in both
- [ ] With `FEATURE_BUILTIN_TOOLS=false`: Builtin Tools checkbox hidden in Capabilities
- [ ] With all features enabled: all checkboxes visible (no regression)
- [ ] Verify in both model builder AND admin → Models → Settings modal

**Implementation Note**: After completing this phase, pause for manual verification in both surfaces.

---

## Phase 3: Frontend — Guard ModelEditor.svelte sections

### Overview

Wrap the Tools, Skills, Knowledge, and TTS Voice sections in feature flag checks.

### Changes Required:

#### 1. ModelEditor.svelte — Add config import and guards

**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`

Add `config` to the existing import from `$lib/stores` (line 5):

```svelte
import {(models, tools, functions, user, config)} from '$lib/stores';
```

Wrap sections in `{#if}` guards:

**Knowledge** (line 742-744):

```svelte
{#if $config?.features?.feature_knowledge !== false}
	<div class="my-4">
		<Knowledge bind:selectedItems={knowledge} />
	</div>
{/if}
```

**Tools** (line 746-748):

```svelte
{#if $config?.features?.feature_tools !== false}
	<div class="my-4">
		<ToolsSelector bind:selectedToolIds={toolIds} tools={$tools ?? []} />
	</div>
{/if}
```

**Skills** (line 750-752):

```svelte
{#if $config?.features?.feature_skills}
	<div class="my-4">
		<SkillsSelector bind:selectedSkillIds={skillIds} />
	</div>
{/if}
```

Note: Skills uses truthy check (no `!== false`) because `FEATURE_SKILLS` defaults to `false` upstream.

**TTS Voice** (line 819-831):

```svelte
{#if $config?.features?.feature_voice !== false && $config?.audio?.tts?.engine}
	<div class="my-4">
		<!-- TTS Voice input unchanged -->
	</div>
{/if}
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] `npm run test:frontend` passes

#### Manual Verification:

- [ ] With `FEATURE_TOOLS=false`: Tools section hidden in model builder
- [ ] With `FEATURE_SKILLS=false`: Skills section hidden
- [ ] With `FEATURE_VOICE=false`: TTS Voice section hidden
- [ ] With `FEATURE_KNOWLEDGE=false`: Knowledge section hidden
- [ ] With all features enabled: all sections visible

**Implementation Note**: Pause for manual verification.

---

## Phase 4: Frontend — Config-aware DEFAULT_CAPABILITIES

### Overview

Make `DEFAULT_CAPABILITIES` respect the deployment config so that new models don't default to having disabled features enabled.

### Changes Required:

#### 1. Add helper function

**File**: `src/lib/constants.ts`

Keep `DEFAULT_CAPABILITIES` as-is (it's used as a static fallback). Add a new function:

```typescript
import { get } from 'svelte/store';
import { config } from '$lib/stores';

/**
 * Returns DEFAULT_CAPABILITIES with disabled features set to false.
 * Used when initializing capabilities for new models.
 */
export function getDefaultCapabilities() {
	const $config = get(config);
	const features = $config?.features ?? {};
	return {
		...DEFAULT_CAPABILITIES,
		web_search: features.enable_web_search !== false ? DEFAULT_CAPABILITIES.web_search : false,
		image_generation:
			features.enable_image_generation !== false ? DEFAULT_CAPABILITIES.image_generation : false,
		code_interpreter:
			features.enable_code_interpreter !== false ? DEFAULT_CAPABILITIES.code_interpreter : false,
		builtin_tools:
			features.feature_builtin_tools !== false ? DEFAULT_CAPABILITIES.builtin_tools : false
	};
}
```

#### 2. Use in ModelEditor.svelte

**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`

Change line 98 from:

```svelte
let capabilities = { ...DEFAULT_CAPABILITIES };
```

to:

```svelte
let capabilities = getDefaultCapabilities();
```

Import `getDefaultCapabilities` alongside `DEFAULT_CAPABILITIES`.

#### 3. Use in ModelSettingsModal.svelte

**File**: `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte`

In the `loadConfig` function (lines 102 and 106), replace `{ ...DEFAULT_CAPABILITIES }` with `getDefaultCapabilities()`.

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] `npm run test:frontend` passes

#### Manual Verification:

- [ ] With `ENABLE_WEB_SEARCH=false`: new model defaults to `web_search: false`
- [ ] With `FEATURE_BUILTIN_TOOLS=false`: new model defaults to `builtin_tools: false`
- [ ] Existing models with saved capabilities are not affected (loaded from model data, not defaults)

**Implementation Note**: Pause for manual verification.

---

## Phase 5: Frontend — TypeScript types

### Overview

Add missing fields to the `Config.features` type so IDE autocomplete works for the new guards.

### Changes Required:

#### 1. Update Config type

**File**: `src/lib/stores/index.ts`

Add to the `features` type (after line 292):

```typescript
enable_channels?: boolean;
enable_notes?: boolean;
enable_code_interpreter?: boolean;
enable_code_execution?: boolean;
feature_skills?: boolean;
feature_builtin_tools?: boolean;
```

#### 2. Update Feature type

**File**: `src/lib/utils/features.ts`

Add `'builtin_tools'` to the `Feature` union type (line 23, before the semicolon):

```typescript
| 'builtin_tools'
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds

---

## Phase 6: Helm — Expose new flags

### Overview

Add `featureBuiltinTools`, `featureSkills`, and `enableMemories` to the helm chart.

### Changes Required:

#### 1. values.yaml

**File**: `helm/open-webui-tenant/values.yaml`

Add after `featureTools` (line 205):

```yaml
featureSkills: 'False'
featureBuiltinTools: 'False'
```

Add in the appropriate section (near other `ENABLE_*` flags):

```yaml
enableMemories: 'true'
```

#### 2. configmap.yaml

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

Add after `FEATURE_TOOLS` (line 117):

```yaml
FEATURE_SKILLS: { { .Values.openWebui.config.featureSkills | quote } }
FEATURE_BUILTIN_TOOLS: { { .Values.openWebui.config.featureBuiltinTools | quote } }
```

Add in the appropriate section:

```yaml
ENABLE_MEMORIES: { { .Values.openWebui.config.enableMemories | quote } }
```

### Success Criteria:

#### Automated Verification:

- [x] `helm template` renders correctly with new values
- [x] Default values produce expected env vars

---

## Testing Strategy

### Manual Testing Steps:

1. Start backend with various flags disabled: `FEATURE_TOOLS=false FEATURE_SKILLS=false FEATURE_VOICE=false FEATURE_BUILTIN_TOOLS=false ENABLE_WEB_SEARCH=false ENABLE_IMAGE_GENERATION=false open-webui dev`
2. Open model builder (create new agent) — verify hidden sections
3. Open admin → Models → Settings (gear) → Model Capabilities — verify hidden checkboxes
4. Open admin → Models → click a model to edit — verify hidden sections
5. Re-enable all flags and verify nothing is missing (no regression)
6. Create a new model with features disabled — verify defaults are `false` for disabled features
7. Edit an existing model that was created with features enabled — verify its saved capabilities are preserved

## References

- Research: `thoughts/shared/research/2026-03-24-model-editor-feature-flag-guards.md`
- Backend builtin tools gating: `backend/open_webui/utils/tools.py:403-564`
- Frontend feature flag utility: `src/lib/utils/features.ts`
