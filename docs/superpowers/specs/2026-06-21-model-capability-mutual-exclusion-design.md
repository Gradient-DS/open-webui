# Design: Mutually-Exclusive Model Capabilities

- **Date:** 2026-06-21
- **Repo:** `open-webui/` (Gradient-DS fork, branch `feat/generative-ui`)
- **Status:** Approved design — ready for implementation planning
- **Author:** Lex Lubbers + Claude

## Summary

Add an admin-configurable policy that makes selected per-model **capabilities**
mutually exclusive, so that two capabilities in the same group can never be
active together on a model. The driving use case is **data sovereignty /
exfiltration prevention** — e.g. a model that can read uploaded documents must
not also be able to search the web.

The policy is delivered through Open WebUI's **PersistentConfig** mechanism:
seeded from an environment variable on first boot, then stored in the database
and managed from the **Admin Panel** (the env value is a one-time default).

Enforcement is **hard and server-side**, applied to **all models (base and
custom), always**. The advanced model editor surfaces the rule with a
radio-style auto-disable UX and an admin-configurable explanation message.

## Goals

- Let an admin declare one or more **mutually-exclusive capability groups**:
  within a group, at most one capability may be active on any model.
- **Guarantee** the constraint server-side regardless of how a model was
  created (advanced editor, simple editor, REST API, or import).
- Configure the policy from `.env` as a seed, then manage it from the Admin
  Panel after first login (PersistentConfig).
- In the advanced `ModelEditor`, enabling a capability **auto-disables** the
  others in its group and shows an **admin-configurable** explanation message.

## Non-goals

- No new capability *types* — we constrain the existing canonical capability set.
- No UI work in the simple/onboarding editor (it does not expose this capability
  set). It is still protected by server-side enforcement.
- No generic constraint engine (`requires` / `implies`). The schema is shaped so
  it *could* grow into one later, but only mutual exclusion is built now.

## Background: how the codebase works today

### Capabilities

The canonical capability set is defined in
`src/lib/constants.ts` (`DEFAULT_CAPABILITIES`, ~lines 107–120) — 12 keys:

```
file_context, vision, file_upload, web_search, image_generation,
code_interpreter, document_writer, terminal, citations, status_updates,
usage, builtin_tools
```

Capabilities are stored per-model in `model.meta.capabilities`
(`backend/open_webui/models/models.py`, `ModelMeta`, ~lines 36–47). Base models
and custom models use the same field; a custom model simply has `base_model_id`
set.

The toggles render in `src/lib/components/workspace/Models/Capabilities.svelte`
(~lines 106–128). A `capabilityConfigGuards` map (~lines 11–17) already hides a
capability checkbox when the admin has globally disabled that feature via
`$config.features`. This is precedent: **admin policy already constrains
per-model toggles** — our feature extends that idea from "on/off" to
"relational."

Backend reads of capabilities happen in several places:
- `backend/open_webui/utils/models.py` (~lines 300–324) merges capability
  defaults into the resolved model — **this is the per-request chokepoint**.
- `backend/open_webui/utils/agent.py` (~lines 188–214) — `vision`, `citations`.
- `backend/open_webui/utils/middleware.py` (~lines 2922, 2971, 3019) —
  `terminal`, `builtin_tools`, `file_context`.
- `backend/open_webui/utils/tools.py` (~lines 410–412) — generic accessor.

### PersistentConfig

`PersistentConfig` (`backend/open_webui/config.py`, ~lines 217–279) reads an env
var on first boot, then prefers the DB-stored value on subsequent boots. The DB
holds the whole config tree as one JSON document in the `config` table
(~lines 81–89); values are addressed by dotted path. Assigning to
`request.app.state.config.<KEY>` persists to the DB and refreshes all registered
configs. Admin settings reach these values through admin-only routers (e.g.
`routers/retrieval.py`, `routers/configs.py`) and the frontend admin settings
components under `src/lib/components/admin/Settings/`.

