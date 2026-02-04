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
            member_ids = Groups.get_group_user_ids_by_id(group_id)
            if member_ids:
                kb_user_ids.update(member_ids)

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
