---
date: 2026-04-09T09:53:00+02:00
researcher: Claude Opus 4.6
git_commit: c95a0823c
branch: dev
repository: open-webui
topic: "Data Sovereignty Guard — Per-Model Capability Warning System"
tags: [research, data-sovereignty, capabilities, admin-panel, modal, send-flow]
status: complete
last_updated: 2026-04-09
last_updated_by: Claude Opus 4.6
last_updated_note: "Resolved open questions: per-model message with capability list, mid-conversation warnings, audit logging, global feature flag, model-switch behavior"
---

# Research: Data Sovereignty Guard — Per-Model Capability Warning System

**Date**: 2026-04-09T09:53:00+02:00
**Researcher**: Claude Opus 4.6
**Git Commit**: c95a0823c
**Branch**: dev
**Repository**: open-webui

## Research Question

How to implement per-model "data sovereignty warnings" — admin-configurable checkboxes on base models that flag certain capabilities (file upload, web search, etc.) as requiring user acknowledgment before first use in a conversation. What parts of the codebase are affected and which functionalities should have this guard?

## Summary

The feature requires changes across four layers: (1) admin UI for configuring which capabilities need warnings per model, (2) model data storage for the warning configuration, (3) chat send flow interception for showing the confirmation popup, and (4) per-conversation state tracking for the "once per conversation" dismissal.

The existing codebase provides strong patterns to build on: the `Capabilities.svelte` checkbox UI, the `ConfirmDialog` component, the open `ModelMeta` schema (accepts arbitrary extra fields), and the `submitPrompt()` validation chain in `Chat.svelte`.

## Detailed Findings

### 1. Admin Model Configuration UI

The checkbox UI for model capabilities is rendered by three shared components in `src/lib/components/workspace/Models/`:

| Component | Purpose | Used in |
|-----------|---------|---------|
| `Capabilities.svelte` | 10 capability checkboxes (vision, file_upload, web_search, etc.) | ModelEditor + ModelSettingsModal |
| `DefaultFeatures.svelte` | Default-on features for new chats | ModelEditor + ModelSettingsModal |
| `BuiltinTools.svelte` | 9 built-in tool checkboxes | ModelEditor + ModelSettingsModal |

**Where to add the new row:** A new component (e.g., `DataWarnings.svelte`) should sit alongside these three, rendered in both:
- `ModelEditor.svelte` (per-model config)
- `ModelSettingsModal.svelte` (system-wide defaults)

The screenshot shows the new row would appear after "Ingebouwde tools" as a fourth section.

### 2. Model Data Structure

**Backend:** `ModelMeta` (`backend/open_webui/models/models.py:38-48`) uses `ConfigDict(extra='allow')`, so a new field like `data_warnings` can be added without DB migration:

```python
class ModelMeta(BaseModel):
    profile_image_url: Optional[str] = '/static/favicon.png'
    description: Optional[str] = None
    capabilities: Optional[dict] = None
    # New field stored via extra='allow', no migration needed:
    # data_warnings: Optional[dict] = None
    model_config = ConfigDict(extra='allow')
```

Suggested schema for the new field on `meta`:
```json
{
  "data_warnings": {
    "file_upload": true,      // warn before sending files to this model
    "web_search": true,       // warn before web search with this model
    "knowledge": true,        // warn before RAG/knowledge with this model
    "vision": true,           // warn before sending images
    "code_interpreter": true, // warn before code execution
    "image_generation": true  // warn before image generation
  },
  "data_warning_message": "This model runs on infrastructure outside your organization's control. Uploaded files and conversation content will be processed by a third-party provider."
}
```

**Frontend:** `ModelMeta` interface (`src/lib/apis/index.ts:1739-1746`) should be extended with `data_warnings?: Record<string, boolean>` and `data_warning_message?: string`.

**Default merging:** `get_all_models()` (`backend/open_webui/utils/models.py:295-312`) already merges `DEFAULT_MODEL_METADATA` as defaults. The same merge logic would apply to `data_warnings`, so system-wide defaults work automatically.

### 3. Chat Send Flow — Interception Point

The send flow in `Chat.svelte`:

```
User clicks Send → MessageInput dispatches 'submit'
  → Chat.svelte submitPrompt() (line 1789)
    → Validates input (lines 1792-1858)
    → Creates user message in history (line 1893)
    → sendMessage() (line 1920)
      → sendMessageSocket() per model (line 2031)
        → getFeatures() resolves active features (line 2049)
        → POST /api/chat/completions
```

**Best interception point:** Inside `submitPrompt()`, after input validation but before creating the user message (between lines 1858 and 1870). At this point:
- We know which models are selected (`selectedModels`)
- We can check which features are active (web search toggle, file attachments, etc.)
- We can cross-reference against each model's `data_warnings` config
- We can check per-conversation state to see if the warning was already accepted

**Feature resolution** happens in `getFeatures()` (line 2049), but the active toggles (`webSearchEnabled`, `imageGenerationEnabled`, `codeInterpreterEnabled`) and file attachments are available as component state earlier.

### 4. Active Feature Detection at Send Time

At the `submitPrompt()` interception point, these signals indicate which capabilities are active:

| Capability | How to detect | Source |
|------------|--------------|--------|
| File upload | `files.length > 0` (non-image files) | `MessageInput` → `Chat.svelte` files prop |
| File context | Same as file upload (files with context) | files in chatFiles |
| Web search | `webSearchEnabled` reactive var | Chat.svelte line 107 |
| Vision | Images in `files` array | files with `type === 'image'` |
| Image generation | `imageGenerationEnabled` reactive var | Chat.svelte line 108 |
| Code interpreter | `codeInterpreterEnabled` reactive var | Chat.svelte line 109 |
| Knowledge | `chatFiles` contains KB documents OR RAG collections active | chatFiles store / ragFilterState |

### 5. Per-Conversation State Tracking

The warning should fire once per conversation. Two approaches:

**Option A — Component-local state (recommended):**
A `Set<string>` tracking which model+capability warnings have been accepted in this conversation session. Stored as a reactive variable in `Chat.svelte`. Resets when navigating to a new chat (component remounts) or starting a new conversation.

```typescript
let acceptedDataWarnings: Set<string> = new Set();
// Key format: `${modelId}:${capability}`
// e.g., "gpt-4:file_upload"
```

This is the simplest approach and matches the requirement ("once per conversation"). No backend persistence needed — if the user reloads the page, they'll see the warning again for that conversation, which is actually desirable from a security standpoint.

**Option B — Backend-persisted per-chat:**
Store accepted warnings in the chat metadata via API. More complex but survives page reloads. The `ConversationFeedback` pattern shows how to do per-chat backend state. Likely overkill.

### 6. Confirmation Dialog

The existing `ConfirmDialog` (`src/lib/components/common/ConfirmDialog.svelte`) is perfect:
- Supports custom `title` and `message` (with Markdown rendering)
- Has Cancel/Confirm buttons
- Dispatches `confirm` and `cancel` events
- Supports keyboard shortcuts (Escape = cancel, Enter = confirm)
- Already used in `Chat.svelte` for event confirmations (lines 131-138, 2729-2738)

The pattern from `Chat.svelte`'s event confirmation can be reused directly. The admin-configured `data_warning_message` would be rendered as the dialog message, with a sensible default.

### 7. Functionalities That Should Have This Guard

Based on data sovereignty analysis — which operations send user/organizational data to external infrastructure:

| Capability | Risk | Guard rationale |
|------------|------|-----------------|
| **File Upload** | Documents sent to model provider for processing | High — organizational documents may contain sensitive data |
| **Web Search** | Query context sent to search provider; results may be logged | Medium — search queries can reveal intent and context |
| **Knowledge / RAG** | Organizational KB documents sent to model as context | High — may include confidential organizational data |
| **Vision** | Images sent to model provider for analysis | Medium — images may contain sensitive visual data |
| **Code Interpreter** | Code sent to external execution environment | Medium — code may contain business logic or credentials |
| **Image Generation** | Prompts sent to image generation service | Low-Medium — prompts reveal intent |

**Not recommended for guard:**
- **Memory** — stored locally in the platform, not sent to external providers
- **Citations** — display-only, no data leaves
- **Status Updates** — display-only
- **Usage** — display-only (token counting)
- **Time & Calculation** — local tool
- **Chat History** — local tool
- **Notes** — local tool
- **Channels** — local tool

### 8. Default Warning Messages

Suggested defaults (configurable per model by admin):

**Generic default:**
> "Dit model draait op externe infrastructuur. Geüploade bestanden en gespreksinhoud worden verwerkt door een externe provider. Wilt u doorgaan?"
>
> "This model runs on external infrastructure. Uploaded files and conversation content will be processed by an external provider. Do you want to continue?"

**Per-capability defaults could also be offered**, e.g., for web search:
> "Web search results for this model are processed by an external search provider. Your query context may be visible to that provider."

## Architecture Insights

### Backend Changes
**Model metadata:** `ModelMeta` uses `extra='allow'`, so `data_warnings` and `data_warning_message` fields are stored without DB migration. They flow through `get_all_models()` and the merge logic automatically.

**Feature flag:** New `ENABLE_DATA_WARNINGS` PersistentConfig (default `True`). Exposed via `/api/config` as `config.features.enable_data_warnings`. Added to Helm chart values.

