# Data Sovereignty Guard — Per-Model Capability Warning System

## Overview

Admin-configurable per-model warnings that fire when a user first uses a flagged capability (file upload, web search, vision, etc.) in a conversation. The warning explains that data will be processed by an external provider and requires explicit acknowledgment before the message is sent. Accepted warnings are audit-logged for GDPR/DPIA compliance.

**Motivation:** Soev.ai serves Dutch public sector clients where data sovereignty is a core value proposition. When models run on external infrastructure, users must be informed before sensitive data (documents, images, search queries) leaves the organization's control.

## Current State Analysis

- **Model metadata** (`backend/open_webui/models/models.py:38-48`): `ModelMeta` uses `ConfigDict(extra='allow')` — arbitrary fields stored without migration
- **Default merging** (`backend/open_webui/utils/models.py:295-312`): `DEFAULT_MODEL_METADATA` applied to all models; per-model overrides win
- **Admin config UI**: `Capabilities.svelte`, `BuiltinTools.svelte`, `DefaultFeatures.svelte` in `src/lib/components/workspace/Models/` — rendered in both `ModelEditor.svelte` and `ModelSettingsModal.svelte`
- **Chat send flow** (`src/lib/components/chat/Chat.svelte:1789-1920`): `submitPrompt()` with validation chain → message creation → `sendMessage()`
- **ConfirmDialog** (`src/lib/components/common/ConfirmDialog.svelte`): Supports title, message (Markdown), slot, confirm/cancel events. Chat.svelte uses `eventCallback` pattern for async confirmation
- **Feature flags**: `PersistentConfig` in `config.py` → `app.state.config` in `main.py` → `/api/config` features dict → frontend `Config` type

### Key Discoveries:

- `BuiltinTools.svelte` is the cleanest pattern for a new checkbox section — single `Record<string, boolean>` prop, config guard visibility, auto-init
- Chat.svelte's `eventCallback` pattern (stored callback + `on:confirm`/`on:cancel`) is the established async confirmation pattern — no Promise-based pattern exists
- The merge logic at `utils/models.py:311-312` applies defaults where per-model value is `None`, so `data_warnings` defaults propagate automatically
- Current Alembic head: `c3d4e5f6a7b8` (TOTP 2FA)
- Helm feature flags live in `helm/open-webui-tenant/values.yaml` → `templates/open-webui/configmap.yaml`

## Desired End State

After implementation:

1. **Admins can configure per-model data warnings**: In the model editor, a "Data Sovereignty Warnings" section with checkboxes for each capability (file_upload, web_search, knowledge, vision, code_interpreter, image_generation). A textarea for a custom warning message.
2. **System-wide defaults**: Same configuration available in ModelSettingsModal for global defaults that apply to all models.
3. **Users see a warning dialog**: When sending a message that uses a flagged capability on a flagged model, a confirmation dialog appears listing the specific capabilities and the admin-configured message. The user must confirm to proceed.
4. **Once per conversation**: Each model+capability combination is warned once per conversation session. If the user enables a new capability mid-conversation, the warning fires for that new capability.
5. **Audit trail**: Every accepted warning is logged (user, model, capabilities, chat, timestamp) for compliance reporting.
6. **Feature-flagged**: Global `ENABLE_DATA_WARNINGS` toggle (default: true). When disabled, no warnings appear and config is preserved but inactive.

### Verification:

- Admin can toggle data warnings per capability per model in model editor
- Admin can set system-wide defaults in ModelSettingsModal
- User sees warning when first using a flagged capability with a flagged model
- Warning doesn't reappear for same model+capability in same conversation
- Warning reappears for new capabilities or new models mid-conversation
- Cancel aborts the send, preserving input state
- Audit log entries created on accept
- Feature flag disables all warnings when off
- Helm chart supports the feature flag

## What We're NOT Doing

- **Per-capability custom messages** — one message per model covers all its warned capabilities. Admin can reference specific capabilities in their freeform text.
- **Backend-persisted per-chat acceptance state** — component-local `Set<string>` resets on page reload, which is desirable from a security standpoint.
- **Warning for non-data-sending capabilities** — memory, citations, status updates, usage, time, chat history, notes, channels are local and don't need guards.
- **Blocking at the API level** — this is a frontend UX feature, not a hard backend gate.