## Design decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Core goal | Data sovereignty / exfiltration prevention → hard server-side enforcement |
| Scope | All models (base + custom), always; conflict UI in the advanced `ModelEditor` only |
| Rule shape | Mutually-exclusive **groups** (ordered capability set; at most one active per group; multiple groups) |
| Editor UX | **Auto-disable** (radio-style) others in the group |
| Notice message | **Admin-configurable**, per-group with a global fallback |
| Runtime conflict resolution | **Priority by group order** — keep first enabled, force the rest off |
| Delivery | PersistentConfig (env-seeded → DB → admin-managed) |
| Implementation approach | A — single policy config + shared resolver; frontend mirrors backend policy |

## Configuration schema

A single PersistentConfig, `MODEL_CAPABILITY_EXCLUSIONS`, holds the whole policy
as a JSON object. Env seed is a one-line JSON string.

```json
{
  "enabled": true,
  "default_message": "These capabilities can't be enabled together on the same model.",
  "groups": [
    {
      "id": "external-data",
      "label": "External data access",
      "capabilities": ["file_upload", "web_search"],
      "message": "A model with document access can't also search the web, to prevent data exfiltration."
    }
  ]
}
```

Field semantics:

- `enabled` (bool) — master switch. When `false`, the resolver is a pass-through
  and the editor behaves as today.
- `default_message` (string) — shown when a group has no `message`.
- `groups[]`:
  - `id` (string) — unique, stable identifier.
  - `label` (string) — human label for the admin UI.
  - `capabilities` (string[]) — **ordered**; order *is* the runtime priority
    (first enabled wins). Each entry must be one of the canonical 12 keys.
  - `message` (string, optional) — per-group explanation; falls back to
    `default_message`.

Constraints (validated on admin save):

- Every capability key must be one of the canonical 12.
- `id` values are unique.
- A capability may appear in **at most one group** (keeps radio semantics
  unambiguous).

Env seed default: `{ "enabled": false, "groups": [] }`. Malformed env JSON logs a
warning and falls back to this default (feature inert), so existing deployments
are unaffected until explicitly configured.

**Storage decision (resolved):** stored as a single JSON PersistentConfig
`MODEL_CAPABILITY_EXCLUSIONS` (config path `models.capability_exclusions`),
sitting alongside `DEFAULT_MODEL_METADATA` (path `models.default_metadata`). It
is read/written through the **existing** admin `models` config endpoint
(`GET/POST /api/v1/configs/models`, `ModelsConfigForm`) rather than a new
endpoint, and surfaced read-only to all clients via `/api/config` for the editor
UX.

## Architecture

Single source of truth in the backend; the frontend mirrors it for UX only.

```
.env (JSON seed)
   │  first boot
   ▼
PersistentConfig MODEL_CAPABILITY_EXCLUSIONS  ──►  DB config row  ◄── Admin Panel edits
   │
   ├──► Backend resolver  resolve_capabilities(caps, policy)
   │       ├─ save-time   (routers/models.py: create/update)   ── sanitize before persist
   │       └─ runtime     (utils/models.py: model resolution)  ── sanitize per request  ← sovereignty guarantee
   │
   └──► /api/config payload  ──►  Frontend Capabilities.svelte (radio auto-disable + message)
```

### Component 1 — Resolver (`backend/open_webui/utils/capability_policy.py`, new)

- `resolve_capabilities(capabilities: dict, policy) -> dict` — pure function.
  For each group, iterate the group's `capabilities` in declared order; keep the
  first one that is enabled, set every other group member to `False`. Capabilities
  not in any group are untouched. No-op when `policy.enabled` is false or there
  are no groups. Returns a new dict (does not mutate input).
- Helper(s) to read/parse the policy from `request.app.state.config` and to
  validate an incoming policy (used by the admin save endpoint).

**What it does:** turns a raw capability dict into a policy-compliant one.
**How you use it:** call with the model's capabilities + current policy.
**Depends on:** the policy object only — no DB or request state inside the pure
function (I/O stays at the boundary).

