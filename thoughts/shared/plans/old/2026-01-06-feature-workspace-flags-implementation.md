# Feature Workspace Flags Implementation Plan

## Overview

Implement four feature flags (`FEATURE_MODELS`, `FEATURE_KNOWLEDGE`, `FEATURE_PROMPTS`, `FEATURE_TOOLS`) that control workspace features for ALL users including admins. When disabled, these flags:
- Hide the corresponding workspace tab from navigation
- Hide related UI elements throughout the application (chat attachments, model editor selectors, etc.)
- Block backend API endpoints with 403 Forbidden
- Hide the workspace sidebar link when ALL four are disabled

**Important**: Admin panel settings remain unchanged - these flags do not affect admin configuration UI.

## Current State Analysis

### Workspace Navigation Locations:

1. **Sidebar.svelte** (lines 752-787, 984-1016)
   - Shows "Workspace" link when user has access to any workspace section

2. **workspace/+layout.svelte** (lines 85-123)
   - Tab navigation showing Models, Knowledge, Prompts, Tools
   - Route guards redirect unauthorized users (lines 24-39)

3. **workspace/+page.svelte** (lines 6-22)
   - Index page redirects to first available workspace section

### Additional UI Locations:

**Knowledge (when `FEATURE_KNOWLEDGE=False`):**
- `InputMenu.svelte` - "Attach Knowledge" option (line 258)
- `InputMenu/Knowledge.svelte` - Knowledge browser panel
- `Commands/Knowledge.svelte` - Command autocomplete for `/knowledge`
- `Models/Knowledge.svelte` - Knowledge selector in model editor
- `Models/Knowledge/KnowledgeSelector.svelte` - Knowledge dropdown

**Prompts (when `FEATURE_PROMPTS=False`):**
- `Commands/Prompts.svelte` - Prompt command selector (`/` commands)
- `CommandSuggestionList.svelte` - Renders prompts in command list

**Tools (when `FEATURE_TOOLS=False`):**
- `IntegrationsMenu.svelte` - Tool toggle UI in chat
- `Models/ToolsSelector.svelte` - Tools selector in model editor
- User settings Tools tab - but NOT admin settings

### Backend API Endpoints:

**Models Router** (`/api/v1/models`):
- `POST /create`, `GET /export`, `POST /import`
- `POST /model/toggle`, `POST /model/update`, `POST /model/delete`

**Knowledge Router** (`/api/v1/knowledge`):
- `POST /create`, `POST /{id}/update`, `DELETE /{id}/delete`
- `POST /{id}/file/add`, `POST /{id}/file/update`, `POST /{id}/file/remove`
- `POST /{id}/reset`, `POST /{id}/files/batch/add`

**Prompts Router** (`/api/v1/prompts`):
- `POST /create`, `POST /command/{command}/update`, `DELETE /command/{command}/delete`

**Tools Router** (`/api/v1/tools`):
- `POST /create`, `GET /export`
- `POST /id/{id}/update`, `DELETE /id/{id}/delete`, `POST /id/{id}/valves/update`

### Key Discoveries:
- Voice feature flag pattern established in `utils/features.py` and `utils/features.ts`
- Feature flags exposed via `/api/config` at `main.py:1926-1932`
- `require_feature()` dependency blocks API access with 403

## Desired End State

After implementation:
1. `FEATURE_MODELS=False`:
   - Hides Models workspace tab
   - Blocks models CRUD API endpoints with 403

2. `FEATURE_KNOWLEDGE=False`:
   - Hides Knowledge workspace tab
   - Hides "Attach Knowledge" in chat input menu
   - Hides Knowledge selector in model editor
   - Hides `/knowledge` command autocomplete
   - Blocks knowledge CRUD API endpoints with 403

3. `FEATURE_PROMPTS=False`:
   - Hides Prompts workspace tab
   - Hides `/` prompt command autocomplete
   - Blocks prompts CRUD API endpoints with 403

