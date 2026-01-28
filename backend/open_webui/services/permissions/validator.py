"""
Sharing Validation Service

Validates sharing operations against source permissions.
Returns detailed results for UI feedback.

Note: Full implementation in Phase 2.
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


class FileAdditionConflict(BaseModel):
    """Conflict when adding restricted files to shared KB."""

    has_conflict: bool
    kb_is_public: bool = False
    users_without_access: List[str] = []  # User IDs who would lose access to new files
    user_details: List[SharingRecommendation] = []  # Details for UI
    source_type: str = ""
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

        # If public KB, any source-restricted files create a conflict
        if kb_is_public:
            # Check if any of the files being added have source restrictions
            for file_id in file_ids:
                file = Files.get_file_by_id(file_id)
                if not file or not file.meta:
                    continue

                source = file.meta.get("source", "local")
                if source != "local":
                    provider = PermissionProviderRegistry.get_provider(source)
                    if provider:
                        grant_url = provider.get_grant_access_url(file_id)
                        return FileAdditionConflict(
                            has_conflict=True,
                            kb_is_public=True,
                            source_type=source,
                            grant_access_url=grant_url,
                        )

            return FileAdditionConflict(has_conflict=False)

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

        if not users_without_source_access:
            return FileAdditionConflict(has_conflict=False)

        # Build user details for UI
        user_details = []
        for user_id in users_without_source_access:
            user = Users.get_user_by_id(user_id)
            if user:
                user_details.append(
                    SharingRecommendation(
                        user_id=user_id,
                        user_name=user.name,
                        user_email=user.email,
                        source_type=source_type,
                        inaccessible_count=len(file_ids),
                        grant_access_url=grant_url,
                    )
                )

        return FileAdditionConflict(
            has_conflict=True,
            kb_is_public=False,
            users_without_access=list(users_without_source_access),
            user_details=user_details,
            source_type=source_type,
            grant_access_url=grant_url,
        )

    def _get_kb_users(self, knowledge) -> set:
        """Get all user IDs with access to the KB."""
        if knowledge.access_control is None:
            # Public KB - return empty set
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
