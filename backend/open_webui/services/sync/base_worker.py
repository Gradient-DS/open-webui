"""Base sync worker - shared logic for cloud storage sync workers."""

import asyncio
import io
import logging
import time
import hashlib
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Optional, Callable, Awaitable, Dict, Any, List
from pathlib import Path

from open_webui.internal.db import get_db
from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files, FileForm, FileUpdateForm
from open_webui.models.users import Users
from open_webui.storage.provider import Storage
from open_webui.config import FILE_PROCESSING_MAX_CONCURRENT
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.services.deletion import DeletionService
from open_webui.services.sync.constants import SyncErrorType, FailedFile, CONTENT_TYPES
from open_webui.services.sync.events import (
    emit_sync_progress,
    emit_file_processing,
    emit_file_added,
)

log = logging.getLogger(__name__)


class BaseSyncWorker(ABC):
    """Abstract base class for cloud storage sync workers.

    Subclasses must implement the abstract properties and methods to provide
    provider-specific behaviour (e.g. Google Drive, OneDrive).
    """

    # ------------------------------------------------------------------
    # Abstract properties – subclasses MUST override
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def meta_key(self) -> str:
        """Key used in knowledge meta, e.g. 'google_drive_sync'."""
        ...

    @property
    @abstractmethod
    def file_id_prefix(self) -> str:
        """Prefix for file IDs, e.g. 'googledrive-'."""
        ...

    @property
    @abstractmethod
    def event_prefix(self) -> str:
        """Prefix for Socket.IO events, e.g. 'googledrive'."""
        ...

    @property
    @abstractmethod
    def internal_request_path(self) -> str:
        """Path used when constructing internal Starlette requests."""
        ...

    @property
    @abstractmethod
    def max_files_config(self) -> int:
        """Maximum number of files allowed per sync."""
        ...

    @property
    @abstractmethod
    def source_clear_delta_keys(self) -> list[str]:
        """Keys to pop from a source on cancellation."""
        ...

    # ------------------------------------------------------------------
    # Abstract methods – subclasses MUST implement
    # ------------------------------------------------------------------

    @abstractmethod
    def _create_client(self):
        """Create and return the provider-specific API client."""
        ...

    @abstractmethod
    async def _close_client(self):
        """Close the API client."""
        ...

    @abstractmethod
    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """Check if a file item is supported for processing."""
        ...

    @abstractmethod
    async def _collect_folder_files(self, source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
        """Collect files from a folder source.

        Returns:
            Tuple of (files_to_process, deleted_count)
        """
        ...

    @abstractmethod
    async def _collect_single_file(self, source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check if a single file needs syncing.

        Returns:
            file_info dict or None if file is up-to-date.
        """
        ...

    @abstractmethod
    async def _download_file_content(self, file_info: Dict[str, Any]) -> bytes:
        """Download file content from the provider."""
        ...

    @abstractmethod
    def _get_provider_storage_headers(self, item_id: str) -> dict:
        """Return provider-specific headers for storage upload."""
        ...

    @abstractmethod
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
        """Return provider-specific metadata for the file record.

        Args:
            file_info: The full file_info dict from collection, available for
                       provider-specific fields (e.g. OneDrive's drive_id).
        """
        ...

    @abstractmethod
    async def _sync_permissions(self) -> None:
        """Sync provider permissions to knowledge access_control."""
        ...

    @abstractmethod
    async def _verify_source_access(self, source: Dict[str, Any]) -> bool:
        """Verify the user can still access a source."""
        ...

    @abstractmethod
    async def _handle_revoked_source(self, source: Dict[str, Any]) -> int:
        """Remove all files associated with a revoked source.

        Returns:
            Count of removed files.
        """
        ...

    # ------------------------------------------------------------------
    # Shared implementation
    # ------------------------------------------------------------------

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
        self._client = None

    def _make_request(self):
        """Construct a minimal Request for calling retrieval functions directly."""
        from starlette.requests import Request
        from starlette.datastructures import Headers

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": self.internal_request_path,
                "query_string": b"",
                "headers": Headers({}).raw,
                "app": self.app,
            }
        )

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
            sync_info = meta.get(self.meta_key, {})
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
            sync_info = meta.get(self.meta_key, {})
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
            meta[self.meta_key] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

        # Convert failed_files to dicts for serialization
        failed_files_dicts = [asdict(f) for f in failed_files] if failed_files else None

        await emit_sync_progress(
            self.event_prefix,
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

    def _get_content_type(self, filename: str) -> str:
        """Get MIME type from filename."""
        ext = Path(filename).suffix.lower()
        return CONTENT_TYPES.get(ext, "application/octet-stream")

    async def _save_sources(self):
        """Save updated sources to knowledge metadata."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})
        sync_info["sources"] = self.sources
        meta[self.meta_key] = sync_info

        Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

    async def _handle_deleted_item(self, item: Dict[str, Any]):
        """Handle a deleted item from changes query."""
        item_id = item.get("id")
        if not item_id:
            return

        file_id = f"{self.file_id_prefix}{item_id}"

        existing = Files.get_file_by_id(file_id)
        if existing:
            log.info(f"Removing deleted file from KB: {file_id}")

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
                log.info(f"File {file_id} still referenced by {len(remaining_refs)} KB(s), preserving")

    async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
        """Copy vectors from the per-file collection into this KB's collection."""
        try:
            from open_webui.routers.retrieval import process_file, ProcessFileForm
            from fastapi import HTTPException

            with get_db() as db:
                process_file(
                    self._make_request(),
                    ProcessFileForm(
                        file_id=file_id,
                        collection_name=self.knowledge_id,
                    ),
                    user=self._get_user(),
                    db=db,
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

        def _call_process_file(form_data):
            """Wrapper that provides a fresh DB session for direct process_file calls."""
            with get_db() as db:
                return process_file(request, form_data, user=user, db=db)

        try:
            # Step 1: Process file content
            try:
                await asyncio.to_thread(
                    _call_process_file,
                    ProcessFileForm(file_id=file_id),
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
                    log.debug(f"File {file_id} already has embeddings, skipping to knowledge base addition")
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
                    _call_process_file,
                    ProcessFileForm(
                        file_id=file_id,
                        collection_name=self.knowledge_id,
                    ),
                )
                log.info(f"Successfully added file {file_id} to knowledge base {self.knowledge_id}")
            except HTTPException as e:
                detail = str(e.detail) if e.detail else ""
                if e.status_code == 400 and "Duplicate content" in detail:
                    log.debug(f"File {file_id} already exists in knowledge base {self.knowledge_id}")
                    return None
                else:
                    log.debug(f"Failed to add file {file_id} to knowledge base: {e.status_code} - {detail}")
                    return FailedFile(
                        filename=filename,
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=(detail[:100] if detail else "Failed to add to knowledge base"),
                    )

            return None
        except Exception as e:
            log.warning(f"Error processing file {file_id} ({filename}): {e}")
            return FailedFile(
                filename=filename,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )

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

        await emit_file_processing(
            self.event_prefix,
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
            content = await self._download_file_content(file_info)
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

        file_id = f"{self.file_id_prefix}{item_id}"
        existing = Files.get_file_by_id(file_id)

        if existing and existing.hash == content_hash:
            log.info(f"File {file_id} unchanged (hash match), ensuring KB association")

            new_relative_path = file_info.get("relative_path")
            existing_meta = existing.meta or {}
            if new_relative_path and existing_meta.get("relative_path") != new_relative_path:
                existing_meta["relative_path"] = new_relative_path
                Files.update_file_by_id(file_id, FileUpdateForm(meta=existing_meta))
                log.info(f"Updated {file_id} meta with relative_path: {new_relative_path}")

            Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)
            result = await self._ensure_vectors_in_kb(file_id)
            if result:
                if result.error_type == SyncErrorType.EMPTY_CONTENT.value:
                    log.info(f"File {file_id} has no extractable content, skipping vectorisation")
                    return None
                log.warning(
                    f"File {file_id} vectors missing despite hash match, "
                    f"will re-process from scratch: {result.error_message}"
                )
            else:
                file_record = Files.get_file_by_id(file_id)
                if file_record:
                    await emit_file_added(
                        self.event_prefix,
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
            storage_headers = {
                "OpenWebUI-User-Id": self.user_id,
                "OpenWebUI-File-Id": file_id,
            }
            storage_headers.update(self._get_provider_storage_headers(item_id))

            contents, file_path = Storage.upload_file(
                io.BytesIO(content),
                temp_filename,
                storage_headers,
            )
        except Exception as e:
            log.warning(f"Failed to upload file to storage {name}: {e}")
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f"Storage upload failed: {str(e)[:80]}",
            )

        try:
            content_type = self._get_content_type(name)
            file_meta = self._get_provider_file_meta(
                item_id=item_id,
                source_item_id=source_item_id,
                relative_path=relative_path,
                name=name,
                content_type=content_type,
                size=len(content),
                file_info=file_info,
            )

            if existing:
                Files.update_file_by_id(
                    file_id,
                    FileUpdateForm(
                        hash=content_hash,
                        meta=file_meta,
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
                    meta=file_meta,
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
                        log.info(f"Propagating updated vectors for {file_id} to KB {kf.knowledge_id}")
                        try:
                            VECTOR_DB_CLIENT.delete(
                                collection_name=kf.knowledge_id,
                                filter={"file_id": file_id},
                            )
                        except Exception as e:
                            log.warning(f"Failed to remove old vectors from KB {kf.knowledge_id}: {e}")
                        try:
                            from open_webui.routers.retrieval import (
                                process_file,
                                ProcessFileForm,
                            )

                            def _call_propagate(form_data):
                                with get_db() as db:
                                    return process_file(
                                        self._make_request(),
                                        form_data,
                                        user=self._get_user(),
                                        db=db,
                                    )

                            await asyncio.to_thread(
                                _call_propagate,
                                ProcessFileForm(
                                    file_id=file_id,
                                    collection_name=kf.knowledge_id,
                                ),
                            )
                        except Exception as e:
                            log.warning(f"Failed to propagate vectors to KB {kf.knowledge_id}: {e}")
            except Exception as e:
                log.warning(f"Failed to propagate vector updates for {file_id}: {e}")

            # Emit file added event
            file_record = Files.get_file_by_id(file_id)
            if file_record:
                await emit_file_added(
                    self.event_prefix,
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

    async def sync(self) -> Dict[str, Any]:
        """Execute sync operation for all sources."""
        self._client = self._create_client()

        try:
            await self._update_sync_status("syncing", 0, 0)

            # Sync provider permissions to Knowledge access_control
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
                        f"Access to '{source.get('name', 'unknown')}' has been revoked. " f"{removed} file(s) removed."
                    ),
                )

            self.sources = verified_sources

            # Aggregate counters
            total_processed = 0
            total_failed = 0
            total_deleted = 0
            failed_files: List[FailedFile] = []

            all_files_to_process = []

            log.info(f"Starting multi-source sync for knowledge {self.knowledge_id}, " f"{len(self.sources)} sources")

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
            max_files = min(self.max_files_config, 250)
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
                            f"Only syncing {available_slots} of {total_found} " f"files due to {max_files}-file limit."
                        ),
                    )

            # Count existing provider files that aren't being re-processed
            processing_item_ids = {f["item"]["id"] for f in all_files_to_process}
            already_synced = sum(
                1
                for f in current_files
                if f.id.startswith(self.file_id_prefix)
                and f.id.removeprefix(self.file_id_prefix) not in processing_item_ids
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

            async def process_with_semaphore(file_info: Dict[str, Any], index: int) -> Optional[FailedFile]:
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

            log.info(f"Starting parallel processing of {total_files} files " f"with max {max_concurrent} concurrent")
            start_time = time.time()

            tasks = [process_with_semaphore(file_info, i) for i, file_info in enumerate(all_files_to_process)]

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
                    for key in self.source_clear_delta_keys:
                        source.pop(key, None)
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
            sync_info = meta.get(self.meta_key, {})
            sync_info["last_sync_at"] = int(time.time())
            sync_info["status"] = "completed" if total_failed == 0 else "completed_with_errors"
            sync_info["last_result"] = {
                "files_processed": total_processed,
                "files_failed": total_failed,
                "total_found": total_files,
                "deleted_count": total_deleted,
                "failed_files": failed_files_dicts,
            }
            meta[self.meta_key] = sync_info
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

            log.info(f"Sync completed for {self.knowledge_id}: " f"{total_processed} processed, {total_failed} failed")

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
            await self._close_client()
