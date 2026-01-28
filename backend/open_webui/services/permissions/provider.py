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
