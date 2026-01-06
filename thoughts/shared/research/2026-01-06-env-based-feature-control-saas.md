---
date: 2026-01-06T09:22:58Z
researcher: Claude
git_commit: 023831554590c846a11fb3da1916cff03c228bef
branch: main
repository: open-webui
topic: "Environment-based feature control for SaaS multi-tenancy"
tags: [research, codebase, configuration, permissions, saas, admin-control, workspace]
status: complete
last_updated: 2026-01-06
last_updated_by: Claude
last_updated_note: "Added implementation scope and wrapper approach details"
---

# Research: Environment-Based Feature Control for SaaS Multi-Tenancy

**Date**: 2026-01-06T09:22:58Z
**Researcher**: Claude
**Git Commit**: 023831554590c846a11fb3da1916cff03c228bef
**Branch**: main
**Repository**: open-webui

## Research Question

Can you set settings for both Admins and Users through the .env file? For example, what if I do not want the workspace available for users AND admins, but do want the rest of the admin settings?

The goal is to sell this as a SaaS where IT gets admin access to configure features available in their tier, but not things not included in that tier (e.g., the workspace).

## Summary

**Short answer**: You can control user permissions extensively via env vars, but **admins bypass all permission checks by default** and there's no env-based way to hide features from admins without code changes.

| What You Want | Possible via ENV? | How |
|---------------|-------------------|-----|
| Disable workspace for **users** | Yes | `USER_PERMISSIONS_WORKSPACE_*_ACCESS=False` |
| Disable workspace for **admins** | No | Requires code modification |
| Hide admin panel features | No | Requires code modification |
| Restrict admin to "tier" features | No | Not supported out-of-box |

For a SaaS tiered model, you'll need to either:
1. **Extend the codebase** with tier-aware feature flags that affect both admins and users
2. **Use the group system** creatively (give IT "user" role, not "admin")
3. **Fork and customize** the admin/workspace visibility logic

## Detailed Findings

### User Permission System

Open WebUI has a robust permission system for regular users, controllable via env vars:

**Workspace Access** (all default to `False`):
```bash
USER_PERMISSIONS_WORKSPACE_MODELS_ACCESS=False
USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ACCESS=False
USER_PERMISSIONS_WORKSPACE_PROMPTS_ACCESS=False
USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS=False
```

**Feature Toggles** (various defaults):
```bash
USER_PERMISSIONS_FEATURES_API_KEYS=False
USER_PERMISSIONS_FEATURES_WEB_SEARCH=True
USER_PERMISSIONS_FEATURES_IMAGE_GENERATION=True
USER_PERMISSIONS_FEATURES_CODE_INTERPRETER=True
USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS=False
```

**Chat Controls**:
```bash
USER_PERMISSIONS_CHAT_DELETE=True
USER_PERMISSIONS_CHAT_EDIT=True
USER_PERMISSIONS_CHAT_SHARE=True
USER_PERMISSIONS_CHAT_TEMPORARY_ENFORCED=False
```

These are defined in `backend/open_webui/config.py:1247-1468` and assembled into `DEFAULT_USER_PERMISSIONS` at lines 1472-1530.

### The Admin Bypass Problem

Admins bypass permission checks in two ways:

**1. Backend bypass pattern** (`backend/open_webui/routers/*.py`):
```python
if user.role != "admin" and not has_permission(user.id, "workspace.tools", ...):
    raise HTTPException(status_code=401)
```
Admins skip the permission check entirely.

**2. Frontend bypass pattern** (`src/lib/components/layout/Sidebar.svelte:752`):
```svelte
{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models}
    <a href="/workspace">Workspace</a>
{/if}
```
Admins always see the link.

**3. BYPASS_ADMIN_ACCESS_CONTROL** (`config.py:1587-1593`):
```python
BYPASS_ADMIN_ACCESS_CONTROL = os.environ.get(
    "BYPASS_ADMIN_ACCESS_CONTROL", "True"
).lower() == "true"
```

Setting `BYPASS_ADMIN_ACCESS_CONTROL=False` would make admins respect access_control rules on *individual items* (models, knowledge bases, etc.), but they'd **still see the workspace UI** and could create new items.

### What Each Admin-Related Env Var Does

