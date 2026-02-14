"""OneDrive sync worker - Downloads and processes files from OneDrive folders."""

import asyncio
import httpx
import io
import logging
import tempfile
import os
import time
import hashlib
import uuid
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
)
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

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

# Version tracking for folder_map schema. Bump this to force a full
# re-enumeration of all folder sources on next sync (clears delta_link).
FOLDER_MAP_VERSION = 1

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
        app,
        event_emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        token_provider: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    ):
        self.knowledge_id = knowledge_id
        self.sources = sources
        self.access_token = access_token  # OneDrive Graph API token
        self.user_id = user_id
        self.app = app
        self.event_emitter = event_emitter
        self._token_provider = token_provider
        self._client: Optional[GraphClient] = None

    def _make_request(self):
        """Construct a minimal Request for calling retrieval functions directly.

        Same pattern as main.py:690-709 (lifespan mock request).
        process_file only accesses request.app.state.config and request.app.state.ef.
        """
        from starlette.requests import Request
        from starlette.datastructures import Headers

        return Request({
            "type": "http",
            "method": "POST",
            "path": "/internal/onedrive-sync",
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
            sync_info = meta.get("onedrive_sync", {})
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
            sync_info = meta.get("onedrive_sync", {})
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

        # Force full re-enumeration if folder_map is outdated or missing
        stored_version = source.get("folder_map_version", 0)
        if stored_version < FOLDER_MAP_VERSION:
            log.info(
                "Folder map version %d < %d for source %s, forcing full sync",
                stored_version, FOLDER_MAP_VERSION, source.get("name"),
            )
            delta_link = None
            source["folder_map"] = {}  # Clear stale map

        try:
            items, new_delta_link = await self._client.get_drive_delta(
                source["drive_id"], source["item_id"], delta_link
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 410:
                log.info("Delta token expired for source %s, performing full sync", source["name"])
                source["delta_link"] = None
                items, new_delta_link = await self._client.get_drive_delta(
                    source["drive_id"], source["item_id"], None
                )
            else:
                raise

        # Update source with new delta link
        source["delta_link"] = new_delta_link

        # Build folder ID → relative path map.
        # Load persisted map from previous syncs (incremental deltas may omit
        # unchanged folders, so we need the historical mapping).
        folder_map: Dict[str, str] = source.get("folder_map", {})
        # The source folder itself is always the root (empty relative path)
        folder_map[source["item_id"]] = ""

        # First pass: update folder_map with any folder items from delta.
        # Delta items may arrive in any order, so we loop until no new folders
        # can be resolved (handles nested folders whose parent appears later).
        changed = True
        folder_items = [
            item for item in items
            if "folder" in item and "@removed" not in item
        ]
        while changed:
            changed = False
            for item in folder_items:
                parent_id = item.get("parentReference", {}).get("id", "")
                if parent_id not in folder_map:
                    continue
                parent_path = folder_map[parent_id]
                new_path = f"{parent_path}/{item['name']}" if parent_path else item["name"]
                if folder_map.get(item["id"]) != new_path:
                    folder_map[item["id"]] = new_path
                    changed = True

        # Handle deleted folders
        for item in items:
            if "folder" in item and "@removed" in item:
                folder_map.pop(item.get("id", ""), None)

        # Persist updated folder_map and version back to source
        source["folder_map"] = folder_map
        source["folder_map_version"] = FOLDER_MAP_VERSION

        # Second pass: separate files and deleted items, compute relative paths
        files_to_process = []
        deleted_count = 0

        for item in items:
            if "@removed" in item:
                await self._handle_deleted_item(item)
                deleted_count += 1
            elif self._is_supported_file(item):
                parent_id = item.get("parentReference", {}).get("id", "")
                parent_path = folder_map.get(parent_id, "")
                item_name = item.get("name", "unknown")
                relative_path = (
                    f"{parent_path}/{item_name}" if parent_path else item_name
                )

                files_to_process.append(
                    {
                        "item": item,
                        "drive_id": source["drive_id"],
                        "source_type": "folder",
                        "source_item_id": source["item_id"],
                        "name": item_name,
                        "relative_path": relative_path,
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
                # Hash matches on OneDrive side, but verify the file was
                # actually processed successfully. If the file record was
                # deleted (orphan cleanup) or processing failed, re-sync it.
                file_id = f"onedrive-{source['item_id']}"
                existing = Files.get_file_by_id(file_id)
                if existing and (existing.data or {}).get("status") == "completed":
                    log.info(f"File unchanged (hash match): {source['name']}")
                    return None
                else:
                    reason = "missing" if not existing else "not processed"
                    log.info(
                        f"File {source['name']} hash matches but record {reason}, "
                        f"re-syncing"
                    )

            if not current_hash:
                log.warning(f"No hash available from OneDrive for: {source['name']}")
            elif not stored_hash:
                log.info(f"First sync for file (no stored hash): {source['name']}")
            elif current_hash != stored_hash:
                log.info(f"File changed (hash mismatch): {source['name']}")

            # Store new hash for later save
            source["content_hash"] = current_hash

            return {
                "item": item,
                "drive_id": source["drive_id"],
                "source_type": "file",
                "source_item_id": source["item_id"],
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

    async def _verify_source_access(self, source: Dict[str, Any]) -> bool:
        """Verify the user can still access a OneDrive source.

        Returns True if access is valid, False if revoked.
        """
        drive_id = source.get("drive_id")
        item_id = source.get("item_id")
        source_type = source.get("type", "folder")

        try:
            item = await self._client.get_item(drive_id, item_id)
            if item is None:
                return False
            return True
        except Exception as e:
            error_str = str(e).lower()
            if (
                "404" in error_str
                or "403" in error_str
                or "not found" in error_str
                or "access denied" in error_str
            ):
                log.warning(
                    f"User {self.user_id} lost access to {source_type} "
                    f"{drive_id}/{item_id}: {e}"
                )
                return False
            # For other errors (network, timeout), assume access is still valid
            # to avoid accidentally removing files
            log.warning(
                f"Error verifying access to {source_type} {drive_id}/{item_id}: {e}"
            )
            return True

    async def _handle_revoked_source(self, source: Dict[str, Any]) -> int:
        """Remove all files associated with a revoked source from this KB.

        Returns the number of files removed.
        """
        source_name = source.get("name", "unknown")
        source_drive_id = source.get("drive_id")
        removed_count = 0

        files = Knowledges.get_files_by_id(self.knowledge_id)
        if not files:
            return 0

        for file in files:
            if not file.id.startswith("onedrive-"):
                continue

            file_meta = file.meta or {}
            file_drive_id = file_meta.get("onedrive_drive_id")
            file_source_item_id = file_meta.get("source_item_id")
            source_item_id = source.get("item_id")

            # Precise match by source_item_id if available
            if file_source_item_id:
                if file_source_item_id != source_item_id:
                    continue
            else:
                # Legacy fallback: match by drive_id (may over-match for same-drive sources)
                if not (file_drive_id and source_drive_id and file_drive_id == source_drive_id):
                    continue

            # Matched — proceed with removal
            Knowledges.remove_file_from_knowledge_by_id(
                self.knowledge_id, file.id
            )
            # Remove vectors from KB collection
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={"file_id": file.id},
                )
            except Exception as e:
                log.warning(f"Failed to remove vectors for {file.id}: {e}")

            # Check for orphans
            remaining = Knowledges.get_knowledge_files_by_file_id(file.id)
            if not remaining:
                try:
                    VECTOR_DB_CLIENT.delete_collection(f"file-{file.id}")
                except Exception:
                    pass
                Files.delete_file_by_id(file.id)

            removed_count += 1

        log.info(
            f"Removed {removed_count} files from KB {self.knowledge_id} "
            f"due to revoked access to source '{source_name}'"
        )

        return removed_count

    async def sync(self) -> Dict[str, Any]:
        """Execute sync operation for all sources.

        Returns:
            Dict with sync results (files_processed, files_failed, failed_files, etc.)
        """
        self._client = GraphClient(self.access_token, token_provider=self._token_provider)

        try:
            await self._update_sync_status("syncing", 0, 0)

            # Sync OneDrive folder permissions to Knowledge access_control
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

            # Update sources list to only include verified sources
            self.sources = verified_sources

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

            # Apply file count limit for external KBs (250 cap)
            max_files = min(ONEDRIVE_MAX_FILES_PER_SYNC, 250)
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

            # Count existing OneDrive files that aren't being re-processed,
            # so the progress counter reflects the full KB file count.
            processing_item_ids = {f["item"]["id"] for f in all_files_to_process}
            already_synced = sum(
                1
                for f in current_files
                if f.id.startswith("onedrive-")
                and f.id.removeprefix("onedrive-") not in processing_item_ids
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

                # Check for cancellation
                if self._check_cancelled():
                    cancelled = True
                    return FailedFile(
                        filename=file_info.get("name", "unknown"),
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message="Sync cancelled by user",
                    )

                async with semaphore:
                    # Re-check cancellation after waiting for semaphore slot
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
        """Handle a deleted item from delta query.

        Only removes the KB association for this KB. The underlying File record
        and vector collection are preserved if other KBs still reference the file.
        """
        item_id = item.get("id")
        if not item_id:
            return

        file_id = f"onedrive-{item_id}"

        existing = Files.get_file_by_id(file_id)
        if existing:
            log.info(f"Removing deleted OneDrive file from KB: {file_id}")

            # Remove association from this KB only
            Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file_id)

            # Remove vectors from this KB's collection
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={"file_id": file_id},
                )
            except Exception as e:
                log.warning(f"Failed to remove vectors for {file_id} from KB: {e}")

            # Only delete the file if no other KBs reference it
            remaining_refs = Knowledges.get_knowledge_files_by_file_id(file_id)
            if not remaining_refs:
                log.info(f"No remaining references to {file_id}, cleaning up")
                try:
                    VECTOR_DB_CLIENT.delete_collection(f"file-{file_id}")
                except Exception as e:
                    log.warning(f"Failed to delete collection for {file_id}: {e}")
                Files.delete_file_by_id(file_id)
            else:
                log.info(
                    f"File {file_id} still referenced by {len(remaining_refs)} KB(s), preserving"
                )

    async def _process_file_info(self, file_info: Dict[str, Any]) -> Optional[FailedFile]:
        """Download and process a single file from file_info structure.

        Returns:
            None on success, FailedFile on error
        """
        item = file_info["item"]
        drive_id = file_info["drive_id"]
        item_id = item["id"]
        name = item["name"]
        source_item_id = file_info.get("source_item_id")
        relative_path = file_info.get("relative_path", name)

        log.info(f"Processing file: {name} (id: {item_id}, relative_path: {relative_path})")

        # Emit processing started event for progressive UI updates
        from open_webui.services.onedrive.sync_events import emit_file_processing

        await emit_file_processing(
            user_id=self.user_id,
            knowledge_id=self.knowledge_id,
            file_info={
                "item_id": item_id,
                "name": name,
                "size": item.get("size", 0),
                "source_item_id": source_item_id,
                "relative_path": file_info.get("relative_path"),
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
            log.info(f"File {file_id} unchanged (hash match), ensuring KB association")

            # Update meta with relative_path if missing or changed (migration for pre-existing files)
            new_relative_path = file_info.get("relative_path")
            existing_meta = existing.meta or {}
            if new_relative_path and existing_meta.get("relative_path") != new_relative_path:
                existing_meta["relative_path"] = new_relative_path
                Files.update_file_by_id(
                    file_id, FileUpdateForm(meta=existing_meta)
                )
                log.info(f"Updated {file_id} meta with relative_path: {new_relative_path}")

            # Create KnowledgeFile association if not exists
            Knowledges.add_file_to_knowledge_by_id(
                self.knowledge_id, file_id, self.user_id
            )
            # Copy vectors from per-file collection to KB collection
            result = await self._ensure_vectors_in_kb(file_id)
            if result:
                # Vectors missing or corrupt (e.g. Weaviate collections were deleted).
                # Fall through to re-upload + re-process the file from scratch
                # instead of returning the error permanently.
                log.warning(
                    f"File {file_id} vectors missing despite hash match, "
                    f"will re-process from scratch: {result.error_message}"
                )
            else:
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

                return None  # Success - no re-processing needed

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
                            "onedrive_drive_id": drive_id,
                            "source_item_id": source_item_id,
                            "relative_path": file_info.get("relative_path"),
                            "last_synced_at": int(time.time()),
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
                        "onedrive_drive_id": drive_id,
                        "source_item_id": source_item_id,
                        "relative_path": file_info.get("relative_path"),
                        "last_synced_at": int(time.time()),
                    },
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

            # Propagate updated vectors to other KBs that reference this file
            # (best-effort: if propagation fails, the other KB gets updated on its next sync)
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
                        # Copy new vectors via direct function call
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

    async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
        """Copy vectors from the per-file collection into this KB's collection.

        Used for cross-user dedup: when a file already exists and doesn't need
        re-processing, we still need to copy its vectors into the current KB.
        """
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
            return None  # Success
        except HTTPException as e:
            detail = str(e.detail) if e.detail else ""
            if e.status_code == 400 and "Duplicate content" in detail:
                return None  # Already in KB, treat as success
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Failed to copy vectors to KB: {detail}"[:100],
            )
        except Exception as e:
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Error copying vectors: {str(e)}"[:80],
            )

    async def _process_file_via_api(self, file_id: str, filename: str) -> Optional[FailedFile]:
        """Process file by calling the retrieval processing function directly.

        Two-step process:
        1. First call WITHOUT collection_name to extract and process file content
        2. Second call WITH collection_name to add the processed content to knowledge base

        This is needed because when collection_name is provided, the retrieval function
        assumes the file has already been processed and tries to use existing vectors
        or file.data.content, which are empty for newly downloaded OneDrive files.

        Returns:
            None on success, FailedFile on error
        """
        from open_webui.routers.retrieval import process_file, ProcessFileForm
        from fastapi import HTTPException

        request = self._make_request()
        user = self._get_user()

        try:
            # Step 1: Process file content (extract text, create embeddings in file-{id} collection)
            # Must run in a thread because process_file -> save_docs_to_vector_db uses
            # asyncio.run() internally, which fails if called from a running event loop.
            try:
                await asyncio.to_thread(
                    process_file,
                    request,
                    ProcessFileForm(file_id=file_id),
                    user=user,
                )
                log.info(f"Successfully extracted content from file {file_id}")
            except HTTPException as e:
                detail = str(e.detail) if e.detail else ""
                if e.status_code == 400 and "Duplicate content" in detail:
                    log.debug(
                        f"File {file_id} already has embeddings, skipping to knowledge base addition"
                    )
                elif e.status_code == 400 and ("No content extracted" in detail or "empty" in detail.lower()):
                    log.debug(f"File {file_id} has no extractable content")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.EMPTY_CONTENT.value,
                        error_message="File has no extractable content",
                    )
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
                    return None  # Success - file is already in the knowledge base
                else:
                    log.debug(f"Failed to add file {file_id} to knowledge base: {e.status_code} - {detail}")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=detail[:100] if detail else "Failed to add to knowledge base",
                    )

            return None  # Success
        except Exception as e:
            log.warning(f"Error processing file {file_id} ({filename}): {e}")
            return FailedFile(
                filename=filename,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )
