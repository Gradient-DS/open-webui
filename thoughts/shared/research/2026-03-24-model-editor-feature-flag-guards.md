---
date: 2026-03-24T18:00:00+01:00
researcher: Claude
git_commit: 39344352f
branch: fix/test-bugs-daan-260323
repository: open-webui
topic: 'Model editor and admin panel sections need feature flag guards for deployment-specific visibility'
tags:
  [research, codebase, model-editor, admin-panel, feature-flags, capabilities, builtin-tools, helm]
status: complete
last_updated: 2026-03-24
last_updated_by: Claude
last_updated_note: 'Expanded to cover builtin tools global flag (new), admin panel model config, helm chart exposure, and both surfaces (model builder + admin)'
---

# Research: Model Editor & Admin Panel Feature Flag Guards

**Date**: 2026-03-24T18:00:00+01:00
**Researcher**: Claude
**Git Commit**: 39344352f
**Branch**: fix/test-bugs-daan-260323
**Repository**: open-webui

## Research Question

The model creation page ("agent builder") and admin model settings panel show options for Tools, Skills, TTS Voice, Capabilities (Web Search, Image Generation, Code Interpreter), Builtin Tools, etc. unconditionally. These should be hidden when the corresponding feature is disabled in that deployment. What env variables / config flags exist, how should they guard the UI, and what new flags are needed (particularly for builtin tools)?

## Summary

1. **Most flags already exist** — Tools, Skills, Voice, Web Search, Image Generation, Code Interpreter, Memories, Notes, Channels, Knowledge all have existing feature flags exposed to the frontend via `$config.features.*`. The frontend just needs to check them.

2. **Builtin tools has NO global flag** — There is no `FEATURE_BUILTIN_TOOLS` or `ENABLE_BUILTIN_TOOLS` env variable. The builtin tools system is currently only controlled per-model via `model.info.meta.capabilities.builtin_tools` and `model.info.meta.builtinTools` (category toggles). A new global `FEATURE_BUILTIN_TOOLS` flag is needed.

3. **Two surfaces need the same guards** — Both the workspace model builder (agents) at `ModelEditor.svelte` and the admin model settings modal at `ModelSettingsModal.svelte` use the same shared sub-components (`Capabilities.svelte`, `BuiltinTools.svelte`, `DefaultFeatures.svelte`). Fixing the sub-components fixes both surfaces.

4. **Helm chart needs the new flag** — The existing `FEATURE_*` flags are already exposed in the helm chart's `values.yaml` and `configmap.yaml`. The new `FEATURE_BUILTIN_TOOLS` needs to be added there too.

## Detailed Findings

### Affected Surfaces

Both surfaces share the same sub-components, so changes propagate to both:

| Surface                    | Entry file                                                                                  | Renders                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **Model builder (agents)** | `src/routes/(app)/workspace/models/create/+page.svelte` → `ModelEditor.svelte`              | Tools, Skills, Knowledge, Capabilities, DefaultFeatures, BuiltinTools, TTS Voice |
| **Admin model config**     | `src/lib/components/admin/Settings/Models.svelte` → `ModelSettingsModal.svelte`             | Capabilities, DefaultFeatures, BuiltinTools (global defaults)                    |
| **Admin per-model edit**   | `src/lib/components/admin/Settings/Models.svelte` → `ModelEditor.svelte` (with `edit=true`) | Same as model builder                                                            |

### Current State: What's Always Shown (No Guards)

#### ModelEditor.svelte (model builder + admin per-model edit)

| Section   | Component        | Line    | Should guard with                         |
| --------- | ---------------- | ------- | ----------------------------------------- |
| Knowledge | `Knowledge`      | 743     | `feature_knowledge`                       |
| Tools     | `ToolsSelector`  | 747     | `feature_tools`                           |
| Skills    | `SkillsSelector` | 751     | `feature_skills`                          |
| TTS Voice | inline `<input>` | 819-831 | `feature_voice` (+ TTS engine configured) |

