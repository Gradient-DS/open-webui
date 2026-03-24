---
date: 2026-03-24T14:00:00+01:00
researcher: Claude Code
git_commit: 39344352fd3d3d415d955d61f38dab8d08e31df6
branch: fix/test-bugs-daan-260323
repository: Gradient-DS/open-webui
topic: "Individual sharing configurability and OneDrive file sharing restrictions"
tags: [research, sharing, access-control, onedrive, helm, env-vars, access-grants]
status: complete
last_updated: 2026-03-24
last_updated_by: Claude Code
---

# Research: Individual Sharing Configurability and OneDrive File Sharing Restrictions

**Date**: 2026-03-24T14:00:00+01:00
**Researcher**: Claude Code
**Git Commit**: 39344352fd3d3d415d955d61f38dab8d08e31df6
**Branch**: fix/test-bugs-daan-260323
**Repository**: Gradient-DS/open-webui

## Research Question

1. The new upstream version supports sharing to individuals — how to make this configurable in env/helm chart per deployment?
2. Can OneDrive files still not be shared with other people in the tenant through the Open WebUI system?

## Summary

**Sharing to individuals** is fully supported in upstream via the `access_grants` system. The backend env vars exist (`USER_PERMISSIONS_WORKSPACE_*_ALLOW_SHARING`, `USER_PERMISSIONS_ACCESS_GRANTS_ALLOW_USERS`) but the **helm chart is missing the non-public sharing vars** — it only exposes `_ALLOW_PUBLIC_SHARING` variants. Adding the `_ALLOW_SHARING` and `_ALLOW_USERS` vars to values.yaml + configmap is needed.

**OneDrive files are well-protected** from sharing through 4 layers of guards: creation forces empty grants, general update preserves grants, frontend hides sharing UI, and OneDrive sync endpoints enforce owner-only access. There is one **minor gap**: the dedicated `/access/update` endpoint doesn't check KB type, but the frontend never calls it for non-local KBs.

## Detailed Findings

### 1. Individual Sharing Architecture (Access Grants System)

The upstream `access_grants` system supports sharing workspace resources (models, knowledge, prompts, tools, skills, notes) to specific users and groups with read/write permissions.

**Three visibility states:**
- **Public**: wildcard grant (`principal_id="*"`) — visible to all users
- **Private**: no grants — visible only to owner
- **Shared with individuals/groups**: explicit user/group grants

**Frontend controls** (`AccessControl.svelte`):
- `share` prop → controls whether sharing UI appears at all (maps to `sharing.{resource}` permission)
- `sharePublic` prop → controls "Public" option (maps to `sharing.public_{resource}` permission)
- `shareUsers` prop → controls adding individual users (maps to `access_grants.allow_users` permission)

### 2. Env Vars That Control Individual Sharing

All default to `False` for non-admin users:

| Env Var | Purpose | In Helm? |
|---------|---------|----------|
| `USER_PERMISSIONS_WORKSPACE_MODELS_ALLOW_SHARING` | Share models to groups/individuals | **NO** |
| `USER_PERMISSIONS_WORKSPACE_MODELS_ALLOW_PUBLIC_SHARING` | Make models public | YES |
| `USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ALLOW_SHARING` | Share KBs to groups/individuals | **NO** |
| `USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ALLOW_PUBLIC_SHARING` | Make KBs public | YES |
| `USER_PERMISSIONS_WORKSPACE_PROMPTS_ALLOW_SHARING` | Share prompts to groups/individuals | **NO** |
| `USER_PERMISSIONS_WORKSPACE_PROMPTS_ALLOW_PUBLIC_SHARING` | Make prompts public | YES |
| `USER_PERMISSIONS_WORKSPACE_TOOLS_ALLOW_SHARING` | Share tools to groups/individuals | **NO** |
| `USER_PERMISSIONS_WORKSPACE_TOOLS_ALLOW_PUBLIC_SHARING` | Make tools public | YES |
| `USER_PERMISSIONS_WORKSPACE_SKILLS_ALLOW_SHARING` | Share skills to groups/individuals | **NO** |
| `USER_PERMISSIONS_WORKSPACE_SKILLS_ALLOW_PUBLIC_SHARING` | Make skills public | **NO** |
| `USER_PERMISSIONS_NOTES_ALLOW_SHARING` | Share notes to groups/individuals | **NO** |
| `USER_PERMISSIONS_NOTES_ALLOW_PUBLIC_SHARING` | Make notes public | YES |
| `USER_PERMISSIONS_ACCESS_GRANTS_ALLOW_USERS` | Allow sharing to individual users (not just groups) | **NO** (defaults True in config.py) |

**Key distinction**: `_ALLOW_SHARING` enables sharing to groups and individuals. `_ALLOW_PUBLIC_SHARING` enables making things visible to all users. `ACCESS_GRANTS_ALLOW_USERS` controls whether the "add specific user" option appears (as opposed to groups only).

### 3. What Needs Adding to Helm Chart

**values.yaml** — add under the "Workspace Public Sharing" section:

```yaml
# Workspace Sharing (allow users to share with specific groups/individuals)
userPermissionsWorkspaceModelsAllowSharing: "false"
userPermissionsWorkspaceKnowledgeAllowSharing: "false"
userPermissionsWorkspacePromptsAllowSharing: "false"
userPermissionsWorkspaceToolsAllowSharing: "false"
userPermissionsWorkspaceSkillsAllowSharing: "false"
userPermissionsWorkspaceSkillsAllowPublicSharing: "false"  # Also missing
userPermissionsNotesAllowSharing: "false"
userPermissionsAccessGrantsAllowUsers: "true"  # Allow individual user sharing (vs groups only)
```

