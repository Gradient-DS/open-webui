"""Google Drive sync worker - Downloads and processes files from Google Drive folders."""

import asyncio
import io
import logging
import time
import hashlib
import uuid
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Callable, Awaitable, Dict, Any, List
from pathlib import Path

from open_webui.services.google_drive.drive_client import (
    GoogleDriveClient,
    GOOGLE_WORKSPACE_EXPORT_MAP,
)
from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files, FileForm, FileUpdateForm
from open_webui.models.users import Users
from open_webui.storage.provider import Storage
from open_webui.config import (
    GOOGLE_DRIVE_MAX_FILES_PER_SYNC,
    GOOGLE_DRIVE_MAX_FILE_SIZE_MB,
    FILE_PROCESSING_MAX_CONCURRENT,
)
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.services.deletion import DeletionService

log = logging.getLogger(__name__)


class SyncErrorType(str, Enum):
    """Error types for Google Drive sync failures."""

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


class GoogleDriveSyncWorker:
    """Worker to sync Google Drive folder contents to a Knowledge base."""

    def __init__(
        self,
        knowledge_id: str,
        sources: List[Dict[str, Any]],
        access_token: str,
        user_id: str,
        app,
        event_emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        token_provider: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    ):
        self.knowledge_id = knowledge_id
        self.sources = sources
        self.access_token = access_token
        self.user_id = user_id
        self.app = app
        self.event_emitter = event_emitter
        self._token_provider = token_provider
        self._client: Optional[GoogleDriveClient] = None

    def _make_request(self):
        """Construct a minimal Request for calling retrieval functions directly."""
        from starlette.requests import Request
        from starlette.datastructures import Headers

        return Request({
            "type": "http",
            "method": "POST",
            "path": "/internal/google-drive-sync",
            "query_string": b"",
            "headers": Headers({}).raw,
            "app": self.app,
        })

    def _get_user(self):
        """Fetch the user object for process_file access control."""
        user = Users.get_user_by_id(self.user_id)
        if not user:
            raise RuntimeError(f"User {self.user_id} not found")
        return user

    def _check_cancelled(self) -> bool:
        """Check if sync has been cancelled by user."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get("google_drive_sync", {})
            return sync_info.get("status") == "cancelled"
        return False

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
            sync_info = meta.get("google_drive_sync", {})
            # Don't overwrite cancelled status with progress updates
            if sync_info.get("status") == "cancelled" and status == "syncing":
                return
            sync_info["status"] = status
            if status == "syncing" and not sync_info.get("sync_started_at"):
                sync_info["sync_started_at"] = int(time.time())
            sync_info["progress_current"] = current
            sync_info["progress_total"] = total
            if error:
                sync_info["error"] = error
            meta["google_drive_sync"] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

        # Convert failed_files to dicts for serialization
        failed_files_dicts = (
            [asdict(f) for f in failed_files] if failed_files else None
        )

        from open_webui.services.google_drive.sync_events import emit_sync_progress

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

    def _is_workspace_file(self, item: Dict[str, Any]) -> bool:
        """Check if a file is a Google Workspace file that needs export."""
        return item.get("mimeType", "") in GOOGLE_WORKSPACE_EXPORT_MAP

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

    def _get_content_type(self, filename: str) -> str:
        """Get MIME type from filename."""
        ext = Path(filename).suffix.lower()
        return CONTENT_TYPES.get(ext, "application/octet-stream")

    def _get_effective_filename(self, item: Dict[str, Any]) -> str:
        """Get the effective filename, appending extension for Workspace files."""
        name = item.get("name", "unknown")
        mime_type = item.get("mimeType", "")

        if mime_type in GOOGLE_WORKSPACE_EXPORT_MAP:
            _, ext = GOOGLE_WORKSPACE_EXPORT_MAP[mime_type]
            if not name.endswith(ext):
                name = name + ext

        return name

    async def _collect_folder_files(
        self, source: Dict[str, Any]
    ) -> tuple[List[Dict[str, Any]], int]:
        """Collect files from a folder using changes API or full listing."""
        page_token = source.get("page_token")

        if page_token:
            return await self._collect_folder_files_incremental(source, page_token)
        else:
            return await self._collect_folder_files_full(source)

    async def _collect_folder_files_full(
        self, source: Dict[str, Any]
    ) -> tuple[List[Dict[str, Any]], int]:
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
                            "relative_path": (
                                f"{parent_path}/{effective_name}"
                                if parent_path
                                else effective_name
                            ),
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
                source.get("name"), e,
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
                        item_path = (
                            f"{parent_path}/{item['name']}"
                            if parent_path
                            else item["name"]
                        )
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
                    "relative_path": (
                        f"{parent_path}/{effective_name}"
                        if parent_path
                        else effective_name
                    ),
                }
            )

        source["folder_map"] = folder_map
        return files_to_process, deleted_count

    def _is_in_folder_tree(
        self, item: Dict[str, Any], folder_map: Dict[str, str]
    ) -> bool:
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

    async def _collect_single_file(
        self, source: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
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
                    log.info(
                        f"File {source['name']} indicator matches but record {reason}, "
                        f"re-syncing"
                    )

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

    async def _save_sources(self):
        """Save updated sources to knowledge metadata."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get("google_drive_sync", {})
        sync_info["sources"] = self.sources
        meta["google_drive_sync"] = sync_info

        Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

    async def _sync_permissions(self):
        """Sync Google Drive folder permissions to Knowledge access_control."""
        folder_source = next(
            (s for s in self.sources if s.get("type") == "folder"), None
        )
        if not folder_source:
            log.info("No folder sources, skipping permission sync")
            return

        try:
            permissions = await self._client.get_file_permissions(
                folder_source["item_id"]
            )

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

                    access_control = {
                        "read": {
                            "user_ids": permitted_user_ids,
                            "group_ids": [],
                        },
                        "write": {
                            "user_ids": [self.user_id],
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
                    f"No matching users found for Google Drive permissions, "
                    f"keeping default access_control"
                )

        except Exception as e:
            log.warning(f"Failed to sync permissions: {e}")

    async def _verify_source_access(self, source: Dict[str, Any]) -> bool:
        """Verify the user can still access a Google Drive source."""
        item_id = source.get("item_id")
        source_type = source.get("type", "folder")

        try:
            item = await self._client.get_file(item_id)
            if item is None:
                return False
            return True
        except Exception as e:
            error_str = str(e).lower()
            if (
                "404" in error_str
                or "403" in error_str
                or "not found" in error_str
            ):
                log.warning(
                    f"User {self.user_id} lost access to {source_type} "
                    f"{item_id}: {e}"
                )
                return False
            log.warning(
                f"Error verifying access to {source_type} {item_id}: {e}"
            )
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

            Knowledges.remove_file_from_knowledge_by_id(
                self.knowledge_id, file.id
            )
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

    async def sync(self) -> Dict[str, Any]:
        """Execute sync operation for all sources."""
        self._client = GoogleDriveClient(self.access_token, token_provider=self._token_provider)

        try:
            await self._update_sync_status("syncing", 0, 0)

            # Sync Google Drive folder permissions to Knowledge access_control
            await self._sync_permissions()

            # Verify access to each source before syncing
            verified_sources = []
            revoked_sources = []

            for source in self.sources:
                has_access = await self._verify_source_access(source)
                if has_access:
                    verified_sources.append(source)
                else:
                    revoked_sources.append(source)

            # Handle revoked sources
            total_revoked_files = 0
            for source in revoked_sources:
                removed = await self._handle_revoked_source(source)
                total_revoked_files += removed

                await self._update_sync_status(
                    "access_revoked",
                    error=(
                        f"Access to '{source.get('name', 'unknown')}' has been revoked. "
                        f"{removed} file(s) removed."
                    ),
                )

            self.sources = verified_sources

            # Aggregate counters
            total_processed = 0
            total_failed = 0
            total_deleted = 0
            failed_files: List[FailedFile] = []

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
                else:
                    file_info = await self._collect_single_file(source)
                    if file_info:
                        all_files_to_process.append(file_info)

            # Apply file count limit
            max_files = min(GOOGLE_DRIVE_MAX_FILES_PER_SYNC, 250)
            current_files = Knowledges.get_files_by_id(self.knowledge_id) or []
            current_file_count = len(current_files)
            available_slots = max(0, max_files - current_file_count)

            if len(all_files_to_process) > available_slots:
                log.warning(
                    f"File limit exceeded: {current_file_count} existing + "
                    f"{len(all_files_to_process)} new > {max_files} limit"
                )
                if available_slots == 0:
                    await self._update_sync_status(
                        "file_limit_exceeded",
                        error=(
                            f"This knowledge base has reached the {max_files}-file limit. "
                            f"Remove files or select fewer items to sync."
                        ),
                    )
                    await self._save_sources()
                    return {
                        "files_processed": 0,
                        "files_failed": 0,
                        "total_found": len(all_files_to_process),
                        "deleted_count": total_deleted,
                        "failed_files": [],
                        "file_limit_exceeded": True,
                    }
                else:
                    total_found = len(all_files_to_process)
                    all_files_to_process = all_files_to_process[:available_slots]
                    await self._update_sync_status(
                        "syncing",
                        error=(
                            f"Only syncing {available_slots} of {total_found} "
                            f"files due to {max_files}-file limit."
                        ),
                    )

            # Count existing Google Drive files that aren't being re-processed
            processing_item_ids = {f["item"]["id"] for f in all_files_to_process}
            already_synced = sum(
                1
                for f in current_files
                if f.id.startswith("googledrive-")
                and f.id.removeprefix("googledrive-") not in processing_item_ids
            )

            total_files = len(all_files_to_process) + already_synced
            log.info(
                f"Total files to process: {len(all_files_to_process)} "
                f"({already_synced} already synced, {total_files} total)"
            )

            # Process all files in parallel with controlled concurrency
            max_concurrent = FILE_PROCESSING_MAX_CONCURRENT.value
            semaphore = asyncio.Semaphore(max_concurrent)
            processed_count = already_synced
            failed_count = 0
            results_lock = asyncio.Lock()
            cancelled = False

            async def process_with_semaphore(
                file_info: Dict[str, Any], index: int
            ) -> Optional[FailedFile]:
                nonlocal processed_count, failed_count, cancelled

                if self._check_cancelled():
                    cancelled = True
                    return FailedFile(
                        filename=file_info.get("name", "unknown"),
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message="Sync cancelled by user",
                    )

                async with semaphore:
                    if cancelled or self._check_cancelled():
                        cancelled = True
                        return FailedFile(
                            filename=file_info.get("name", "unknown"),
                            error_type=SyncErrorType.PROCESSING_ERROR.value,
                            error_message="Sync cancelled by user",
                        )

                    try:
                        result = await self._process_file_info(file_info)

                        async with results_lock:
                            if result is None:
                                processed_count += 1
                            else:
                                failed_count += 1

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

            tasks = [
                process_with_semaphore(file_info, i)
                for i, file_info in enumerate(all_files_to_process)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

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

                for source in self.sources:
                    source.pop("page_token", None)
                    source.pop("folder_map", None)
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

            # Save updated sources
            await self._save_sources()

            failed_files_dicts = [asdict(f) for f in failed_files]

            # Update final sync status
            knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
            meta = knowledge.meta or {}
            sync_info = meta.get("google_drive_sync", {})
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
            meta["google_drive_sync"] = sync_info
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
            await self._update_sync_status("failed", error=str(e))
            raise

        finally:
            if self._client:
                await self._client.close()

    async def _handle_deleted_item(self, item: Dict[str, Any]):
        """Handle a deleted item from changes query."""
        item_id = item.get("id")
        if not item_id:
            return

        file_id = f"googledrive-{item_id}"

        existing = Files.get_file_by_id(file_id)
        if existing:
            log.info(f"Removing deleted Google Drive file from KB: {file_id}")

            Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file_id)

            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={"file_id": file_id},
                )
            except Exception as e:
                log.warning(f"Failed to remove vectors for {file_id} from KB: {e}")

            remaining_refs = Knowledges.get_knowledge_files_by_file_id(file_id)
            if not remaining_refs:
                log.info(f"No remaining references to {file_id}, cleaning up")
                await asyncio.to_thread(DeletionService.delete_file, file_id)
            else:
                log.info(
                    f"File {file_id} still referenced by {len(remaining_refs)} KB(s), preserving"
                )

    async def _download_file_content(self, item: Dict[str, Any]) -> bytes:
        """Download file content, using export for Workspace files."""
        file_id = item["id"]
        mime_type = item.get("mimeType", "")

        if mime_type in GOOGLE_WORKSPACE_EXPORT_MAP:
            export_mime, _ = GOOGLE_WORKSPACE_EXPORT_MAP[mime_type]
            return await self._client.export_file(file_id, export_mime)
        else:
            return await self._client.download_file(file_id)

    async def _process_file_info(self, file_info: Dict[str, Any]) -> Optional[FailedFile]:
        """Download and process a single file.

        Returns:
            None on success, FailedFile on error
        """
        item = file_info["item"]
        item_id = item["id"]
        name = file_info["name"]
        source_item_id = file_info.get("source_item_id")
        relative_path = file_info.get("relative_path", name)

        log.info(f"Processing file: {name} (id: {item_id}, relative_path: {relative_path})")

        if self._check_cancelled():
            log.info(f"Sync cancelled, skipping file {name}")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message="Sync cancelled by user",
            )

        from open_webui.services.google_drive.sync_events import emit_file_processing

        await emit_file_processing(
            user_id=self.user_id,
            knowledge_id=self.knowledge_id,
            file_info={
                "item_id": item_id,
                "name": name,
                "size": item.get("size", 0),
                "source_item_id": source_item_id,
                "relative_path": relative_path,
            },
        )

        # Download file content
        try:
            content = await self._download_file_content(item)
        except Exception as e:
            log.warning(f"Failed to download file {name}: {e}")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.DOWNLOAD_ERROR.value,
                error_message=f"Download failed: {str(e)[:80]}",
            )

        if not content or len(content) == 0:
            log.warning(f"File {name} has no content")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.EMPTY_CONTENT.value,
                error_message="File is empty",
            )

        # Calculate hash for change detection
        content_hash = hashlib.sha256(content).hexdigest()

        file_id = f"googledrive-{item_id}"
        existing = Files.get_file_by_id(file_id)

        if existing and existing.hash == content_hash:
            log.info(f"File {file_id} unchanged (hash match), ensuring KB association")

            new_relative_path = file_info.get("relative_path")
            existing_meta = existing.meta or {}
            if new_relative_path and existing_meta.get("relative_path") != new_relative_path:
                existing_meta["relative_path"] = new_relative_path
                Files.update_file_by_id(
                    file_id, FileUpdateForm(meta=existing_meta)
                )
                log.info(f"Updated {file_id} meta with relative_path: {new_relative_path}")

            Knowledges.add_file_to_knowledge_by_id(
                self.knowledge_id, file_id, self.user_id
            )
            result = await self._ensure_vectors_in_kb(file_id)
            if result:
                if result.error_type == SyncErrorType.EMPTY_CONTENT.value:
                    log.info(
                        f"File {file_id} has no extractable content, skipping vectorisation"
                    )
                    return None
                log.warning(
                    f"File {file_id} vectors missing despite hash match, "
                    f"will re-process from scratch: {result.error_message}"
                )
            else:
                file_record = Files.get_file_by_id(file_id)
                if file_record:
                    from open_webui.services.google_drive.sync_events import emit_file_added

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

        # Save to storage
        temp_filename = f"{file_id}_{name}"
        try:
            contents, file_path = Storage.upload_file(
                io.BytesIO(content),
                temp_filename,
                {
                    "OpenWebUI-User-Id": self.user_id,
                    "OpenWebUI-File-Id": file_id,
                    "OpenWebUI-Source": "google_drive",
                    "OpenWebUI-GoogleDrive-Item-Id": item_id,
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
            if existing:
                Files.update_file_by_id(
                    file_id,
                    FileUpdateForm(
                        hash=content_hash,
                        meta={
                            "name": name,
                            "content_type": self._get_content_type(name),
                            "size": len(content),
                            "source": "google_drive",
                            "google_drive_item_id": item_id,
                            "source_item_id": source_item_id,
                            "relative_path": relative_path,
                            "last_synced_at": int(time.time()),
                        },
                    ),
                )
                Files.update_file_path_by_id(file_id, file_path)
                log.info(f"Updated existing file record: {file_id}")
            else:
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
                        "source": "google_drive",
                        "google_drive_item_id": item_id,
                        "source_item_id": source_item_id,
                        "relative_path": relative_path,
                        "last_synced_at": int(time.time()),
                    },
                )
                Files.insert_new_file(self.user_id, file_form)
                log.info(f"Created new file record: {file_id}")

            # Process file via internal API call
            failed = await self._process_file_via_api(file_id, name)
            if failed:
                return failed

            if self._check_cancelled():
                log.info(f"Sync cancelled, skipping KB association for {file_id}")
                return FailedFile(
                    filename=name,
                    error_type=SyncErrorType.PROCESSING_ERROR.value,
                    error_message="Sync cancelled by user",
                )

            Knowledges.add_file_to_knowledge_by_id(
                self.knowledge_id,
                file_id,
                self.user_id,
            )
            log.info(f"Added file to knowledge base: {file_id}")

            # Propagate updated vectors to other KBs
            try:
                knowledge_files = Knowledges.get_knowledge_files_by_file_id(file_id)
                for kf in knowledge_files:
                    if kf.knowledge_id != self.knowledge_id:
                        log.info(
                            f"Propagating updated vectors for {file_id} to KB {kf.knowledge_id}"
                        )
                        try:
                            VECTOR_DB_CLIENT.delete(
                                collection_name=kf.knowledge_id,
                                filter={"file_id": file_id},
                            )
                        except Exception as e:
                            log.warning(
                                f"Failed to remove old vectors from KB {kf.knowledge_id}: {e}"
                            )
                        try:
                            from open_webui.routers.retrieval import process_file, ProcessFileForm
                            await asyncio.to_thread(
                                process_file,
                                self._make_request(),
                                ProcessFileForm(
                                    file_id=file_id,
                                    collection_name=kf.knowledge_id,
                                ),
                                user=self._get_user(),
                            )
                        except Exception as e:
                            log.warning(
                                f"Failed to propagate vectors to KB {kf.knowledge_id}: {e}"
                            )
            except Exception as e:
                log.warning(f"Failed to propagate vector updates for {file_id}: {e}")

            # Emit file added event
            file_record = Files.get_file_by_id(file_id)
            if file_record:
                from open_webui.services.google_drive.sync_events import emit_file_added

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

    async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
        """Copy vectors from the per-file collection into this KB's collection."""
        try:
            from open_webui.routers.retrieval import process_file, ProcessFileForm
            from fastapi import HTTPException

            process_file(
                self._make_request(),
                ProcessFileForm(
                    file_id=file_id,
                    collection_name=self.knowledge_id,
                ),
                user=self._get_user(),
            )
            return None
        except HTTPException as e:
            detail = str(e.detail) if e.detail else ""
            if e.status_code == 400 and "Duplicate content" in detail:
                return None
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Failed to copy vectors to KB: {detail}"[:100],
            )
        except ValueError as e:
            error_msg = str(e).lower()
            if "empty" in error_msg or "no content" in error_msg:
                return FailedFile(
                    filename=file_id,
                    error_type=SyncErrorType.EMPTY_CONTENT.value,
                    error_message="File has no extractable content",
                )
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Error copying vectors: {str(e)}"[:80],
            )
        except Exception as e:
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Error copying vectors: {str(e)}"[:80],
            )

    async def _process_file_via_api(self, file_id: str, filename: str) -> Optional[FailedFile]:
        """Process file by calling the retrieval processing function directly."""
        from open_webui.routers.retrieval import process_file, ProcessFileForm
        from fastapi import HTTPException

        request = self._make_request()
        user = self._get_user()

        try:
            # Step 1: Process file content
            try:
                await asyncio.to_thread(
                    process_file,
                    request,
                    ProcessFileForm(file_id=file_id),
                    user=user,
                )
                log.info(f"Successfully extracted content from file {file_id}")
            except ValueError as e:
                error_msg = str(e).lower()
                if "empty" in error_msg or "no content" in error_msg:
                    log.debug(f"File {file_id} has no extractable content")
                    return None
                raise
            except HTTPException as e:
                detail = str(e.detail) if e.detail else ""
                if e.status_code == 400 and "Duplicate content" in detail:
                    log.debug(
                        f"File {file_id} already has embeddings, skipping to knowledge base addition"
                    )
                elif e.status_code == 400 and ("No content extracted" in detail or "empty" in detail.lower()):
                    log.debug(f"File {file_id} has no extractable content")
                    return None
                else:
                    log.debug(f"Failed to process file content {file_id}: {e.status_code} - {detail}")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=detail[:100] if detail else "Processing failed",
                    )

            # Step 2: Add processed content to knowledge base collection
            try:
                await asyncio.to_thread(
                    process_file,
                    request,
                    ProcessFileForm(
                        file_id=file_id,
                        collection_name=self.knowledge_id,
                    ),
                    user=user,
                )
                log.info(f"Successfully added file {file_id} to knowledge base {self.knowledge_id}")
            except HTTPException as e:
                detail = str(e.detail) if e.detail else ""
                if e.status_code == 400 and "Duplicate content" in detail:
                    log.debug(
                        f"File {file_id} already exists in knowledge base {self.knowledge_id}"
                    )
                    return None
                else:
                    log.debug(f"Failed to add file {file_id} to knowledge base: {e.status_code} - {detail}")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=detail[:100] if detail else "Failed to add to knowledge base",
                    )

            return None
        except Exception as e:
            log.warning(f"Error processing file {file_id} ({filename}): {e}")
            return FailedFile(
                filename=filename,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )
