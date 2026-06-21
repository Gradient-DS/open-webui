# Model Capability Mutual-Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin declare mutually-exclusive groups of model capabilities (at most one active per group), enforced server-side for all models and surfaced as radio-style auto-disable in the capability editor.

**Architecture:** A single JSON `PersistentConfig` (`MODEL_CAPABILITY_EXCLUSIONS`) holds the policy `{enabled, default_message, groups:[{id,label,capabilities,message}]}`. A new pure backend module (`utils/capability_policy.py`) resolves a capability dict against the policy (declared order = priority). It is called at two enforcement points — model resolution (`utils/models.py`) and model save (`routers/models.py`) — and the policy is mirrored to the frontend via `/api/config` so `Capabilities.svelte` applies latest-click-wins exclusion. Admin editing reuses the existing `models` config endpoint and lives in `ModelSettingsModal.svelte`.

**Tech Stack:** FastAPI + SQLAlchemy (backend), pytest; SvelteKit 5 (runes) + Vitest, Tailwind, bits-ui (frontend).

**Spec:** `docs/superpowers/specs/2026-06-21-model-capability-mutual-exclusion-design.md`

## Global Constraints

- **No git commits.** The user has opted out of commits for this work. Each task ends at a **verification checkpoint** (run the listed command, confirm expected output) — do NOT run `git add`/`git commit`.
- **Upstream-merge additivity.** New logic lives in NEW files (`utils/capability_policy.py`, `Settings/Models/CapabilityExclusions.svelte`). Edits to upstream files (`Capabilities.svelte`, `utils/models.py`, `main.py`, `routers/models.py`, `routers/configs.py`, `ModelSettingsModal.svelte`, `stores/index.ts`, `utils/capabilities.ts`) must be minimal, additive, and no-ops when the policy is disabled.
- **Feature defaults OFF.** Env/DB default is `{"enabled": false, "groups": []}`; the feature is inert until an admin configures it. No DB migration (config is stored in the existing `config` JSON row).
- **i18n.** Every new user-facing string is added to BOTH `src/lib/i18n/locales/en-US/translation.json` and `nl-NL/translation.json`, keys alphabetically sorted. en-US value is `""` (means "use the key text"); nl-NL gets the Dutch translation. Admin-authored policy messages are dynamic data, NOT translation keys.
- **Capability-key parity.** Backend `KNOWN_CAPABILITY_KEYS` must mirror `DEFAULT_CAPABILITIES` in `src/lib/constants.ts` (12 keys). If one changes, change both.
- **Shell commands single-line**, no backslash continuations (repo convention).
- **Formatting/linting:** backend Black (`npm run format:backend`), PyLint (`npm run lint:backend`); frontend Prettier (`npm run format`), ESLint (`npm run lint:frontend`), type-check (`npm run check`).

---

### Task 1: Backend resolver + validator module (pure, fully unit-tested)

**Files:**
- Create: `backend/open_webui/utils/capability_policy.py`
- Test: `backend/open_webui/test/util/test_capability_policy.py`

**Interfaces:**
- Produces (consumed by Tasks 3, 4, 5):
  - `KNOWN_CAPABILITY_KEYS: set[str]`
  - `is_policy_active(policy: Optional[dict]) -> bool`
  - `resolve_capabilities(capabilities: Optional[dict], policy: Optional[dict]) -> Optional[dict]`
  - `enforce_policy_on_models(models: list[dict], policy: Optional[dict]) -> None` (in-place)
  - `validate_exclusion_policy(policy: Optional[dict]) -> dict`
  - `class CapabilityPolicyError(ValueError)`

- [ ] **Step 1: Write the failing tests**

Create `backend/open_webui/test/util/test_capability_policy.py`:

```python
import pytest

from open_webui.utils.capability_policy import (
    CapabilityPolicyError,
    enforce_policy_on_models,
    resolve_capabilities,
    validate_exclusion_policy,
)

POLICY = {
    "enabled": True,
    "default_message": "default",
    "groups": [
        {"id": "g1", "label": "G1", "capabilities": ["file_upload", "web_search"], "message": "msg1"},
        {"id": "g2", "label": "G2", "capabilities": ["terminal", "code_interpreter"], "message": ""},
    ],
}


def test_keeps_first_enabled_in_declared_order():
    caps = {"file_upload": True, "web_search": True, "vision": True}
    out = resolve_capabilities(caps, POLICY)
    assert out["file_upload"] is True
    assert out["web_search"] is False
    assert out["vision"] is True  # ungrouped untouched


def test_second_group_is_independent():
    out = resolve_capabilities({"terminal": True, "code_interpreter": True}, POLICY)
    assert out["terminal"] is True
    assert out["code_interpreter"] is False


def test_single_enabled_unchanged():
    assert resolve_capabilities({"web_search": True}, POLICY) == {"web_search": True}


def test_disabled_policy_is_noop():
    caps = {"file_upload": True, "web_search": True}
    assert resolve_capabilities(caps, {**POLICY, "enabled": False}) == caps


def test_empty_groups_is_noop():
    caps = {"file_upload": True, "web_search": True}
    assert resolve_capabilities(caps, {"enabled": True, "groups": []}) == caps


def test_none_capabilities_passthrough():
    assert resolve_capabilities(None, POLICY) is None


def test_does_not_mutate_input():
    caps = {"file_upload": True, "web_search": True}
    resolve_capabilities(caps, POLICY)
    assert caps == {"file_upload": True, "web_search": True}


def test_enforce_policy_on_models_inplace():
    models = [
        {"id": "a", "info": {"meta": {"capabilities": {"file_upload": True, "web_search": True}}}},
        {"id": "b"},  # no info -> skipped
        {"id": "c", "info": {"meta": {}}},  # no caps -> skipped
    ]
    enforce_policy_on_models(models, POLICY)
    assert models[0]["info"]["meta"]["capabilities"] == {"file_upload": True, "web_search": False}
    assert "info" not in models[1]
    assert models[2]["info"]["meta"] == {}


def test_validate_ok():
    out = validate_exclusion_policy(POLICY)
    assert out["enabled"] is True
    assert out["groups"][0]["capabilities"] == ["file_upload", "web_search"]


def test_validate_none_returns_inert():
    assert validate_exclusion_policy(None) == {"enabled": False, "default_message": "", "groups": []}


def test_validate_rejects_unknown_capability():
    with pytest.raises(CapabilityPolicyError):
        validate_exclusion_policy({"enabled": True, "groups": [{"id": "g", "capabilities": ["nope"]}]})


def test_validate_rejects_duplicate_ids():
    with pytest.raises(CapabilityPolicyError):
        validate_exclusion_policy(
            {"enabled": True, "groups": [{"id": "g", "capabilities": []}, {"id": "g", "capabilities": []}]}
        )


def test_validate_rejects_capability_in_two_groups():
    with pytest.raises(CapabilityPolicyError):
        validate_exclusion_policy(
            {"enabled": True, "groups": [{"id": "a", "capabilities": ["vision"]}, {"id": "b", "capabilities": ["vision"]}]}
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lexlubbers/Code/soev/open-webui/backend && python -m pytest open_webui/test/util/test_capability_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'open_webui.utils.capability_policy'`.

