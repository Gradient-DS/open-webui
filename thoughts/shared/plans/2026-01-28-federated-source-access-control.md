# Federated Source Access Control Implementation Plan

## Overview

Implement a Permission Provider abstraction layer that validates external source access (OneDrive, SharePoint, etc.) when sharing knowledge bases. This ensures users can only share KBs with people who have access to the underlying source documents, with actionable feedback when sharing is blocked.

**MVP Scope**: OneDrive integration only, with architecture designed for future source additions.

## Current State Analysis

### What Exists

1. **Three-tier access control** (`backend/open_webui/utils/access_control.py:124-150`):
   - `None` = Public (all authenticated users)
   - `{}` = Private (owner only)
   - `{read/write: {user_ids, group_ids}}` = Group-based

2. **OneDrive sync** (`backend/open_webui/services/onedrive/sync_worker.py:298-395`):
   - Already maps OneDrive folder permissions to `access_control` via email matching
   - Extracts emails from `grantedTo`, `grantedToIdentities`, `grantedToIdentitiesV2`
   - Maps to Open WebUI users via `Users.get_user_by_email()`

3. **Provider patterns** to follow:
   - Storage Provider: ABC + factory function (`backend/open_webui/storage/provider.py`)
   - Vector DB: Enum + factory class (`backend/open_webui/retrieval/vector/`)

### Key Gaps

**Gap 1 - Forward Direction**: When a KB is made public or shared with additional users beyond OneDrive permissions, the OneDrive permission mapping can be overwritten/bypassed. No validation occurs at share time.

**Gap 2 - Reverse Direction**: When adding source-restricted files (e.g., OneDrive) to an already-public or broadly-shared KB, no validation occurs. Users with KB access but without source access would see limited content without warning.

## Desired End State

After implementation:

1. **STRICT_SOURCE_PERMISSIONS mode (default=true)**:
   - Users without source access are excluded from KB sharing
   - Clear feedback showing who was excluded and why
   - "Grant access" links to source systems (OneDrive sharing modal)

2. **Real-time enforcement**:
   - Users lose KB access immediately when source permissions are revoked
   - Clear messaging explaining why access was lost

3. **Owner control model**:
   - Source access is a prerequisite; KB owner still decides who gets KB access
   - Access Tab shows "Ready to Add" users (have source access, no KB access)

### Verification

**Forward (Sharing KB):**
- [ ] Cannot share KB to user without OneDrive access (strict mode)
- [ ] Warning shown when sharing to user without source access (lenient mode)
- [ ] User loses KB access when removed from OneDrive folder
- [ ] "Grant access" link opens OneDrive sharing modal
- [ ] Model with restricted KB shows warning in chat

**Reverse (Adding Files):**
- [ ] Cannot add OneDrive files to public KB without making it private first (strict mode)
- [ ] Warning shown when adding restricted files to shared KB (lenient mode)
- [ ] OneDrive sync detects and handles public/over-shared KB conflicts
- [ ] "Make Private" option correctly updates KB access control

## What We're NOT Doing

- SharePoint, Slack, Google Drive integrations (future phases after MVP)
- Admin override of source permissions (security principle)
- Webhook-based real-time permission sync (polling + on-access check for now)
- Modifying upstream Open WebUI's access_control schema

## Upstream Merge Conflict Mitigation Strategy

To minimize merge conflicts with upstream Open WebUI:

| High-Risk File | Strategy | Upstream Change |
|----------------|----------|-----------------|
| `retrieval.py` | Create `retrieval_filter.py` wrapper | 2 lines (import + call) |
| `AccessControl.svelte` | Create `SourceAwareAccessControl.svelte` wrapper | None - use wrapper in our components |
| `knowledge.py` | Create `knowledge_permissions.py` router | None - mount in main.py instead |
| `knowledge/index.ts` | Create `knowledge/permissions.ts` module | None - import from new module |

**Principle**: Create new files that wrap/extend upstream functionality. Only touch upstream files for imports and minimal integration points.

## Implementation Approach

Build a Permission Provider abstraction following the existing Storage Provider pattern:
1. Abstract base class with standard interface
2. Provider registry for dynamic lookup
3. OneDrive-specific implementation using existing sync infrastructure
4. Validation service that orchestrates multi-source checks
5. Frontend wrapper component (no changes to upstream `AccessControl.svelte`)
6. Retrieval filter module (minimal 2-line change to upstream `retrieval.py`)

**Upstream File Impact Summary**:
| File | Lines Changed | Change Type |
|------|--------------|-------------|
| `config.py` | ~5 | Append config variable |
| `main.py` | ~7 | Add imports + registration + router mount |
| `retrieval.py` | 2 | Import + function call |
| `sync_worker.py` | ~10 | Store permitted_emails |
| `KnowledgeBase.svelte` | ~3 | Change import + use wrapper |
| `stores/index.ts` | ~1 | Add config type |
| Translation files | ~20 each | Add new strings |

**Files with ZERO upstream changes** (using wrapper approach):
| Would-be File | Wrapper File Instead |
|---------------|---------------------|
| `knowledge.py` | `routers/knowledge_permissions.py` (new router) |
| `knowledge/index.ts` | `apis/knowledge/permissions.ts` (new module) |
| `AccessControl.svelte` | `SourceAwareAccessControl.svelte` (new component) |

**New Files Created**: 14 (services/permissions/, routers/, apis/, components/)

---

## Phase 1: Foundation - Permission Provider Infrastructure

### Overview

Create the Permission Provider abstraction layer, configuration, and OneDrive implementation.

### Changes Required

#### 1. Configuration Setting

**File**: `backend/open_webui/config.py`

Add after line ~1620 (after `ENABLE_USER_ARCHIVAL`):

```python
####################################
# Source Permission Settings
####################################

STRICT_SOURCE_PERMISSIONS = PersistentConfig(
    "STRICT_SOURCE_PERMISSIONS",
    "permissions.strict_source_permissions",
    os.environ.get("STRICT_SOURCE_PERMISSIONS", "true").lower() == "true",
)
```

**File**: `backend/open_webui/main.py`

Add to imports (around line 431):
```python
from open_webui.config import (
    # ... existing imports ...
    STRICT_SOURCE_PERMISSIONS,
)
```

Add to app.state.config initialization (around line 872):
```python
app.state.config.STRICT_SOURCE_PERMISSIONS = STRICT_SOURCE_PERMISSIONS
```

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

Add to data section:
```yaml
STRICT_SOURCE_PERMISSIONS: {{ .Values.openWebui.config.strictSourcePermissions | default "true" | quote }}
```

**File**: `helm/open-webui-tenant/values.yaml`

Add under `openWebui.config`:
```yaml
strictSourcePermissions: true
```

#### 2. Permission Provider Interface

**File**: `backend/open_webui/services/permissions/__init__.py` (new)

```python
from open_webui.services.permissions.provider import (
    PermissionProvider,
    PermissionCheckResult,
    UserAccessStatus,
    PermissionSyncResult,
)
from open_webui.services.permissions.registry import PermissionProviderRegistry
from open_webui.services.permissions.validator import (
    SharingValidator,
    SharingValidationResult,
)

__all__ = [
    "PermissionProvider",
    "PermissionCheckResult",
    "UserAccessStatus",
    "PermissionSyncResult",
    "PermissionProviderRegistry",
    "SharingValidator",
    "SharingValidationResult",
]
```

**File**: `backend/open_webui/services/permissions/provider.py` (new)

```python
"""
Permission Provider Interface

Abstract base class for source-level permission checking.
Implementations check if users have access to resources in external systems
(OneDrive, SharePoint, Slack, etc.).
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Set, Dict
from pydantic import BaseModel


class PermissionCheckResult(BaseModel):
    """Result of checking a user's access to resources."""

    has_access: bool
    accessible_items: List[str] = []  # IDs of accessible resources
    inaccessible_items: List[str] = []  # IDs of inaccessible resources
    grant_access_url: Optional[str] = None  # URL to grant access
    message: Optional[str] = None  # User-friendly explanation


class UserAccessStatus(BaseModel):
    """Status of a user's access to source resources."""

    user_id: str
    user_name: str
    user_email: str
    has_source_access: bool
    has_kb_access: bool
    missing_resources: List[str] = []
    missing_resource_count: int = 0
    source_type: str = ""
    grant_access_url: Optional[str] = None


class PermissionSyncResult(BaseModel):
    """Result of a permission sync operation."""

    synced_at: int
    resources_checked: int
    permissions_changed: int
    users_gained_access: List[str] = []
    users_lost_access: List[str] = []


class PermissionProvider(ABC):
    """
    Abstract interface for source permission checking.

    Implementations should check permissions in external systems like
    OneDrive, SharePoint, Slack, etc.
    """

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
            use_cache: If False, always check live permissions

        Returns:
            PermissionCheckResult with access status and details
        """
        pass

    @abstractmethod
    async def check_bulk_access(
        self,
        user_ids: List[str],
        resource_ids: List[str],
        permission_type: str = "read",
    ) -> Dict[str, PermissionCheckResult]:
        """
        Check access for multiple users at once.

        Used for sharing validation to check all target users efficiently.

        Returns:
            Dict mapping user_id to their PermissionCheckResult
        """
        pass

    @abstractmethod
    async def get_permitted_users(
        self,
        resource_ids: List[str],
        permission_type: str = "read",
    ) -> Set[str]:
        """
        Get all Open WebUI user IDs with access to given resources.

        Returns:
            Set of user IDs that have access to ALL specified resources
        """
        pass

    @abstractmethod
    async def get_users_ready_for_access(
        self,
        knowledge_id: str,
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
        """
        Get URL for granting access to a resource.

        For OneDrive, returns the sharing modal URL.
        """
        pass
```

#### 3. Permission Provider Registry

**File**: `backend/open_webui/services/permissions/registry.py` (new)