**Audit logging:** New `DataWarningLog` model/table to record accepted warnings:
- `id`, `user_id`, `chat_id`, `model_id`, `capabilities` (JSON list of acknowledged capabilities), `warning_message`, `created_at`
- New endpoint `POST /api/v1/data-warnings/accept` to log acceptance from frontend
- Queryable for compliance reporting

### Frontend Component Structure

```
ModelEditor.svelte / ModelSettingsModal.svelte
  └── DataWarnings.svelte (new)     ← admin config checkboxes
        Props: dataWarnings, warningMessage

Chat.svelte
  ├── acceptedDataWarnings: Set<string>    ← per-conversation tracking
  ├── submitPrompt()                       ← interception point
  │     └── checkDataWarnings()            ← new: detects active warned capabilities
  │           └── if unacknowledged → show dialog, await confirm/cancel
  │           └── on confirm → add to Set, log via API, proceed with send
  │           └── on cancel → abort send, keep input state intact
  └── DataWarningDialog.svelte (new, or reuse ConfirmDialog)
        ← lists which capabilities triggered, shows admin-configured message
```

### Send Flow with Data Warnings

```
submitPrompt() validates input
  → checkDataWarnings(selectedModels, activeCapabilities)
    → for each model: compare active capabilities against model.info.meta.data_warnings
    → filter out already-accepted (from acceptedDataWarnings Set)
    → if any unacknowledged warnings remain:
        → show DataWarningDialog listing the specific capabilities
        → await user response (Promise-based)
        → on confirm: add to acceptedDataWarnings, POST /api/v1/data-warnings/accept
        → on cancel: return early (send aborted, input preserved)
  → proceed with user message creation and sendMessage()
```

### Upstream Compatibility
This is fully additive:
- New component files (no upstream file modification for the UI)
- New `meta` fields via `extra='allow'` (no schema changes)
- Interception in `submitPrompt()` is the only upstream-file touch point
- Can be feature-flagged to disable entirely

## Code References

- `src/lib/components/workspace/Models/Capabilities.svelte` — existing capability checkboxes pattern
- `src/lib/components/workspace/Models/ModelEditor.svelte:138,194-208` — how meta fields are saved
- `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte:115-132` — system-wide defaults save
- `src/lib/components/chat/Chat.svelte:1789-1920` — submitPrompt validation chain (interception point)
- `src/lib/components/chat/Chat.svelte:2049-2088` — getFeatures() active feature resolution
- `src/lib/components/chat/Chat.svelte:131-138,2729-2738` — existing ConfirmDialog pattern in chat
- `src/lib/components/common/ConfirmDialog.svelte` — reusable confirmation dialog
- `backend/open_webui/models/models.py:38-48` — ModelMeta with extra='allow'
- `backend/open_webui/utils/models.py:295-312` — default metadata merging
- `src/lib/apis/index.ts:1731-1746` — frontend ModelMeta TypeScript types
- `src/lib/constants.ts:100-111` — DEFAULT_CAPABILITIES

## Design Decisions (Resolved)

1. **Granularity:** One warning message per model, listing the specific capabilities that triggered it. The message is configurable per model by the admin, with a sensible default.
2. **Mid-conversation capability changes:** The warning fires whenever a *new* capability is used that hasn't been acknowledged yet in this conversation. E.g., if a user sends a text message (no warning), then enables web search on the next message, the web search warning fires at that point. The `acceptedDataWarnings` set tracks `${modelId}:${capability}` keys, so only truly new combinations trigger the dialog.
3. **Audit logging:** Accepted warnings are logged for compliance. When a user accepts a data warning, a log entry is created capturing: user ID, model ID, capabilities acknowledged, chat ID, and timestamp. This supports GDPR/DPIA audit trails.
4. **Feature flag:** Global `ENABLE_DATA_WARNINGS` feature flag (default: enabled). Added to config.py, Helm chart values, and frontend config. When disabled, all data warning configuration is ignored and no popups are shown. Per-model `data_warnings` config is preserved but inactive.
5. **Model switching:** If the user switches models mid-conversation, warnings for the new model's flagged capabilities fire independently — the accepted set is keyed by `${modelId}:${capability}`.

## Open Questions

1. **Warning message i18n:** Should the default warning message be an i18n key (translatable) or a freeform admin-entered string? Given the Dutch public sector audience, i18n support for the default seems valuable, with admin override as freeform text.
2. **Log storage:** Where should audit logs be stored — a new `data_warning_logs` table, or appended to existing chat metadata?
3. **Cancel behavior:** When the user clicks cancel, the capability that triggered the warning should be disabled for that message (e.g., files removed from the message, web search toggled off) — or should the entire send be cancelled? Current plan: entire send cancelled, user returns to the input state with everything still enabled.
