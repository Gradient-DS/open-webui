# OneDrive Group Permission Gaps - Implementation Plan

## Overview

The OneDrive permission system was built for individual user sharing but has 5 gaps when groups are involved. This plan closes all gaps with a restrictive approach: groups with unauthorized members are hard-blocked from accessing source-restricted KBs, and the system enforces this at every entry point (sharing, group membership changes, sync, file uploads, and listing).

## Current State Analysis

The permission system has two layers:
- **Layer 1 (KB access control)**: Standard `access_control` JSON with `read`/`write` containing `user_ids` and `group_ids`. Checked at list time via `has_access()`.
- **Layer 2 (Source permissions)**: OneDrive email matching against `permitted_emails` stored in file/knowledge metadata. Checked only at detail/open time via `check_knowledge_access()`.

Groups are first-class in Layer 1 but invisible to Layer 2. This creates gaps at every point where groups interact with source-restricted KBs.

### Key Discoveries:
- Sync worker always writes `group_ids: []`, wiping manually-added groups (`sync_worker.py:424-433`)
- Frontend only extracts `read.group_ids` for validation, missing `write.group_ids` (`SourceAwareAccessControl.svelte:67-68`)
- Strict mode filters `user_ids` but spreads `group_ids` unchanged (`SourceAwareAccessControl.svelte:112-120`)
- `add_user_to_group` performs zero source-permission validation (`groups.py:171-195`)
- KB list endpoints use `has_access()` only, no source checks (`knowledge.py:57-87`)
- Knowledge-level `meta.onedrive_sync.permitted_emails` is already stored and can be used as a fast cached check

## Desired End State

After implementation:

1. **Sharing a KB to a group** where any member lacks OneDrive access is blocked with a clear error listing affected groups and members
2. **Adding a user to a group** that has access to source-restricted KBs is blocked if the user lacks OneDrive access
3. **OneDrive re-sync** preserves manually-added groups but auto-removes groups whose members have lost upstream access
4. **KB list** hides source-restricted KBs from users who lack OneDrive access (no more "visible but 403" state)
5. **Uploading source files** to a KB shared with groups containing unauthorized members is blocked

### Verification:
- All 5 scenarios above can be tested via the UI and verified with the success criteria in each phase
- Existing individual-user sharing behavior is unchanged
- Admin users continue to have unrestricted access

## What We're NOT Doing

- **Event/hook system for group membership**: No generic pub/sub architecture. We add direct validation calls at the specific entry points.
- **Mapping OneDrive groups to Open WebUI groups**: The sync worker continues to operate on individual email→user mappings. Groups remain a manual overlay.
- **Per-user-per-KB cache table**: We use the existing `meta.onedrive_sync.permitted_emails` on the knowledge row as the fast-path check for list filtering. No new database tables.
- **Non-strict (lenient) mode changes**: The lenient mode path is not modified. All changes apply to strict mode behavior.

## Implementation Approach

Each phase targets one entry point, from backend-first (sync worker) to frontend-facing (list UX). Phases are independently testable but build on each other.

---

## Phase 1: Sync Worker — Preserve Groups & Enforce Permissions

### Overview
Fix the sync worker to preserve existing `group_ids` during re-sync, then validate each preserved group against the new OneDrive permissions. Groups with unauthorized members are automatically removed.

### Changes Required:

#### 1. Preserve and validate group_ids during permission sync
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Lines**: 413-452 (the `access_control` construction block)

Replace the current hardcoded `group_ids: []` with logic that:
1. Reads the existing `access_control` from the knowledge base
2. Preserves existing `group_ids` from `read` and `write`
3. Validates each group: expand members, check each member's email against `permitted_emails`
4. Removes groups where any member lacks access
5. Logs which groups were removed and why