| Variable | Default | What It Controls |
|----------|---------|------------------|
| `BYPASS_ADMIN_ACCESS_CONTROL` | `True` | Admin sees all workspace content (models, prompts, etc.) |
| `ENABLE_ADMIN_CHAT_ACCESS` | `True` | Admin can view other users' chats |
| `ENABLE_ADMIN_EXPORT` | `True` | Admin can export data |
| `ENABLE_ADMIN_WORKSPACE_CONTENT_ACCESS` | `True` | Legacy alias for BYPASS_ADMIN_ACCESS_CONTROL |

None of these hide the workspace or admin panel entirely.

### Frontend Visibility Logic

**Workspace sidebar link** (`src/lib/components/layout/Sidebar.svelte:752-787`):
```svelte
{#if $user?.role === 'admin' ||
     $user?.permissions?.workspace?.models ||
     $user?.permissions?.workspace?.knowledge || ...}
```

**Admin panel link** (`src/lib/components/layout/Sidebar/UserMenu.svelte:245-280`):
```svelte
{#if role === 'admin'}
    <DropdownMenu.Item href="/admin">Admin Panel</DropdownMenu.Item>
{/if}
```

There's no env var that controls these - they're hardcoded to check `role === 'admin'`.

### How Permissions Flow

```
Startup:
  .env → config.py parses USER_PERMISSIONS_* → PersistentConfig
       → Stored in app.state.config.USER_PERMISSIONS
       → Can be overridden via admin panel (saved to DB)

Runtime:
  1. User logs in → GET /api/auths/ returns permissions
  2. Frontend stores in $user.permissions
  3. Components check $user.role and $user.permissions.*
  4. Backend routes check has_permission() for non-admins
```

### Group-Based Permissions Alternative

Users can be assigned to groups with custom permissions (`backend/open_webui/models/groups.py`). Groups use a "most permissive wins" merge strategy.

For a pseudo-admin tier, you could:
1. Keep IT users as role="user"
2. Create a "Tier 1 Admin" group with elevated permissions (but no workspace)
3. Create a "Tier 2 Admin" group with workspace access

This approach means IT wouldn't have access to `/admin` routes though.

## Code References

| File | Lines | Description |
|------|-------|-------------|
| `backend/open_webui/config.py` | 1247-1536 | All USER_PERMISSIONS_* env vars and DEFAULT_USER_PERMISSIONS dict |
| `backend/open_webui/config.py` | 1581-1596 | BYPASS_ADMIN_ACCESS_CONTROL, ENABLE_ADMIN_* vars |
| `backend/open_webui/utils/access_control.py` | 71-105 | `has_permission()` function |
| `backend/open_webui/utils/access_control.py` | 28-68 | `get_permissions()` - combines group permissions |
| `backend/open_webui/routers/users.py` | 160-224 | WorkspacePermissions, UserPermissions Pydantic models |
| `backend/open_webui/routers/users.py` | 227-250 | Admin endpoints to get/set default permissions |
| `src/lib/components/layout/Sidebar.svelte` | 752-787 | Workspace link visibility check |
| `src/lib/components/layout/Sidebar/UserMenu.svelte` | 245-280 | Admin panel link (admin-only) |
| `src/routes/(app)/workspace/+layout.svelte` | 23-43 | Workspace route guard |
| `src/routes/(app)/admin/+layout.svelte` | 15-19 | Admin route guard |

## Recommended Implementation: Tier-Based Feature Flags with Wrapper Approach

### Implementation Scope

| Category | Files | LOC Changes | Merge Risk |
|----------|-------|-------------|------------|
| Backend Config | 1 | ~20 | Low |
| Backend API | 1 | ~10 | Low |
| Frontend Types | 1 | ~10 | Low |
| Frontend Utility | 1 (new) | ~30 | None |
| Frontend Components | 4-6 | ~20 | Low |
| Route Guards | 2 | ~10 | Low |
| **Total** | **~11 files** | **~100 LOC** | **Low** |

### Wrapper Approach (Recommended)

Create a centralized tier utility that encapsulates all tier checks. This provides:
- Single point of change for tier logic
- Easier rebasing when upstream updates
- Cleaner component code
- Testability

