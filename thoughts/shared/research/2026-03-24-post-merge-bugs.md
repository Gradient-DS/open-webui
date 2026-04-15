---
date: 2026-03-24T12:00:00+01:00
researcher: Claude Code
git_commit: c463eff54a69d225f2c26aa1be1d63b92018e3ab
branch: feat/security-cicd
repository: Gradient-DS/open-webui
topic: 'Post-upstream-merge bugs: PDF preview, prompt templates, agent pinning'
tags: [research, codebase, bugs, upstream-merge, citations, prompts, pinning]
status: complete
last_updated: 2026-03-24
last_updated_by: Claude Code
---

# Research: Post-Upstream-Merge Bugs

**Date**: 2026-03-24T12:00:00+01:00
**Researcher**: Claude Code
**Git Commit**: c463eff54a69d225f2c26aa1be1d63b92018e3ab
**Branch**: feat/security-cicd
**Repository**: Gradient-DS/open-webui

## Research Question

Three bugs were reported after merging upstream Open WebUI v0.8.9 (commit `c26ae48d6`):

1. PDF previewer in citation sources no longer works
2. Standard prompts with `{{variable}}` template placeholders no longer work
3. Agent pinning UX regression - requires 3-dot menu click instead of direct pin

## Summary

| Bug              | Root Cause                                                                                   | Severity | Fix Complexity                          |
| ---------------- | -------------------------------------------------------------------------------------------- | -------- | --------------------------------------- |
| PDF preview      | Upstream overwrote Gradient-DS custom tabbed preview in CitationModal                        | High     | Medium - restore custom code            |
| Prompt templates | Likely database migration issue (`title`→`name`, `command` PK→`id` PK) or `is_active` filter | High     | Low-Medium - verify migration, test API |
| Agent pinning UX | Upstream design choice, not a regression per se                                              | Low      | Low - add auto-pin on agent creation    |

## Detailed Findings

### Bug 1: PDF Previewer in Citations

**Root cause**: The upstream merge completely replaced the Gradient-DS custom `CitationModal.svelte` which had a tabbed Preview/Content interface for viewing PDFs, images, and audio inline.

**What was lost** (old code, removed by upstream):

- `selectedTab` state toggling between 'preview' and 'content' views
- File type detection: `isPDF`, `isImage`, `isAudio`, `isPreviewable`
- `previewUrl` computed property for iframe/img/audio rendering
- HEAD request to check file availability before showing preview
- Tab switcher UI component

**What replaced it** (upstream version):

- Only shows extracted text chunks with Markdown rendering
- No inline PDF/image/audio preview capability
- Link in header opens raw file in new tab (still works)

**Key files**:

- `src/lib/components/chat/Messages/Citations/CitationModal.svelte` - the modal that lost preview tabs
- `src/lib/components/common/PDFViewer.svelte` - exists but is NOT wired into CitationModal (only used by `FileItemModal` and `FilePreview`)

**Fix approach**: Restore the tabbed Preview/Content interface from the pre-merge version. The old code is recoverable from git history (commit before `c26ae48d6`). The fix involves:

1. Re-adding file type detection logic (`isPDF`, `isImage`, etc.)
2. Re-adding `selectedTab` state and tab switcher UI
3. Re-adding the iframe/img/audio preview rendering in the Preview tab
4. Re-adding the HEAD request for file availability check
5. Keeping upstream improvements (Markdown rendering, text fragment URLs, expanded docs)

**How to test**:

1. Upload a PDF document to a knowledge base
2. Start a chat that uses that knowledge base for RAG
3. Ask a question that triggers citations from the PDF
4. Click on a citation source badge (e.g., `[1]`)
5. **Expected**: Modal opens with Preview/Content tabs; Preview tab shows the PDF inline
6. **Actual (broken)**: Modal only shows extracted text chunks, no PDF preview

### Bug 2: Prompt Templates with {{variables}}

**Root cause analysis**: The upstream merge made significant changes to the prompt system:

1. **Database schema change**: `title` column renamed to `name`, primary key changed from `command` to `id` (UUID). Migration at `374d2f66af06_add_prompt_history_table.py` handles this, including stripping leading `/` from command names (line 128).

2. **Prompt loading change**: Previously, `CommandSuggestionList.svelte` loaded all prompts into a Svelte store on mount and passed them as props. Now, `Prompts.svelte` fetches them internally with a 200ms debounce on each query change.

3. **`is_active` filter added**: The new `get_prompts()` backend method filters `.filter(Prompt.is_active == True)`. If migration didn't properly set `is_active=True` for existing prompts, they'd be invisible.

**Most likely failure points**:

- **Migration not applied**: If the alembic migration `374d2f66af06` didn't run, the prompt table still has the old schema (`title`, `command` as PK, no `is_active`), causing the API to fail silently
- **`is_active` default**: Migration sets `server_default="1"` which should work, but worth verifying
- **API response format**: Frontend now expects `name` field (was `title`); if data is stale, sorting by `a.name.localeCompare(b.name)` would fail on undefined

**The specific prompt content is NOT the issue** - the `{{variable}}` parsing regex (`/{{\s*([^|}\s]+)\s*}}/g`) correctly handles the reported variables (`{{doelgroep}}`, `{{toon}}`, `{{max_zinslengte}}`, `{{samenvatting_bullets}}`, `{{bron_tekst}}`). The single `{` in `{herschreven_tekst_in_too` does NOT match the double-brace regex.