### Component 2 — Runtime enforcement (primary guarantee)

In `backend/open_webui/utils/models.py`'s `get_all_models`, add a **separate,
unconditional block** *after* the existing `if default_metadata:` merge block
(~line 324) and *before* `request.app.state.MODELS` is assigned (~line 387). It
must NOT be folded into the `if default_metadata:` branch — that branch is
skipped when no default metadata is configured, which would silently disable
enforcement. The block loops over every model and runs `resolve_capabilities` on
`model['info']['meta']['capabilities']` when the policy is enabled.

Verified during planning: downstream readers (`agent.py` vision/citations,
`middleware.py` terminal/builtin_tools/file_context, `tools.py`) all read
capabilities from the resolved `request.app.state.MODELS` dict that this function
populates — so sanitizing here covers every consumption path.

### Component 3 — Save-time enforcement

In `backend/open_webui/routers/models.py` create/update handlers, run the same
resolver on `form_data.meta.capabilities` before persisting. This catches models
created/updated via REST API or import that bypass the editor. Behavior is
**sanitize (priority order), not reject** — consistent with the editor and the
runtime, so the rule behaves identically everywhere.

### Component 4 — Frontend delivery + editor UX

- Add the policy to the existing `/api/config` payload (alongside `features`):
  `model_capability_exclusions: { enabled, default_message, groups }`. This is
  non-secret config; any user permitted to edit models receives it.
- `src/lib/components/workspace/Models/Capabilities.svelte`:
  - Build a `capability → group` map from the policy.
  - When a capability is enabled, set the other members of its group to `false`
    (radio behavior).
  - Render the group's `message` (or `default_message`) as an inline note near
    the affected group.
  - Preserve existing `capabilityConfigGuards` behavior (globally-disabled
    capabilities stay hidden); the two mechanisms compose.
- No changes to the simple/onboarding editor.

### Component 5 — Admin Panel

A new collapsible **"Capability Exclusions"** section inside the admin model-wide
config modal `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte`
(opened from Admin → Settings → Models via the "Settings" button — the same modal
that already edits `DEFAULT_MODEL_METADATA`). The heavy editor UI lives in a new
self-contained component `Settings/Models/CapabilityExclusions.svelte`:
- Master `enabled` toggle.
- `default_message` text field.
- Repeatable group editor: `label`, ordered capability list (order defines
  priority, with up/down reorder), optional per-group `message`.

Persistence reuses the **existing** `models` config flow — no new endpoint:
- Extend `ModelsConfigForm` (`routers/configs.py:619`) with
  `MODEL_CAPABILITY_EXCLUSIONS: Optional[dict] = None`, and add it to both
  `get_models_config` and `set_models_config`.
- `set_models_config` validates the policy (canonical keys, unique ids, no
  capability in two groups) via `validate_exclusion_policy` from the resolver
  module before assigning to `request.app.state.config.MODEL_CAPABILITY_EXCLUSIONS`.
- The modal already calls `getModelsConfig`/`setModelsConfig`; it loads the policy
  in `init()` and includes it in the existing Save (`submitHandler`) — so there is
  **one** Save button, not a separate one.

## Data flow

1. **Boot:** env JSON seeds `MODEL_CAPABILITY_EXCLUSIONS`; DB value wins if present.
2. **Admin edit:** admin saves policy → validated → persisted to DB →
   PersistentConfig refreshed → included in next `/api/config`.
3. **Editing a model:** frontend reads policy from `$config`; radio auto-disable +
   message guide the user. On save, backend sanitizes before persisting.
4. **Using a model:** runtime resolver sanitizes capabilities per request before
   any consumer reads them.

## Edge cases

- **`file_upload` auto-disabled** → existing rule hiding `file_context` when
  `file_upload` is off still applies (composes cleanly).
- **Capability in no group** → never modified.
- **`enabled: false` / empty `groups`** → resolver is a pass-through; zero
  behavior change vs. today.
