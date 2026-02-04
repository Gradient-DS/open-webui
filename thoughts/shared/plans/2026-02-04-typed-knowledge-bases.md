# Typed Knowledge Bases Implementation Plan

## Overview

Separate knowledge bases into typed categories (Local vs OneDrive/external) with type-specific creation flows, access control rules, hash-based document deduplication for cross-user file reuse, upstream permission enforcement, and file count limits. This covers all four phases from the research document in a single plan.

## Current State Analysis

- The `knowledge` table has no `type` column. OneDrive KBs are identified implicitly via `meta.onedrive_sync` and `onedrive-` file ID prefix.
- All KBs share the same creation form (name, description, access_control).
- OneDrive sync maps folder permissions to KB `access_control` via `_sync_permissions()` in the sync worker.
- Each file gets its own `file-{id}` vector collection; vectors are copied into the KB's collection.
- OneDrive files use deterministic IDs (`onedrive-{item_id}`), and the sync worker already does hash-based skip-if-unchanged.
- `KnowledgeForm` accepts only `name`, `description`, `access_control` (no `type` field).
- The "New Knowledge" button is a plain `<a>` link to `/workspace/knowledge/create`.

### Key Discoveries:
- `backend/open_webui/models/knowledge.py:42-70` -- Knowledge table has no `type` column
- `backend/open_webui/models/knowledge.py:138-141` -- `KnowledgeForm` has no `type` field
- `backend/open_webui/services/onedrive/sync_worker.py:298-394` -- `_sync_permissions()` maps OneDrive permissions to `access_control`
- `backend/open_webui/services/onedrive/sync_worker.py:688-696` -- Hash-based dedup already exists per-file, but only checks against own previous sync
- `backend/open_webui/routers/knowledge.py:548-643` -- File removal always deletes underlying File record by default (`delete_file=True`)
- `src/lib/components/workspace/Knowledge.svelte:139-148` -- "New Knowledge" is a simple `<a>` link
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1221-1230` -- AccessControlModal always shows for write-access users
- Alembic head revision: `f8e1a9c2d3b4` (merge migration from 2026-01-28)
- Existing KBs are either all-local or all-OneDrive in practice (no mixed KBs found)

## Desired End State

1. The `knowledge` table has a `type` column (`"local"`, `"onedrive"`, extensible to future sources)
2. Existing KBs with `meta.onedrive_sync` are migrated to `type="onedrive"`
3. Backend enforces: non-local KBs always have `access_control = {}` (private, owner-only)
4. Frontend shows a dropdown for "New Knowledge" with type-specific flows
5. OneDrive creation flow combines KB creation + picker + sync start
6. Access control UI is hidden for non-local KBs
7. KB list shows type badges ("Local" / "OneDrive")
8. Multiple users syncing the same OneDrive document share the underlying File record and vector embeddings
9. File removal from external KBs preserves shared File records
10. Sync worker verifies upstream permissions and removes files when access is revoked
11. External KBs enforce a 250-file limit

### Verification:
- Create a local KB -> type badge shows "Local", access control available
- Create an OneDrive KB -> type badge shows "OneDrive", no access control UI, picker opens
- Two users sync the same OneDrive folder -> File record created once, vectors embedded once
- Remove a file from one user's OneDrive KB -> File record persists for the other user
- User loses OneDrive folder access -> files removed from their KB on next sync
- Attempt to sync > 250 files -> sync aborts with file limit exceeded status

## What We're NOT Doing

- Not creating a generic `SyncProvider` abstraction (that's the multi-datasource plan)
- Not adding SharePoint, Confluence, or other source types yet (just `"local"` and `"onedrive"`)
- Not implementing background/scheduled sync (that's the background-sync plan)
- Not changing URL routes
- Not modifying the sidebar layout (that's the cosmetic-frontend-changes plan, which should land first)
- Not adding admin UI for managing external KBs across users
- Not implementing real-time cross-user vector propagation (lazy propagation on next sync)

## Implementation Approach

Four phases, each independently deployable. Phase 1 is backend-only (no frontend changes). Phase 2 adds the frontend type-specific UI. Phase 3 adds cross-user document deduplication. Phase 4 adds upstream permission enforcement.

**Dependency**: The cosmetic-frontend-changes plan (Phase 4: Knowledge to sidebar) should land before Phase 2, since the "New Knowledge" dropdown needs to work in the new sidebar location.

---

## Phase 1: Data Model + Type-Aware Backend

### Overview
Add a `type` column to the `knowledge` table, update all Pydantic models, enforce type-based access control rules in the backend, and migrate existing data. No frontend changes -- existing UI continues to work, creating `"local"` KBs by default.

### Changes Required:

#### 1. Alembic Migration
**File**: `backend/open_webui/migrations/versions/<new>_add_knowledge_type_column.py`
**Changes**: New migration file

```python
"""add knowledge type column"""

from alembic import op
import sqlalchemy as sa
from open_webui.migrations.util import get_existing_tables