**configmap.yaml** — add corresponding mappings:

```yaml
USER_PERMISSIONS_WORKSPACE_MODELS_ALLOW_SHARING: {{ .Values.openWebui.config.userPermissionsWorkspaceModelsAllowSharing | quote }}
USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ALLOW_SHARING: {{ .Values.openWebui.config.userPermissionsWorkspaceKnowledgeAllowSharing | quote }}
USER_PERMISSIONS_WORKSPACE_PROMPTS_ALLOW_SHARING: {{ .Values.openWebui.config.userPermissionsWorkspacePromptsAllowSharing | quote }}
USER_PERMISSIONS_WORKSPACE_TOOLS_ALLOW_SHARING: {{ .Values.openWebui.config.userPermissionsWorkspaceToolsAllowSharing | quote }}
USER_PERMISSIONS_WORKSPACE_SKILLS_ALLOW_SHARING: {{ .Values.openWebui.config.userPermissionsWorkspaceSkillsAllowSharing | quote }}
USER_PERMISSIONS_WORKSPACE_SKILLS_ALLOW_PUBLIC_SHARING: {{ .Values.openWebui.config.userPermissionsWorkspaceSkillsAllowPublicSharing | quote }}
USER_PERMISSIONS_NOTES_ALLOW_SHARING: {{ .Values.openWebui.config.userPermissionsNotesAllowSharing | quote }}
USER_PERMISSIONS_ACCESS_GRANTS_ALLOW_USERS: {{ .Values.openWebui.config.userPermissionsAccessGrantsAllowUsers | quote }}
```

### 4. OneDrive File Sharing Restrictions

**OneDrive KBs are protected from sharing at 4 layers:**

| Layer | Mechanism | Location |
|-------|-----------|----------|
| Creation (backend) | Forces `access_grants = []` for `type != "local"` | `knowledge.py:294-296` |
| General update (backend) | Preserves existing grants for `type != "local"` | `knowledge.py:518-522` |
| Creation (frontend) | Only sends access grants for local type | `CreateKnowledgeBase.svelte:38` |
| Detail page (frontend) | Only renders AccessControlModal for local type | `KnowledgeBase.svelte:1379` |
| OneDrive sync endpoints | Owner-only via `user_id` equality check | `onedrive_sync.py:80,182,217,307,394,509,539` |

**Minor gap**: The dedicated `POST /api/v1/knowledge/{id}/access/update` endpoint (`knowledge.py:561-605`) does NOT check `knowledge.type`. It accepts any access grants and writes them directly. However, the frontend never calls this endpoint for non-local KBs because the AccessControlModal is not rendered.

**Risk assessment**: Low. Exploiting this would require a direct API call (bypassing the frontend), and the caller would need write access to the KB (owner or admin only, since OneDrive KBs have no grants). An admin could theoretically share an OneDrive KB via direct API call, but admins already have full access. A regular user who owns an OneDrive KB could share it via direct API call — this is the only real gap.

### 5. Chat Sharing (Separate System)

Chat sharing is **link-based only** — it creates a snapshot accessible to anyone with the URL. There is no mechanism to share a chat with a specific user. This is unrelated to the access grants system and controlled by `USER_PERMISSIONS_CHAT_SHARE` (already in helm).

## Code References

- `backend/open_webui/config.py:1449-1523` — All sharing permission env var definitions
- `backend/open_webui/config.py:1670-1700` — DEFAULT_USER_PERMISSIONS sharing section
- `backend/open_webui/models/access_grants.py:21-45` — Access grant table schema
- `backend/open_webui/routers/knowledge.py:294-296` — OneDrive creation guard
- `backend/open_webui/routers/knowledge.py:518-522` — OneDrive update guard
- `backend/open_webui/routers/knowledge.py:561-605` — Access update endpoint (missing type guard)
- `backend/open_webui/utils/access_control/__init__.py:231-281` — Grant filtering by permissions
- `src/lib/components/workspace/common/AccessControl.svelte` — Sharing UI component
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1379` — OneDrive modal guard
- `helm/open-webui-tenant/values.yaml:241-276` — Current sharing config (missing non-public vars)
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml:154-159` — Current configmap mappings

## Architecture Insights

The access grants system has two distinct permission dimensions:
1. **Resource-level sharing** (`_ALLOW_SHARING`) — can users share this resource type at all?
2. **Public vs private sharing** (`_ALLOW_PUBLIC_SHARING`) — can users make resources visible to everyone?
3. **Principal type** (`ACCESS_GRANTS_ALLOW_USERS`) — can users share with individuals, or only groups?

These are independent toggles. For a typical enterprise deployment wanting individual sharing but not public sharing:
- Set `_ALLOW_SHARING` = true for desired resource types
- Keep `_ALLOW_PUBLIC_SHARING` = false
- Set `ACCESS_GRANTS_ALLOW_USERS` = true

## Open Questions

1. **Should we add a type guard to the `/access/update` endpoint?** The risk is low but it would close the gap for direct API callers. A one-line check would suffice.
2. **Should sharing of OneDrive KBs be allowed in the future?** If OneDrive documents are synced into the vector store, sharing the KB means sharing access to query those documents — not sharing the OneDrive files themselves. The distinction matters for compliance.
3. **Per-group overrides**: The current system supports per-group permission overrides (most-permissive-wins). Should different groups have different sharing permissions? This works without helm changes — it's configured in the admin UI.