## Implementation Approach

Four phases, each independently testable: backend data model + feature flag, admin config UI, chat send flow integration, Helm wiring.

The approach follows established patterns: `BuiltinTools.svelte` for the checkbox component, `eventCallback` for the confirmation dialog, `PersistentConfig` for the feature flag, `extra='allow'` for metadata storage.

---

## Phase 1: Backend Foundation

### Overview

Add the `ENABLE_DATA_WARNINGS` feature flag, `data_warnings` metadata merge support, audit log model + table, and the acceptance logging endpoint.

### Changes Required:

#### 1.1 Feature Flag — `ENABLE_DATA_WARNINGS`

**File**: `backend/open_webui/config.py`

Add after the 2FA section (~line 1754):

```python
####################################
# Data Sovereignty Warnings
####################################

ENABLE_DATA_WARNINGS = PersistentConfig(
    'ENABLE_DATA_WARNINGS',
    'features.enable_data_warnings',
    os.environ.get('ENABLE_DATA_WARNINGS', 'True').lower() == 'true',
)
```

**File**: `backend/open_webui/main.py`

Mount on app state (near line 1090, after `DEFAULT_MODEL_METADATA`):

```python
app.state.config.ENABLE_DATA_WARNINGS = ENABLE_DATA_WARNINGS
```

Expose in `/api/config` features dict (after `enable_email_invites` at line 2478):

```python
'enable_data_warnings': app.state.config.ENABLE_DATA_WARNINGS,
```

Import `ENABLE_DATA_WARNINGS` from `config` at the top of `main.py`.

#### 1.2 Default Metadata Merge — `data_warnings`

**File**: `backend/open_webui/utils/models.py`

In the merge loop (lines 306-312), add special merge handling for `data_warnings` alongside `capabilities`:

```python
for key, value in default_metadata.items():
    if key == 'capabilities':
        # Merge capabilities: defaults as base, per-model overrides win
        existing = meta.get('capabilities') or {}
        meta['capabilities'] = {**value, **existing}
    elif key == 'data_warnings':
        # Merge data_warnings: defaults as base, per-model overrides win
        existing = meta.get('data_warnings') or {}
        meta['data_warnings'] = {**value, **existing}
    elif meta.get(key) is None:
        meta[key] = copy.deepcopy(value)
```

This ensures system-wide data warning defaults are applied but per-model overrides win — same pattern as capabilities.

#### 1.3 Audit Log Model

**File**: `backend/open_webui/models/data_warnings.py` (new)

```python
"""Data sovereignty warning audit log model."""

import time
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Column, String, Text, BigInteger, JSON

from open_webui.internal.db import Base, get_db


class DataWarningLog(Base):
    __tablename__ = "data_warning_log"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    chat_id = Column(String, nullable=False, index=True)
    model_id = Column(String, nullable=False)
    capabilities = Column(JSON, nullable=False)  # list of capability strings
    warning_message = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class DataWarningLogForm(BaseModel):
    chat_id: str
    model_id: str
    capabilities: list[str]
    warning_message: Optional[str] = None


class DataWarningLogModel(BaseModel):
    id: str
    user_id: str
    chat_id: str
    model_id: str
    capabilities: list[str]
    warning_message: Optional[str]
    created_at: int


class DataWarningLogs:
    @staticmethod
    def insert_log(user_id: str, form: DataWarningLogForm) -> DataWarningLogModel:
        with get_db() as db:
            log = DataWarningLog(
                id=str(uuid.uuid4()),
                user_id=user_id,
                chat_id=form.chat_id,
                model_id=form.model_id,
                capabilities=form.capabilities,
                warning_message=form.warning_message,
                created_at=int(time.time()),
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            return DataWarningLogModel.model_validate(log)

    @staticmethod
    def get_logs_by_user(user_id: str) -> list[DataWarningLogModel]:
        with get_db() as db:
            logs = (
                db.query(DataWarningLog)
                .filter(DataWarningLog.user_id == user_id)
                .order_by(DataWarningLog.created_at.desc())
                .all()
            )
            return [DataWarningLogModel.model_validate(log) for log in logs]

    @staticmethod
    def get_logs_by_chat(chat_id: str) -> list[DataWarningLogModel]:
        with get_db() as db:
            logs = (
                db.query(DataWarningLog)
                .filter(DataWarningLog.chat_id == chat_id)
                .order_by(DataWarningLog.created_at.desc())
                .all()
            )
            return [DataWarningLogModel.model_validate(log) for log in logs]
```