```python
# Update knowledge access_control
if permitted_user_ids:
    # Ensure owner is included
    if self.user_id not in permitted_user_ids:
        permitted_user_ids.append(self.user_id)

    knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
    if knowledge:
        from open_webui.models.knowledge import KnowledgeForm

        # Preserve existing group_ids, validating each against new permissions
        existing_ac = knowledge.access_control or {}
        existing_read_groups = existing_ac.get("read", {}).get("group_ids", [])
        existing_write_groups = existing_ac.get("write", {}).get("group_ids", [])

        permitted_emails_lower = {e.lower() for e in permitted_emails}

        validated_read_groups = self._validate_groups_for_source_access(
            existing_read_groups, permitted_emails_lower
        )
        validated_write_groups = self._validate_groups_for_source_access(
            existing_write_groups, permitted_emails_lower
        )

        access_control = {
            "read": {
                "user_ids": permitted_user_ids,
                "group_ids": validated_read_groups,
            },
            "write": {
                "user_ids": [self.user_id],
                "group_ids": validated_write_groups,
            },
        }

        Knowledges.update_knowledge_by_id(
            self.knowledge_id,
            KnowledgeForm(
                name=knowledge.name,
                description=knowledge.description,
                access_control=access_control,
            ),
        )
```

#### 2. Add group validation helper method
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Location**: New method on `OneDriveSyncWorker` class

```python
def _validate_groups_for_source_access(
    self, group_ids: list[str], permitted_emails: set[str]
) -> list[str]:
    """Validate groups against permitted emails, returning only compliant groups.

    A group is compliant if ALL its members have emails in permitted_emails.
    Groups with any unauthorized member are removed.
    """
    validated = []
    for group_id in group_ids:
        group = Groups.get_group_by_id(group_id)
        if not group:
            log.info(f"Group {group_id} no longer exists, removing from KB access")
            continue

        member_ids = Groups.get_group_user_ids_by_id(group_id)
        if not member_ids:
            # Empty group is safe to keep
            validated.append(group_id)
            continue

        group_valid = True
        for member_id in member_ids:
            user = Users.get_user_by_id(member_id)
            if user and user.email:
                if user.email.lower() not in permitted_emails:
                    log.info(
                        f"Removing group '{group.name}' ({group_id}) from KB "
                        f"{self.knowledge_id}: member {user.email} lacks OneDrive access"
                    )
                    group_valid = False
                    break
            else:
                # User without email can't be validated — remove group
                log.info(
                    f"Removing group '{group.name}' ({group_id}) from KB "
                    f"{self.knowledge_id}: member {member_id} has no email"
                )
                group_valid = False
                break

        if group_valid:
            validated.append(group_id)

    return validated
```

#### 3. Add sync event for removed groups
**File**: `backend/open_webui/services/onedrive/sync_worker.py`
**Location**: Within the `_sync_permissions` method, after `access_control` is built

Add logging after comparing preserved vs validated groups to report which groups were removed. The existing `_emit_event` infrastructure can be used to notify the frontend via WebSocket.

```python
removed_read = set(existing_read_groups) - set(validated_read_groups)
removed_write = set(existing_write_groups) - set(validated_write_groups)
if removed_read or removed_write:
    removed_names = []
    for gid in removed_read | removed_write:
        g = Groups.get_group_by_id(gid)
        if g:
            removed_names.append(g.name)
    log.warning(
        f"Sync removed {len(removed_read | removed_write)} group(s) from KB "
        f"{self.knowledge_id} due to permission changes: {removed_names}"
    )
    await self._emit_event(
        "groups_removed",
        {
            "removed_group_ids": list(removed_read | removed_write),
            "removed_group_names": removed_names,
            "reason": "Members lost OneDrive access during re-sync",
        },
    )
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] Type checking passes: `npm run check`
- [x] Linting passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Create a KB with OneDrive files, manually share with a group via the UI. Trigger re-sync. Group is preserved in access_control.
- [ ] Remove OneDrive access for one group member upstream. Trigger re-sync. Group is automatically removed from access_control. Log message appears.
- [ ] KB with no existing groups syncs correctly (backwards compatible, no regression).

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Group-Aware Sharing Validation (Issues 1 & 5)

### Overview
Enhance the sharing validator to return group-level conflict information and hard-block sharing to groups with unauthorized members. Fix the frontend to validate both `read` and `write` group_ids.

### Changes Required:

#### 1. Add GroupConflict model
**File**: `backend/open_webui/services/permissions/validator.py`
**Location**: After `SharingRecommendation` class (line 33)

```python
class GroupConflict(BaseModel):
    """A group that cannot receive KB access due to source permissions."""
    group_id: str
    group_name: str
    role: str  # "read" or "write"
    members_without_access: List[SharingRecommendation]