- **Group member globally disabled** (via `capabilityConfigGuards`) → that
  capability is hidden anyway; remaining members behave normally.
- **Malformed policy from env** → warn + fall back to disabled/empty default.
- **Legacy/imported model with a pre-existing conflict** → resolved
  deterministically at runtime (and sanitized next time it is saved).

## Backward compatibility

Default configuration is `enabled: false` with no groups, so the feature is inert
until an admin configures it. No migration is required; the new key is additive.

## Testing strategy

- **Unit (`resolve_capabilities`):** priority order within a group; multiple
  independent groups; disabled policy is a no-op; empty groups is a no-op;
  ungrouped capabilities untouched; first-enabled-wins when several are on.
- **Integration:** create/update a model via API with a conflicting combo →
  stored value is sanitized; chat request against a legacy conflicting model →
  capabilities resolved at runtime before consumers read them.
- **Frontend:** radio auto-disable behavior; per-group vs. fallback message
  rendering; composition with `capabilityConfigGuards`; no-op when policy
  disabled.

## Key file references

| Purpose | File |
|---|---|
| Canonical capability list | `src/lib/constants.ts` (~107–120) |
| Model meta schema | `backend/open_webui/models/models.py` (~36–116) |
| PersistentConfig + config table | `backend/open_webui/config.py` (~81–366) |
| New resolver | `backend/open_webui/utils/capability_policy.py` (new) |
| Runtime chokepoint | `backend/open_webui/utils/models.py` `get_all_models` (after ~line 324, before ~line 387) |
| Save-time enforcement | `backend/open_webui/routers/models.py` (`create_new_model` ~232, `update_model_by_id` ~619) |
| Admin config endpoint | `backend/open_webui/routers/configs.py` `ModelsConfigForm`/`get_models_config`/`set_models_config` (~619–658) |
| New backend config | `backend/open_webui/config.py` (after `DEFAULT_MODEL_METADATA`, ~1358) |
| Capabilities UI | `src/lib/components/workspace/Models/Capabilities.svelte` |
| Frontend toggle helper | `src/lib/utils/capabilities.ts` (+ `capabilities.test.ts`) |
| Admin settings UI | `Settings/Models/ModelSettingsModal.svelte` + new `Settings/Models/CapabilityExclusions.svelte` |
| Config api client | `src/lib/apis/configs/index.ts` (`getModelsConfig`/`setModelsConfig`, ~508/535) |
| Config store type | `src/lib/stores/index.ts` (`Config` type, ~344) |
| `/api/config` assembly | `backend/open_webui/main.py` `get_app_config` (~3206) |
| i18n | `src/lib/i18n/locales/{en-US,nl-NL}/translation.json` |

## Fork-specific constraints (from CLAUDE.md / collab world model)

- **Upstream-merge additivity:** new logic goes in *new* files
  (`capability_policy.py`, `CapabilityExclusions.svelte`); edits to upstream files
  (`Capabilities.svelte`, `utils/models.py`, `main.py`, `routers/models.py`,
  `configs.py`, `ModelSettingsModal.svelte`, stores) are minimal, additive, and
  guarded by the policy so they are no-ops when the feature is off.
- **i18n:** every new user-facing string is added to both `en-US` and `nl-NL`
  translation files (keys alphabetically sorted; admin-authored messages are
  dynamic data and are NOT translation keys).
- **Capability-key parity:** the backend `KNOWN_CAPABILITY_KEYS` set mirrors
  `DEFAULT_CAPABILITIES` in `src/lib/constants.ts`; keep them in sync.

## Open questions for planning — resolved

1. **Storage:** single JSON PersistentConfig `MODEL_CAPABILITY_EXCLUSIONS` (path
   `models.capability_exclusions`). ✓
2. **Runtime chokepoint:** confirmed — `get_all_models` populates
   `request.app.state.MODELS`, which every capability reader consumes. ✓
3. **Admin endpoint:** reuse the existing `models` config endpoint
   (`ModelsConfigForm`) — no new endpoint. ✓
