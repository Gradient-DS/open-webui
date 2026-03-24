---
date: 2026-03-24T18:00:00+01:00
researcher: Claude
git_commit: 39344352f
branch: fix/test-bugs-daan-260323
repository: open-webui
topic: "Model editor sections need feature flag guards for deployment-specific visibility"
tags: [research, codebase, model-editor, feature-flags, capabilities, builtin-tools]
status: complete
last_updated: 2026-03-24
last_updated_by: Claude
---

# Research: Model Editor Feature Flag Guards

**Date**: 2026-03-24T18:00:00+01:00
**Researcher**: Claude
**Git Commit**: 39344352f
**Branch**: fix/test-bugs-daan-260323
**Repository**: open-webui

## Research Question

The model creation/editing page shows options for Tools, Skills, TTS Voice, Capabilities (Web Search, Image Generation, Code Interpreter), Builtin Tools, etc. unconditionally. These should be hidden when the corresponding feature is disabled in the deployment. What env variables / config flags exist, and how should they guard the UI?

## Summary

The `ModelEditor.svelte` component renders several sections unconditionally that should be gated by existing feature flags. The backend already exposes all necessary flags via `GET /api/config` → `$config.features.*`. The frontend just needs to read them. No backend changes are required.

## Detailed Findings

### Current State: What's Always Shown (No Guards)

| Section | Component | ModelEditor.svelte line |
|---------|-----------|------------------------|
| Tools | `ToolsSelector` | 747 |
| Skills | `SkillsSelector` | 751 |
| TTS Voice | inline `<input>` | 819-831 |
| Capabilities → Web Search | `Capabilities.svelte` checkbox | always in list |
| Capabilities → Image Generation | `Capabilities.svelte` checkbox | always in list |
| Capabilities → Code Interpreter | `Capabilities.svelte` checkbox | always in list |
| Default Features (web_search, image_generation, code_interpreter) | `DefaultFeatures.svelte` | 798-811 |
| Builtin Tools → Memory | `BuiltinTools.svelte` checkbox | always in list |
| Builtin Tools → Notes | `BuiltinTools.svelte` checkbox | always in list |
| Builtin Tools → Channels | `BuiltinTools.svelte` checkbox | always in list |
| Builtin Tools → Knowledge Base | `BuiltinTools.svelte` checkbox | always in list |
| Builtin Tools → Web Search | `BuiltinTools.svelte` checkbox | always in list |
| Builtin Tools → Image Generation | `BuiltinTools.svelte` checkbox | always in list |
| Builtin Tools → Code Interpreter | `BuiltinTools.svelte` checkbox | always in list |

### Available Config Flags (Already Exposed to Frontend)

All flags below are returned by `GET /api/config` (main.py:2379-2449) and available in `$config.features.*`:

| Flag | Config Key | Type | Default | Controls |
|------|-----------|------|---------|----------|
| `FEATURE_TOOLS` | `feature_tools` | FEATURE_* (env-only) | `true` | Tools workspace page |
| `FEATURE_SKILLS` | `feature_skills` | FEATURE_* (env-only) | **`false`** | Skills feature |
| `FEATURE_VOICE` | `feature_voice` | FEATURE_* (env-only) | `true` | Voice/audio features |
| `FEATURE_KNOWLEDGE` | `feature_knowledge` | FEATURE_* (env-only) | `true` | Knowledge workspace |
| `ENABLE_WEB_SEARCH` | `enable_web_search` | PersistentConfig | `false` | Web search |
| `ENABLE_IMAGE_GENERATION` | `enable_image_generation` | PersistentConfig | `false` | Image generation |
| `ENABLE_CODE_INTERPRETER` | `enable_code_interpreter` | PersistentConfig | `true` | Code interpreter |
| `ENABLE_MEMORIES` | `enable_memories` | PersistentConfig | `true` | Memory feature |
| `ENABLE_CHANNELS` | `enable_channels` | PersistentConfig | `true` | Channels |
| `ENABLE_NOTES` | `enable_notes` | PersistentConfig | `true` | Notes |
| `AUDIO_TTS_ENGINE` | `audio.tts.engine` | PersistentConfig | `""` (disabled) | TTS (non-empty = enabled) |

### Proposed Mapping: Section → Guard

#### ModelEditor.svelte (top-level sections)