```

#### 2. Add group_conflicts field to SharingValidationResult
**File**: `backend/open_webui/services/permissions/validator.py`
**Lines**: 46-55

Add a new field:

```python
class SharingValidationResult(BaseModel):
    """Result of validating a sharing operation."""
    can_share: bool
    can_share_to_users: List[str] = []
    cannot_share_to_users: List[str] = []
    blocking_resources: Dict[str, List[str]] = {}
    recommendations: List[SharingRecommendation] = []
    source_restricted: bool = False
    group_conflicts: List[GroupConflict] = []  # NEW
```

#### 3. Enhance validate_knowledge_share to track group conflicts
**File**: `backend/open_webui/services/permissions/validator.py`
**Method**: `validate_knowledge_share` (lines 60-166)

Changes:
- Accept both `read_group_ids` and `write_group_ids` (or restructure the input)
- After the bulk access check, attribute unauthorized users back to their groups
- Build `GroupConflict` entries for each group that has unauthorized members
- Set `can_share = False` when any `group_conflicts` exist

The method signature changes to accept structured group info:

```python
async def validate_knowledge_share(
    self,
    knowledge_id: str,
    target_user_ids: List[str],
    target_group_ids: List[str] = [],
    write_group_ids: List[str] = [],
) -> SharingValidationResult:
```

After the per-user permission check loop (line 157), add group conflict detection:

```python
# Detect group-level conflicts
group_conflicts: List[GroupConflict] = []

for role, gids in [("read", target_group_ids), ("write", write_group_ids)]:
    for group_id in gids:
        group = Groups.get_group_by_id(group_id)
        if not group:
            continue

        member_ids = Groups.get_group_user_ids_by_id(group_id)
        if not member_ids:
            continue

        unauthorized_members = []
        for member_id in member_ids:
            if member_id in cannot_share_to:
                # This member was already flagged as unauthorized
                rec = next(
                    (r for r in recommendations if r.user_id == member_id), None
                )
                if rec:
                    unauthorized_members.append(rec)

        if unauthorized_members:
            group_conflicts.append(
                GroupConflict(
                    group_id=group_id,
                    group_name=group.name,
                    role=role,
                    members_without_access=unauthorized_members,
                )
            )

has_group_conflicts = len(group_conflicts) > 0
```

Update the return statement:

```python
return SharingValidationResult(
    can_share=len(cannot_share_to) == 0 and not has_group_conflicts,
    can_share_to_users=list(can_share_to),
    cannot_share_to_users=list(cannot_share_to),
    blocking_resources=blocking_resources,
    recommendations=recommendations,
    source_restricted=True,
    group_conflicts=group_conflicts,
)
```

#### 4. Update the validate-share endpoint to pass write group_ids
**File**: `backend/open_webui/routers/knowledge.py`
**Location**: The `POST /{id}/validate-share` endpoint

Update the request model to accept `write_group_ids`:

```python
class ShareValidationRequest(BaseModel):
    user_ids: List[str] = []
    group_ids: List[str] = []
    write_group_ids: List[str] = []
```

Pass `write_group_ids` through to the validator.

#### 5. Update frontend TypeScript types
**File**: `src/lib/apis/knowledge/permissions.ts`
**Lines**: 10-26

Add `GroupConflict` interface and update `ShareValidationResult`:

```typescript
export interface GroupConflict {
    group_id: string;
    group_name: string;
    role: string;
    members_without_access: SharingRecommendation[];
}

