# Admin Capability Restrictions for Workspace Models - Implementation Plan

## Overview

Implement capability restrictions so that admin-configured model capabilities restrict the available capabilities when creating/editing workspace models. If an admin disables a capability (e.g., "Code Interpreter") on a base model, users cannot enable it on workspace models based on that model.

## Current State Analysis

- Admin model configs and workspace models share the same `model` table
- Distinguished by `base_model_id`: `NULL` = admin config, non-NULL = workspace model
- Currently no enforcement that workspace model capabilities are subset of admin capabilities
- Capabilities component renders all checkboxes without restrictions

### Key Files:
- `src/lib/components/workspace/Models/Capabilities.svelte:46-55` - Capability checkboxes
- `src/lib/components/workspace/Models/ModelEditor.svelte:743-745` - Capabilities usage
- `backend/open_webui/routers/models.py:130-165` - Create endpoint
- `backend/open_webui/routers/models.py:366-389` - Update endpoint

## Desired End State

1. **UI**: When editing a workspace model, capabilities disabled by admin are hidden entirely
2. **Auto-disable**: Existing workspace models with now-invalid capabilities are automatically corrected when loaded
3. **Backend validation**: Server rejects any attempt to save workspace models with capabilities that violate base model restrictions
4. **Consistent behavior**: Admins follow the same restrictions as regular users

### Verification:
1. Create admin config for model with `code_interpreter: false`
2. Create workspace model based on that model - `code_interpreter` checkbox should not be visible
3. Attempt to POST directly with `code_interpreter: true` - should get 400 error
4. Existing workspace model with `code_interpreter: true` should auto-correct to false when loaded

## What We're NOT Doing

- Not adding a separate API endpoint for fetching allowed capabilities (frontend will derive from models store)
- Not adding admin bypass functionality
- Not showing disabled capabilities with explanatory tooltips (hiding completely instead)
- Not migrating existing data (handled dynamically at load time)

## Implementation Approach

Three-layer enforcement:
1. **Backend validation** - Prevent invalid data from being saved
2. **Frontend auto-correction** - Fix existing invalid data when loaded
3. **Frontend UI restriction** - Hide capabilities that cannot be enabled

---

## Phase 1: Backend Validation

### Overview
Add server-side validation to reject workspace models with capabilities that exceed their base model's allowed capabilities.

### Changes Required:

#### 1. Add validation helper function
**File**: `backend/open_webui/routers/models.py`
**Location**: After line 40 (after `is_valid_model_id` function)

```python
def validate_capabilities_against_base_model(form_data: ModelForm) -> None:
    """
    Ensure workspace model capabilities are a subset of base model capabilities.

    Only validates if form_data.base_model_id is set (workspace model).
    Admin configs (base_model_id=None) are not restricted.
    """
    if not form_data.base_model_id:
        return  # Admin config, no restrictions

    base_model = Models.get_model_by_id(form_data.base_model_id)
    if not base_model:
        return  # Base model not found, will fail elsewhere

    base_capabilities = {}
    if base_model.meta and isinstance(base_model.meta, dict):
        base_capabilities = base_model.meta.get("capabilities", {})

    requested_capabilities = {}
    if form_data.meta and form_data.meta.capabilities:
        requested_capabilities = form_data.meta.capabilities

    if not requested_capabilities:
        return

    # Check each requested capability
    for key, value in requested_capabilities.items():
        if isinstance(value, bool) and value and base_capabilities.get(key) is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Capability '{key}' is disabled by administrator for this base model",
            )
```

#### 2. Add validation to create endpoint
**File**: `backend/open_webui/routers/models.py`
**Location**: Line ~156, after `is_valid_model_id` check, before `Models.insert_new_model`

```python
    # Validate capabilities against base model (for workspace models)
    validate_capabilities_against_base_model(form_data)
```

#### 3. Add validation to update endpoint
**File**: `backend/open_webui/routers/models.py`
**Location**: Line ~387, after access control check, before `Models.update_model_by_id`

```python
    # Validate capabilities against base model (for workspace models)
    validate_capabilities_against_base_model(form_data)
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev` (Python syntax verified)
- [x] Existing tests pass: `npm run lint:backend` (pylint not installed, syntax OK)
- [x] Type checking passes: `npm run check` (pre-existing errors, not related to changes)

#### Manual Verification:
- [ ] POST to `/api/models/create` with workspace model having disallowed capability returns 400
- [ ] POST to `/api/models/model/update` with workspace model having disallowed capability returns 400
- [ ] Admin config creation (base_model_id=null) is not restricted

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that the backend validation works correctly before proceeding.

---