revision = "<generated>"
down_revision = "f8e1a9c2d3b4"
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = get_existing_tables()
    if "knowledge" in existing_tables:
        op.add_column(
            "knowledge",
            sa.Column("type", sa.Text(), nullable=False, server_default="local"),
        )

        # Data migration: set type="onedrive" for KBs with onedrive_sync in meta
        conn = op.get_bind()
        dialect = conn.dialect.name

        if dialect == "sqlite":
            conn.execute(
                sa.text(
                    """
                    UPDATE knowledge
                    SET type = 'onedrive'
                    WHERE json_extract(meta, '$.onedrive_sync') IS NOT NULL
                    """
                )
            )
        else:
            # PostgreSQL
            conn.execute(
                sa.text(
                    """
                    UPDATE knowledge
                    SET type = 'onedrive'
                    WHERE meta::jsonb ? 'onedrive_sync'
                    """
                )
            )


def downgrade():
    op.drop_column("knowledge", "type")
```

#### 2. Knowledge SQLAlchemy Model
**File**: `backend/open_webui/models/knowledge.py:42-70`
**Changes**: Add `type` column to the `Knowledge` table class

Add after the `user_id` column (line 46):
```python
type = Column(Text, nullable=False, server_default="local")
```

#### 3. KnowledgeModel Pydantic Model
**File**: `backend/open_webui/models/knowledge.py:73-87`
**Changes**: Add `type` field

```python
type: str = "local"
```

#### 4. KnowledgeForm Pydantic Model
**File**: `backend/open_webui/models/knowledge.py:138-141`
**Changes**: Add optional `type` field

```python
class KnowledgeForm(BaseModel):
    name: str
    description: str
    type: Optional[str] = None
    access_control: Optional[dict] = None
```

The `type` field is `Optional` because the update endpoint reuses this form but should not allow changing the type after creation. The create endpoint will handle defaulting.

#### 5. Create Endpoint
**File**: `backend/open_webui/routers/knowledge.py:159-194`
**Changes**: Accept `type` field, enforce access control for non-local types

After the existing public sharing check (line 184), add type-based enforcement:

```python
# Set type, default to "local"
if form_data.type is None:
    form_data.type = "local"

# Validate type value
if form_data.type not in ("local", "onedrive"):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid knowledge base type. Must be 'local' or 'onedrive'.",
    )

# External KBs are always private
if form_data.type != "local":
    form_data.access_control = {}
```

#### 6. Update Endpoint
**File**: `backend/open_webui/routers/knowledge.py:300-347`
**Changes**: Prevent access_control changes on non-local KBs, prevent type changes

After the write access check (line 323), add:

```python
# Prevent changing type after creation
form_data.type = None  # Strip type from update form

# Prevent access_control changes on non-local KBs
if knowledge.type != "local":
    form_data.access_control = knowledge.access_control
```

The `update_knowledge_by_id` method uses `form_data.model_dump()` which will include `type: None`. We need to handle this in the update method to skip `None` values, or alternatively exclude `type` from the dump. The cleanest approach: modify `update_knowledge_by_id` to skip `None` values.

#### 7. Update `update_knowledge_by_id` Method
**File**: `backend/open_webui/models/knowledge.py:574-590`
**Changes**: Skip `None` values in the form data to avoid overwriting `type` with `None`

Replace the update logic to exclude `None` values:

```python
def update_knowledge_by_id(
    self, id: str, form_data: KnowledgeForm, overwrite: bool = False
) -> Optional[KnowledgeModel]:
    try:
        with get_db() as db:
            knowledge = db.query(Knowledge).filter_by(id=id).first()
            if not knowledge:
                return None

            update_data = {
                k: v
                for k, v in form_data.model_dump().items()
                if v is not None or k == "access_control"  # access_control can be None (public)
            }
            update_data["updated_at"] = int(time.time())

            db.query(Knowledge).filter_by(id=id).update(update_data)
            db.commit()
            db.refresh(knowledge)
            return KnowledgeModel.model_validate(knowledge)
    except Exception as e:
        log.exception(f"update_knowledge_by_id: {e}")
        return None
```

Note: `access_control` is special -- `None` means "public" in the access control system, so we must allow it through even when it's `None`. All other `None` values are stripped.

#### 8. File Removal Endpoint
**File**: `backend/open_webui/routers/knowledge.py:548-643`
**Changes**: For external-source KBs, default `delete_file` to `False`

After the knowledge fetch (around line 556), add logic to override `delete_file`:

```python
# For external-source KBs, never delete the underlying file
# (other users may reference it via their own KBs)
if knowledge.type != "local":
    delete_file = False
```

#### 9. List/Search Endpoints: Add Type Filter
**File**: `backend/open_webui/routers/knowledge.py:55-85` (list) and `:88-128` (search)
**Changes**: Accept optional `type` query parameter

Add `type: Optional[str] = None` to both endpoint signatures. Pass it through to `search_knowledge_bases`:

```python
@router.get("/search", response_model=KnowledgeAccessListResponse)
async def search_knowledge_bases_endpoint(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    type: Optional[str] = None,
    page: int = 1,
    user=Depends(get_verified_user),
):
    # ... existing filter construction ...
    if type:
        filter["type"] = type
    # ... rest of endpoint ...
