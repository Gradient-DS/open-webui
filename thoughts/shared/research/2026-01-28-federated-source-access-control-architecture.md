---
date: 2026-01-28T14:30:00+01:00
researcher: Claude
git_commit: 1c81fe4586508a4ef68dd281f1ca7358af952aad
branch: feat/data-control
repository: open-webui
topic: "Federated Source Access Control Architecture for Knowledge Bases"
tags: [research, codebase, access-control, onedrive, sharepoint, knowledge-base, sharing, rbac, architecture]
status: complete
last_updated: 2026-01-28
last_updated_by: Claude
last_updated_note: "Added STRICT_SOURCE_PERMISSIONS mode, upstream revocation handling, and owner control model based on stakeholder feedback"
---

# Research: Federated Source Access Control Architecture for Knowledge Bases

**Date**: 2026-01-28T14:30:00+01:00
**Researcher**: Claude
**Git Commit**: 1c81fe4586508a4ef68dd281f1ca7358af952aad
**Branch**: feat/data-control
**Repository**: open-webui

## Research Question

How should we architect a scalable solution for respecting external source access controls (OneDrive, SharePoint, Slack, Outlook, Discord, etc.) when sharing knowledge bases and workspace models in Open WebUI? The goal is to:

1. Prevent users from sharing knowledge bases with people who don't have access to the underlying source documents
2. Provide actionable feedback (e.g., OneDrive modal/link) when sharing is blocked
3. Allow partial sharing to users who have access while excluding those who don't
4. Design an extensible architecture that scales as new integrations are added

## Summary

**The recommended architecture is a Permission Provider abstraction layer** with a configurable **STRICT_SOURCE_PERMISSIONS** mode that:

1. **Defines a common interface** for checking source-level permissions across any integration
2. **Validates sharing operations at creation time** with explicit user confirmation
3. **Enforces real-time access revocation** when source permissions change
4. **Keeps owners in control** - source access enables sharing, but owners grant KB access
5. **Provides actionable feedback** with links to grant source access

This approach aligns with industry best practices from Microsoft 365 Copilot, Glean, and other enterprise AI tools that implement **permission-aware indexing** with real-time validation.

### Key Design Principles

| Principle | Description |
|-----------|-------------|
| **Source Permissions Are Authoritative** | Never override source access controls - not even for admins |
| **Owner Remains In Control** | Source access is a prerequisite; KB owner decides who gets KB access |
| **Real-Time Enforcement** | Revoked source access = immediate KB/model inaccessibility |
| **Validate at Share Time** | Check permissions when sharing is configured with explicit confirmation |
| **Provide Actionable Feedback** | Tell users exactly which documents are inaccessible and how to grant access |
| **Design for Extension** | Abstract permission checking so new sources plug in easily |

### STRICT_SOURCE_PERMISSIONS Mode

| Mode | Sharing Behavior | Access Enforcement |
|------|------------------|-------------------|
| **Strict** (`true`) | Only users with source access receive KB access. Others excluded but can be added later when they gain source access. | Users lose KB access immediately when source access is revoked. |
| **Lenient** (`false`) | Warning shown but owner can share to anyone. Owner takes responsibility for access decisions. | Source access still checked at retrieval time - users without source access see limited content. |

**Key difference**: Strict mode automatically filters who gets KB access based on source permissions. Lenient mode trusts the owner's judgment but shows warnings.

---

## Current State Analysis

### Open WebUI Access Control Architecture

**Three-tier system** (`backend/open_webui/utils/access_control.py:124-150`):

| State | `access_control` Value | Who Can Access |
|-------|------------------------|----------------|
| **Public** | `None` | All authenticated users (read), owner (write) |
| **Private** | `{}` | Owner only |
| **Group/User** | `{"read": {...}, "write": {...}}` | Specified groups/users |

**Enforcement points:**
- API level: `has_access()` utility in routers
- Database level: `has_permission()` SQL filters in `utils/db/access_control.py`
- Frontend: `write_access` flag controls edit buttons

### OneDrive Permission Sync (Existing Implementation)

From `backend/open_webui/services/onedrive/sync_worker.py:170-259`:

```python
# Current: Maps OneDrive folder permissions to Open WebUI access_control
access_control = {
    "read": {
        "user_ids": permitted_user_ids,  # From OneDrive email matching
        "group_ids": [],
    },
    "write": {
        "user_ids": [self.user_id],  # Only owner
        "group_ids": [],
    },
}
```

**Current gap**: When a knowledge base is made **public** or shared with additional users, OneDrive permission mapping is overwritten/bypassed. Users can see files in the UI but retrieval correctly filters (separate permission check).

