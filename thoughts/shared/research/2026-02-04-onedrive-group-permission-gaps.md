---
date: 2026-02-04T18:30:00+01:00
researcher: Claude
git_commit: 2f06a078a3174f445d354cc9be03d1a8f045ec24
branch: feat/data-control
repository: Gradient-DS/open-webui
topic: "OneDrive permission gaps when sharing knowledge bases with groups"
tags: [research, codebase, onedrive, groups, permissions, access-control, knowledge-base]
status: complete
last_updated: 2026-02-04
last_updated_by: Claude
---

# Research: OneDrive Permission Gaps When Sharing Knowledge Bases with Groups

**Date**: 2026-02-04T18:30:00+01:00
**Researcher**: Claude
**Git Commit**: 2f06a078a3174f445d354cc9be03d1a8f045ec24
**Branch**: feat/data-control
**Repository**: Gradient-DS/open-webui

## Research Question

When knowledge bases contain OneDrive files, the permission system has gaps around group-based sharing. Specifically:
1. A KB with OneDrive files can be shared to a group whose members don't have OneDrive access
2. Users can be added to a group that has access to a KB with OneDrive files without any permission check
3. OneDrive files can be uploaded to a KB shared with a group containing users without OneDrive permissions
4. Users without OneDrive permissions can see the KB in their list but get a 403 when opening it

## Summary

The OneDrive permission system was designed primarily around **individual user sharing**, not group-based sharing. The current implementation has five distinct gaps when groups are involved:

1. **Group sharing bypasses source-permission filtering** -- When sharing a KB to a group in strict mode, users without OneDrive access are identified but the group itself is never removed from the access control, so all group members retain KB-level access.
2. **Adding users to groups has no permission hooks** -- The `add_user_to_group` endpoint performs zero source-permission validation.
3. **OneDrive sync never writes group_ids** -- The sync worker hardcodes `group_ids: []` in access_control, so the group-based sharing path is entirely manual/admin-driven and disconnected from OneDrive permissions.
4. **KB listing doesn't check source permissions** -- Users see KBs they can't actually open, creating a confusing UX.
5. **Write-role groups are not validated** -- The frontend only extracts `read.group_ids` for validation, missing `write.group_ids`.

## Detailed Findings

### Issue 1: Group Sharing Bypasses Source-Permission Filtering in Strict Mode

**The problem:** When a user shares a KB (containing OneDrive files) with a group, the `SourceAwareAccessControl` component validates the share by calling `validateKnowledgeShare()` with the group_ids. The backend properly expands the group to individual users and identifies which users lack OneDrive access. However, when the user confirms the share in strict mode, only individual `user_ids` are filtered -- the `group_ids` array passes through unchanged.

**Root cause in frontend** (`src/lib/components/workspace/common/SourceAwareAccessControl.svelte:109-121`):

```typescript
const filteredAccessControl = {
    ...pendingAccessControl,
    read: {
        ...pendingAccessControl.read,  // group_ids preserved via spread
        user_ids: (pendingAccessControl.read?.user_ids ?? []).filter((id: string) =>
            allowedUserIds.has(id)
        )
    }
};
onChange(filteredAccessControl);
```

The spread `...pendingAccessControl.read` preserves `group_ids` untouched. Only `user_ids` are filtered against the `allowedUserIds` set. The group remains in the access control even if some of its members lack OneDrive access.

**Backend validation confirms the issue** (`backend/open_webui/services/permissions/validator.py:94-99`): The validator correctly expands groups to users and checks each user, but the result (`can_share_to_users` / `cannot_share_to_users`) is a flat list of user IDs with no group-level information. The frontend has no data to decide whether to remove a group.

**Impact:** In strict mode, the intent is to prevent users without OneDrive access from reaching the KB. But because the group stays in access_control, all group members (including those without OneDrive access) retain KB-level access. They will see the KB in their list and get a 403 when opening it.

### Issue 2: Adding Users to Groups Has No Permission Hooks

**The problem:** When an admin adds a user to a group via the admin panel, there is no validation of whether the group has access to any knowledge bases with OneDrive files, and whether the new user has OneDrive permissions for those files.

**Root cause** (`backend/open_webui/routers/groups.py:171-195`):

```python
async def add_user_to_group(id, form_data, user=Depends(get_admin_user)):
    form_data.user_ids = Users.get_valid_user_ids(form_data.user_ids)
    group = Groups.add_users_to_group(id, form_data.user_ids)
    return GroupResponse(...)
```

The endpoint validates only that the user IDs exist. There is:
- No lookup of knowledge bases that reference this group in their `access_control`
- No call to `SharingValidator` or any permission provider
- No event emission or callback after the membership change
- No warning to the admin about potential permission conflicts

**Model layer confirms** (`backend/open_webui/models/groups.py:463-499`): `add_users_to_group()` is a pure DB write with no callbacks, signals, or events.

**Impact:** A user added to a group immediately gains access to all KBs shared with that group (via `has_access()` checking group membership at query time). For KBs with OneDrive files, the user will see the KB in their list but get a 403 when trying to open it. The admin receives no warning about this.