```

#### 10. Update `search_knowledge_bases` Method
**File**: `backend/open_webui/models/knowledge.py:210-267`
**Changes**: Support `type` filter key

Add a type filter condition after the existing query/view_option filters (around line 233):

```python
if "type" in filter and filter["type"]:
    query = query.filter(Knowledge.type == filter["type"])
```

#### 11. `insert_new_knowledge` Method
**File**: `backend/open_webui/models/knowledge.py:159-183`
**Changes**: Include `type` in the knowledge creation

The existing code at line 171 does `form_data.model_dump()` which will now include `type`. Since the `Knowledge` SQLAlchemy model has the `type` column, this will work automatically. No code change needed here -- just verify the flow works.

### Success Criteria:

#### Automated Verification:
- [ ] Alembic migration applies cleanly: `cd backend/open_webui && alembic upgrade head`
- [ ] Backend starts without errors: `open-webui dev`
- [x] `npm run lint:backend` passes
- [ ] Creating a KB without `type` defaults to `"local"`: `curl -X POST /api/v1/knowledge/create -d '{"name":"test","description":"test"}'` returns `type: "local"`
- [ ] Creating a KB with `type: "onedrive"` forces `access_control: {}`: verify response
- [ ] Updating a non-local KB's `access_control` is silently preserved (no error, value unchanged)
- [ ] List endpoint with `?type=local` returns only local KBs
- [ ] List endpoint with `?type=onedrive` returns only OneDrive KBs

#### Manual Verification:
- [ ] Existing KBs with OneDrive sync metadata now have `type="onedrive"` in the database
- [ ] Existing local KBs have `type="local"`
- [ ] The existing UI still works (creates local KBs, all features functional)
- [ ] OneDrive sync still works end-to-end on a `type="onedrive"` KB

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Split Creation Flow + Type-Specific UI

### Overview
Replace the "New Knowledge" button with a dropdown offering type-specific creation flows. Hide access control UI for non-local KBs. Show type badges in the KB list. Add a combined OneDrive creation flow (name/description -> picker -> create KB + start sync).

**Prerequisite**: The cosmetic-frontend-changes plan (Phase 4: Knowledge to sidebar) should be completed first. The "New Knowledge" dropdown needs to work in the sidebar Knowledge page, not the workspace tab bar.

### Changes Required:

#### 1. API Client: Update `createNewKnowledge`
**File**: `src/lib/apis/knowledge/index.ts:3-39`
**Changes**: Accept `type` parameter

```typescript
export const createNewKnowledge = async (
	token: string,
	name: string,
	description: string,
	accessControl: null | object,
	type: string = 'local'
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/knowledge/create`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			name: name,
			description: description,
			access_control: accessControl,
			type: type
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
```

#### 2. Replace "New Knowledge" Button with Dropdown
**File**: `src/lib/components/workspace/Knowledge.svelte:139-148`
**Changes**: Replace the `<a>` link with a dropdown menu offering "Local Knowledge Base" and "From OneDrive"

Replace the existing `<a>` button with a `DropdownMenu` component (using the same `bits-ui` dropdown already used in `AddContentMenu.svelte`):

```svelte
<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		<button
			class="px-2 py-1.5 rounded-xl bg-black text-white dark:bg-white dark:text-black transition font-medium text-sm flex items-center"
		>
			<Plus className="size-3" strokeWidth="2.5" />
			<div class="hidden md:block md:ml-1 text-xs">{$i18n.t('New Knowledge')}</div>
		</button>
	</DropdownMenu.Trigger>

	<DropdownMenu.Content
		class="w-full max-w-[200px] rounded-xl px-1 py-1.5 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
		side="bottom"
		align="end"
	>
		<DropdownMenu.Item
			class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
			on:click={() => {
				goto('/workspace/knowledge/create?type=local');
			}}
		>
			<Database className="size-4" strokeWidth="2" />
			<div class="flex items-center">{$i18n.t('Local Knowledge Base')}</div>
		</DropdownMenu.Item>

		{#if $config?.features?.enable_onedrive_integration}
			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
				on:click={() => {
					goto('/workspace/knowledge/create?type=onedrive');
				}}
			>
				<CloudArrowUp className="size-4" strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('From OneDrive')}</div>
			</DropdownMenu.Item>
		{/if}
	</DropdownMenu.Content>
</DropdownMenu.Root>
```

Add necessary imports: `DropdownMenu` from `bits-ui`, `Database` and `CloudArrowUp` icons, `goto` from `$app/navigation`, `config` store.

#### 3. Update CreateKnowledgeBase Component
**File**: `src/lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte`
**Changes**: Read `type` from URL query param, conditionally show access control, pass `type` to API

Add `type` state from URL:
```svelte
<script>
	import { page } from '$app/stores';
	// ... existing imports ...

	let type = $page.url.searchParams.get('type') || 'local';
	// ... existing state ...
</script>
```

Conditionally render the AccessControl section (lines 115-122):
```svelte
{#if type === 'local'}
	<AccessControl
		bind:accessControl
		accessRoles={['read', 'write']}
		share={$user?.permissions?.sharing?.knowledge || $user?.role === 'admin'}
		sharePublic={$user?.permissions?.sharing?.public_knowledge || $user?.role === 'admin'}
	/>
{/if}
```

Update the submit handler (line 29) to pass `type`:
```typescript
const res = await createNewKnowledge(
	localStorage.token,
	name,
	description,
	type === 'local' ? accessControl : {},
	type
);
```

For OneDrive type: after successful creation, navigate to the KB detail page where the user can use the existing OneDrive sync flow:
```typescript
if (res) {
	if (type === 'onedrive') {
		goto(`/workspace/knowledge/${res.id}?start_onedrive_sync=true`);
	} else {
		goto(`/workspace/knowledge/${res.id}`);
	}
}
```

#### 4. Auto-Start OneDrive Sync on KB Detail Page
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Check for `start_onedrive_sync` query parameter and auto-trigger the OneDrive picker

In the `onMount` block (around line 1104), after fetching the knowledge base, add:

```typescript
// Auto-start OneDrive sync if directed from creation flow
if ($page.url.searchParams.get('start_onedrive_sync') === 'true' && knowledge) {
	// Clean up the URL param
	const url = new URL(window.location.href);
	url.searchParams.delete('start_onedrive_sync');
	history.replaceState({}, '', url.toString());

	// Trigger the OneDrive sync handler
	await tick();
	oneDriveSyncHandler();
}
```

#### 5. Hide Access Control UI for Non-Local KBs
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

**AccessControlModal** (lines 1221-1230): Wrap in type check:
```svelte
{#if knowledge?.type === 'local' || !knowledge?.type}
	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessControl={knowledge.access_control}
		share={$user?.permissions?.sharing?.knowledge || $user?.role === 'admin'}
		sharePublic={$user?.permissions?.sharing?.public_knowledge || $user?.role === 'admin'}
		onChange={() => {
			changeDebounceHandler();
		}}
		accessRoles={['read', 'write']}
	/>
{/if}
```

**"Access" button** (lines 1300-1320): Add type check:
```svelte
{#if knowledge?.write_access && (knowledge?.type === 'local' || !knowledge?.type)}
	<!-- existing Access button -->
{:else if knowledge?.write_access}
	<!-- Non-local KB: show type badge instead of access button -->
	<div class="text-xs shrink-0 text-gray-500 flex items-center gap-1">
		<LockClosed strokeWidth="2.5" className="size-3" />
		{$i18n.t('Private')}
	</div>
{:else}
	<div class="text-xs shrink-0 text-gray-500">
		{$i18n.t('Read Only')}
	</div>
{/if}
```

#### 6. Show Type Badge on KB List
**File**: `src/lib/components/workspace/Knowledge.svelte:228`
**Changes**: Replace hardcoded "Collection" badge with type-aware badge

Replace the static `Badge` at line 228:
```svelte
{#if item?.type === 'onedrive'}
	<Badge type="info" content={$i18n.t('OneDrive')} />
{:else}
	<Badge type="muted" content={$i18n.t('Local')} />
{/if}
```

#### 7. Show Type Badge on KB Detail Page Header
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Add a type indicator near the KB name in the header

In the header area (near line 1240), add a type badge:
```svelte
{#if knowledge?.type === 'onedrive'}
	<Badge type="info" content={$i18n.t('OneDrive')} />
{:else}
	<Badge type="muted" content={$i18n.t('Local')} />
{/if}
```

#### 8. i18n Translation Keys
**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add new translation keys

```json
"From OneDrive": "",
"Local Knowledge Base": "",
"Local": "",
"OneDrive": "",
```

**File**: `src/lib/i18n/locales/nl-NL/translation.json`
**Changes**: Add Dutch translations

```json
"From OneDrive": "Vanuit OneDrive",
"Local Knowledge Base": "Lokale kennisbank",
"Local": "Lokaal",
"OneDrive": "OneDrive",
```

#### 9. File Count Limit: Frontend Warning
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Show current file count and limit for external KBs

In the KB detail page header area, for non-local KBs, display a file count indicator:
```svelte
{#if knowledge?.type !== 'local' && knowledge?.type}
	<div class="text-xs text-gray-500">
		{fileItemsTotal} / 250 {$i18n.t('files')}
	</div>
{/if}
```

### Success Criteria:

#### Automated Verification:
- [x] `npm run check` passes (TypeScript)
- [x] `npm run lint:frontend` passes
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] "New Knowledge" shows a dropdown with "Local Knowledge Base" and "From OneDrive" options
- [ ] "From OneDrive" only appears when `enable_onedrive_integration` is enabled
- [ ] Creating a local KB shows the access control form
- [ ] Creating an OneDrive KB hides the access control form
- [ ] After creating an OneDrive KB, the OneDrive picker opens automatically
- [ ] KB list shows "Local" or "OneDrive" badges per KB
- [ ] KB detail page shows type badge in header
- [ ] Non-local KBs show "Private" label instead of "Access" button
- [ ] Non-local KBs show file count / 250 limit indicator
- [ ] Dutch translations appear correctly

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Hash-Based Document Deduplication

