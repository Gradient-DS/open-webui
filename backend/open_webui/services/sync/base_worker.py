"""Base sync worker - shared logic for cloud storage sync workers."""

import asyncio
import logging
import os
import time

import httpx
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Optional, Callable, Awaitable, Dict, Any, List
from pathlib import Path

from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files, FileForm
from open_webui.models.users import Users
from open_webui.config import KNOWLEDGE_MAX_FILE_COUNT
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.services.deletion import DeletionService
from open_webui.services.sync.constants import SyncErrorType, FailedFile, CONTENT_TYPES
from open_webui.services.sync.events import (
    emit_sync_progress,
    emit_file_processing,
    emit_file_added,
)
from open_webui.services.sync.pipeline_client import PipelineClient, PipelineUnreachableError

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


def _max_job_wall_clock_seconds() -> int:
    """Wall-clock cap for polling a single loader-worker job.

    Read at call time (not import) so a tenant with a very large initial
    sync can raise SYNC_MAX_JOB_WALL_CLOCK_SECONDS via deployment config
    without a code change. Default 4h.

    The previous 30-min default was too short for large KBs: a multi-thousand-
    file sync cannot finish in 30 min on the current stack, so it hit the
    timeout path — which fail-marks in-flight items AND clears the cloud delta
    tokens, forcing a full re-enumeration next run and an infinite ~30-min
    re-sync loop. 4h covers ~10x the observed 2526-file duration; the env
    override remains for even larger installs.

    Without this guard a stuck loader-worker (the 2026-04-29 staging incident)
    leaves spinners spinning indefinitely and blocks user-initiated re-syncs.
    """
    return int(os.environ.get('SYNC_MAX_JOB_WALL_CLOCK_SECONDS', '14400'))


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

    def _item_from_file_info(self, file_info: Dict[str, Any], access_token: str) -> Dict[str, Any]:
        """Build a loader-worker job item dict from a discovered file_info.

        Providers override to supply provider-specific ``source_descriptor``
        fields the loader-worker's ``SourceClient`` knows how to interpret.
        Default implementation produces a generic item shape.
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

    # Providers whose successful download ALWAYS yields non-empty extractable
    # text should override this to True. When True, a row that is 'completed'
    # but has empty ``data['content']`` is treated as NOT fully ingested — the
    # residue of an empty/failed extraction — so the cloud-hash short-circuit
    # re-submits it instead of freezing it empty forever. Binary-file
    # providers (OneDrive/Google Drive) MUST leave this False: image-only
    # files legitimately extract to empty content, and re-submitting them
    # every sync would defeat the cloud-hash skip.
    expect_nonempty_content: bool = False

    # Snapshot of file_ids linked to this KB BEFORE the current sync run
    # created any stubs. Set by sync(); None means "no gate" (call paths
    # that never snapshot keep the legacy global-status behavior). Drives
    # the KB-membership gate in _classify_for_submit.
    _kb_member_file_ids: Optional[set] = None

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
        # File ingestion is delegated to the per-tenant loader-worker pod
        # (see thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md).
        self._pipeline_client: PipelineClient = PipelineClient()

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

    @staticmethod
    def _dedup_discovered_files(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse duplicate feed emissions to one entry per provider item id.

        Graph's /delta (and Drive's changes API) may emit the same item more
        than once in a single enumeration; per the API contract the LAST
        occurrence is authoritative (newest metadata/hash). Also collapses a
        single-file source that overlaps a picked folder. First-seen order is
        preserved so progress remains stable.
        """
        by_id: Dict[str, Dict[str, Any]] = {}
        for file_info in files:
            by_id[file_info['item']['id']] = file_info
        return list(by_id.values())

    async def _classify_for_submit(self, file_info: Dict[str, Any]) -> tuple[str, str]:
        """Decide whether to submit this file_info to the loader-worker.

        Returns (category, file_id):
          - ('unchanged', file_id) — cloud_hash matches stored AND row is 'completed'; SKIP submission
          - ('updated', file_id)   — existing row but hash mismatch or non-completed; SUBMIT
          - ('added', file_id)     — no existing row; SUBMIT

        Stops the loader-worker path from re-processing files that haven't
        changed — the structural cause of the "5 extra" toast where a re-sync
        of an unchanged folder showed N "synced" instead of "no changes".
        """
        item = file_info['item']
        item_id = item['id']
        file_id = f'{self.file_id_prefix}{item_id}'
        existing = await Files.get_file_by_id(file_id)
        if existing is None:
            return 'added', file_id
        # 'unchanged' (skip submit) additionally requires that THIS KB already
        # held the file before this sync run. The global data.status ==
        # 'completed' on a shared row proves *some* KB ingested it — not that
        # this KB's collection has vectors. Files net-new to the KB are always
        # submitted; overlapping KBs process the same file once each (accepted
        # double-work, decision 2026-07-02). Known residual race: two
        # overlapping KBs syncing *concurrently* can still interleave on the
        # shared row's global status — transiently dishonest, self-correcting
        # when each KB's own /ingest lands.
        if self._kb_member_file_ids is not None and file_id not in self._kb_member_file_ids:
            return 'added', file_id
        cloud_hash = self._get_cloud_hash(file_info)
        if not cloud_hash:
            # No hash available (e.g. provider didn't return one) — be
            # conservative and treat as updated. The unchanged short-circuit
            # only fires on a positive hash match.
            return 'updated', file_id
        stored = (existing.meta or {}).get('cloud_hash')
        if stored == cloud_hash and self._is_fully_ingested(existing):
            return 'unchanged', file_id
        return 'updated', file_id

    def _is_fully_ingested(self, existing) -> bool:
        """Whether an existing File row represents a genuinely complete ingest.

        The cloud-hash short-circuit (``_classify_for_submit``) gates on this
        so a prior empty/failed ingest self-heals instead of being frozen by a
        matching cloud_hash. ``status == 'completed'`` alone is not proof of a
        real ingest: the empty-extraction branch marks a row 'completed' even
        when no text was captured. For providers that guarantee non-empty
        content (``expect_nonempty_content``), an empty ``data['content']``
        therefore signals a failed ingest that must be re-run.
        """
        data = existing.data or {}
        if data.get('status') != 'completed':
            return False
        if self.expect_nonempty_content and not (data.get('content') or '').strip():
            return False
        return True

    # ------------------------------------------------------------------
    # Loader-worker orchestration
    # ------------------------------------------------------------------

    async def _submit_pipeline_job(self, files: List[Dict[str, Any]]) -> Optional[str]:
        """Submit a single loader-worker job carrying every discovered file.

        Returns the job_id. The loader-worker is responsible for
        download → parse+chunk → embed → push to /ingest, and tracks its
        own concurrency.
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
        spinner.

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
                # Provider content hash at discovery time, staged on the row
                # as pending_cloud_hash. /ingest promotes it to cloud_hash
                # ONLY on a successful ingest — a failed download/parse/embed
                # keeps the old cloud_hash so the next sync retries the file
                # as 'updated' instead of freezing it 'unchanged' under a
                # hash it never ingested.
                cloud_hash = self._get_cloud_hash(file_info)

                existing = await Files.get_file_by_id(file_id)
                if existing is not None:
                    # Self-heal identity drift: stored source_item_id /
                    # relative_path can be stale (pre-canonicalization picker
                    # ids, "Papers/"-prefixed paths from the folder_map bug, or
                    # loader-era overwrites). Refresh file.meta from this
                    # sync's computed identity BEFORE re-linking, so the
                    # add_file_to_knowledge_by_id upsert mirrors the healed
                    # values onto the join row's denormalized path columns.
                    existing_meta = existing.meta or {}
                    stale = {
                        key: value
                        for key, value in (
                            ('source_item_id', source_item_id),
                            ('relative_path', relative_path),
                            ('pending_cloud_hash', cloud_hash),
                        )
                        if value and existing_meta.get(key) != value
                    }
                    if stale:
                        await Files.update_file_metadata_by_id(file_id, stale)
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
                    # Mirror pending status into meta at stub-creation time so
                    # the KB file-list query (Task 3) can read status from the
                    # cheap meta column without de-TOASTing data.content.
                    file_meta['status'] = 'pending'
                    if cloud_hash:
                        file_meta['pending_cloud_hash'] = cloud_hash

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
                await Files.set_status(file_id, error_status, error=message)
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
                    await Files.set_status(file_id, stage)

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
        reported a real terminal within ``_max_job_wall_clock_seconds()`` —
        without this guard a stuck pod (the 2026-04-29 staging incident)
        keeps OWUI polling forever and stubs remain in 'pending'.

        ``unchanged_count`` carries through the pre-submit short-circuit
        result so the in-flight progress bar shows total scope, not just
        the submitted subset.

        Forwards an in-flight cancellation request to the loader-worker when
        the user cancels via the UI.
        """
        terminal = {'completed', 'partial', 'failed', 'cancelled'}
        cancel_requested = False
        started_at = time.monotonic()
        wall_clock_cap = _max_job_wall_clock_seconds()

        while True:
            elapsed = time.monotonic() - started_at
            if elapsed > wall_clock_cap:
                log.error(
                    f'Loader-worker job {job_id} exceeded '
                    f'SYNC_MAX_JOB_WALL_CLOCK_SECONDS={wall_clock_cap}; '
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
                return status

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
        status, persists ``last_result`` in KB meta, and returns the sync
        result dict.

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
        except PipelineUnreachableError as e:
            # Loader-worker / ingestion pipeline could not be reached. This is
            # transient (the pod may be restarting): fail-mark the stubs so the
            # KB UI doesn't leave spinners, log a single concise line (no
            # traceback), and re-raise so sync()'s top-level handler attributes
            # the failure to the ingestion service and skips the cycle (no
            # last_sync_at stamp, retry next tick). We intentionally do NOT
            # stamp a generic 'pipeline submit failed' status here — the
            # top-level handler sets the accurate loader-worker message.
            log.warning(
                f'Failed to submit loader-worker job for {self.knowledge_id}: ingestion service unreachable ({e})'
            )
            await self._fail_mark_outstanding_stubs('Document ingestion service temporarily unreachable')
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
                        await Files.set_status(
                            file_id,
                            'error',
                            error=f'sync ended with item still in stage={orphan.get("stage")}',
                        )
                except Exception:
                    log.warning(f'Failed to fail-mark orphan-stage stub {file_id}', exc_info=True)

        # Synthetic timeout from _track_job_progress: fail-mark every stub
        # this sync inserted that's still in a non-terminal state.
        if terminal == 'timed_out':
            changed = await self._fail_mark_outstanding_stubs('Sync timed out')
            log.warning(
                f'Sync timed out for KB {self.knowledge_id}: fail-marked {changed} stub(s) '
                f'(SYNC_MAX_JOB_WALL_CLOCK_SECONDS={_max_job_wall_clock_seconds()})'
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
            total_deleted = 0

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

            # Collapse duplicate feed emissions before anything counts or
            # consumes slots: every downstream per-occurrence counter
            # (progress total, the Classified log, loader items_total) must
            # equal the distinct file count, and the loader must never
            # process the same item twice.
            discovered_count = len(all_files_to_process)
            all_files_to_process = self._dedup_discovered_files(all_files_to_process)
            if len(all_files_to_process) != discovered_count:
                log.info(
                    f'Collapsed {discovered_count - len(all_files_to_process)} duplicate '
                    f'feed emission(s) for {self.knowledge_id}: {discovered_count} -> '
                    f'{len(all_files_to_process)} distinct files'
                )

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
            # Snapshot the KB's membership BEFORE classification and before
            # _create_stub_file_rows links this sync's files — the gate in
            # _classify_for_submit needs pre-sync membership, not post-stub.
            self._kb_member_file_ids = {f.id for f in current_files}
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
            # what passed through the loader-worker.
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

            # Delegate everything to the per-tenant loader-worker pod.
            return await self._sync_via_pipeline(
                all_files_to_process=all_files_to_process,
                total_files=total_files,
                added_file_ids=added_file_ids,
                updated_file_ids=updated_file_ids,
                unchanged_count=unchanged_count,
                total_deleted=total_deleted,
            )

        except (ConnectionError, httpx.TransportError) as e:
            # Connectivity loss — DNS failure, connection refused, or timeout —
            # is transient and expected: the host may be offline or the
            # provider briefly unreachable. Log a single concise line instead
            # of a full traceback, and return a skipped-cycle result rather
            # than re-raising as an unexpected error. The `transient` flag lets
            # the scheduler log a WARNING, and the next tick retries
            # automatically (last_sync_at is not stamped, so the KB stays due).
            #
            # Two distinct failure modes share this handler; only the attributed
            # message and log line differ — control flow (skip cycle, no
            # last_sync_at stamp, retry next tick) is identical:
            #   * PipelineUnreachableError → the loader-worker / ingestion
            #     pipeline is down. The sync source (e.g. Confluence) was
            #     reached fine; mis-attributing this to the source sends
            #     operators debugging the wrong system.
            #   * everything else → the sync source itself is unreachable.
            if isinstance(e, PipelineUnreachableError):
                log.warning(
                    f'Sync skipped for {self.knowledge_id}: document ingestion service (loader-worker) unreachable ({e})'
                )
                error_message = (
                    'Document ingestion service is temporarily unreachable — '
                    'the next scheduled sync will retry automatically.'
                )
                result_error = 'document ingestion service (loader-worker) unreachable'
            else:
                log.warning(f'Sync skipped for {self.knowledge_id}: {self.provider_slug} unreachable ({e})')
                error_message = (
                    'Sync source is temporarily unreachable — the next scheduled sync will retry automatically.'
                )
                result_error = f'{self.provider_slug} unreachable'

            await self._update_sync_status('failed', error=error_message)
            return {
                'files_processed': 0,
                'files_failed': 0,
                'total_found': 0,
                'deleted_count': 0,
                'failed_files': [],
                'error': result_error,
                'transient': True,
            }

        except Exception as e:
            log.exception(f'Sync failed: {e}')
            await self._update_sync_status('failed', error=str(e))
            raise

        finally:
            await self._close_client()