### Existing Research: OneDrive File Permission Filtering

From `thoughts/shared/research/2026-01-18-onedrive-file-permission-filtering-ui.md`:

Proposed storing OneDrive permitted users in `knowledge.meta.onedrive_sync.permitted_user_ids` and filtering file visibility based on OneDrive access. This addresses **display filtering** but not **sharing validation**.

### Knowledge Base ↔ Model Relationship

Models store knowledge references in `meta.knowledge` array (`models.py:141-148`):
```python
# No validation when attaching knowledge bases
# Knowledge array stored as-is without checking user access
```

**Gap**: No validation that the model user/group has access to attached knowledge bases' source documents.

---

## Industry Best Practices

### Microsoft 365 Copilot Approach

> "Copilot only surfaces organizational data to which individual users have at least view permissions. The permissions model within your Microsoft 365 tenant ensures data won't unintentionally leak between users."

**Key patterns:**
- Semantic Index respects SharePoint/OneDrive permissions
- Sensitivity labels require EXTRACT/VIEW usage rights
- Restricted Content Discovery flags prevent unauthorized finding

### Glean's Permission-Aware Framework

> "Each datasource's permissions framework is unique. Glean built a framework compatible with all datasources that understands different permission architectures."

**Key patterns:**
- Real-time permission sync when source permissions change
- Unified permission storage model for scalability
- Indexed approach (not federated) for performance

### Pre-Filter vs Post-Filter Pattern

| Pattern | Flow | Best For |
|---------|------|----------|
| **Pre-Filter** | Authorization → Retrieval → LLM | Large corpus, share validation |
| **Post-Filter** | Retrieval → Authorization → LLM | High authorized hit-rate |

**Recommendation**: Use **pre-filter** for sharing validation (check before allowing share).

---

## Design Decisions (Confirmed)

### 1. STRICT_SOURCE_PERMISSIONS Configuration

```python
# backend/open_webui/config.py
STRICT_SOURCE_PERMISSIONS = PersistentConfig(
    "STRICT_SOURCE_PERMISSIONS",
    "permissions.strict_source_permissions",
    os.environ.get("STRICT_SOURCE_PERMISSIONS", "true").lower() == "true",
)
```

| Mode | Public KB with Source Files | Sharing Behavior | Access Enforcement |
|------|----------------------------|------------------|-------------------|
| **Strict** | Blocked (must use group sharing) | Only users with source access receive KB access; others excluded | Lose access immediately when source access revoked |
| **Lenient** | Allowed with warning | Warning shown, owner shares to whoever they want | Source checked at retrieval; limited content for users without source access |

**Default**: Strict mode (`true`) - aligns with enterprise security requirements.

### 2. Owner Control Model

**Principle**: Source access is a **prerequisite**, but the KB owner **grants** KB access.

```
┌─────────────────────────────────────────────────────────────────┐
│  Two-Layer Permission Model                                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 1: Source Permission (OneDrive, SharePoint, etc.)         │
│  ─────────────────────────────────────────────────────────       │
│  • Managed in source system (not Open WebUI)                     │
│  • Synced/validated by Permission Providers                      │
│  • Acts as a GATE - blocks access if not present                 │
│                                                                  │
│                           ↓                                      │
│                    [Has Source Access?]                          │
│                     /            \                               │
│                   No              Yes                            │
│                   ↓                ↓                              │
│              BLOCKED         Layer 2: KB Permission              │
│                              ────────────────────                │
│                              • Managed by KB owner               │
│                              • Standard access_control           │
│                              • Owner decides who to share with   │
│                                                                  │
│  Result: User needs BOTH source access AND KB access             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Key behaviors**:
- If a user gains source access later, they still need KB owner to grant KB access
- KB owner can see who has source access and grant/revoke KB access accordingly
- Source access revocation immediately blocks KB access (no owner action needed)

### 3. Upstream Revocation Handling

When source permissions are revoked upstream (e.g., OneDrive access removed):

```python
# Real-time enforcement at retrieval time
async def check_knowledge_access(user_id: str, knowledge_id: str) -> AccessResult:
    """
    Called on every KB/retrieval access, not just at share time.
    """
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)

    # Standard Open WebUI access check
    if not has_access(user_id, "read", knowledge.access_control):
        return AccessResult(allowed=False, reason="no_kb_access")

    # Source permission check (real-time)
    files = Knowledges.get_files_by_id(knowledge_id)
    source_files = [f for f in files if f.meta.get("source") != "local"]

    if source_files:
        validator = SharingValidator()
        result = await validator.validate_user_source_access(user_id, source_files)

        if not result.has_access:
            return AccessResult(
                allowed=False,
                reason="source_access_revoked",
                message="Your access to the source documents has been revoked.",
                inaccessible_sources=result.inaccessible_sources,
            )

    return AccessResult(allowed=True)