### Overview
When multiple users sync the same OneDrive document, embed it only once and share the underlying File record. Modify file removal for external KBs to preserve shared data. Add orphan cleanup.

### Changes Required:

#### 1. Modify `_process_file_info()`: Cross-User Dedup
**File**: `backend/open_webui/services/onedrive/sync_worker.py:641-800`
**Changes**: When a File record with matching ID exists but belongs to another user, skip processing and just create the KnowledgeFile association + copy vectors

Replace the current dedup logic (lines 688-696) with expanded logic:

```python
# Hash-based dedup
content_hash = hashlib.sha256(content).hexdigest()
file_id = f"onedrive-{item_id}"
existing = Files.get_file_by_id(file_id)

if existing:
    if existing.hash == content_hash:
        # File unchanged -- just ensure KB association exists and vectors are copied
        log.info(
            f"File {file_id} unchanged (hash match), ensuring KB association"
        )
        # Create KnowledgeFile association if not exists
        Knowledges.add_file_to_knowledge_by_id(
            self.knowledge_id, file_id, self.user_id
        )
        # Copy vectors from per-file collection to KB collection
        result = await self._ensure_vectors_in_kb(file_id)
        if result:
            return result  # FailedFile
        # Emit file added event
        await self._emit_file_added(file_id, name)
        return None  # Success
    else:
        # File changed upstream -- re-process
        log.info(
            f"File {file_id} changed (hash mismatch), re-processing"
        )
        # Fall through to download + process flow
```