#### 1.4 Alembic Migration

**File**: `backend/open_webui/migrations/versions/d4e5f6a7b8c9_add_data_warning_log.py` (new)

```python
"""add data warning log table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from open_webui.migrations.util import get_existing_tables

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = set(get_existing_tables())
    if 'data_warning_log' not in existing_tables:
        op.create_table(
            'data_warning_log',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.String(), nullable=False, index=True),
            sa.Column('chat_id', sa.String(), nullable=False, index=True),
            sa.Column('model_id', sa.String(), nullable=False),
            sa.Column('capabilities', sa.JSON(), nullable=False),
            sa.Column('warning_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )


def downgrade():
    op.drop_table('data_warning_log')
```

#### 1.5 Router — `POST /api/v1/data-warnings/accept`

**File**: `backend/open_webui/routers/data_warnings.py` (new)

```python
"""Data sovereignty warning audit logging router."""

from fastapi import APIRouter, Depends, Request
from open_webui.models.data_warnings import DataWarningLogForm, DataWarningLogModel, DataWarningLogs
from open_webui.utils.auth import get_verified_user

router = APIRouter()


@router.post("/accept", response_model=DataWarningLogModel)
async def log_data_warning_acceptance(
    request: Request,
    form: DataWarningLogForm,
    user=Depends(get_verified_user),
):
    """Log that a user accepted a data sovereignty warning."""
    if not request.app.state.config.ENABLE_DATA_WARNINGS:
        # Silently succeed — don't break the send flow if feature is off
        return DataWarningLogModel(
            id="noop",
            user_id=user.id,
            chat_id=form.chat_id,
            model_id=form.model_id,
            capabilities=form.capabilities,
            warning_message=form.warning_message,
            created_at=0,
        )
    return DataWarningLogs.insert_log(user_id=user.id, form=form)
```

**File**: `backend/open_webui/main.py`

Add import and router registration (after `invites` router, ~line 1852):

```python
from open_webui.routers import data_warnings
# ...
app.include_router(data_warnings.router, prefix='/api/v1/data-warnings', tags=['data-warnings'])
```

### Success Criteria:

#### Automated Verification:

- [ ] Alembic migration applies cleanly: `alembic upgrade head`
- [ ] Backend starts without errors: `open-webui dev`
- [ ] `POST /api/v1/data-warnings/accept` returns 200 with valid payload
- [ ] `/api/config` response includes `enable_data_warnings` in features
- [x] `npm run build` succeeds

#### Manual Verification:

- [ ] Feature flag visible in admin settings (if exposed via admin UI toggle — may defer to Phase 4)
- [ ] Data warning log entries appear in the database after API call

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Admin Configuration UI

### Overview

New `DataWarnings.svelte` component with capability checkboxes and a warning message textarea, integrated into both `ModelEditor.svelte` (per-model) and `ModelSettingsModal.svelte` (system-wide defaults).

### Changes Required:

#### 2.1 DataWarnings Component

**File**: `src/lib/components/workspace/Models/DataWarnings.svelte` (new)