```python
"""
Permission Provider Registry

Central registry for permission providers. Providers register themselves
and can be looked up by source type.
"""

import logging
from typing import Dict, List, Optional

from open_webui.services.permissions.provider import PermissionProvider

log = logging.getLogger(__name__)


class PermissionProviderRegistry:
    """Central registry for permission providers."""

    _providers: Dict[str, PermissionProvider] = {}
    _initialized: bool = False

    @classmethod
    def register(cls, provider: PermissionProvider) -> None:
        """Register a permission provider."""
        cls._providers[provider.source_type] = provider
        log.info(f"Registered permission provider: {provider.source_type}")

    @classmethod
    def unregister(cls, source_type: str) -> None:
        """Unregister a permission provider."""
        if source_type in cls._providers:
            del cls._providers[source_type]
            log.info(f"Unregistered permission provider: {source_type}")

    @classmethod
    def get_provider(cls, source_type: str) -> Optional[PermissionProvider]:
        """Get a permission provider by source type."""
        return cls._providers.get(source_type)

    @classmethod
    def get_all_providers(cls) -> List[PermissionProvider]:
        """Get all registered permission providers."""
        return list(cls._providers.values())

    @classmethod
    def get_provider_for_file(cls, file_meta: dict) -> Optional[PermissionProvider]:
        """
        Get the appropriate provider for a file based on its metadata.

        Args:
            file_meta: File metadata dict containing 'source' key

        Returns:
            PermissionProvider if one exists for this source type
        """
        source = file_meta.get("source", "local")
        if source == "local":
            return None  # Local files have no source restrictions
        return cls.get_provider(source)

    @classmethod
    def has_provider(cls, source_type: str) -> bool:
        """Check if a provider is registered for the given source type."""
        return source_type in cls._providers

    @classmethod
    def clear(cls) -> None:
        """Clear all registered providers. Used for testing."""
        cls._providers.clear()
        cls._initialized = False
```

#### 4. OneDrive Permission Provider

**File**: `backend/open_webui/services/permissions/providers/__init__.py` (new)

```python
from open_webui.services.permissions.providers.onedrive import OneDrivePermissionProvider

__all__ = ["OneDrivePermissionProvider"]
```

**File**: `backend/open_webui/services/permissions/providers/onedrive.py` (new)

```python
"""
OneDrive Permission Provider

Checks OneDrive file/folder permissions for sharing validation.
Uses cached permissions from file metadata, with optional live refresh.
"""

import logging
from typing import Dict, List, Optional, Set

from open_webui.models.files import Files
from open_webui.models.users import Users
from open_webui.models.knowledge import Knowledges
from open_webui.models.groups import Groups
from open_webui.utils.access_control import has_access
from open_webui.services.permissions.provider import (
    PermissionProvider,
    PermissionCheckResult,
    UserAccessStatus,
)

log = logging.getLogger(__name__)


class OneDrivePermissionProvider(PermissionProvider):
    """Permission provider for OneDrive files."""

    source_type = "onedrive"

    async def check_user_access(
        self,
        user_id: str,
        resource_ids: List[str],
        permission_type: str = "read",
        use_cache: bool = True,
    ) -> PermissionCheckResult:
        """Check if user has OneDrive access to specified files."""
        user = Users.get_user_by_id(user_id)
        if not user:
            return PermissionCheckResult(
                has_access=False,
                message="User not found",
            )

        user_email = user.email.lower() if user.email else ""
        accessible = []
        inaccessible = []
        first_inaccessible_id = None

        for resource_id in resource_ids:
            file = Files.get_file_by_id(resource_id)
            if not file:
                # File not found - treat as accessible (might be deleted)
                accessible.append(resource_id)
                continue

            source = file.meta.get("source") if file.meta else None
            if source != "onedrive":
                # Non-OneDrive files pass through
                accessible.append(resource_id)
                continue

            # Get permitted emails from file metadata
            permitted_emails = file.meta.get("permitted_emails", [])
            if not permitted_emails:
                # Fall back to knowledge-level permissions if file doesn't have them
                # This handles files synced before per-file permission storage
                knowledge_id = file.meta.get("knowledge_id")
                if knowledge_id:
                    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
                    if knowledge and knowledge.meta:
                        onedrive_sync = knowledge.meta.get("onedrive_sync", {})
                        permitted_emails = onedrive_sync.get("permitted_emails", [])

            # Check if user's email is in permitted list
            permitted_lower = [e.lower() for e in permitted_emails]
            if user_email and user_email in permitted_lower:
                accessible.append(resource_id)
            else:
                inaccessible.append(resource_id)
                if first_inaccessible_id is None:
                    first_inaccessible_id = resource_id

        grant_url = None
        if first_inaccessible_id:
            grant_url = self.get_grant_access_url(first_inaccessible_id)

        return PermissionCheckResult(
            has_access=len(inaccessible) == 0,
            accessible_items=accessible,
            inaccessible_items=inaccessible,
            grant_access_url=grant_url,
            message=(
                f"{len(inaccessible)} OneDrive file(s) not accessible"
                if inaccessible
                else None
            ),
        )

    async def check_bulk_access(
        self,
        user_ids: List[str],
        resource_ids: List[str],
        permission_type: str = "read",
    ) -> Dict[str, PermissionCheckResult]:
        """Check access for multiple users efficiently."""
        results = {}
        for user_id in user_ids:
            results[user_id] = await self.check_user_access(
                user_id, resource_ids, permission_type
            )
        return results

    async def get_permitted_users(
        self,
        resource_ids: List[str],
        permission_type: str = "read",
    ) -> Set[str]:
        """Get all Open WebUI users with access to all specified resources."""
        if not resource_ids:
            return set()

        # Collect all permitted emails across resources
        all_permitted_emails: Optional[Set[str]] = None

        for resource_id in resource_ids:
            file = Files.get_file_by_id(resource_id)
            if not file:
                continue

            source = file.meta.get("source") if file.meta else None
            if source != "onedrive":
                continue

            permitted_emails = file.meta.get("permitted_emails", [])
            permitted_set = {e.lower() for e in permitted_emails}

            if all_permitted_emails is None:
                all_permitted_emails = permitted_set
            else:
                # Intersection - user needs access to ALL resources
                all_permitted_emails &= permitted_set

        if not all_permitted_emails:
            return set()

        # Map emails to user IDs
        permitted_user_ids = set()
        for email in all_permitted_emails:
            user = Users.get_user_by_email(email)
            if user:
                permitted_user_ids.add(user.id)

        return permitted_user_ids

    async def get_users_ready_for_access(
        self,
        knowledge_id: str,
        resource_ids: List[str],
    ) -> List[UserAccessStatus]:
        """
        Get users who have OneDrive access but haven't been granted KB access.
        """
        knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
        if not knowledge:
            return []

        # Get users with source access
        permitted_user_ids = await self.get_permitted_users(resource_ids)

        # Get users who already have KB access
        kb_access_control = knowledge.access_control or {}
        kb_user_ids = set(kb_access_control.get("read", {}).get("user_ids", []))
        kb_group_ids = kb_access_control.get("read", {}).get("group_ids", [])

        # Expand group members
        for group_id in kb_group_ids:
            group = Groups.get_group_by_id(group_id)
            if group:
                kb_user_ids.update(group.user_ids)

        # Also include owner
        kb_user_ids.add(knowledge.user_id)

        # Find users ready to add (have source access, no KB access)
        ready_user_ids = permitted_user_ids - kb_user_ids

        result = []
        for user_id in ready_user_ids:
            user = Users.get_user_by_id(user_id)
            if user:
                result.append(
                    UserAccessStatus(
                        user_id=user.id,
                        user_name=user.name,
                        user_email=user.email,
                        has_source_access=True,
                        has_kb_access=False,
                        source_type="onedrive",
                    )
                )

        return result

    def get_grant_access_url(
        self,
        resource_id: str,
        target_user_email: Optional[str] = None,
    ) -> Optional[str]:
        """Get OneDrive sharing URL for a file."""
        file = Files.get_file_by_id(resource_id)
        if not file or not file.meta:
            return None

        source = file.meta.get("source")
        if source != "onedrive":
            return None

        # Get OneDrive web URL if available
        web_url = file.meta.get("onedrive_web_url")
        if web_url:
            return web_url

        # Construct URL from drive/item IDs
        drive_id = file.meta.get("onedrive_drive_id")
        item_id = file.meta.get("onedrive_item_id")

        if drive_id and item_id:
            # Link to OneDrive item details (user can share from there)
            return f"https://onedrive.live.com/?id={item_id}"

        return None
```

#### 5. Register Provider on Startup

**File**: `backend/open_webui/main.py`

Add import (around line 150 with other service imports):
```python
from open_webui.services.permissions.registry import PermissionProviderRegistry
from open_webui.services.permissions.providers.onedrive import OneDrivePermissionProvider
```

Add registration in the startup section (around line 900, after app.state.config setup):
```python
# Register permission providers
PermissionProviderRegistry.register(OneDrivePermissionProvider())
log.info("Registered OneDrive permission provider")
```

#### 6. Store Permitted Emails in File Metadata During Sync

**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Update `_process_file()` method to store permitted emails in file metadata.

Find the section where file is created/updated (around line 180-200) and ensure `permitted_emails` is stored:

```python
# In _process_file method, when creating/updating file metadata:
file_meta = {
    "source": "onedrive",
    "onedrive_item_id": item_id,
    "onedrive_drive_id": drive_id,
    "onedrive_web_url": web_url,
    "knowledge_id": self.knowledge_id,
    "permitted_emails": list(self._permitted_emails),  # Add this line
}
```

Also update `_sync_permissions()` to store emails at knowledge level:

```python
# In _sync_permissions method, after extracting permitted_emails:
# Store in knowledge meta for reference
if knowledge and knowledge.meta:
    meta = knowledge.meta.copy()
    if "onedrive_sync" not in meta:
        meta["onedrive_sync"] = {}
    meta["onedrive_sync"]["permitted_emails"] = list(permitted_emails)
    meta["onedrive_sync"]["permission_sync_at"] = int(time.time())
    # Update knowledge meta (need to add this capability)
```

