# Cloud KB Permission Fix + Suspension Lifecycle

## Overview

Rewrite the `_sync_permissions()` method in both cloud sync workers to stop mirroring upstream sharing permissions into Open WebUI access grants. Instead, only verify that the KB owner still has access to the cloud folder. If the owner loses access, suspend the KB with a 30-day grace period before permanent hard-deletion.

## Current State Analysis

### The Problem

`_sync_permissions()` in `OneDriveSyncWorker` (`:279`) and `GoogleDriveSyncWorker` (`:203`) runs on every sync cycle and:

1. Fetches ALL folder permissions from the cloud provider API
2. Maps every email to an Open WebUI user
3. Creates `read` access grants for every matched user + `write` for the owner
4. Calls `Knowledges.update_knowledge_by_id()` which replaces all grants via `AccessGrants.set_access_grants()`

In corporate M365/Google Workspace tenants, folders are often shared with entire teams or the organization, making private KBs visible to all team members as read-only.

### What Already Exists

- `_verify_source_access()` (OneDrive `:378`, Google Drive `:275`) already checks if the owner can access a source (403/404 → false). This runs per-source AFTER `_sync_permissions()`.
- `_handle_revoked_source()` removes files + vectors when a source fails verification.
- `_update_sync_status()` can set `access_revoked` status in `meta[meta_key]`.
- Knowledge model has `deleted_at` for soft-delete, `meta` (JSON) for sync state.
- Frontend already handles `access_revoked` status (toast + polling).
- `DeletionService.delete_knowledge()` does full cascade cleanup (vectors, model refs, KB row).
- Cleanup worker (`services/deletion/cleanup_worker.py`) runs every 60s, processes soft-deleted KBs via `get_pending_deletions()`.

### Key Discoveries:

- Owner gets implicit access in the KB listing query — `has_permission_filter()` at `access_grants.py:761-765` uses `OR(owner, grant_exists)`, so the owner never needs an explicit grant.
- The router's `update_knowledge_by_id` endpoint (`:505-506`) already prevents manual access grant changes on non-local KBs — but the sync worker calls the model layer directly, bypassing this guard.
- The retrieval path (`retrieval/utils.py:1074-1083`) also checks KB access with the same admin/owner/grant pattern. If `get_knowledge_by_id()` returns `None` (deleted/soft-deleted), the KB is silently skipped.
- Sync meta keys: `onedrive_sync` and `google_drive_sync` — both stored in `knowledge.meta`.

## Desired End State

After this plan is complete:

1. **No cloud sharing → access grants mapping**: `_sync_permissions()` only verifies the owner still has access to the folder. No access grants are created for any user.
2. **Suspension lifecycle**: When the owner loses access, the KB enters a "suspended" state (`meta[meta_key].suspended_at = epoch`). The KB:
   - Is grayed out in the KB list with an info tooltip
   - Cannot be navigated to (content blocked)
   - Returns 403 on detail/file API endpoints with explanatory message
   - Is not queryable via chat (retrieval path skips it)
   - Is not synced by the scheduler
3. **Auto-unsuspend**: If the owner regains access on the next sync cycle, `suspended_at` is cleared and the KB resumes normal operation.
4. **30-day hard-delete**: KBs suspended for 30+ days are permanently deleted (files, vectors, KB row) by the cleanup worker.
5. **Defence in depth preserved**: `_verify_source_access()` per-source check remains alongside the new owner-level check in `_sync_permissions()`.

### How to verify:

- Cloud KBs no longer create access grants for non-owner users
- A KB where the owner lost cloud access shows as suspended (grayed, info tooltip, content blocked)
- Re-sharing the folder with the owner auto-unsuspends on next sync
- After 30 days suspended, the cleanup worker hard-deletes the KB
- The scheduler skips suspended KBs

## What We're NOT Doing

- **Cleaning up existing access grants**: Will be done manually on the affected tenant, not via migration.
- **Changing `_verify_source_access()`**: Per-source verification stays as-is for defence in depth.
- **Adding a new DB column**: `suspended_at` lives in `meta[meta_key]`, not as a column — avoids a migration and keeps the pattern consistent with other sync state.
- **Admin override for suspension**: Admins can still delete suspended KBs, but there's no "force unsuspend" action.