4. `FEATURE_TOOLS=False`:
   - Hides Tools workspace tab
   - Hides tool toggle UI in chat (IntegrationsMenu)
   - Hides Tools selector in model editor
   - Blocks tools CRUD API endpoints with 403
   - Does NOT hide admin panel tool settings

5. When ALL four are disabled:
   - Workspace link hidden from sidebar
   - `/workspace` redirects to `/`

6. Pipelines continue to work in the model picker regardless of flags

### Verification:
- All features visible when flags are `True` (default)
- Individual features hidden when their flag is `False`
- API returns 403 for disabled feature endpoints
- Admin panel settings remain accessible

## What We're NOT Doing

- NOT hiding admin panel settings (admin can still configure tools, etc.)
- NOT affecting model picker or chat basic functionality
- NOT blocking read-only API endpoints (list, get operations)
- NOT changing the permission system
- NOT modifying how pipelines are registered or function

---

## Phase 1: Backend Feature Flags & API Protection

### Overview
Add four new feature flag environment variables, expose them via config API, and protect workspace API endpoints.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: After line 1609 (after FEATURE_VOICE)

```python
FEATURE_MODELS = os.environ.get("FEATURE_MODELS", "True").lower() == "true"
FEATURE_KNOWLEDGE = os.environ.get("FEATURE_KNOWLEDGE", "True").lower() == "true"
FEATURE_PROMPTS = os.environ.get("FEATURE_PROMPTS", "True").lower() == "true"
FEATURE_TOOLS = os.environ.get("FEATURE_TOOLS", "True").lower() == "true"
```

#### 2. Backend Feature Utility (`backend/open_webui/utils/features.py`)

**File**: `backend/open_webui/utils/features.py`

Update imports (lines 11-19):
```python
from open_webui.config import (
    FEATURE_CHAT_CONTROLS,
    FEATURE_CAPTURE,
    FEATURE_ARTIFACTS,
    FEATURE_PLAYGROUND,
    FEATURE_CHAT_OVERVIEW,
    FEATURE_NOTES_AI_CONTROLS,
    FEATURE_VOICE,
    FEATURE_MODELS,
    FEATURE_KNOWLEDGE,
    FEATURE_PROMPTS,
    FEATURE_TOOLS,
)
```

Update Feature type (lines 21-29):
```python
Feature = Literal[
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
    "notes_ai_controls",
    "voice",
    "models",
    "knowledge",
    "prompts",
    "tools",
]
```

Update FEATURE_FLAGS dict (lines 31-39):
```python
FEATURE_FLAGS: dict[Feature, bool] = {
    "chat_controls": FEATURE_CHAT_CONTROLS,
    "capture": FEATURE_CAPTURE,
    "artifacts": FEATURE_ARTIFACTS,
    "playground": FEATURE_PLAYGROUND,
    "chat_overview": FEATURE_CHAT_OVERVIEW,
    "notes_ai_controls": FEATURE_NOTES_AI_CONTROLS,
    "voice": FEATURE_VOICE,
    "models": FEATURE_MODELS,
    "knowledge": FEATURE_KNOWLEDGE,
    "prompts": FEATURE_PROMPTS,
    "tools": FEATURE_TOOLS,
}
```

#### 3. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`

