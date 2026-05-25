"""Base sync worker - shared logic for cloud storage sync workers."""

import asyncio
import io
import logging
import os
import time
import hashlib
import uuid

import httpx
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Optional, Callable, Awaitable, Dict, Any, List, Union
from pathlib import Path

from open_webui.internal.db import get_db
from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files, FileForm, FileUpdateForm
from open_webui.models.users import Users
from open_webui.storage.provider import Storage
from open_webui.config import FILE_PROCESSING_MAX_CONCURRENT, KNOWLEDGE_MAX_FILE_COUNT
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.services.deletion import DeletionService
from open_webui.services.sync.constants import SyncErrorType, FailedFile, CONTENT_TYPES
from open_webui.services.sync.events import (
    emit_sync_progress,
    emit_file_processing,
    emit_file_added,
)
from open_webui.services.sync.pipeline_client import PipelineClient

log = logging.getLogger(__name__)


# Maps loader-worker `error_code` strings (see
# genai-utils/api/gateway/loader_worker/error_codes.py) to OWUI's
# `SyncErrorType` enum used by the failed-files toast. Duplicated here on
# purpose: OWUI doesn't import from genai-utils, and the cross-repo coupling
# is one-way (loader-worker emits, OWUI consumes).
_LOADER_ERROR_CODE_TO_SYNC_TYPE: dict[str, SyncErrorType] = {
    'cancelled': SyncErrorType.PROCESSING_ERROR,
    'needs_token_refresh': SyncErrorType.NEEDS_TOKEN_REFRESH,
    'hard_source_error': SyncErrorType.DOWNLOAD_ERROR,
    'empty_extraction': SyncErrorType.EMPTY_CONTENT,
    'doc_processor_schema_error': SyncErrorType.SCHEMA_ERROR,
    'config_error': SyncErrorType.CONFIG_ERROR,
    'unsupported_content_type': SyncErrorType.UNSUPPORTED_CONTENT_TYPE,
    'source_access_revoked': SyncErrorType.SOURCE_ACCESS_REVOKED,
    'unexpected_error': SyncErrorType.PROCESSING_ERROR,
}


# Error codes that are *terminal per-item*: re-running the sync won't fix them
# because the file's content/type/state, not the sync infrastructure, is the
# problem. We let the delta cursor advance past these so a single bad file
# (e.g. a .png a user picked, or an empty docx) doesn't force every
# subsequent sync to walk the entire folder tree from scratch.
#
# Codes that mean "transient — try again next time" (needs_token_refresh,
# hard_source_error, config_error, unexpected_error, cancelled) keep the
# cursor frozen so the failed item gets re-enumerated on the next sync.
_NON_RETRYABLE_LOADER_ERROR_CODES: frozenset[str] = frozenset(
    {
        'empty_extraction',
        'doc_processor_schema_error',
        'unsupported_content_type',
        # Source access permanently revoked: the file is gone for this
        # credential. Advance the cursor — re-running the sync won't
        # bring it back, and freezing the cursor would force a full
        # re-walk of the folder tree on every subsequent sync.
        'source_access_revoked',
    }
)


class ConfigurationError(RuntimeError):
    """Raised when a sync prerequisite (env var, etc.) is missing or invalid.

    Surfaces as a clean toast in the UI; the sync never enters the
    loader-worker. Typical case: ``WEBUI_PUBLIC_BASE_URL`` is unset, so the
    loader-worker would push to a relative URL and fail with cryptic httpx
    errors per item (the 2026-04-29 staging incident pattern).
    """


def _validate_callback_base_url(url: str) -> None:
    """Reject empty / scheme-less callback URLs with a clear ConfigurationError."""
    if not url:
        raise ConfigurationError(
            'WEBUI_PUBLIC_BASE_URL / OPENWEBUI_BASE_URL is not set; '
            'the loader-worker callback would be unreachable. '
            'Set it on the OWUI pod to http(s)://<host>.'
        )
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ConfigurationError(
            f'callback_base_url is invalid: {url!r}. Set WEBUI_PUBLIC_BASE_URL to http(s)://<host> on the OWUI pod.'
        )


# Bound on how long _track_job_progress will poll the loader-worker before
# giving up and fail-marking the still-pending stub File rows. Without this,
# a stuck loader-worker (the 2026-04-29 staging incident) leaves spinners
# spinning indefinitely and blocks user-initiated re-syncs. Defaults to 30
# minutes — well above any realistic batch — and overridable via env for
# tenants with very large initial syncs.
MAX_JOB_WALL_CLOCK_SECONDS = int(os.environ.get('SYNC_MAX_JOB_WALL_CLOCK_SECONDS', '1800'))