- [ ] **Step 3: Implement the module**

Create `backend/open_webui/utils/capability_policy.py`:

```python
"""Mutually-exclusive model-capability policy (soev data-sovereignty feature).

Pure helpers that make selected per-model capabilities mutually exclusive. The
policy shape is::

    {"enabled": bool, "default_message": str,
     "groups": [{"id": str, "label": str, "capabilities": [str], "message": str}]}

Enforced server-side at model resolution (``utils/models.py``) and at model save
(``routers/models.py``); mirrored to the frontend via ``/api/config`` for editor
UX. ``KNOWN_CAPABILITY_KEYS`` mirrors ``DEFAULT_CAPABILITIES`` in
``src/lib/constants.ts`` — keep the two in sync.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# Mirror of DEFAULT_CAPABILITIES keys in src/lib/constants.ts (12 keys).
KNOWN_CAPABILITY_KEYS = {
    "file_context",
    "vision",
    "file_upload",
    "web_search",
    "image_generation",
    "code_interpreter",
    "document_writer",
    "terminal",
    "citations",
    "status_updates",
    "usage",
    "builtin_tools",
}


class CapabilityPolicyError(ValueError):
    """Raised when an admin-supplied exclusion policy is invalid."""


def is_policy_active(policy: Optional[dict]) -> bool:
    """True when a usable, enabled policy with at least one group is present."""
    return bool(policy and policy.get("enabled") and policy.get("groups"))


def resolve_capabilities(
    capabilities: Optional[dict], policy: Optional[dict]
) -> Optional[dict]:
    """Return a policy-compliant copy of ``capabilities``.

    For each group, walk its capabilities in declared order, keep the first one
    that is enabled, and force every other group member off. Capabilities not in
    any group are left untouched. No-op (returns the input unchanged) when the
    policy is inactive or ``capabilities`` is falsy. Never mutates the input.
    """
    if not capabilities or not is_policy_active(policy):
        return capabilities

    result = dict(capabilities)
    for group in policy.get("groups") or []:
        kept = False
        for cap in group.get("capabilities") or []:
            if result.get(cap):
                if kept:
                    result[cap] = False
                else:
                    kept = True
    return result


def enforce_policy_on_models(models: list[dict], policy: Optional[dict]) -> None:
    """In-place: apply ``resolve_capabilities`` to each model's
    ``info.meta.capabilities``. No-op when the policy is inactive."""
    if not is_policy_active(policy):
        return
    for model in models:
        info = model.get("info") or {}
        meta = info.get("meta") or {}
        caps = meta.get("capabilities")
        if caps:
            meta["capabilities"] = resolve_capabilities(caps, policy)


def validate_exclusion_policy(policy: Optional[dict]) -> dict:
    """Validate + normalize an admin-supplied policy.

    Returns a normalized ``{enabled, default_message, groups}`` dict. A falsy
    policy normalizes to the inert default. Raises ``CapabilityPolicyError`` on
    unknown capability keys, duplicate group ids, or a capability appearing in
    more than one group.
    """
    if not policy:
        return {"enabled": False, "default_message": "", "groups": []}
    if not isinstance(policy, dict):
        raise CapabilityPolicyError("policy must be an object")

    raw_groups = policy.get("groups") or []
    if not isinstance(raw_groups, list):
        raise CapabilityPolicyError("groups must be a list")

    seen_ids: set[str] = set()
    seen_caps: set[str] = set()
    groups = []
    for idx, group in enumerate(raw_groups):
        if not isinstance(group, dict):
            raise CapabilityPolicyError(f"group #{idx + 1} must be an object")

        gid = str(group.get("id") or "").strip()
        if not gid:
            raise CapabilityPolicyError(f"group #{idx + 1} is missing an id")
        if gid in seen_ids:
            raise CapabilityPolicyError(f"duplicate group id '{gid}'")
        seen_ids.add(gid)

        caps = group.get("capabilities") or []
        if not isinstance(caps, list):
            raise CapabilityPolicyError(f"group '{gid}' capabilities must be a list")

        normalized_caps = []
        for cap in caps:
            cap = str(cap)
            if cap not in KNOWN_CAPABILITY_KEYS:
                raise CapabilityPolicyError(f"group '{gid}' has unknown capability '{cap}'")
            if cap in seen_caps:
                raise CapabilityPolicyError(f"capability '{cap}' appears in more than one group")
            seen_caps.add(cap)
            normalized_caps.append(cap)

        groups.append(
            {
                "id": gid,
                "label": str(group.get("label") or gid),
                "capabilities": normalized_caps,
                "message": str(group.get("message") or ""),
            }
        )

    return {
        "enabled": bool(policy.get("enabled", False)),
        "default_message": str(policy.get("default_message") or ""),
        "groups": groups,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lexlubbers/Code/soev/open-webui/backend && python -m pytest open_webui/test/util/test_capability_policy.py -v`