### Success Criteria

#### Automated Verification:
- [x] Config loads correctly: `python -c "from open_webui.config import STRICT_SOURCE_PERMISSIONS; print(STRICT_SOURCE_PERMISSIONS.value)"`
- [x] Provider registers: `python -c "from open_webui.services.permissions.registry import PermissionProviderRegistry; from open_webui.services.permissions.providers.onedrive import OneDrivePermissionProvider; PermissionProviderRegistry.register(OneDrivePermissionProvider()); print(PermissionProviderRegistry.get_provider('onedrive'))"`
- [x] Type checking passes: `npm run check` (pre-existing errors only)
- [x] Backend linting: `npm run lint:backend` (pre-existing warnings only)
- [ ] App starts without errors: `open-webui dev`

#### Manual Verification:
- [ ] OneDrive sync still works correctly
- [ ] File metadata includes `permitted_emails` after sync
- [ ] Knowledge meta includes `onedrive_sync.permitted_emails` after sync

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 1b: Reverse Validation - Adding Restricted Files to Shared KBs

### Overview

**Critical Gap Identified**: Phase 1 handles "sharing KB → checking source access", but we also need to handle the reverse scenario: adding source-restricted files to a KB that is already public or shared with users who lack source access.

**Scenarios:**
1. KB is **public** (`access_control = None`) and user adds OneDrive files with restricted access
2. KB is **shared with groups/users** and user adds OneDrive files that some of those users can't access
3. OneDrive sync runs on a KB that has been made public or shared more broadly than source permissions allow

### Desired Behavior

**Strict Mode (default):**
- Block adding restricted files to public/over-shared KBs
- Show modal with options:
  1. "Make KB Private" - Set KB to private, then add files (user can reshare afterwards)
  2. "Cancel" - Don't add the files
- Include grant access links where possible (e.g., OneDrive sharing modal)

**Lenient Mode:**
- Show warning modal explaining the conflict
- Options:
  1. "Continue Anyway" - Add files despite access mismatch (users without source access will see limited content)
  2. "Make KB Private First" - Set KB to private, then add files
  3. "Cancel" - Don't add the files
- Include grant access links

### Changes Required

#### 1. File Addition Validator Service

**File**: `backend/open_webui/services/permissions/validator.py`

Add method to existing `SharingValidator` class:

```python
class FileAdditionConflict(BaseModel):
    """Conflict when adding restricted files to shared KB."""

    has_conflict: bool
    kb_is_public: bool = False
    users_without_access: List[str] = []  # User IDs who would lose access to new files
    user_details: List[SharingRecommendation] = []  # Details for UI
    source_type: str = ""
    grant_access_url: Optional[str] = None


class SharingValidator:
    # ... existing methods ...

    async def validate_file_addition(
        self,
        knowledge_id: str,
        file_ids: List[str],
    ) -> FileAdditionConflict:
        """
        Validate adding files to a KB doesn't create access conflicts.

        Checks if the KB's current access control is broader than
        the source permissions of the files being added.
        """
        knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
        if not knowledge:
            return FileAdditionConflict(has_conflict=False)

        # Check if KB is public
        kb_is_public = knowledge.access_control is None

        # Get all users who currently have KB access
        kb_user_ids = self._get_kb_users(knowledge)

        # For each file, check source permissions
        users_without_source_access = set()
        source_type = ""
        grant_url = None

        for file_id in file_ids:
            file = Files.get_file_by_id(file_id)
            if not file or not file.meta:
                continue

            source = file.meta.get("source", "local")
            if source == "local":
                continue

            provider = PermissionProviderRegistry.get_provider(source)
            if not provider:
                continue

            source_type = source

            # Check each KB user against source permissions
            for user_id in kb_user_ids:
                result = await provider.check_user_access(user_id, [file_id])
                if not result.has_access:
                    users_without_source_access.add(user_id)
                    if not grant_url:
                        grant_url = result.grant_access_url

        if not users_without_source_access and not kb_is_public:
            return FileAdditionConflict(has_conflict=False)

        # Build user details for UI
        user_details = []
        for user_id in users_without_source_access:
            user = Users.get_user_by_id(user_id)
            if user:
                user_details.append(SharingRecommendation(
                    user_id=user_id,
                    user_name=user.name,
                    user_email=user.email,
                    source_type=source_type,
                    inaccessible_count=len(file_ids),
                    grant_access_url=grant_url,
                ))

        return FileAdditionConflict(
            has_conflict=True,
            kb_is_public=kb_is_public,
            users_without_access=list(users_without_source_access),
            user_details=user_details,
            source_type=source_type,
            grant_access_url=grant_url,
        )

    def _get_kb_users(self, knowledge) -> Set[str]:
        """Get all user IDs with access to the KB."""
        if knowledge.access_control is None:
            # Public KB - return all users (or a representative sample)
            # For validation, we can't check all users, so return empty
            # and rely on kb_is_public flag
            return set()

        user_ids = set()
        ac = knowledge.access_control

        # Direct user access
        user_ids.update(ac.get("read", {}).get("user_ids", []))
        user_ids.update(ac.get("write", {}).get("user_ids", []))

        # Group members
        for group_id in ac.get("read", {}).get("group_ids", []):
            group = Groups.get_group_by_id(group_id)
            if group:
                user_ids.update(group.user_ids)
        for group_id in ac.get("write", {}).get("group_ids", []):
            group = Groups.get_group_by_id(group_id)
            if group:
                user_ids.update(group.user_ids)

        # Always include owner
        user_ids.add(knowledge.user_id)

        return user_ids
```

#### 2. File Addition Validation API Endpoint

**File**: `backend/open_webui/routers/knowledge_permissions.py`

Add endpoint:

```python
class FileAdditionValidationRequest(BaseModel):
    file_ids: List[str] = []


class FileAdditionConflictResponse(BaseModel):
    has_conflict: bool
    kb_is_public: bool
    users_without_access: List[str]
    user_details: List[SharingRecommendation]
    source_type: str
    grant_access_url: Optional[str]


@router.post("/{id}/validate-file-addition", response_model=FileAdditionConflictResponse)
async def validate_file_addition(
    id: str,
    request: Request,
    form_data: FileAdditionValidationRequest,
    user=Depends(get_verified_user),
):
    """
    Validate adding files to a knowledge base.

    Returns conflict info if the KB is shared more broadly than
    the source permissions of the files being added.
    """
    knowledge = Knowledges.get_knowledge_by_id(id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge not found",
        )

    # Check user has write access
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    validator = SharingValidator()
    result = await validator.validate_file_addition(id, form_data.file_ids)

    return FileAdditionConflictResponse(**result.model_dump())
```

#### 3. OneDrive Sync Pre-Check

**File**: `backend/open_webui/services/onedrive/sync_worker.py`

Add validation before sync starts:

```python
async def _validate_kb_access_level(self) -> Optional[Dict[str, Any]]:
    """
    Check if KB access level is compatible with OneDrive permissions.

    Returns conflict info if KB is public or shared too broadly.
    """
    knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
    if not knowledge:
        return None

    # If KB is public, that's a conflict for restricted source files
    if knowledge.access_control is None:
        return {
            "has_conflict": True,
            "kb_is_public": True,
            "message": "Knowledge base is public but OneDrive files have restricted access",
        }

    # Check if any KB users lack OneDrive access
    # (This will be populated after _sync_permissions runs)

    return None
```

Modify sync() to check this and emit appropriate events.

#### 4. Frontend: File Addition Conflict Modal

**File**: `src/lib/components/workspace/common/FileAdditionConflictModal.svelte` (new)

Similar to ShareConfirmationModal but for the reverse scenario:
- Shows when adding files would create access conflicts
- In strict mode: "Make Private" or "Cancel" options
- In lenient mode: "Continue Anyway", "Make Private", or "Cancel" options
- Shows grant access links

#### 5. Frontend API Client Update

**File**: `src/lib/apis/knowledge/permissions.ts`

Add:

```typescript
export interface FileAdditionConflict {
    has_conflict: boolean;
    kb_is_public: boolean;
    users_without_access: string[];
    user_details: SharingRecommendation[];
    source_type: string;
    grant_access_url: string | null;
}

export const validateFileAddition = async (
    token: string,
    knowledgeId: string,
    fileIds: string[]
): Promise<FileAdditionConflict | null> => {
    // ... similar to validateKnowledgeShare
};
```

#### 6. Integration Points

**OneDrive Sync Start:**
- Before sync begins, call validation
- If conflict detected, emit socket event with conflict info
- Frontend shows modal with options before sync proceeds

**Manual File Upload to KB:**
- When user uploads/adds files to a KB, validate first
- Show conflict modal if needed

**KB Access Control Change:**
- When making KB public or adding users/groups
- Validate against existing source-restricted files
- This is already handled by Phase 2's sharing validation

### Success Criteria

#### Automated Verification:
- [x] `validate_file_addition` endpoint returns correct conflicts
- [x] OneDrive sync detects public KB conflict
- [x] Type checking passes: `npm run check` (pre-existing errors only)
- [x] Backend linting: `npm run lint:backend` (pre-existing warnings only)

#### Manual Verification:
- [ ] Adding OneDrive files to public KB shows conflict modal
- [ ] "Make Private" option works correctly
- [ ] Grant access links open OneDrive sharing
- [ ] Lenient mode allows "Continue Anyway"

**Implementation Note**: This phase ensures bidirectional validation - both when sharing KBs and when adding restricted files.

---

## Phase 2: Sharing Validation Service & API

### Overview

Create the SharingValidator service and API endpoint for validating shares before they're applied.

### Changes Required

#### 1. Sharing Validator Service

**File**: `backend/open_webui/services/permissions/validator.py` (new)