export interface ShareValidationResult {
    can_share: boolean;
    can_share_to_users: string[];
    cannot_share_to_users: string[];
    blocking_resources: Record<string, string[]>;
    recommendations: SharingRecommendation[];
    source_restricted: boolean;
    group_conflicts: GroupConflict[];
}
```

#### 6. Update validateKnowledgeShare API call to pass write group_ids
**File**: `src/lib/apis/knowledge/permissions.ts`
**Lines**: 49-84

Update the function signature and body:

```typescript
export const validateKnowledgeShare = async (
    token: string,
    knowledgeId: string,
    userIds: string[],
    groupIds: string[],
    writeGroupIds: string[] = []
): Promise<ShareValidationResult | null> => {
    // ...
    body: JSON.stringify({
        user_ids: userIds,
        group_ids: groupIds,
        write_group_ids: writeGroupIds
    })
    // ...
};
```

#### 7. Fix SourceAwareAccessControl to validate both read and write groups
**File**: `src/lib/components/workspace/common/SourceAwareAccessControl.svelte`
**Lines**: 67-68

Change from:
```typescript
const userIds = newAccessControl?.read?.user_ids ?? [];
const groupIds = newAccessControl?.read?.group_ids ?? [];
```

To:
```typescript
const userIds = newAccessControl?.read?.user_ids ?? [];
const groupIds = newAccessControl?.read?.group_ids ?? [];
const writeGroupIds = newAccessControl?.write?.group_ids ?? [];
```

And update the validation call (line 72-77):
```typescript
validationResult = await validateKnowledgeShare(
    localStorage.token,
    knowledgeId,
    userIds,
    groupIds,
    writeGroupIds
);
```

#### 8. Handle group_conflicts in SourceAwareAccessControl
**File**: `src/lib/components/workspace/common/SourceAwareAccessControl.svelte`
**Lines**: 79-93

Add a check for group_conflicts alongside the existing checks:

```typescript
if (isGoingPublic && validationResult?.source_restricted) {
    pendingAccessControl = newAccessControl;
    showConfirmModal = true;
} else if (
    !isGoingPublic &&
    validationResult?.source_restricted &&
    validationResult?.group_conflicts?.length > 0
) {
    // Groups with unauthorized members — hard block
    pendingAccessControl = newAccessControl;
    showConfirmModal = true;
} else if (
    !isGoingPublic &&
    validationResult?.source_restricted &&
    !validationResult?.can_share
) {
    pendingAccessControl = newAccessControl;
    showConfirmModal = true;
} else {
    onChange(newAccessControl);
}
```

#### 9. Update handleConfirmShare to never proceed with group conflicts
**File**: `src/lib/components/workspace/common/SourceAwareAccessControl.svelte`
**Lines**: 103-130

In `handleConfirmShare`, if `group_conflicts` exist, do NOT proceed. The modal should only offer "Go back":

```typescript
function handleConfirmShare(event: CustomEvent) {
    const { shareToAll } = event.detail;

    // Never confirm if group conflicts exist (hard block)
    if (validationResult?.group_conflicts?.length > 0) {
        handleCancelShare();
        return;
    }

    // ... existing logic for user-level handling
}
```

#### 10. Add group conflict display to ShareConfirmationModal
**File**: `src/lib/components/workspace/common/ShareConfirmationModal.svelte`

Add a new props:
```typescript
export let groupConflicts: GroupConflict[] = [];
```

Add a new template section for group conflicts (shown when `groupConflicts.length > 0`). This section should:
- Display a red warning panel header: "Groups with members lacking source access"
- For each conflicting group: show group name, role, and list of unauthorized members with their emails
- Show "Grant access" links for each unauthorized member (using `grant_access_url` from the recommendation)
- Only show a "Go back" button (no confirm button when groups are blocked)
- Below the group list, explain: "Remove these groups from sharing, or grant OneDrive access to all their members before sharing."

#### 11. Pass groupConflicts to ShareConfirmationModal
**File**: `src/lib/components/workspace/common/SourceAwareAccessControl.svelte`
**Lines**: 258-266

```svelte
<ShareConfirmationModal
    bind:show={showConfirmModal}
    {validationResult}
    groupConflicts={validationResult?.group_conflicts ?? []}
    strictMode={strictSourcePermissions}
    targetName={knowledgeName}
    {isGoingPublic}
    on:confirm={handleConfirmShare}
    on:cancel={handleCancelShare}
