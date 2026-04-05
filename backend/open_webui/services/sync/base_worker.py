"""Base sync worker - shared logic for cloud storage sync workers."""

import asyncio
import io
import logging
import time
import hashlib
import uuid
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
from open_webui.services.deletion import DeletionService
from open_webui.services.sync.constants import SyncErrorType, FailedFile, CONTENT_TYPES
from open_webui.services.sync.events import (
    emit_sync_progress,
    emit_file_processing,
    emit_file_added,
)

log = logging.getLogger(__name__)


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
                'type': 'http',
                'method': 'POST',
                'path': self.internal_request_path,
                'query_string': b'',
                'headers': Headers({}).raw,
                'app': self.app,
            }
        )

    def _get_user(self):
        """Fetch the user object for process_file access control."""
        user = Users.get_user_by_id(self.user_id)
        if not user:
            raise RuntimeError(f'User {self.user_id} not found')
        return user

    def _check_cancelled(self) -> bool:
        """Check if sync has been cancelled by user."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
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
        failed_files: Optional[List[FailedFile]] = None,
    ):
        """Update sync status in knowledge meta and emit Socket.IO event."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
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
            if error:
                sync_info['error'] = error
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
                        'failed_files': failed_files_dicts,
                    },
                }
            )

    def _get_content_type(self, filename: str) -> str:
        """Get MIME type from filename."""
        ext = Path(filename).suffix.lower()
        return CONTENT_TYPES.get(ext, 'application/octet-stream')

    async def _save_sources(self):
        """Save updated sources to knowledge metadata."""
        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})
        sync_info['sources'] = self.sources
        meta[self.meta_key] = sync_info

        Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

    async def _handle_deleted_item(self, item: Dict[str, Any]):
        """Handle a deleted item from changes query."""
        item_id = item.get('id')
        if not item_id:
            return

        file_id = f'{self.file_id_prefix}{item_id}'

        existing = Files.get_file_by_id(file_id)
        if existing:
            log.info(f'Removing deleted file from KB: {file_id}')

            Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file_id)

            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={'file_id': file_id},
                )
            except Exception as e:
                log.warning(f'Failed to remove vectors for {file_id} from KB: {e}')

            remaining_refs = Knowledges.get_knowledge_files_by_file_id(file_id)
            if not remaining_refs:
                log.info(f'No remaining references to {file_id}, cleaning up')
                await asyncio.to_thread(DeletionService.delete_file, file_id)
            else:
                log.info(f'File {file_id} still referenced by {len(remaining_refs)} KB(s), preserving')

    async def _ensure_vectors_in_kb(self, file_id: str) -> Optional[FailedFile]:
        """Copy vectors from the per-file collection into this KB's collection.

        Runs in a thread because process_file uses asyncio.run_coroutine_threadsafe
        internally for embeddings, which would deadlock if called directly on the
        event loop thread.
        """
        try:
            from open_webui.routers.retrieval import process_file, ProcessFileForm
            from fastapi import HTTPException

            def _call():
                log.info(f'[sync:ensure:{file_id}] >>> PROCESS_FILE START')
                t0 = time.time()
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
                log.info(f'[sync:ensure:{file_id}] <<< PROCESS_FILE END ({time.time() - t0:.1f}s)')

            await asyncio.to_thread(_call)
            return None
        except HTTPException as e:
            detail = str(e.detail) if e.detail else ''
            if e.status_code == 400 and 'Duplicate content' in detail:
                return None
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f'Failed to copy vectors to KB: {detail}'[:100],
            )
        except ValueError as e:
            error_msg = str(e).lower()
            if 'empty' in error_msg or 'no content' in error_msg:
                return FailedFile(
                    filename=file_id,
                    error_type=SyncErrorType.EMPTY_CONTENT.value,
                    error_message='File has no extractable content',
                )
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f'Error copying vectors: {str(e)}'[:80],
            )
        except Exception as e:
            return FailedFile(
                filename=file_id,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message=f'Error copying vectors: {str(e)}'[:80],
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
        user = self._get_user()

        def _extract_in_thread():
            with get_db() as db:
                if user.role == 'admin':
                    file = Files.get_file_by_id(file_id, db=db)
                else:
                    file = Files.get_file_by_id_and_user_id(file_id, user.id, db=db)

                if not file:
                    raise ValueError(f'File {file_id} not found')

                file_path = file.path
                if not file_path:
                    raise ValueError(f'File {file_id} has no path')

                file_path = Storage.get_file(file_path)

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
                use_external = external_pipeline_url and external_pipeline_url.strip() != ''
                docs = None

                if use_external:
                    try:
                        result = call_external_pipeline(
                            file_path=file_path,
                            filename=file.filename,
                            content_type=file.meta.get('content_type', ''),
                            external_pipeline_url=external_pipeline_url,
                            external_pipeline_api_key=getattr(
                                request.app.state.config, 'EXTERNAL_PIPELINE_API_KEY', None
                            ),
                            loader_instance=loader,
                        )
                        if result.get('success') and result.get('chunks'):
                            docs = [
                                Document(
                                    page_content=chunk['text'],
                                    metadata=chunk.get('metadata', {}),
                                )
                                for chunk in result['chunks']
                            ]
                    except Exception as e:
                        log.warning(f'External pipeline failed for {file.filename}: {e}, falling back')
                        use_external = False

                if docs is None:
                    use_external = False
                    docs = loader.load(file.filename, file.meta.get('content_type'), file_path)

                if not docs:
                    return None

                docs = [
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
                    for doc in docs
                ]

                text_content = ' '.join([doc.page_content for doc in docs])

                # Save extracted text to file record
                Files.update_file_data_by_id(file.id, {'content': text_content}, db=db)
                db.commit()

                return docs, file, not use_external  # needs_split=True for internal pipeline

        return await asyncio.to_thread(_extract_in_thread)

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
        user = self._get_user()

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

            items_file = [
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

            # Insert into per-file collection (overwrite if exists)
            file_collection = f'file-{file_id}'
            log.info(f'[sync:{filename}] >>> WEAVIATE FILE INSERT START ({len(items_file)} vectors)')
            if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
            VECTOR_DB_CLIENT.insert(collection_name=file_collection, items=items_file)
            t_file = time.time()
            log.info(f'[sync:{filename}] <<< WEAVIATE FILE INSERT END ({t_file - t_kb:.1f}s)')

            # Update file metadata
            with get_db() as session:
                Files.update_file_metadata_by_id(file_id, {'collection_name': file_collection}, db=session)
                Files.update_file_data_by_id(file_id, {'status': 'completed'}, db=session)
                Files.update_file_hash_by_id(file_id, file_hash, db=session)

            log.info(f'[sync:{filename}] DONE total={t_file - t0:.1f}s')
            return True

        result = await asyncio.to_thread(_split_embed_and_store)

        if not result:
            log.warning(f'No text content extracted from {filename}')
            with get_db() as session:
                Files.update_file_metadata_by_id(file_id, {'collection_name': f'file-{file_id}'}, db=session)
                Files.update_file_data_by_id(file_id, {'status': 'completed'}, db=session)
                Files.update_file_hash_by_id(file_id, file_hash, db=session)

        return True

    async def _download_and_store(self, file_info: Dict[str, Any]) -> Union[PreparedFile, FailedFile, None]:
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

        if self._check_cancelled():
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Sync cancelled by user',
            )

        # Pre-download cloud hash check — skip download if cloud reports no change.
        # Existing KBs without cloud_hash in meta will fall through to download,
        # populating cloud_hash for subsequent syncs (backward compatible).
        cloud_hash = self._get_cloud_hash(file_info)
        existing = Files.get_file_by_id(file_id)

        if cloud_hash and existing:
            existing_meta = existing.meta or {}
            stored_cloud_hash = existing_meta.get('cloud_hash')
            if stored_cloud_hash and stored_cloud_hash == cloud_hash:
                log.info(f'File {file_id} unchanged (cloud hash match), skipping download')

                new_relative_path = file_info.get('relative_path')
                if new_relative_path and existing_meta.get('relative_path') != new_relative_path:
                    existing_meta['relative_path'] = new_relative_path
                    Files.update_file_by_id(file_id, FileUpdateForm(meta=existing_meta))

                Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)

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
                Files.update_file_by_id(file_id, FileUpdateForm(meta=existing_meta))

            Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)

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
                Files.update_file_by_id(
                    file_id,
                    FileUpdateForm(hash=content_hash, meta=file_meta),
                )
                Files.update_file_path_by_id(file_id, file_path)
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
        """Phase 2: Extract content, embed once, insert into KB + per-file collections.

        Returns None on success, FailedFile on error.
        """
        file_id = prepared.file_id
        name = prepared.name

        if self._check_cancelled():
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

            if self._check_cancelled():
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

        if self._check_cancelled():
            return FailedFile(
                filename=name,
                error_type=SyncErrorType.PROCESSING_ERROR.value,
                error_message='Sync cancelled by user',
            )

        # KB association
        Knowledges.add_file_to_knowledge_by_id(self.knowledge_id, file_id, self.user_id)

        # Cross-KB vector propagation (still uses process_file for other KBs)
        try:
            knowledge_files = Knowledges.get_knowledge_files_by_file_id(file_id)
            for kf in knowledge_files:
                if kf.knowledge_id != self.knowledge_id:
                    log.info(f'Propagating vectors for {file_id} to KB {kf.knowledge_id}')
                    try:
                        VECTOR_DB_CLIENT.delete(
                            collection_name=kf.knowledge_id,
                            filter={'file_id': file_id},
                        )
                    except Exception as e:
                        log.warning(f'Failed to remove old vectors from KB {kf.knowledge_id}: {e}')
                    try:
                        from open_webui.routers.retrieval import process_file, ProcessFileForm

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
                        log.warning(f'Failed to propagate vectors to KB {kf.knowledge_id}: {e}')
        except Exception as e:
            log.warning(f'Failed to propagate vector updates for {file_id}: {e}')

        # Emit file added event
        file_record = Files.get_file_by_id(file_id)
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

    async def sync(self) -> Dict[str, Any]:
        """Execute sync operation for all sources."""
        self._client = self._create_client()

        try:
            await self._update_sync_status('syncing', 0, 0)

            # Verify the owner still has access; may suspend the KB
            await self._sync_permissions()

            # Check if KB was suspended by _sync_permissions()
            knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
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

            # Apply file count limit
            max_files = min(self.max_files_config, KNOWLEDGE_MAX_FILE_COUNT)
            current_files = Knowledges.get_files_by_id(self.knowledge_id) or []
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

            # Count existing provider files that aren't being re-processed
            processing_item_ids = {f['item']['id'] for f in all_files_to_process}
            already_synced = sum(
                1
                for f in current_files
                if f.id.startswith(self.file_id_prefix)
                and f.id.removeprefix(self.file_id_prefix) not in processing_item_ids
            )

            total_files = len(all_files_to_process) + already_synced
            log.info(
                f'Total files to process: {len(all_files_to_process)} '
                f'({already_synced} already synced, {total_files} total)'
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
            processed_count = already_synced
            failed_count = 0
            results_lock = asyncio.Lock()
            cancelled = False

            # Per-file timeout: extraction (120s) + chunking (120s) + embedding (300s) + overhead
            FILE_PIPELINE_TIMEOUT = 600  # 10 minutes

            async def _pipeline_inner(file_info: Dict[str, Any], index: int) -> Optional[FailedFile]:
                nonlocal processed_count, failed_count, cancelled

                if cancelled or self._check_cancelled():
                    cancelled = True
                    return FailedFile(
                        filename=file_info.get('name', 'unknown'),
                        error_type=SyncErrorType.PROCESSING_ERROR.value,
                        error_message='Sync cancelled by user',
                    )

                try:
                    # Phase 1: Download + store (high concurrency)
                    async with download_semaphore:
                        if cancelled or self._check_cancelled():
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
                        if cancelled or self._check_cancelled():
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
                                file_record = Files.get_file_by_id(result.file_id)
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

            # Process files in batches to avoid overwhelming the event loop
            # and thread pool. Each batch runs with the existing semaphores
            # for download/process concurrency within the batch.
            batch_size = max_download_concurrent + max_process_concurrent
            log.info(
                f'Starting pipeline processing of {len(all_files_to_process)} files '
                f'(thread pool: {thread_pool_size}, '
                f'download concurrency: {max_download_concurrent}, '
                f'process concurrency: {max_process_concurrent}, '
                f'batch size: {batch_size})'
            )
            start_time = time.time()

            total_batches = (len(all_files_to_process) + batch_size - 1) // batch_size

            for batch_start in range(0, len(all_files_to_process), batch_size):
                if cancelled or self._check_cancelled():
                    cancelled = True
                    break

                batch_num = batch_start // batch_size + 1
                batch = all_files_to_process[batch_start : batch_start + batch_size]
                log.info(f'>>> BATCH {batch_num}/{total_batches} START ({len(batch)} files, offset {batch_start})')
                batch_t0 = time.time()
                batch_tasks = [pipeline(file_info, batch_start + i) for i, file_info in enumerate(batch)]
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                log.info(
                    f'<<< BATCH {batch_num}/{total_batches} END '
                    f'({time.time() - batch_t0:.1f}s, '
                    f'processed={processed_count}, failed={failed_count})'
                )

                for result in batch_results:
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
                    total_processed + total_failed,
                    total_files,
                    '',
                    'Sync cancelled by user',
                    total_processed,
                    total_failed,
                    total_deleted,
                    failed_files,
                )
                return {
                    'files_processed': total_processed,
                    'files_failed': total_failed,
                    'total_found': total_files,
                    'deleted_count': total_deleted,
                    'cancelled': True,
                    'failed_files': [asdict(f) for f in failed_files],
                }

            # Save updated sources
            await self._save_sources()

            failed_files_dicts = [asdict(f) for f in failed_files]

            # Update final sync status
            knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
            meta = knowledge.meta or {}
            sync_info = meta.get(self.meta_key, {})
            sync_info['last_sync_at'] = int(time.time())
            sync_info['status'] = 'completed' if total_failed == 0 else 'completed_with_errors'
            sync_info['last_result'] = {
                'files_processed': total_processed,
                'files_failed': total_failed,
                'total_found': total_files,
                'deleted_count': total_deleted,
                'failed_files': failed_files_dicts,
            }
            meta[self.meta_key] = sync_info
            Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

            await self._update_sync_status(
                sync_info['status'],
                total_files,
                total_files,
                '',
                None,
                total_processed,
                total_failed,
                total_deleted,
                failed_files,
            )

            log.info(f'Sync completed for {self.knowledge_id}: {total_processed} processed, {total_failed} failed')

            return {
                'files_processed': total_processed,
                'files_failed': total_failed,
                'total_found': total_files,
                'deleted_count': total_deleted,
                'failed_files': failed_files_dicts,
            }

        except Exception as e:
            log.exception(f'Sync failed: {e}')
            await self._update_sync_status('failed', error=str(e))
            raise

        finally:
            await self._close_client()