| Section | Guard | How |
|---------|-------|-----|
| Tools (`ToolsSelector`) | `$config?.features?.feature_tools` | Wrap in `{#if}` |
| Skills (`SkillsSelector`) | `$config?.features?.feature_skills` | Wrap in `{#if}` |
| TTS Voice | `$config?.features?.feature_voice` AND `$config?.audio?.tts?.engine` | Only show if voice feature enabled AND a TTS engine is configured |
| Knowledge | `$config?.features?.feature_knowledge` | Wrap in `{#if}` (optional, already makes sense without KB) |

#### Capabilities.svelte (filter `visibleCapabilities`)

| Capability | Guard |
|------------|-------|
| `web_search` | `$config?.features?.enable_web_search` |
| `image_generation` | `$config?.features?.enable_image_generation` |
| `code_interpreter` | `$config?.features?.enable_code_interpreter` |

These should be filtered out of `visibleCapabilities` when the feature is disabled. The component needs to import the `config` store.

#### BuiltinTools.svelte (filter `allTools`)

| Tool | Guard |
|------|-------|
| `memory` | `$config?.features?.enable_memories` |
| `notes` | `$config?.features?.enable_notes` |
| `channels` | `$config?.features?.enable_channels` |
| `knowledge` | `$config?.features?.feature_knowledge` |
| `web_search` | `$config?.features?.enable_web_search` |
| `image_generation` | `$config?.features?.enable_image_generation` |
| `code_interpreter` | `$config?.features?.enable_code_interpreter` |

These should be filtered out of `allTools` when the feature is disabled. The component needs to import the `config` store.

#### DefaultFeatures.svelte

Already receives `availableFeatures` as a prop filtered from capabilities. If capabilities are properly filtered, this component will automatically only show enabled features. No changes needed here.

### Implementation Approach

**Option A: Pass config down as props** — Each sub-component receives a filtered list or config object via props. Keeps components "pure" but requires prop-threading.

**Option B: Import `config` store directly in sub-components** — Simpler, consistent with how other parts of the codebase use `$config`. Components like `Chat.svelte` and `MessageInput.svelte` already check `$config?.features?.enable_*` directly.

**Recommendation: Option B** — Import the `config` store in `Capabilities.svelte` and `BuiltinTools.svelte`, filter items reactively. For `ModelEditor.svelte`, the store is already available (imported via other components' patterns). This matches existing patterns in the codebase.

### TypeScript Type Gap

The `Config` type in `stores/index.ts` is missing some fields that are returned by the API:

- `feature_skills` — not in the type (line 310 has `feature_tools` but no `feature_skills`)
- `enable_channels` — not in the type
- `enable_notes` — not in the type
- `enable_code_interpreter` — not in the type
- `enable_code_execution` — not in the type

These should be added to the `Config.features` type, though since the codebase has ~8000 svelte-check errors already, this is cosmetic.

## Code References

- `src/lib/components/workspace/Models/ModelEditor.svelte:746-831` — Sections needing guards
- `src/lib/components/workspace/Models/Capabilities.svelte:70-75` — `visibleCapabilities` filter (extend this)
- `src/lib/components/workspace/Models/BuiltinTools.svelte:48-59` — `allTools` list (needs filtering)
- `src/lib/components/workspace/Models/DefaultFeatures.svelte` — Already filtered by capabilities
- `backend/open_webui/main.py:2379-2449` — All flags exposed in `/api/config`
- `backend/open_webui/config.py:1907-1960` — FEATURE_* flag definitions
- `src/lib/stores/index.ts:268-318` — Config type definition (needs additions)
- `src/lib/utils/features.ts:30-38` — `isFeatureEnabled()` utility

## Open Questions

1. **Should capabilities default to `false` when the feature is globally disabled?** Currently `DEFAULT_CAPABILITIES` sets `web_search: true`, `image_generation: true`, etc. If web search is disabled deployment-wide, should new models still default to having that capability enabled (but hidden)? Or should the default change based on config?
2. **Knowledge section** — Should the Knowledge selector also be hidden when `FEATURE_KNOWLEDGE=false`? It's shown unconditionally today.
3. **TTS guard strictness** — Is checking `feature_voice` sufficient, or should we also check `audio.tts.engine !== ""`? A deployment might have `FEATURE_VOICE=true` but no TTS engine configured.