#### ModelSettingsModal.svelte (admin global defaults)

The modal at `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte` renders `Capabilities` (line 313), `DefaultFeatures` (line 328), and `BuiltinTools` (line 338) with the same lack of feature flag guards.

#### Capabilities.svelte (shared — both surfaces)

All capability checkboxes always render. These should be hidden when globally disabled:

| Capability         | Guard                     |
| ------------------ | ------------------------- |
| `web_search`       | `enable_web_search`       |
| `image_generation` | `enable_image_generation` |
| `code_interpreter` | `enable_code_interpreter` |

#### BuiltinTools.svelte (shared — both surfaces)

All 9 tool checkboxes always render. These should be hidden when globally disabled:

| Tool               | Guard                                 |
| ------------------ | ------------------------------------- |
| `time`             | _(always available — no flag needed)_ |
| `memory`           | `enable_memories`                     |
| `chats`            | _(always available — no flag needed)_ |
| `notes`            | `enable_notes`                        |
| `knowledge`        | `feature_knowledge`                   |
| `channels`         | `enable_channels`                     |
| `web_search`       | `enable_web_search`                   |
| `image_generation` | `enable_image_generation`             |
| `code_interpreter` | `enable_code_interpreter`             |

Note: The backend `get_builtin_tools()` in `utils/tools.py:403-564` already applies these exact same guards server-side (e.g., lines 479-484 check `ENABLE_WEB_SEARCH` + model capability + features). The frontend just needs to match this so users don't see options that will never work.

#### DefaultFeatures.svelte

Already receives `availableFeatures` as a prop filtered from capabilities. If capabilities are properly filtered, this component will automatically only show enabled features. **No changes needed here.**

### Available Config Flags (Already Exposed to Frontend)

All flags below are returned by `GET /api/config` (main.py:2379-2449) and available in `$config.features.*`:

| Flag                      | Config Key                | Type                   | Default (upstream) | Default (helm)  | Controls            |
| ------------------------- | ------------------------- | ---------------------- | ------------------ | --------------- | ------------------- |
| `FEATURE_TOOLS`           | `feature_tools`           | FEATURE\_\* (env-only) | `true`             | `false`         | Tools workspace     |
| `FEATURE_SKILLS`          | `feature_skills`          | FEATURE\_\* (env-only) | **`false`**        | _(not in helm)_ | Skills feature      |
| `FEATURE_VOICE`           | `feature_voice`           | FEATURE\_\* (env-only) | `true`             | `false`         | Voice/audio         |
| `FEATURE_KNOWLEDGE`       | `feature_knowledge`       | FEATURE\_\* (env-only) | `true`             | `true`          | Knowledge workspace |
| `ENABLE_WEB_SEARCH`       | `enable_web_search`       | PersistentConfig       | `false`            | `true`          | Web search          |
| `ENABLE_IMAGE_GENERATION` | `enable_image_generation` | PersistentConfig       | `false`            | `false`         | Image generation    |
| `ENABLE_CODE_INTERPRETER` | `enable_code_interpreter` | PersistentConfig       | `true`             | `false`         | Code interpreter    |
| `ENABLE_MEMORIES`         | `enable_memories`         | PersistentConfig       | `true`             | _(not in helm)_ | Memory feature      |
| `ENABLE_CHANNELS`         | `enable_channels`         | PersistentConfig       | `true`             | `false`         | Channels            |
| `ENABLE_NOTES`            | `enable_notes`            | PersistentConfig       | `true`             | `true`          | Notes               |
| `AUDIO_TTS_ENGINE`        | `audio.tts.engine`        | PersistentConfig       | `""` (disabled)    | _(not in helm)_ | TTS engine          |

### New Flag Needed: `FEATURE_BUILTIN_TOOLS`

**Current state:** No global flag exists. Builtin tools are controlled only per-model:

- `model.info.meta.capabilities.builtin_tools` — capability toggle (default: `true`)
- `model.info.meta.builtinTools` — dict of category toggles (default: all `true`)
- Backend `get_builtin_tools()` (`utils/tools.py:403`) gates individual tools by global `ENABLE_*` flags, but the builtin tools _system itself_ has no global on/off switch.

**What's needed:**

1. **Backend** (`config.py`): Add `FEATURE_BUILTIN_TOOLS` as a FEATURE\_\* env-only flag (default `true`)
2. **Backend** (`main.py`): Expose in `/api/config` features dict
3. **Backend** (`utils/features.py`): Add to `FEATURE_FLAGS` dict for `require_feature()` dependency
4. **Backend** (`utils/middleware.py:2716`): Check `FEATURE_BUILTIN_TOOLS` before injecting builtin tools
5. **Frontend** (`stores/index.ts`): Add to Config type
6. **Frontend** (`ModelEditor.svelte`): Guard the `{#if capabilities.builtin_tools}` block
7. **Frontend** (`Capabilities.svelte`): Hide `builtin_tools` checkbox when feature disabled
8. **Helm** (`values.yaml`): Add `featureBuiltinTools` with default
9. **Helm** (`configmap.yaml`): Map to `FEATURE_BUILTIN_TOOLS` env var

### Helm Chart Integration

The helm chart at `helm/open-webui-tenant/` already exposes all `FEATURE_*` flags:

**values.yaml** (lines 192-213): All `FEATURE_*` flags under `openWebui.config.feature*`
**configmap.yaml** (lines 105-126): Maps values to env vars

Current helm `FEATURE_*` flags and their defaults (note these differ from upstream defaults for our deployment):

| Helm key                  | Env var                     | Helm default |
| ------------------------- | --------------------------- | ------------ |
| `featureChatControls`     | `FEATURE_CHAT_CONTROLS`     | `"true"`     |
| `featureCapture`          | `FEATURE_CAPTURE`           | `"False"`    |
| `featureArtifacts`        | `FEATURE_ARTIFACTS`         | `"False"`    |
| `featurePlayground`       | `FEATURE_PLAYGROUND`        | `"False"`    |
| `featureChatOverview`     | `FEATURE_CHAT_OVERVIEW`     | `"False"`    |
| `featureNotesAiControls`  | `FEATURE_NOTES_AI_CONTROLS` | `"False"`    |
| `featureVoice`            | `FEATURE_VOICE`             | `"False"`    |
| `featureChangelog`        | `FEATURE_CHANGELOG`         | `"False"`    |
| `featureSystemPrompt`     | `FEATURE_SYSTEM_PROMPT`     | `"False"`    |
| `featureModels`           | `FEATURE_MODELS`            | `"True"`     |
| `featureKnowledge`        | `FEATURE_KNOWLEDGE`         | `"True"`     |
| `featurePrompts`          | `FEATURE_PROMPTS`           | `"True"`     |
| `featureTools`            | `FEATURE_TOOLS`             | `"False"`    |
| `featureAdminEvaluations` | `FEATURE_ADMIN_EVALUATIONS` | `"True"`     |
| `featureAdminFunctions`   | `FEATURE_ADMIN_FUNCTIONS`   | `"False"`    |
| `featureAdminSettings`    | `FEATURE_ADMIN_SETTINGS`    | `"True"`     |
| `featureInputMenu`        | `FEATURE_INPUT_MENU`        | `"True"`     |
| `featureTemporaryChat`    | `FEATURE_TEMPORARY_CHAT`    | `"True"`     |
| `featureToolServers`      | `FEATURE_TOOL_SERVERS`      | `"False"`    |
| `featureTerminalServers`  | `FEATURE_TERMINAL_SERVERS`  | `"False"`    |

**Missing from helm** (need adding):

