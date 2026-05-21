# Document Writer Model Capability Toggle Implementation Plan

## Overview

Surface the existing **Document Writer** capability in the model/assistant editor, so admins and builders can toggle it per model — exactly like Web Search and Code Interpreter. This is a **frontend-only** change; the Document Writer feature itself (built-in `write_document` tool, prompt injection, streaming `<document>` tag parsing, feature flags, chat toggle) is already fully implemented.

## Current State Analysis

Document Writer is a complete, working capability wired through every layer **except the model editor UI**:

**Already implemented (no work needed):**
- Built-in tool: `write_document()` — `backend/open_webui/tools/builtin.py:527`
- Per-turn prompt injection — `backend/open_webui/utils/middleware.py:2440-2453`
- Streaming `<document>` tag parsing — `backend/open_webui/utils/middleware.py:4059-4064`
- Native function-calling registration gated on `get_model_capability('document_writer')` — `backend/open_webui/utils/tools.py:489-495`
- Feature flags: `FEATURE_DOCUMENT_WRITER` / `ENABLE_DOCUMENT_WRITER`, user permission `document_writer` — `config.py:1570,1867,2503`
- Config exposed to frontend: `enable_document_writer`, `feature_document_writer` — `main.py:2695,2723`
- Chat input toggle — `src/lib/components/chat/MessageInput/InputMenu.svelte:871`
- Chat-side capability gating — `MessageInput.svelte` filters `documentWriterCapableModels` by `info.meta.capabilities.document_writer ?? true`; `Chat.svelte` seeds the per-turn toggle from `model.info.meta.defaultFeatureIds`
- `DEFAULT_CAPABILITIES.document_writer = true` — `src/lib/constants.ts:107`
- i18n key `"Document Writer"` already present in both `en-US` and `nl-NL` (`nl-NL` = "Documentschrijver")

**The gap:** `document_writer` has **no entry** in two model-editor components:
- `src/lib/components/workspace/Models/Capabilities.svelte` — the capability checkbox grid (`capabilityLabels`, lines 18-63) and config-guard map (`capabilityConfigGuards`, lines 11-16) omit it.
- `src/lib/components/workspace/Models/DefaultFeatures.svelte` — the "on-by-default per turn" picker (`featureLabels`, `availableFeatures`) omits it.

**Consequence today:** because the chat side reads `capabilities.document_writer ?? true`, every model *silently* has Document Writer enabled, and there is **no UI to turn it off** or to set it default-on. A model can have the capability but the editor exposes no checkbox.

## Desired End State

In the model/assistant editor (`ModelEditor.svelte`, used for both base models and custom assistants — there is a single shared `Capabilities.svelte`):

- A **"Document Writer"** checkbox appears in the **Capabilities** section, next to "Code Interpreter".
- The checkbox is hidden when the global `ENABLE_DOCUMENT_WRITER` config flag is off (config-guard parity with Web Search / Code Interpreter).
- When the Document Writer capability is checked, **"Document Writer"** becomes selectable in the **Default Features** section (on-by-default per turn).
- Saving the model persists `document_writer` into `meta.capabilities` (and into `meta.defaultFeatureIds` if defaulted on); the chat input menu already respects both.

**Verification:** uncheck Document Writer on a model → save → that model's chat input menu no longer shows the Document Writer toggle. Check it + add to Default Features → a new chat with that model has Document Writer pre-enabled.

### Key Discoveries:

- `Capabilities.svelte` is the **single** capabilities-toggle component — used everywhere capabilities are set; no duplication (`ModelEditor.svelte:833`).
- The Gradient-DS custom **Agents** admin panel (`admin/Settings/Agents.svelte`) has no capabilities concept — out of scope, confirmed.
- `capabilities` is a free-form dict on the model `meta` JSON blob (`backend/open_webui/models/models.py`) — no schema/migration needed for a new key.
- `?? true` defaults in `MessageInput.svelte` make this fully **backward compatible**: existing models without the key keep Document Writer available; editing an old model shows the checkbox pre-checked (matches today's always-on behavior).
- `ModelEditor.svelte:836-849` computes `availableFeatures` for the Default Features picker from enabled capabilities, filtered by an allow-list array at line 840.

## What We're NOT Doing

- No backend changes — the Document Writer feature, flags, tool, and prompts already exist.
- No changes to the chat input menu / `Chat.svelte` / `MessageInput.svelte` — they already read `capabilities.document_writer` and `defaultFeatureIds`.
- No changes to the Gradient-DS Agents admin panel — it has no capabilities model.
- No database migration — `meta.capabilities` is a free-form dict.
- Not adding Document Writer to `image_generation`-style mutual exclusivity — it has none.

## Implementation Approach

A single, tightly-coupled frontend change across five files: register `document_writer` in the two editor components, extend the `ModelEditor` allow-list and the `getDefaultCapabilities` config-guard helper, and add one new i18n description string (the label string already exists). All changes mirror the existing `web_search` / `code_interpreter` treatment exactly.

## Phase 1: Surface Document Writer in the Model Editor

### Overview

Register `document_writer` in `Capabilities.svelte` and `DefaultFeatures.svelte`, wire it into `ModelEditor.svelte`'s Default Features allow-list, extend the `getDefaultCapabilities()` config guard, and add the missing i18n description.

### Changes Required:

#### 1. Capabilities checkbox grid

**File**: `src/lib/components/workspace/Models/Capabilities.svelte`
**Changes**: Add `document_writer` to the config-guard map, the capability label registry, and the prop type.

Add to `capabilityConfigGuards` (lines 11-16):

```ts
const capabilityConfigGuards: Record<string, string> = {
	web_search: 'enable_web_search',
	image_generation: 'enable_image_generation',
	code_interpreter: 'enable_code_interpreter',
	document_writer: 'enable_document_writer',
	builtin_tools: 'feature_builtin_tools'
};
```

Add to `capabilityLabels`, immediately after the `code_interpreter` entry (after line 42):

```ts
	document_writer: {
		label: $i18n.t('Document Writer'),
		description: $i18n.t('Model can write and create downloadable documents')
	},
```

Add to the `capabilities` prop type (lines 65-76), after `code_interpreter?: boolean;`:

```ts
	document_writer?: boolean;
```

#### 2. Default Features picker

**File**: `src/lib/components/workspace/Models/DefaultFeatures.svelte`
**Changes**: Add `document_writer` to `featureLabels` and to the `availableFeatures` default array.

Add to `featureLabels`, after the `code_interpreter` entry (after line 21):

```ts
	document_writer: {
		label: $i18n.t('Document Writer'),
		description: $i18n.t('Model can write and create downloadable documents')
	}
```

Update the `availableFeatures` default (line 24):

```ts
export let availableFeatures = [
	'web_search',
	'image_generation',
	'code_interpreter',
	'document_writer'
];
```

#### 3. ModelEditor Default Features allow-list

**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`
**Changes**: Add `'document_writer'` to the allow-list at line 840 so the Default Features picker includes it when the capability is enabled.

```svelte
{@const availableFeatures = Object.entries(capabilities)
	.filter(
		([key, value]) =>
			value &&
			['web_search', 'code_interpreter', 'image_generation', 'document_writer'].includes(key)
	)
	.map(([key, value]) => key)}
```

#### 4. Default capabilities config guard

**File**: `src/lib/utils/capabilities.ts`
**Changes**: Add `document_writer` to the guarded-defaults flip in `getDefaultCapabilities()`, so a new model starts with Document Writer off when the global flag is disabled (parity with `web_search` / `code_interpreter`).

```ts
return {
	...DEFAULT_CAPABILITIES,
	web_search: features.enable_web_search !== false ? DEFAULT_CAPABILITIES.web_search : false,
	image_generation:
		features.enable_image_generation !== false ? DEFAULT_CAPABILITIES.image_generation : false,
	code_interpreter:
		features.enable_code_interpreter !== false ? DEFAULT_CAPABILITIES.code_interpreter : false,
	document_writer:
		features.enable_document_writer !== false ? DEFAULT_CAPABILITIES.document_writer : false,
	builtin_tools:
		features.feature_builtin_tools !== false ? DEFAULT_CAPABILITIES.builtin_tools : false
};
```

#### 5. i18n — new description string

**Files**: `src/lib/i18n/locales/en-US/translation.json`, `src/lib/i18n/locales/nl-NL/translation.json`
**Changes**: The label key `"Document Writer"` already exists in both locales. Add the **one new** description key (used by the capability/feature tooltip), placed alphabetically:

- `en-US`: `"Model can write and create downloadable documents": ""` (empty = use key text). Running `npm run i18n:parse` will auto-insert it.
- `nl-NL`: `"Model can write and create downloadable documents": "Model kan downloadbare documenten schrijven en aanmaken"`

> Note: confirm the sibling keys `"Model can search the web for information"` and `"Model can execute code and perform calculations"` already exist in both locale files (they are used by the same components today); the new key sits alongside them.

### Success Criteria:

#### Automated Verification:

- [x] Type checking introduces no new errors: `npm run check` (changed files add no new *kind* of error; `capabilities.ts` has one additional line of the same pre-existing `{}`-typed `features` error as its 4 sibling lines — mirrors the `web_search`/`code_interpreter` treatment exactly)
- [x] Linting passes on changed files: `npm run lint:frontend` (`Capabilities.svelte`, `DefaultFeatures.svelte`, `capabilities.ts` clean; `ModelEditor.svelte` only has pre-existing errors)
- [x] Production build succeeds: `npm run build` (built in 1m 15s)
- [x] Both locale files contain `"Document Writer"` and `"Model can write and create downloadable documents"`: `grep -c "Document Writer"` → 4 each; new description key present in both

#### Manual Verification:

- [ ] Workspace → Models → create/edit a model: a **"Document Writer"** checkbox appears in the Capabilities section, next to "Code Interpreter"
- [ ] With Document Writer checked, **"Document Writer"** appears as a selectable option in the Default Features section
- [ ] Uncheck Document Writer → save → open a chat with that model → the Document Writer toggle no longer appears in the input menu
- [ ] Check Document Writer + add it to Default Features → save → start a new chat with that model → the Document Writer toggle is pre-enabled
- [ ] Editing a pre-existing model (created before this change) shows the Document Writer checkbox **checked** (backward-compatible default)
- [ ] With global `ENABLE_DOCUMENT_WRITER` disabled, the Document Writer checkbox is hidden from the editor
- [ ] Dutch UI (`nl-NL`): the checkbox label reads "Documentschrijver" and the tooltip shows the Dutch description

**Implementation Note**: After automated verification passes, pause for manual confirmation before considering the work complete.

---

## Testing Strategy

### Unit Tests:

No new unit tests — the change is declarative UI registration (object entries + an array element). Existing frontend tests are unaffected.

### Manual Testing Steps:

1. `npm run dev` (frontend) + `open-webui dev` (backend).
2. Workspace → Models → Create a model → confirm "Document Writer" checkbox in Capabilities.
3. Toggle it on → confirm it appears in Default Features → check it there too → save.
4. Start a new chat with the model → confirm Document Writer toggle is present and pre-enabled.
5. Edit the model → uncheck Document Writer capability → save → new chat → confirm the toggle is gone.
6. Edit a model created before this change → confirm the checkbox shows checked.
7. Disable `ENABLE_DOCUMENT_WRITER` globally → confirm the checkbox disappears from the editor.
8. Switch UI language to Dutch → confirm label and tooltip translations.

## Performance Considerations

None — adds a few object keys and one array element rendered in an existing `{#each}` loop.

## Migration Notes

No migration. `meta.capabilities` is a free-form JSON dict. Existing models lacking the `document_writer` key continue to behave as before (`?? true` keeps Document Writer available); the key is only persisted once a model is saved through the updated editor.

## References

- Capabilities component: `src/lib/components/workspace/Models/Capabilities.svelte:11-76`
- Default Features component: `src/lib/components/workspace/Models/DefaultFeatures.svelte:9-25`
- Model editor wiring: `src/lib/components/workspace/Models/ModelEditor.svelte:833-849`
- Default capabilities helper: `src/lib/utils/capabilities.ts:9-22`
- Default capabilities constant: `src/lib/constants.ts:100-112`
- Existing Document Writer chat integration: `src/lib/components/chat/MessageInput/InputMenu.svelte:871`, `Chat.svelte:380-410`
- Backend (already implemented, reference only): `backend/open_webui/tools/builtin.py:527`, `utils/tools.py:489-495`, `utils/middleware.py:2440-2453`