```python
"""
Sharing Validation Service

Validates sharing operations against source permissions.
Returns detailed results for UI feedback.
"""

import logging
from typing import List, Dict, Optional
from pydantic import BaseModel

from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files, FileModel
from open_webui.models.users import Users
from open_webui.models.groups import Groups
from open_webui.services.permissions.registry import PermissionProviderRegistry
from open_webui.services.permissions.provider import UserAccessStatus

log = logging.getLogger(__name__)


class SharingRecommendation(BaseModel):
    """Recommendation for granting source access."""

    user_id: str
    user_name: str
    user_email: str
    source_type: str
    inaccessible_count: int
    grant_access_url: Optional[str] = None


class SharingValidationResult(BaseModel):
    """Result of validating a sharing operation."""

    can_share: bool  # True if all users have source access
    can_share_to_users: List[str] = []  # User IDs that can receive access
    cannot_share_to_users: List[str] = []  # User IDs blocked by source permissions
    blocking_resources: Dict[str, List[str]] = {}  # user_id -> inaccessible resource IDs
    recommendations: List[SharingRecommendation] = []  # How to grant access
    source_restricted: bool = False  # True if KB has source-restricted files


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

        Args:
            knowledge_id: Knowledge base ID
            target_user_ids: User IDs to share with
            target_group_ids: Group IDs to share with

        Returns:
            SharingValidationResult with detailed access information
        """
        knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
        if not knowledge:
            return SharingValidationResult(
                can_share=False,
                source_restricted=False,
            )

        # Get files in the knowledge base
        files = self._get_knowledge_files(knowledge_id)
        if not files:
            # No files = no restrictions
            return SharingValidationResult(
                can_share=True,
                can_share_to_users=target_user_ids,
                source_restricted=False,
            )

        # Expand groups to user IDs
        all_user_ids = set(target_user_ids)
        for group_id in target_group_ids:
            group = Groups.get_group_by_id(group_id)
            if group:
                all_user_ids.update(group.user_ids)

        # Group files by source
        files_by_source: Dict[str, List[str]] = {}
        for file in files:
            source = file.meta.get("source", "local") if file.meta else "local"
            if source not in files_by_source:
                files_by_source[source] = []
            files_by_source[source].append(file.id)

        # Check if any files have source restrictions
        source_types = [s for s in files_by_source.keys() if s != "local"]
        if not source_types:
            return SharingValidationResult(
                can_share=True,
                can_share_to_users=list(all_user_ids),
                source_restricted=False,
            )

        # Check permissions for each source
        can_share_to = set(all_user_ids)
        cannot_share_to = set()
        blocking_resources: Dict[str, List[str]] = {}
        recommendations: List[SharingRecommendation] = []

        for source, file_ids in files_by_source.items():
            if source == "local":
                continue

            provider = PermissionProviderRegistry.get_provider(source)
            if not provider:
                log.warning(f"No permission provider for source: {source}")
                continue

            # Bulk check all users
            results = await provider.check_bulk_access(
                list(all_user_ids), file_ids, "read"
            )

            for user_id, result in results.items():
                if not result.has_access:
                    can_share_to.discard(user_id)
                    cannot_share_to.add(user_id)
                    blocking_resources[user_id] = result.inaccessible_items

                    # Add recommendation for this user
                    if user_id not in [r.user_id for r in recommendations]:
                        user = Users.get_user_by_id(user_id)
                        if user:
                            recommendations.append(
                                SharingRecommendation(
                                    user_id=user_id,
                                    user_name=user.name,
                                    user_email=user.email,
                                    source_type=source,
                                    inaccessible_count=len(result.inaccessible_items),
                                    grant_access_url=result.grant_access_url,
                                )
                            )

        return SharingValidationResult(
            can_share=len(cannot_share_to) == 0,
            can_share_to_users=list(can_share_to),
            cannot_share_to_users=list(cannot_share_to),
            blocking_resources=blocking_resources,
            recommendations=recommendations,
            source_restricted=True,
        )

    async def get_users_with_source_access(
        self,
        knowledge_id: str,
    ) -> List[UserAccessStatus]:
        """
        Get status of all users' source access for a knowledge base.

        Returns list of users showing who has source access and KB access.
        """
        knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
        if not knowledge:
            return []

        files = self._get_knowledge_files(knowledge_id)
        source_file_ids = [
            f.id for f in files
            if f.meta and f.meta.get("source", "local") != "local"
        ]

        if not source_file_ids:
            return []

        # Get users ready to add from each provider
        result = []
        providers_checked = set()

        for file in files:
            if not file.meta:
                continue
            source = file.meta.get("source", "local")
            if source == "local" or source in providers_checked:
                continue

            provider = PermissionProviderRegistry.get_provider(source)
            if provider:
                users = await provider.get_users_ready_for_access(
                    knowledge_id, source_file_ids
                )
                result.extend(users)
                providers_checked.add(source)

        return result

    def _get_knowledge_files(self, knowledge_id: str) -> List[FileModel]:
        """Get all files associated with a knowledge base."""
        knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
        if not knowledge or not knowledge.data:
            return []

        file_ids = knowledge.data.get("file_ids", [])
        files = []
        for file_id in file_ids:
            file = Files.get_file_by_id(file_id)
            if file:
                files.append(file)

        return files
```

#### 2. Knowledge Permissions Router (NEW FILE - No upstream changes to knowledge.py)

**File**: `backend/open_webui/routers/knowledge_permissions.py` (new)

Create a separate router for permission-related endpoints:

```python
"""
Knowledge Permissions Router

Handles permission validation endpoints for knowledge bases.
Mounted alongside the main knowledge router to avoid modifying upstream code.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.models.knowledge import Knowledges
from open_webui.utils.access_control import has_access
from open_webui.utils.auth import get_verified_user
from open_webui.constants import ERROR_MESSAGES
from open_webui.services.permissions.validator import (
    SharingValidator,
    SharingRecommendation,
)
from open_webui.services.permissions.provider import UserAccessStatus

log = logging.getLogger(__name__)

router = APIRouter()


####################################
# Request/Response Models
####################################


class ShareValidationRequest(BaseModel):
    user_ids: List[str] = []
    group_ids: List[str] = []


class ShareValidationResponse(BaseModel):
    can_share: bool
    can_share_to_users: List[str]
    cannot_share_to_users: List[str]
    blocking_resources: dict
    recommendations: List[SharingRecommendation]
    source_restricted: bool


class UsersReadyForAccessResponse(BaseModel):
    users: List[UserAccessStatus]


####################################
# Endpoints
####################################


@router.post("/{id}/validate-share", response_model=ShareValidationResponse)
async def validate_knowledge_share(
    id: str,
    request: Request,
    form_data: ShareValidationRequest,
    user=Depends(get_verified_user),
):
    """
    Validate sharing a knowledge base with specified users/groups.

    Returns which users can/cannot be shared with based on source permissions.
    """
    knowledge = Knowledges.get_knowledge_by_id(id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge not found",
        )

    # Check user has write access
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    validator = SharingValidator()
    result = await validator.validate_knowledge_share(
        id, form_data.user_ids, form_data.group_ids
    )

    return ShareValidationResponse(**result.model_dump())


@router.get("/{id}/users-ready-for-access", response_model=UsersReadyForAccessResponse)
async def get_users_ready_for_access(
    id: str,
    request: Request,
    user=Depends(get_verified_user),
):
    """
    Get users who have source access but haven't been granted KB access.

    Used for the "Ready to Add" section in the Access tab.
    """
    knowledge = Knowledges.get_knowledge_by_id(id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge not found",
        )

    # Check user has write access
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    validator = SharingValidator()
    users = await validator.get_users_with_source_access(id)

    return UsersReadyForAccessResponse(users=users)
```

**File**: `backend/open_webui/main.py`

Add router mount (around line 150 with other router imports):
```python
from open_webui.routers import knowledge_permissions
```

Add to router mounts (around line 950):
```python
app.include_router(knowledge_permissions.router, prefix="/api/v1/knowledge", tags=["knowledge"])
```

**Upstream impact**: 2 lines added to main.py (import + mount). Zero changes to knowledge.py.

#### 3. Frontend API Client (NEW FILE - No upstream changes to knowledge/index.ts)

**File**: `src/lib/apis/knowledge/permissions.ts` (new)