The key difference: instead of `return None` (skip entirely), we now create the KnowledgeFile association and copy vectors. This handles the case where user B syncs a document that user A already processed.

#### 2. Add `_ensure_vectors_in_kb()` Helper
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: New method to copy vectors from per-file collection to KB collection

```python
async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
    """Copy vectors from the per-file collection into this KB's collection.

    This is the second step of _process_file_via_api() extracted for reuse
    when the file already exists and doesn't need re-processing.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._get_base_url()}/api/v1/retrieval/process/file",
                headers={
                    "Authorization": f"Bearer {self.user_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "file_id": file_id,
                    "collection_name": self.knowledge_id,
                },
            )
            if response.status_code == 200:
                return None
            elif response.status_code == 400:
                detail = response.json().get("detail", "")
                if "Duplicate content" in detail:
                    return None  # Already in KB
                return FailedFile(
                    filename=file_id,
                    error_type=SyncErrorType.PROCESSING_ERROR,
                    error_message=f"Failed to copy vectors to KB: {detail}",
                )
            else:
                return FailedFile(
                    filename=file_id,
                    error_type=SyncErrorType.PROCESSING_ERROR,
                    error_message=f"HTTP {response.status_code}",
                )
    except httpx.TimeoutException:
        return FailedFile(
            filename=file_id,
            error_type=SyncErrorType.TIMEOUT,
            error_message="Timeout copying vectors to KB",
        )
```

#### 3. Update Hash-Changed Flow: Propagate to Other KBs
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: When a file's hash changes, after re-processing, update vectors in all other KBs that reference it

After the file is re-processed in `_process_file_info()` (around line 773 where `add_file_to_knowledge_by_id` is called), add propagation logic:

```python
# After successful re-processing, update vectors in other KBs
# that reference this file (lazy: only when this sync detects the change)
try:
    knowledge_files = Knowledges.get_knowledge_files_by_file_id(file_id)
    for kf in knowledge_files:
        if kf.knowledge_id != self.knowledge_id:
            log.info(
                f"Propagating updated vectors for {file_id} to KB {kf.knowledge_id}"
            )
            # Remove old vectors
            VECTOR_DB_CLIENT.delete(
                collection_name=kf.knowledge_id,
                filter={"file_id": file_id},
            )
            # Copy new vectors
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    await client.post(
                        f"{self._get_base_url()}/api/v1/retrieval/process/file",
                        headers={
                            "Authorization": f"Bearer {self.user_token}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "file_id": file_id,
                            "collection_name": kf.knowledge_id,
                        },
                    )
            except Exception as e:
                log.warning(
                    f"Failed to propagate vectors to KB {kf.knowledge_id}: {e}"
                )
except Exception as e:
    log.warning(f"Failed to propagate vector updates for {file_id}: {e}")
```

Note: This is best-effort. If propagation fails, the other KB will get updated vectors on its next sync cycle.

#### 4. File Removal: Preserve Shared Files
**File**: `backend/open_webui/routers/knowledge.py:548-643`
**Changes**: Already handled in Phase 1 (step 8) where `delete_file` is forced to `False` for non-local KBs. Verify this works correctly.

Additionally, after removing a KnowledgeFile record, check if any other KnowledgeFile records reference this file. If none remain, the file is orphaned:

After the KnowledgeFile removal (around line 582), add:

```python
# Check if this was the last reference to the file
remaining_refs = Knowledges.get_knowledge_files_by_file_id(form_data.file_id)
if not remaining_refs and knowledge.type != "local":
    # Orphaned external file -- clean up
    log.info(f"Cleaning up orphaned external file {form_data.file_id}")
    VECTOR_DB_CLIENT.delete_collection(f"file-{form_data.file_id}")
    Files.delete_file_by_id(form_data.file_id)
```

#### 5. Deletion Handler: Preserve Shared Files
**File**: `backend/open_webui/services/onedrive/sync_worker.py:624-639`
**Changes**: `_handle_deleted_item()` currently deletes the File record unconditionally. Change it to only remove the KnowledgeFile association and check for orphans.

Replace the current implementation:

```python
def _handle_deleted_item(self, item_id: str) -> None:
    file_id = f"onedrive-{item_id}"
    existing = Files.get_file_by_id(file_id)

    if existing:
        # Remove association from this KB only
        Knowledges.remove_file_from_knowledge_by_id(
            self.knowledge_id, file_id
        )

        # Remove vectors from this KB's collection
        try:
            VECTOR_DB_CLIENT.delete(
                collection_name=self.knowledge_id,
                filter={"file_id": file_id},
            )
        except Exception as e:
            log.warning(f"Failed to remove vectors for {file_id} from KB: {e}")

        # Only delete the file if no other KBs reference it
        remaining_refs = Knowledges.get_knowledge_files_by_file_id(file_id)
        if not remaining_refs:
            log.info(f"No remaining references to {file_id}, cleaning up")
            try:
                VECTOR_DB_CLIENT.delete_collection(f"file-{file_id}")
            except Exception as e:
                log.warning(f"Failed to delete collection for {file_id}: {e}")
            Files.delete_file_by_id(file_id)
        else:
            log.info(
                f"File {file_id} still referenced by {len(remaining_refs)} KB(s), preserving"
            )
```

#### 6. Import VECTOR_DB_CLIENT in Sync Worker
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Add import for the vector DB client

```python
from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
```

Check if this import already exists; if not, add it near the top of the file.

### Success Criteria:

#### Automated Verification:
- [x] `npm run lint:backend` passes
- [ ] Backend starts without import errors: `open-webui dev`
- [ ] No regressions in existing sync functionality

#### Manual Verification:
- [ ] User A syncs a OneDrive folder -> files processed normally, KnowledgeFile records created
- [ ] User B syncs the same OneDrive folder -> files are NOT re-embedded (check logs for "hash match" messages), KnowledgeFile records created, vectors copied to KB B's collection
- [ ] User A removes a file from their KB -> KnowledgeFile deleted, File record preserved (user B still has it)
- [ ] User B removes the same file -> File record and vector collection deleted (orphan cleanup)
- [ ] OneDrive file changes upstream -> both users' KBs get updated vectors (user A's sync detects change and propagates)
- [ ] A file deleted in OneDrive -> delta sync removes it from the triggering user's KB, preserves for others

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 4.

---

## Phase 4: Upstream Permission Enforcement + File Count Limits

### Overview
Verify user's access to OneDrive sources before syncing. Remove files when access is revoked. Enforce 250-file limit for external KBs.

### Changes Required:

#### 1. Add `_verify_source_access()` Method
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: New method to check if the user still has access to a OneDrive source

```python
async def _verify_source_access(
    self, source: dict
) -> bool:
    """Verify the user can still access a OneDrive source.

    Returns True if access is valid, False if revoked.
    """
    drive_id = source.get("drive_id")
    item_id = source.get("item_id")
    source_type = source.get("type", "folder")

    try:
        item = await self._client.get_item(drive_id, item_id)
        if item is None:
            return False
        return True
    except Exception as e:
        error_str = str(e).lower()
        if "404" in error_str or "403" in error_str or "not found" in error_str or "access denied" in error_str:
            log.warning(
                f"User {self.user_id} lost access to {source_type} "
                f"{drive_id}/{item_id}: {e}"
            )
            return False
        # For other errors (network, timeout), assume access is still valid
        # to avoid accidentally removing files
        log.warning(
            f"Error verifying access to {source_type} {drive_id}/{item_id}: {e}"
        )
        return True
```

#### 2. Add `_handle_revoked_source()` Method
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Remove all files from a revoked source

```python
async def _handle_revoked_source(self, source: dict) -> int:
    """Remove all files associated with a revoked source from this KB.

    Returns the number of files removed.
    """
    source_name = source.get("name", "unknown")
    removed_count = 0

    # Get all files in this KB
    files = Knowledges.get_files_by_id(self.knowledge_id)

    for file in files:
        # Check if this file belongs to the revoked source
        # For folder sources: file was synced from this folder
        # We can identify by checking if the file's OneDrive metadata
        # matches the source's drive_id
        if not file.id.startswith("onedrive-"):
            continue

        file_meta = file.meta or {}
        file_drive_id = file_meta.get("drive_id")
        source_drive_id = source.get("drive_id")

        # If we can match the drive_id, remove files from this drive
        # For more precise matching, we'd need to track source->file mappings
        if file_drive_id and source_drive_id and file_drive_id == source_drive_id:
            # Remove KnowledgeFile association
            Knowledges.remove_file_from_knowledge_by_id(
                self.knowledge_id, file.id
            )
            # Remove vectors from KB collection
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={"file_id": file.id},
                )
            except Exception as e:
                log.warning(f"Failed to remove vectors for {file.id}: {e}")

            # Check for orphans
            remaining = Knowledges.get_knowledge_files_by_file_id(file.id)
            if not remaining:
                try:
                    VECTOR_DB_CLIENT.delete_collection(f"file-{file.id}")
                except Exception:
                    pass
                Files.delete_file_by_id(file.id)

            removed_count += 1

    log.info(
        f"Removed {removed_count} files from KB {self.knowledge_id} "
        f"due to revoked access to source '{source_name}'"
    )

    return removed_count
```