/>
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] TypeScript type checking passes: `npm run check`
- [x] Frontend builds: `npm run build`
- [x] Backend linting passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Share a source-restricted KB with a group where all members have OneDrive access → share succeeds
- [ ] Share a source-restricted KB with a group where one member lacks OneDrive access → share is blocked with clear error showing which group and which member
- [ ] Share a source-restricted KB with both a valid group and an invalid group → blocked (must remove invalid group)
- [ ] Add a group with write access where a member lacks source access → blocked
- [ ] Individual user sharing behavior is unchanged (existing strict mode filtering still works)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Group Membership Validation (Issue 2)

### Overview
Add source-permission validation when adding users to groups. If the group has access to source-restricted KBs and the new user lacks source access, the add is blocked.

### Changes Required:

#### 1. Add method to find KBs by group_id
**File**: `backend/open_webui/models/knowledge.py`
**Location**: On the `KnowledgeTable` class, after existing methods

```python
def get_source_restricted_knowledge_by_group_id(
    self, group_id: str
) -> list[KnowledgeModel]:
    """Find source-restricted KBs that have this group in their access_control."""
    with get_db() as db:
        all_kbs = (
            db.query(Knowledge)
            .filter(Knowledge.access_control.isnot(None))
            .all()
        )
        result = []
        for kb in all_kbs:
            ac = kb.access_control
            if not ac:
                continue
            read_groups = ac.get("read", {}).get("group_ids", [])
            write_groups = ac.get("write", {}).get("group_ids", [])
            if group_id not in read_groups and group_id not in write_groups:
                continue
            # Check if KB has source-restricted files
            meta = kb.meta or {}
            if "onedrive_sync" in meta:
                result.append(KnowledgeModel.model_validate(kb))
        return result
```

#### 2. Add validation to add_user_to_group endpoint
**File**: `backend/open_webui/routers/groups.py`
**Lines**: 171-195

After the existing `Users.get_valid_user_ids()` call and before `Groups.add_users_to_group()`, add source permission validation:

```python
@router.post("/id/{id}/users/add", response_model=Optional[GroupResponse])
async def add_user_to_group(
    id: str,
    form_data: UserIdsForm,
    user=Depends(get_admin_user),
):
    if form_data.user_ids:
        form_data.user_ids = Users.get_valid_user_ids(form_data.user_ids)

    # Check if this group has access to source-restricted KBs
    source_restricted_kbs = Knowledges.get_source_restricted_knowledge_by_group_id(id)

    if source_restricted_kbs and form_data.user_ids:
        conflicts = []
        for uid in form_data.user_ids:
            target_user = Users.get_user_by_id(uid)
            if not target_user or not target_user.email:
                conflicts.append({
                    "user_id": uid,
                    "user_name": target_user.name if target_user else "Unknown",
                    "reason": "User has no email address",
                })
                continue

            user_email = target_user.email.lower()
            for kb in source_restricted_kbs:
                meta = kb.meta or {}
                permitted_emails = meta.get("onedrive_sync", {}).get("permitted_emails", [])
                permitted_lower = {e.lower() for e in permitted_emails}
                if user_email not in permitted_lower:
                    conflicts.append({
                        "user_id": uid,
                        "user_name": target_user.name,
                        "user_email": target_user.email,
                        "knowledge_id": kb.id,
                        "knowledge_name": kb.name,
                        "reason": f"User lacks OneDrive access for KB '{kb.name}'",
                    })
                    break  # One conflict per user is enough

        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Cannot add user(s) to group: source permission conflicts",
                    "conflicts": conflicts,
                },
            )

    group = Groups.add_users_to_group(id, form_data.user_ids)
    # ... rest of existing logic
```

Add the import for `Knowledges` at the top of `groups.py`.

#### 3. Handle 409 response in frontend group management
**File**: `src/lib/components/admin/Users/Groups/Users.svelte`
**Lines**: 66-80