## Implementation Approach

Store `suspended_at` as an epoch timestamp inside the existing `meta[meta_key]` dict. This is consistent with how all other sync state is stored (`status`, `last_sync_at`, `needs_reauth`, etc.) and avoids a schema migration. The cleanup worker already runs every 60s and can cheaply check for expired suspensions.

The frontend changes are minimal — the list view already has badge/tooltip patterns, and the detail page already has `disabled:opacity-40` styling. We add a suspension check that prevents navigation and shows an info message.

---

## Phase 1: Rewrite `_sync_permissions()` — Owner-Only Access Check

### Overview

Replace the email-mapping permission sync in both workers with a simple owner-access verification. If the owner lost access to the folder, set `suspended_at` in sync meta. If the owner has access and `suspended_at` exists, clear it (unsuspend).

### Changes Required:

#### 1. OneDrive Sync Worker

**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Changes**: Replace `_sync_permissions()` (lines 279-376)

```python
async def _sync_permissions(self) -> None:
    """Verify the KB owner still has access to the cloud folder.

    If the owner lost access, suspend the KB by setting suspended_at in sync meta.
    If the owner has access and the KB was previously suspended, unsuspend it.
    Does NOT mirror cloud sharing permissions to Open WebUI access grants.
    """
    folder_source = next((s for s in self.sources if s.get('type') == 'folder'), None)
    if not folder_source:
        log.info('No folder sources, skipping owner access check')
        return

    try:
        # Check if the owner can still access the folder
        item = await self._client.get_item(
            folder_source['drive_id'], folder_source['item_id']
        )
        owner_has_access = item is not None
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 404):
            owner_has_access = False
        else:
            # Transient error — don't change suspension state
            log.warning(f'Transient error checking owner access: {e}')
            return
    except Exception as e:
        log.warning(f'Error checking owner access: {e}')
        return

    knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
    if not knowledge:
        return

    meta = knowledge.meta or {}
    sync_info = meta.get(self.meta_key, {})

    if owner_has_access:
        if sync_info.get('suspended_at'):
            log.info(
                f'Owner regained access to folder, unsuspending KB {self.knowledge_id}'
            )
            sync_info.pop('suspended_at', None)
            sync_info.pop('suspended_reason', None)
            meta[self.meta_key] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
    else:
        if not sync_info.get('suspended_at'):
            log.warning(
                f'Owner {self.user_id} lost access to OneDrive folder, '
                f'suspending KB {self.knowledge_id}'
            )
            sync_info['suspended_at'] = int(time.time())
            sync_info['suspended_reason'] = 'owner_access_lost'
            meta[self.meta_key] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

            await self._update_sync_status(
                'suspended',
                error='Owner no longer has access to the cloud folder. '
                      'KB suspended — will be deleted after 30 days if access is not restored.',
            )
```

#### 2. Google Drive Sync Worker

**File**: `backend/open_webui/services/google_drive/sync_worker.py`
**Changes**: Replace `_sync_permissions()` (lines 203-273)

```python
async def _sync_permissions(self) -> None:
    """Verify the KB owner still has access to the cloud folder.

    If the owner lost access, suspend the KB by setting suspended_at in sync meta.
    If the owner has access and the KB was previously suspended, unsuspend it.
    Does NOT mirror cloud sharing permissions to Open WebUI access grants.
    """
    import httpx

    folder_source = next((s for s in self.sources if s.get('type') == 'folder'), None)
    if not folder_source:
        log.info('No folder sources, skipping owner access check')
        return

    try:
        item = await self._client.get_file(folder_source['item_id'])
        owner_has_access = item is not None
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 404):
            owner_has_access = False
        else:
            log.warning(f'Transient error checking owner access: {e}')
            return
    except Exception as e:
        log.warning(f'Error checking owner access: {e}')
        return

    knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
    if not knowledge:
        return

    meta = knowledge.meta or {}
    sync_info = meta.get(self.meta_key, {})

    if owner_has_access:
        if sync_info.get('suspended_at'):
            log.info(
                f'Owner regained access to folder, unsuspending KB {self.knowledge_id}'
            )
            sync_info.pop('suspended_at', None)
            sync_info.pop('suspended_reason', None)
            meta[self.meta_key] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
    else:
        if not sync_info.get('suspended_at'):
            log.warning(
                f'Owner {self.user_id} lost access to Google Drive folder, '
                f'suspending KB {self.knowledge_id}'
            )
            sync_info['suspended_at'] = int(time.time())
            sync_info['suspended_reason'] = 'owner_access_lost'
            meta[self.meta_key] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

            await self._update_sync_status(
                'suspended',
                error='Owner no longer has access to the cloud folder. '
                      'KB suspended — will be deleted after 30 days if access is not restored.',
            )
```