@dataclass
class PreparedFile:
    """File that has been downloaded and stored, ready for content extraction."""

    file_id: str
    file_info: Dict[str, Any]
    name: str
    content_hash: str
    is_new: bool  # True if newly downloaded, False if hash-matched


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
    def provider_slug(self) -> str:
        """Provider slug used in INTEGRATION_PROVIDERS / loader-worker payloads.

        e.g. 'onedrive', 'google_drive'. Echoed by the loader-worker on
        ``/api/v1/integrations/ingest`` callbacks via ``X-Acting-Provider`` so
        the ingest endpoint can resolve the right provider config.
        """
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
        """Download file content from the provider.

        Removed in cleanup commit after USE_SHARED_LOADER rollout completes —
        the per-tenant loader-worker pod owns the download path.
        """
        ...

    def _item_from_file_info(self, file_info: Dict[str, Any], access_token: str) -> Dict[str, Any]:
        """Build a loader-worker job item dict from a discovered file_info.

        Used in shared-loader mode (USE_SHARED_LOADER=true). Providers
        override to supply provider-specific ``source_descriptor`` fields the
        loader-worker's ``SourceClient`` knows how to interpret. Default
        implementation produces a generic item shape.
        """
        item = file_info['item']
        item_id = item['id']
        name = file_info['name']
        source_item_id = file_info.get('source_item_id')
        relative_path = file_info.get('relative_path', name)
        content_type = self._get_content_type(name)
        size = item.get('size', 0)

        metadata = self._get_provider_file_meta(
            item_id=item_id,
            source_item_id=source_item_id,
            relative_path=relative_path,
            name=name,
            content_type=content_type,
            size=size,
            file_info=file_info,
        )

        return {
            'source': self.provider_slug,
            'source_descriptor': file_info,
            'source_credential': access_token,
            'credential_type': 'user_oauth',
            'file_id': f'{self.file_id_prefix}{item_id}',
            # Raw provider item id; sent to /ingest as doc.source_id, where
            # it's re-prefixed to f'{provider}-{source_id}'. Must NOT be the
            # already-prefixed file_id, or /ingest creates a second File row
            # with id 'onedrive-onedrive-<item>' next to the stub.
            'source_id': item_id,
            'filename': name,
            'content_type': content_type,
            'metadata': metadata,
        }

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
    def _get_cloud_hash(self, file_info: Dict[str, Any]) -> Optional[str]:
        """Extract cloud-provided hash/change indicator from item metadata.

        Returns a provider-specific hash string that can be compared across
        sync cycles to detect changes without downloading file content.
        Returns None if no hash is available.
        """
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
        use_shared_loader: bool = False,
    ):
        self.knowledge_id = knowledge_id
        self.sources = sources
        self.access_token = access_token
        self.user_id = user_id
        self.app = app
        self.event_emitter = event_emitter
        self._token_provider = token_provider
        self._client = None
        # When True, file ingestion is delegated to the per-tenant loader-worker
        # pod (see thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md).
        # The legacy in-pod download/embed pipeline stays available behind this
        # flag for instant rollback until the cleanup commit removes it.
        self._use_shared_loader = use_shared_loader
        self._pipeline_client: Optional[PipelineClient] = PipelineClient() if use_shared_loader else None

    def _make_request(self):
        """Construct a minimal Request for calling retrieval functions directly."""
        from starlette.requests import Request
        from starlette.datastructures import Headers

        return Request(
            {
                'type': 'http',
                'method': 'POST',
                'path': self.internal_request_path,
                'query_string': b'',
                'headers': Headers({}).raw,
                'app': self.app,
            }
        )

    async def _get_user(self):
        """Fetch the user object for process_file access control."""
        user = await Users.get_user_by_id(self.user_id)
        if not user:
            raise RuntimeError(f'User {self.user_id} not found')
        return user

    async def _check_cancelled(self) -> bool:
        """Check if sync has been cancelled by user."""
        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get(self.meta_key, {})
            return sync_info.get('status') == 'cancelled'
        return False

    async def _update_sync_status(
        self,
        status: str,
        current: int = 0,
        total: int = 0,
        filename: str = '',
        error: Optional[str] = None,
        files_processed: int = 0,
        files_failed: int = 0,
        deleted_count: int = 0,
        files_added: int = 0,
        files_updated: int = 0,
        files_unchanged: int = 0,
        files_removed: int = 0,
        failed_files: Optional[List[FailedFile]] = None,
        stage_counts: Optional[Dict[str, int]] = None,
    ):
        """Update sync status in knowledge meta and emit Socket.IO event.

        ``files_added`` / ``files_updated`` / ``files_unchanged`` /
        ``files_removed`` carry the toast's per-category breakdown so the UI
        can render "Added 5, Updated 2" instead of "Synced 7". When a caller
        omits them they default to 0; ``files_processed`` is preserved for
        backwards compatibility (and equals files_added + files_updated).
        """
        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get(self.meta_key, {})
            # Don't overwrite cancelled status with progress updates
            if sync_info.get('status') == 'cancelled' and status == 'syncing':
                return
            sync_info['status'] = status
            if status == 'syncing' and not sync_info.get('sync_started_at'):
                sync_info['sync_started_at'] = int(time.time())
            sync_info['progress_current'] = current
            sync_info['progress_total'] = total
            if stage_counts:
                sync_info['stage_counts'] = stage_counts
            if error:
                sync_info['error'] = error
            meta[self.meta_key] = sync_info
            await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

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
            files_added=files_added,
            files_updated=files_updated,
            files_unchanged=files_unchanged,
            files_removed=files_removed,
            failed_files=failed_files_dicts,
            stage_counts=stage_counts,
        )

        if self.event_emitter:
            await self.event_emitter(
                {
                    'type': 'sync_progress',
                    'data': {
                        'knowledge_id': self.knowledge_id,
                        'status': status,
                        'current': current,
                        'total': total,
                        'filename': filename,
                        'error': error,
                        'files_processed': files_processed,
                        'files_failed': files_failed,
                        'deleted_count': deleted_count,
                        'files_added': files_added,
                        'files_updated': files_updated,
                        'files_unchanged': files_unchanged,
                        'files_removed': files_removed,
                        'failed_files': failed_files_dicts,
                        'stage_counts': stage_counts,
                    },
                }
            )

    def _get_content_type(self, filename: str) -> str:
        """Get MIME type from filename."""
        ext = Path(filename).suffix.lower()
        return CONTENT_TYPES.get(ext, 'application/octet-stream')

    async def _save_sources(self):
        """Save updated sources to knowledge metadata."""
        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})
        sync_info['sources'] = self.sources
        meta[self.meta_key] = sync_info

        await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

    async def _handle_deleted_item(self, item: Dict[str, Any]):
        """Handle a deleted item from changes query."""
        item_id = item.get('id')
        if not item_id:
            return

        file_id = f'{self.file_id_prefix}{item_id}'

        existing = await Files.get_file_by_id(file_id)
        if existing:
            log.info(f'Removing deleted file from KB: {file_id}')

            await Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file_id)

            try:
                await ASYNC_VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={'file_id': file_id},
                )
            except Exception as e:
                log.warning(f'Failed to remove vectors for {file_id} from KB: {e}')

            remaining_refs = await Knowledges.get_knowledge_files_by_file_id(file_id)
            if not remaining_refs:
                log.info(f'No remaining references to {file_id}, cleaning up')
                await DeletionService.delete_file(file_id)
            else:
                log.info(f'File {file_id} still referenced by {len(remaining_refs)} KB(s), preserving')

    async def _handle_revoked_item(self, file_id: str) -> int:
        """Remove a single file from this KB after the loader-worker reports
        its source access was permanently revoked. Mirrors
        ``_handle_deleted_item`` but is keyed on the loader-worker error
        stream (no delta ``@removed`` marker — the provider says 403/404 on
        read instead).

        Returns 1 if a row was removed, 0 otherwise.
        """
        if not file_id or file_id == 'unknown':
            return 0
        existing = await Files.get_file_by_id(file_id)
        if not existing:
            return 0
        log.info(f'Removing revoked-access file from KB: {file_id}')
        await Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file_id)
        try:
            await ASYNC_VECTOR_DB_CLIENT.delete(
                collection_name=self.knowledge_id,
                filter={'file_id': file_id},
            )
        except Exception as e:
            log.warning(f'Failed to remove vectors for {file_id} from KB: {e}')
        remaining_refs = await Knowledges.get_knowledge_files_by_file_id(file_id)
        if not remaining_refs:
            log.info(f'No remaining references to {file_id}, cleaning up')
            await DeletionService.delete_file(file_id)
        else:
            log.info(f'File {file_id} still referenced by {len(remaining_refs)} KB(s), preserving the File row')
        try:
            from open_webui.socket.main import sio

            await sio.emit(
                f'{self.event_prefix}:file:deleted',
                {
                    'knowledge_id': self.knowledge_id,
                    'file_id': file_id,
                    'reason': 'access_revoked',
                },
                room=f'user:{self.user_id}',
            )
        except Exception as e:
            log.debug(f'Failed to emit revoked-access deletion event: {e}')
        return 1

    async def _classify_for_submit(self, file_info: Dict[str, Any]) -> tuple[str, str]:
        """Decide whether to submit this file_info to the loader-worker.

        Returns (category, file_id):
          - ('unchanged', file_id) — cloud_hash matches stored AND row is 'completed'; SKIP submission
          - ('updated', file_id)   — existing row but hash mismatch or non-completed; SUBMIT
          - ('added', file_id)     — no existing row; SUBMIT

        Mirrors the legacy short-circuit at ``_download_and_store_legacy``
        (the cloud-hash check around line 912-921) so the shared-loader path
        stops re-processing files that haven't changed — the structural cause
        of the "5 extra" toast where a re-sync of an unchanged folder showed
        N "synced" instead of "no changes".
        """
        item = file_info['item']
        item_id = item['id']
        file_id = f'{self.file_id_prefix}{item_id}'
        existing = await Files.get_file_by_id(file_id)
        if existing is None:
            return 'added', file_id
        cloud_hash = self._get_cloud_hash(file_info)
        if not cloud_hash:
            # No hash available (e.g. provider didn't return one) — be
            # conservative and treat as updated. The unchanged short-circuit
            # only fires on a positive hash match.
            return 'updated', file_id
        stored = (existing.meta or {}).get('cloud_hash')
        status = (existing.data or {}).get('status')
        if stored == cloud_hash and status == 'completed':
            return 'unchanged', file_id
        return 'updated', file_id

    async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
        """Verify vectors for this file exist in the KB collection.

        Queries the KB collection filtered by file_id. If vectors are found,
        the file is already indexed — no work needed. If not found, returns a
        FailedFile so the orchestrator falls back to full re-processing.

        Also performs gradual cleanup: if a legacy per-file collection
        (file-{file_id}) exists, delete it.
        """
        try:

            def _check():
                log.info(f'[sync:ensure:{file_id}] >>> KB QUERY START')
                t0 = time.time()

                # Check if vectors already exist in KB collection
                result = VECTOR_DB_CLIENT.query(
                    collection_name=self.knowledge_id,
                    filter={'file_id': file_id},
                    limit=1,
                )

                has_vectors = result is not None and len(result.ids) > 0 and len(result.ids[0]) > 0

                # Gradual cleanup: remove legacy per-file collection if it exists
                file_collection = f'file-{file_id}'
                if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                    log.info(f'[sync:ensure:{file_id}] Cleaning up legacy per-file collection')
                    VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)

                log.info(
                    f'[sync:ensure:{file_id}] <<< KB QUERY END ({time.time() - t0:.1f}s) has_vectors={has_vectors}'
                )
                return has_vectors

            has_vectors = await asyncio.to_thread(_check)

            if has_vectors:
                return None  # Success — vectors already in KB collection

            # No vectors found — signal for re-processing
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Vectors not found in KB collection',
            )

        except Exception as e:
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f'Error checking vectors: {str(e)}'[:80],
            )

    async def _extract_content(self, file_id: str) -> Optional[tuple]:
        """Extract text content from a file, returning Documents for embedding.

        Uses the same extraction pipeline as process_file (external pipeline with
        internal fallback), but returns the documents instead of embedding them.

        Returns:
            Tuple of (docs, file, needs_split) or None if no content could be extracted.
            needs_split is True for internal pipeline (needs chunking), False for external
            pipeline (already chunked).
        """
        from open_webui.retrieval.loaders.main import Loader
        from open_webui.retrieval.vector.utils import filter_metadata
        from open_webui.routers.external_retrieval import call_external_pipeline
        from langchain_core.documents import Document

        request = self._make_request()
        user = await self._get_user()

        # Pre-fetch DB data BEFORE the thread — async model calls cannot run
        # inside asyncio.to_thread (Option B pattern).
        if user.role == 'admin':
            file = await Files.get_file_by_id(file_id)
        else:
            file = await Files.get_file_by_id_and_user_id(file_id, user.id)

        if not file:
            raise ValueError(f'File {file_id} not found')

        if not file.path:
            raise ValueError(f'File {file_id} has no path')

        local_file_path = Storage.get_file(file.path)

        def _extract_in_thread():
            loader = Loader(
                engine=request.app.state.config.CONTENT_EXTRACTION_ENGINE,
                user=user,
                EXTERNAL_DOCUMENT_LOADER_URL=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL,
                EXTERNAL_DOCUMENT_LOADER_API_KEY=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
                TIKA_SERVER_URL=request.app.state.config.TIKA_SERVER_URL,
                DOCLING_SERVER_URL=request.app.state.config.DOCLING_SERVER_URL,
                DOCLING_API_KEY=request.app.state.config.DOCLING_API_KEY,
                DOCLING_PARAMS=request.app.state.config.DOCLING_PARAMS,
                PDF_EXTRACT_IMAGES=request.app.state.config.PDF_EXTRACT_IMAGES,
                PDF_LOADER_MODE=request.app.state.config.PDF_LOADER_MODE,
                DATALAB_MARKER_API_KEY=request.app.state.config.DATALAB_MARKER_API_KEY,
                DATALAB_MARKER_API_BASE_URL=request.app.state.config.DATALAB_MARKER_API_BASE_URL,
                DATALAB_MARKER_ADDITIONAL_CONFIG=request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG,
                DATALAB_MARKER_SKIP_CACHE=request.app.state.config.DATALAB_MARKER_SKIP_CACHE,
                DATALAB_MARKER_FORCE_OCR=request.app.state.config.DATALAB_MARKER_FORCE_OCR,
                DATALAB_MARKER_PAGINATE=request.app.state.config.DATALAB_MARKER_PAGINATE,
                DATALAB_MARKER_STRIP_EXISTING_OCR=request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR,
                DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION=request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
                DATALAB_MARKER_FORMAT_LINES=request.app.state.config.DATALAB_MARKER_FORMAT_LINES,
                DATALAB_MARKER_USE_LLM=request.app.state.config.DATALAB_MARKER_USE_LLM,
                DATALAB_MARKER_OUTPUT_FORMAT=request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT,
                DOCUMENT_INTELLIGENCE_ENDPOINT=request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
                DOCUMENT_INTELLIGENCE_KEY=request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
                DOCUMENT_INTELLIGENCE_MODEL=request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL,
                MISTRAL_OCR_API_BASE_URL=request.app.state.config.MISTRAL_OCR_API_BASE_URL,
                MISTRAL_OCR_API_KEY=request.app.state.config.MISTRAL_OCR_API_KEY,
                MINERU_API_MODE=request.app.state.config.MINERU_API_MODE,
                MINERU_API_URL=request.app.state.config.MINERU_API_URL,
                MINERU_API_KEY=request.app.state.config.MINERU_API_KEY,
                MINERU_API_TIMEOUT=request.app.state.config.MINERU_API_TIMEOUT,
                MINERU_PARAMS=request.app.state.config.MINERU_PARAMS,
            )

            # Try external pipeline first
            external_pipeline_url = getattr(request.app.state.config, 'EXTERNAL_PIPELINE_URL', None)
            use_external_local = bool(external_pipeline_url and external_pipeline_url.strip() != '')
            docs_local = None

            if use_external_local:
                try:
                    result = call_external_pipeline(
                        file_path=local_file_path,
                        filename=file.filename,
                        content_type=file.meta.get('content_type', ''),
                        external_pipeline_url=external_pipeline_url,
                        external_pipeline_api_key=getattr(request.app.state.config, 'EXTERNAL_PIPELINE_API_KEY', None),
                        loader_instance=loader,
                    )
                    if result.get('success') and result.get('chunks'):
                        docs_local = [
                            Document(
                                page_content=chunk['text'],
                                metadata=chunk.get('metadata', {}),
                            )
                            for chunk in result['chunks']
                        ]
                except Exception as e:
                    log.warning(f'External pipeline failed for {file.filename}: {e}, falling back')
                    use_external_local = False

            if docs_local is None:
                use_external_local = False
                docs_local = loader.load(file.filename, file.meta.get('content_type'), local_file_path)

            if not docs_local:
                return None

            docs_local = [
                Document(
                    page_content=doc.page_content,
                    metadata={
                        **filter_metadata(doc.metadata),
                        'name': file.filename,
                        'created_by': file.user_id,
                        'file_id': file.id,
                        'source': file.filename,
                    },
                )
                for doc in docs_local
            ]

            return docs_local, not use_external_local  # needs_split=True for internal pipeline

        result = await asyncio.to_thread(_extract_in_thread)
        if result is None:
            return None
        docs, needs_split = result

        text_content = ' '.join([doc.page_content for doc in docs])
        # Save extracted text to file record (async, OUTSIDE the thread).
        await Files.update_file_data_by_id(file.id, {'content': text_content})

        return docs, file, needs_split

    async def _embed_to_collections(
        self,
        docs: list,
        file_id: str,
        file_hash: str,
        filename: str,
        needs_split: bool = True,
    ) -> bool:
        """Embed documents once and insert vectors into both KB and per-file collections.

        This replaces the double process_file call by generating embeddings once
        and writing the resulting vectors to both collections.
        """
        import tiktoken
        from open_webui.retrieval.utils import get_embedding_function
        from open_webui.utils.misc import sanitize_text_for_db
        from open_webui.config import RAG_EMBEDDING_CONTENT_PREFIX
        from open_webui.env import RAG_EMBEDDING_TIMEOUT
        from langchain_core.documents import Document
        from langchain_text_splitters import RecursiveCharacterTextSplitter, TokenTextSplitter
        from langchain_text_splitters import MarkdownHeaderTextSplitter

        request = self._make_request()
        user = await self._get_user()

        metadata = {
            'file_id': file_id,
            'name': filename,
            'hash': file_hash,
        }

        def _split_embed_and_store():
            """Split, embed, and store vectors (all in thread to avoid blocking event loop)."""
            t0 = time.time()
            working_docs = list(docs)

            # Split if needed (internal pipeline; external pipeline pre-chunks)
            if needs_split:
                # Markdown header splitting (if enabled)
                if request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER:
                    markdown_splitter = MarkdownHeaderTextSplitter(
                        headers_to_split_on=[
                            ('#', 'Header 1'),
                            ('##', 'Header 2'),
                            ('###', 'Header 3'),
                            ('####', 'Header 4'),
                            ('#####', 'Header 5'),
                            ('######', 'Header 6'),
                        ],
                        strip_headers=False,
                    )
                    split_docs = []
                    for doc in working_docs:
                        split_docs.extend(
                            [
                                Document(
                                    page_content=split_chunk.page_content,
                                    metadata={**doc.metadata},
                                )
                                for split_chunk in markdown_splitter.split_text(doc.page_content)
                            ]
                        )
                    working_docs = split_docs

                    if request.app.state.config.CHUNK_MIN_SIZE_TARGET > 0:
                        from open_webui.routers.retrieval import merge_docs_to_target_size

                        working_docs = merge_docs_to_target_size(request, working_docs)

                # Text splitting
                if request.app.state.config.TEXT_SPLITTER in ['', 'character']:
                    splitter = RecursiveCharacterTextSplitter(
                        chunk_size=request.app.state.config.CHUNK_SIZE,
                        chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                        add_start_index=True,
                    )
                    working_docs = splitter.split_documents(working_docs)
                elif request.app.state.config.TEXT_SPLITTER == 'token':
                    tiktoken.get_encoding(str(request.app.state.config.TIKTOKEN_ENCODING_NAME))
                    splitter = TokenTextSplitter(
                        encoding_name=str(request.app.state.config.TIKTOKEN_ENCODING_NAME),
                        chunk_size=request.app.state.config.CHUNK_SIZE,
                        chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                        add_start_index=True,
                    )
                    working_docs = splitter.split_documents(working_docs)

            if not working_docs:
                return False

            t_split = time.time()
            log.info(f'[sync:{filename}] split: {len(working_docs)} chunks in {t_split - t0:.1f}s')

            texts = [sanitize_text_for_db(doc.page_content) for doc in working_docs]
            metadatas = [
                {
                    **doc.metadata,
                    **metadata,
                    'embedding_config': {
                        'engine': request.app.state.config.RAG_EMBEDDING_ENGINE,
                        'model': request.app.state.config.RAG_EMBEDDING_MODEL,
                    },
                }
                for doc in working_docs
            ]

            # Generate embeddings
            embedding_function = get_embedding_function(
                request.app.state.config.RAG_EMBEDDING_ENGINE,
                request.app.state.config.RAG_EMBEDDING_MODEL,
                request.app.state.ef,
                (
                    request.app.state.config.RAG_OPENAI_API_BASE_URL
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == 'openai'
                    else (
                        request.app.state.config.RAG_OLLAMA_BASE_URL
                        if request.app.state.config.RAG_EMBEDDING_ENGINE == 'ollama'
                        else request.app.state.config.RAG_AZURE_OPENAI_BASE_URL
                    )
                ),
                (
                    request.app.state.config.RAG_OPENAI_API_KEY
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == 'openai'
                    else (
                        request.app.state.config.RAG_OLLAMA_API_KEY
                        if request.app.state.config.RAG_EMBEDDING_ENGINE == 'ollama'
                        else request.app.state.config.RAG_AZURE_OPENAI_API_KEY
                    )
                ),
                request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
                azure_api_version=(
                    request.app.state.config.RAG_AZURE_OPENAI_API_VERSION
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == 'azure_openai'
                    else None
                ),
                enable_async=request.app.state.config.ENABLE_ASYNC_EMBEDDING,
                concurrent_requests=request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
            )

            log.info(f'[sync:{filename}] >>> EMBED START ({len(texts)} texts)')
            future = asyncio.run_coroutine_threadsafe(
                embedding_function(
                    list(map(lambda x: x.replace('\n', ' '), texts)),
                    prefix=RAG_EMBEDDING_CONTENT_PREFIX,
                    user=user,
                ),
                request.app.state.main_loop,
            )
            embeddings = future.result(timeout=RAG_EMBEDDING_TIMEOUT)
            t_embed = time.time()
            log.info(f'[sync:{filename}] <<< EMBED END ({t_embed - t_split:.1f}s)')

            # Build vector items with separate UUIDs per collection
            items_kb = [
                {
                    'id': str(uuid.uuid4()),
                    'text': text,
                    'vector': embeddings[idx],
                    'metadata': metadatas[idx],
                }
                for idx, text in enumerate(texts)
            ]

            # Insert into KB collection (sync Weaviate calls — kept in thread
            # to avoid blocking the event loop)
            log.info(f'[sync:{filename}] >>> WEAVIATE KB INSERT START ({len(items_kb)} vectors)')
            VECTOR_DB_CLIENT.insert(collection_name=self.knowledge_id, items=items_kb)
            t_kb = time.time()
            log.info(f'[sync:{filename}] <<< WEAVIATE KB INSERT END ({t_kb - t_embed:.1f}s)')

            log.info(f'[sync:{filename}] DONE total={t_kb - t0:.1f}s')
            return True

        result = await asyncio.to_thread(_split_embed_and_store)
        if result:
            # Persist file metadata AFTER the thread — async ORM cannot run in to_thread.
            await Files.update_file_metadata_by_id(file_id, {'collection_name': self.knowledge_id})
            await Files.update_file_data_by_id(file_id, {'status': 'completed'})
            await Files.update_file_hash_by_id(file_id, file_hash)

        if not result:
            log.warning(f'No text content extracted from {filename}')
            await Files.update_file_metadata_by_id(file_id, {'collection_name': self.knowledge_id})
            await Files.update_file_data_by_id(file_id, {'status': 'completed'})
            await Files.update_file_hash_by_id(file_id, file_hash)

        return True

    async def _download_and_store(self, file_info: Dict[str, Any]) -> Union[PreparedFile, FailedFile, None]:
        """Phase 1 entrypoint. Branches on USE_SHARED_LOADER.

        In shared-loader mode, the per-tenant loader-worker pod handles
        download → parse+chunk → embed → push to /ingest. The pod itself
        creates File records on its callback, so this method returns None
        and the orchestration in ``sync()`` skips the per-item fan-out.

        Legacy mode preserves the in-pod download path verbatim under
        ``_download_and_store_legacy`` for instant rollback. Both methods
        are deleted in the cleanup commit after USE_SHARED_LOADER rollout.
        """
        if self._use_shared_loader:
            # sync() bypasses the per-item pipeline in shared mode; defensive.
            return None
        return await self._download_and_store_legacy(file_info)

    async def _download_and_store_legacy(self, file_info: Dict[str, Any]) -> Union[PreparedFile, FailedFile, None]:
        """Phase 1: Download from cloud, check hash, upload to S3, create file record.

        Returns:
            PreparedFile if file needs processing
            None if file is unchanged (hash match, vectors verified)
            FailedFile on error
        """
        item = file_info['item']
        item_id = item['id']
        name = file_info['name']
        source_item_id = file_info.get('source_item_id')
        relative_path = file_info.get('relative_path', name)
        file_id = f'{self.file_id_prefix}{item_id}'

        if await self._check_cancelled():
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Sync cancelled by user',
            )

        # Pre-download cloud hash check — skip download if cloud reports no change.
        # Existing KBs without cloud_hash in meta will fall through to download,
        # populating cloud_hash for subsequent syncs (backward compatible).
        cloud_hash = self._get_cloud_hash(file_info)
        existing = await Files.get_file_by_id(file_id)

        if cloud_hash and existing:
            existing_meta = existing.meta or {}
            stored_cloud_hash = existing_meta.get('cloud_hash')
            if stored_cloud_hash and stored_cloud_hash == cloud_hash:
                log.info(f'File {file_id} unchanged (cloud hash match), skipping download')

                new_relative_path = file_info.get('relative_path')
                if new_relative_path and existing_meta.get('relative_path') != new_relative_path:
                    existing_meta['relative_path'] = new_relative_path
                    await Files.update_file_by_id(file_id, FileUpdateForm(meta=existing_meta))

                await Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)

                return PreparedFile(
                    file_id=file_id,
                    file_info=file_info,
                    name=name,
                    content_hash=existing.hash,
                    is_new=False,
                )

        log.info(f'Downloading file: {name} (id: {item_id})')

        await emit_file_processing(
            self.event_prefix,
            user_id=self.user_id,
            knowledge_id=self.knowledge_id,
            file_info={
                'item_id': item_id,
                'name': name,
                'size': item.get('size', 0),
                'source_item_id': source_item_id,
                'relative_path': relative_path,
            },
        )

        # Download file content
        try:
            content = await self._download_file_content(file_info)
        except Exception as e:
            log.warning(f'Failed to download file {name}: {e}')
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.DOWNLOAD_ERROR.value,
                error_message=f'Download failed: {str(e)[:80]}',
            )

        if not content or len(content) == 0:
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.EMPTY_CONTENT.value,
                error_message='File is empty',
            )

        # Post-download content hash check
        content_hash = hashlib.sha256(content).hexdigest()

        if existing and existing.hash == content_hash:
            log.info(f'File {file_id} unchanged (content hash match)')

            existing_meta = existing.meta or {}
            updated = False

            new_relative_path = file_info.get('relative_path')
            if new_relative_path and existing_meta.get('relative_path') != new_relative_path:
                existing_meta['relative_path'] = new_relative_path
                updated = True

            # Store cloud hash so next sync can skip the download
            if cloud_hash and existing_meta.get('cloud_hash') != cloud_hash:
                existing_meta['cloud_hash'] = cloud_hash
                updated = True

            if updated:
                await Files.update_file_by_id(file_id, FileUpdateForm(meta=existing_meta))

            await Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)

            # Return PreparedFile with is_new=False so vector verification
            # runs under the process semaphore (not the download semaphore).
            return PreparedFile(
                file_id=file_id,
                file_info=file_info,
                name=name,
                content_hash=content_hash,
                is_new=False,
            )

        # Upload to storage
        temp_filename = f'{file_id}_{name}'
        try:
            storage_headers = {
                'OpenWebUI-User-Id': self.user_id,
                'OpenWebUI-File-Id': file_id,
            }
            storage_headers.update(self._get_provider_storage_headers(item_id))

            contents, file_path = Storage.upload_file(
                io.BytesIO(content),
                temp_filename,
                storage_headers,
            )
        except Exception as e:
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f'Storage upload failed: {str(e)[:80]}',
            )

        # Create/update file record
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

            # Store cloud hash for pre-download skip on next sync
            if cloud_hash:
                file_meta['cloud_hash'] = cloud_hash

            if existing:
                await Files.update_file_by_id(
                    file_id,
                    FileUpdateForm(hash=content_hash, meta=file_meta),
                )
                await Files.update_file_path_by_id(file_id, file_path)
            else:
                file_form = FileForm(
                    id=file_id,
                    filename=name,
                    path=file_path,
                    hash=content_hash,
                    data={},
                    meta=file_meta,
                )
                await Files.insert_new_file(self.user_id, file_form)

            return PreparedFile(
                file_id=file_id,
                file_info=file_info,
                name=name,
                content_hash=content_hash,
                is_new=not existing,
            )
        except Exception as e:
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )

    async def _process_and_embed(self, prepared: PreparedFile) -> Optional[FailedFile]:
        """Phase 2 entrypoint. Branches on USE_SHARED_LOADER.

        In shared-loader mode, processing + embedding happen in the
        loader-worker pod. The legacy in-pod implementation is preserved
        under ``_process_and_embed_legacy`` for instant rollback.
        """
        if self._use_shared_loader:
            return None
        return await self._process_and_embed_legacy(prepared)

    async def _process_and_embed_legacy(self, prepared: PreparedFile) -> Optional[FailedFile]:
        """Phase 2: Extract content, embed once, insert into KB + per-file collections.

        Returns None on success, FailedFile on error.
        """
        file_id = prepared.file_id
        name = prepared.name

        if await self._check_cancelled():
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Sync cancelled by user',
            )

        try:
            # Extract content (loader / external pipeline)
            log.info(f'[sync:{name}] >>> EXTRACT START')
            t_start = time.time()
            result = await self._extract_content(file_id)
            t_extract = time.time()

            if result is None:
                log.debug(f'File {file_id} has no extractable content')
                return None

            docs, file_record, needs_split = result
            log.info(f'[sync:{name}] <<< EXTRACT END ({len(docs)} docs, {t_extract - t_start:.1f}s)')

            if not docs or not any(doc.page_content.strip() for doc in docs):
                log.debug(f'File {file_id} has no text content')
                return None

            if await self._check_cancelled():
                return FailedFile(
                    filename=name,
                    error_type=SyncErrorType.PROCESSING_ERROR.value,
                    error_message='Sync cancelled by user',
                )

            # Embed once → insert into both KB and per-file collections
            success = await self._embed_to_collections(
                docs=docs,
                file_id=file_id,
                file_hash=prepared.content_hash,
                filename=name,
                needs_split=needs_split,
            )

            if not success:
                return FailedFile(
                    filename=name,
                    error_type=SyncErrorType.PROCESSING_ERROR.value,
                    error_message='Failed to save vectors',
                )

        except Exception as e:
            log.warning(f'Error processing file {file_id} ({name}): {e}')
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=str(e)[:100],
            )

        if await self._check_cancelled():
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Sync cancelled by user',
            )

        # KB association
        await Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)

        # Cross-KB vector propagation (still uses process_file for other KBs)
        try:
            knowledge_files = await Knowledges.get_knowledge_files_by_file_id(file_id)
            for kf in knowledge_files:
                if kf.knowledge_id != self.knowledge_id:
                    log.info(f'Propagating vectors for {file_id} to KB {kf.knowledge_id}')
                    try:
                        await ASYNC_VECTOR_DB_CLIENT.delete(
                            collection_name=kf.knowledge_id,
                            filter={'file_id': file_id},
                        )
                    except Exception as e:
                        log.warning(f'Failed to remove old vectors from KB {kf.knowledge_id}: {e}')
                    try:
                        from open_webui.routers.retrieval import process_file, ProcessFileForm

                        # Pre-fetch user (now async); pass it into the thread
                        # so the sync ``process_file`` in the carve-out file
                        # doesn't try to call an async helper.
                        propagate_user = await self._get_user()

                        def _call_propagate(form_data):
                            with get_db() as db:
                                return process_file(
                                    self._make_request(),
                                    form_data,
                                    user=propagate_user,
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
                        log.warning(f'Failed to propagate vectors to KB {kf.knowledge_id}: {e}')
        except Exception as e:
            log.warning(f'Failed to propagate vector updates for {file_id}: {e}')

        # Emit file added event
        file_record = await Files.get_file_by_id(file_id)
        if file_record:
            await emit_file_added(
                self.event_prefix,
                user_id=self.user_id,
                knowledge_id=self.knowledge_id,
                file_data={
                    'id': file_record.id,
                    'filename': file_record.filename,
                    'meta': file_record.meta,
                    'created_at': file_record.created_at,
                    'updated_at': file_record.updated_at,
                },
            )

        return None

    # ------------------------------------------------------------------
    # Shared-loader orchestration (USE_SHARED_LOADER=true)
    # ------------------------------------------------------------------

    async def _submit_pipeline_job(self, files: List[Dict[str, Any]]) -> Optional[str]:
        """Submit a single loader-worker job carrying every discovered file.

        Returns the job_id. The legacy in-pod fan-out is bypassed; the
        loader-worker is responsible for download → parse+chunk → embed →
        push to /ingest, and tracks its own concurrency.
        """
        if not files:
            return None

        # Use the access token already resolved in execute_sync(). The
        # loader-worker doesn't refresh per-call — long-queued jobs may see
        # 401s, surfaced as needs_token_refresh in the job result.
        access_token = self.access_token
        if self._token_provider:
            refreshed = await self._token_provider()
            if refreshed:
                access_token = refreshed

        items = [self._item_from_file_info(f, access_token) for f in files]

        callback_base_url = os.environ.get('WEBUI_PUBLIC_BASE_URL', '')
        if not callback_base_url:
            # Fallback: in-cluster service DNS via tenant config. The
            # loader-worker only needs to reach /api/v1/integrations/ingest.
            callback_base_url = os.environ.get('OPENWEBUI_BASE_URL', '')

        # Pre-submit validation. Fails fast with a clean toast instead of
        # the historical retry storm where every loader-worker item failed
        # with a cryptic httpx exception class.
        _validate_callback_base_url(callback_base_url)

        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        kb_name = knowledge.name if knowledge else self.knowledge_id

        # data_type=chunked_text matches the existing /ingest handler:
        # loader-worker pushes parsed text chunks; open-webui re-embeds via
        # save_docs_to_vector_db (per plan amendment 2026-04-26).
        collection = {
            'source_id': self.knowledge_id,
            'name': kb_name,
            'data_type': 'chunked_text',
        }

        # Pre-create stub File rows so the KB UI can render the discovered
        # tree (with folder structure) immediately, in pending state, instead
        # of staying empty until /ingest callbacks land. The stub carries the
        # provider meta (name, content_type, relative_path, source_item_id…)
        # needed for the SourceGroupedFiles tree renderer; the eventual
        # /ingest callback upserts on file_id and overwrites these fields
        # with the post-parse values. status='pending' on the file's data
        # column drives the per-file spinner in the UI.
        #
        # Stash the touched file_ids on the worker so non-clean exits
        # (submit failure, timeout, cancellation) can fail-mark any row
        # that never transitioned out of 'pending'. Without this, the KB
        # UI shows infinite spinners after a stuck loader-worker.
        self._current_job_stub_file_ids = await self._create_stub_file_rows(files)

        job_id = await self._pipeline_client.submit_job(
            knowledge_id=self.knowledge_id,
            acting_user_id=self.user_id,
            provider_slug=self.provider_slug,
            callback_base_url=callback_base_url,
            collection=collection,
            items=items,
        )
        log.info(f'Submitted loader-worker job {job_id} for KB {self.knowledge_id} with {len(items)} items')
        return job_id

    async def _create_stub_file_rows(self, files: List[Dict[str, Any]]) -> list[str]:
        """Insert ``Files`` rows in ``status='pending'`` for every discovered file.

        Called *before* the loader-worker job is submitted so the KB UI
        immediately reflects the file/folder tree the sync will populate.
        Idempotent: existing rows (re-sync of a previously-synced KB) are
        left as-is — the eventual ``/ingest`` callback updates them.

        Also fires ``:file:processing`` per stub so the existing frontend
        cloud-event handler adds the file to its in-memory list with a
        spinner, matching legacy ``_download_and_store`` UX.

        Returns the list of file_ids touched by this sync (both newly
        inserted and re-attached to the KB). The caller stashes this on
        the worker so failure paths can fail-mark rows that never
        transitioned out of ``pending``.
        """
        touched: list[str] = []
        for file_info in files:
            try:
                item = file_info['item']
                item_id = item['id']
                name = file_info['name']
                file_id = f'{self.file_id_prefix}{item_id}'
                source_item_id = file_info.get('source_item_id')
                relative_path = file_info.get('relative_path', name)
                content_type = self._get_content_type(name)

                if await Files.get_file_by_id(file_id) is not None:
                    await Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)
                    touched.append(file_id)
                else:
                    # Google Drive returns ``size`` as a string per its v3 API
                    # (``files.list``). Storing that raw makes the frontend's
                    # formatFileSize show "Invalid size" because it requires
                    # ``typeof === 'number'``. Coerce defensively so every
                    # provider's stub starts with a numeric size.
                    raw_size = item.get('size', 0) or 0
                    try:
                        size = int(raw_size)
                    except (TypeError, ValueError):
                        size = 0
                    file_meta = self._get_provider_file_meta(
                        item_id=item_id,
                        source_item_id=source_item_id,
                        relative_path=relative_path,
                        name=name,
                        content_type=content_type,
                        size=size,
                        file_info=file_info,
                    )

                    file_form = FileForm(
                        id=file_id,
                        filename=name,
                        path='',
                        hash='',
                        data={'status': 'pending'},
                        meta=file_meta,
                    )
                    await Files.insert_new_file(self.user_id, file_form)
                    await Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)
                    touched.append(file_id)

                await emit_file_processing(
                    self.event_prefix,
                    user_id=self.user_id,
                    knowledge_id=self.knowledge_id,
                    file_info={
                        'item_id': item_id,
                        'name': name,
                        'size': item.get('size', 0),
                        'source_item_id': source_item_id,
                        'relative_path': relative_path,
                    },
                )
            except Exception as e:
                # Stub creation is best-effort UI hint — never let a failure
                # here block the actual sync. The /ingest callback will
                # create the row from scratch if the stub is missing.
                log.warning(f'Failed to create stub File row for {file_info.get("name", "?")}: {e}')
        return touched

    async def _fail_mark_outstanding_stubs(
        self,
        message: str,
        error_status: str = 'error',
    ) -> int:
        """Sweep this sync's stubs; transition any non-terminal row to ``error_status``.

        Called on submit failure, timeout, cancellation, and when the
        loader-worker reports a terminal job that left some items in a
        non-terminal stage. Rows already in ``completed`` or ``error`` are
        left alone — those are authoritative writes from /ingest.

        Returns the number of rows changed.
        """
        file_ids = getattr(self, '_current_job_stub_file_ids', None) or []
        if not file_ids:
            return 0
        changed = 0
        for file_id in file_ids:
            try:
                existing = await Files.get_file_by_id(file_id)
                if existing is None:
                    continue
                current_status = (existing.data or {}).get('status')
                if current_status in ('completed', 'error'):
                    continue
                await Files.update_file_data_by_id(
                    file_id,
                    {'status': error_status, 'error': message},
                )
                changed += 1
            except Exception:
                log.warning(f'Failed to fail-mark stub {file_id}', exc_info=True)
        return changed

    async def _apply_item_stages_to_files(self, item_states: List[Dict[str, Any]]) -> None:
        """Mirror per-item ``stage`` from the loader-worker onto ``Files.data.status``.

        Drives the per-file spinner in the KB UI without waiting for the
        ``/ingest`` callback. Only updates rows whose state actually changed,
        and never overwrites the terminal ``completed`` / ``error`` state
        that ``/ingest`` writes — those are authoritative once set. Also
        fires ``:file:added`` once per item the first time we observe its
        terminal ``ok`` stage, so the frontend transitions the per-file
        spinner to ``uploaded`` mid-sync (Option A's progressive
        appearance).
        """
        if not hasattr(self, '_announced_ok_file_ids'):
            self._announced_ok_file_ids: set[str] = set()

        for item in item_states:
            file_id = item.get('file_id')
            stage = item.get('stage')
            if not file_id or not stage:
                continue
            try:
                existing = await Files.get_file_by_id(file_id)
                if existing is None:
                    continue
                current_data = existing.data or {}
                current_status = current_data.get('status')
                # /ingest's terminal writes win — don't churn rows that are
                # already in their final state.
                if current_status not in ('completed', 'error') and current_status != stage:
                    await Files.update_file_data_by_id(file_id, {'status': stage})

                if stage == 'ok' and file_id not in self._announced_ok_file_ids:
                    self._announced_ok_file_ids.add(file_id)
                    refreshed = await Files.get_file_by_id(file_id)
                    if refreshed:
                        await emit_file_added(
                            self.event_prefix,
                            user_id=self.user_id,
                            knowledge_id=self.knowledge_id,
                            file_data={
                                'id': refreshed.id,
                                'filename': refreshed.filename,
                                'meta': refreshed.meta,
                                'created_at': refreshed.created_at,
                                'updated_at': refreshed.updated_at,
                            },
                        )
            except Exception as e:
                log.debug(f'Failed to mirror loader-worker stage onto File {file_id}: {e}')

    async def _track_job_progress(self, job_id: str, total_files: int, unchanged_count: int) -> Dict[str, Any]:
        """Poll loader-worker for job status, emit progress, return terminal status.

        Terminal states: ``completed``, ``partial``, ``failed``, ``cancelled``.
        Synthesises a ``timed_out`` terminal when the loader-worker hasn't
        reported a real terminal within ``MAX_JOB_WALL_CLOCK_SECONDS`` —
        without this guard a stuck pod (the 2026-04-29 staging incident)
        keeps OWUI polling forever and stubs remain in 'pending'.

        ``unchanged_count`` carries through the pre-submit short-circuit
        result so the in-flight progress bar shows total scope, not just
        the submitted subset.

        Forwards an in-flight cancellation request to the loader-worker when
        the user cancels via the UI.
        """
        terminal = {'completed', 'partial', 'failed', 'cancelled'}
        last_status = ''
        cancel_requested = False
        started_at = time.monotonic()

        while True:
            elapsed = time.monotonic() - started_at
            if elapsed > MAX_JOB_WALL_CLOCK_SECONDS:
                log.error(
                    f'Loader-worker job {job_id} exceeded '
                    f'MAX_JOB_WALL_CLOCK_SECONDS={MAX_JOB_WALL_CLOCK_SECONDS}; '
                    f'returning synthetic timed_out status so caller can fail-mark stubs.'
                )
                return {
                    'status': 'timed_out',
                    'items_completed': 0,
                    'items_failed': 0,
                    'items': [],
                    'errors': [],
                    'stage_counts': {},
                }

            status = await self._pipeline_client.get_status(job_id)
            current_status = status.get('status', '')
            items_completed = status.get('items_completed', 0)
            items_failed = status.get('items_failed', 0)
            stage_counts = status.get('stage_counts') or {}
            item_states = status.get('items') or []

            # Reflect the loader-worker's per-item stage state on the stub
            # File rows so the KB UI's per-file spinners can transition
            # downloading → parsing → ingesting → completed without waiting
            # for the terminal ingest callback. The /ingest callback still
            # owns the final transition to 'completed' / 'error'.
            await self._apply_item_stages_to_files(item_states)

            await self._update_sync_status(
                'syncing',
                current=items_completed + items_failed + unchanged_count,
                total=total_files,
                files_processed=items_completed,
                files_failed=items_failed,
                stage_counts=stage_counts,
            )

            if not cancel_requested and await self._check_cancelled():
                try:
                    await self._pipeline_client.cancel_job(job_id)
                except Exception as e:
                    log.warning(f'Failed to cancel loader-worker job {job_id}: {e}')
                cancel_requested = True

            if current_status in terminal:
                last_status = current_status
                return status

            last_status = current_status
            await asyncio.sleep(2)

    async def _sync_via_pipeline(  # noqa: C901 — terminal-state branching is irreducible (completed/partial/failed/cancelled/timed_out + orphan sweep)
        self,
        all_files_to_process: List[Dict[str, Any]],
        total_files: int,
        added_file_ids: set[str],
        updated_file_ids: set[str],
        unchanged_count: int,
        total_deleted: int,
    ) -> Dict[str, Any]:
        """Drive a sync via the per-tenant loader-worker.

        Submits one job carrying every discovered file, polls for terminal
        status, persists ``last_result`` in KB meta, and returns a result
        dict shape-compatible with the legacy in-pod path.

        ``added_file_ids`` / ``updated_file_ids`` are the pre-classified
        partition of the submit batch; intersecting them with the loader-
        worker's per-item ok set yields the toast's ``files_added`` /
        ``files_updated`` counts.
        """
        failed_files: List[FailedFile] = []

        if not all_files_to_process:
            await self._save_sources()
            await self._update_sync_status(
                'completed',
                current=total_files,
                total=total_files,
                files_processed=0,
                files_failed=0,
                deleted_count=total_deleted,
                files_added=0,
                files_updated=0,
                files_unchanged=unchanged_count,
                files_removed=total_deleted,
                failed_files=failed_files,
            )
            return {
                'files_processed': 0,
                'files_failed': 0,
                'total_found': total_files,
                'deleted_count': total_deleted,
                'files_added': 0,
                'files_updated': 0,
                'files_unchanged': unchanged_count,
                'files_removed': total_deleted,
                'failed_files': [],
            }

        try:
            job_id = await self._submit_pipeline_job(all_files_to_process)
        except ConfigurationError as e:
            # Sync prerequisite missing (e.g. WEBUI_PUBLIC_BASE_URL unset).
            # Fail-mark the stubs we just inserted so the KB UI doesn't
            # leave spinners — and surface the real reason in the toast.
            log.error(f'Sync prerequisite failed: {e}')
            await self._fail_mark_outstanding_stubs(str(e))
            await self._update_sync_status('failed', error=str(e))
            raise
        except Exception as e:
            log.exception(f'Failed to submit loader-worker job: {e}')
            await self._fail_mark_outstanding_stubs(f'pipeline submit failed: {e}')
            await self._update_sync_status('failed', error=f'pipeline submit failed: {e}')
            raise

        status = await self._track_job_progress(
            job_id=job_id,
            total_files=total_files,
            unchanged_count=unchanged_count,
        )

        items_completed = status.get('items_completed', 0)
        items_failed = status.get('items_failed', 0)
        terminal = status.get('status', 'completed')

        # Per-item ok set, used to split items_completed back into the
        # pre-submit added/updated buckets so the toast can say "Added N,
        # Updated M" instead of just "N processed". Empty if loader-worker
        # didn't emit items[]; in that case the toast falls back to
        # showing only the totals.
        ok_file_ids: set[str] = {
            it.get('file_id')
            for it in (status.get('items') or [])
            if it.get('file_id') and (it.get('stage') in ('ok', 'completed') or it.get('status') == 'ok')
        }

        # When the loader-worker reports terminal=failed (e.g. /ingest callback
        # rejected the batch), items_completed reflects items that *processed*
        # successfully but did NOT land in the KB. Treat them as failed so the
        # UI doesn't show a false-positive sync confirmation.
        if terminal == 'failed' and items_completed > 0:
            items_failed = items_failed + items_completed
            items_completed = 0
            ok_file_ids = set()

        final_added = len(ok_file_ids & added_file_ids)
        final_updated = len(ok_file_ids & updated_file_ids)
        final_failed = items_failed
        final_unchanged = unchanged_count

        # File ids that were definitively removed because the loader-worker
        # reported their source access was permanently revoked. Keyed for the
        # orphan-stage sweep below so we don't try to fail-mark a file row
        # that no longer exists.
        revoked_count = 0
        revoked_file_ids: set[str] = set()

        for err in status.get('errors', []) or []:
            code = err.get('error_code') or 'unexpected_error'
            file_id = err.get('file_id', 'unknown')

            if code == 'source_access_revoked':
                # Server-confirmed permanent loss. Mirror _handle_deleted_item:
                # remove from KB, purge vectors, hard-delete the File row if
                # no other KB references it. Excluded from the user-facing
                # ``failed_files`` list — it's a "Removed", not a "Failed".
                removed = await self._handle_revoked_item(file_id)
                if removed:
                    revoked_count += removed
                    if file_id and file_id != 'unknown':
                        revoked_file_ids.add(file_id)
                # Don't subtract from items_failed — the loader-worker
                # already counted this item there. Compensate downstream by
                # rolling revoked_count into total_deleted (Phase 2's
                # files_removed) and dropping it from final_failed.
                continue

            error_type = _LOADER_ERROR_CODE_TO_SYNC_TYPE.get(
                code,
                SyncErrorType.PROCESSING_ERROR,
            )
            # Look up the real filename from the stub File row; the
            # loader-worker's ``err`` dict only has ``file_id`` (the
            # internal opaque id like 'googledrive-1J-g2oT…'). Falling
            # back to the file_id when the row is missing is fine for
            # logs but should never reach the user-facing toast — the
            # stub was inserted in this same sync, so it must exist.
            display_name = file_id
            if file_id and file_id != 'unknown':
                try:
                    existing = await Files.get_file_by_id(file_id)
                    if existing and existing.filename:
                        display_name = existing.filename
                except Exception:
                    log.debug(f'Could not resolve filename for {file_id}', exc_info=True)
            failed_files.append(
                FailedFile(
                    filename=display_name,
                    error_type=error_type.value,
                    # Keep error_message empty in the user-facing payload —
                    # the loader-worker's raw text is English and exposes
                    # IDs/URLs, neither of which the user wants in the toast.
                    # The category label (error_type) is the only thing
                    # rendered now; raw text is retained server-side via
                    # last_result.failed_files for debugging.
                    error_message='',
                )
            )

        # Roll revoked items into Phase 2's files_removed and drop them from
        # the failed counter so the toast says "Removed N" instead of
        # "N failed" for permission-revoke fallout.
        if revoked_count:
            total_deleted += revoked_count
            final_failed = max(0, final_failed - revoked_count)

        # Cover the "stage stuck mid-pipeline" case: loader-worker reported a
        # terminal job, but ``items[]`` shows individual items still in
        # ``downloading`` / ``parsing`` / ``ingesting`` / ``pending``. The
        # /ingest callback never landed for those — fail-mark them so they
        # don't sit forever with a spinner. Skipped on cancellation, which is
        # handled below.
        if terminal != 'cancelled':
            non_terminal_stages = {'pending', 'downloading', 'parsing', 'ingesting'}
            for orphan in status.get('items') or []:
                if orphan.get('stage') not in non_terminal_stages:
                    continue
                file_id = orphan.get('file_id')
                if not file_id:
                    continue
                if file_id in revoked_file_ids:
                    # Already deleted via _handle_revoked_item; the row no
                    # longer exists and re-fail-marking would race a 404.
                    continue
                try:
                    existing = await Files.get_file_by_id(file_id)
                    if existing and (existing.data or {}).get('status') not in ('completed', 'error'):
                        await Files.update_file_data_by_id(
                            file_id,
                            {
                                'status': 'error',
                                'error': (f'sync ended with item still in stage={orphan.get("stage")}'),
                            },
                        )
                except Exception:
                    log.warning(f'Failed to fail-mark orphan-stage stub {file_id}', exc_info=True)

        # Synthetic timeout from _track_job_progress: fail-mark every stub
        # this sync inserted that's still in a non-terminal state.
        if terminal == 'timed_out':
            changed = await self._fail_mark_outstanding_stubs('Sync timed out')
            log.warning(
                f'Sync timed out for KB {self.knowledge_id}: fail-marked {changed} stub(s) '
                f'(MAX_JOB_WALL_CLOCK_SECONDS={MAX_JOB_WALL_CLOCK_SECONDS})'
            )
            for source in self.sources:
                for key in self.source_clear_delta_keys:
                    source.pop(key, None)
            await self._save_sources()
            await self._update_sync_status(
                'failed',
                current=total_files,
                total=total_files,
                error='Sync timed out',
                files_processed=0,
                files_failed=changed,
                deleted_count=total_deleted,
                files_added=0,
                files_updated=0,
                files_unchanged=final_unchanged,
                files_removed=total_deleted,
                failed_files=failed_files,
            )
            return {
                'files_processed': 0,
                'files_failed': changed,
                'total_found': total_files,
                'deleted_count': total_deleted,
                'files_added': 0,
                'files_updated': 0,
                'files_unchanged': final_unchanged,
                'files_removed': total_deleted,
                'timed_out': True,
                'failed_files': [asdict(f) for f in failed_files],
            }

        total_processed = final_added + final_updated
        total_failed = final_failed

        if terminal == 'cancelled':
            await self._fail_mark_outstanding_stubs(
                'Sync cancelled by user',
                error_status='cancelled',
            )
            for source in self.sources:
                for key in self.source_clear_delta_keys:
                    source.pop(key, None)
            await self._save_sources()
            await self._update_sync_status(
                'cancelled',
                current=total_processed + total_failed,
                total=total_files,
                error='Sync cancelled by user',
                files_processed=total_processed,
                files_failed=total_failed,
                deleted_count=total_deleted,
                files_added=final_added,
                files_updated=final_updated,
                files_unchanged=final_unchanged,
                files_removed=total_deleted,
                failed_files=failed_files,
            )
            return {
                'files_processed': total_processed,
                'files_failed': total_failed,
                'total_found': total_files,
                'deleted_count': total_deleted,
                'files_added': final_added,
                'files_updated': final_updated,
                'files_unchanged': final_unchanged,
                'files_removed': total_deleted,
                'cancelled': True,
                'failed_files': [asdict(f) for f in failed_files],
            }

        # Persist the delta cursor when the only failures are non-retryable
        # (e.g. unsupported file type, schema-skew, empty extraction). Re-
        # running won't help those, so freezing the cursor would just force
        # a full re-walk on every subsequent sync — which is what users
        # observed when a single .png in their folder kept the next "add
        # one file" sync re-enumerating thousands of files.
        #
        # Retryable failures (needs_token_refresh, hard_source_error,
        # config_error, unexpected_error, cancelled) still freeze the
        # cursor so the failed items get another chance next sync.
        retryable_codes = {
            (e.get('error_code') or 'unexpected_error') for e in (status.get('errors') or [])
        } - _NON_RETRYABLE_LOADER_ERROR_CODES
        if terminal in ('completed', 'partial') and not retryable_codes:
            await self._save_sources()
            if total_failed:
                log.info(
                    f'Advancing delta cursor for {self.knowledge_id} despite {total_failed} non-retryable failure(s).'
                )
        else:
            log.warning(
                f'Skipping _save_sources() for {self.knowledge_id}: '
                f'terminal={terminal}, items_failed={total_failed}, '
                f'retryable_codes={sorted(retryable_codes)}. '
                f'Delta cursor not advanced; next sync will re-enumerate.'
            )

        failed_files_dicts = [asdict(f) for f in failed_files]
        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})
        sync_info['last_sync_at'] = int(time.time())
        sync_info['status'] = 'completed' if total_failed == 0 else 'completed_with_errors'
        sync_info['last_result'] = {
            'files_processed': total_processed,
            'files_failed': total_failed,
            'total_found': total_files,
            'deleted_count': total_deleted,
            'files_added': final_added,
            'files_updated': final_updated,
            'files_unchanged': final_unchanged,
            'files_removed': total_deleted,
            'failed_files': failed_files_dicts,
        }
        meta[self.meta_key] = sync_info
        await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

        await self._update_sync_status(
            sync_info['status'],
            current=total_files,
            total=total_files,
            files_processed=total_processed,
            files_failed=total_failed,
            deleted_count=total_deleted,
            files_added=final_added,
            files_updated=final_updated,
            files_unchanged=final_unchanged,
            files_removed=total_deleted,
            failed_files=failed_files,
        )

        log.info(
            f'Sync via pipeline completed for {self.knowledge_id}: '
            f'added={final_added}, updated={final_updated}, '
            f'unchanged={final_unchanged}, failed={total_failed} (job_id={job_id})'
        )

        return {
            'files_processed': total_processed,
            'files_failed': total_failed,
            'total_found': total_files,
            'deleted_count': total_deleted,
            'files_added': final_added,
            'files_updated': final_updated,
            'files_unchanged': final_unchanged,
            'files_removed': total_deleted,
            'failed_files': failed_files_dicts,
        }

    async def sync(self) -> Dict[str, Any]:
        """Execute sync operation for all sources."""
        self._client = self._create_client()

        try:
            await self._update_sync_status('syncing', 0, 0)

            # Verify the owner still has access; may suspend the KB
            await self._sync_permissions()

            # Check if KB was suspended by _sync_permissions()
            knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
            if knowledge:
                meta = knowledge.meta or {}
                sync_info = meta.get(self.meta_key, {})
                if sync_info.get('suspended_at'):
                    log.info(f'KB {self.knowledge_id} is suspended, skipping sync')
                    return {
                        'files_processed': 0,
                        'files_failed': 0,
                        'total_found': 0,
                        'deleted_count': 0,
                        'failed_files': [],
                        'suspended': True,
                    }

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
                    'access_revoked',
                    error=(f"Access to '{source.get('name', 'unknown')}' has been revoked. {removed} file(s) removed."),
                )

            self.sources = verified_sources

            # Aggregate counters
            total_processed = 0
            total_failed = 0
            total_deleted = 0
            failed_files: List[FailedFile] = []

            all_files_to_process = []

            log.info(f'Starting multi-source sync for knowledge {self.knowledge_id}, {len(self.sources)} sources')

            for source in self.sources:
                if source.get('type') == 'folder':
                    files, deleted = await self._collect_folder_files(source)
                    all_files_to_process.extend(files)
                    total_deleted += deleted
                else:
                    file_info = await self._collect_single_file(source)
                    if file_info:
                        all_files_to_process.append(file_info)

            # Apply file count limit. A falsy max_files_config (0/None) means
            # the provider sets no per-sync cap — fall back to the KB-wide
            # KNOWLEDGE_MAX_FILE_COUNT safety net alone.
            max_files = (
                min(self.max_files_config, KNOWLEDGE_MAX_FILE_COUNT)
                if self.max_files_config
                else KNOWLEDGE_MAX_FILE_COUNT
            )
            current_files = await Knowledges.get_files_by_id(self.knowledge_id) or []
            current_file_count = len(current_files)
            available_slots = max(0, max_files - current_file_count)

            if len(all_files_to_process) > available_slots:
                log.warning(
                    f'File limit exceeded: {current_file_count} existing + '
                    f'{len(all_files_to_process)} new > {max_files} limit'
                )
                if available_slots == 0:
                    await self._update_sync_status(
                        'file_limit_exceeded',
                        error=(
                            f'This knowledge base has reached the {max_files}-file limit. '
                            f'Remove files or select fewer items to sync.'
                        ),
                    )
                    await self._save_sources()
                    return {
                        'files_processed': 0,
                        'files_failed': 0,
                        'total_found': len(all_files_to_process),
                        'deleted_count': total_deleted,
                        'failed_files': [],
                        'file_limit_exceeded': True,
                    }
                else:
                    total_found = len(all_files_to_process)
                    all_files_to_process = all_files_to_process[:available_slots]
                    await self._update_sync_status(
                        'syncing',
                        error=(f'Only syncing {available_slots} of {total_found} files due to {max_files}-file limit.'),
                    )

            # Categorize discovered files before submission so the toast can
            # report what actually changed (added/updated/unchanged), not just
            # what passed through the loader-worker. The legacy in-pod path
            # had a per-file short-circuit at `_download_and_store_legacy`;
            # the shared-loader path lacked one, which is the structural
            # cause of the "5 extra" re-sync toast.
            added_file_ids: set[str] = set()
            updated_file_ids: set[str] = set()
            unchanged_count = 0
            to_submit: List[Dict[str, Any]] = []
            for fi in all_files_to_process:
                cat, fid = await self._classify_for_submit(fi)
                if cat == 'unchanged':
                    unchanged_count += 1
                    continue
                if cat == 'added':
                    added_file_ids.add(fid)
                else:
                    updated_file_ids.add(fid)
                to_submit.append(fi)

            all_files_to_process = to_submit
            total_files = len(all_files_to_process) + unchanged_count
            log.info(
                f'Classified {len(all_files_to_process) + unchanged_count} files: '
                f'{len(added_file_ids)} added, {len(updated_file_ids)} updated, '
                f'{unchanged_count} unchanged'
            )

            # Pre-create the KB collection so individual file inserts don't
            # race to create it (avoids N-1 wasted 422 roundtrips).
            await ASYNC_VECTOR_DB_CLIENT.insert(collection_name=self.knowledge_id, items=[])

            # USE_SHARED_LOADER branch: delegate everything to the per-tenant
            # loader-worker pod. The semaphore-bounded fan-out below is bypassed.
            if self._use_shared_loader and self._pipeline_client:
                return await self._sync_via_pipeline(
                    all_files_to_process=all_files_to_process,
                    total_files=total_files,
                    added_file_ids=added_file_ids,
                    updated_file_ids=updated_file_ids,
                    unchanged_count=unchanged_count,
                    total_deleted=total_deleted,
                )

            # Process all files with two-phase pipeline
            from open_webui.config import FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER

            # Cap process concurrency to the default thread pool size
            # (min(32, os.cpu_count() + 4)) minus headroom for embedding
            # callbacks. On a 1-CPU pod the pool is only 5 threads; allowing
            # more concurrent process tasks than pool slots causes starvation.
            import os

            thread_pool_size = min(32, (os.cpu_count() or 1) + 4)
            max_process_concurrent = min(
                FILE_PROCESSING_MAX_CONCURRENT.value,
                max(1, thread_pool_size - 2),  # leave 2 slots for embeddings / other work
            )
            max_download_concurrent = max_process_concurrent * FILE_DOWNLOAD_CONCURRENCY_MULTIPLIER
            download_semaphore = asyncio.Semaphore(max_download_concurrent)
            process_semaphore = asyncio.Semaphore(max_process_concurrent)
            processed_count = unchanged_count
            failed_count = 0
            results_lock = asyncio.Lock()
            cancelled = False

            # Per-file timeout: extraction (120s) + chunking (120s) + embedding (300s) + overhead
            FILE_PIPELINE_TIMEOUT = 600  # 10 minutes

            async def _pipeline_inner(file_info: Dict[str, Any], index: int) -> Optional[FailedFile]:
                nonlocal processed_count, failed_count, cancelled

                if cancelled or await self._check_cancelled():
                    cancelled = True
                    return FailedFile(
                        filename=file_info.get('name', 'unknown'),
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message='Sync cancelled by user',
                    )

                try:
                    # Phase 1: Download + store (high concurrency)
                    async with download_semaphore:
                        if cancelled or await self._check_cancelled():
                            cancelled = True
                            return FailedFile(
                                filename=file_info.get('name', 'unknown'),
                                error_type=SyncErrorType.PROCESSING_ERROR.value,
                                error_message='Sync cancelled by user',
                            )
                        result = await self._download_and_store(file_info)

                    # Handle download phase results
                    if isinstance(result, FailedFile):
                        async with results_lock:
                            failed_count += 1
                            await self._update_sync_status(
                                'syncing',
                                processed_count + failed_count,
                                total_files,
                                file_info.get('name', ''),
                                files_processed=processed_count,
                                files_failed=failed_count,
                            )
                        return result

                    if result is None:
                        # Hash match — already handled, count as success
                        async with results_lock:
                            processed_count += 1
                            await self._update_sync_status(
                                'syncing',
                                processed_count + failed_count,
                                total_files,
                                file_info.get('name', ''),
                                files_processed=processed_count,
                                files_failed=failed_count,
                            )
                        return None

                    # Phase 2: Process + embed (normal concurrency)
                    async with process_semaphore:
                        if cancelled or await self._check_cancelled():
                            cancelled = True
                            return FailedFile(
                                filename=file_info.get('name', 'unknown'),
                                error_type=SyncErrorType.PROCESSING_ERROR.value,
                                error_message='Sync cancelled by user',
                            )

                        if not result.is_new:
                            # Hash-matched file: just verify vectors are in KB
                            verify_result = await self._ensure_vectors_in_kb(result.file_id)
                            if verify_result:
                                if verify_result.error_type == SyncErrorType.EMPTY_CONTENT.value:
                                    process_result = None  # Skip, not failure
                                else:
                                    log.warning(f'File {result.file_id} vectors missing, re-processing')
                                    process_result = await self._process_and_embed(result)
                            else:
                                # Vectors verified, emit file added event
                                file_record = await Files.get_file_by_id(result.file_id)
                                if file_record:
                                    await emit_file_added(
                                        self.event_prefix,
                                        user_id=self.user_id,
                                        knowledge_id=self.knowledge_id,
                                        file_data={
                                            'id': file_record.id,
                                            'filename': file_record.filename,
                                            'meta': file_record.meta,
                                            'created_at': file_record.created_at,
                                            'updated_at': file_record.updated_at,
                                        },
                                    )
                                process_result = None
                        else:
                            process_result = await self._process_and_embed(result)

                    async with results_lock:
                        if process_result is None:
                            processed_count += 1
                        else:
                            failed_count += 1
                        await self._update_sync_status(
                            'syncing',
                            processed_count + failed_count,
                            total_files,
                            file_info.get('name', ''),
                            files_processed=processed_count,
                            files_failed=failed_count,
                        )
                    return process_result

                except Exception as e:
                    log.error(f'Error in pipeline for {file_info.get("name")}: {e}')
                    async with results_lock:
                        failed_count += 1
                    return FailedFile(
                        filename=file_info.get('name', 'unknown'),
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=str(e)[:100],
                    )

            async def pipeline(file_info: Dict[str, Any], index: int) -> Optional[FailedFile]:
                """Wrapper that enforces a per-file timeout to prevent indefinite hangs."""
                nonlocal failed_count
                try:
                    return await asyncio.wait_for(
                        _pipeline_inner(file_info, index),
                        timeout=FILE_PIPELINE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    log.error(f'File {file_info.get("name")} timed out after {FILE_PIPELINE_TIMEOUT}s')
                    async with results_lock:
                        failed_count += 1
                    return FailedFile(
                        filename=file_info.get('name', 'unknown'),
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message=f'Timed out after {FILE_PIPELINE_TIMEOUT}s',
                    )

            log.info(
                f'Starting pipeline processing of {len(all_files_to_process)} files '
                f'(thread pool: {thread_pool_size}, '
                f'download concurrency: {max_download_concurrent}, '
                f'process concurrency: {max_process_concurrent})'
            )
            start_time = time.time()

            batch_size = max_download_concurrent + max_process_concurrent
            total_batches = -(-len(all_files_to_process) // batch_size)  # ceil division
            all_results = []

            for batch_start in range(0, len(all_files_to_process), batch_size):
                batch_num = batch_start // batch_size + 1
                batch = all_files_to_process[batch_start : batch_start + batch_size]

                if cancelled or await self._check_cancelled():
                    cancelled = True
                    break

                log.info(f'Batch {batch_num}/{total_batches}: processing {len(batch)} files (offset {batch_start})')
                batch_t0 = time.time()

                batch_tasks = [pipeline(file_info, batch_start + i) for i, file_info in enumerate(batch)]
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                all_results.extend(batch_results)

                log.info(
                    f'Batch {batch_num}/{total_batches} done in {time.time() - batch_t0:.1f}s '
                    f'(processed={processed_count}, failed={failed_count})'
                )

            for result in all_results:
                if isinstance(result, Exception):
                    log.error(f'Unexpected error during file processing: {result}')
                    total_failed += 1
                    failed_files.append(
                        FailedFile(
                            filename='unknown',
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
                f'Pipeline processing completed in {processing_time:.2f}s: '
                f'{total_processed} succeeded, {total_failed} failed'
            )

            # Check if cancelled during processing
            if cancelled:
                log.info(f'Sync cancelled by user for knowledge {self.knowledge_id}')

                for source in self.sources:
                    for key in self.source_clear_delta_keys:
                        source.pop(key, None)
                await self._save_sources()

                await self._update_sync_status(
                    'cancelled',
                    current=total_processed + total_failed,
                    total=total_files,
                    error='Sync cancelled by user',
                    files_processed=total_processed,
                    files_failed=total_failed,
                    deleted_count=total_deleted,
                    files_unchanged=unchanged_count,
                    files_removed=total_deleted,
                    failed_files=failed_files,
                )
                return {
                    'files_processed': total_processed,
                    'files_failed': total_failed,
                    'total_found': total_files,
                    'deleted_count': total_deleted,
                    'files_unchanged': unchanged_count,
                    'files_removed': total_deleted,
                    'cancelled': True,
                    'failed_files': [asdict(f) for f in failed_files],
                }

            # Save updated sources
            await self._save_sources()

            failed_files_dicts = [asdict(f) for f in failed_files]

            # Update final sync status. The legacy in-pod path doesn't track
            # per-file outcomes by classification bucket, so we approximate
            # the new toast counts: items that ran through the pipeline are
            # ``files_processed - unchanged_count``, and we attribute them
            # all to ``files_added`` (the legacy path is dead-coded behind
            # USE_SHARED_LOADER and doesn't need precise added/updated split).
            legacy_added = max(0, total_processed - unchanged_count)
            knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
            meta = knowledge.meta or {}
            sync_info = meta.get(self.meta_key, {})
            sync_info['last_sync_at'] = int(time.time())
            sync_info['status'] = 'completed' if total_failed == 0 else 'completed_with_errors'
            sync_info['last_result'] = {
                'files_processed': total_processed,
                'files_failed': total_failed,
                'total_found': total_files,
                'deleted_count': total_deleted,
                'files_added': legacy_added,
                'files_updated': 0,
                'files_unchanged': unchanged_count,
                'files_removed': total_deleted,
                'failed_files': failed_files_dicts,
            }
            meta[self.meta_key] = sync_info
            await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

            await self._update_sync_status(
                sync_info['status'],
                current=total_files,
                total=total_files,
                files_processed=total_processed,
                files_failed=total_failed,
                deleted_count=total_deleted,
                files_added=legacy_added,
                files_updated=0,
                files_unchanged=unchanged_count,
                files_removed=total_deleted,
                failed_files=failed_files,
            )

            log.info(f'Sync completed for {self.knowledge_id}: {total_processed} processed, {total_failed} failed')

            return {
                'files_processed': total_processed,
                'files_failed': total_failed,
                'total_found': total_files,
                'deleted_count': total_deleted,
                'failed_files': failed_files_dicts,
            }

        except (ConnectionError, httpx.TransportError) as e:
            # Connectivity loss — DNS failure, connection refused, or timeout —
            # is transient and expected: the host may be offline or the
            # provider briefly unreachable. Log a single concise line instead
            # of a full traceback, and return a skipped-cycle result rather
            # than re-raising as an unexpected error. The `transient` flag lets
            # the scheduler log a WARNING, and the next tick retries
            # automatically (last_sync_at is not stamped, so the KB stays due).
            log.warning(f'Sync skipped for {self.knowledge_id}: {self.provider_slug} unreachable ({e})')
            await self._update_sync_status(
                'failed',
                error='Sync source is temporarily unreachable — the next scheduled sync will retry automatically.',
            )
            return {
                'files_processed': 0,
                'files_failed': 0,
                'total_found': 0,
                'deleted_count': 0,
                'failed_files': [],
                'error': f'{self.provider_slug} unreachable',
                'transient': True,
            }

        except Exception as e:
            log.exception(f'Sync failed: {e}')
            await self._update_sync_status('failed', error=str(e))
            raise

        finally:
            await self._close_client()