Create a separate module for permission-related API calls:
```typescript
/**
 * Knowledge Permissions API
 *
 * Handles permission validation for knowledge base sharing.
 * Separate module to avoid modifying upstream knowledge/index.ts.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface SharingRecommendation {
	user_id: string;
	user_name: string;
	user_email: string;
	source_type: string;
	inaccessible_count: number;
	grant_access_url: string | null;
}

export interface ShareValidationResult {
	can_share: boolean;
	can_share_to_users: string[];
	cannot_share_to_users: string[];
	blocking_resources: Record<string, string[]>;
	recommendations: SharingRecommendation[];
	source_restricted: boolean;
}

export interface UserAccessStatus {
	user_id: string;
	user_name: string;
	user_email: string;
	has_source_access: boolean;
	has_kb_access: boolean;
	missing_resources: string[];
	missing_resource_count: number;
	source_type: string;
	grant_access_url: string | null;
}

export const validateKnowledgeShare = async (
	token: string,
	knowledgeId: string,
	userIds: string[],
	groupIds: string[]
): Promise<ShareValidationResult | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/knowledge/${knowledgeId}/validate-share`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			user_ids: userIds,
			group_ids: groupIds
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getUsersReadyForAccess = async (
	token: string,
	knowledgeId: string
): Promise<UserAccessStatus[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/knowledge/${knowledgeId}/users-ready-for-access`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return { users: [] };
		});

	if (error) {
		throw error;
	}

	return res.users;
};
```

**Upstream impact**: Zero changes to `knowledge/index.ts`. Frontend components import from `$lib/apis/knowledge/permissions`.

### Success Criteria

#### Automated Verification:
- [x] Type checking passes: `npm run check` (pre-existing errors only)
- [x] Backend linting: `npm run lint:backend` (pre-existing warnings only)
- [x] Frontend linting: `npm run lint:frontend` (pre-existing errors only)
- [x] App starts: modules import successfully
- [ ] API endpoint responds: `curl -X POST http://localhost:8080/api/v1/knowledge/{id}/validate-share -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"user_ids": [], "group_ids": []}'`

#### Manual Verification:
- [ ] Validate-share endpoint returns correct results for OneDrive KB
- [ ] Users without OneDrive access appear in `cannot_share_to_users`
- [ ] Recommendations include correct grant_access_url

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Sharing Validation UI

### Overview

Create a wrapper component `SourceAwareAccessControl.svelte` that adds sharing validation to the existing `AccessControl.svelte`. This approach avoids modifying the upstream component directly.

### Changes Required

#### 1. Sharing Confirmation Modal

**File**: `src/lib/components/workspace/common/ShareConfirmationModal.svelte` (new)

```svelte
<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import type { ShareValidationResult, SharingRecommendation } from '$lib/apis/knowledge/permissions';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let validationResult: ShareValidationResult | null = null;
	export let strictMode = true;
	export let targetName = '';

	$: canShareCount = validationResult?.can_share_to_users?.length ?? 0;
	$: cannotShareCount = validationResult?.cannot_share_to_users?.length ?? 0;
	$: totalCount = canShareCount + cannotShareCount;

	function handleConfirm() {
		dispatch('confirm', { shareToAll: !strictMode });
	}

	function handleCancel() {
		dispatch('cancel');
	}
</script>

<Modal size="md" bind:show>
	<div class="p-6">
		<div class="flex items-center gap-3 mb-4">
			<div class="p-2 bg-yellow-100 dark:bg-yellow-900/30 rounded-full">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					fill="none"
					viewBox="0 0 24 24"
					stroke-width="1.5"
					stroke="currentColor"
					class="w-6 h-6 text-yellow-600 dark:text-yellow-400"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
					/>
				</svg>
			</div>
			<div>
				<h3 class="text-lg font-semibold">{$i18n.t('Confirm Sharing')}</h3>
				<p class="text-sm text-gray-500">
					{$i18n.t('Sharing "{{name}}"', { name: targetName })}
				</p>
			</div>
		</div>

		{#if validationResult}
			<!-- Users with full access -->
			{#if canShareCount > 0}
				<div class="mb-4 p-3 bg-green-50 dark:bg-green-900/20 rounded-lg">
					<div class="flex items-center gap-2 text-green-800 dark:text-green-200">
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M5 13l4 4L19 7"
							/>
						</svg>
						<span class="font-medium">
							{$i18n.t('{{count}} users with full source access', { count: canShareCount })}
						</span>
					</div>
					<p class="text-sm text-green-700 dark:text-green-300 mt-1">
						{$i18n.t('Have permissions for all source documents')}
					</p>
				</div>
			{/if}

			<!-- Users without access -->
			{#if cannotShareCount > 0}
				<div class="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
					<div class="flex items-center gap-2 text-yellow-800 dark:text-yellow-200">
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
							/>
						</svg>
						<span class="font-medium">
							{#if strictMode}
								{$i18n.t('{{count}} users will NOT receive access', { count: cannotShareCount })}
							{:else}
								{$i18n.t('{{count}} users missing source access', { count: cannotShareCount })}
							{/if}
						</span>
					</div>

					<div class="mt-3 space-y-2 max-h-48 overflow-y-auto">
						{#each validationResult.recommendations.slice(0, 5) as rec}
							<div class="flex items-center justify-between text-sm py-1">
								<div>
									<span class="font-medium">{rec.user_email}</span>
									<span class="text-gray-500 ml-2">
										{$i18n.t('Missing: {{count}} {{source}} files', {
											count: rec.inaccessible_count,
											source: rec.source_type
										})}
									</span>
								</div>
								{#if rec.grant_access_url}
									<a
										href={rec.grant_access_url}
										target="_blank"
										rel="noopener noreferrer"
										class="text-blue-600 hover:underline text-xs"
									>
										{$i18n.t('Grant access')} ↗
									</a>
								{/if}
							</div>
						{/each}
						{#if validationResult.recommendations.length > 5}
							<div class="text-sm text-gray-500">
								{$i18n.t('And {{count}} more...', {
									count: validationResult.recommendations.length - 5
								})}
							</div>
						{/if}
					</div>
				</div>
			{/if}

			<!-- Summary -->
			<div class="mb-6 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg text-sm">
				{#if strictMode}
					<p>
						{$i18n.t('Sharing to {{count}} users with source access.', { count: canShareCount })}
					</p>
					{#if cannotShareCount > 0}
						<p class="text-gray-500 mt-1">
							{$i18n.t('{{count}} users excluded - you can reshare once they have access.', {
								count: cannotShareCount
							})}
						</p>
					{/if}
				{:else}
					<p class="text-yellow-700 dark:text-yellow-300">
						{$i18n.t(
							'Warning: {{count}} users don\'t have access to all source documents. They will see limited content.',
							{ count: cannotShareCount }
						)}
					</p>
				{/if}
				<p class="text-gray-500 mt-2">
					{$i18n.t('Note: Users will lose access if their source permissions are revoked.')}
				</p>
			</div>
		{/if}

		<!-- Actions -->
		<div class="flex justify-end gap-3">
			<button
				class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
				on:click={handleCancel}
			>
				{$i18n.t('Cancel')}
			</button>
			<button
				class="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg"
				on:click={handleConfirm}
			>
				{#if strictMode && cannotShareCount > 0}
					{$i18n.t('Share to {{count}} users', { count: canShareCount })}
				{:else}
					{$i18n.t('Share to all {{count}} users', { count: totalCount })}
				{/if}
			</button>
		</div>
	</div>
</Modal>
```

#### 2. Source-Aware Access Control Wrapper (NEW FILE - No upstream changes)

**File**: `src/lib/components/workspace/common/SourceAwareAccessControl.svelte` (new)

This wrapper component adds source permission validation without modifying the original `AccessControl.svelte`:

```svelte
<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import AccessControl from './AccessControl.svelte';
	import ShareConfirmationModal from './ShareConfirmationModal.svelte';
	import {
		validateKnowledgeShare,
		getUsersReadyForAccess,
		type ShareValidationResult,
		type UserAccessStatus
	} from '$lib/apis/knowledge/permissions';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UserCircleSolid from '$lib/components/icons/UserCircleSolid.svelte';

	const i18n = getContext('i18n');

	// Props passed through to AccessControl
	export let onChange: Function = () => {};
	export let accessRoles = ['read'];
	export let accessControl = {};
	export let share = true;
	export let sharePublic = true;

	// Source permission props
	export let knowledgeId: string | null = null;
	export let knowledgeName: string = 'Knowledge Base';
	export let strictSourcePermissions = true;

	// Validation state
	let validationResult: ShareValidationResult | null = null;
	let showConfirmModal = false;
	let pendingAccessControl: any = null;
	let validating = false;

	// Users ready to add
	let usersReadyForAccess: UserAccessStatus[] = [];
	let loadingUsersReady = false;

	async function validateAndShare(newAccessControl: any) {
		if (!knowledgeId) {
			onChange(newAccessControl);
			return;
		}

		validating = true;
		try {
			const userIds = newAccessControl?.read?.user_ids ?? [];
			const groupIds = newAccessControl?.read?.group_ids ?? [];

			validationResult = await validateKnowledgeShare(
				localStorage.token,
				knowledgeId,
				userIds,
				groupIds
			);

			if (validationResult?.source_restricted && !validationResult?.can_share) {
				pendingAccessControl = newAccessControl;
				showConfirmModal = true;
			} else {
				onChange(newAccessControl);
			}
		} catch (err) {
			console.error('Validation failed:', err);
			onChange(newAccessControl);
		} finally {
			validating = false;
		}
	}

	function handleConfirmShare(event: CustomEvent) {
		const { shareToAll } = event.detail;

		if (strictSourcePermissions && validationResult && !shareToAll) {
			const allowedUserIds = new Set(validationResult.can_share_to_users);
			const filteredAccessControl = {
				...pendingAccessControl,
				read: {
					...pendingAccessControl.read,
					user_ids: (pendingAccessControl.read?.user_ids ?? []).filter((id: string) =>
						allowedUserIds.has(id)
					)
				}
			};
			onChange(filteredAccessControl);
		} else {
			onChange(pendingAccessControl);
		}

		showConfirmModal = false;
		pendingAccessControl = null;
		validationResult = null;
	}

	function handleCancelShare() {
		showConfirmModal = false;
		pendingAccessControl = null;
		validationResult = null;
	}

	async function loadUsersReadyForAccess() {
		if (!knowledgeId) return;

		loadingUsersReady = true;
		try {
			usersReadyForAccess = await getUsersReadyForAccess(localStorage.token, knowledgeId);
		} catch (err) {
			console.error('Failed to load users ready for access:', err);
		} finally {
			loadingUsersReady = false;
		}
	}

	function addUserToAccess(userId: string) {
		const newAccessControl = {
			...accessControl,
			read: {
				...(accessControl?.read ?? {}),
				user_ids: [...(accessControl?.read?.user_ids ?? []), userId]
			}
		};
		onChange(newAccessControl);
		usersReadyForAccess = usersReadyForAccess.filter((u) => u.user_id !== userId);
	}

	onMount(async () => {
		await loadUsersReadyForAccess();
	});
</script>

<!-- Original AccessControl with intercepted onChange -->
<AccessControl
	{accessRoles}
	{accessControl}
	{share}
	{sharePublic}
	onChange={validateAndShare}
/>

<!-- Users Ready to Add Section -->
{#if knowledgeId && usersReadyForAccess.length > 0}
	<div class="mt-4">
		<div class="flex justify-between mb-2.5">
			<div class="text-xs font-medium text-gray-500">
				{$i18n.t('Ready to Add')}
			</div>
			<Tooltip content={$i18n.t("Users with source access who haven't been granted KB access")}>
				<svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
					/>
				</svg>
			</Tooltip>
		</div>

		<div class="flex flex-col gap-1.5 px-0.5 mx-0.5">
			{#each usersReadyForAccess.slice(0, 5) as user}
				<div class="flex items-center justify-between text-sm py-1">
					<div class="flex items-center gap-2">
						<UserCircleSolid className="w-5 h-5 text-gray-400" />
						<div>
							<span class="font-medium">{user.user_name}</span>
							<span class="text-xs text-gray-500 ml-1">{user.user_email}</span>
						</div>
					</div>
					<button
						class="px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded"
						on:click={() => addUserToAccess(user.user_id)}
					>
						{$i18n.t('Add')}
					</button>
				</div>
			{/each}
			{#if usersReadyForAccess.length > 5}
				<div class="text-xs text-gray-500">
					{$i18n.t('And {{count}} more...', { count: usersReadyForAccess.length - 5 })}
				</div>
			{/if}
		</div>
	</div>
{/if}

<!-- Confirmation Modal -->
<ShareConfirmationModal
	bind:show={showConfirmModal}
	{validationResult}
	strictMode={strictSourcePermissions}
	targetName={knowledgeName}
	on:confirm={handleConfirmShare}
	on:cancel={handleCancelShare}
/>
```

#### 3. Update Knowledge Components to Use Wrapper

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

Replace `AccessControl` with `SourceAwareAccessControl` where source validation is needed:

```svelte
<!-- Change import -->
import SourceAwareAccessControl from '$lib/components/workspace/common/SourceAwareAccessControl.svelte';

<!-- Use wrapper component -->
<SourceAwareAccessControl
	bind:accessControl={knowledge.access_control}
	knowledgeId={knowledge.id}
	knowledgeName={knowledge.name}
	strictSourcePermissions={$config?.strictSourcePermissions ?? true}
	onChange={async (value) => {
		// existing onChange logic
	}}
/>
```

**Note**: Other components that use `AccessControl` but don't need source validation continue to use the original component unchanged.

#### 4. Add Config to Frontend

**File**: `src/lib/stores/index.ts`

Add to the config type (around line 50):
```typescript
strictSourcePermissions?: boolean;
```

**File**: `src/lib/apis/configs/index.ts`

Add to the config response type if not already present.

### Success Criteria

#### Automated Verification:
- [x] Type checking passes: `npm run check` (pre-existing errors only)
- [x] Frontend linting: `npm run lint:frontend` (pre-existing errors only)
- [x] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] Sharing OneDrive KB to user without access shows confirmation modal
- [ ] Modal shows correct user counts
- [ ] "Grant access" links work (open OneDrive)
- [ ] Strict mode: excluded users don't receive access
- [ ] Lenient mode: warning shown but all users receive access

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Real-Time Access Enforcement

