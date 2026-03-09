"""
User Archival Service

Collects user data for archival. Archives store chats in the native Open WebUI
export format, so they can be directly imported by another user.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)


@dataclass
class ArchiveData:
    """Container for archived user data"""
    user_profile: Dict[str, Any] = field(default_factory=dict)
    chats: List[Dict[str, Any]] = field(default_factory=list)
    archived_at: int = 0
    version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "archived_at": self.archived_at,
            "user_profile": self.user_profile,
            "chats": self.chats,
            "stats": {
                "chat_count": len(self.chats),
            }
        }

    def get_exportable_chats(self) -> List[Dict[str, Any]]:
        """
        Returns chats in the native Open WebUI export format.
        This format can be directly imported using Settings > Data Controls > Import Chats.
        """
        return self.chats


@dataclass
class ArchiveResult:
    """Result of archive operation"""
    success: bool = False
    archive_id: Optional[str] = None
    stats: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class ArchiveService:
    """Service for creating user archives"""

    @staticmethod
    def collect_user_data(user_id: str) -> ArchiveData:
        """
        Collect all user data for archival.
        Chats are stored in the native Open WebUI export format.
        """
        from open_webui.models.users import Users
        from open_webui.models.chats import Chats, ChatResponse

        data = ArchiveData()
        data.archived_at = int(time.time())

        # 1. User profile (for reference/metadata)
        try:
            user = Users.get_user_by_id(user_id)
            if user:
                data.user_profile = {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                    "profile_image_url": user.profile_image_url,
                    "created_at": user.created_at,
                    "updated_at": user.updated_at,
                }
        except Exception as e:
            log.error(f"Error collecting user profile: {e}")

        # 2. Chats in native export format (compatible with Import Chats)
        # Uses the same ChatResponse model as GET /api/v1/chats/all to ensure
        # format compatibility with Open WebUI's native export/import
        try:
            chats_response = Chats.get_chats_by_user_id(user_id)
            for chat in chats_response.items:
                # Convert via ChatResponse model - same as native /chats/all endpoint
                chat_response = ChatResponse(**chat.model_dump())
                data.chats.append(chat_response.model_dump())
        except Exception as e:
            log.error(f"Error collecting chats: {e}")

        return data

    @staticmethod
    def create_archive(
        user_id: str,
        archived_by: str,
        reason: str,
        retention_days: Optional[int] = None,
        never_delete: bool = False,
    ) -> ArchiveResult:
        """
        Create an archive of user data.

        Args:
            user_id: ID of user to archive
            archived_by: ID of admin creating the archive
            reason: Reason for archival (for compliance)
            retention_days: Days to retain (None = use default)
            never_delete: If True, archive is never auto-deleted
        """
        from open_webui.models.users import Users
        from open_webui.models.user_archives import UserArchives

        result = ArchiveResult()

        # Get user info
        user = Users.get_user_by_id(user_id)
        if not user:
            result.errors.append(f"User {user_id} not found")
            return result

        # Collect data
        try:
            data = ArchiveService.collect_user_data(user_id)
            result.stats = {
                "chats": len(data.chats),
            }
        except Exception as e:
            result.errors.append(f"Error collecting user data: {e}")
            return result

        # Create archive record
        try:
            archive = UserArchives.insert_archive(
                user_id=user_id,
                user_email=user.email,
                user_name=user.name,
                reason=reason,
                archived_by=archived_by,
                data=data.to_dict(),
                retention_days=retention_days,
                never_delete=never_delete,
            )
            if archive:
                result.success = True
                result.archive_id = archive.id
            else:
                result.errors.append("Failed to insert archive record")
        except Exception as e:
            result.errors.append(f"Error creating archive: {e}")

        return result

    @staticmethod
    def get_exportable_chats(archive_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Get chats from an archive in the native Open WebUI export format.
        This can be directly imported using Settings > Data Controls > Import Chats.

        Returns:
            List of chats in export format, or None if archive not found
        """
        from open_webui.models.user_archives import UserArchives

        archive = UserArchives.get_archive_by_id(archive_id)
        if not archive:
            return None

        data = archive.data
        return data.get("chats", [])

    @staticmethod
    def cleanup_expired_archives() -> Dict[str, int]:
        """
        Delete archives past their retention period.
        Called by background job.
        """
        from open_webui.models.user_archives import UserArchives

        stats = {"checked": 0, "deleted": 0, "errors": 0}

        expired = UserArchives.get_expired_archives()
        stats["checked"] = len(expired)

        for archive in expired:
            try:
                if UserArchives.delete_archive(archive.id):
                    stats["deleted"] += 1
                    log.info(f"Deleted expired archive {archive.id} for user {archive.user_email}")
                else:
                    stats["errors"] += 1
            except Exception as e:
                log.error(f"Error deleting archive {archive.id}: {e}")
                stats["errors"] += 1

        return stats