#### 1. Backend Config (`backend/open_webui/config.py`)

Add at end of feature flags section (~line 1600):

```python
####################################
# Tier Feature Flags
####################################

TIER_ENABLE_WORKSPACE = os.environ.get("TIER_ENABLE_WORKSPACE", "True").lower() == "true"
TIER_ENABLE_ADMIN_PANEL = os.environ.get("TIER_ENABLE_ADMIN_PANEL", "True").lower() == "true"
TIER_ENABLE_PLAYGROUND = os.environ.get("TIER_ENABLE_PLAYGROUND", "True").lower() == "true"
TIER_ENABLE_CHANNELS = os.environ.get("TIER_ENABLE_CHANNELS", "True").lower() == "true"
TIER_ENABLE_KNOWLEDGE = os.environ.get("TIER_ENABLE_KNOWLEDGE", "True").lower() == "true"
```

#### 2. Backend API (`backend/open_webui/main.py`)

Add to `/api/config` response (~line 1925):

```python
"tier": {
    "enable_workspace": TIER_ENABLE_WORKSPACE,
    "enable_admin_panel": TIER_ENABLE_ADMIN_PANEL,
    "enable_playground": TIER_ENABLE_PLAYGROUND,
    "enable_channels": TIER_ENABLE_CHANNELS,
    "enable_knowledge": TIER_ENABLE_KNOWLEDGE,
},
```

#### 3. Frontend Types (`src/lib/stores/index.ts`)

Extend `Config` type:

```typescript
tier?: {
    enable_workspace: boolean;
    enable_admin_panel: boolean;
    enable_playground: boolean;
    enable_channels: boolean;
    enable_knowledge: boolean;
};
```

#### 4. Frontend Tier Utility (NEW: `src/lib/utils/tier.ts`)

```typescript
import { get } from 'svelte/store';
import { config, user } from '$lib/stores';

export type TierFeature =
    | 'workspace'
    | 'admin_panel'
    | 'playground'
    | 'channels'
    | 'knowledge';

/**
 * Check if a tier feature is enabled globally.
 * This check applies to ALL users including admins.
 */
export function isTierEnabled(feature: TierFeature): boolean {
    const $config = get(config);
    const key = `enable_${feature}` as keyof typeof $config.tier;
    return $config?.tier?.[key] ?? true; // Default to enabled if not set
}

/**
 * Check if user has access to a feature considering both tier AND permissions.
 * Admins get access if tier is enabled, regular users need tier + permission.
 */
export function hasFeatureAccess(
    feature: TierFeature,
    permissionKey?: string
): boolean {
    const $user = get(user);

    // First check: tier must be enabled
    if (!isTierEnabled(feature)) {
        return false;
    }

    // Second check: admin always has access if tier enabled
    if ($user?.role === 'admin') {
        return true;
    }

    // Third check: regular users need specific permission
    if (permissionKey) {
        const keys = permissionKey.split('.');
        let perms: any = $user?.permissions;
        for (const key of keys) {
            perms = perms?.[key];
        }
        return Boolean(perms);
    }

    return false;
}

/**
 * Reactive store-based check for use in Svelte components.
 * Usage: $: canAccessWorkspace = $tierAccess('workspace', 'workspace.models')
 */
export function createTierAccessStore(feature: TierFeature, permissionKey?: string) {
    return {
        subscribe(fn: (value: boolean) => void) {
            // Re-evaluate when config or user changes
            const unsubConfig = config.subscribe(() => fn(hasFeatureAccess(feature, permissionKey)));
            const unsubUser = user.subscribe(() => fn(hasFeatureAccess(feature, permissionKey)));
            return () => { unsubConfig(); unsubUser(); };
        }
    };
}
```

#### 5. Component Updates

**Sidebar.svelte (line 752)** - Before:
```svelte
{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || ...}
```

**After:**
```svelte
<script>
import { isTierEnabled, hasFeatureAccess } from '$lib/utils/tier';
</script>

{#if isTierEnabled('workspace') && ($user?.role === 'admin' || $user?.permissions?.workspace?.models || ...)}
```

**UserMenu.svelte (line 245)** - Before:
```svelte
{#if role === 'admin'}
```