### Overview

Enforce source permissions at retrieval time, not just at share time. Users who lose source access should immediately lose KB access.

**Note**: This is a **safety net/precaution**. If OneDrive background sync is working properly and permissions are synced in real-time, users should rarely hit access denial here. However, this catches edge cases where:
- Sync hasn't run yet after permission change
- Sync failed or is delayed
- Manual permission changes in OneDrive

### Changes Required

#### 1. Permission Enforcement Service (NEW FILE)

**File**: `backend/open_webui/services/permissions/enforcement.py` (new)

```python
"""
Real-Time Permission Enforcement

Checks source permissions at access time, not just share time.
Ensures users who lose source access immediately lose KB access.
"""

import logging
from typing import Optional, List
from pydantic import BaseModel

from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files
from open_webui.utils.access_control import has_access
from open_webui.services.permissions.registry import PermissionProviderRegistry

log = logging.getLogger(__name__)


class AccessDenialReason(BaseModel):
    """Details about why access was denied."""

    reason: str  # "no_kb_access", "source_access_revoked", "source_check_failed"
    message: str
    source_type: Optional[str] = None
    inaccessible_count: int = 0
    grant_access_url: Optional[str] = None


class KnowledgeAccessResult(BaseModel):
    """Result of checking knowledge base access."""

    allowed: bool
    denial: Optional[AccessDenialReason] = None
    accessible_file_ids: List[str] = []  # Files the user can access
    inaccessible_file_ids: List[str] = []  # Files the user cannot access


async def check_knowledge_access(
    user_id: str,
    knowledge_id: str,
    strict_mode: bool = True,
) -> KnowledgeAccessResult:
    """
    Check if user has access to a knowledge base, including source permissions.

    Args:
        user_id: User ID to check
        knowledge_id: Knowledge base ID
        strict_mode: If True, any inaccessible source file blocks all access.
                     If False, allows partial access.

    Returns:
        KnowledgeAccessResult with access status and details
    """
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        return KnowledgeAccessResult(
            allowed=False,
            denial=AccessDenialReason(
                reason="not_found",
                message="Knowledge base not found.",
            ),
        )

    # Check standard Open WebUI access control
    is_owner = knowledge.user_id == user_id
    has_kb_access = is_owner or has_access(user_id, "read", knowledge.access_control)

    if not has_kb_access:
        return KnowledgeAccessResult(
            allowed=False,
            denial=AccessDenialReason(
                reason="no_kb_access",
                message="You don't have access to this knowledge base.",
            ),
        )

    # Get files and check source permissions
    file_ids = knowledge.data.get("file_ids", []) if knowledge.data else []
    if not file_ids:
        return KnowledgeAccessResult(allowed=True, accessible_file_ids=[])

    accessible_files = []
    inaccessible_files = []
    denial_info = None

    for file_id in file_ids:
        file = Files.get_file_by_id(file_id)
        if not file:
            continue

        source = file.meta.get("source", "local") if file.meta else "local"
        if source == "local":
            accessible_files.append(file_id)
            continue

        provider = PermissionProviderRegistry.get_provider(source)
        if not provider:
            # No provider = no source restrictions
            accessible_files.append(file_id)
            continue

        # Check source permission
        try:
            result = await provider.check_user_access(
                user_id, [file_id], "read", use_cache=False
            )
            if result.has_access:
                accessible_files.append(file_id)
            else:
                inaccessible_files.append(file_id)
                if denial_info is None:
                    denial_info = AccessDenialReason(
                        reason="source_access_revoked",
                        message=f"Your access to {source.title()} documents has been revoked.",
                        source_type=source,
                        grant_access_url=result.grant_access_url,
                    )
        except Exception as e:
            log.warning(f"Source permission check failed for {file_id}: {e}")
            if strict_mode:
                inaccessible_files.append(file_id)

    if inaccessible_files:
        if denial_info:
            denial_info.inaccessible_count = len(inaccessible_files)

        if strict_mode:
            return KnowledgeAccessResult(
                allowed=False,
                denial=denial_info,
                accessible_file_ids=accessible_files,
                inaccessible_file_ids=inaccessible_files,
            )

    return KnowledgeAccessResult(
        allowed=True,
        accessible_file_ids=accessible_files,
        inaccessible_file_ids=inaccessible_files,
    )


async def filter_accessible_files(
    user_id: str,
    file_ids: List[str],
) -> List[str]:
    """
    Filter a list of file IDs to only those the user can access.

    Used for retrieval to ensure users only see files they have source access to.
    """
    accessible = []

    for file_id in file_ids:
        file = Files.get_file_by_id(file_id)
        if not file:
            continue

        source = file.meta.get("source", "local") if file.meta else "local"
        if source == "local":
            accessible.append(file_id)
            continue

        provider = PermissionProviderRegistry.get_provider(source)
        if not provider:
            accessible.append(file_id)
            continue

        try:
            result = await provider.check_user_access(user_id, [file_id], "read")
            if result.has_access:
                accessible.append(file_id)
        except Exception as e:
            log.warning(f"Permission check failed for {file_id}: {e}")
            # Fail closed - don't include file if check fails

    return accessible
```

#### 2. Retrieval Permission Filter (NEW FILE - Minimal upstream change)

Create a wrapper module that can be imported with a single line change in retrieval.py:

**File**: `backend/open_webui/services/permissions/retrieval_filter.py` (new)

```python
"""
Retrieval Permission Filter

Provides file filtering for retrieval operations based on source permissions.
Import this module in retrieval.py to add permission filtering.
"""

import logging
from typing import List, Optional

from open_webui.services.permissions.enforcement import filter_accessible_files

log = logging.getLogger(__name__)


async def filter_retrieval_files(
    user_id: Optional[str],
    file_ids: List[str],
) -> List[str]:
    """
    Filter file IDs for retrieval based on source permissions.

    This is the single function to call from retrieval.py.
    Returns only files the user has source access to.
    """
    if not file_ids or not user_id:
        return file_ids

    try:
        return await filter_accessible_files(user_id, file_ids)
    except Exception as e:
        log.warning(f"Permission filter failed, returning original files: {e}")
        # Fail open to avoid breaking retrieval
        return file_ids
```

**File**: `backend/open_webui/routers/retrieval.py`

Add single import at top (minimal change):
```python
from open_webui.services.permissions.retrieval_filter import filter_retrieval_files
```

In the query endpoint (around line 800-900), add single line before retrieving:
```python
# Filter by source permissions (single line addition)
if file_ids and user:
    file_ids = await filter_retrieval_files(user.id, file_ids)
```

**Upstream impact**: Only 2 lines added to retrieval.py (import + function call).

#### 3. Add Access Check to Knowledge Get Endpoint

**File**: `backend/open_webui/routers/knowledge.py`

Update the get endpoint to check source permissions:

```python
from open_webui.services.permissions.enforcement import check_knowledge_access

@router.get("/{id}", response_model=Optional[KnowledgeFilesResponse])
async def get_knowledge_by_id(
    id: str,
    request: Request,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)

    if knowledge:
        # Check both KB access and source permissions
        strict_mode = request.app.state.config.STRICT_SOURCE_PERMISSIONS
        access_result = await check_knowledge_access(user.id, id, strict_mode)

        if not access_result.allowed:
            if access_result.denial and access_result.denial.reason == "source_access_revoked":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=access_result.denial.message,
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

        # Admin always has access
        if user.role == "admin":
            return KnowledgeFilesResponse(
                **knowledge.model_dump(),
                write_access=True,
            )

        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            write_access=(
                user.id == knowledge.user_id
                or has_access(user.id, "write", knowledge.access_control)
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ERROR_MESSAGES.NOT_FOUND,
    )
```

### Success Criteria

#### Automated Verification:
- [x] Type checking passes: `npm run check` (pre-existing errors only)
- [x] Backend linting: `npm run lint:backend` (pre-existing warnings only)
- [ ] App starts: `open-webui dev`

#### Manual Verification:
- [ ] User with KB access but revoked OneDrive access gets 403
- [ ] Error message explains source access was revoked
- [ ] Retrieval only returns files user has source access to
- [ ] Admin still has access regardless of source permissions

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 5: Translations & Polish

### Overview

Add i18n translations for all new strings and polish the UI components.

**Note**: The "Ready to Add" users section is already included in the `SourceAwareAccessControl.svelte` wrapper component from Phase 3.

### Changes Required

#### 1. Add Translation Strings

**File**: `src/lib/i18n/locales/en-US/translation.json`

Add new strings:
```json
{
  "Confirm Sharing": "Confirm Sharing",
  "Sharing \"{{name}}\"": "Sharing \"{{name}}\"",
  "{{count}} users with full source access": "{{count}} users with full source access",
  "Have permissions for all source documents": "Have permissions for all source documents",
  "{{count}} users will NOT receive access": "{{count}} users will NOT receive access",
  "{{count}} users missing source access": "{{count}} users missing source access",
  "Missing: {{count}} {{source}} files": "Missing: {{count}} {{source}} files",
  "Grant access": "Grant access",
  "And {{count}} more...": "And {{count}} more...",
  "Sharing to {{count}} users with source access.": "Sharing to {{count}} users with source access.",
  "{{count}} users excluded - you can reshare once they have access.": "{{count}} users excluded - you can reshare once they have access.",
  "Warning: {{count}} users don't have access to all source documents. They will see limited content.": "Warning: {{count}} users don't have access to all source documents. They will see limited content.",
  "Note: Users will lose access if their source permissions are revoked.": "Note: Users will lose access if their source permissions are revoked.",
  "Share to {{count}} users": "Share to {{count}} users",
  "Share to all {{count}} users": "Share to all {{count}} users",
  "Ready to Add": "Ready to Add",
  "Users with source access who haven't been granted KB access": "Users with source access who haven't been granted KB access",
  "Add": "Add",
  "Some knowledge bases attached to this model are not accessible to you.": "Some knowledge bases attached to this model are not accessible to you.",
  "Your access to the source documents has been revoked.": "Your access to the source documents has been revoked."
}
```

**File**: `src/lib/i18n/locales/nl-NL/translation.json`

Add Dutch translations:
```json
{
  "Confirm Sharing": "Delen bevestigen",
  "Sharing \"{{name}}\"": "\"{{name}}\" delen",
  "{{count}} users with full source access": "{{count}} gebruikers met volledige brontoegang",
  "Have permissions for all source documents": "Hebben rechten voor alle brondocumenten",
  "{{count}} users will NOT receive access": "{{count}} gebruikers krijgen GEEN toegang",
  "{{count}} users missing source access": "{{count}} gebruikers missen brontoegang",
  "Missing: {{count}} {{source}} files": "Ontbrekend: {{count}} {{source}} bestanden",
  "Grant access": "Toegang verlenen",
  "And {{count}} more...": "En {{count}} meer...",
  "Sharing to {{count}} users with source access.": "Delen met {{count}} gebruikers met brontoegang.",
  "{{count}} users excluded - you can reshare once they have access.": "{{count}} gebruikers uitgesloten - je kunt opnieuw delen zodra ze toegang hebben.",
  "Warning: {{count}} users don't have access to all source documents. They will see limited content.": "Waarschuwing: {{count}} gebruikers hebben geen toegang tot alle brondocumenten. Ze zien beperkte inhoud.",
  "Note: Users will lose access if their source permissions are revoked.": "Let op: Gebruikers verliezen toegang als hun bronrechten worden ingetrokken.",
  "Share to {{count}} users": "Delen met {{count}} gebruikers",
  "Share to all {{count}} users": "Delen met alle {{count}} gebruikers",
  "Ready to Add": "Klaar om toe te voegen",
  "Users with source access who haven't been granted KB access": "Gebruikers met brontoegang die nog geen KB-toegang hebben",
  "Add": "Toevoegen",
  "Some knowledge bases attached to this model are not accessible to you.": "Sommige kennisbanken gekoppeld aan dit model zijn niet toegankelijk voor jou.",
  "Your access to the source documents has been revoked.": "Je toegang tot de brondocumenten is ingetrokken."
}
```

### Success Criteria

#### Automated Verification:
- [x] Type checking passes: `npm run check` (pre-existing errors in RichTextInput only)
- [x] Frontend linting: `npm run lint:frontend` (pre-existing errors in swagger-ui-bundle.js only)
- [x] Frontend builds: `npm run build`
- [x] i18n parse: `npm run i18n:parse` (no missing keys)

#### Manual Verification:
- [ ] All new UI text appears correctly in English
- [ ] All new UI text appears correctly in Dutch
- [ ] No untranslated strings visible in UI

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 6: Model Integration

### Overview

Validate and filter knowledge bases when users interact with models that have attached KBs.

### Changes Required

#### 1. Model Knowledge Access Filter

**File**: `backend/open_webui/services/permissions/enforcement.py`

Add function:
```python
async def get_accessible_model_knowledge(
    model_id: str,
    user_id: str,
    strict_mode: bool = True,
) -> tuple[List[str], List[dict]]:
    """
    Get accessible knowledge bases for a model.

    Returns:
        Tuple of (accessible_kb_ids, inaccessible_warnings)
    """
    from open_webui.models.models import Models

    model = Models.get_model_by_id(model_id)
    if not model or not model.meta:
        return [], []

    knowledge_refs = model.meta.get("knowledge", [])
    if not knowledge_refs:
        return [], []

    accessible = []
    warnings = []

    for kb_ref in knowledge_refs:
        kb_id = kb_ref.get("collection_name") or kb_ref.get("id")
        if not kb_id:
            continue

        result = await check_knowledge_access(user_id, kb_id, strict_mode)

        if result.allowed:
            accessible.append(kb_id)
        else:
            warnings.append({
                "knowledge_id": kb_id,
                "reason": result.denial.reason if result.denial else "unknown",
                "message": result.denial.message if result.denial else "Access denied",
            })

    return accessible, warnings
```

#### 2. Update Chat Retrieval

**File**: `backend/open_webui/routers/chats.py` or wherever model-based retrieval happens

Add filtering of model knowledge bases based on user access. The exact location depends on how retrieval is triggered for models.

#### 3. Frontend Warning in Chat

**File**: `src/lib/components/chat/Chat.svelte` (or similar)

Add warning when model has inaccessible KBs:
```svelte
{#if modelAccessWarnings.length > 0}
	<div class="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
		<div class="flex items-center gap-2 text-yellow-800 dark:text-yellow-200">
			<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
				/>
			</svg>
			<span class="text-sm">
				{$i18n.t('Some knowledge bases attached to this model are not accessible to you.')}
			</span>
		</div>
	</div>
{/if}
```

### Success Criteria

#### Automated Verification:
- [x] Type checking passes: `npm run check` (pre-existing errors in RichTextInput only)
- [x] Backend linting: `npm run lint:backend` (pre-existing duplicate code warnings only)
- [x] Frontend linting: `npm run lint:frontend` (pre-existing errors only)
- [x] Frontend builds: `npm run build`
- [x] Backend import works: `get_accessible_model_knowledge` imports successfully

#### Manual Verification:
- [ ] Model with inaccessible KB shows warning in chat
- [ ] Retrieval only uses KBs the user can access
- [ ] Model still works (uses accessible KBs only)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to finalization.

---

## Testing Strategy

### Unit Tests

**File**: `backend/open_webui/test/services/permissions/test_provider.py` (new)

```python
import pytest
from open_webui.services.permissions.provider import PermissionCheckResult
from open_webui.services.permissions.registry import PermissionProviderRegistry
from open_webui.services.permissions.providers.onedrive import OneDrivePermissionProvider


def test_registry_register_and_get():
    PermissionProviderRegistry.clear()
    provider = OneDrivePermissionProvider()
    PermissionProviderRegistry.register(provider)

    assert PermissionProviderRegistry.get_provider("onedrive") is provider
    assert PermissionProviderRegistry.get_provider("unknown") is None


def test_registry_has_provider():
    PermissionProviderRegistry.clear()
    provider = OneDrivePermissionProvider()
    PermissionProviderRegistry.register(provider)

    assert PermissionProviderRegistry.has_provider("onedrive")
    assert not PermissionProviderRegistry.has_provider("sharepoint")


def test_permission_check_result_model():
    result = PermissionCheckResult(
        has_access=False,
        accessible_items=["file1"],
        inaccessible_items=["file2", "file3"],
        message="2 files not accessible",
    )
    assert not result.has_access
    assert len(result.inaccessible_items) == 2
```

### Integration Tests

Test the full flow:
1. Create OneDrive-synced KB
2. Share to user without OneDrive access
3. Verify validation returns correct results
4. Verify user is blocked from accessing KB

### Manual Testing Steps

1. **Setup**: Create KB with OneDrive folder sync
2. **Validation**: Try sharing to user without OneDrive access
3. **Confirmation Modal**: Verify modal shows correct counts
4. **Strict Mode**: Verify excluded users don't get access
5. **Access Revocation**: Remove user from OneDrive, verify KB access lost
6. **Model**: Create model with restricted KB, verify warning in chat