```

**User-facing messages**:

| Scenario | Message |
|----------|---------|
| KB access but no source access | "This knowledge base contains documents you no longer have access to. Contact the document owner to restore access." |
| Model uses inaccessible KB | "This model uses a knowledge base with documents you can't access. The knowledge base will not be used for your queries." |
| Partial access (loose mode) | "Some documents in this knowledge base are not accessible to you. Results may be limited." |

### 4. Access Tab UI (Potential Grants)

The KB Access Control tab shows not just current access, but also:

```svelte
<!-- AccessControl.svelte - Access Tab Enhancement -->

<div class="access-management">
    <!-- Current Access Section -->
    <section class="current-access">
        <h3>Current Access</h3>
        <UserList users={usersWithAccess} />
    </section>

    <!-- Potential Access Section (NEW) -->
    {#if sourceRestrictedKB}
        <section class="potential-access mt-4">
            <h3>Pending Source Access</h3>
            <p class="text-sm text-gray-500">
                These users have KB access but are missing source document permissions.
                Grant them source access to enable full KB access.
            </p>

            {#each usersWithoutSourceAccess as user}
                <div class="flex items-center justify-between py-2">
                    <div>
                        <span class="font-medium">{user.name}</span>
                        <span class="text-sm text-gray-500">{user.email}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <span class="text-xs text-yellow-600">
                            Missing: {user.missingSourceCount} {user.sourceType} files
                        </span>
                        <a
                            href={user.grantAccessUrl}
                            target="_blank"
                            class="btn btn-sm btn-outline"
                        >
                            Grant {user.sourceType} access
                        </a>
                    </div>
                </div>
            {/each}
        </section>

        <!-- Users With Full Access Section -->
        <section class="full-access mt-4">
            <h3>Ready to Add</h3>
            <p class="text-sm text-gray-500">
                These users have source access but haven't been granted KB access yet.
            </p>

            {#each usersReadyToAdd as user}
                <div class="flex items-center justify-between py-2">
                    <div>
                        <span class="font-medium">{user.name}</span>
                        <span class="text-sm text-gray-500">{user.email}</span>
                    </div>
                    <button
                        class="btn btn-sm btn-primary"
                        on:click={() => grantKBAccess(user.id)}
                    >
                        Grant KB access
                    </button>
                </div>
            {/each}
        </section>
    {/if}
</div>
```

### 5. Confirmation Flow (Explicit)

When sharing a KB with users who don't have full source access:

**STRICT MODE:**
```
┌─────────────────────────────────────────────────────────────────┐
│  Confirm Sharing                                         [X]     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  You're sharing "Company Policies" with Marketing Team           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ✓ Will receive access (38 users)                        │    │
│  │   Have OneDrive permissions for all source documents    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ✗ Will NOT receive access (5 users)                     │    │
│  │   Missing OneDrive permissions - can be added later     │    │
│  │                                                          │    │
│  │   sarah@company.com                                      │    │
│  │   └─ Missing: 3 OneDrive files    [Grant access ↗]       │    │
│  │                                                          │    │
│  │   mike@company.com                                       │    │
│  │   └─ Missing: 3 OneDrive files    [Grant access ↗]       │    │
│  │                                                          │    │
│  │   [Show 3 more...]                                       │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ─────────────────────────────────────────────────────────────  │
│                                                                  │
│  Sharing to 38 users with source access.                         │
│  5 users excluded - you can reshare once they have access.       │
│                                                                  │
│  Note: Users will lose access if their source permissions are    │
│  revoked.                                                        │
│                                                                  │
│  [Cancel]                          [Share to 38 users]           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**LENIENT MODE:**
```
┌─────────────────────────────────────────────────────────────────┐
│  Confirm Sharing                                         [X]     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  You're sharing "Company Policies" with Marketing Team           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ✓ Full source access (38 users)                         │    │
│  │   Have OneDrive permissions for all source documents    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ⚠ Missing source access (5 users)                       │    │
│  │   Will receive KB access but may not see all content    │    │
│  │                                                          │    │
│  │   sarah@company.com                                      │    │
│  │   └─ Missing: 3 OneDrive files    [Grant access ↗]       │    │
│  │                                                          │    │
│  │   mike@company.com                                       │    │
│  │   └─ Missing: 3 OneDrive files    [Grant access ↗]       │    │
│  │                                                          │    │
│  │   [Show 3 more...]                                       │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ─────────────────────────────────────────────────────────────  │
│                                                                  │
│  ⚠ Warning: 5 users don't have access to all source documents.  │
│  They will be able to see the KB but not all OneDrive files.     │
│                                                                  │
│  [Cancel]                              [Share to all 43 users]   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 6. Model Access with KB Source Restrictions

When a model uses a KB with source-restricted files:

```python
# At model usage time (chat/retrieval)
async def get_model_knowledge_for_user(
    model_id: str,
    user_id: str
) -> List[Knowledge]:
    """
    Returns only knowledge bases the user can actually access.
    """
    model = Models.get_model_by_id(model_id)
    knowledge_ids = model.meta.get("knowledge", [])

    accessible_knowledge = []
    inaccessible_reasons = []

    for kb_ref in knowledge_ids:
        kb_id = kb_ref.get("collection_name") or kb_ref.get("id")
        access_result = await check_knowledge_access(user_id, kb_id)

        if access_result.allowed:
            accessible_knowledge.append(kb_id)
        else:
            inaccessible_reasons.append({
                "knowledge_id": kb_id,
                "reason": access_result.reason,
                "message": access_result.message,
            })

    # Log/notify about inaccessible KBs
    if inaccessible_reasons:
        log.info(f"User {user_id} has partial model access: "
                 f"{len(accessible_knowledge)} accessible, "
                 f"{len(inaccessible_reasons)} restricted")

    return accessible_knowledge, inaccessible_reasons
```

**UI feedback when using a model with restricted KBs**:

```svelte
{#if modelAccessWarnings.length > 0}
    <div class="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
        <div class="flex items-center gap-2 text-yellow-800 dark:text-yellow-200">
            <WarningIcon class="size-4" />
            <span class="text-sm">
                {$i18n.t('Some knowledge bases attached to this model are not accessible to you.')}
            </span>
        </div>
        <details class="mt-2">
            <summary class="text-sm cursor-pointer">Details</summary>
            <ul class="mt-2 text-sm">
                {#each modelAccessWarnings as warning}
                    <li>{warning.message}</li>
                {/each}
            </ul>
        </details>
    </div>
{/if}
```

---

## Proposed Architecture

### 1. Permission Provider Interface

Create an abstraction for source-level permission checking:

```python
# backend/open_webui/services/permissions/provider.py

from abc import ABC, abstractmethod
from typing import List, Optional, Set
from pydantic import BaseModel

class PermissionCheckResult(BaseModel):
    has_access: bool
    accessible_items: List[str] = []      # IDs of accessible items
    inaccessible_items: List[str] = []    # IDs of inaccessible items
    grant_access_url: Optional[str] = None # URL to grant access
    message: Optional[str] = None          # User-friendly explanation

class PermissionProvider(ABC):
    """Abstract interface for source permission checking."""

    source_type: str  # e.g., "onedrive", "sharepoint", "slack"

    @abstractmethod
    async def check_user_access(
        self,
        user_id: str,
        resource_ids: List[str],
        permission_type: str = "read",
        use_cache: bool = True,
    ) -> PermissionCheckResult:
        """
        Check if user has access to specific resources in the source system.

        Args:
            user_id: Open WebUI user ID
            resource_ids: List of file/resource IDs to check
            permission_type: "read" or "write"
            use_cache: If False, always check live permissions (for real-time enforcement)
        """
        pass

    @abstractmethod
    async def check_bulk_access(
        self,
        user_ids: List[str],
        resource_ids: List[str],
        permission_type: str = "read",
    ) -> dict[str, PermissionCheckResult]:
        """Check access for multiple users at once (for sharing validation)."""
        pass

    @abstractmethod
    async def get_permitted_users(
        self,
        resource_ids: List[str],
        permission_type: str = "read",
    ) -> Set[str]:
        """Get all Open WebUI user IDs with access to given resources."""
        pass

    @abstractmethod
    async def get_users_ready_for_access(
        self,
        resource_ids: List[str],
    ) -> List[UserAccessStatus]:
        """
        Get users who have source access but haven't been granted KB access.
        Used for the "Ready to Add" section in the Access tab.
        """
        pass

    @abstractmethod
    def get_grant_access_url(
        self,
        resource_id: str,
        target_user_email: Optional[str] = None,
    ) -> Optional[str]:
        """Get URL for granting access to a resource (e.g., OneDrive sharing link)."""
        pass

    @abstractmethod
    async def sync_permissions(
        self,
        resource_ids: List[str],
    ) -> PermissionSyncResult:
        """
        Sync permissions from source system to local cache.
        Called periodically and on-demand.
        """
        pass


class UserAccessStatus(BaseModel):
    """Status of a user's access to source resources."""
    user_id: str
    user_name: str
    user_email: str
    has_source_access: bool
    has_kb_access: bool
    missing_resources: List[str] = []
    grant_access_url: Optional[str] = None


class PermissionSyncResult(BaseModel):
    """Result of a permission sync operation."""
    synced_at: int
    resources_checked: int
    permissions_changed: int
    users_gained_access: List[str] = []
    users_lost_access: List[str] = []
```

### 2. Permission Provider Registry

```python
# backend/open_webui/services/permissions/registry.py

class PermissionProviderRegistry:
    """Central registry for permission providers."""

    _providers: dict[str, PermissionProvider] = {}

    @classmethod
    def register(cls, provider: PermissionProvider):
        cls._providers[provider.source_type] = provider

    @classmethod
    def get_provider(cls, source_type: str) -> Optional[PermissionProvider]:
        return cls._providers.get(source_type)

    @classmethod
    def get_all_providers(cls) -> List[PermissionProvider]:
        return list(cls._providers.values())
```

### 3. OneDrive Permission Provider Implementation

```python
# backend/open_webui/services/permissions/providers/onedrive.py

class OneDrivePermissionProvider(PermissionProvider):
    source_type = "onedrive"

    async def check_user_access(
        self,
        user_id: str,
        resource_ids: List[str],
        permission_type: str = "read",
    ) -> PermissionCheckResult:
        # Get user's email
        user = Users.get_user_by_id(user_id)
        if not user:
            return PermissionCheckResult(has_access=False, message="User not found")

        accessible = []
        inaccessible = []

        for resource_id in resource_ids:
            # Check if resource is OneDrive file
            file = Files.get_file_by_id(resource_id)
            if not file or file.meta.get("source") != "onedrive":
                accessible.append(resource_id)  # Non-OneDrive files pass through
                continue

            # Get OneDrive permissions for this file
            permitted_emails = await self._get_onedrive_permissions(
                file.meta.get("onedrive_drive_id"),
                file.meta.get("onedrive_item_id"),
            )

            if user.email.lower() in [e.lower() for e in permitted_emails]:
                accessible.append(resource_id)
            else:
                inaccessible.append(resource_id)

        return PermissionCheckResult(
            has_access=len(inaccessible) == 0,
            accessible_items=accessible,
            inaccessible_items=inaccessible,
            grant_access_url=self._get_sharing_url(inaccessible[0]) if inaccessible else None,
            message=f"{len(inaccessible)} OneDrive files are not accessible" if inaccessible else None,
        )

    def get_grant_access_url(self, resource_id: str, target_user_email: Optional[str] = None) -> Optional[str]:
        file = Files.get_file_by_id(resource_id)
        if not file or file.meta.get("source") != "onedrive":
            return None

        drive_id = file.meta.get("onedrive_drive_id")
        item_id = file.meta.get("onedrive_item_id")

        # Return OneDrive web sharing URL
        return f"https://onedrive.live.com/manage/permissions?id={item_id}"
```

### 4. Sharing Validation Service

```python
# backend/open_webui/services/sharing/validator.py

class SharingValidationResult(BaseModel):
    can_share: bool
    can_share_to_users: List[str] = []     # User IDs that can be shared with
    cannot_share_to_users: List[str] = []   # User IDs that cannot be shared with
    blocking_resources: dict[str, List[str]] = {}  # user_id -> list of inaccessible resource IDs
    recommendations: List[dict] = []         # Actions to enable sharing

class SharingValidator:
    """Validates sharing operations against source permissions."""

    async def validate_knowledge_share(
        self,
        knowledge_id: str,
        target_user_ids: List[str],
        target_group_ids: List[str] = [],
    ) -> SharingValidationResult:
        """
        Validate that all target users have access to source documents.
        Returns detailed results for UI feedback.
        """
        knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
        files = Knowledges.get_files_by_id(knowledge_id)

        # Expand groups to user IDs
        all_user_ids = set(target_user_ids)
        for group_id in target_group_ids:
            group = Groups.get_group_by_id(group_id)
            if group:
                all_user_ids.update(group.user_ids)

        # Group files by source
        files_by_source: dict[str, List[str]] = {}
        for file in files:
            source = file.meta.get("source", "local")
            if source not in files_by_source:
                files_by_source[source] = []
            files_by_source[source].append(file.id)

        # Check each source's permissions
        can_share_to = set(all_user_ids)
        cannot_share_to = set()
        blocking_resources = {}
        recommendations = []

        for source, file_ids in files_by_source.items():
            provider = PermissionProviderRegistry.get_provider(source)
            if not provider:
                continue  # No provider = no restrictions (local files)

            # Bulk check all users against this source's files
            results = await provider.check_bulk_access(
                list(all_user_ids), file_ids, "read"
            )

            for user_id, result in results.items():
                if not result.has_access:
                    can_share_to.discard(user_id)
                    cannot_share_to.add(user_id)
                    blocking_resources[user_id] = result.inaccessible_items

                    # Add recommendation for first inaccessible item
                    if result.grant_access_url and user_id not in [r["user_id"] for r in recommendations]:
                        user = Users.get_user_by_id(user_id)
                        recommendations.append({
                            "user_id": user_id,
                            "user_name": user.name if user else "Unknown",
                            "source": source,
                            "grant_access_url": result.grant_access_url,
                            "inaccessible_count": len(result.inaccessible_items),
                        })

        return SharingValidationResult(
            can_share=len(cannot_share_to) == 0,
            can_share_to_users=list(can_share_to),
            cannot_share_to_users=list(cannot_share_to),
            blocking_resources=blocking_resources,
            recommendations=recommendations,
        )
```

### 5. API Endpoint for Sharing Validation

```python
# backend/open_webui/routers/knowledge.py (additions)

class ShareValidationRequest(BaseModel):
    user_ids: List[str] = []
    group_ids: List[str] = []

class ShareValidationResponse(BaseModel):
    can_share: bool
    can_share_to_users: List[str]
    cannot_share_to_users: List[str]
    blocking_resources: dict[str, List[str]]
    recommendations: List[dict]

@router.post("/{id}/validate-share", response_model=ShareValidationResponse)
async def validate_knowledge_share(
    id: str,
    request: ShareValidationRequest,
    user=Depends(get_verified_user),
):
    """
    Validate sharing a knowledge base with specified users/groups.
    Returns which users can/cannot be shared with based on source permissions.
    """
    knowledge = Knowledges.get_knowledge_by_id(id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Check user has write access
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(status_code=403, detail="Access prohibited")

    validator = SharingValidator()
    result = await validator.validate_knowledge_share(
        id, request.user_ids, request.group_ids
    )

    return ShareValidationResponse(**result.model_dump())
```

### 6. Frontend Integration

```svelte
<!-- src/lib/components/workspace/common/AccessControl.svelte additions -->

<script>
    import { validateKnowledgeShare } from '$lib/apis/knowledge';

    let validationResult = null;
    let validating = false;

    async function onAccessControlChange(newAccessControl) {
        if (!knowledgeId || !newAccessControl) return;

        validating = true;
        try {
            const userIds = newAccessControl?.read?.user_ids || [];
            const groupIds = newAccessControl?.read?.group_ids || [];

            validationResult = await validateKnowledgeShare(knowledgeId, {
                user_ids: userIds,
                group_ids: groupIds,
            });
        } finally {
            validating = false;
        }
    }
</script>

{#if validationResult && !validationResult.can_share}
    <div class="my-3 px-4 py-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
        <div class="flex items-center gap-2 text-yellow-800 dark:text-yellow-200">
            <WarningIcon class="size-5" />
            <span class="font-medium">
                {$i18n.t('Some users cannot access all source documents')}
            </span>
        </div>

        <div class="mt-2 text-sm">
            {#each validationResult.recommendations as rec}
                <div class="flex items-center justify-between py-1">
                    <span>{rec.user_name} - {rec.inaccessible_count} {rec.source} files</span>
                    {#if rec.grant_access_url}
                        <a
                            href={rec.grant_access_url}
                            target="_blank"
                            class="text-blue-600 hover:underline"
                        >
                            {$i18n.t('Grant access')}
                        </a>
                    {/if}
                </div>
            {/each}
        </div>

        <div class="mt-3 flex gap-2">
            <button
                class="btn btn-sm"
                on:click={() => shareToAccessibleOnly()}
            >
                {$i18n.t('Share to {{count}} users with access', {
                    count: validationResult.can_share_to_users.length
                })}
            </button>
        </div>
    </div>
{/if}
```

### 7. Model-Knowledge Validation

Add validation when attaching knowledge bases to models:

```python
# backend/open_webui/routers/models.py (additions)

async def validate_model_knowledge_access(
    model_access_control: dict,
    knowledge_ids: List[str],
) -> SharingValidationResult:
    """
    Validate that users with model access also have source access
    to all attached knowledge bases.
    """
    # Get all users who would have model access
    if model_access_control is None:
        # Public model - check against all users (expensive, maybe skip)
        return SharingValidationResult(can_share=True, ...)

    model_user_ids = model_access_control.get("read", {}).get("user_ids", [])
    model_group_ids = model_access_control.get("read", {}).get("group_ids", [])

    # Aggregate validation across all knowledge bases
    aggregated = SharingValidationResult(can_share=True, ...)

    for knowledge_id in knowledge_ids:
        validator = SharingValidator()
        result = await validator.validate_knowledge_share(
            knowledge_id, model_user_ids, model_group_ids
        )
        # Merge results...

    return aggregated
```

---

## Data Model Changes

### 1. Store Source Permissions in Knowledge Meta

```python
# knowledge.meta structure
{
    "onedrive_sync": {
        "sources": [...],
        "status": "idle",
        "permitted_user_ids": ["user-1", "user-2"],  # NEW: Cached OneDrive permissions
        "permission_sync_at": 1706454600,            # NEW: When permissions were last synced
    },
    # Future sources follow same pattern
    "sharepoint_sync": {
        "permitted_user_ids": [...],
        "permission_sync_at": ...,
    }
}
```

### 2. Store Source Permissions in File Meta

```python
# file.meta structure
{
    "source": "onedrive",
    "onedrive_item_id": "...",
    "onedrive_drive_id": "...",
    "permitted_emails": ["user@company.com", ...],  # NEW: Cached at file level
    "permission_sync_at": 1706454600,               # NEW
}
```

---

## Implementation Phases

### Phase 1: Foundation
1. Add `STRICT_SOURCE_PERMISSIONS` config setting
2. Create `PermissionProvider` interface and registry
3. Implement `OneDrivePermissionProvider`
4. Add `/validate-share` API endpoint
5. Store `permitted_user_ids` in knowledge/file meta during sync

### Phase 2: Sharing Validation UI
1. Add validation feedback in `AccessControl.svelte`
2. Add explicit confirmation modal for sharing
3. Add "Grant access" links to OneDrive
4. Add "Share to users with access" button
5. Handle strict vs loose mode differences

### Phase 3: Real-Time Enforcement
1. Add `check_knowledge_access()` middleware for retrieval
2. Implement upstream revocation detection
3. Add user-facing messages for access denial
4. Add warning banners in chat when model KBs are inaccessible

### Phase 4: Access Tab Enhancement
1. Add "Pending Source Access" section
2. Add "Ready to Add" section for users with source access
3. Show grant/revoke actions per user
4. Add permission sync status display

### Phase 5: Model Integration
1. Add validation when attaching knowledge to models
2. Show warnings in `ModelEditor.svelte`
3. Filter inaccessible KBs at chat time
4. Add model access warnings in chat UI

### Phase 6: Additional Sources
1. Implement `SharePointPermissionProvider`
2. Implement `SlackPermissionProvider`
3. Implement `GoogleDrivePermissionProvider`
4. Each follows same interface pattern

---

## Security Considerations

### Permission Caching

**Risk**: Stale cached permissions could allow unauthorized access.

**Mitigations**:
1. **Short cache TTL**: Refresh permissions every 15-30 minutes
2. **Revalidate on share**: Always check live permissions when sharing
3. **Store sync timestamp**: Track when permissions were last verified
4. **Real-time webhooks** (future): Use OneDrive change notifications

### Permission Expansion vs Restriction

**Principle**: When in doubt, restrict access.

```python
# If permission check fails or times out, deny access
try:
    result = await provider.check_user_access(user_id, resource_ids)
except Exception:
    return PermissionCheckResult(has_access=False, message="Permission check failed")
```

### Audit Trail

Log all sharing validation results:
```python
log.info(f"Sharing validation for knowledge {knowledge_id}: "
         f"requested={len(all_user_ids)}, allowed={len(can_share_to)}, "
         f"denied={len(cannot_share_to)}")
```

---

## Code References

### Existing Implementation
- `backend/open_webui/utils/access_control.py:124-150` - `has_access()` utility
- `backend/open_webui/utils/db/access_control.py:9-130` - SQL permission filters
- `backend/open_webui/services/onedrive/sync_worker.py:170-259` - OneDrive permission sync
- `backend/open_webui/routers/knowledge.py:326-336` - Public sharing permission check
- `backend/open_webui/models/knowledge.py:330-337` - `check_access_by_user_id()`

### UI Components
- `src/lib/components/workspace/common/AccessControl.svelte` - Access control input
- `src/lib/components/workspace/common/AccessControlModal.svelte` - Modal wrapper
- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1300-1315` - KB access button
- `src/lib/components/workspace/Models/ModelEditor.svelte:333-339` - Model access control

### Storage Provider Pattern (Reference)
- `backend/open_webui/storage/provider.py:41-58` - Abstract storage provider interface

---

## Historical Context (from thoughts/)

### Existing Research
- `thoughts/shared/research/2026-01-18-onedrive-file-permission-filtering-ui.md` - OneDrive file filtering in shared collections
- `thoughts/shared/research/2026-01-18-onedrive-implementation-best-practices-review.md` - OneDrive best practices review
- `thoughts/shared/research/2026-01-21-knowledge-base-deletion-orphaned-embeddings.md` - KB deletion and file cleanup

### Existing Plans
- `thoughts/shared/plans/2026-01-18-onedrive-refresh-token-storage.md` - Refresh token storage for background sync

---

## Decisions Made

| Question | Decision | Rationale |
|----------|----------|-----------|
| **Public KB with source files** | Blocked in strict mode (use groups instead); warning in lenient mode | Strict enforces source compliance; lenient trusts owner judgment |
| **Admin override** | **No** - never override source permissions | Source permissions are authoritative, even for admins |
| **Strict mode sharing** | Share only to users with source access; others excluded (can reshare later) | Automatic compliance; users can be added when they gain source access |
| **Lenient mode sharing** | Warning shown but owner can share to anyone | Flexibility; owner takes responsibility |
| **Model inheritance** | Yes - real-time validation at usage time | Models using inaccessible KBs show warnings; KB not used for retrieval |
| **Owner control** | Source access enables, owner grants | Even if user gains source access, owner must explicitly add to KB |
| **Upstream revocation** | Immediate blocking with clear messaging (both modes) | No stale access; users see why they lost access |

## Open Questions

1. **Permission cache duration**: How long should we cache source permissions before revalidating?
   - For display: 15-30 minutes (performance)
   - For sharing operations: Always revalidate live
   - For retrieval: TBD - balance between performance and freshness

2. **Webhook vs polling for permission changes**: Should we implement OneDrive webhooks for real-time permission sync?
   - Pro: Immediate revocation enforcement
   - Con: Additional infrastructure complexity
   - Current: Polling on sync interval + real-time check on access

3. **Group-level source permissions**: How should we handle groups where some members have source access and others don't?
   - Option A: Block sharing to group entirely (strict)
   - Option B: Share to group, but only members with source access can use KB
   - Leaning toward Option B for flexibility

4. **Audit logging**: What level of audit trail should we maintain for permission changes?
   - Sharing validation results
   - Access denials due to source permission loss
   - Permission sync events

---

## Alignment with Industry Best Practices

| Our Approach | Industry Pattern | Source |
|--------------|------------------|--------|
| **Source permissions are authoritative** | "Copilot only surfaces data to which users have view permissions" | Microsoft 365 Copilot |
| **Real-time enforcement** | "If any permissions change, results reflect those changes immediately" | Glean |
| **Permission Provider abstraction** | "Framework compatible with all datasources" | Glean |
| **Pre-filter validation** | "Authorization → Retrieval → LLM" pattern | Pinecone RAG Access Control |
| **No admin override** | Principle of least privilege, no backdoors | NIST, SOC2 |
| **Owner control model** | Separation of source permissions from app permissions | Enterprise IAM patterns |
| **Explicit confirmation flow** | User consent for security-impacting actions | GDPR, security UX best practices |

**Key validation**: The architecture we've designed matches how Microsoft 365 Copilot, Glean, and other enterprise AI tools handle multi-source permissions. The combination of:
- Strict mode option
- Real-time enforcement
- Owner-controlled sharing
- Actionable feedback

...represents current best practice for enterprise RAG permission management.

---

## Related Research

- [Microsoft 365 Copilot Data Protection](https://learn.microsoft.com/en-us/copilot/microsoft-365/microsoft-365-copilot-architecture-data-protection-auditing)
- [Glean - Secure Generative AI Permissions](https://www.glean.com/blog/secure-generative-ai-for-the-enterprise-requires-the-right-permissions-structure)
- [Pinecone - RAG Access Control](https://www.pinecone.io/learn/rag-access-control/)
- [Cerbos - Access Control for RAG LLMs](https://www.cerbos.dev/blog/access-control-for-rag-llms)
- [Descope - ReBAC for RAG Pipelines](https://www.descope.com/blog/post/rebac-rag)
- [Open WebUI ERI Proposal - Discussion #19969](https://github.com/open-webui/open-webui/discussions/19969)