Update `toggleMember` to handle the 409 conflict response:

```typescript
async function toggleMember(userId: string, state: string) {
    try {
        if (state === 'checked') {
            await addUserToGroup(localStorage.token, groupId, [userId]);
        } else {
            await removeUserFromGroup(localStorage.token, groupId, [userId]);
        }
    } catch (err: any) {
        if (err?.conflicts) {
            // Show source permission conflict error
            const conflict = err.conflicts[0];
            toast.error(
                `Cannot add user: ${conflict.user_name} lacks OneDrive access for "${conflict.knowledge_name}". ` +
                `Grant them access in OneDrive first.`,
                { duration: 8000 }
            );
            return;
        }
        toast.error(err?.message ?? 'Failed to update group membership');
        return;
    }
    getUserList();
}
```

Update the `addUserToGroup` API function (in `src/lib/apis/groups/index.ts`) to throw the parsed error detail instead of just the message string, so the conflict data is available to the caller.

#### 4. Update addUserToGroup API to preserve error detail
**File**: `src/lib/apis/groups/index.ts`
**Lines**: Within the `addUserToGroup` function

Ensure the `.catch` handler throws the full error detail object (not just `err.detail` as a string) so that `conflicts` is accessible:

```typescript
.catch((err) => {
    console.error(err);
    error = err.detail ?? err;  // Preserve the full detail object
    return null;
});
// ...
if (error) {
    throw error;
}
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] TypeScript type checking passes: `npm run check`
- [x] Frontend builds: `npm run build`
- [x] Backend linting passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Add a user with OneDrive access to a group that has access to a source-restricted KB → succeeds
- [ ] Add a user without OneDrive access to a group that has access to a source-restricted KB → blocked with toast error explaining the conflict
- [ ] Add a user to a group with no source-restricted KB access → succeeds (no change in behavior)
- [ ] The checkbox reverts to unchecked when the add is blocked

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: KB List Source Filtering (Issue 4)

### Overview
Filter source-restricted KBs from the list for users who lack OneDrive access. Uses the existing `meta.onedrive_sync.permitted_emails` as a fast cached check — no additional DB queries or tables needed.

### Changes Required:

#### 1. Add source-access post-filter to list endpoint
**File**: `backend/open_webui/routers/knowledge.py`
**Lines**: 57-87 (`get_knowledge_bases`) and 90-130 (`search_knowledge_bases`)

Add a helper function and apply it in both endpoints:

```python
def _filter_source_accessible(
    items: list, user_email: str, is_admin: bool
) -> list:
    """Filter out source-restricted KBs the user cannot access.

    Uses knowledge-level permitted_emails as a fast cached check.
    Admin users bypass this filter.
    """
    if is_admin and BYPASS_ADMIN_ACCESS_CONTROL:
        return items

    if not user_email:
        # User without email can't access any source-restricted KB
        return [
            kb for kb in items
            if not (kb.meta or {}).get("onedrive_sync")
        ]

    user_email_lower = user_email.lower()
    filtered = []
    for kb in items:
        meta = kb.meta or {}
        onedrive_sync = meta.get("onedrive_sync")
        if not onedrive_sync:
            # Not source-restricted, include
            filtered.append(kb)
            continue

        permitted_emails = onedrive_sync.get("permitted_emails", [])
        if user_email_lower in {e.lower() for e in permitted_emails}:
            filtered.append(kb)
        # else: user lacks source access, exclude from list

    return filtered
```

Apply in `get_knowledge_bases` (after line 73):

```python
result = Knowledges.search_knowledge_bases(
    user.id, filter=filter, skip=skip, limit=limit
)

# Filter source-restricted KBs the user can't access
filtered_items = _filter_source_accessible(
    result.items, user.email, user.role == "admin"
)

