---
date: 2026-01-06T15:30:00+01:00
researcher: Claude
git_commit: cde6c1f98802afcb06cfdfd6c116a44af73486fc
branch: feat/admin-config
repository: open-webui
topic: "Admin model capabilities restricting workspace model capabilities"
tags: [research, codebase, capabilities, permissions, admin, workspace, models]
status: complete
last_updated: 2026-01-06
last_updated_by: Claude
---

# Research: Admin Model Capabilities Restricting Workspace Model Capabilities

**Date**: 2026-01-06T15:30:00+01:00
**Researcher**: Claude
**Git Commit**: cde6c1f98802afcb06cfdfd6c116a44af73486fc
**Branch**: feat/admin-config
**Repository**: open-webui

## Research Question

How can we make admin-configured model capabilities restrict the available capabilities in workspace model creation/editing? For example, if an admin disables "Code Interpreter" on a base model, users should not be able to enable it when creating workspace models based on that model.

## Summary

**Feasibility**: Fully feasible with moderate changes (~100-150 LOC across 4-5 files)

**Current State**: Admin model capabilities and workspace model capabilities are completely independent. Both are stored in the same `meta.capabilities` field but there's no enforcement that workspace model capabilities must be a subset of admin capabilities.

**Key Insight**: The distinction between admin configs and workspace models is determined by `base_model_id`:
- Admin configs: `base_model_id = NULL` (configure actual LLMs)
- Workspace models: `base_model_id = <model-id>` (presets referencing LLMs)

**Implementation Effort**:

| Component | Files | LOC | Risk |
|-----------|-------|-----|------|
| Frontend Capabilities restriction | 2 | ~50 | Low |
| Backend validation | 1 | ~30 | Low |
| API helper (optional) | 1 | ~20 | Low |
| **Total** | **4** | **~100** | **Low** |

## Detailed Findings

### Current Data Model

Both admin model configs and workspace models are stored in the `model` table:

```python
# backend/open_webui/models/models.py:53-103
class Model(Base):
    __tablename__ = "model"

    id = Column(Text, primary_key=True)
    base_model_id = Column(Text, nullable=True)  # Key discriminator
    name = Column(Text)
    meta = Column(JSONField)  # Contains capabilities
    params = Column(JSONField)
    access_control = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)
```

**Admin model config** (`base_model_id = NULL`):
```python
# Created via admin panel at /admin/settings
{
    "id": "gpt-4o",  # Same as actual LLM ID
    "base_model_id": null,
    "meta": {
        "capabilities": {
            "vision": true,
            "code_interpreter": false,  # Admin disabled this
            ...
        }
    }
}
```

**Workspace model** (`base_model_id != NULL`):
```python
# Created via workspace at /workspace/models/create
{
    "id": "my-custom-assistant",  # Custom ID
    "base_model_id": "gpt-4o",    # References actual LLM
    "meta": {
        "capabilities": {
            "vision": true,
            "code_interpreter": true,  # Currently allowed regardless of admin
            ...
        }
    }
}
```

### Capability Fields

Defined in `src/lib/components/workspace/Models/Capabilities.svelte:46-55`:

| Capability | Default | Description |
|------------|---------|-------------|
| `vision` | `true` | Model accepts image inputs |
| `file_upload` | `true` | Model accepts file inputs |
| `web_search` | `true` | Model can search the web |
| `image_generation` | `true` | Model can generate images |
| `code_interpreter` | `true` | Model can execute code |
| `usage` | `undefined` | Include usage stats in response |
| `citations` | `true` | Display citations |
| `status_updates` | `true` | Display status updates |

### Frontend Component Flow

**Admin Settings Page** (`src/lib/components/admin/Settings/Models.svelte`):
```typescript
// Line 82-106: Loads models with admin configs overlaid
const init = async () => {
    workspaceModels = await getBaseModels(localStorage.token);  // Admin configs
    baseModels = await getModels(localStorage.token, null, true); // Actual LLMs

    models = baseModels.map((m) => {
        const workspaceModel = workspaceModels.find((wm) => wm.id === m.id);
        if (workspaceModel) {
            return { ...m, ...workspaceModel };  // Merge admin config
        }
        return { ...m, id: m.id, name: m.name, is_active: true };
    });
};

// Line 108-109: When saving, forces base_model_id to null
const upsertModelHandler = async (model) => {
    model.base_model_id = null;  // This makes it an admin config
    ...
};
```