### Issue 3: OneDrive Sync Never Writes Group IDs to Access Control

**The problem:** The OneDrive sync worker always writes `group_ids: []` in the KB's access_control. This means OneDrive-permissioned KBs can only have individual users in their access control (set automatically by the sync), while group-based sharing is a completely manual/admin-driven action that is disconnected from OneDrive permission state.

**Root cause** (`backend/open_webui/services/onedrive/sync_worker.py:424-433`):

```python
access_control = {
    "read": {
        "user_ids": permitted_user_ids,
        "group_ids": [],          # Always empty
    },
    "write": {
        "user_ids": [self.user_id],
        "group_ids": [],          # Always empty
    },
}
```

When a re-sync happens, the access_control is rebuilt from scratch using only the OneDrive email-to-user mapping. Any manually added groups would be **overwritten** with empty arrays.

**Impact:**
- Groups can only be added to OneDrive KBs manually (via the sharing UI), not automatically during sync
- If a re-sync occurs, any manually added groups are wiped out
- There's a fundamental disconnect: the sync manages user-level access, while group access is managed separately with no cross-validation

### Issue 4: KB Listing Doesn't Check Source Permissions (Bad UX)

**The problem:** The KB list endpoints (GET `/` and GET `/search`) only check standard `access_control` (group/user membership). They do not run source-level permission checks. A user in a group with KB access will see the KB in their list, but gets a 403 error toast and redirect when they click it.

**Listing path** (`backend/open_webui/routers/knowledge.py:57-87`): Uses `has_permission()` from `utils/db/access_control.py` which only checks the `access_control` JSON column against the user's groups and ID. No call to `check_knowledge_access()` or any permission provider.

**Detail path** (`backend/open_webui/routers/knowledge.py:272-312`): Calls `check_knowledge_access()` which additionally checks source permissions per file.

**Frontend UX flow:**
1. User sees the KB in `/workspace/knowledge` -- no visual indicator of OneDrive content or potential access issues
2. User clicks the KB
3. Backend returns 403 with message "Your access to Onedrive documents has been revoked."
4. Frontend shows a toast error and redirects back to the list
5. KB is still visible in the list; clicking it again repeats the cycle

**No visual indicators:** The KB list page (`src/lib/components/workspace/Knowledge.svelte`) renders no OneDrive badge, warning icon, or "restricted" indicator. The `KnowledgeAccessResponse` model only includes a `write_access` boolean, with no source-restriction metadata.

### Issue 5: Write-Role Groups Not Validated

**The problem:** The frontend `SourceAwareAccessControl.svelte` only extracts `read.group_ids` for validation, missing any groups in `write.group_ids`.

**Root cause** (`src/lib/components/workspace/common/SourceAwareAccessControl.svelte:67-68`):

```typescript
const userIds = newAccessControl?.read?.user_ids ?? [];
const groupIds = newAccessControl?.read?.group_ids ?? [];
```

Only `read` role IDs are extracted. If a group is added with write access, it would appear in `write.group_ids` but not be passed to the validation endpoint.

**Impact:** Groups granted write access are not checked for source permissions at all during the sharing validation flow.

## Suggested Solutions

### Solution 1: Add Group-Aware Sharing Validation

**Frontend changes** (`SourceAwareAccessControl.svelte`):
- Extract both `read` and `write` group_ids for validation
- Enhance the validation result to include group-level information (which groups have members without access)
- In strict mode, either:
  - **Option A:** Remove the entire group from access_control if any member lacks access (simple but restrictive)
  - **Option B:** Show a warning per group listing which members lack access, and let the admin decide (better UX)

**Backend changes** (`validator.py`):
- Return group-level aggregated results alongside user-level results
- Include group name and which members within each group lack access
- New response field: `group_conflicts: [{group_id, group_name, members_without_access: [{user_id, user_email}]}]`

**Frontend changes** (`ShareConfirmationModal.svelte`):
- Add a group-level conflict section showing which groups have members without access
- Provide actionable options per group (remove group, grant access to members, proceed anyway in lenient mode)

### Solution 2: Add Permission Validation When Adding Users to Groups