- `featureSkills` → `FEATURE_SKILLS` (upstream default: `false`)
- `featureBuiltinTools` → `FEATURE_BUILTIN_TOOLS` (new flag, suggest default: `true`)

### Implementation Approach

**Shared sub-components import `config` store directly** — This matches the existing codebase pattern. Components like `Chat.svelte` and `MessageInput.svelte` already check `$config?.features?.enable_*` directly.

Changes needed in each file:

1. **`Capabilities.svelte`** — Import `config` store, extend `visibleCapabilities` filter to hide `web_search`, `image_generation`, `code_interpreter`, `builtin_tools` when globally disabled
2. **`BuiltinTools.svelte`** — Import `config` store, filter `allTools` reactively to exclude disabled features
3. **`ModelEditor.svelte`** — Import `config` store (or use `isFeatureEnabled`), wrap Tools/Skills/Knowledge/TTS sections in `{#if}` guards
4. **`ModelSettingsModal.svelte`** — No changes needed if sub-components self-guard (the Capabilities/BuiltinTools/DefaultFeatures components will already hide disabled items)

### TypeScript Type Gap

The `Config.features` type in `stores/index.ts` (line 277) is missing:

- `feature_skills` — returned by API but not in type
- `feature_builtin_tools` — needs adding (new flag)
- `enable_channels` — returned by API but not in type
- `enable_notes` — returned by API but not in type
- `enable_code_interpreter` — returned by API but not in type
- `enable_code_execution` — returned by API but not in type

## Code References

### Frontend (both surfaces share these)

- `src/lib/components/workspace/Models/ModelEditor.svelte:742-831` — Sections needing guards
- `src/lib/components/workspace/Models/Capabilities.svelte:70-75` — `visibleCapabilities` filter (extend)
- `src/lib/components/workspace/Models/BuiltinTools.svelte:48-59` — `allTools` (needs filtering)
- `src/lib/components/workspace/Models/DefaultFeatures.svelte` — Already filtered by capabilities (no changes)
- `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte:291-343` — Admin capabilities section
- `src/lib/components/admin/Settings/Models.svelte:737-750` — Admin per-model editor (reuses ModelEditor)
- `src/lib/stores/index.ts:268-318` — Config type (needs additions)
- `src/lib/constants.ts:98-109` — `DEFAULT_CAPABILITIES`

### Backend

- `backend/open_webui/config.py:1907-1960` — FEATURE\_\* flag definitions (add FEATURE_BUILTIN_TOOLS here)
- `backend/open_webui/main.py:2379-2449` — `/api/config` features dict (expose new flag here)
- `backend/open_webui/utils/features.py:55` — FEATURE_FLAGS dict (add entry here)
- `backend/open_webui/utils/tools.py:403-564` — `get_builtin_tools()` (already gates per-tool, add global check)
- `backend/open_webui/utils/middleware.py:2709-2735` — Builtin tools injection (add FEATURE_BUILTIN_TOOLS check)

### Helm

- `helm/open-webui-tenant/values.yaml:192-213` — FEATURE\_\* values (add featureBuiltinTools, featureSkills)
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml:105-126` — Env var mapping (add entries)

## Open Questions

1. **Should capabilities default to `false` when the feature is globally disabled?** Currently `DEFAULT_CAPABILITIES` in `constants.ts` sets `web_search: true`, `image_generation: true`, etc. If web search is disabled deployment-wide, should new models still default to having that capability enabled (but hidden)? Or should the default change based on config?

2. **TTS guard strictness** — Is checking `feature_voice` sufficient, or should we also check `audio.tts.engine !== ""`? A deployment might have `FEATURE_VOICE=true` but no TTS engine configured yet.

3. **Builtin tools default** — Should `FEATURE_BUILTIN_TOOLS` default to `true` (matching current implicit behavior) or `false` (requiring explicit opt-in)?

4. **`ENABLE_MEMORIES` in helm** — Currently not exposed in the helm chart. Should it be added alongside the other new flags?