Follows `BuiltinTools.svelte` pattern exactly:

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n = getContext('i18n');

	// Map warning keys to config feature guards
	// Only show warnings for capabilities whose global feature is enabled
	const warningConfigGuards: Record<string, string> = {
		web_search: 'enable_web_search',
		image_generation: 'enable_image_generation',
		code_interpreter: 'enable_code_interpreter'
	};

	const warningLabels: Record<string, { label: string; description: string }> = {
		file_upload: {
			label: $i18n.t('File Upload'),
			description: $i18n.t('Warn before sending files to this model')
		},
		web_search: {
			label: $i18n.t('Web Search'),
			description: $i18n.t('Warn before web search queries with this model')
		},
		knowledge: {
			label: $i18n.t('Knowledge / RAG'),
			description: $i18n.t('Warn before sending knowledge base content to this model')
		},
		vision: {
			label: $i18n.t('Vision'),
			description: $i18n.t('Warn before sending images to this model')
		},
		code_interpreter: {
			label: $i18n.t('Code Interpreter'),
			description: $i18n.t('Warn before sending code to this model for execution')
		},
		image_generation: {
			label: $i18n.t('Image Generation'),
			description: $i18n.t('Warn before sending prompts to image generation service')
		}
	};

	const allWarnings = Object.keys(warningLabels);

	export let dataWarnings: Record<string, boolean> = {};
	export let warningMessage: string = '';

	// Filter to only warnings whose global feature is enabled
	$: visibleWarnings = allWarnings.filter((key) => {
		const configKey = warningConfigGuards[key];
		if (configKey && !$config?.features?.[configKey]) {
			return false;
		}
		return true;
	});

	// Initialize missing keys to false (default: no warnings)
	$: {
		for (const key of allWarnings) {
			if (!(key in dataWarnings)) {
				dataWarnings[key] = false;
			}
		}
	}
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class="self-center text-xs font-medium text-gray-500">
			{$i18n.t('Data Sovereignty Warnings')}
		</div>
	</div>
	<div class="text-xs text-gray-400 mb-2">
		{$i18n.t(
			'Select capabilities that require user acknowledgment before first use in a conversation.'
		)}
	</div>
	<div class="flex items-center mt-2 flex-wrap">
		{#each visibleWarnings as key}
			<div class="flex items-center gap-2 mr-3">
				<Checkbox
					state={dataWarnings[key] ? 'checked' : 'unchecked'}
					on:change={(e) => {
						dataWarnings = {
							...dataWarnings,
							[key]: e.detail === 'checked'
						};
					}}
				/>
				<div class="py-0.5 text-sm">
					<Tooltip content={marked.parse(warningLabels[key].description)}>
						{$i18n.t(warningLabels[key].label)}
					</Tooltip>
				</div>
			</div>
		{/each}
	</div>

	{#if Object.values(dataWarnings).some((v) => v)}
		<div class="mt-3">
			<div class="text-xs font-medium text-gray-500 mb-1">
				{$i18n.t('Warning Message')}
			</div>
			<textarea
				class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 dark:text-gray-200 outline-hidden resize-none"
				rows="3"
				placeholder={$i18n.t(
					'This model runs on external infrastructure. Uploaded files and conversation content will be processed by an external provider. Do you want to continue?'
				)}
				bind:value={warningMessage}
			/>
		</div>
	{/if}
</div>
```

**Key design decisions:**

- Unlike `BuiltinTools` which defaults to `true`, data warnings default to `false` — admins opt-in to warnings
- Warning message textarea only appears when at least one warning is enabled
- `file_upload`, `knowledge`, and `vision` have no config guard — they're always available
- `web_search`, `image_generation`, `code_interpreter` are hidden when the global feature is disabled

#### 2.2 ModelEditor Integration

**File**: `src/lib/components/workspace/Models/ModelEditor.svelte`

**Import** (near other Models/ imports):

```svelte
import DataWarnings from './DataWarnings.svelte';
```

**Local state** (after `builtinTools` at line 102):

```typescript
let dataWarnings: Record<string, boolean> = {};
let dataWarningMessage: string = '';
```

**Load admin defaults** (after `builtinTools` assignment at line ~248):

```typescript
dataWarnings = defaultMeta.data_warnings ?? {};
dataWarningMessage = defaultMeta.data_warning_message ?? '';
```

**Per-model overrides** (after line 319):

```typescript
dataWarnings = model?.meta?.data_warnings ?? dataWarnings;
dataWarningMessage = model?.meta?.data_warning_message ?? dataWarningMessage;
```

**Save handler** (after `builtinTools` save at line ~208):

```typescript
if (Object.values(dataWarnings).some((v) => v)) {
	info.meta.data_warnings = dataWarnings;
	info.meta.data_warning_message = dataWarningMessage || '';
} else {
	if (info.meta.data_warnings) {
		delete info.meta.data_warnings;
	}
	if (info.meta.data_warning_message) {
		delete info.meta.data_warning_message;
	}
}
```

**Template** (after `BuiltinTools` at line ~836, guarded by feature flag):

```svelte
{#if $config?.features?.enable_data_warnings}
	<div class="my-4">
		<DataWarnings bind:dataWarnings bind:warningMessage={dataWarningMessage} />
	</div>
{/if}
```

#### 2.3 ModelSettingsModal Integration

**File**: `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte`

**Import**:

```svelte
import DataWarnings from '$lib/components/workspace/Models/DataWarnings.svelte';
```

**Local state** (after `builtinTools`):

```typescript
let defaultDataWarnings: Record<string, boolean> = {};
let defaultWarningMessage: string = '';
```

**Init** (after line 105):

```typescript
defaultDataWarnings = savedMeta.data_warnings ?? {};
defaultWarningMessage = savedMeta.data_warning_message ?? '';
```

**Submit handler** — update the `metadata` assembly (lines 118-122):

```typescript
const metadata = {
	capabilities: defaultCapabilities,
	...(defaultFeatureIds.length > 0 ? { defaultFeatureIds } : {}),
	...(Object.keys(builtinTools).length > 0 ? { builtinTools } : {}),
	...(Object.values(defaultDataWarnings).some((v) => v)
		? {
				data_warnings: defaultDataWarnings,
				data_warning_message: defaultWarningMessage || ''
			}
		: {})
};
```

**Template** (after BuiltinTools section, inside the collapsible):

```svelte
{#if $config?.features?.enable_data_warnings}
	<div class="my-4">
		<DataWarnings
			bind:dataWarnings={defaultDataWarnings}
			bind:warningMessage={defaultWarningMessage}
		/>
	</div>
{/if}
```

#### 2.4 Frontend Types

**File**: `src/lib/apis/index.ts`

Extend `ModelMeta` interface (line 1739-1744):

```typescript
export interface ModelMeta {
	toolIds: never[];
	description?: string;
	capabilities?: object;
	profile_image_url?: string;
	data_warnings?: Record<string, boolean>;
	data_warning_message?: string;
}
```

**File**: `src/lib/stores/index.ts`

Add to `Config.features` type (after `enable_code_execution` at line 332):

```typescript
enable_data_warnings?: boolean;
```

#### 2.5 i18n Translations

**File**: `src/lib/i18n/locales/en-US/translation.json`

Add keys (alphabetically sorted):

```json
"Data Sovereignty Warnings": "",
"Select capabilities that require user acknowledgment before first use in a conversation.": "",
"This model runs on external infrastructure. Uploaded files and conversation content will be processed by an external provider. Do you want to continue?": "",
"Warn before sending code to this model for execution": "",
"Warn before sending files to this model": "",
"Warn before sending images to this model": "",
"Warn before sending knowledge base content to this model": "",
"Warn before sending prompts to image generation service": "",
"Warn before web search queries with this model": "",
"Warning Message": ""
```

**File**: `src/lib/i18n/locales/nl-NL/translation.json`

Add Dutch translations:

```json
"Data Sovereignty Warnings": "Datsoevereiniteitswaarschuwingen",
"Select capabilities that require user acknowledgment before first use in a conversation.": "Selecteer functionaliteiten waarvoor gebruikersbevestiging nodig is bij eerste gebruik in een gesprek.",
"This model runs on external infrastructure. Uploaded files and conversation content will be processed by an external provider. Do you want to continue?": "Dit model draait op externe infrastructuur. Geüploade bestanden en gespreksinhoud worden verwerkt door een externe provider. Wilt u doorgaan?",
"Warn before sending code to this model for execution": "Waarschuw voordat code naar dit model wordt gestuurd voor uitvoering",
"Warn before sending files to this model": "Waarschuw voordat bestanden naar dit model worden gestuurd",
"Warn before sending images to this model": "Waarschuw voordat afbeeldingen naar dit model worden gestuurd",
"Warn before sending knowledge base content to this model": "Waarschuw voordat kennisbankcontent naar dit model wordt gestuurd",
"Warn before sending prompts to image generation service": "Waarschuw voordat prompts naar de beeldgeneratieservice worden gestuurd",
"Warn before web search queries with this model": "Waarschuw voordat webzoekopdrachten met dit model worden uitgevoerd",
"Warning Message": "Waarschuwingsbericht"
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [x] No new TypeScript errors from `npm run check` (beyond pre-existing ~8000)

#### Manual Verification:

- [ ] DataWarnings section visible in ModelEditor when `ENABLE_DATA_WARNINGS=True`
- [ ] DataWarnings section visible in ModelSettingsModal
- [ ] Checkboxes toggle correctly, warning message textarea appears when at least one is checked
- [ ] Saved data_warnings persist on model save and reload
- [ ] System-wide defaults apply to models without per-model overrides
- [ ] Per-model overrides win over system-wide defaults
- [ ] Section hidden when `ENABLE_DATA_WARNINGS=False`
- [ ] Dutch translations render correctly

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Chat Send Flow Integration

### Overview

Intercept the send flow in `submitPrompt()` to check for data warning requirements, show a confirmation dialog, track accepted warnings per conversation, and log acceptance via the API.

### Changes Required:

#### 3.1 Frontend API Client

**File**: `src/lib/apis/data-warnings/index.ts` (new)

```typescript
import { WEBUI_API_BASE_URL } from '$lib/constants';

export const logDataWarningAcceptance = async (
	token: string,
	chatId: string,
	modelId: string,
	capabilities: string[],
	warningMessage: string | null
) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/data-warnings/accept`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			chat_id: chatId,
			model_id: modelId,
			capabilities,
			warning_message: warningMessage
		})
	});

	if (!res.ok) {
		console.error('Failed to log data warning acceptance');
	}

	return res.json();
};
```

#### 3.2 Chat.svelte — Per-Conversation State and Interception

**File**: `src/lib/components/chat/Chat.svelte`

**Import** (near other API imports):

```typescript
import { logDataWarningAcceptance } from '$lib/apis/data-warnings';
```

**Per-conversation state** (after `eventCallback` at line ~138):

```typescript
let acceptedDataWarnings: Set<string> = new Set();
```

**Reset on navigation** — add to `navigateHandler()` (near line 192 where other state is reset):

```typescript
acceptedDataWarnings = new Set();
```

**Reset on init** — add to `init()` (near line 768 where other state is reset):

```typescript
acceptedDataWarnings = new Set();
```

**Data warning dialog state** (after `eventCallback` variables):

```typescript
let showDataWarningDialog = false;
let dataWarningTitle = '';
let dataWarningMessage = '';
let dataWarningCapabilities: string[] = [];
let dataWarningCallback: ((confirmed: boolean) => void) | null = null;
```

**`checkDataWarnings()` function** — add before `submitPrompt()`:

```typescript
const checkDataWarnings = async (
	modelIds: string[],
	activeCapabilities: Record<string, boolean>
): Promise<boolean> => {
	if (!$config?.features?.enable_data_warnings) return true;

	// Collect all unacknowledged warnings across selected models
	const pendingWarnings: {
		modelId: string;
		modelName: string;
		capabilities: string[];
		message: string;
	}[] = [];

	for (const modelId of modelIds) {
		const model = $models.find((m) => m.id === modelId);
		if (!model) continue;

		const warnings = model.info?.meta?.data_warnings;
		if (!warnings) continue;

		const unacknowledged: string[] = [];
		for (const [capability, warned] of Object.entries(warnings)) {
			if (!warned) continue;
			if (!activeCapabilities[capability]) continue;
			const key = `${modelId}:${capability}`;
			if (acceptedDataWarnings.has(key)) continue;
			unacknowledged.push(capability);
		}

		if (unacknowledged.length > 0) {
			pendingWarnings.push({
				modelId,
				modelName: model.name,
				capabilities: unacknowledged,
				message: model.info?.meta?.data_warning_message || ''
			});
		}
	}

	if (pendingWarnings.length === 0) return true;

	// Show confirmation for each model with pending warnings
	for (const warning of pendingWarnings) {
		const confirmed = await new Promise<boolean>((resolve) => {
			const capabilityLabels = warning.capabilities.map((c) =>
				c.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())
			);

			dataWarningTitle = $i18n.t('Data Sovereignty Warning');
			dataWarningMessage =
				warning.message ||
				$i18n.t(
					'This model runs on external infrastructure. Uploaded files and conversation content will be processed by an external provider. Do you want to continue?'
				);
			dataWarningCapabilities = capabilityLabels;
			dataWarningCallback = resolve;
			showDataWarningDialog = true;
		});

		if (!confirmed) return false;

		// Mark as accepted
		for (const cap of warning.capabilities) {
			acceptedDataWarnings.add(`${warning.modelId}:${cap}`);
		}
		acceptedDataWarnings = acceptedDataWarnings; // trigger reactivity

		// Log acceptance (fire-and-forget)
		logDataWarningAcceptance(
			localStorage.token,
			chatId || 'new',
			warning.modelId,
			warning.capabilities,
			warning.message || null
		);
	}

	return true;
};
```

**Interception in `submitPrompt()`** — insert between the last validation block (line ~1868) and input clearing (line ~1870):

```typescript
// Data sovereignty warning check
const activeCapabilities: Record<string, boolean> = {
	file_upload: files.some((f) => f.type !== 'image'),
	vision: files.some((f) => f.type === 'image'),
	web_search: webSearchEnabled,
	image_generation: imageGenerationEnabled,
	code_interpreter: codeInterpreterEnabled,
	knowledge: chatFiles.length > 0
};

