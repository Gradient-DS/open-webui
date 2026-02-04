"""OneDrive sync worker - Downloads and processes files from OneDrive folders."""

import asyncio
import io
import logging
import os
import time
import hashlib
from dataclasses import dataclass, asdict
from enum import Enum
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
    FILE_PROCESSING_MAX_CONCURRENT,
    STRICT_SOURCE_PERMISSIONS,
)

log = logging.getLogger(__name__)


class SyncErrorType(str, Enum):
    """Error types for OneDrive sync failures."""

    TIMEOUT = "timeout"
    EMPTY_CONTENT = "empty_content"
    PROCESSING_ERROR = "processing_error"
    DOWNLOAD_ERROR = "download_error"


@dataclass
class FailedFile:
    """Represents a file that failed to sync."""

    filename: str
    error_type: str
    error_message: str


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
        sources: List[Dict[str, Any]],
        access_token: str,
        user_id: str,
        user_token: str,
        event_emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.knowledge_id = knowledge_id
        self.sources = sources
        self.access_token = access_token  # OneDrive Graph API token
        self.user_id = user_id
        self.user_token = user_token  # Open WebUI JWT for internal API calls
        self.event_emitter = event_emitter
        self._client: Optional[GraphClient] = None
        self._permitted_emails: set = set()  # Cached permitted emails from OneDrive

    def _check_cancelled(self) -> bool:
        """Check if sync has been cancelled by user."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get("onedrive_sync", {})
            return sync_info.get("status") == "cancelled"
        return False

    def _get_excluded_item_ids(self) -> set:
        """Get OneDrive item IDs that the user has explicitly removed from the KB."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return set()
        meta = knowledge.meta or {}
        sync_info = meta.get("onedrive_sync", {})
        return set(sync_info.get("excluded_item_ids", []))

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

        return None

    async def _update_sync_status(
        self,
        status: str,
        current: int = 0,
        total: int = 0,
        filename: str = "",
        error: Optional[str] = None,
        files_processed: int = 0,
        files_failed: int = 0,
        deleted_count: int = 0,
        failed_files: Optional[List[FailedFile]] = None,
    ):
        """Update sync status in knowledge meta and emit Socket.IO event."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get("onedrive_sync", {})

            # Don't overwrite cancelled status with progress updates
            if sync_info.get("status") == "cancelled" and status == "syncing":
                return

            sync_info["status"] = status
            sync_info["progress_current"] = current
            sync_info["progress_total"] = total
            if error:
                sync_info["error"] = error
            meta["onedrive_sync"] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

        # Convert failed_files to dicts for serialization
        failed_files_dicts = (
            [asdict(f) for f in failed_files] if failed_files else None
        )

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
            files_processed=files_processed,
            files_failed=files_failed,
            deleted_count=deleted_count,
            failed_files=failed_files_dicts,
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
                        "files_processed": files_processed,
                        "files_failed": files_failed,
                        "deleted_count": deleted_count,
                        "failed_files": failed_files_dicts,
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

    async def _collect_folder_files(
        self, source: Dict[str, Any]
    ) -> tuple[List[Dict[str, Any]], int]:
        """Collect files from a folder using delta query."""
        delta_link = source.get("delta_link")

        items, new_delta_link = await self._client.get_drive_delta(
            source["drive_id"], source["item_id"], delta_link
        )

        # Update source with new delta link
        source["delta_link"] = new_delta_link

        # Separate files and deleted items
        files_to_process = []
        deleted_count = 0

        for item in items:
            if "@removed" in item:
                await self._handle_deleted_item(item)
                deleted_count += 1
            elif self._is_supported_file(item):
                files_to_process.append(
                    {
                        "item": item,
                        "drive_id": source["drive_id"],
                        "source_type": "folder",
                        "name": item.get("name", "unknown"),
                    }
                )

        return files_to_process, deleted_count

    async def _collect_single_file(
        self, source: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Check if a single file needs syncing based on content hash."""
        try:
            # Get current file metadata from Graph API
            item = await self._client.get_item(source["drive_id"], source["item_id"])

            if not item:
                log.warning(f"File not found: {source['name']}")
                return None

            # Check content hash for changes
            # OneDrive returns different hash types depending on the drive:
            # - sha256Hash: OneDrive for Business
            # - quickXorHash: OneDrive Personal
            hashes = item.get("file", {}).get("hashes", {})
            current_hash = hashes.get("sha256Hash") or hashes.get("quickXorHash")
            stored_hash = source.get("content_hash")

            if current_hash and current_hash == stored_hash:
                # Verify the file was actually processed and added to the KB.
                # A previous sync may have saved the hash but failed during
                # retrieval processing, leaving an orphan record.
                file_id = f"onedrive-{source['item_id']}"
                kb_links = Knowledges.get_knowledge_files_by_file_id(file_id)
                in_kb = any(
                    kf.knowledge_id == self.knowledge_id for kf in kb_links
                )
                if in_kb:
                    log.info(f"File unchanged (hash match): {source['name']}")
                    return None
                else:
                    log.info(
                        f"File hash matches but not in KB, re-queuing: {source['name']}"
                    )

            if not current_hash:
                log.warning(f"No hash available from OneDrive for: {source['name']}")
            elif not stored_hash:
                log.info(f"First sync for file (no stored hash): {source['name']}")
            else:
                log.info(f"File changed (hash mismatch): {source['name']}")

            # Store new hash for later save
            source["content_hash"] = current_hash

            return {
                "item": item,
                "drive_id": source["drive_id"],
                "source_type": "file",
                "name": item.get("name", source["name"]),
            }

        except Exception as e:
            log.error(f"Failed to check file {source['name']}: {e}")
            return None

    async def _save_sources(self):
        """Save updated sources (with delta links and hashes) to knowledge metadata."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get("onedrive_sync", {})
        sync_info["sources"] = self.sources
        meta["onedrive_sync"] = sync_info

        Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

    async def _sync_permissions(self):
        """Sync OneDrive folder permissions to Knowledge access_control.

        Maps OneDrive sharing permissions to Open WebUI users by email.
        Users with OneDrive access get read permission on the collection.
        Only the owner (sync initiator) gets write permission.
        """
        # Find first folder source to get permissions from
        folder_source = next(
            (s for s in self.sources if s.get("type") == "folder"), None
        )
        if not folder_source:
            log.info("No folder sources, skipping permission sync")
            return

        try:
            permissions = await self._client.get_folder_permissions(
                folder_source["drive_id"], folder_source["item_id"]
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

            # Store permitted emails for later use in file metadata
            self._permitted_emails = permitted_emails

            # Store permitted emails in knowledge meta for permission provider access
            knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
            if knowledge:
                meta = knowledge.meta or {}
                if "onedrive_sync" not in meta:
                    meta["onedrive_sync"] = {}
                meta["onedrive_sync"]["permitted_emails"] = list(permitted_emails)
                meta["onedrive_sync"]["permission_sync_at"] = int(time.time())
                Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
                log.info(f"Stored {len(permitted_emails)} permitted emails in knowledge meta")

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
        """Execute sync operation for all sources.

        Returns:
            Dict with sync results (files_processed, files_failed, failed_files, etc.)
        """
        self._client = GraphClient(self.access_token)

        try:
            await self._update_sync_status("syncing", 0, 0)

            # Check if KB access level is compatible with OneDrive permissions
            conflict = await self._validate_kb_access_level()
            if conflict and conflict.get("has_conflict"):
                strict_mode = STRICT_SOURCE_PERMISSIONS.value
                if strict_mode:
                    # Abort sync — cannot add OneDrive files to a public KB
                    error_msg = (
                        "Cannot sync OneDrive files to a public knowledge base. "
                        "Make the knowledge base private first, then share it with "
                        "users who have source access."
                    )
                    await self._update_sync_status(
                        "failed",
                        error=error_msg,
                    )
                    log.warning(
                        f"KB access conflict — aborting sync for {self.knowledge_id}: "
                        f"{error_msg}"
                    )
                    return {
                        "files_processed": 0,
                        "files_failed": 0,
                        "failed_files": [],
                        "deleted_count": 0,
                        "error": error_msg,
                    }
                else:
                    # Lenient mode — warn but proceed
                    from open_webui.services.onedrive.sync_events import emit_sync_progress

                    await emit_sync_progress(
                        user_id=self.user_id,
                        knowledge_id=self.knowledge_id,
                        status="access_conflict",
                        current=0,
                        total=0,
                        filename="",
                        error=conflict.get("message"),
                    )
                    log.warning(
                        f"KB access conflict detected for {self.knowledge_id}: "
                        f"{conflict.get('message')}"
                    )

            # Sync OneDrive folder permissions to Knowledge access_control
            await self._sync_permissions()

            # Aggregate counters
            total_processed = 0
            total_failed = 0
            total_deleted = 0
            failed_files: List[FailedFile] = []

            # Collect all files to process from all sources
            all_files_to_process = []

            log.info(
                f"Starting multi-source sync for knowledge {self.knowledge_id}, "
                f"{len(self.sources)} sources"
            )

            for source in self.sources:
                if source.get("type") == "folder":
                    files, deleted = await self._collect_folder_files(source)
                    all_files_to_process.extend(files)
                    total_deleted += deleted
                else:  # file
                    file_info = await self._collect_single_file(source)
                    if file_info:
                        all_files_to_process.append(file_info)

            # Filter out files that the user has explicitly removed from the KB
            excluded_item_ids = self._get_excluded_item_ids()
            if excluded_item_ids:
                before_count = len(all_files_to_process)
                all_files_to_process = [
                    f
                    for f in all_files_to_process
                    if f["item"].get("id") not in excluded_item_ids
                ]
                excluded_count = before_count - len(all_files_to_process)
                if excluded_count > 0:
                    log.info(
                        f"Excluded {excluded_count} previously removed files "
                        f"from sync for knowledge {self.knowledge_id}"
                    )

            # Apply file limit
            max_files = ONEDRIVE_MAX_FILES_PER_SYNC
            if len(all_files_to_process) > max_files:
                log.warning(
                    f"Limiting sync to {max_files} files "
                    f"(found {len(all_files_to_process)})"
                )
                all_files_to_process = all_files_to_process[:max_files]

            total_files = len(all_files_to_process)
            log.info(f"Total files to process: {total_files}")

            # Process all files in parallel with controlled concurrency
            max_concurrent = FILE_PROCESSING_MAX_CONCURRENT.value
            semaphore = asyncio.Semaphore(max_concurrent)
            processed_count = 0
            failed_count = 0
            results_lock = asyncio.Lock()
            cancelled = False

            async def process_with_semaphore(
                file_info: Dict[str, Any], index: int
            ) -> Optional[FailedFile]:
                nonlocal processed_count, failed_count, cancelled

                # Fast-path: another coroutine already detected cancellation
                if cancelled:
                    return FailedFile(
                        filename=file_info.get("name", "unknown"),
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message="Sync cancelled by user",
                    )

                async with semaphore:
                    # Re-check cancellation AFTER acquiring semaphore
                    if cancelled or self._check_cancelled():
                        cancelled = True
                        return FailedFile(
                            filename=file_info.get("name", "unknown"),
                            error_type=SyncErrorType.PROCESSING_ERROR.value,
                            error_message="Sync cancelled by user",
                        )

                    try:
                        result = await self._process_file_info(file_info)

                        # Update counters with lock for thread safety
                        async with results_lock:
                            if result is None:
                                processed_count += 1
                            else:
                                failed_count += 1

                            # Emit progress update
                            await self._update_sync_status(
                                "syncing",
                                processed_count + failed_count,
                                total_files,
                                file_info.get("name", ""),
                                files_processed=processed_count,
                                files_failed=failed_count,
                            )

                        return result
                    except Exception as e:
                        log.error(f"Error processing file {file_info.get('name')}: {e}")
                        async with results_lock:
                            failed_count += 1
                        return FailedFile(
                            filename=file_info.get("name", "unknown"),
                            error_type=SyncErrorType.PROCESSING_ERROR.value,
                            error_message=str(e)[:100],
                        )

            log.info(
                f"Starting parallel processing of {total_files} files "
                f"with max {max_concurrent} concurrent"
            )
            start_time = time.time()

            # Create tasks for all files
            tasks = [
                process_with_semaphore(file_info, i)
                for i, file_info in enumerate(all_files_to_process)
            ]

            # Execute all tasks in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for result in results:
                if isinstance(result, Exception):
                    log.error(f"Unexpected error during file processing: {result}")
                    total_failed += 1
                    failed_files.append(
                        FailedFile(
                            filename="unknown",
                            error_type=SyncErrorType.PROCESSING_ERROR.value,
                            error_message=str(result)[:100],
                        )
                    )
                elif result is not None:
                    # result is a FailedFile
                    failed_files.append(result)

            total_processed = processed_count
            total_failed = failed_count

            processing_time = time.time() - start_time
            log.info(
                f"Parallel processing completed in {processing_time:.2f}s: "
                f"{total_processed} succeeded, {total_failed} failed"
            )

            # Check if cancelled during processing
            if cancelled:
                log.info(f"Sync cancelled by user for knowledge {self.knowledge_id}")

                # Save sources to preserve delta links for next sync
                await self._save_sources()

                await self._update_sync_status(
                    "cancelled",
                    total_processed + total_failed,
                    total_files,
                    "",
                    "Sync cancelled by user",
                    total_processed,
                    total_failed,
                    total_deleted,
                    failed_files,
                )
                return {
                    "files_processed": total_processed,
                    "files_failed": total_failed,
                    "total_found": total_files,
                    "deleted_count": total_deleted,
                    "cancelled": True,
                    "failed_files": [asdict(f) for f in failed_files],
                }

            # Save updated sources with delta links / hashes
            await self._save_sources()

            # Convert failed_files to dicts for storage
            failed_files_dicts = [asdict(f) for f in failed_files]

            # Update final sync status
            knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
            meta = knowledge.meta or {}
            sync_info = meta.get("onedrive_sync", {})
            sync_info["last_sync_at"] = int(time.time())
            sync_info["status"] = (
                "completed" if total_failed == 0 else "completed_with_errors"
            )
            sync_info["last_result"] = {
                "files_processed": total_processed,
                "files_failed": total_failed,
                "total_found": total_files,
                "deleted_count": total_deleted,
                "failed_files": failed_files_dicts,
            }
            meta["onedrive_sync"] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

            await self._update_sync_status(
                sync_info["status"],
                total_files,
                total_files,
                "",
                None,
                total_processed,
                total_failed,
                total_deleted,
                failed_files,
            )

            log.info(
                f"Sync completed for {self.knowledge_id}: "
                f"{total_processed} processed, {total_failed} failed"
            )

            return {
                "files_processed": total_processed,
                "files_failed": total_failed,
                "total_found": total_files,
                "deleted_count": total_deleted,
                "failed_files": failed_files_dicts,
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

    async def _process_file_info(self, file_info: Dict[str, Any]) -> Optional[FailedFile]:
        """Download and process a single file from file_info structure.

        Returns:
            None on success, FailedFile on error
        """
        item = file_info["item"]
        drive_id = file_info["drive_id"]
        item_id = item["id"]
        name = item["name"]

        log.info(f"Processing file: {name} (id: {item_id})")

        # Emit processing started event for progressive UI updates
        from open_webui.services.onedrive.sync_events import emit_file_processing

        await emit_file_processing(
            user_id=self.user_id,
            knowledge_id=self.knowledge_id,
            file_info={
                "item_id": item_id,
                "name": name,
                "size": item.get("size", 0),
            },
        )

        # Download file content
        try:
            content = await self._client.download_file(drive_id, item_id)
        except Exception as e:
            log.warning(f"Failed to download file {name}: {e}")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.DOWNLOAD_ERROR.value,
                error_message=f"Download failed: {str(e)[:80]}",
            )

        # Check for empty content
        if not content or len(content) == 0:
            log.warning(f"File {name} has no content")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.EMPTY_CONTENT.value,
                error_message="File is empty",
            )

        # Calculate hash for change detection
        content_hash = hashlib.sha256(content).hexdigest()

        # Check if file already exists with same hash
        file_id = f"onedrive-{item_id}"
        existing = Files.get_file_by_id(file_id)

        if existing and existing.hash == content_hash:
            # Check if the file was actually processed and added to the KB.
            # A previous sync may have created the DB record but failed during
            # retrieval processing (e.g. wrong API URL), leaving an orphan record.
            kb_links = Knowledges.get_knowledge_files_by_file_id(file_id)
            in_kb = any(
                kf.knowledge_id == self.knowledge_id for kf in kb_links
            )
            if in_kb:
                log.info(f"File unchanged (same hash), skipping: {name}")
                return None  # Success - no change needed
            else:
                log.info(
                    f"File record exists but not in KB, re-processing: {name}"
                )

        # Save to storage
        temp_filename = f"{file_id}_{name}"
        try:
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
        except Exception as e:
            log.warning(f"Failed to upload file to storage {name}: {e}")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Storage upload failed: {str(e)[:80]}",
            )

        try:
            # Create or update file record
            # Include permitted_emails for permission provider access validation
            file_meta = {
                "name": name,
                "content_type": self._get_content_type(name),
                "size": len(content),
                "source": "onedrive",
                "onedrive_item_id": item_id,
                "onedrive_drive_id": drive_id,
                "knowledge_id": self.knowledge_id,
                "permitted_emails": list(self._permitted_emails),
                "last_synced_at": int(time.time()),
            }

            if existing:
                # Update existing file with FileUpdateForm
                Files.update_file_by_id(
                    file_id,
                    FileUpdateForm(
                        hash=content_hash,
                        meta=file_meta,
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
                    meta=file_meta,
                )
                Files.insert_new_file(self.user_id, file_form)
                log.info(f"Created new file record: {file_id}")

            # Process file via internal API call
            failed = await self._process_file_via_api(file_id, name)
            if failed:
                return failed

            # Add to knowledge base (if not already added)
            Knowledges.add_file_to_knowledge_by_id(
                self.knowledge_id,
                file_id,
                self.user_id,
            )
            log.info(f"Added file to knowledge base: {file_id}")

            # Emit file added event for progressive UI updates
            file_record = Files.get_file_by_id(file_id)
            if file_record:
                from open_webui.services.onedrive.sync_events import emit_file_added

                await emit_file_added(
                    user_id=self.user_id,
                    knowledge_id=self.knowledge_id,
                    file_data={
                        "id": file_record.id,
                        "filename": file_record.filename,
                        "meta": file_record.meta,
                        "created_at": file_record.created_at,
                        "updated_at": file_record.updated_at,
                    },
                )

            return None  # Success

        except Exception as e:
            log.warning(f"Error processing file {name}: {e}")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )

    async def _process_file_via_api(self, file_id: str, filename: str) -> Optional[FailedFile]:
        """Process file by calling the internal retrieval API.

        Two-step process:
        1. First call WITHOUT collection_name to extract and process file content
        2. Second call WITH collection_name to add the processed content to knowledge base

        This is needed because when collection_name is provided, the retrieval API
        assumes the file has already been processed and tries to use existing vectors
        or file.data.content, which are empty for newly downloaded OneDrive files.

        Returns:
            None on success, FailedFile on error
        """
        import httpx

        # Use the backend's own port for internal API calls.
        # WEBUI_URL is browser-facing and may point to the frontend dev server,
        # so we construct the internal URL from the PORT env var instead.
        port = os.environ.get("PORT", "8080")
        base_url = f"http://localhost:{port}"

        # Use 60-second timeout per document to prevent hanging
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
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

                # Handle duplicate content gracefully - if embeddings already exist, proceed to step 2
                if response.status_code == 400:
                    error_data = response.json()
                    detail = error_data.get("detail", "")
                    if "Duplicate content" in detail:
                        log.debug(
                            f"File {file_id} already has embeddings, skipping to knowledge base addition"
                        )
                    elif "No content extracted" in detail or "empty" in detail.lower():
                        log.debug(f"File {file_id} has no extractable content")
                        return FailedFile(
                            filename=filename,
                            error_type=SyncErrorType.EMPTY_CONTENT.value,
                            error_message="File has no extractable content",
                        )
                    else:
                        log.debug(
                            f"Failed to process file content {file_id}: "
                            f"{response.status_code} - {response.text}"
                        )
                        return FailedFile(
                            filename=filename,
                            error_type=SyncErrorType.PROCESSING_ERROR.value,
                            error_message=detail[:100] if detail else "Processing failed",
                        )
                elif response.status_code != 200:
                    log.debug(
                        f"Failed to process file content {file_id}: "
                        f"{response.status_code} - {response.text}"
                    )
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=f"HTTP {response.status_code}",
                    )
                else:
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

                # Handle duplicate content in knowledge base gracefully as well
                if response.status_code == 400:
                    error_data = response.json()
                    detail = error_data.get("detail", "")
                    if "Duplicate content" in detail:
                        log.debug(
                            f"File {file_id} already exists in knowledge base {self.knowledge_id}"
                        )
                        return None  # Success - file is already in the knowledge base
                    else:
                        log.debug(
                            f"Failed to add file {file_id} to knowledge base: "
                            f"{response.status_code} - {response.text}"
                        )
                        return FailedFile(
                            filename=filename,
                            error_type=SyncErrorType.PROCESSING_ERROR.value,
                            error_message=detail[:100] if detail else "Failed to add to knowledge base",
                        )
                elif response.status_code != 200:
                    log.debug(
                        f"Failed to add file {file_id} to knowledge base: "
                        f"{response.status_code} - {response.text}"
                    )
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=f"HTTP {response.status_code}",
                    )
                else:
                    log.info(f"Successfully added file {file_id} to knowledge base {self.knowledge_id}")

            return None  # Success
        except httpx.TimeoutException:
            log.warning(f"Timeout processing file {file_id} ({filename})")
            return FailedFile(
                filename=filename,
                error_type=SyncErrorType.TIMEOUT.value,
                error_message="Processing timed out after 60 seconds",
            )
        except Exception as e:
            log.warning(f"Error processing file {file_id} ({filename}): {e}")
            return FailedFile(
                filename=filename,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )
