"""Google Drive sync worker - Downloads and processes files from Google Drive folders."""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

from open_webui.services.google_drive.drive_client import (
    GoogleDriveClient,
    GOOGLE_WORKSPACE_EXPORT_MAP,
)
from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files
from open_webui.models.users import Users
from open_webui.config import (
    GOOGLE_DRIVE_MAX_FILES_PER_SYNC,
    GOOGLE_DRIVE_MAX_FILE_SIZE_MB,
)
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.services.deletion import DeletionService
from open_webui.services.sync.constants import (
    SyncErrorType,
    FailedFile,
    SUPPORTED_EXTENSIONS,
)
from open_webui.services.sync.base_worker import BaseSyncWorker

log = logging.getLogger(__name__)


class GoogleDriveSyncWorker(BaseSyncWorker):
    """Worker to sync Google Drive folder contents to a Knowledge base."""

    # ------------------------------------------------------------------
    # Abstract properties
    # ------------------------------------------------------------------

    @property
    def meta_key(self) -> str:
        return "google_drive_sync"

    @property
    def file_id_prefix(self) -> str:
        return "googledrive-"

    @property
    def event_prefix(self) -> str:
        return "googledrive"

    @property
    def internal_request_path(self) -> str:
        return "/internal/google-drive-sync"

    @property
    def max_files_config(self) -> int:
        return GOOGLE_DRIVE_MAX_FILES_PER_SYNC

    @property
    def source_clear_delta_keys(self) -> list[str]:
        return ["page_token", "folder_map"]

    # ------------------------------------------------------------------
    # Abstract methods - provider-specific implementations
    # ------------------------------------------------------------------

    def _create_client(self):
        return GoogleDriveClient(self.access_token, token_provider=self._token_provider)

    async def _close_client(self):
        if self._client:
            await self._client.close()

    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """Check if file is supported for processing."""
        mime_type = item.get("mimeType", "")

        # Google Workspace files (Docs, Sheets, Slides) are always supported
        if mime_type in GOOGLE_WORKSPACE_EXPORT_MAP:
            return True

        # Folders are not files
        if mime_type == "application/vnd.google-apps.folder":
            return False

        # Other Google apps types we can't export
        if mime_type.startswith("application/vnd.google-apps."):
            log.debug(f"Skipping unsupported Google Apps type: {mime_type}")
            return False

        name = item.get("name", "")
        ext = Path(name).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            log.debug(f"Skipping unsupported file type: {name}")
            return False

        size = item.get("size", 0)
        if size:
            size = int(size)
            max_size = GOOGLE_DRIVE_MAX_FILE_SIZE_MB * 1024 * 1024
            if size > max_size:
                log.warning(f"Skipping {name}: size {size} exceeds max {max_size}")
                return False

        return True

    async def _collect_folder_files(self, source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
        """Collect files from a folder using changes API or full listing."""
        page_token = source.get("page_token")

        if page_token:
            return await self._collect_folder_files_incremental(source, page_token)
        else:
            return await self._collect_folder_files_full(source)

    async def _collect_single_file(self, source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check if a single file needs syncing based on hash or modified time."""
        try:
            item = await self._client.get_file(source["item_id"])

            if not item:
                log.warning(f"File not found: {source['name']}")
                return None

            # For Workspace files, use modifiedTime as change indicator
            # For regular files, use md5Checksum
            if self._is_workspace_file(item):
                current_indicator = item.get("modifiedTime", "")
                stored_indicator = source.get("modified_time")
            else:
                current_indicator = item.get("md5Checksum", "")
                stored_indicator = source.get("content_hash")

            if current_indicator and current_indicator == stored_indicator:
                file_id = f"googledrive-{source['item_id']}"
                existing = Files.get_file_by_id(file_id)
                if existing and (existing.data or {}).get("status") == "completed":
                    log.info(f"File unchanged: {source['name']}")
                    return None
                else:
                    reason = "missing" if not existing else "not processed"
                    log.info(f"File {source['name']} indicator matches but record {reason}, " f"re-syncing")

            # Store new indicator for later save
            if self._is_workspace_file(item):
                source["modified_time"] = current_indicator
            else:
                source["content_hash"] = current_indicator

            effective_name = self._get_effective_filename(item)

            return {
                "item": item,
                "source_type": "file",
                "source_item_id": source["item_id"],
                "name": effective_name,
            }

        except Exception as e:
            log.error(f"Failed to check file {source['name']}: {e}")
            return None

    async def _download_file_content(self, file_info: Dict[str, Any]) -> bytes:
        """Download file content, using export for Workspace files."""
        item = file_info["item"]
        file_id = item["id"]
        mime_type = item.get("mimeType", "")

        if mime_type in GOOGLE_WORKSPACE_EXPORT_MAP:
            export_mime, _ = GOOGLE_WORKSPACE_EXPORT_MAP[mime_type]
            return await self._client.export_file(file_id, export_mime)
        else:
            return await self._client.download_file(file_id)

    def _get_provider_storage_headers(self, item_id: str) -> dict:
        return {
            "OpenWebUI-Source": "google_drive",
            "OpenWebUI-GoogleDrive-Item-Id": item_id,
        }

    def _get_provider_file_meta(
        self,
        item_id: str,
        source_item_id: Optional[str],
        relative_path: str,
        name: str,
        content_type: str,
        size: int,
        file_info: Optional[Dict[str, Any]] = None,
    ) -> dict:
        return {
            "name": name,
            "content_type": content_type,
            "size": size,
            "source": "google_drive",
            "google_drive_item_id": item_id,
            "source_item_id": source_item_id,
            "relative_path": relative_path,
            "last_synced_at": int(time.time()),
        }

    async def _sync_permissions(self) -> None:
        """Sync Google Drive folder permissions to Knowledge access_control."""
        folder_source = next((s for s in self.sources if s.get("type") == "folder"), None)
        if not folder_source:
            log.info("No folder sources, skipping permission sync")
            return

        try:
            permissions = await self._client.get_file_permissions(folder_source["item_id"])

            permitted_emails = set()
            for perm in permissions:
                # Google returns emailAddress directly on permission objects
                email = perm.get("emailAddress")
                if email:
                    permitted_emails.add(email.lower())

            log.info(f"Found {len(permitted_emails)} permitted emails from Google Drive")

            permitted_user_ids = []
            for email in permitted_emails:
                user = Users.get_user_by_email(email)
                if user:
                    permitted_user_ids.append(user.id)
                    log.debug(f"Mapped Google Drive permission for {email} to user {user.id}")

            if permitted_user_ids:
                if self.user_id not in permitted_user_ids:
                    permitted_user_ids.append(self.user_id)

                knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
                if knowledge:
                    from open_webui.models.knowledge import KnowledgeForm

                    # Build access_grants list (new upstream format)
                    access_grants = []
                    for user_id in permitted_user_ids:
                        access_grants.append(
                            {
                                "principal_type": "user",
                                "principal_id": user_id,
                                "permission": "read",
                            }
                        )
                    access_grants.append(
                        {
                            "principal_type": "user",
                            "principal_id": self.user_id,
                            "permission": "write",
                        }
                    )

                    Knowledges.update_knowledge_by_id(
                        self.knowledge_id,
                        KnowledgeForm(
                            name=knowledge.name,
                            description=knowledge.description,
                            type=knowledge.type,
                            access_grants=access_grants,
                        ),
                    )

                    log.info(
                        f"Updated access_control for {self.knowledge_id}: "
                        f"{len(permitted_user_ids)} users with read access"
                    )
            else:
                log.info(f"No matching users found for Google Drive permissions, " f"keeping default access_control")

        except Exception as e:
            log.warning(f"Failed to sync permissions: {e}")

    async def _verify_source_access(self, source: Dict[str, Any]) -> bool:
        """Verify the user can still access a Google Drive source."""
        import httpx

        item_id = source.get("item_id")
        source_type = source.get("type", "folder")

        try:
            item = await self._client.get_file(item_id)
            if item is None:
                return False
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                log.warning(f"User {self.user_id} lost access to {source_type} " f"{item_id}: {e.response.status_code}")
                return False
            log.warning(f"Error verifying access to {source_type} {item_id}: {e}")
            return True
        except Exception as e:
            log.warning(f"Error verifying access to {source_type} {item_id}: {e}")
            return True

    async def _handle_revoked_source(self, source: Dict[str, Any]) -> int:
        """Remove all files associated with a revoked source from this KB."""
        source_name = source.get("name", "unknown")
        removed_count = 0

        files = Knowledges.get_files_by_id(self.knowledge_id)
        if not files:
            return 0

        for file in files:
            if not file.id.startswith("googledrive-"):
                continue

            file_meta = file.meta or {}
            file_source_item_id = file_meta.get("source_item_id")
            source_item_id = source.get("item_id")

            if file_source_item_id and file_source_item_id != source_item_id:
                continue

            Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file.id)
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={"file_id": file.id},
                )
            except Exception as e:
                log.warning(f"Failed to remove vectors for {file.id}: {e}")

            remaining = Knowledges.get_knowledge_files_by_file_id(file.id)
            if not remaining:
                await asyncio.to_thread(DeletionService.delete_file, file.id)

            removed_count += 1

        log.info(
            f"Removed {removed_count} files from KB {self.knowledge_id} "
            f"due to revoked access to source '{source_name}'"
        )

        return removed_count

    # ------------------------------------------------------------------
    # Google Drive-specific helper methods
    # ------------------------------------------------------------------

    def _is_workspace_file(self, item: Dict[str, Any]) -> bool:
        """Check if a file is a Google Workspace file that needs export."""
        return item.get("mimeType", "") in GOOGLE_WORKSPACE_EXPORT_MAP

    def _get_effective_filename(self, item: Dict[str, Any]) -> str:
        """Get the effective filename, appending extension for Workspace files."""
        name = item.get("name", "unknown")
        mime_type = item.get("mimeType", "")

        if mime_type in GOOGLE_WORKSPACE_EXPORT_MAP:
            _, ext = GOOGLE_WORKSPACE_EXPORT_MAP[mime_type]
            if not name.endswith(ext):
                name = name + ext

        return name

    async def _collect_folder_files_full(self, source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
        """Full listing of all files in a folder (initial sync)."""
        folder_id = source["item_id"]

        # Get start page token BEFORE listing (captures changes during listing)
        new_page_token = await self._client.get_start_page_token()

        # Build folder map for path resolution
        folder_map: Dict[str, str] = {folder_id: ""}
        files_to_process = []

        # BFS through folder structure
        folders_to_visit = [(folder_id, "")]
        while folders_to_visit:
            current_id, parent_path = folders_to_visit.pop(0)
            items = await self._client.list_folder_children(current_id)

            for item in items:
                item_name = item.get("name", "unknown")
                item_path = f"{parent_path}/{item_name}" if parent_path else item_name
                mime_type = item.get("mimeType", "")

                if mime_type == "application/vnd.google-apps.folder":
                    folder_map[item["id"]] = item_path
                    folders_to_visit.append((item["id"], item_path))
                elif self._is_supported_file(item):
                    effective_name = self._get_effective_filename(item)
                    files_to_process.append(
                        {
                            "item": item,
                            "source_type": "folder",
                            "source_item_id": source["item_id"],
                            "name": effective_name,
                            "relative_path": (f"{parent_path}/{effective_name}" if parent_path else effective_name),
                        }
                    )

        # Persist folder map and page token
        source["folder_map"] = folder_map
        source["page_token"] = new_page_token

        return files_to_process, 0

    async def _collect_folder_files_incremental(
        self, source: Dict[str, Any], page_token: str
    ) -> tuple[List[Dict[str, Any]], int]:
        """Incremental sync using the changes API."""
        folder_id = source["item_id"]
        folder_map: Dict[str, str] = source.get("folder_map", {folder_id: ""})

        try:
            changes, new_page_token = await self._client.get_changes(page_token)
        except Exception as e:
            log.warning(
                "Changes API failed for source %s: %s, falling back to full sync",
                source.get("name"),
                e,
            )
            source["page_token"] = None
            source["folder_map"] = {}
            return await self._collect_folder_files_full(source)

        if new_page_token:
            source["page_token"] = new_page_token

        # Filter changes to only those within our synced folder tree
        files_to_process = []
        deleted_count = 0

        for item in changes:
            if item.get("@removed"):
                await self._handle_deleted_item(item)
                deleted_count += 1
                continue

            # Check if this item is within our folder tree
            if not self._is_in_folder_tree(item, folder_map):
                continue

            mime_type = item.get("mimeType", "")

            # Update folder map for folder items
            if mime_type == "application/vnd.google-apps.folder":
                parents = item.get("parents", [])
                for parent_id in parents:
                    if parent_id in folder_map:
                        parent_path = folder_map[parent_id]
                        item_path = f"{parent_path}/{item['name']}" if parent_path else item["name"]
                        folder_map[item["id"]] = item_path
                        break
                continue

            if not self._is_supported_file(item):
                continue

            # Compute relative path
            parents = item.get("parents", [])
            parent_path = ""
            for parent_id in parents:
                if parent_id in folder_map:
                    parent_path = folder_map[parent_id]
                    break

            effective_name = self._get_effective_filename(item)
            files_to_process.append(
                {
                    "item": item,
                    "source_type": "folder",
                    "source_item_id": source["item_id"],
                    "name": effective_name,
                    "relative_path": (f"{parent_path}/{effective_name}" if parent_path else effective_name),
                }
            )

        source["folder_map"] = folder_map
        return files_to_process, deleted_count

    def _is_in_folder_tree(self, item: Dict[str, Any], folder_map: Dict[str, str]) -> bool:
        """Check if an item is within the synced folder tree."""
        # If the item's ID is in the folder map, it's a known folder
        if item.get("id") in folder_map:
            return True

        # Check if any of the item's parents are in the folder map
        parents = item.get("parents", [])
        for parent_id in parents:
            if parent_id in folder_map:
                return True

        return False