Add imports (around line 421 with other FEATURE_* imports):
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_MODELS,
    FEATURE_KNOWLEDGE,
    FEATURE_PROMPTS,
    FEATURE_TOOLS,
)
```

Add to features dict in `get_app_config()` (after line 1932):
```python
"feature_models": FEATURE_MODELS,
"feature_knowledge": FEATURE_KNOWLEDGE,
"feature_prompts": FEATURE_PROMPTS,
"feature_tools": FEATURE_TOOLS,
```

#### 4. Models Router Protection (`backend/open_webui/routers/models.py`)

**File**: `backend/open_webui/routers/models.py`

Add import at top:
```python
from open_webui.utils.features import require_feature
```

Add `require_feature("models")` dependency to these endpoints:

**Line 166** - `create_new_model`:
```python
async def create_new_model(
    request: Request,
    form_data: ModelForm,
    user=Depends(get_verified_user),
    _=Depends(require_feature("models")),
):
```

**Line 211** - `export_models`:
```python
async def export_models(
    request: Request,
    user=Depends(get_verified_user),
    _=Depends(require_feature("models")),
):
```

**Line 236** - `import_models`:
```python
async def import_models(
    request: Request,
    form_data: ImportModelsForm,
    user=Depends(get_verified_user),
    _=Depends(require_feature("models")),
):
```

**Line 369** - `toggle_model_by_id`:
```python
async def toggle_model_by_id(
    id: str, request: Request, user=Depends(get_verified_user), _=Depends(require_feature("models"))
):
```

**Line 404** - `update_model_by_id`:
```python
async def update_model_by_id(
    id: str,
    request: Request,
    form_data: ModelForm,
    user=Depends(get_verified_user),
    _=Depends(require_feature("models")),
):
```

**Line 438** - `delete_model_by_id`:
```python
async def delete_model_by_id(
    id: str, request: Request, user=Depends(get_verified_user), _=Depends(require_feature("models"))
):
```

#### 5. Knowledge Router Protection (`backend/open_webui/routers/knowledge.py`)

**File**: `backend/open_webui/routers/knowledge.py`

Add import at top:
```python
from open_webui.utils.features import require_feature
```

Add `require_feature("knowledge")` to these endpoints:
- Line 158: `create_new_knowledge`
- Line 296: `update_knowledge_by_id`
- Line 407: `add_file_to_knowledge_by_id`
- Line 474: `update_file_from_knowledge_by_id`
- Line 541: `remove_file_from_knowledge_by_id`
- Line 621: `delete_knowledge_by_id`
- Line 684: `reset_knowledge_by_id`
- Line 718: `add_files_to_knowledge_batch`

#### 6. Prompts Router Protection (`backend/open_webui/routers/prompts.py`)

**File**: `backend/open_webui/routers/prompts.py`

Add import at top:
```python
from open_webui.utils.features import require_feature
```

Add `require_feature("prompts")` to these endpoints:
- Line 47: `create_new_prompt`
- Line 110: `update_prompt_by_command`
- Line 149: `delete_prompt_by_command`

#### 7. Tools Router Protection (`backend/open_webui/routers/tools.py`)

**File**: `backend/open_webui/routers/tools.py`

Add import at top:
```python
from open_webui.utils.features import require_feature
```

Add `require_feature("tools")` to these endpoints:
- Line 247: `export_tools`
- Line 268: `create_new_tools`
- Line 361: `update_tools_by_id`
- Line 424: `delete_tools_by_id`
- Line 511: `update_tools_valves_by_id`

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] API returns new feature flags: `curl http://localhost:8080/api/config | jq '.features'`
- [ ] Default values are all `true`

#### Manual Verification:
- [ ] Set `FEATURE_MODELS=False` - API returns `feature_models: false`
- [ ] With `FEATURE_MODELS=False` - `POST /api/v1/models/create` returns 403
- [ ] With `FEATURE_KNOWLEDGE=False` - `POST /api/v1/knowledge/create` returns 403

---

## Phase 2: Frontend Feature Types

### Overview
Add the four new feature types to the frontend type system.

### Changes Required:

#### 1. Feature Utility (`src/lib/utils/features.ts`)

**File**: `src/lib/utils/features.ts`
**Location**: Update Feature type (lines 4-11)

```typescript
export type Feature =
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'playground'
	| 'chat_overview'
	| 'notes_ai_controls'
	| 'voice'
	| 'models'
	| 'knowledge'
	| 'prompts'
	| 'tools';
```

#### 2. Config Store Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: In the Config.features type (after line 286)