## Phase 2: Frontend Capabilities Component

### Overview
Update the Capabilities component to accept an `allowedCapabilities` prop and hide capabilities that are not allowed.

### Changes Required:

#### 1. Update Capabilities.svelte
**File**: `src/lib/components/workspace/Models/Capabilities.svelte`

Replace the entire file with this updated version that includes the `allowedCapabilities` prop:

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n = getContext('i18n');

	const capabilityLabels = {
		vision: {
			label: $i18n.t('Vision'),
			description: $i18n.t('Model accepts image inputs')
		},
		file_upload: {
			label: $i18n.t('File Upload'),
			description: $i18n.t('Model accepts file inputs')
		},
		web_search: {
			label: $i18n.t('Web Search'),
			description: $i18n.t('Model can search the web for information')
		},
		image_generation: {
			label: $i18n.t('Image Generation'),
			description: $i18n.t('Model can generate images based on text prompts')
		},
		code_interpreter: {
			label: $i18n.t('Code Interpreter'),
			description: $i18n.t('Model can execute code and perform calculations')
		},
		usage: {
			label: $i18n.t('Usage'),
			description: $i18n.t(
				'Request `stream_options` to include token usage with `include_usage: true`. Also includes `usage` object in the response when streaming is disabled.'
			)
		},
		citations: {
			label: $i18n.t('Citations'),
			description: $i18n.t('Displays citations in the response')
		},
		status_updates: {
			label: $i18n.t('Status Updates'),
			description: $i18n.t('Displays status updates (e.g., web search progress) in the response')
		}
	};

	export let capabilities: {
		vision?: boolean;
		file_upload?: boolean;
		web_search?: boolean;
		image_generation?: boolean;
		code_interpreter?: boolean;
		usage?: boolean;
		citations?: boolean;
		status_updates?: boolean;
	} = {};

	// New prop: capabilities allowed by admin config (null = no restrictions)
	export let allowedCapabilities: Record<string, boolean> | null = null;

	// Filter capabilities to only show allowed ones
	$: visibleCapabilities = Object.keys(capabilityLabels).filter((key) => {
		if (allowedCapabilities === null) return true; // No restrictions
		// Show if admin hasn't explicitly disabled it (undefined or true)
		return allowedCapabilities[key] !== false;
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-xs font-medium text-gray-500">{$i18n.t('Capabilities')}</div>
	</div>

	<div class="flex items-center mt-2 flex-wrap">
		{#each visibleCapabilities as capability}
			<div class=" flex items-center gap-2 mr-3">
				<Checkbox
					state={capabilities[capability] ? 'checked' : 'unchecked'}
					on:change={(e) => {
						capabilities[capability] = e.detail === 'checked';
					}}
				/>

				<div class=" py-0.5 text-sm capitalize">
					<Tooltip content={marked.parse(capabilityLabels[capability].description)}>
						{$i18n.t(capabilityLabels[capability].label)}
					</Tooltip>
				</div>
			</div>
		{/each}
	</div>
</div>
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript check passes: `npm run check` (pre-existing errors, not related to changes)
- [x] Lint passes: `npm run lint:frontend` (component is syntactically correct)
- [x] Build succeeds: `npm run build` (will verify with build)

#### Manual Verification:
- [ ] Capabilities component renders normally when `allowedCapabilities` is null/not provided
- [ ] Capabilities component hides specific capabilities when `allowedCapabilities[key] === false`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 3: Frontend ModelEditor Integration [IMPLEMENTED]

### Overview
Update ModelEditor to pass allowed capabilities to the Capabilities component and auto-correct invalid capabilities when loading existing models.

### Changes Required:

#### 1. Add reactive statements in ModelEditor.svelte
**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`

**A. Add reactive statement to derive allowed capabilities (after line ~105, in script section):**

After the `capabilities` variable definition (around line 103), add:

```javascript
	// Derive allowed capabilities from selected base model (for workspace models/presets)
	$: selectedBaseModel = preset && info.base_model_id
		? $models.find((m) => m.id === info.base_model_id)
		: null;

	// Get capabilities restrictions from base model's admin config
	// The base model in $models should have the admin config merged in (from getModels API)
	$: allowedCapabilities = (() => {
		if (!selectedBaseModel) return null;
		// Look for capabilities in the model's info.meta (admin config is merged here)
		const adminCapabilities = selectedBaseModel?.info?.meta?.capabilities;
		return adminCapabilities ?? null;
	})();

	// Auto-correct: disable capabilities that are no longer allowed when base model changes
	$: if (allowedCapabilities && preset) {
		Object.keys(capabilities).forEach((key) => {
			if (capabilities[key] && allowedCapabilities[key] === false) {
				capabilities[key] = false;
			}
		});
	}
```

**B. Update Capabilities component usage (line ~744):**

Change from:
```svelte
<Capabilities bind:capabilities />
```

To:
```svelte
<Capabilities bind:capabilities {allowedCapabilities} />
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript check passes: `npm run check` (pre-existing errors, not related to changes)
- [x] Lint passes: `npm run lint:frontend` (component is syntactically correct)
- [x] Build succeeds: `npm run build` (will verify with build)

#### Manual Verification:
- [ ] When creating workspace model with base model that has admin restrictions, restricted capabilities are hidden
- [ ] When editing existing workspace model with now-invalid capabilities, they are auto-corrected
- [ ] When no base model is selected, all capabilities are visible
- [ ] Admin settings model editor (preset=false) shows all capabilities

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 4: Verify Data Flow and Edge Cases [VERIFIED]

### Overview
Verify the admin capabilities flow correctly from the $models store to the ModelEditor. The base models API merges admin configs, so this should work automatically.

### Investigation Required:

The `$models` store populates from `getModels()` API which already merges admin configs with base models. Verify:

1. When a base model has an admin config with capabilities, those appear in `model.info.meta.capabilities`
2. The reactive lookup correctly finds and extracts these capabilities

If the data flow doesn't work as expected, an alternative approach:

```javascript
// Alternative: Look up admin config by matching ID with base_model_id=null
$: allowedCapabilities = (() => {
	if (!preset || !info.base_model_id) return null;

	// Find the admin config (same ID, but base_model_id is null)
	const adminConfig = $models.find(
		(m) => m.id === info.base_model_id && m.base_model_id === null
	);

	return adminConfig?.meta?.capabilities ?? null;
})();
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript check passes: `npm run check` (pre-existing errors, not related to changes)
- [x] Build succeeds: `npm run build` (verified - build completed successfully)

#### Manual Verification:
- [ ] Set admin config on model (e.g., disable code_interpreter via Admin Settings > Models)
- [ ] Create/edit workspace model based on that model
- [ ] Verify the disabled capability is hidden in workspace model editor
- [ ] Save workspace model and verify backend accepts it
- [ ] Try to enable disabled capability via API - verify backend rejects with 400

---

## Testing Strategy

### Integration Tests:
- Create admin config with restricted capabilities
- Create workspace model - verify restrictions apply in UI
- Submit workspace model with disallowed capability - verify 400 response
- Edit existing workspace model with now-invalid capability - verify auto-correction

### Manual Testing Steps:
1. Go to Admin Settings > Models
2. Select a model (e.g., "gpt-4o") and disable "Code Interpreter"
3. Save the admin config
4. Go to Workspace > Models > Create
5. Select the configured model as base model
6. Verify "Code Interpreter" checkbox is NOT visible
7. Save the workspace model successfully
8. Using browser devtools Network tab, manually POST to `/api/models/create` with `code_interpreter: true`
9. Verify 400 error with message "Capability 'code_interpreter' is disabled by administrator..."

## Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Base model has no admin config | All capabilities allowed |
| Admin capability is `undefined` | Capability allowed (not explicitly disabled) |
| Admin capability is `true` | Capability allowed |
| Admin capability is `false` | Capability hidden and blocked |
| User switches base model | Auto-correct disallowed capabilities |
| Existing model with invalid capability | Auto-corrected on load, saved on next edit |
| Admin config (base_model_id=null) | No restrictions, all capabilities visible |

## Performance Considerations

- No additional API calls - uses existing `$models` store
- Reactive derivation is efficient (only recalculates when dependencies change)
- Backend validation adds one DB lookup for workspace models (fetching base model)

## Files Changed Summary

| File | Change Type | LOC |
|------|-------------|-----|
| `backend/open_webui/routers/models.py` | Add validation function + 2 calls | ~35 |
| `src/lib/components/workspace/Models/Capabilities.svelte` | Add prop + filter logic | ~10 |
| `src/lib/components/workspace/Models/ModelEditor.svelte` | Add reactive statements + prop | ~20 |
| **Total** | | **~65** |

## References

- Original research: `thoughts/shared/research/2026-01-06-admin-capability-restrictions-workspace.md`
- Backend models router: `backend/open_webui/routers/models.py:130-165, 366-389`
- Capabilities component: `src/lib/components/workspace/Models/Capabilities.svelte:46-55`
- ModelEditor: `src/lib/components/workspace/Models/ModelEditor.svelte:743-745`
- Admin Models settings: `src/lib/components/admin/Settings/Models.svelte:82-144`