return KnowledgeAccessListResponse(
    items=[
        KnowledgeAccessResponse(
            **kb.model_dump(),
            write_access=(
                user.id == kb.user_id
                or has_access(user.id, "write", kb.access_control)
            ),
        )
        for kb in filtered_items
    ],
    total=result.total,  # Keep original total for now; frontend uses empty-page detection
)
```

Apply the same filter in `search_knowledge_bases` (after line 116).

#### 2. No frontend changes needed
The frontend uses infinite scroll with empty-page detection (`pageItems.length === 0` → `allItemsLoaded = true`). Partially-filled pages are handled naturally — the frontend simply loads the next page sooner. The `total` count is not displayed in the UI, so the slight mismatch from post-filtering is invisible to the user.

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] Backend linting passes: `npm run lint:backend`

#### Manual Verification:
- [ ] User with OneDrive access sees source-restricted KBs in their list
- [ ] User without OneDrive access does NOT see source-restricted KBs in their list
- [ ] Admin sees all KBs regardless of source access
- [ ] Non-source-restricted KBs appear normally for all users (no regression)
- [ ] Infinite scroll continues to work (no empty pages in the middle, eventually reaches end)
- [ ] The "visible but 403" scenario no longer occurs

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 5: File Upload Group Validation

### Overview
Enhance `validate_file_addition` to detect when uploading source-restricted files to a KB shared with groups containing unauthorized members. Block the upload with group-level conflict information.

### Changes Required:

#### 1. Add group_conflicts field to FileAdditionConflict
**File**: `backend/open_webui/services/permissions/validator.py`
**Lines**: 35-43

```python
class FileAdditionConflict(BaseModel):
    """Conflict when adding restricted files to shared KB."""
    has_conflict: bool
    kb_is_public: bool = False
    users_without_access: List[str] = []
    user_details: List[SharingRecommendation] = []
    source_type: str = ""
    grant_access_url: Optional[str] = None
    group_conflicts: List[GroupConflict] = []  # NEW
```

#### 2. Enhance validate_file_addition to detect group conflicts
**File**: `backend/open_webui/services/permissions/validator.py`
**Method**: `validate_file_addition` (lines 215-312)

After the per-user check loop (which uses `_get_kb_users()` to expand groups to users), attribute unauthorized users back to their source groups:

```python
# After building users_without_source_access set (line 286)...

# Detect group-level conflicts
group_conflicts: List[GroupConflict] = []
if users_without_source_access and knowledge.access_control:
    ac = knowledge.access_control
    for role_key in ["read", "write"]:
        for group_id in ac.get(role_key, {}).get("group_ids", []):
            group = Groups.get_group_by_id(group_id)
            if not group:
                continue

            member_ids = Groups.get_group_user_ids_by_id(group_id)
            unauthorized_members = []
            for member_id in member_ids or []:
                if member_id in users_without_source_access:
                    user = Users.get_user_by_id(member_id)
                    if user:
                        unauthorized_members.append(
                            SharingRecommendation(
                                user_id=member_id,
                                user_name=user.name,
                                user_email=user.email,
                                source_type=source_type,
                                inaccessible_count=len(file_ids),
                                grant_access_url=grant_url,
                            )
                        )

            if unauthorized_members:
                group_conflicts.append(
                    GroupConflict(
                        group_id=group_id,
                        group_name=group.name,
                        role=role_key,
                        members_without_access=unauthorized_members,
                    )
                )
