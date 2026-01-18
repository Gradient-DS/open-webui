"""OneDrive sync worker - Downloads and processes files from OneDrive folders."""

import asyncio
import io
import logging
import tempfile
import os
import time
import hashlib
import uuid
from typing import Optional, Callable, Awaitable, Dict, Any, List
from pathlib import Path

from open_webui.services.onedrive.graph_client import GraphClient
from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files, FileForm, FileUpdateForm
from open_webui.models.users import Users
from open_webui.storage.provider import Storage
from open_webui.config import (
    ONEDRIVE_MAX_FILES_PER_SYNC,
    ONEDRIVE_MAX_FILE_SIZE_MB,
)

log = logging.getLogger(__name__)

# Supported file extensions for processing
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".json",
    ".xml",
    ".csv",
}

# MIME types for supported extensions
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".xml": "application/xml",
    ".csv": "text/csv",
}


class OneDriveSyncWorker:
    """Worker to sync OneDrive folder contents to a Knowledge base."""

    def __init__(
        self,
        knowledge_id: str,
        drive_id: str,
        folder_id: str,
        access_token: str,
        user_id: str,
        user_token: str,
        event_emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.knowledge_id = knowledge_id
        self.drive_id = drive_id
        self.folder_id = folder_id
        self.access_token = access_token  # OneDrive Graph API token
        self.user_id = user_id
        self.user_token = user_token  # Open WebUI JWT for internal API calls
        self.event_emitter = event_emitter
        self._client: Optional[GraphClient] = None

    async def _update_sync_status(
        self,
        status: str,
        current: int = 0,
        total: int = 0,
        filename: str = "",
        error: Optional[str] = None,
    ):
        """Update sync status in knowledge meta and emit Socket.IO event."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get("onedrive_sync", {})
            sync_info["status"] = status
            sync_info["progress_current"] = current
            sync_info["progress_total"] = total
            if error:
                sync_info["error"] = error
            meta["onedrive_sync"] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

        # Emit Socket.IO event for real-time progress updates
        from open_webui.services.onedrive.sync_events import emit_sync_progress

        await emit_sync_progress(
            user_id=self.user_id,
            knowledge_id=self.knowledge_id,
            status=status,
            current=current,
            total=total,
            filename=filename,
            error=error,
        )

        # Also emit via custom emitter if provided
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "sync_progress",
                    "data": {
                        "knowledge_id": self.knowledge_id,
                        "status": status,
                        "current": current,
                        "total": total,
                        "filename": filename,
                        "error": error,
                    },
                }
            )

    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """Check if file is supported for processing."""
        if "folder" in item:
            return False

        name = item.get("name", "")
        ext = Path(name).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            log.debug(f"Skipping unsupported file type: {name}")
            return False

        size = item.get("size", 0)
        max_size = ONEDRIVE_MAX_FILE_SIZE_MB * 1024 * 1024

        if size > max_size:
            log.warning(f"Skipping {name}: size {size} exceeds max {max_size}")
            return False

        return True

    def _get_content_type(self, filename: str) -> str:
        """Get MIME type from filename."""
        ext = Path(filename).suffix.lower()
        return CONTENT_TYPES.get(ext, "application/octet-stream")

    async def _sync_permissions(self):
        """Sync OneDrive folder permissions to Knowledge access_control.

        Maps OneDrive sharing permissions to Open WebUI users by email.
        Users with OneDrive access get read permission on the collection.
        Only the owner (sync initiator) gets write permission.
        """
        try:
            permissions = await self._client.get_folder_permissions(
                self.drive_id, self.folder_id
            )

            # Collect all emails from permissions
            permitted_emails = set()
            for perm in permissions:
                # Check grantedTo (direct shares)
                granted_to = perm.get("grantedTo", {})
                user_info = granted_to.get("user", {})
                email = user_info.get("email")
                if email:
                    permitted_emails.add(email.lower())

                # Check grantedToIdentities (for sharing links)
                for identity in perm.get("grantedToIdentities", []):
                    user_info = identity.get("user", {})
                    email = user_info.get("email")
                    if email:
                        permitted_emails.add(email.lower())

                # Check grantedToIdentitiesV2 (newer API format)
                for identity in perm.get("grantedToIdentitiesV2", []):
                    user_info = identity.get("user", {})
                    email = user_info.get("email")
                    if email:
                        permitted_emails.add(email.lower())

            log.info(f"Found {len(permitted_emails)} permitted emails from OneDrive")

            # Find matching Open WebUI users
            permitted_user_ids = []
            for email in permitted_emails:
                user = Users.get_user_by_email(email)
                if user:
                    permitted_user_ids.append(user.id)
                    log.debug(f"Mapped OneDrive permission for {email} to user {user.id}")

            # Update knowledge access_control
            if permitted_user_ids:
                # Ensure owner is included
                if self.user_id not in permitted_user_ids:
                    permitted_user_ids.append(self.user_id)

                knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
                if knowledge:
                    from open_webui.models.knowledge import KnowledgeForm

                    # Create access control structure
                    access_control = {
                        "read": {
                            "user_ids": permitted_user_ids,
                            "group_ids": [],
                        },
                        "write": {
                            "user_ids": [self.user_id],  # Only owner can write
                            "group_ids": [],
                        },
                    }

                    Knowledges.update_knowledge_by_id(
                        self.knowledge_id,
                        KnowledgeForm(
                            name=knowledge.name,
                            description=knowledge.description,
                            access_control=access_control,
                        ),
                    )

                    log.info(
                        f"Updated access_control for {self.knowledge_id}: "
                        f"{len(permitted_user_ids)} users with read access"
                    )
            else:
                log.info(
                    f"No matching users found for OneDrive permissions, "
                    f"keeping default access_control"
                )

        except Exception as e:
            log.warning(f"Failed to sync permissions: {e}")
            # Don't fail the entire sync if permission mapping fails

    async def sync(self) -> Dict[str, Any]:
        """Execute sync operation.

        Returns:
            Dict with sync results (files_processed, files_failed, etc.)
        """
        self._client = GraphClient(self.access_token)

        try:
            await self._update_sync_status("syncing", 0, 0)

            # Sync OneDrive folder permissions to Knowledge access_control
            await self._sync_permissions()

            # Get existing sync state
            knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
            meta = knowledge.meta or {}
            sync_info = meta.get("onedrive_sync", {})
            delta_link = sync_info.get("delta_link")

            # Get changed items via delta query
            log.info(
                f"Starting delta sync for knowledge {self.knowledge_id}, "
                f"delta_link exists: {bool(delta_link)}"
            )
            items, new_delta_link = await self._client.get_drive_delta(
                self.drive_id, self.folder_id, delta_link
            )

            log.info(f"Delta query returned {len(items)} items")

            # Filter to supported files
            files = [item for item in items if self._is_supported_file(item)]

            # Handle deleted files (items with @removed property)
            deleted_items = [item for item in items if "@removed" in item]
            for deleted_item in deleted_items:
                await self._handle_deleted_item(deleted_item)

            log.info(
                f"After filtering: {len(files)} supported files, "
                f"{len(deleted_items)} deleted items"
            )

            # Apply limit
            if len(files) > ONEDRIVE_MAX_FILES_PER_SYNC:
                log.warning(
                    f"Limiting sync to {ONEDRIVE_MAX_FILES_PER_SYNC} files "
                    f"(found {len(files)})"
                )
                files = files[:ONEDRIVE_MAX_FILES_PER_SYNC]

            total = len(files)
            processed = 0
            failed = 0

            for i, item in enumerate(files):
                try:
                    await self._update_sync_status(
                        "syncing", i + 1, total, item["name"]
                    )
                    await self._process_file(item)
                    processed += 1
                except Exception as e:
                    log.error(f"Failed to process {item['name']}: {e}")
                    failed += 1

            # Update sync state with new delta link
            sync_info["delta_link"] = new_delta_link
            sync_info["last_sync_at"] = int(time.time())
            sync_info["status"] = (
                "completed" if failed == 0 else "completed_with_errors"
            )
            sync_info["last_result"] = {
                "files_processed": processed,
                "files_failed": failed,
                "total_found": total,
                "deleted_count": len(deleted_items),
            }
            meta["onedrive_sync"] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

            await self._update_sync_status(
                sync_info["status"], total, total, ""
            )

            log.info(
                f"Sync completed for {self.knowledge_id}: "
                f"{processed} processed, {failed} failed"
            )

            return {
                "files_processed": processed,
                "files_failed": failed,
                "total_found": total,
                "deleted_count": len(deleted_items),
            }

        except Exception as e:
            log.exception(f"Sync failed: {e}")

            # Update status to failed
            await self._update_sync_status("failed", error=str(e))
            raise

        finally:
            if self._client:
                await self._client.close()

    async def _handle_deleted_item(self, item: Dict[str, Any]):
        """Handle a deleted item from delta query."""
        item_id = item.get("id")
        if not item_id:
            return

        file_id = f"onedrive-{item_id}"

        # Check if file exists in our system
        existing = Files.get_file_by_id(file_id)
        if existing:
            log.info(f"Removing deleted OneDrive file: {file_id}")
            # Remove from knowledge base
            Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file_id)
            # Delete file record
            Files.delete_file_by_id(file_id)

    async def _process_file(self, item: Dict[str, Any]):
        """Download and process a single file."""
        item_id = item["id"]
        name = item["name"]

        log.info(f"Processing file: {name} (id: {item_id})")

        # Download file content
        content = await self._client.download_file(self.drive_id, item_id)

        # Calculate hash for change detection
        content_hash = hashlib.sha256(content).hexdigest()

        # Check if file already exists with same hash
        file_id = f"onedrive-{item_id}"
        existing = Files.get_file_by_id(file_id)

        if existing and existing.hash == content_hash:
            log.info(f"File unchanged (same hash), skipping: {name}")
            return

        # Save to storage
        temp_filename = f"{file_id}_{name}"
        contents, file_path = Storage.upload_file(
            io.BytesIO(content),
            temp_filename,
            {
                "OpenWebUI-User-Id": self.user_id,
                "OpenWebUI-File-Id": file_id,
                "OpenWebUI-Source": "onedrive",
                "OpenWebUI-OneDrive-Item-Id": item_id,
            },
        )

        try:
            # Create or update file record
            if existing:
                # Update existing file with FileUpdateForm
                Files.update_file_by_id(
                    file_id,
                    FileUpdateForm(
                        hash=content_hash,
                        meta={
                            "name": name,
                            "content_type": self._get_content_type(name),
                            "size": len(content),
                            "source": "onedrive",
                            "onedrive_item_id": item_id,
                            "onedrive_drive_id": self.drive_id,
                        },
                    ),
                )
                # Update path separately (FileUpdateForm doesn't include path)
                Files.update_file_path_by_id(file_id, file_path)
                log.info(f"Updated existing file record: {file_id}")
            else:
                # Create new file
                file_form = FileForm(
                    id=file_id,
                    filename=name,
                    path=file_path,
                    hash=content_hash,
                    data={},
                    meta={
                        "name": name,
                        "content_type": self._get_content_type(name),
                        "size": len(content),
                        "source": "onedrive",
                        "onedrive_item_id": item_id,
                        "onedrive_drive_id": self.drive_id,
                    },
                )
                Files.insert_new_file(self.user_id, file_form)
                log.info(f"Created new file record: {file_id}")

            # Process file via internal API call
            await self._process_file_via_api(file_id)

            # Add to knowledge base (if not already added)
            Knowledges.add_file_to_knowledge_by_id(
                self.knowledge_id,
                file_id,
                self.user_id,
            )
            log.info(f"Added file to knowledge base: {file_id}")

        except Exception as e:
            # Clean up on failure
            log.error(f"Error processing file {name}: {e}")
            raise

    async def _process_file_via_api(self, file_id: str):
        """Process file by calling the internal retrieval API.

        Two-step process:
        1. First call WITHOUT collection_name to extract and process file content
        2. Second call WITH collection_name to add the processed content to knowledge base

        This is needed because when collection_name is provided, the retrieval API
        assumes the file has already been processed and tries to use existing vectors
        or file.data.content, which are empty for newly downloaded OneDrive files.
        """
        import httpx

        # Get the base URL from config or use default
        from open_webui.config import WEBUI_URL

        base_url = WEBUI_URL.value if WEBUI_URL.value else "http://localhost:8080"

        async with httpx.AsyncClient(timeout=300.0) as client:
            # Step 1: Process file content (extract text, create embeddings in file-{id} collection)
            response = await client.post(
                f"{base_url}/api/v1/retrieval/process/file",
                headers={
                    "Authorization": f"Bearer {self.user_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "file_id": file_id,
                    # Don't pass collection_name here - this triggers actual file processing
                },
            )

            if response.status_code != 200:
                log.error(
                    f"Failed to process file content {file_id}: "
                    f"{response.status_code} - {response.text}"
                )
                raise Exception(f"File processing failed: {response.text}")

            log.info(f"Successfully extracted content from file {file_id}")

            # Step 2: Add processed content to knowledge base collection
            response = await client.post(
                f"{base_url}/api/v1/retrieval/process/file",
                headers={
                    "Authorization": f"Bearer {self.user_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "file_id": file_id,
                    "collection_name": self.knowledge_id,
                },
            )

            if response.status_code != 200:
                log.error(
                    f"Failed to add file {file_id} to knowledge base: "
                    f"{response.status_code} - {response.text}"
                )
                raise Exception(f"Failed to add to knowledge base: {response.text}")

            log.info(f"Successfully added file {file_id} to knowledge base {self.knowledge_id}")


async def sync_folder_to_knowledge(
    knowledge_id: str,
    drive_id: str,
    folder_id: str,
    access_token: str,
    user_id: str,
    user_token: str,
    event_emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """Convenience function to execute sync."""
    worker = OneDriveSyncWorker(
        knowledge_id=knowledge_id,
        drive_id=drive_id,
        folder_id=folder_id,
        access_token=access_token,
        user_id=user_id,
        user_token=user_token,
        event_emitter=event_emitter,
    )
    return await worker.sync()