**After:**
```svelte
{#if role === 'admin' && isTierEnabled('admin_panel')}
```

#### 6. Route Guards

**workspace/+layout.svelte:**
```svelte
<script>
import { isTierEnabled } from '$lib/utils/tier';

onMount(async () => {
    // Tier check first - applies to everyone
    if (!isTierEnabled('workspace')) {
        await goto('/');
        return;
    }

    // Then existing permission checks for non-admins
    if ($user?.role !== 'admin') {
        // ... existing permission checks
    }
});
</script>
```

**admin/+layout.svelte:**
```svelte
<script>
import { isTierEnabled } from '$lib/utils/tier';

onMount(async () => {
    if ($user?.role !== 'admin' || !isTierEnabled('admin_panel')) {
        await goto('/');
        return;
    }
});
</script>
```

### Example .env Configurations Per Tier

```bash
# ============================================
# Tier 1: Basic Chat Only
# ============================================
TIER_ENABLE_WORKSPACE=False
TIER_ENABLE_ADMIN_PANEL=False
TIER_ENABLE_PLAYGROUND=False
TIER_ENABLE_CHANNELS=False
TIER_ENABLE_KNOWLEDGE=False

# ============================================
# Tier 2: Standard (Team collaboration)
# ============================================
TIER_ENABLE_WORKSPACE=False
TIER_ENABLE_ADMIN_PANEL=True
TIER_ENABLE_PLAYGROUND=False
TIER_ENABLE_CHANNELS=True
TIER_ENABLE_KNOWLEDGE=False

# ============================================
# Tier 3: Professional (Custom models)
# ============================================
TIER_ENABLE_WORKSPACE=True
TIER_ENABLE_ADMIN_PANEL=True
TIER_ENABLE_PLAYGROUND=True
TIER_ENABLE_CHANNELS=True
TIER_ENABLE_KNOWLEDGE=False

# ============================================
# Tier 4: Enterprise (Full features)
# ============================================
TIER_ENABLE_WORKSPACE=True
TIER_ENABLE_ADMIN_PANEL=True
TIER_ENABLE_PLAYGROUND=True
TIER_ENABLE_CHANNELS=True
TIER_ENABLE_KNOWLEDGE=True
```

### Merge Conflict Mitigation

| Strategy | Benefit |
|----------|---------|
| New utility file (`tier.ts`) | No conflicts - it's a new file |
| Additive config changes | Low conflict risk - adding to end of sections |
| Minimal component changes | Only 1-2 line additions per component |
| Wrapper function pattern | If upstream changes permission checks, wrapper logic adapts |

### Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add ~10 lines at end | Low |
| `backend/open_webui/main.py` | Add ~8 lines to config response | Low |
| `src/lib/stores/index.ts` | Add ~8 lines to Config type | Low |
| `src/lib/utils/tier.ts` | **New file** (~50 lines) | None |
| `src/lib/components/layout/Sidebar.svelte` | Add 1 import + 2 condition changes | Low |
| `src/lib/components/layout/Sidebar/UserMenu.svelte` | Add 1 import + 2 condition changes | Low |
| `src/routes/(app)/workspace/+layout.svelte` | Add 1 import + 4 line check | Low |
| `src/routes/(app)/admin/+layout.svelte` | Add 1 import + 1 condition change | Low |

## Alternative Options (Not Recommended)

### Option A: Role Remapping

Don't give IT users `admin` role. Instead:
- Create a `tenant_admin` or `manager` concept via groups
- Give them elevated permissions via group assignment
- Keep actual `admin` role for your platform operations

**Downside:** IT wouldn't have access to `/admin` routes.

### Option B: Multi-Tenant Architecture

Deploy separate instances per tenant with different env configurations.

**Downside:** Higher infrastructure cost and complexity.

## Open Questions

1. Should tier restrictions be stored in the database (allowing runtime changes) or strictly env-based?
2. How should the admin panel adapt? Should certain tabs be hidden based on tier?
3. Is there a licensing/entitlement system to integrate with for tier validation?
4. For SaaS, how will you handle the `PersistentConfig` system that allows admins to override env vars via the UI?

## Related Files

All environment configuration documentation from Open WebUI:
- https://docs.openwebui.com/getting-started/env-configuration/