Expected: PASS (all 13 tests green).

- [ ] **Step 5: Format + checkpoint (no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run format:backend`
Confirm the new files are Black-clean and tests still pass. Do not commit.

---

### Task 2: Backend config + app-state registration

**Files:**
- Modify: `backend/open_webui/config.py` (after `DEFAULT_MODEL_METADATA`, ~line 1358)
- Modify: `backend/open_webui/main.py` (import ~line 529; registration ~line 1268)

**Interfaces:**
- Produces: `config.MODEL_CAPABILITY_EXCLUSIONS` (PersistentConfig) and `request.app.state.config.MODEL_CAPABILITY_EXCLUSIONS` (the policy dict), consumed by Tasks 3, 4, 5, 6.

- [ ] **Step 1: Add the PersistentConfig**

In `backend/open_webui/config.py`, immediately AFTER the `DEFAULT_MODEL_METADATA` block (which ends at the line `)` closing `DEFAULT_MODEL_METADATA = PersistentConfig(...)`, ~line 1358), insert:

```python

try:
    model_capability_exclusions = json.loads(
        os.environ.get("MODEL_CAPABILITY_EXCLUSIONS", "{}")
    )
except Exception as e:
    log.exception(f"Error loading MODEL_CAPABILITY_EXCLUSIONS: {e}")
    model_capability_exclusions = {}