#### 3. Base Sync Worker — Skip Sync When Suspended

**File**: `backend/open_webui/services/sync/base_worker.py`
**Changes**: Add early return in `sync()` after `_sync_permissions()` if KB was suspended

After the `await self._sync_permissions()` call at line 701, add:

```python
# Check if KB was suspended by _sync_permissions()
knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
if knowledge:
    meta = knowledge.meta or {}
    sync_info = meta.get(self.meta_key, {})
    if sync_info.get('suspended_at'):
        log.info(f'KB {self.knowledge_id} is suspended, skipping sync')
        return {
            'files_processed': 0,
            'files_failed': 0,
            'total_found': 0,
            'deleted_count': 0,
            'failed_files': [],
            'suspended': True,
        }
```

#### 4. Sync Scheduler — Skip Suspended KBs

**File**: `backend/open_webui/services/sync/scheduler.py`
**Changes**: Add suspension check in `_is_sync_due()` (around line 129)

Add after the existing `needs_reauth` check:

```python
# Skip suspended KBs
if sync_info.get('suspended_at'):
    return False
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts successfully: `open-webui dev`
- [x] No import errors in sync worker modules
- [ ] Type checking passes on modified files: `npm run check` (note: pre-existing errors expected)

#### Manual Verification:

- [ ] Create a cloud KB, verify no access grants are created for other users
- [ ] Revoke owner access upstream, trigger sync — verify `suspended_at` is set in meta
- [ ] Re-grant owner access, trigger sync — verify `suspended_at` is cleared
- [ ] Suspended KB is not re-synced by the scheduler

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Suspension Lifecycle — 30-Day Auto-Delete

### Overview

Extend the existing cleanup worker to detect KBs suspended for 30+ days and hard-delete them. Also add a helper to check suspension state from the knowledge model.

### Changes Required:

#### 1. Knowledge Model — Add Suspension Helpers

**File**: `backend/open_webui/models/knowledge.py`
**Changes**: Add methods to `KnowledgeTable`

```python
SUSPENSION_TTL_DAYS = 30

def get_suspended_expired_knowledge(self, limit: int = 50) -> list[KnowledgeModel]:
    """Get cloud KBs suspended for longer than SUSPENSION_TTL_DAYS."""
    import time
    cutoff = int(time.time()) - (SUSPENSION_TTL_DAYS * 24 * 60 * 60)

    with get_db() as db:
        # Query all non-local, non-deleted KBs
        candidates = (
            db.query(Knowledge)
            .filter(Knowledge.deleted_at.is_(None))
            .filter(Knowledge.type != 'local')
            .limit(limit * 5)  # over-fetch since we filter in Python
            .all()
        )

        expired = []
        for kb in candidates:
            meta = kb.meta or {}
            # Check all possible sync meta keys
            for meta_key in ('onedrive_sync', 'google_drive_sync'):
                sync_info = meta.get(meta_key, {})
                suspended_at = sync_info.get('suspended_at')
                if suspended_at and suspended_at < cutoff:
                    expired.append(self._to_knowledge_model(kb, db=db))
                    break

            if len(expired) >= limit:
                break

        return expired