**Workspace Model Editor** (`src/lib/components/workspace/Models/ModelEditor.svelte`):
```typescript
// Line 526-548: Base model selector (only shown when preset=true)
{#if preset}
    <select bind:value={info.base_model_id} required>
        <option value={null}>Select a base model</option>
        {#each $models.filter((m) => !m?.preset && ...) as model}
            <option value={model.id}>{model.name}</option>
        {/each}
    </select>
{/if}

// Line 744: Capabilities rendered without restrictions
<Capabilities bind:capabilities />
```

**Capabilities Component** (`src/lib/components/workspace/Models/Capabilities.svelte`):
```typescript
// Line 62-78: All capabilities rendered as checkboxes
{#each Object.keys(capabilityLabels) as capability}
    <Checkbox
        state={capabilities[capability] ? 'checked' : 'unchecked'}
        on:change={(e) => {
            capabilities[capability] = e.detail === 'checked';
        }}
    />
{/each}
```

### Backend Validation

**Create endpoint** (`backend/open_webui/routers/models.py:130-165`):
```python
@router.post("/create", response_model=Optional[ModelModel])
async def create_new_model(
    request: Request,
    form_data: ModelForm,
    user=Depends(get_verified_user),
):
    # Permission check exists
    if user.role != "admin" and not has_permission(...):
        raise HTTPException(...)

    # ID validation exists
    if not is_valid_model_id(form_data.id):
        raise HTTPException(...)

    # NO capability validation currently
    model = Models.insert_new_model(form_data, user.id)
```

## Recommended Implementation

### 1. Update Capabilities Component

**File**: `src/lib/components/workspace/Models/Capabilities.svelte`

Add an `allowedCapabilities` prop to restrict which capabilities can be enabled:

```svelte
<script lang="ts">
    export let capabilities = {};
    export let allowedCapabilities: Record<string, boolean> | null = null;

    $: isCapabilityAllowed = (key: string): boolean => {
        if (allowedCapabilities === null) return true;  // No restrictions
        return allowedCapabilities[key] ?? false;
    };
</script>

{#each Object.keys(capabilityLabels) as capability}
    {@const allowed = isCapabilityAllowed(capability)}
    <div class="flex items-center gap-2 mr-3">
        <Checkbox
            state={capabilities[capability] ? 'checked' : 'unchecked'}
            disabled={!allowed}
            on:change={(e) => {
                if (allowed) {
                    capabilities[capability] = e.detail === 'checked';
                }
            }}
        />
        <div class="py-0.5 text-sm {!allowed ? 'opacity-50' : ''}">
            <Tooltip content={allowed
                ? marked.parse(capabilityLabels[capability].description)
                : $i18n.t('Disabled by administrator')
            }>
                {$i18n.t(capabilityLabels[capability].label)}
            </Tooltip>
        </div>
    </div>
{/each}
```

**Estimated LOC**: ~20 lines changed

### 2. Update ModelEditor to Pass Allowed Capabilities

**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`

Track the selected base model and extract its capabilities:

```svelte
<script lang="ts">
    // Add reactive statement to get base model's allowed capabilities
    $: selectedBaseModel = preset && info.base_model_id
        ? $models.find(m => m.id === info.base_model_id)
        : null;

    $: allowedCapabilities = selectedBaseModel?.info?.meta?.capabilities ?? null;

    // Reset disallowed capabilities when base model changes
    $: if (allowedCapabilities && preset) {
        Object.keys(capabilities).forEach(key => {
            if (capabilities[key] && allowedCapabilities[key] === false) {
                capabilities[key] = false;
            }
        });
    }
</script>