MODEL_CAPABILITY_EXCLUSIONS = PersistentConfig(
    "MODEL_CAPABILITY_EXCLUSIONS",
    "models.capability_exclusions",
    model_capability_exclusions,
)
```

- [ ] **Step 2: Import it in main.py**

In `backend/open_webui/main.py`, find the line `    DEFAULT_MODEL_METADATA,` (~line 529, inside the `from open_webui.config import (` block) and add directly below it:

```python
    MODEL_CAPABILITY_EXCLUSIONS,
```

- [ ] **Step 3: Register it on app.state.config**

In `backend/open_webui/main.py`, find `app.state.config.DEFAULT_MODEL_METADATA = DEFAULT_MODEL_METADATA` (~line 1268) and add directly below it:

```python
app.state.config.MODEL_CAPABILITY_EXCLUSIONS = MODEL_CAPABILITY_EXCLUSIONS
```

- [ ] **Step 4: Verify it loads (checkpoint, no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui/backend && python -c "import open_webui.config as c; print(c.MODEL_CAPABILITY_EXCLUSIONS.value)"`
Expected: prints `{}` (the inert default). No import errors.

---

### Task 3: Runtime enforcement in `get_all_models`

**Files:**
- Modify: `backend/open_webui/utils/models.py` (import near top; insert after ~line 324)

**Interfaces:**
- Consumes: `enforce_policy_on_models` (Task 1), `request.app.state.config.MODEL_CAPABILITY_EXCLUSIONS` (Task 2).

- [ ] **Step 1: Add the import**

In `backend/open_webui/utils/models.py`, add to the imports near the top of the file:

```python
from open_webui.utils.capability_policy import enforce_policy_on_models
```

- [ ] **Step 2: Insert the enforcement block**

In `get_all_models`, the `if default_metadata:` block ends just before the comment `# Batch-fetch all function valves in one query to avoid N+1 DB hits`. Insert the following IMMEDIATELY before that comment (so it runs unconditionally, after the metadata merge, before `request.app.state.MODELS` is populated):

```python
    # Enforce the mutually-exclusive capability policy (data-sovereignty).
    # Unconditional — must run even when no DEFAULT_MODEL_METADATA is configured,
    # so it is intentionally OUTSIDE the `if default_metadata:` block above.
    enforce_policy_on_models(
        models,
        getattr(request.app.state.config, "MODEL_CAPABILITY_EXCLUSIONS", None),
    )

```

- [ ] **Step 3: Verify resolver behavior is covered (no new unit test needed)**

The per-model logic is `enforce_policy_on_models`, already tested in Task 1 (`test_enforce_policy_on_models_inplace`). Re-run to confirm nothing regressed:

Run: `cd /Users/lexlubbers/Code/soev/open-webui/backend && python -m pytest open_webui/test/util/test_capability_policy.py -v`
Expected: PASS.

- [ ] **Step 4: Manual runtime check (checkpoint, no commit)**

After the full stack is running (Task 13 covers run instructions), with a policy enabling group `{file_upload, web_search}` and a model that has both true in the DB, calling `GET /api/models` should return that model with `web_search: false`. Note this for the Task 13 manual pass; no commit here.

---

### Task 4: Save-time enforcement in models router

**Files:**
- Modify: `backend/open_webui/routers/models.py` (import; before persist in `create_new_model` ~line 232 and `update_model_by_id` ~line 619)

**Interfaces:**
- Consumes: `resolve_capabilities` (Task 1), `request.app.state.config.MODEL_CAPABILITY_EXCLUSIONS` (Task 2).

- [ ] **Step 1: Add the import**

In `backend/open_webui/routers/models.py`, add to the imports near the top:

```python
from open_webui.utils.capability_policy import resolve_capabilities
```

- [ ] **Step 2: Sanitize before insert (create)**

In `create_new_model`, locate the line `        model = await Models.insert_new_model(form_data, user.id, db=db)` and insert IMMEDIATELY before it:

```python
        form_data.meta.capabilities = resolve_capabilities(
            form_data.meta.capabilities,
            getattr(request.app.state.config, "MODEL_CAPABILITY_EXCLUSIONS", None),
        )
```

- [ ] **Step 3: Sanitize before update**

In `update_model_by_id`, locate the line
`    model = await Models.update_model_by_id(form_data.id, ModelForm(**form_data.model_dump()), db=db)`
and insert IMMEDIATELY before it:

```python
    form_data.meta.capabilities = resolve_capabilities(
        form_data.meta.capabilities,
        getattr(request.app.state.config, "MODEL_CAPABILITY_EXCLUSIONS", None),
    )
```

- [ ] **Step 4: Format + checkpoint (no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run format:backend`
Confirm clean. Manual API check (curl create with a conflicting combo → GET model shows sanitized) is folded into Task 13.

---

### Task 5: Extend the `models` admin config endpoint

**Files:**
- Modify: `backend/open_webui/routers/configs.py` (import; `ModelsConfigForm` ~619; `get_models_config` ~635; `set_models_config` ~646)

**Interfaces:**
- Consumes: `validate_exclusion_policy`, `CapabilityPolicyError` (Task 1).
- Produces: `MODEL_CAPABILITY_EXCLUSIONS` in the GET/POST `/api/v1/configs/models` payload (consumed by Task 11 frontend).

- [ ] **Step 1: Add imports**

In `backend/open_webui/routers/configs.py`, add to the imports near the top:

```python
from open_webui.utils.capability_policy import (
    CapabilityPolicyError,
    validate_exclusion_policy,
)
```

Confirm `HTTPException` and `status` are already imported in this file (they are used by other handlers). If not, add `from fastapi import HTTPException, status`.

- [ ] **Step 2: Extend the form model**

In `ModelsConfigForm` (line ~619), add the new optional field after `DEFAULT_MODEL_PARAMS`:

```python
class ModelsConfigForm(BaseModel):
    DEFAULT_MODELS: Optional[str]
    DEFAULT_PINNED_MODELS: Optional[str]
    MODEL_ORDER_LIST: Optional[list[str]]
    DEFAULT_MODEL_METADATA: Optional[dict] = None
    DEFAULT_MODEL_PARAMS: Optional[dict] = None
    MODEL_CAPABILITY_EXCLUSIONS: Optional[dict] = None
```

- [ ] **Step 3: Return it from GET**

In `get_models_config`, add the key to the returned dict (after `DEFAULT_MODEL_PARAMS`):

```python
        "MODEL_CAPABILITY_EXCLUSIONS": request.app.state.config.MODEL_CAPABILITY_EXCLUSIONS,
```

- [ ] **Step 4: Validate + persist in POST**

In `set_models_config`, add validation BEFORE the existing assignments, then assign the validated policy, and include it in the returned dict. The handler becomes:

```python
@router.post('/models', response_model=ModelsConfigForm)
async def set_models_config(request: Request, form_data: ModelsConfigForm, user=Depends(get_admin_user)):
    try:
        validated_exclusions = validate_exclusion_policy(form_data.MODEL_CAPABILITY_EXCLUSIONS)
    except CapabilityPolicyError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    request.app.state.config.DEFAULT_MODELS = form_data.DEFAULT_MODELS
    request.app.state.config.DEFAULT_PINNED_MODELS = form_data.DEFAULT_PINNED_MODELS
    request.app.state.config.MODEL_ORDER_LIST = form_data.MODEL_ORDER_LIST
    request.app.state.config.DEFAULT_MODEL_METADATA = form_data.DEFAULT_MODEL_METADATA
    request.app.state.config.DEFAULT_MODEL_PARAMS = form_data.DEFAULT_MODEL_PARAMS
    request.app.state.config.MODEL_CAPABILITY_EXCLUSIONS = validated_exclusions
    return {
        'DEFAULT_MODELS': request.app.state.config.DEFAULT_MODELS,
        'DEFAULT_PINNED_MODELS': request.app.state.config.DEFAULT_PINNED_MODELS,
        'MODEL_ORDER_LIST': request.app.state.config.MODEL_ORDER_LIST,
        'DEFAULT_MODEL_METADATA': request.app.state.config.DEFAULT_MODEL_METADATA,
        'DEFAULT_MODEL_PARAMS': request.app.state.config.DEFAULT_MODEL_PARAMS,
        'MODEL_CAPABILITY_EXCLUSIONS': request.app.state.config.MODEL_CAPABILITY_EXCLUSIONS,
    }
```

- [ ] **Step 5: Format + checkpoint (no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run format:backend`
Confirm clean.

---

### Task 6: Expose policy via `/api/config`

**Files:**
- Modify: `backend/open_webui/main.py` `get_app_config` (~line 3206)

**Interfaces:**
- Produces: top-level `model_capability_exclusions` key on `/api/config` (consumed by Tasks 7, 9).

- [ ] **Step 1: Add the key to the response**

In `get_app_config`, inside the returned dict, find the `'oauth': {'providers': ...}` line and insert directly after it:

```python
        'model_capability_exclusions': getattr(app.state.config, 'MODEL_CAPABILITY_EXCLUSIONS', None) or {},
```

- [ ] **Step 2: Verify (checkpoint, no commit)**

With the backend running, `GET /api/config` (unauthenticated) returns `"model_capability_exclusions": {}` by default. Confirm during Task 13 manual pass.

---

### Task 7: Frontend Config type

**Files:**
- Modify: `src/lib/stores/index.ts` (`Config` type, ~line 344)

**Interfaces:**
- Produces: typed `$config.model_capability_exclusions` (consumed by Tasks 8, 9).

- [ ] **Step 1: Add the field to the Config type**

In `src/lib/stores/index.ts`, inside the `type Config = { ... }` block, add (e.g. immediately after the `oauth: { providers: {...} };` field):

```typescript
	model_capability_exclusions?: {
		enabled?: boolean;
		default_message?: string;
		groups?: { id: string; label?: string; capabilities: string[]; message?: string }[];
	};
```

- [ ] **Step 2: Type-check (checkpoint, no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run check`
Expected: no NEW type errors from this change.

---

### Task 8: Frontend pure toggle helper (+ Vitest)

**Files:**
- Modify: `src/lib/utils/capabilities.ts`
- Test: `src/lib/utils/capabilities.test.ts` (new)

**Interfaces:**
- Produces (consumed by Task 9):
  - `applyExclusionGroupsOnToggle(capabilities, policy, changedKey) -> Record<string, boolean>`
  - `getExclusionNotice(capabilities, policy, changedKey) -> string | null`
  - types `CapabilityExclusionPolicy`, `CapabilityExclusionGroup`

- [ ] **Step 1: Write the failing tests**

Create `src/lib/utils/capabilities.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { applyExclusionGroupsOnToggle, getExclusionNotice } from './capabilities';

const policy = {
	enabled: true,
	default_message: 'default',
	groups: [{ id: 'g1', capabilities: ['file_upload', 'web_search'], message: 'msg1' }]
};

describe('applyExclusionGroupsOnToggle', () => {
	it('disables group-mates when one is enabled', () => {
		const out = applyExclusionGroupsOnToggle(
			{ file_upload: true, web_search: true },
			policy,
			'file_upload'
		);
		expect(out.file_upload).toBe(true);
		expect(out.web_search).toBe(false);
	});

	it('is a no-op when the toggled key was turned off', () => {
		const out = applyExclusionGroupsOnToggle(
			{ file_upload: false, web_search: true },
			policy,
			'file_upload'
		);
		expect(out.web_search).toBe(true);
	});

	it('is a no-op when the policy is disabled', () => {
		const out = applyExclusionGroupsOnToggle(
			{ file_upload: true, web_search: true },
			{ ...policy, enabled: false },
			'file_upload'
		);
		expect(out.web_search).toBe(true);
	});

	it('does not mutate the input', () => {
		const input = { file_upload: true, web_search: true };
		applyExclusionGroupsOnToggle(input, policy, 'file_upload');
		expect(input.web_search).toBe(true);
	});
});

describe('getExclusionNotice', () => {
	it('returns the group message when a mate will be disabled', () => {
		expect(getExclusionNotice({ file_upload: true, web_search: true }, policy, 'file_upload')).toBe(
			'msg1'
		);
	});

	it('falls back to default_message when the group has none', () => {
		const p = { ...policy, groups: [{ id: 'g1', capabilities: ['file_upload', 'web_search'] }] };
		expect(getExclusionNotice({ file_upload: true, web_search: true }, p, 'file_upload')).toBe(
			'default'
		);
	});

	it('returns null when nothing will be disabled', () => {
		expect(
			getExclusionNotice({ file_upload: true, web_search: false }, policy, 'file_upload')
		).toBeNull();
	});
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npx vitest run src/lib/utils/capabilities.test.ts`
Expected: FAIL — `applyExclusionGroupsOnToggle is not a function` / export missing.

- [ ] **Step 3: Implement the helpers**

Append to `src/lib/utils/capabilities.ts` (keep the existing `getDefaultCapabilities`):

```typescript
export interface CapabilityExclusionGroup {
	id: string;
	label?: string;
	capabilities: string[];
	message?: string;
}

export interface CapabilityExclusionPolicy {
	enabled?: boolean;
	default_message?: string;
	groups?: CapabilityExclusionGroup[];
}

/**
 * Latest-click-wins mutual exclusion for the capabilities editor. When
 * `changedKey` was just enabled, turn off every other capability that shares an
 * exclusion group with it. Returns a NEW object; never mutates the input. No-op
 * when the policy is disabled/empty or the key was turned off.
 */
export function applyExclusionGroupsOnToggle(
	capabilities: Record<string, boolean>,
	policy: CapabilityExclusionPolicy | null | undefined,
	changedKey: string
): Record<string, boolean> {
	const next = { ...capabilities };
	if (!policy?.enabled || !next[changedKey]) {
		return next;
	}
	const group = (policy.groups ?? []).find((g) => (g.capabilities ?? []).includes(changedKey));
	if (!group) {
		return next;
	}
	for (const cap of group.capabilities ?? []) {
		if (cap !== changedKey) {
			next[cap] = false;
		}
	}
	return next;
}

/**
 * The message to show when enabling `changedKey` would disable other members of
 * its group, or null when nothing would be disabled.
 */
export function getExclusionNotice(
	capabilities: Record<string, boolean>,
	policy: CapabilityExclusionPolicy | null | undefined,
	changedKey: string
): string | null {
	if (!policy?.enabled || !capabilities[changedKey]) {
		return null;
	}
	const group = (policy.groups ?? []).find((g) => (g.capabilities ?? []).includes(changedKey));
	if (!group) {
		return null;
	}
	const willDisable = (group.capabilities ?? []).some((c) => c !== changedKey && capabilities[c]);
	if (!willDisable) {
		return null;
	}
	return group.message || policy.default_message || '';
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npx vitest run src/lib/utils/capabilities.test.ts`
Expected: PASS (7 tests).

---

### Task 9: Capabilities editor — radio auto-disable + notice

**Files:**
- Modify: `src/lib/components/workspace/Models/Capabilities.svelte`

**Interfaces:**
- Consumes: `applyExclusionGroupsOnToggle`, `getExclusionNotice` (Task 8), `$config.model_capability_exclusions` (Task 6/7).

- [ ] **Step 1: Add imports + reactive policy + notice state**

In the `<script>` of `src/lib/components/workspace/Models/Capabilities.svelte`, add the helper import (the `config` store is already imported at line 3):

```typescript
	import { applyExclusionGroupsOnToggle, getExclusionNotice } from '$lib/utils/capabilities';
```

Then, after the `visibleCapabilities` reactive block (after line ~103), add:

```typescript
	$: exclusionPolicy = $config?.model_capability_exclusions ?? null;
	let exclusionNotice = '';
```

- [ ] **Step 2: Replace the checkbox change handler with the radio logic**

Replace the existing handler (lines ~113–118):

```svelte
				<Checkbox
					state={capabilities[capability] ? 'checked' : 'unchecked'}
					on:change={(e) => {
						capabilities[capability] = e.detail === 'checked';
					}}
				/>
```

with:

```svelte
				<Checkbox
					state={capabilities[capability] ? 'checked' : 'unchecked'}
					on:change={(e) => {
						const checked = e.detail === 'checked';
						const updated = { ...capabilities, [capability]: checked };
						exclusionNotice = getExclusionNotice(updated, exclusionPolicy, capability) ?? '';
						capabilities = applyExclusionGroupsOnToggle(updated, exclusionPolicy, capability);
					}}
				/>
```

- [ ] **Step 3: Render the notice**

Immediately after the capabilities grid `</div>` (the `<div class="flex items-center mt-2 flex-wrap">…</div>` block ending at line ~127), and before the component's outer closing `</div>` (line ~128), add:

```svelte
	{#if exclusionNotice}
		<div class="mt-2 text-xs text-gray-500">{exclusionNotice}</div>
	{/if}
```

- [ ] **Step 4: Type-check + format (checkpoint, no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run check`
Expected: no new errors. Then `npm run format`. Do not commit.

> Note: `Capabilities.svelte` is shared by `ModelEditor.svelte`, the admin
> `ModelSettingsModal.svelte` (default capabilities), so the radio behavior
> applies consistently in all of them. The simple/onboarding editor uses a
> different component and is intentionally unaffected (still protected
> server-side by Tasks 3–4).

---

### Task 10: Admin editor component `CapabilityExclusions.svelte`

**Files:**
- Create: `src/lib/components/admin/Settings/Models/CapabilityExclusions.svelte`

**Interfaces:**
- Consumes: `DEFAULT_CAPABILITIES` (constants), i18n keys (Task 12).
- Produces: a component with `export let policy` (two-way bound by Task 11).

- [ ] **Step 1: Create the component**

Create `src/lib/components/admin/Settings/Models/CapabilityExclusions.svelte`:

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import { DEFAULT_CAPABILITIES } from '$lib/constants';
	import Switch from '$lib/components/common/Switch.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Minus from '$lib/components/icons/Minus.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	const i18n = getContext('i18n');

	export let policy: {
		enabled?: boolean;
		default_message?: string;
		groups?: { id: string; label?: string; capabilities: string[]; message?: string }[];
	} = { enabled: false, default_message: '', groups: [] };

	// Defensive shape normalization (env/DB may hold {} or partial objects).
	$: if (!policy) policy = { enabled: false, default_message: '', groups: [] };
	$: if (!Array.isArray(policy.groups)) policy.groups = [];

	const ALL_CAPABILITIES = Object.keys(DEFAULT_CAPABILITIES);

	const addGroup = () => {
		const n = (policy.groups?.length ?? 0) + 1;
		policy.groups = [
			...(policy.groups ?? []),
			{ id: `group-${n}`, label: '', capabilities: [], message: '' }
		];
	};

	const removeGroup = (idx: number) => {
		policy.groups = (policy.groups ?? []).filter((_, i) => i !== idx);
	};

	const addCapability = (groupIdx: number, cap: string) => {
		const group = policy.groups[groupIdx];
		if (!group.capabilities.includes(cap)) {
			group.capabilities = [...group.capabilities, cap];
		}
		policy.groups = policy.groups;
	};

	const removeCapability = (groupIdx: number, cap: string) => {
		const group = policy.groups[groupIdx];
		group.capabilities = group.capabilities.filter((c) => c !== cap);
		policy.groups = policy.groups;
	};

	const moveCapability = (groupIdx: number, capIdx: number, dir: -1 | 1) => {
		const group = policy.groups[groupIdx];
		const caps = [...group.capabilities];
		const target = capIdx + dir;
		if (target < 0 || target >= caps.length) return;
		[caps[capIdx], caps[target]] = [caps[target], caps[capIdx]];
		group.capabilities = caps;
		policy.groups = policy.groups;
	};

	// A capability already used by ANY other group cannot be added here.
	const usedElsewhere = (groupIdx: number, cap: string) =>
		(policy.groups ?? []).some((g, i) => i !== groupIdx && g.capabilities.includes(cap));
</script>

<div class="flex flex-col gap-2.5">
	<div class="flex w-full justify-between items-center">
		<div class="text-xs text-gray-500 font-medium">{$i18n.t('Capability Exclusions')}</div>
		<Switch bind:state={policy.enabled} />
	</div>

	<div class="text-xs text-gray-400">
		{$i18n.t(
			'Within each group, only one capability can be enabled per model. The first listed capability wins when there is a conflict.'
		)}
	</div>

	<div class="flex flex-col gap-1">
		<div class="text-xs text-gray-500">{$i18n.t('Default exclusion message')}</div>
		<textarea
			class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden resize-none"
			rows="2"
			bind:value={policy.default_message}
			placeholder={$i18n.t('Shown when a group has no message of its own')}
		/>
	</div>

	{#each policy.groups ?? [] as group, groupIdx (groupIdx)}
		<div class="flex flex-col gap-2 rounded-lg border border-gray-100 dark:border-gray-850 p-3">
			<div class="flex items-center gap-2">
				<input
					class="flex-1 rounded-lg px-3 py-1.5 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden"
					bind:value={group.label}
					placeholder={$i18n.t('Group label')}
				/>
				<button
					type="button"
					class="p-1 text-gray-500 hover:text-red-500"
					on:click={() => removeGroup(groupIdx)}
				>
					<Minus className="size-4" />
				</button>
			</div>

			{#if group.capabilities.length > 0}
				<div class="flex flex-col gap-1">
					{#each group.capabilities as cap, capIdx (cap)}
						<div class="flex items-center gap-2 text-sm">
							<span class="text-gray-400 w-5 text-right">{capIdx + 1}.</span>
							<span class="flex-1 font-mono text-xs">{cap}</span>
							<button
								type="button"
								class="p-0.5 disabled:opacity-30"
								disabled={capIdx === 0}
								on:click={() => moveCapability(groupIdx, capIdx, -1)}
							>
								<ChevronUp className="size-3" />
							</button>
							<button
								type="button"
								class="p-0.5 disabled:opacity-30"
								disabled={capIdx === group.capabilities.length - 1}
								on:click={() => moveCapability(groupIdx, capIdx, 1)}
							>
								<ChevronDown className="size-3" />
							</button>
							<button
								type="button"
								class="p-0.5 text-gray-500 hover:text-red-500"
								on:click={() => removeCapability(groupIdx, cap)}
							>
								<XMark className="size-3" />
							</button>
						</div>
					{/each}
				</div>
			{/if}

			<div class="flex flex-wrap gap-1">
				{#each ALL_CAPABILITIES.filter((c) => !group.capabilities.includes(c) && !usedElsewhere(groupIdx, c)) as cap}
					<button
						type="button"
						class="text-xs px-2 py-1 rounded-lg bg-gray-50 dark:bg-gray-850 hover:bg-gray-100 dark:hover:bg-gray-800 font-mono"
						on:click={() => addCapability(groupIdx, cap)}
					>
						+ {cap}
					</button>
				{/each}
			</div>

			<textarea
				class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden resize-none"
				rows="2"
				bind:value={group.message}
				placeholder={$i18n.t('Message (optional)')}
			/>
		</div>
	{/each}

	<button
		type="button"
		class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 self-start"
		on:click={addGroup}
	>
		<Plus className="size-3" />
		{$i18n.t('Add exclusion group')}
	</button>
</div>
```

- [ ] **Step 2: Type-check (checkpoint, no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run check`
Expected: no new errors. (The component is not yet mounted; Task 11 wires it in.)

> If `XMark`, `ChevronUp`, `ChevronDown`, `Plus`, `Minus`, or `Switch` import
> paths differ, copy the exact paths used in `ModelSettingsModal.svelte`
> (lines 15–36), which imports all of these.

---

### Task 11: Wire the editor into `ModelSettingsModal.svelte`

**Files:**
- Modify: `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte`

**Interfaces:**
- Consumes: `CapabilityExclusions.svelte` (Task 10); `getModelsConfig`/`setModelsConfig` already return/accept `MODEL_CAPABILITY_EXCLUSIONS` after Task 5.

- [ ] **Step 1: Import the component + add state**

In the `<script>` of `ModelSettingsModal.svelte`, add the import (near the other section imports, ~line 33):

```typescript
	import CapabilityExclusions from './CapabilityExclusions.svelte';
```

Add two state variables (near the other `let show*`/`let default*` declarations, ~lines 58–68):

```typescript
	let showCapabilityExclusions = false;
	let capabilityExclusions = { enabled: false, default_message: '', groups: [] };
```

- [ ] **Step 2: Load the policy in init()**

In `init()`, after `defaultParams = config?.DEFAULT_MODEL_PARAMS ?? {};` (line ~116), add:

```typescript
		capabilityExclusions = config?.MODEL_CAPABILITY_EXCLUSIONS ?? {
			enabled: false,
			default_message: '',
			groups: []
		};
```

- [ ] **Step 3: Include the policy in submitHandler()**

In `submitHandler()`, add the field to the `setModelsConfig` payload object (after `DEFAULT_MODEL_PARAMS: ...`, ~line 140):

```typescript
			MODEL_CAPABILITY_EXCLUSIONS: capabilityExclusions,
```

- [ ] **Step 4: Render the collapsible section**

In the `defaults` tab, the "Model Parameters" section begins with a `<div>` wrapping a button that toggles `showDefaultParams`. Insert the following block IMMEDIATELY before that `<div>` (i.e., right after the `<hr ... />` that precedes the Model Parameters section, ~line 366):

```svelte
							<div>
								<button
									class="flex w-full justify-between items-center"
									type="button"
									on:click={() => {
										showCapabilityExclusions = !showCapabilityExclusions;
									}}
								>
									<div class="text-xs text-gray-500 font-medium">
										{$i18n.t('Capability Exclusions')}
									</div>
									<div>
										{#if showCapabilityExclusions}
											<ChevronUp className="size-3" />
										{:else}
											<ChevronDown className="size-3" />
										{/if}
									</div>
								</button>

								{#if showCapabilityExclusions}
									<div class="mt-2">
										<CapabilityExclusions bind:policy={capabilityExclusions} />
									</div>
								{/if}
							</div>

							<hr class=" border-gray-50 dark:border-gray-800/10 my-2.5 w-full" />
```

(`ChevronUp`/`ChevronDown` are already imported in this file at lines 22–23.)

- [ ] **Step 5: Type-check + format (checkpoint, no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run check`
Expected: no new errors. Then `npm run format`.

---

### Task 12: i18n strings (en-US + nl-NL)

**Files:**
- Modify: `src/lib/i18n/locales/en-US/translation.json`
- Modify: `src/lib/i18n/locales/nl-NL/translation.json`

**Interfaces:**
- Consumes: the `$i18n.t(...)` keys introduced in Tasks 10 and 11.

- [ ] **Step 1: Add keys to en-US (alphabetical, value = "")**

Insert each key into `src/lib/i18n/locales/en-US/translation.json` at its alphabetical position, value `""`:

```json
"Add exclusion group": "",
"Capability Exclusions": "",
"Default exclusion message": "",
"Group label": "",
"Message (optional)": "",
"Shown when a group has no message of its own": "",
"Within each group, only one capability can be enabled per model. The first listed capability wins when there is a conflict.": "",
```

- [ ] **Step 2: Add the same keys to nl-NL (with Dutch values)**

Insert into `src/lib/i18n/locales/nl-NL/translation.json` at the matching alphabetical positions:

```json
"Add exclusion group": "Uitsluitingsgroep toevoegen",
"Capability Exclusions": "Mogelijkheidsuitsluitingen",
"Default exclusion message": "Standaard uitsluitingsbericht",
"Group label": "Groepslabel",
"Message (optional)": "Bericht (optioneel)",
"Shown when a group has no message of its own": "Wordt getoond wanneer een groep geen eigen bericht heeft",
"Within each group, only one capability can be enabled per model. The first listed capability wins when there is a conflict.": "Binnen elke groep kan per model slechts één mogelijkheid worden ingeschakeld. De eerst vermelde mogelijkheid wint bij een conflict.",
```

- [ ] **Step 3: Verify JSON validity (checkpoint, no commit)**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && node -e "JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/en-US/translation.json')); JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/nl-NL/translation.json')); console.log('ok')"`
Expected: prints `ok` (no JSON parse errors / trailing-comma mistakes).

---

### Task 13: Env example, full verification, manual E2E

**Files:**
- Modify: `.env.example` (documented, commented seed)

- [ ] **Step 1: Document the env seed**

Add to `.env.example` (single line, commented):

```bash
# Mutually-exclusive model capabilities (data-sovereignty). Seeds the policy on
# first boot; afterwards manage it in Admin > Settings > Models > Capability Exclusions.
# MODEL_CAPABILITY_EXCLUSIONS='{"enabled":true,"default_message":"These capabilities cannot be enabled together on the same model.","groups":[{"id":"external-data","label":"External data access","capabilities":["file_upload","web_search"],"message":"A model with document access cannot also search the web, to prevent data exfiltration."}]}'
```

- [ ] **Step 2: Run the full backend test suite for the new module**

Run: `cd /Users/lexlubbers/Code/soev/open-webui/backend && python -m pytest open_webui/test/util/test_capability_policy.py -v`
Expected: PASS.

- [ ] **Step 3: Run the frontend unit tests + type-check + lint**

Run: `cd /Users/lexlubbers/Code/soev/open-webui && npx vitest run src/lib/utils/capabilities.test.ts`
Expected: PASS.
Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run check`
Expected: no new type errors.
Run: `cd /Users/lexlubbers/Code/soev/open-webui && npm run lint:frontend`
Expected: clean (auto-fix applied).

- [ ] **Step 4: Manual end-to-end (two terminals)**

Terminal 1 (backend): `cd /Users/lexlubbers/Code/soev/open-webui && open-webui dev`
Terminal 2 (frontend): `cd /Users/lexlubbers/Code/soev/open-webui && npm run dev`

Verify, in order:
1. Admin → Settings → Models → "Settings" → Defaults tab → expand **Capability Exclusions**. Enable it, add a group `external-data` with `file_upload` then `web_search`, set a message, Save. Confirm a success toast.
2. Reopen the modal → the group persists (loaded from DB via `getModelsConfig`).
3. Edit any model (workspace or admin) → Capabilities: enabling `web_search` auto-unchecks `file_upload` (and vice-versa), and the configured message appears below the grid.
4. Via API, create a model with BOTH `file_upload` and `web_search` true:
   `curl -s -X POST localhost:8080/api/v1/models/create -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"id":"x-test","name":"x","meta":{"capabilities":{"file_upload":true,"web_search":true}},"params":{}}'`
   Then GET it back and confirm `web_search` is `false` (save-time sanitize, priority = declared order).
5. Toggle the policy OFF and Save → the editor no longer auto-disables; existing models are untouched.

- [ ] **Step 5: Final checkpoint (no commit)**

Confirm all of the above. Leave the working tree uncommitted (user opted out of git). Summarize what was verified.

---

## Self-Review

**Spec coverage:**
- Config schema (single JSON PersistentConfig) → Task 2. ✓
- Resolver (declared-order priority, pure) → Task 1. ✓
- Runtime enforcement (separate unconditional block in `get_all_models`) → Task 3. ✓
- Save-time enforcement (create + update, sanitize not reject) → Task 4. ✓
- Admin endpoint reuse (`ModelsConfigForm` + validation) → Task 5. ✓
- `/api/config` exposure → Task 6; Config type → Task 7. ✓
- Editor radio auto-disable + admin-configurable per-group/default message → Tasks 8, 9. ✓
- Admin UI in `ModelSettingsModal` → Tasks 10, 11. ✓
- Edge cases (file_context composition, ungrouped untouched, disabled = no-op, malformed env fallback, legacy conflict) → covered by resolver design (Task 1) + default-off (Task 2) + manual E2E (Task 13). ✓
- i18n en-US + nl-NL → Task 12. ✓
- Backward compatibility (default off, no migration) → Global Constraints + Task 2. ✓

**Placeholder scan:** No TBD/TODO; every code step contains complete code; commands have expected output.

**Type consistency:** `resolve_capabilities`/`enforce_policy_on_models`/`validate_exclusion_policy`/`CapabilityPolicyError` names match across Tasks 1, 3, 4, 5. Frontend `applyExclusionGroupsOnToggle`/`getExclusionNotice` match across Tasks 8, 9. Policy shape `{enabled, default_message, groups:[{id,label,capabilities,message}]}` is identical in backend, `/api/config`, Config type, helper, and editor component.