**Key files**:

- `src/lib/components/chat/MessageInput/Commands/Prompts.svelte` - now fetches prompts internally
- `src/lib/components/chat/MessageInput.svelte:373-408` - `insertTextAtCursor` with two-phase variable handling
- `src/lib/components/chat/MessageInput.svelte:183-201` - `inputVariableHandler` shows variable modal
- `src/lib/utils/index.ts:1404-1424` - `extractInputVariables` regex parsing
- `src/lib/components/common/RichTextInput.svelte:499-540` - `replaceVariables` in ProseMirror
- `backend/open_webui/models/prompts.py` - new schema with `name`, `id`, `is_active`
- `backend/open_webui/migrations/versions/374d2f66af06_add_prompt_history_table.py` - migration

**How to test**:

1. Check if migration ran: `SELECT id, command, name, is_active FROM prompt LIMIT 5;`
2. Check API response: `curl -H "Authorization: Bearer <token>" <base_url>/api/v1/prompts/`
3. In the UI, type `/` in the chat input - do prompts appear in the autocomplete?
4. Select a prompt with `{{variables}}` - does the variable input modal appear?
5. Fill in variable values and submit - are variables replaced in the editor?

**Quick diagnostic**: Open browser DevTools Network tab, type `/` in chat, check if `GET /api/v1/prompts/` returns data with `name` field (not `title`).

### Bug 3: Agent Pinning UX Regression

**Root cause**: This is an upstream design choice, not a Gradient-DS regression. Upstream Open WebUI requires users to:

1. Find the agent in the model selector or workspace
2. Click the 3-dot menu (`ModelMenu.svelte` or `ModelItemMenu.svelte`)
3. Select "Keep in Sidebar" to pin

There has never been auto-pinning of new agents.

**Current architecture**:

- `pinnedModels` stored in `$settings.ui.pinnedModels` (per-user, persisted via API)
- `DEFAULT_PINNED_MODELS` env var / config for server-wide defaults
- Pin toggle in workspace menu (`ModelMenu.svelte:136-155`) and chat selector menu (`ModelItemMenu.svelte:81-106`)
- `PinnedModelList.svelte` renders sidebar section, auto-cleans stale pins

**Fix approach**: Auto-pin newly created agents to the sidebar. Add pinning logic after successful agent creation:

**Primary location** - `src/routes/(app)/workspace/models/create/+page.svelte:55-63`:

```js
// After createNewModel succeeds (line 55-61):
const currentPinned = $settings?.pinnedModels ?? [];
if (!currentPinned.includes(modelId)) {
	settings.set({
		...$settings,
		pinnedModels: [...new Set([...currentPinned, modelId])]
	});
	await updateUserSettings(localStorage.token, { ui: $settings });
}
```

**Secondary locations** (model import):

- `src/lib/components/workspace/Models.svelte:309-316` (JSON import)
- `src/lib/components/admin/Settings/Models.svelte:192-220` (admin import)

**How to test**:

1. Go to Workspace > Models > Create
2. Create a new agent
3. **Expected (after fix)**: Agent appears in sidebar pinned models section
4. **Current**: Agent does not appear in sidebar; user must find it and pin via 3-dot menu

## Code References

- `src/lib/components/chat/Messages/Citations/CitationModal.svelte` - Citation modal (lost PDF preview)
- `src/lib/components/common/PDFViewer.svelte` - PDF renderer (not connected to citations)
- `src/lib/components/chat/MessageInput.svelte:373-408` - Prompt text insertion + variable handling
- `src/lib/components/chat/MessageInput.svelte:183-201` - Variable modal trigger
- `src/lib/utils/index.ts:1404-1424` - Variable extraction regex
- `src/lib/components/chat/MessageInput/Commands/Prompts.svelte:38-43` - Prompt fetching
- `src/routes/(app)/workspace/models/create/+page.svelte:22-66` - Agent creation (auto-pin target)
- `src/lib/components/layout/Sidebar/PinnedModelList.svelte` - Pinned models rendering
- `backend/open_webui/models/prompts.py` - Prompt model (`title`→`name` migration)
- `backend/open_webui/migrations/versions/374d2f66af06_add_prompt_history_table.py` - Schema migration

## Architecture Insights

- The upstream merge (`c26ae48d6`, v0.8.9) changed 700+ lines across `MessageInput.svelte` alone
- Prompt system moved from centralized store-based loading to per-component API fetching with debounce
- CitationModal was simplified upstream (no inline preview), losing Gradient-DS customizations
- The pinning system is entirely frontend-driven (no backend pinning model)

## Open Questions

1. **Prompt bug specifics**: Without the screenshot, the exact failure mode is unclear. Is the `/` autocomplete not showing prompts? Or do prompts show but variables don't resolve? Need to reproduce.
2. **Migration status**: Has `374d2f66af06` actually been applied on the production database? Check `alembic_version` table.
3. **PDF preview scope**: Should we restore the full tabbed interface, or use the upstream `embed_url` path with a side-panel PDF viewer instead?
4. **Auto-pin scope**: Should auto-pinning apply only to agents created by the current user, or also to imported agents?