#### 3. Integrate Access Verification into `sync()`
**File**: `backend/open_webui/services/onedrive/sync_worker.py:397-622`
**Changes**: Before collecting files from each source, verify access. Remove revoked sources.

In the `sync()` method, before the source iteration loop (around line 425), add:

```python
# Verify access to each source before syncing
verified_sources = []
revoked_sources = []

for source in self.sources:
    has_access = await self._verify_source_access(source)
    if has_access:
        verified_sources.append(source)
    else:
        revoked_sources.append(source)

# Handle revoked sources
total_revoked_files = 0
for source in revoked_sources:
    removed = await self._handle_revoked_source(source)
    total_revoked_files += removed

    # Emit notification
    await self._update_sync_status(
        "access_revoked",
        error=f"Access to '{source.get('name', 'unknown')}' has been revoked. "
              f"{removed} file(s) removed.",
    )

# Update sources list to only include verified sources
self.sources = verified_sources
```

#### 4. Emit Revocation Notification to Frontend
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Handle `"access_revoked"` sync status in the progress handler

In the `handleOneDriveSyncProgress` handler (the Socket.IO event handler), add handling for the new status:

```typescript
if (data.status === 'access_revoked') {
    toast.warning(data.error || $i18n.t('Access to a OneDrive source has been revoked'));
}
```

#### 5. Track Source-to-File Mapping
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Store `drive_id` in file metadata during processing to enable source identification

In `_process_file_info()`, when creating the file's `meta` dict (around lines 725-738 and 747-758), ensure `drive_id` from the `file_info` is included:

```python
meta = {
    "name": name,
    "content_type": content_type,
    "size": len(content),
    "source": "onedrive",
    "onedrive_item_id": item_id,
    "drive_id": file_info.get("drive_id"),  # Add this for source tracking
    "last_synced_at": int(time.time()),
}
```

Verify this is already present. If `drive_id` is already in the meta, no change needed.

#### 6. File Count Limit: Backend Enforcement
**File**: `backend/open_webui/services/onedrive/sync_worker.py:436-442`
**Changes**: After collecting all files, check against the 250-file limit for external KBs

The existing `ONEDRIVE_MAX_FILES_PER_SYNC` check (lines 436-442) already truncates files. Update it to also set a specific status:

```python
# Check file count limit for external KBs
max_files = min(ONEDRIVE_MAX_FILES_PER_SYNC, 250)  # Cap at 250 for external KBs
current_file_count = len(Knowledges.get_files_by_id(self.knowledge_id) or [])
available_slots = max(0, max_files - current_file_count)

if len(all_files_to_process) > available_slots:
    log.warning(
        f"File limit exceeded: {current_file_count} existing + "
        f"{len(all_files_to_process)} new > {max_files} limit"
    )
    if available_slots == 0:
        # Cannot add any more files
        await self._update_sync_status(
            "file_limit_exceeded",
            error=f"This knowledge base has reached the {max_files}-file limit. "
                  f"Remove files or select fewer items to sync.",
        )
        # Save sources (delta links) and return early
        self._save_sources()
        return {
            "files_processed": 0,
            "files_failed": 0,
            "total_found": len(all_files_to_process),
            "deleted_count": deleted_count,
            "failed_files": [],
            "file_limit_exceeded": True,
        }
    else:
        # Process up to the limit with a warning
        all_files_to_process = all_files_to_process[:available_slots]
        await self._update_sync_status(
            "syncing",
            error=f"Only syncing {available_slots} of {len(all_files_to_process)} "
                  f"files due to {max_files}-file limit.",
        )
```

#### 7. File Count Limit: Frontend Status Handling
**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
**Changes**: Handle `"file_limit_exceeded"` sync status

In the sync progress handler and sync status polling:

```typescript
if (data.status === 'file_limit_exceeded') {
    toast.error(data.error || $i18n.t('File limit exceeded'));
    isSyncingOneDrive = false;
}
```

#### 8. File Count Limit: Add Endpoint for External KB
**File**: `backend/open_webui/routers/knowledge.py`
**Changes**: In the `POST /{id}/file/add` and batch add endpoints, check file count limits for non-local KBs

In the single file add endpoint (around line 437), add before processing:

```python
# Check file count limit for non-local KBs
if knowledge.type != "local":
    current_files = Knowledges.get_files_by_id(id)
    if current_files and len(current_files) >= 250:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This knowledge base has reached the 250-file limit.",
        )
```

In the batch add endpoint (around line 786), add a similar check:

```python
# Check file count limit for non-local KBs
if knowledge.type != "local":
    current_files = Knowledges.get_files_by_id(id)
    current_count = len(current_files) if current_files else 0
    if current_count + len(form_data) > 250:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Adding {len(form_data)} files would exceed the 250-file limit "
                   f"({current_count} files currently).",
        )
```

#### 9. i18n Keys for Phase 4
**File**: `src/lib/i18n/locales/en-US/translation.json`
```json
"Access to a OneDrive source has been revoked": "",
"File limit exceeded": "",
```

**File**: `src/lib/i18n/locales/nl-NL/translation.json`
```json
"Access to a OneDrive source has been revoked": "Toegang tot een OneDrive-bron is ingetrokken",
"File limit exceeded": "Bestandslimiet bereikt",
```

### Success Criteria:

#### Automated Verification:
- [ ] `npm run lint:backend` passes
- [ ] `npm run lint:frontend` passes
- [ ] `npm run check` passes
- [ ] `npm run build` succeeds
- [ ] Backend starts without errors: `open-webui dev`

#### Manual Verification:
- [ ] Sync a OneDrive folder -> files processed normally
- [ ] Remove user's access to the OneDrive folder (in OneDrive) -> next sync removes files from KB, shows notification
- [ ] Files removed due to revoked access are preserved for other users who still have access
- [ ] Sync a folder with > 250 files -> sync stops with "file_limit_exceeded" status
- [ ] Try to manually add a file to a full (250-file) non-local KB -> 400 error
- [ ] Batch add that would exceed limit -> 400 error with clear message
- [ ] File count indicator in KB header reflects accurate count

**Implementation Note**: After completing this phase and all verification passes, the typed knowledge bases feature is complete.

---

## Testing Strategy

### Unit Tests:
- Migration: verify `type` column added, data migration sets `type="onedrive"` for KBs with `meta.onedrive_sync`
- `KnowledgeForm` with `type` field: creation defaults, validation
- Access control enforcement: non-local KB forced to `access_control={}`
- File removal: external files preserved, orphan cleanup triggered when last reference removed
- File count limit: 250-file check for non-local KBs

### Integration Tests:
- Create local KB -> verify type="local", access control works
- Create OneDrive KB -> verify type="onedrive", access_control={}
- Two users sync same file -> one File record, two KnowledgeFile records
- Remove file from one KB -> File preserved for other
- Remove file from both KBs -> File cleaned up

### Manual Testing Steps:
1. Run migration, verify existing data
2. Create both KB types via UI
3. Sync OneDrive files across two user accounts
4. Verify dedup (check vector DB for single collection per file)
5. Remove files and verify preservation/cleanup
6. Revoke OneDrive access and verify sync cleanup
7. Test file count limits

## Performance Considerations

- **Dedup saves embedding cost**: Each OneDrive document is embedded once regardless of user count. For an organization with 50 users syncing the same folder, this saves 49x embedding API calls.
- **Vector propagation**: When a file changes, vectors are updated in all referencing KBs synchronously during the detecting user's sync. For KBs with many cross-references, this could slow down that sync. The `log.warning` on failure ensures best-effort without blocking.
- **Orphan cleanup**: Done inline during file removal (not a background job). This adds a single DB query per removal but avoids complexity of a scheduler.
- **Access verification**: One Graph API call per source before syncing. Adds latency proportional to source count, but sources are typically 1-3 per KB.

## Migration Notes

- The Alembic migration adds `type` with `server_default="local"`, so all existing rows get `type="local"` automatically
- The data migration then updates KBs with `meta.onedrive_sync` to `type="onedrive"`
- No data loss: the `meta.onedrive_sync` field is preserved (we're adding a column, not removing anything)
- Rollback: `alembic downgrade -1` drops the `type` column; the implicit detection via `meta.onedrive_sync` still works

## References

- Research document: `thoughts/shared/research/2026-02-04-typed-knowledge-bases-architecture.md`
- Cosmetic frontend changes (prerequisite for Phase 2): `thoughts/shared/plans/2026-02-04-cosmetic-frontend-changes.md`
- Background sync plan (complementary): `thoughts/shared/plans/2026-02-04-background-sync-multi-datasource.md`
- Knowledge model: `backend/open_webui/models/knowledge.py:42-70`
- Knowledge router: `backend/open_webui/routers/knowledge.py`
- OneDrive sync worker: `backend/open_webui/services/onedrive/sync_worker.py`
- Frontend KB list: `src/lib/components/workspace/Knowledge.svelte`
- Frontend KB creation: `src/lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte`
- Frontend KB detail: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- API client: `src/lib/apis/knowledge/index.ts`
- Access control: `backend/open_webui/utils/access_control.py:124-150`
- File model: `backend/open_webui/models/files.py:16-31`
- Alembic head: `f8e1a9c2d3b4` (2026-01-28)