<!-- Line 744: Update Capabilities usage -->
<Capabilities bind:capabilities {allowedCapabilities} />
```

**Estimated LOC**: ~20 lines added

### 3. Add Backend Validation

**File**: `backend/open_webui/routers/models.py`

Add validation helper and use in create/update endpoints:

```python
def validate_capabilities_against_base_model(
    form_data: ModelForm,
) -> None:
    """Ensure workspace model capabilities are subset of base model capabilities."""
    if not form_data.base_model_id:
        return  # Admin config, no restrictions

    base_model = Models.get_model_by_id(form_data.base_model_id)
    if not base_model:
        return  # Base model not found, will fail elsewhere

    base_capabilities = base_model.meta.get("capabilities", {}) if base_model.meta else {}
    requested_capabilities = form_data.meta.capabilities if form_data.meta else {}

    if not requested_capabilities:
        return

    for key, value in requested_capabilities.items():
        if value and base_capabilities.get(key) is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Capability '{key}' is disabled by administrator for this base model",
            )


@router.post("/create", response_model=Optional[ModelModel])
async def create_new_model(...):
    # ... existing checks ...

    # Add capability validation
    validate_capabilities_against_base_model(form_data)

    # ... rest of function ...


@router.post("/model/update", response_model=Optional[ModelModel])
async def update_model_by_id(...):
    # ... existing checks ...

    # Add capability validation
    validate_capabilities_against_base_model(form_data)

    # ... rest of function ...
```

**Estimated LOC**: ~30 lines added

### 4. (Optional) API Endpoint for Allowed Capabilities

**File**: `backend/open_webui/routers/models.py`

Add endpoint to get allowed capabilities for a base model:

```python
@router.get("/model/{id}/capabilities")
async def get_model_allowed_capabilities(
    id: str,
    user=Depends(get_verified_user),
):
    model = Models.get_model_by_id(id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    return model.meta.get("capabilities", {}) if model.meta else {}
```

**Estimated LOC**: ~10 lines

## Implementation Order

1. **Backend validation first** - Prevents invalid data from being saved
2. **Frontend Capabilities component** - Adds `allowedCapabilities` prop
3. **Frontend ModelEditor** - Passes allowed capabilities based on base model selection
4. **Testing** - Verify admin restrictions flow through to workspace

## Edge Cases to Handle

| Scenario | Behavior |
|----------|----------|
| Base model has no capabilities set | All capabilities allowed (existing behavior) |
| Base model capability is `undefined` | Capability allowed (treat as not restricted) |
| Base model capability is `false` | Capability disabled in workspace |
| User switches base model | Reset disallowed capabilities |
| Editing existing workspace model where base model capabilities changed | Backend rejects save if now-invalid |

## Code References

| File | Lines | Description |
|------|-------|-------------|
| `backend/open_webui/models/models.py` | 53-103 | Model SQLAlchemy table |
| `backend/open_webui/models/models.py` | 38-50 | ModelMeta with capabilities field |
| `backend/open_webui/routers/models.py` | 130-165 | Create endpoint |
| `backend/open_webui/routers/models.py` | 366-389 | Update endpoint |
| `src/lib/components/workspace/Models/Capabilities.svelte` | 1-81 | Capabilities checkbox component |
| `src/lib/components/workspace/Models/ModelEditor.svelte` | 526-548 | Base model selector |
| `src/lib/components/workspace/Models/ModelEditor.svelte` | 744 | Capabilities usage |
| `src/lib/components/admin/Settings/Models.svelte` | 82-106 | Admin model loading |
| `src/lib/components/admin/Settings/Models.svelte` | 108-144 | Admin model upsert |

## Related Research

- `thoughts/shared/research/2026-01-06-env-based-feature-control-saas.md` - Tier-based feature control (different scope - global features vs per-model capabilities)

## Open Questions

1. **UX for existing workspace models**: If admin later disables a capability, should existing workspace models be automatically updated, or should they become invalid until manually fixed?

2. **Default capabilities inheritance**: Should workspace models inherit base model's capabilities as defaults, or start with all-true and let users disable?

3. **Capability visibility**: Should disabled capabilities be hidden entirely or shown as disabled with explanation?

4. **Admin override**: Should admins be able to create workspace models without capability restrictions?