**Backend changes** (`routers/groups.py`):
- After `Groups.add_users_to_group()`, query all knowledge bases that reference this group in their `access_control.read.group_ids` or `access_control.write.group_ids`
- For each KB with source-restricted files, check if the new users have source access
- Return warnings in the response (non-blocking, since it's an admin action)

**Frontend changes** (`src/lib/components/admin/Users/Groups/Users.svelte`):
- After toggling a user into a group, display any permission conflict warnings
- Show a modal similar to `ShareConfirmationModal` listing KBs where the new user lacks source access
- Provide "Grant access" links to the OneDrive source

**Alternative approach:** Add a pre-check before adding the user:
- New endpoint: `POST /groups/id/{id}/validate-user-addition` that checks source permissions for the candidate users
- Frontend calls this before adding, showing warnings proactively

### Solution 3: Protect Group IDs During OneDrive Re-Sync

**Backend changes** (`sync_worker.py`):
- Before overwriting `access_control`, read the existing value
- Preserve any existing `group_ids` from the current access_control
- Only update `user_ids` based on OneDrive permissions
- Alternatively, merge rather than replace: add new permitted users without removing manually-added groups

```python
existing_ac = knowledge.access_control or {}
access_control = {
    "read": {
        "user_ids": permitted_user_ids,
        "group_ids": existing_ac.get("read", {}).get("group_ids", []),
    },
    "write": {
        "user_ids": [self.user_id],
        "group_ids": existing_ac.get("write", {}).get("group_ids", []),
    },
}
```

### Solution 4: Add Source-Permission Awareness to KB Listing

**Option A (lightweight):** Add metadata to the KB list response:
- Include a `source_restricted: bool` field in `KnowledgeAccessResponse`
- Include `source_accessible: bool` per KB (requires running source checks per KB in the list -- potentially expensive)
- Frontend renders a warning badge for source-restricted KBs

**Option B (performant):** Add a flag without per-user source checks:
- Include `has_source_files: bool` derived from `knowledge.meta.onedrive_sync` presence
- Frontend shows a OneDrive badge/indicator
- When the user clicks, the 403 is expected but at least visually communicated

**Option C (comprehensive):** Filter source-inaccessible KBs from the list entirely:
- After the standard DB query, run `check_knowledge_access()` per KB and exclude those that fail
- This is expensive for large lists but provides the cleanest UX
- Could be cached per user session

### Solution 5: Add Event System for Group Membership Changes

**Backend changes:**
- Create a lightweight event/hook system for group membership changes
- After `add_users_to_group()` or `remove_users_from_group()`, emit an event
- Register handlers that can react to membership changes (e.g., re-validate source permissions, send notifications)

This is a broader architectural change that would also support future features (audit logging, real-time UI updates, etc.).

## Code References

- `backend/open_webui/services/permissions/validator.py:60-166` - `validate_knowledge_share()` with group expansion
- `backend/open_webui/services/permissions/validator.py:314-342` - `_get_kb_users()` with group expansion
- `backend/open_webui/services/permissions/enforcement.py:39-142` - `check_knowledge_access()` (source + standard)
- `backend/open_webui/services/permissions/providers/onedrive.py:29-97` - `check_user_access()` (email-based)
- `backend/open_webui/services/onedrive/sync_worker.py:424-433` - access_control with hardcoded empty group_ids
- `backend/open_webui/routers/groups.py:171-195` - `add_user_to_group()` (no permission checks)
- `backend/open_webui/routers/knowledge.py:57-87` - KB list endpoint (no source checks)
- `backend/open_webui/routers/knowledge.py:272-312` - KB detail endpoint (with source checks)
- `backend/open_webui/utils/access_control.py:124-150` - `has_access()` core function
- `src/lib/components/workspace/common/SourceAwareAccessControl.svelte:67-68` - Only reads `read` role
- `src/lib/components/workspace/common/SourceAwareAccessControl.svelte:109-121` - Filters user_ids but not group_ids
- `src/lib/components/workspace/common/ShareConfirmationModal.svelte:163-194` - User-level only warnings
- `src/lib/components/workspace/Knowledge.svelte:208-284` - No source indicators in KB list
- `src/lib/components/admin/Users/Groups/Users.svelte:218-225` - Simple checkbox toggle, no permission checks

## Architecture Insights

1. **Two-layer permission model mismatch:** The standard access control layer understands groups natively (groups in access_control JSON), but the source permission layer (OneDrive) operates exclusively on individual users (email matching). This fundamental mismatch means group-based operations can bypass source checks.

2. **No reactive permission system:** The system evaluates permissions at query time only. There are no events, hooks, or triggers when group membership changes. This makes it impossible to proactively enforce source permissions when group composition changes.

3. **Sync worker overwrites manual access:** The OneDrive sync worker rebuilds access_control from scratch with only user_ids, wiping any manually-added group_ids. This means group-based sharing of OneDrive KBs is inherently fragile.

4. **List vs detail permission gap:** Listing endpoints use a fast DB-level filter that doesn't check source permissions, while detail endpoints run the full check. This creates a "visible but inaccessible" state.

5. **Strict mode incomplete for groups:** Strict mode was designed to filter individual users, but the filtering mechanism doesn't extend to group removal. The group persists in access_control even when strict mode would exclude some of its members.

## Open Questions

1. Should groups be able to be shared OneDrive KBs at all, or should it be restricted to individual users only (simpler but more restrictive)?
2. When an admin adds a user to a group with OneDrive KB access, should it be a hard block (prevent the add) or a soft warning (allow but notify)?
3. Should the OneDrive sync worker be made group-aware (map permitted emails to groups), or should groups remain a manual-only overlay?
4. For the KB listing UX, is filtering inaccessible KBs from the list preferred over showing them with a visual indicator?
5. Should the re-sync preserve manually-added groups, or should it be a clean rebuild every time?