const warningAccepted = await checkDataWarnings(selectedModels, activeCapabilities);
if (!warningAccepted) return;
```

**Data warning dialog template** — add near the existing `EventConfirmDialog` (~line 2739):

```svelte
<ConfirmDialog
	bind:show={showDataWarningDialog}
	title={dataWarningTitle}
	on:confirm={() => {
		if (dataWarningCallback) dataWarningCallback(true);
	}}
	on:cancel={() => {
		if (dataWarningCallback) dataWarningCallback(false);
	}}
>
	<div class="text-sm text-gray-500">
		<div class="bg-amber-500/20 text-amber-700 dark:text-amber-200 rounded-lg px-4 py-3 mb-3">
			<div class="font-medium mb-1">
				{$i18n.t('The following capabilities will send data to an external provider')}:
			</div>
			<ul class="list-disc pl-4 text-xs">
				{#each dataWarningCapabilities as cap}
					<li>{cap}</li>
				{/each}
			</ul>
		</div>
		<div class="whitespace-pre-wrap">
			{@html DOMPurify.sanitize(marked.parse(dataWarningMessage))}
		</div>
	</div>
</ConfirmDialog>
```

**Import ConfirmDialog** — add a second import alias (near line 101):

```typescript
import DataWarningConfirmDialog from '../common/ConfirmDialog.svelte';
```

(Use this alias in the template instead of `ConfirmDialog` to avoid name collision with `EventConfirmDialog`.)

#### 3.3 i18n for Chat Dialog

**File**: `src/lib/i18n/locales/en-US/translation.json`

```json
"Data Sovereignty Warning": "",
"The following capabilities will send data to an external provider": ""
```

**File**: `src/lib/i18n/locales/nl-NL/translation.json`

```json
"Data Sovereignty Warning": "Datsoevereiniteitswaarschuwing",
"The following capabilities will send data to an external provider": "De volgende functionaliteiten sturen data naar een externe provider"
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds
- [ ] No new TypeScript errors from `npm run check` (beyond pre-existing)

#### Manual Verification:

- [ ] Configure a model with `data_warnings.file_upload = true` and a custom message
- [ ] Attach a file and send — warning dialog appears with the custom message
- [ ] Click Cancel — send is aborted, file and prompt preserved in input
- [ ] Click Confirm — message sends normally
- [ ] Send another message with a file — no warning (already accepted for this conversation)
- [ ] Enable web search and send — warning fires for web_search (new capability)
- [ ] Switch to a different flagged model — warning fires for the new model
- [ ] Navigate to a new chat — warnings reset (new conversation)
- [ ] Check database — audit log entries created for each acceptance
- [ ] Disable `ENABLE_DATA_WARNINGS` — no warnings appear

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 4.

---

## Phase 4: Helm Chart and Feature Flag Wiring

### Overview

Add Helm chart configuration for the feature flag so it can be controlled per tenant deployment.

### Changes Required:

#### 4.1 Helm Values

**File**: `helm/open-webui-tenant/values.yaml`

Add after the feature flags section (~line 218):

```yaml
# Data Sovereignty
enableDataWarnings: 'True'
```

#### 4.2 Helm Configmap

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

Add the env var mapping (near other ENABLE\_ entries):

```yaml
ENABLE_DATA_WARNINGS: { { .Values.openWebui.config.enableDataWarnings | default "true" | quote } }
```

### Success Criteria:

#### Automated Verification:

- [x] `helm template` renders correctly with default values
- [x] `npm run build` still succeeds

#### Manual Verification:

- [ ] Deploy with `enableDataWarnings: "True"` — feature active
- [ ] Deploy with `enableDataWarnings: "False"` — feature disabled, no warnings shown

---

## Testing Strategy

### Unit Tests:

- None required for Phase 1 (standard CRUD model pattern, covered by integration test via API)
- Frontend component tests are optional given pre-existing test coverage patterns

### Integration Tests:

- `POST /api/v1/data-warnings/accept` returns 200 with valid payload
- `POST /api/v1/data-warnings/accept` with feature disabled returns noop response
- `/api/config` includes `enable_data_warnings` in features

### Manual Testing Steps:

1. Configure system-wide defaults: enable file_upload + web_search warnings with custom message
2. Verify defaults apply to a model without per-model overrides
3. Override one model to disable file_upload warning — verify per-model override wins
4. Start a conversation, attach a file, send — verify warning appears
5. Confirm warning, verify send proceeds, verify audit log entry
6. Send another file message — verify no duplicate warning
7. Enable web_search mid-conversation — verify new warning fires
8. Cancel a warning — verify send is aborted, input preserved
9. Navigate to new chat — verify warnings reset
10. Switch model mid-conversation to a differently-configured model — verify independent warnings
11. Disable feature flag — verify all warnings suppressed
12. Re-enable — verify config preserved and warnings resume

## Performance Considerations

- `checkDataWarnings()` is synchronous model lookup + Set check — negligible cost
- Audit log POST is fire-and-forget — doesn't block the send flow
- No additional API calls on the render path — warning config comes from existing model metadata

## Migration Notes

- Alembic migration `d4e5f6a7b8c9` creates `data_warning_log` table (new table, no existing data affected)
- `data_warnings` and `data_warning_message` stored as extra fields on `ModelMeta` — no schema migration needed
- Feature defaults to enabled (`ENABLE_DATA_WARNINGS=True`) — no warnings appear until admin configures them per model

## Upstream Compatibility

This feature is **fully additive** with minimal upstream touch points:

- **New files**: `DataWarnings.svelte`, `data_warnings.py` (model), `data_warnings.py` (router), `data-warnings/index.ts` (API client), migration file
- **Modified upstream files**: `Chat.svelte` (interception point + dialog), `ModelEditor.svelte` (import + section), `ModelSettingsModal.svelte` (import + section), `config.py` (flag), `main.py` (flag + router), `utils/models.py` (merge logic), `stores/index.ts` (type), `apis/index.ts` (type)
- **Feature-flagged**: All UI behind `$config.features.enable_data_warnings` — zero impact when disabled

## References

- Research document: `thoughts/shared/research/data-sovereignty-guard-research.md`
- BuiltinTools pattern: `src/lib/components/workspace/Models/BuiltinTools.svelte`
- EventConfirmDialog pattern: `src/lib/components/chat/Chat.svelte:101,130-138,2721-2739`
- PersistentConfig pattern: `backend/open_webui/config.py:1739-1743` (ENABLE_2FA)
- Default metadata merge: `backend/open_webui/utils/models.py:295-312`