---

## Performance Considerations

1. **Permission Caching**: File-level `permitted_emails` cached in metadata during sync
2. **Bulk Checks**: `check_bulk_access` reduces API calls when validating groups
3. **Lazy Provider Loading**: Providers only loaded when needed
4. **Fail-Open for UX**: If validation fails, proceed (log warning) rather than blocking

---

## Migration Notes

No database migrations required. Changes are:
1. New config setting (PersistentConfig handles storage)
2. New file metadata fields (added during sync, backwards compatible)
3. New API endpoints (additive)
4. New services (additive)

Existing OneDrive-synced KBs will work without changes. New permission fields will be populated on next sync.

---

## Follow-Up: Background Sync Integration

This plan creates the Permission Provider infrastructure that **OneDrive Background Sync** will use. After completing this plan, the background sync feature should:

1. **Use `permitted_emails` storage** established in Phase 1
2. **Call permission providers** to update cached permissions
3. **Trigger real-time updates** when source permissions change

The background sync becomes a producer that writes to the permission cache; this plan creates the consumers that read from it.

**Recommended sequencing**:
1. Complete Phases 1-3 of this plan (foundation + validation + UI)
2. Implement OneDrive Background Sync (uses permission infrastructure)
3. Complete Phases 4-6 (real-time enforcement becomes more of a safety net)

---

## References

- Research document: `thoughts/shared/research/2026-01-28-federated-source-access-control-architecture.md`
- OneDrive sync: `backend/open_webui/services/onedrive/sync_worker.py`
- Access control: `backend/open_webui/utils/access_control.py`
- Storage provider pattern: `backend/open_webui/storage/provider.py`
- Vector DB factory pattern: `backend/open_webui/retrieval/vector/factory.py`

---

## Comprehensive Manual Testing Steps (Phases 1-6)

This section provides a detailed test plan for verifying the complete federated source access control implementation.

### Prerequisites

1. **Two test users**: User A (owner) and User B (test subject)
2. **OneDrive account**: With a folder containing test documents
3. **Open WebUI instance**: Running with `STRICT_SOURCE_PERMISSIONS=true` (default)
4. **OneDrive permissions**: User A has access to OneDrive folder, User B does NOT have access initially

### Phase 1: Foundation - Permission Provider Infrastructure

#### Test 1.1: Config Loading
```bash
# Verify config loads correctly
python -c "from open_webui.config import STRICT_SOURCE_PERMISSIONS; print(f'STRICT_SOURCE_PERMISSIONS: {STRICT_SOURCE_PERMISSIONS.value}')"
```
- [ ] Should print `STRICT_SOURCE_PERMISSIONS: True`

#### Test 1.2: Provider Registration
```bash
# Verify provider registers on startup
python -c "
from open_webui.services.permissions.registry import PermissionProviderRegistry
from open_webui.services.permissions.providers.onedrive import OneDrivePermissionProvider
PermissionProviderRegistry.register(OneDrivePermissionProvider())
print(f'OneDrive provider: {PermissionProviderRegistry.get_provider(\"onedrive\")}')"
```
- [ ] Should print provider object reference

#### Test 1.3: OneDrive Sync with Permitted Emails
1. As User A, create a new Knowledge Base
2. Connect it to an OneDrive folder via sync
3. Wait for sync to complete
4. Check file metadata in database:
   - [ ] File metadata contains `source: "onedrive"`
   - [ ] File metadata contains `permitted_emails` array
   - [ ] Knowledge meta contains `onedrive_sync.permitted_emails`

### Phase 1b: Reverse Validation - Adding Restricted Files to Shared KBs

#### Test 1b.1: Adding OneDrive Files to Public KB (Strict Mode)
1. As User A, create a public Knowledge Base (`access_control = None`)
2. Try to add OneDrive-synced files to this KB
3. Verify:
   - [ ] Conflict modal appears
   - [ ] Shows "This knowledge base is public, but the files you are adding have restricted access"
   - [ ] "Make Private" option is available
   - [ ] "Cancel" option is available
   - [ ] Clicking "Make Private" sets KB to private then adds files

#### Test 1b.2: OneDrive Sync Detects Public KB
1. Create a public Knowledge Base
2. Configure OneDrive sync for this KB
3. Verify:
   - [ ] Sync detects public KB conflict
   - [ ] Warning is shown about restricted access
   - [ ] Option to make KB private is offered

### Phase 2: Sharing Validation Service & API

#### Test 2.1: Validate Share API Endpoint
```bash
# Test the validation endpoint (replace {id} and {token})
curl -X POST "http://localhost:8080/api/v1/knowledge/{id}/validate-share" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"user_ids": ["user_b_id"], "group_ids": []}'
```
- [ ] For user without OneDrive access: Returns `can_share: false` with `cannot_share_to_users` containing the user
- [ ] For user with OneDrive access: Returns `can_share: true`
- [ ] Response includes `recommendations` with grant access URLs

#### Test 2.2: Users Ready for Access API
```bash
curl "http://localhost:8080/api/v1/knowledge/{id}/users-ready-for-access" \
  -H "Authorization: Bearer {token}"
```
- [ ] Returns users who have OneDrive access but not KB access

### Phase 3: Sharing Validation UI

#### Test 3.1: Share Confirmation Modal
1. As User A, open the Knowledge Base settings
2. Go to Access Control section
3. Try to share with User B (who lacks OneDrive access)
4. Verify:
   - [ ] Confirmation modal appears
   - [ ] Shows "{{count}} users will NOT receive access" in strict mode
   - [ ] Shows user details with email
   - [ ] Shows "Grant access" link to OneDrive
   - [ ] "Grant access" link opens OneDrive sharing page

#### Test 3.2: Ready to Add Users Section
1. Grant User B access to OneDrive folder (outside Open WebUI)
2. Wait for permission sync or trigger manual sync
3. Open KB Access Control
4. Verify:
   - [ ] "Ready to Add" section appears
   - [ ] User B is listed with "Add" button
   - [ ] Clicking "Add" grants KB access to User B

#### Test 3.3: Strict vs Lenient Mode
1. Set `STRICT_SOURCE_PERMISSIONS=false`
2. Repeat Test 3.1
3. Verify:
   - [ ] Modal shows "Warning: users don't have access to all source documents"
   - [ ] "Continue Anyway" option is available
   - [ ] Clicking "Continue Anyway" shares with all users despite missing source access

### Phase 4: Real-Time Access Enforcement

#### Test 4.1: Access Revocation
1. User A shares KB with User B (who has OneDrive access)
2. User B can access the KB and its files
3. Remove User B's access from OneDrive folder
4. Trigger permission sync
5. Verify:
   - [ ] User B gets 403 error when accessing KB
   - [ ] Error message: "Your access to the source documents has been revoked"

#### Test 4.2: Retrieval Filtering
1. User A has KB with mixed files (local + OneDrive)
2. User B has access to local files but not OneDrive files
3. User B uses RAG/retrieval on this KB
4. Verify:
   - [ ] Retrieval only returns chunks from local files
   - [ ] OneDrive file chunks are filtered out

#### Test 4.3: Admin Override
1. Admin user accesses KB with restricted OneDrive files
2. Verify:
   - [ ] Admin can access all files regardless of source permissions
   - [ ] No access denied errors for admin

### Phase 5: Translations & Polish

#### Test 5.1: English Translations
1. Set browser language to English
2. Navigate through all new UI elements
3. Verify:
   - [ ] Share confirmation modal text appears correctly
   - [ ] Ready to Add section labels appear correctly
   - [ ] Warning toasts appear correctly
   - [ ] Error messages appear correctly

#### Test 5.2: Dutch Translations
1. Set browser language to Dutch
2. Repeat Test 5.1
3. Verify:
   - [ ] All new strings are translated
   - [ ] No untranslated English text visible

### Phase 6: Model Integration

#### Test 6.1: Model with Inaccessible KB Warning
1. User A creates a model with a restricted OneDrive KB attached
2. User B (without OneDrive access) tries to chat with this model
3. Verify:
   - [ ] Warning toast appears: "Some knowledge bases attached to this model are not accessible to you."
   - [ ] Chat still works (uses only accessible KBs)

#### Test 6.2: Model Retrieval Filtering
1. User A creates model with multiple KBs (some accessible, some not)
2. User B chats with the model and asks questions about KB content
3. Verify:
   - [ ] Only content from accessible KBs is retrieved
   - [ ] Inaccessible KB content is not returned in responses

#### Test 6.3: Status History Shows Warning
1. After Test 6.1, check the message status history
2. Verify:
   - [ ] Status shows `action: "model_knowledge_warning"`
   - [ ] Warnings array contains details about inaccessible KBs

### End-to-End Scenarios

#### Scenario A: Full Sharing Flow
1. User A creates KB with OneDrive files
2. User A tries to share with User B (no OneDrive access)
3. Modal shows warning → User A clicks "Grant access" link
4. User A grants User B OneDrive access
5. User A returns to Open WebUI, sees User B in "Ready to Add"
6. User A adds User B
7. User B can now fully access the KB
- [ ] All steps complete successfully

#### Scenario B: Access Revocation Flow
1. User B has full access to KB (OneDrive + KB permissions)
2. Admin removes User B from OneDrive folder
3. Permission sync runs (or wait for background sync)
4. User B tries to access KB
5. User B gets access denied with clear message
- [ ] All steps complete successfully

#### Scenario C: Model Degradation Flow
1. Model has 2 KBs: KB-Public (local files) and KB-Restricted (OneDrive)
2. User B only has access to KB-Public
3. User B chats with model about content from both KBs
4. Response includes content from KB-Public only
5. Warning shown about limited access
- [ ] Model works with partial KB access

### Cleanup

After testing:
- [ ] Remove test users if not needed
- [ ] Reset `STRICT_SOURCE_PERMISSIONS` to desired value
- [ ] Remove test KBs and files
- [ ] Revoke any test OneDrive permissions