def is_suspended(self, id: str) -> bool:
    """Check if a knowledge base is currently suspended."""
    try:
        with get_db() as db:
            knowledge = db.query(Knowledge).filter_by(id=id).filter(Knowledge.deleted_at.is_(None)).first()
            if not knowledge:
                return False
            meta = knowledge.meta or {}
            for meta_key in ('onedrive_sync', 'google_drive_sync'):
                sync_info = meta.get(meta_key, {})
                if sync_info.get('suspended_at'):
                    return True
            return False
    except Exception:
        return False

def get_suspension_info(self, id: str) -> Optional[dict]:
    """Get suspension details for a KB. Returns None if not suspended."""
    try:
        with get_db() as db:
            knowledge = db.query(Knowledge).filter_by(id=id).filter(Knowledge.deleted_at.is_(None)).first()
            if not knowledge:
                return None
            meta = knowledge.meta or {}
            for meta_key in ('onedrive_sync', 'google_drive_sync'):
                sync_info = meta.get(meta_key, {})
                suspended_at = sync_info.get('suspended_at')
                if suspended_at:
                    return {
                        'suspended_at': suspended_at,
                        'reason': sync_info.get('suspended_reason', 'unknown'),
                        'days_remaining': max(0, SUSPENSION_TTL_DAYS - ((int(time.time()) - suspended_at) // 86400)),
                    }
            return None
    except Exception:
        return None
```

#### 2. Cleanup Worker — Add Suspended KB Cleanup

**File**: `backend/open_webui/services/deletion/cleanup_worker.py`
**Changes**: Add `_process_expired_suspensions()` to the cleanup loop

```python
def _process_pending_deletions():
    """Process all pending KB and chat deletions. Runs in thread pool."""
    _process_pending_kb_deletions()
    _process_pending_chat_deletions()
    _process_expired_suspensions()


def _process_expired_suspensions():
    """Hard-delete cloud KBs that have been suspended for 30+ days."""
    from open_webui.models.knowledge import Knowledges
    from open_webui.services.deletion import DeletionService

    expired_kbs = Knowledges.get_suspended_expired_knowledge(limit=10)
    if not expired_kbs:
        return

    log.info('Processing %d expired suspended KBs for hard-deletion', len(expired_kbs))

    for kb in expired_kbs:
        try:
            kb_files = Knowledges.get_files_by_id(kb.id)
            kb_file_ids = [f.id for f in kb_files]

            report = DeletionService.delete_knowledge(kb.id, delete_files=False)

            if report.has_errors:
                log.warning('Suspended KB %s cleanup had errors: %s', kb.id, report.errors)

            if kb_file_ids:
                file_report = DeletionService.delete_orphaned_files_batch(kb_file_ids)
                if file_report.has_errors:
                    log.warning('Suspended KB %s file cleanup errors: %s', kb.id, file_report.errors)

            log.info('Suspended KB %s (%s) hard-deleted after 30-day grace period', kb.id, kb.name)

        except Exception:
            log.exception('Failed to cleanup suspended KB %s', kb.id)
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts successfully: `open-webui dev`
- [x] No import errors in cleanup worker and knowledge model

#### Manual Verification:

- [ ] Manually set `suspended_at` to 31 days ago in a test KB's meta — verify cleanup worker deletes it within 60s
- [ ] Verify `is_suspended()` and `get_suspension_info()` return correct results

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Backend — Block Access to Suspended KBs

### Overview

Add suspension checks to the knowledge detail, files, and retrieval endpoints. Suspended KBs should return 403 with an explanatory message.

### Changes Required:

#### 1. Knowledge Router — Block Detail and File Access

**File**: `backend/open_webui/routers/knowledge.py`
**Changes**: Add suspension check after the existing access check in `GET /{id}` and `GET /{id}/files`

In the `GET /{id}` endpoint (around line 436, after access is verified):

```python
# Check suspension status
suspension_info = Knowledges.get_suspension_info(id)
if suspension_info and user.role != 'admin':
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"This knowledge base is suspended because the owner lost access to the cloud folder. "
               f"It will be permanently deleted in {suspension_info['days_remaining']} days "
               f"unless the owner restores access.",
    )
```

Same check in the `GET /{id}/files` endpoint (around line 637).

**Note**: Admins can still access suspended KBs (for inspection/manual deletion).

#### 2. Knowledge List — Add Suspension Info to Response

**File**: `backend/open_webui/models/knowledge.py`
**Changes**: Extend `KnowledgeUserModel` response to include `suspension_info`

Add to `KnowledgeUserModel`:

```python
class KnowledgeUserModel(KnowledgeModel):
    user: Optional[UserResponse] = None
    suspension_info: Optional[dict] = None
```

In `search_knowledge_bases()` (around line 276), after building the results list, annotate each item with suspension info:

```python
# Annotate suspension info for cloud KBs
for item in knowledge_items:
    if item.type not in ('local',) and item.meta:
        for meta_key in ('onedrive_sync', 'google_drive_sync'):
            sync_info = (item.meta or {}).get(meta_key, {})
            suspended_at = sync_info.get('suspended_at')
            if suspended_at:
                item.suspension_info = {
                    'suspended_at': suspended_at,
                    'reason': sync_info.get('suspended_reason', 'unknown'),
                    'days_remaining': max(0, SUSPENSION_TTL_DAYS - ((int(time.time()) - suspended_at) // 86400)),
                }
                break
```

#### 3. Retrieval Path — Skip Suspended KBs in Chat

**File**: `backend/open_webui/retrieval/utils.py`
**Changes**: In `get_sources_from_items()` (around line 1074), after fetching the KB and before the access check:

```python
# Skip suspended KBs
if knowledge and Knowledges.is_suspended(knowledge.id):
    log.info(f'Skipping suspended KB {item.get("id")} in retrieval')
    continue
```

### Success Criteria:

#### Automated Verification:

- [x] Backend starts successfully: `open-webui dev`
- [x] Build succeeds: `npm run build`

#### Manual Verification:

- [ ] Non-admin user trying to access a suspended KB detail page gets 403 with explanatory message
- [ ] Admin user can still access suspended KB detail
- [ ] Suspended KB is skipped when used in chat (no error, just silently excluded)
- [ ] KB list API returns `suspension_info` for suspended KBs

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to Phase 4.

---

## Phase 4: Frontend — Suspended KB Display

### Overview

Gray out suspended KBs in the knowledge list, prevent navigation to their content, and show an info tooltip explaining the situation.

### Changes Required:

#### 1. Knowledge List — Gray Out Suspended KBs

**File**: `src/lib/components/workspace/Knowledge.svelte`
**Changes**: Modify the KB item rendering (around line 367)

Replace the button element for each item with conditional styling and click behavior:

```svelte
<button
    class="flex space-x-4 cursor-pointer text-left w-full px-3 py-2.5 dark:hover:bg-gray-850/50 hover:bg-gray-50 transition rounded-2xl
        {item.suspension_info ? 'opacity-50 cursor-not-allowed' : ''}"
    on:click={() => {
        if (item.suspension_info) {
            // Don't navigate — suspended KBs can't be viewed
            return;
        }
        if (item?.meta?.document) {
            toast.error(
                $i18n.t(
                    'Only collections can be edited, create a new knowledge base to edit/add documents.'
                )
            );
        } else {
            goto(`/workspace/knowledge/${item.id}`);
        }
    }}
>
```

Add a suspension badge + info tooltip after the existing type badges (around line 409, after the `{/if}` closing the type badge block):

```svelte
{#if item.suspension_info}
	<Tooltip
		content={$i18n.t(
			'The owner lost access to the cloud folder. This knowledge base will be permanently deleted in {{days}} days unless access is restored.',
			{ days: item.suspension_info.days_remaining }
		)}
	>
		<Badge type="warning" content={$i18n.t('Suspended')} />
	</Tooltip>
{/if}
```

#### 2. Knowledge Detail Page — Handle 403 for Suspended KBs

**File**: `src/routes/(app)/workspace/knowledge/[id]/+page.svelte`
**Changes**: The existing error handling for the GET detail call should already handle a 403 response. Verify that the error message from the backend is shown to the user via toast or error state. If not, add handling:

```svelte
// In the load/init function, catch 403 and show the suspension message
const res = await getKnowledgeById(localStorage.token, $page.params.id).catch((error) => {
    toast.error(error);
    goto('/workspace/knowledge');
    return null;
});
```

This should already work because the API client functions in `src/lib/apis/knowledge/index.ts` throw the error detail string on non-2xx responses.

#### 3. TypeScript Types — Add `suspension_info`

**File**: `src/lib/apis/knowledge/index.ts` (or wherever the KB type is defined)
**Changes**: If there's an explicit TypeScript interface for knowledge items, add `suspension_info?: { suspended_at: number; reason: string; days_remaining: number } | null`.

If the codebase uses implicit types (no explicit interface), this step can be skipped — the field will be available dynamically.

### Success Criteria:

#### Automated Verification:

- [x] Frontend builds: `npm run build`
- [ ] No new TypeScript errors in modified files: `npm run check`

#### Manual Verification:

- [ ] Suspended KB appears grayed out (opacity-50) in the knowledge list
- [ ] Suspended KB shows a "Suspended" warning badge with tooltip explaining the situation and days remaining
- [ ] Clicking a suspended KB does NOT navigate to the detail page
- [ ] Navigating directly to `/workspace/knowledge/{suspended-id}` shows error toast and redirects to list
- [ ] Non-suspended KBs are unaffected

**Implementation Note**: After completing this phase, all changes are complete.

---

## Testing Strategy

### Unit Tests:

- `is_suspended()` returns `True`/`False` correctly based on meta
- `get_suspension_info()` returns correct `days_remaining` calculation
- `get_suspended_expired_knowledge()` only returns KBs past the 30-day cutoff

### Integration Tests:

- Sync cycle with valid owner access: no `suspended_at` set, sync proceeds normally
- Sync cycle with revoked owner access: `suspended_at` set, sync returns early
- Sync cycle after re-granting access: `suspended_at` cleared, sync resumes
- Cleanup worker processes expired suspended KB: full hard-delete

### Manual Testing Steps:

1. Create OneDrive KB, verify no access grants for non-owner users
2. Create Google Drive KB, verify same
3. Simulate owner access loss (revoke sharing), trigger sync — verify suspension
4. Verify list view shows grayed-out KB with warning badge
5. Verify detail page returns 403 for non-admin
6. Re-grant access, trigger sync — verify unsuspension
7. Set `suspended_at` to 31 days ago, wait for cleanup worker — verify hard-delete

## Performance Considerations

- `get_suspended_expired_knowledge()` queries all non-local KBs and filters in Python. This is fine for the expected scale (tens to low hundreds of cloud KBs per tenant). If scale grows, add a SQL filter on `meta` JSON (database-specific).
- `is_suspended()` makes a single DB query per call. In the retrieval hot path, this adds one lightweight query per KB in the chat context. Acceptable for typical usage (1-3 KBs per chat).
- The cleanup worker over-fetches candidates (`limit * 5`) to compensate for Python-side filtering. The `limit=10` default means at most 50 rows scanned per cycle.

## Migration Notes

- No schema migration needed — `suspended_at` lives in the existing `meta` JSON column.
- Existing access grants created by the old permission sync will be cleaned up manually on the affected tenant.
- After deploying, the first sync cycle for each cloud KB will run the new `_sync_permissions()` which no longer creates access grants. Existing grants remain until manually cleaned.

## References

- OneDrive sync worker: `backend/open_webui/services/onedrive/sync_worker.py:279`
- Google Drive sync worker: `backend/open_webui/services/google_drive/sync_worker.py:203`
- Base sync worker: `backend/open_webui/services/sync/base_worker.py:693`
- Sync scheduler: `backend/open_webui/services/sync/scheduler.py:129`
- Access grants model: `backend/open_webui/models/access_grants.py:663`
- Knowledge model: `backend/open_webui/models/knowledge.py`
- Knowledge router: `backend/open_webui/routers/knowledge.py`
- Retrieval utils: `backend/open_webui/retrieval/utils.py:926`
- Cleanup worker: `backend/open_webui/services/deletion/cleanup_worker.py`
- Frontend KB list: `src/lib/components/workspace/Knowledge.svelte`
- Frontend KB detail: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