```typescript
features: {
    // ... existing fields ...
    feature_voice?: boolean;
    feature_models?: boolean;
    feature_knowledge?: boolean;
    feature_prompts?: boolean;
    feature_tools?: boolean;
};
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] No lint errors: `npm run lint:frontend`

---

## Phase 3: Workspace Navigation Updates

### Overview
Update the workspace navigation to respect feature flags.

### Changes Required:

#### 1. Workspace Layout (`src/routes/(app)/workspace/+layout.svelte`)

**File**: `src/routes/(app)/workspace/+layout.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Route guards (lines 23-43)** - Replace entire onMount:

```typescript
onMount(async () => {
    // Feature flag checks apply to ALL users including admins
    if ($page.url.pathname.includes('/models') && !isFeatureEnabled('models')) {
        goto('/');
        return;
    }
    if ($page.url.pathname.includes('/knowledge') && !isFeatureEnabled('knowledge')) {
        goto('/');
        return;
    }
    if ($page.url.pathname.includes('/prompts') && !isFeatureEnabled('prompts')) {
        goto('/');
        return;
    }
    if ($page.url.pathname.includes('/tools') && !isFeatureEnabled('tools')) {
        goto('/');
        return;
    }

    // Permission checks for non-admin users
    if ($user?.role !== 'admin') {
        if ($page.url.pathname.includes('/models') && !$user?.permissions?.workspace?.models) {
            goto('/');
        } else if ($page.url.pathname.includes('/knowledge') && !$user?.permissions?.workspace?.knowledge) {
            goto('/');
        } else if ($page.url.pathname.includes('/prompts') && !$user?.permissions?.workspace?.prompts) {
            goto('/');
        } else if ($page.url.pathname.includes('/tools') && !$user?.permissions?.workspace?.tools) {
            goto('/');
        }
    }

    loaded = true;
});
```

**Tab navigation (lines 85-123)** - Add feature flag checks:

Line 85 - Models tab:
```svelte
{#if isFeatureEnabled('models') && ($user?.role === 'admin' || $user?.permissions?.workspace?.models)}
```

Line 94 - Knowledge tab:
```svelte
{#if isFeatureEnabled('knowledge') && ($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)}
```

Line 105 - Prompts tab:
```svelte
{#if isFeatureEnabled('prompts') && ($user?.role === 'admin' || $user?.permissions?.workspace?.prompts)}
```

Line 114 - Tools tab:
```svelte
{#if isFeatureEnabled('tools') && ($user?.role === 'admin' || $user?.permissions?.workspace?.tools)}
```

#### 2. Workspace Index Page (`src/routes/(app)/workspace/+page.svelte`)

**File**: `src/routes/(app)/workspace/+page.svelte`

Replace entire file:
```svelte
<script lang="ts">
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import { onMount } from 'svelte';

	onMount(() => {
		// Find first available workspace section considering both feature flags and permissions
		if (isFeatureEnabled('models') && ($user?.role === 'admin' || $user?.permissions?.workspace?.models)) {
			goto('/workspace/models');
		} else if (isFeatureEnabled('knowledge') && ($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)) {
			goto('/workspace/knowledge');
		} else if (isFeatureEnabled('prompts') && ($user?.role === 'admin' || $user?.permissions?.workspace?.prompts)) {
			goto('/workspace/prompts');
		} else if (isFeatureEnabled('tools') && ($user?.role === 'admin' || $user?.permissions?.workspace?.tools)) {
			goto('/workspace/tools');
		} else {
			goto('/');
		}
	});
</script>
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With all flags `True`: all workspace tabs visible
- [ ] With `FEATURE_MODELS=False`: Models tab hidden
- [ ] With all flags `False`: workspace redirects to `/`

---

## Phase 4: Sidebar Updates

### Overview
Update the sidebar to hide the Workspace link when all workspace features are disabled.

### Changes Required:

#### 1. Sidebar Component (`src/lib/components/layout/Sidebar.svelte`)

**File**: `src/lib/components/layout/Sidebar.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Collapsed sidebar (line 752)** - Update condition:

```svelte
{#if (isFeatureEnabled('models') || isFeatureEnabled('knowledge') || isFeatureEnabled('prompts') || isFeatureEnabled('tools')) && ($user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools)}
```

**Expanded sidebar (line 984)** - Update condition:

```svelte
{#if (isFeatureEnabled('models') || isFeatureEnabled('knowledge') || isFeatureEnabled('prompts') || isFeatureEnabled('tools')) && ($user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools)}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With all flags `True`: Workspace link visible
- [ ] With ALL flags `False`: Workspace link hidden

---

## Phase 5: Knowledge UI Updates

### Overview
Hide Knowledge-related UI elements in chat when `FEATURE_KNOWLEDGE=False`.

### Changes Required:

#### 1. Input Menu (`src/lib/components/chat/MessageInput/InputMenu.svelte`)

**File**: `src/lib/components/chat/MessageInput/InputMenu.svelte`

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Line 258** - Hide "Knowledge" tab option:
Wrap the knowledge tab button with feature check:
```svelte
{#if isFeatureEnabled('knowledge')}
    <button ... on:click={() => { tab = 'knowledge'; }}>
        Knowledge
    </button>
{/if}
```

**Line 453-470** - Also wrap the Knowledge panel rendering:
```svelte
{#if tab === 'knowledge' && isFeatureEnabled('knowledge')}
    <Knowledge ... />
{/if}
```

#### 2. Command Suggestion List (`src/lib/components/chat/MessageInput/CommandSuggestionList.svelte`)

**File**: `src/lib/components/chat/MessageInput/CommandSuggestionList.svelte`

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

Wrap Knowledge command component (around line 99):
```svelte
{#if isFeatureEnabled('knowledge')}
    <Knowledge ... />
{/if}
```

#### 3. Model Editor Knowledge Selector (`src/lib/components/workspace/Models/ModelEditor.svelte`)

**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Line 718** - Wrap Knowledge component:
```svelte
{#if isFeatureEnabled('knowledge')}
    <Knowledge bind:selectedItems={knowledge} />
{/if}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_KNOWLEDGE=True`: "Attach Knowledge" visible in input menu
- [ ] With `FEATURE_KNOWLEDGE=False`: "Attach Knowledge" hidden, `/knowledge` command hidden

---

## Phase 6: Prompts UI Updates

### Overview
Hide Prompts-related UI elements when `FEATURE_PROMPTS=False`.

### Changes Required:

#### 1. Command Suggestion List (`src/lib/components/chat/MessageInput/CommandSuggestionList.svelte`)

**File**: `src/lib/components/chat/MessageInput/CommandSuggestionList.svelte`

Wrap Prompts command component:
```svelte
{#if isFeatureEnabled('prompts')}
    <Prompts prompts={$prompts ?? []} ... />
{/if}
```

Also conditionally skip fetching prompts if feature is disabled (in onMount or reactive block):
```typescript
if (isFeatureEnabled('prompts')) {
    prompts.set(await getPrompts(localStorage.token));
}
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`

#### Manual Verification:
- [ ] With `FEATURE_PROMPTS=True`: `/` prompt commands work
- [ ] With `FEATURE_PROMPTS=False`: `/` prompt commands hidden

---

## Phase 7: Tools UI Updates

### Overview
Hide Tools-related UI elements in chat when `FEATURE_TOOLS=False`. Admin panel remains unchanged.

### Changes Required:

#### 1. Integrations Menu (`src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`)

**File**: `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

Wrap the entire tools section or return early if disabled:
```svelte
{#if isFeatureEnabled('tools')}
    <!-- existing tool toggles UI -->
{/if}
```

#### 2. Model Editor Tools Selector (`src/lib/components/workspace/Models/ModelEditor.svelte`)

**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`

Wrap ToolsSelector component:
```svelte
{#if isFeatureEnabled('tools')}
    <ToolsSelector bind:selectedToolIds={toolIds} tools={$tools} />
{/if}
```

#### 3. Message Input (`src/lib/components/chat/MessageInput.svelte`)

**File**: `src/lib/components/chat/MessageInput.svelte`

Add import and conditionally hide the tools button:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

Wrap `showToolsButton` logic or the tools button rendering.

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`

#### Manual Verification:
- [ ] With `FEATURE_TOOLS=True`: Tool toggles visible in chat
- [ ] With `FEATURE_TOOLS=False`: Tool toggles hidden
- [ ] Admin panel tool settings still accessible

---

## Phase 8: Route Guards for Sub-Routes

### Overview
Add feature flag guards to workspace create/edit routes.

### Changes Required:

Add `isFeatureEnabled` check to onMount in each of these files:

**Models routes:**
- `src/routes/(app)/workspace/models/+page.svelte`
- `src/routes/(app)/workspace/models/create/+page.svelte`
- `src/routes/(app)/workspace/models/edit/+page.svelte`

**Knowledge routes:**
- `src/routes/(app)/workspace/knowledge/+page.svelte`
- `src/routes/(app)/workspace/knowledge/create/+page.svelte`
- `src/routes/(app)/workspace/knowledge/[id]/+page.svelte`

**Prompts routes:**
- `src/routes/(app)/workspace/prompts/+page.svelte`
- `src/routes/(app)/workspace/prompts/create/+page.svelte`
- `src/routes/(app)/workspace/prompts/edit/+page.svelte`

**Tools routes:**
- `src/routes/(app)/workspace/tools/+page.svelte`
- `src/routes/(app)/workspace/tools/create/+page.svelte`
- `src/routes/(app)/workspace/tools/edit/+page.svelte`
- `src/routes/(app)/workspace/functions/create/+page.svelte`

Example pattern:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
import { goto } from '$app/navigation';
import { onMount } from 'svelte';

onMount(() => {
    if (!isFeatureEnabled('models')) {
        goto('/');
    }
});
```

### Success Criteria:

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`

#### Manual Verification:
- [ ] Direct navigation to `/workspace/models/create` with `FEATURE_MODELS=False` redirects to `/`

---

## Phase 9: Tests

### Overview
Add tests for the workspace feature flags.

### Changes Required:

#### 1. Backend Unit Tests (`backend/open_webui/test/util/test_features.py`)

```python
class TestWorkspaceFeatures:
    """Tests for workspace feature flags."""

    @pytest.mark.parametrize("feature", ["models", "knowledge", "prompts", "tools"])
    def test_workspace_features_enabled_by_default(self, feature):
        """Workspace features should be enabled by default."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {feature: True}
        ):
            assert is_feature_enabled(feature) is True

    @pytest.mark.parametrize("feature", ["models", "knowledge", "prompts", "tools"])
    def test_workspace_features_can_be_disabled(self, feature):
        """Workspace features should be disableable."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {feature: False}
        ):
            assert is_feature_enabled(feature) is False

    @pytest.mark.parametrize("feature", ["models", "knowledge", "prompts", "tools"])
    def test_require_feature_blocks_when_disabled(self, feature):
        """Should raise 403 when workspace feature is disabled."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {feature: False}
        ):
            check = require_feature(feature)
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403
```

#### 2. Frontend Unit Tests (`src/lib/utils/features.test.ts`)

```typescript
describe('workspace features', () => {
    const workspaceFeatures = ['models', 'knowledge', 'prompts', 'tools'] as const;

    workspaceFeatures.forEach(feature => {
        it(`returns true when ${feature} feature is enabled`, () => {
            vi.mocked(get).mockReturnValue({
                features: { [`feature_${feature}`]: true }
            });
            expect(isFeatureEnabled(feature)).toBe(true);
        });

        it(`returns false when ${feature} feature is disabled`, () => {
            vi.mocked(get).mockReturnValue({
                features: { [`feature_${feature}`]: false }
            });
            expect(isFeatureEnabled(feature)).toBe(false);
        });

        it(`returns true when ${feature} feature is undefined (default)`, () => {
            vi.mocked(get).mockReturnValue({ features: {} });
            expect(isFeatureEnabled(feature)).toBe(true);
        });
    });
});
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend tests pass: `pytest backend/open_webui/test/util/test_features.py -v`
- [ ] Frontend tests pass: `npm run test:frontend`

---

## Testing Strategy

### Manual Testing Steps:

1. **Default state (all flags True)**:
   - All workspace tabs visible
   - All chat features (knowledge attach, tools, prompts) visible
   - Pipelines appear in model picker

2. **FEATURE_MODELS=False**:
   - Models tab hidden
   - `/workspace/models` redirects to `/`
   - `POST /api/v1/models/create` returns 403
   - Pipelines STILL appear in model picker

3. **FEATURE_KNOWLEDGE=False**:
   - Knowledge tab hidden
   - "Attach Knowledge" hidden in chat input menu
   - Knowledge selector hidden in model editor
   - `/knowledge` command hidden
   - `POST /api/v1/knowledge/create` returns 403

4. **FEATURE_PROMPTS=False**:
   - Prompts tab hidden
   - `/` prompt commands hidden
   - `POST /api/v1/prompts/create` returns 403

5. **FEATURE_TOOLS=False**:
   - Tools tab hidden
   - Tool toggles hidden in chat
   - Tools selector hidden in model editor
   - `POST /api/v1/tools/create` returns 403
   - Admin panel tool settings STILL accessible

6. **All flags disabled**:
   - Workspace link hidden from sidebar
   - `/workspace` redirects to `/`
   - Chat still works with pipelines

---

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add 4 lines | Low |
| `backend/open_webui/utils/features.py` | Add ~8 lines | Low |
| `backend/open_webui/main.py` | Add ~5 lines | Low |
| `backend/open_webui/routers/models.py` | Add 1 import + 6 deps | Low |
| `backend/open_webui/routers/knowledge.py` | Add 1 import + 8 deps | Low |
| `backend/open_webui/routers/prompts.py` | Add 1 import + 3 deps | Low |
| `backend/open_webui/routers/tools.py` | Add 1 import + 5 deps | Low |
| `src/lib/utils/features.ts` | Add 4 lines | Low |
| `src/lib/stores/index.ts` | Add 4 lines | Low |
| `src/routes/(app)/workspace/+layout.svelte` | Modify guards + tabs | Low |
| `src/routes/(app)/workspace/+page.svelte` | Rewrite redirect logic | Low |
| `src/lib/components/layout/Sidebar.svelte` | Modify 2 conditions | Low |
| `src/lib/components/chat/MessageInput/InputMenu.svelte` | Add feature checks | Low |
| `src/lib/components/chat/MessageInput/CommandSuggestionList.svelte` | Add feature checks | Low |
| `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte` | Add feature checks | Low |
| `src/lib/components/chat/MessageInput.svelte` | Add feature checks | Low |
| `src/lib/components/workspace/Models/ModelEditor.svelte` | Add feature checks | Low |
| 13 workspace route files | Add route guards | Low |
| Test files | Add ~60 lines | None |

**Total: ~35 files, ~200 LOC additions**

---

## References

- Voice feature flag plan: `thoughts/shared/plans/2026-01-06-feature-voice-flag-implementation.md`
- Feature utility: `src/lib/utils/features.ts` and `backend/open_webui/utils/features.py`
- Workspace layout: `src/routes/(app)/workspace/+layout.svelte`
- Sidebar: `src/lib/components/layout/Sidebar.svelte`
- Pipeline registration: `scripts/pipes/bootstrap_functions.py`