```

Include `group_conflicts` in the return:

```python
return FileAdditionConflict(
    has_conflict=True,
    kb_is_public=False,
    users_without_access=list(users_without_source_access),
    user_details=user_details,
    source_type=source_type,
    grant_access_url=grant_url,
    group_conflicts=group_conflicts,
)
```

#### 3. Update frontend FileAdditionConflict type
**File**: `src/lib/apis/knowledge/permissions.ts`
**Lines**: 40-47

```typescript
export interface FileAdditionConflict {
    has_conflict: boolean;
    kb_is_public: boolean;
    users_without_access: string[];
    user_details: SharingRecommendation[];
    source_type: string;
    grant_access_url: string | null;
    group_conflicts: GroupConflict[];
}
```

#### 4. Update file upload conflict UI to show group information
**File**: The component that calls `validateFileAddition` and displays the conflict modal

When `group_conflicts` is non-empty in the file addition conflict response, display group-level information similar to the ShareConfirmationModal: list affected groups, their unauthorized members, and "Grant access" links. Block the upload until resolved.

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] TypeScript type checking passes: `npm run check`
- [x] Frontend builds: `npm run build`
- [x] Backend linting passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Upload an OneDrive file to a KB shared only with users who have access → upload succeeds
- [ ] Upload an OneDrive file to a KB shared with a group containing a member without access → upload is blocked with error showing which group and member
- [ ] Upload a local file to a KB shared with any group → upload succeeds (no false positive)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding.

---

## Testing Strategy

### Unit Tests:
- `validate_knowledge_share` returns `group_conflicts` for groups with unauthorized members
- `validate_knowledge_share` returns empty `group_conflicts` for groups where all members have access
- `validate_file_addition` includes `group_conflicts` when uploading restricted files to group-shared KBs
- `_validate_groups_for_source_access` correctly filters groups (all pass, some fail, empty group, deleted group)
- `_filter_source_accessible` correctly filters KBs (admin bypass, user with access, user without, no-email user, non-restricted KB)
- `get_source_restricted_knowledge_by_group_id` finds the right KBs

### Integration Tests:
- End-to-end sharing flow: create KB → sync OneDrive files → share to group → validate → block/allow
- Re-sync flow: share to group → change OneDrive permissions → re-sync → group removed
- Group add flow: group has KB access → add user without OneDrive access → 409 conflict

### Manual Testing Steps:
1. Create a KB, sync OneDrive files from a shared folder
2. Create a group with 2 users (one with OneDrive access, one without)
3. Try to share KB with the group → verify blocked with group conflict error
4. Grant OneDrive access to the second user → retry share → verify succeeds
5. Revoke OneDrive access for the second user → trigger re-sync → verify group removed from KB access_control
6. Log in as the user without access → verify KB is not visible in the list
7. Try to add a user without OneDrive access to the group → verify 409 error in admin panel
8. Upload a new OneDrive file to a KB shared with a group containing unauthorized members → verify blocked

## Performance Considerations

- **Phase 4 (KB list filtering)**: The post-filter reads `meta.onedrive_sync.permitted_emails` from the knowledge row already loaded from the DB. No additional queries. The set construction per KB is O(n) where n is the number of permitted emails (typically <100). This adds negligible overhead.
- **Phase 3 (group membership check)**: `get_source_restricted_knowledge_by_group_id` scans all non-public KBs and filters in Python. For typical deployments (<1000 KBs), this is fast. For larger deployments, this could be optimized with a JSON query or a denormalized index, but that's out of scope.
- **Phase 1 (sync group validation)**: Runs during sync (background task), not in the request path. No user-facing latency impact.

## Migration Notes

No database migrations needed. All changes use existing columns and JSON structures:
- `knowledge.access_control` (existing JSON column)
- `knowledge.meta.onedrive_sync.permitted_emails` (existing JSON field)
- `group_member` table (existing)

The only "migration" is behavioral: after Phase 1, the next OneDrive re-sync will preserve groups and validate them. Groups that were previously overwritten with `[]` on every sync will now be preserved.

## References

- Research document: `thoughts/shared/research/2026-02-04-onedrive-group-permission-gaps.md`
- Validator: `backend/open_webui/services/permissions/validator.py:60-166`
- Enforcement: `backend/open_webui/services/permissions/enforcement.py:39-142`
- OneDrive provider: `backend/open_webui/services/permissions/providers/onedrive.py:29-97`
- Sync worker: `backend/open_webui/services/onedrive/sync_worker.py:345-456`
- Groups router: `backend/open_webui/routers/groups.py:171-195`
- KB list endpoint: `backend/open_webui/routers/knowledge.py:57-130`
- Frontend access control: `src/lib/components/workspace/common/SourceAwareAccessControl.svelte`
- Frontend confirmation modal: `src/lib/components/workspace/common/ShareConfirmationModal.svelte`
- Frontend group management: `src/lib/components/admin/Users/Groups/Users.svelte`
- Frontend permission types: `src/lib/apis/knowledge/permissions.ts`
