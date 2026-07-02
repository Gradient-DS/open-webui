import hashlib
import json
import logging
import os
import time
import uuid
from typing import NamedTuple, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from langchain_core.documents import Document
from pydantic import BaseModel

from open_webui.config import KNOWLEDGE_MAX_FILE_COUNT
from open_webui.models.file_attachments import FileAttachmentForm, FileAttachments
from open_webui.models.files import FileForm, Files
from open_webui.models.knowledge import KnowledgeForm, Knowledges
from open_webui.retrieval.loaders.main import Loader
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.routers.retrieval import save_docs_to_vector_db, submit_existing_file_to_pipeline
from open_webui.services.files.events import emit_file_status
from open_webui.services.sync.provider import file_id_prefix_for
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_verified_user
from open_webui.utils.doc_pipeline import ACTING_PROVIDER
from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal

router = APIRouter()
log = logging.getLogger(__name__)


# --- Pydantic Models ---


VALID_DATA_TYPES = {'parsed_text', 'chunked_text', 'full_documents'}


class IngestCollection(BaseModel):
    source_id: str
    name: str
    description: str = ''
    data_type: str = 'parsed_text'
    # 'knowledge' → embed into the KB collection + link the file to the KB
    # (default; unchanged behavior). 'file' → per-file chat attachment: embed
    # into file-{doc.source_id}, update the existing File row, link to NO KB.
    # warren echoes this key back opaquely (it never inspects collection), so
    # the per-file target is enforced entirely here on the OWUI side.
    target: str = 'knowledge'
    language: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}
    # None = public, {} = private (default), {"read": {"group_ids": [], "user_ids": []}, ...} = custom
    access_control: Optional[dict] = {}


class IngestAttachmentManifest(BaseModel):
    """One render artefact attached to a document.

    Manifest entries live inside each document in the ``data`` JSON
    body; their bytes ride on the multipart envelope under field name
    ``attachments``. ``part_name`` is the multipart filename the
    receiver uses to find the matching ``UploadFile``.
    """

    kind: str
    content_type: str = 'image/png'
    storey: Optional[str] = None
    caption: str = ''
    part_name: str


class IngestDocumentBase(BaseModel):
    source_id: str
    filename: str
    content_type: str = 'text/plain'
    title: Optional[str] = None
    source_url: Optional[str] = None
    language: Optional[str] = None
    author: Optional[str] = None
    modified_at: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}
    attachments: list[IngestAttachmentManifest] = []


class ParsedTextDocument(IngestDocumentBase):
    text: str


class ChunkedTextDocument(IngestDocumentBase):
    chunks: list[str]


class FullDocument(IngestDocumentBase):
    pass


class IngestForm(BaseModel):
    collection: IngestCollection
    documents: list[dict]


# Warren cloud-sync (Option 1b) — loader-worker staging + submit + poll.


class StageRequest(BaseModel):
    knowledge_id: str
    source_id: str
    filename: str
    content_type: str = 'application/octet-stream'


class SubmitRequest(BaseModel):
    file_id: str
    knowledge_id: str


# --- Helper Functions ---


def get_integration_provider(request: Request, user) -> tuple[str, dict]:
    """Resolve the integration provider from the authenticated service account."""
    provider_slug = (user.info or {}).get('integration_provider')
    if not provider_slug:
        raise HTTPException(
            status_code=403,
            detail=(
                f'User {user.id!r} ({user.email}) is authenticated but is not bound to an '
                'integration provider — user.info.integration_provider is empty. Bind this '
                "user to a provider in OWUI admin → Integraties → 'Service account', or "
                'authenticate with a different sk- key.'
            ),
        )
    providers = request.app.state.config.INTEGRATION_PROVIDERS
    if not providers:
        raise HTTPException(
            status_code=403,
            detail=(
                'INTEGRATION_PROVIDERS is empty — no providers are registered on this '
                'deployment. Register one in OWUI admin → Integraties before pushing.'
            ),
        )
    provider_config = providers.get(provider_slug)
    if not provider_config:
        raise HTTPException(
            status_code=403,
            detail=(
                f'Integration provider {provider_slug!r} (bound to this service account) is '
                f'not registered. Known providers: {sorted(providers.keys()) or "[]"}. '
                'Register the slug in OWUI admin → Integraties or rebind the service account.'
            ),
        )
    return provider_slug, provider_config


def _validate_custom_metadata(doc: IngestDocumentBase, provider_config: dict):
    """Validate that required custom metadata fields are present in doc.metadata."""
    custom_fields = provider_config.get('custom_metadata_fields', [])
    missing = []
    for field in custom_fields:
        if field.get('required') and field.get('key') not in doc.metadata:
            missing.append(field['key'])
    if missing:
        raise HTTPException(
            400,
            f"Document '{doc.source_id}' is missing required metadata fields: {', '.join(missing)}",
        )


async def _find_kb_by_source_id(provider: str, source_id: str):
    """Find a knowledge base by provider slug + external source_id."""
    kbs = await Knowledges.get_knowledge_bases_by_type(provider)
    for kb in kbs:
        meta = kb.meta or {}
        if meta.get('integration', {}).get('source_id') == source_id:
            return kb
    return None


async def _create_kb_for_provider(
    provider: str,
    provider_config: dict,
    collection: IngestCollection,
    user_id: str,
):
    """Create a new knowledge base for a push provider."""
    form = KnowledgeForm(
        name=collection.name,
        description=collection.description,
        type=provider,
        access_control=collection.access_control,
    )
    knowledge = await Knowledges.insert_new_knowledge(user_id, form)
    meta = {
        'integration': {
            'provider': provider,
            'source_id': collection.source_id,
            'data_type': collection.data_type,
            'language': collection.language,
            'tags': collection.tags,
            'provider_metadata': collection.metadata,
        }
    }
    await Knowledges.update_knowledge_meta_by_id(knowledge.id, meta)
    return await Knowledges.get_knowledge_by_id(knowledge.id)


def _maybe_upload_original_bytes(
    file_id: str,
    doc: IngestDocumentBase,
    provider: str,
    original_file: Optional[UploadFile],
) -> str:
    """Upload the document's source bytes to ``Storage`` and return the path.

    Returns ``''`` when ``original_file`` is None — the historical
    behaviour for callers (push integrations) that ship parsed text but
    no original blob. The loader-worker started shipping bytes alongside
    chunks so the citation-modal preview can serve them; this helper is
    the receiving seam. Mirrors the pattern in
    :func:`_process_full_document` so all three ingest paths converge on
    a populated ``file.path`` when bytes are available.
    """
    if original_file is None:
        return ''
    contents, file_path = Storage.upload_file(
        original_file.file,
        f'{file_id}_{doc.filename}',
        {'provider': provider, 'source_id': doc.source_id},
    )
    return file_path


async def _create_or_update_file_record(
    file_id: str,
    doc: IngestDocumentBase,
    content_text: str,
    file_path: str,
    provider: str,
    knowledge_id: Optional[str],
    user_id: str,
) -> str:
    """Create or update a File record. Returns 'created' or 'updated'.

    ``knowledge_id=None`` is the per-file (non-KB) chat-attachment target: the
    File row is created/updated exactly as for the KB path, but it is NOT linked
    to any KB (no ``add_file_to_knowledge_by_id``) and the KB path-field write
    (``set_path_fields_by_file_id``) is skipped. The ``data={'content': ...}``
    write stays either way — it is what BYPASS full-content injection reads."""
    meta = {
        'name': doc.title or doc.filename,
        'content_type': doc.content_type,
        'source': provider,
        'source_id': doc.source_id,
        'source_url': doc.source_url,
        'language': doc.language,
        'author': doc.author,
        'tags': doc.tags,
        'provider_metadata': doc.metadata,
    }

    # Promote folder-rendering / change-detection keys to top-level so the KB
    # UI's SourceGroupedFiles tree (reads file.meta.relative_path) and the next
    # sync cycle's cloud-hash short-circuit (reads file.meta.cloud_hash) keep
    # working. Legacy in-pod sync wrote these at top level; the loader-worker
    # path nests them under provider_metadata, which silently broke the folder
    # tree until promoted back.
    for key in (
        'relative_path',
        'source_item_id',
        'onedrive_item_id',
        'onedrive_drive_id',
        'google_drive_item_id',
        'cloud_hash',
        'last_synced_at',
    ):
        if key in doc.metadata:
            meta[key] = doc.metadata[key]

    existing_file = await Files.get_file_by_id(file_id)
    if existing_file:
        await Files.update_file_metadata_by_id(file_id, meta)
        await Files.update_file_data_by_id(file_id, {'content': content_text})
        # This branch updates file.meta but never calls
        # add_file_to_knowledge_by_id (the KB link already exists from the
        # sync stub), so mirror the freshly-promoted relative_path /
        # source_item_id onto the knowledge_file rows here. Captures files that
        # moved folders between stub discovery and the loader-worker callback.
        # Skipped for per-file chat attachments (knowledge_id=None): there are
        # no knowledge_file rows to keep in sync.
        if knowledge_id is not None:
            await Knowledges.set_path_fields_by_file_id(file_id, {**(existing_file.meta or {}), **meta})
        # Stub File rows created up-front by sync workers
        # (services/sync/base_worker._create_stub_file_rows) carry
        # ``path=''`` until the loader-worker callback arrives with the
        # actual bytes. Update the path here so the citation-modal
        # preview endpoint can serve them. Don't overwrite a non-empty
        # existing path with an empty new one — that would silently
        # break previews for callers that opt out of byte shipping
        # (e.g. push integrations re-syncing a previously-byte-bearing
        # KB).
        if file_path and file_path != (existing_file.path or ''):
            await Files.update_file_path_by_id(file_id, file_path)
        return 'updated'
    else:
        text_hash = hashlib.sha256(content_text.encode()).hexdigest()
        file_form = FileForm(
            id=file_id,
            filename=doc.filename,
            hash=text_hash,
            path=file_path,
            data={'content': content_text},
            meta=meta,
        )
        await Files.insert_new_file(user_id, file_form)
        # Per-file chat attachments (knowledge_id=None) are deliberately KB-less:
        # retrieval already resolves them via the file-{id} cache collection, so
        # no KB membership row is created.
        if knowledge_id is not None:
            await Knowledges.add_file_to_knowledge_by_id(knowledge_id, file_id, user_id)
        return 'created'


async def _delete_old_vectors(knowledge_id: str, file_id: str):
    """Delete existing vectors for a file (idempotent update)."""
    try:
        await ASYNC_VECTOR_DB_CLIENT.delete(
            collection_name=knowledge_id,
            filter={'file_id': file_id},
        )
    except Exception:
        log.warning(f'Failed to delete old vectors for {file_id}, proceeding with insert')


def _get_loader_kwargs(request: Request) -> dict:
    """Build kwargs dict for Loader() from app config."""
    config = request.app.state.config
    return {
        'DATALAB_MARKER_API_KEY': config.DATALAB_MARKER_API_KEY,
        'DATALAB_MARKER_API_BASE_URL': config.DATALAB_MARKER_API_BASE_URL,
        'DATALAB_MARKER_ADDITIONAL_CONFIG': config.DATALAB_MARKER_ADDITIONAL_CONFIG,
        'DATALAB_MARKER_SKIP_CACHE': config.DATALAB_MARKER_SKIP_CACHE,
        'DATALAB_MARKER_FORCE_OCR': config.DATALAB_MARKER_FORCE_OCR,
        'DATALAB_MARKER_PAGINATE': config.DATALAB_MARKER_PAGINATE,
        'DATALAB_MARKER_STRIP_EXISTING_OCR': config.DATALAB_MARKER_STRIP_EXISTING_OCR,
        'DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION': config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
        'DATALAB_MARKER_FORMAT_LINES': config.DATALAB_MARKER_FORMAT_LINES,
        'DATALAB_MARKER_USE_LLM': config.DATALAB_MARKER_USE_LLM,
        'DATALAB_MARKER_OUTPUT_FORMAT': config.DATALAB_MARKER_OUTPUT_FORMAT,
        'EXTERNAL_DOCUMENT_LOADER_URL': config.EXTERNAL_DOCUMENT_LOADER_URL,
        'EXTERNAL_DOCUMENT_LOADER_API_KEY': config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
        'TIKA_SERVER_URL': config.TIKA_SERVER_URL,
        'DOCLING_SERVER_URL': config.DOCLING_SERVER_URL,
        'DOCLING_API_KEY': config.DOCLING_API_KEY,
        'DOCLING_PARAMS': config.DOCLING_PARAMS,
        'PDF_EXTRACT_IMAGES': config.PDF_EXTRACT_IMAGES,
        'DOCUMENT_INTELLIGENCE_ENDPOINT': config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        'DOCUMENT_INTELLIGENCE_KEY': config.DOCUMENT_INTELLIGENCE_KEY,
        'DOCUMENT_INTELLIGENCE_MODEL': config.DOCUMENT_INTELLIGENCE_MODEL,
        'MISTRAL_OCR_API_BASE_URL': config.MISTRAL_OCR_API_BASE_URL,
        'MISTRAL_OCR_API_KEY': config.MISTRAL_OCR_API_KEY,
        'MINERU_API_MODE': config.MINERU_API_MODE,
        'MINERU_API_URL': config.MINERU_API_URL,
        'MINERU_API_KEY': config.MINERU_API_KEY,
        'MINERU_API_TIMEOUT': config.MINERU_API_TIMEOUT,
        'MINERU_PARAMS': config.MINERU_PARAMS,
    }


def _build_base_metadata(doc: IngestDocumentBase, file_id: str, provider: str, user_id: str) -> dict:
    """Build common metadata dict for LangChain Documents."""
    base = {
        'name': doc.title or doc.filename,
        'source': doc.source_url or doc.filename,
        'file_id': file_id,
        'created_by': user_id,
        'author': doc.author,
        'language': doc.language,
        'source_provider': provider,
        'content_type': doc.content_type,
        'tags': doc.tags,
    }
    # Flatten doc.metadata into prefixed keys to avoid collisions
    for key, value in doc.metadata.items():
        base[f'meta_{key}'] = value
    return base


# --- Processing Functions ---


async def _process_parsed_text_document(
    request: Request,
    knowledge_id: str,
    provider: str,
    doc: ParsedTextDocument,
    user_id: str,
    original_file: Optional[UploadFile] = None,
) -> dict:
    """Process a parsed_text document: create file record, chunk, embed, store.

    When ``original_file`` is provided (loader-worker shipping the bytes
    it downloaded from the cloud provider), the bytes are uploaded via
    :data:`Storage` so ``file.path`` is populated for the citation-modal
    PDF preview. When absent — the historical contract for push
    integrations that don't have source bytes — ``file.path`` stays
    empty and the preview tab is gracefully unavailable.
    """
    file_id = f'{file_id_prefix_for(provider)}{doc.source_id}'

    file_path = _maybe_upload_original_bytes(file_id, doc, provider, original_file)

    status = await _create_or_update_file_record(
        file_id=file_id,
        doc=doc,
        content_text=doc.text,
        file_path=file_path,
        provider=provider,
        knowledge_id=knowledge_id,
        user_id=user_id,
    )

    if status == 'updated':
        await _delete_old_vectors(knowledge_id, file_id)

    text_hash = hashlib.sha256(doc.text.encode()).hexdigest()
    lc_doc = Document(
        page_content=doc.text,
        metadata=_build_base_metadata(doc, file_id, provider, user_id),
    )

    try:
        # save_docs_to_vector_db is sync and blocks on embedding + vector-DB
        # I/O. Offload so the event loop stays responsive — otherwise large
        # ingest batches from the loader-worker callback freeze the pod and
        # trip liveness probes.
        await run_in_threadpool(
            save_docs_to_vector_db,
            request=request,
            docs=[lc_doc],
            collection_name=knowledge_id,
            metadata={
                'file_id': file_id,
                'name': doc.title or doc.filename,
                'hash': text_hash,
            },
            add=True,
            split=True,
        )
        # Dual-write status into data (existing readers) and meta (cheap
        # KB file-list read path).  set_status clears the stale ``error``
        # field in both columns so a re-run after a prior failure does not
        # leave a stale error message in data.error.
        await Files.set_status(file_id, 'completed', error=None)
    except Exception as e:
        log.exception(f'Failed to store document {doc.source_id} in vector DB')
        await Files.set_status(file_id, 'error', error=str(e))
        return {
            'source_id': doc.source_id,
            'file_id': file_id,
            'status': 'error',
            'error': str(e),
        }

    return {'source_id': doc.source_id, 'file_id': file_id, 'status': status}


async def _process_chunked_text_document(
    request: Request,
    knowledge_id: Optional[str],
    provider: str,
    doc: ChunkedTextDocument,
    user_id: str,
    original_file: Optional[UploadFile] = None,
    *,
    collection_name: Optional[str] = None,
    add: bool = True,
    skip_embed: bool = False,
) -> dict:
    """Process a chunked_text document: create file record, embed pre-chunked text, store.

    See :func:`_process_parsed_text_document` for the ``original_file``
    contract — same semantics here.

    Embed target vs KB link are decoupled so the same helper serves both the KB
    push and the per-file chat-attachment path:
    - ``collection_name`` is the vector-DB collection to embed into. Defaults to
      ``knowledge_id`` (KB path); the per-file path passes ``f'file-{file_id}'``.
    - ``add`` mirrors ``save_docs_to_vector_db``'s append semantics: ``True`` for
      the KB collection (dedup-then-append), ``False`` for the per-file cache
      (created fresh, native chat parity).
    - ``knowledge_id`` still drives the KB link inside
      :func:`_create_or_update_file_record`; pass ``None`` for the per-file path.
    - ``skip_embed`` (BYPASS_EMBEDDING_AND_RETRIEVAL): store the File-row content
      only and write no vectors, mirroring native BYPASS.
    """
    file_id = f'{file_id_prefix_for(provider)}{doc.source_id}'
    joined_text = '\n\n'.join(doc.chunks)

    file_path = _maybe_upload_original_bytes(file_id, doc, provider, original_file)

    status = await _create_or_update_file_record(
        file_id=file_id,
        doc=doc,
        content_text=joined_text,
        file_path=file_path,
        provider=provider,
        knowledge_id=knowledge_id,
        user_id=user_id,
    )

    embed_collection = collection_name if collection_name is not None else knowledge_id

    if skip_embed:
        # BYPASS_EMBEDDING_AND_RETRIEVAL: the File-row content is already written
        # by _create_or_update_file_record; full-content injection reads that. No
        # vectors are written and no collection is touched.
        await Files.set_status(file_id, 'completed', error=None)
        return {'source_id': doc.source_id, 'file_id': file_id, 'status': status}

    # Dedup-then-append only makes sense on the KB append path (add=True). The
    # per-file cache uses add=False (fresh collection, native chat parity), where
    # deleting first would drop vectors the no-op re-insert never restores.
    if add and status == 'updated':
        await _delete_old_vectors(embed_collection, file_id)

    text_hash = hashlib.sha256(joined_text.encode()).hexdigest()
    base_metadata = _build_base_metadata(doc, file_id, provider, user_id)

    lc_docs = [Document(page_content=chunk, metadata=base_metadata) for chunk in doc.chunks]

    try:
        await run_in_threadpool(
            save_docs_to_vector_db,
            request=request,
            docs=lc_docs,
            collection_name=embed_collection,
            metadata={
                'file_id': file_id,
                'name': doc.title or doc.filename,
                'hash': text_hash,
            },
            add=add,
            split=False,
        )
        # Dual-write status into data (existing readers) and meta (cheap
        # KB file-list read path).  set_status clears the stale ``error``
        # field in both columns so a re-run after a prior failure does not
        # leave a stale error message in data.error.
        await Files.set_status(file_id, 'completed', error=None)
    except Exception as e:
        log.exception(f'Failed to store chunked document {doc.source_id} in vector DB')
        await Files.set_status(file_id, 'error', error=str(e))
        return {
            'source_id': doc.source_id,
            'file_id': file_id,
            'status': 'error',
            'error': str(e),
        }

    return {'source_id': doc.source_id, 'file_id': file_id, 'status': status}


async def _process_full_document(
    request: Request,
    knowledge_id: str,
    provider: str,
    doc: FullDocument,
    upload_file: UploadFile,
    user_id: str,
) -> dict:
    """Process a full_document: upload binary, extract text, chunk, embed, store."""
    file_id = f'{file_id_prefix_for(provider)}{doc.source_id}'

    # Upload binary file to storage
    try:
        contents, file_path = Storage.upload_file(
            upload_file.file,
            f'{file_id}_{doc.filename}',
            {'provider': provider, 'source_id': doc.source_id},
        )
    except Exception as e:
        log.exception(f'Failed to upload file {doc.filename}')
        return {
            'source_id': doc.source_id,
            'file_id': file_id,
            'status': 'error',
            'error': str(e),
        }

    # Extract text using Loader
    try:
        loader_kwargs = _get_loader_kwargs(request)
        loader = Loader(
            engine=request.app.state.config.CONTENT_EXTRACTION_ENGINE,
            **loader_kwargs,
        )
        local_path = Storage.get_file(file_path)
        extracted_docs = loader.load(doc.filename, doc.content_type, local_path)
        extracted_text = '\n\n'.join(d.page_content for d in extracted_docs)
    except Exception as e:
        log.exception(f'Failed to extract text from {doc.filename}')
        return {
            'source_id': doc.source_id,
            'file_id': file_id,
            'status': 'error',
            'error': str(e),
        }

    status = await _create_or_update_file_record(
        file_id=file_id,
        doc=doc,
        content_text=extracted_text,
        file_path=file_path,
        provider=provider,
        knowledge_id=knowledge_id,
        user_id=user_id,
    )

    if status == 'updated':
        await _delete_old_vectors(knowledge_id, file_id)

    text_hash = hashlib.sha256(extracted_text.encode()).hexdigest()
    base_metadata = _build_base_metadata(doc, file_id, provider, user_id)

    # Add file-level metadata from extraction
    for d in extracted_docs:
        d.metadata.update(base_metadata)

    try:
        await run_in_threadpool(
            save_docs_to_vector_db,
            request=request,
            docs=extracted_docs,
            collection_name=knowledge_id,
            metadata={
                'file_id': file_id,
                'name': doc.title or doc.filename,
                'hash': text_hash,
            },
            add=True,
            split=True,
        )
        # Dual-write status into data (existing readers) and meta (cheap
        # KB file-list read path).  set_status clears the stale ``error``
        # field in both columns so a re-run after a prior failure does not
        # leave a stale error message in data.error.
        await Files.set_status(file_id, 'completed', error=None)
    except Exception as e:
        log.exception(f'Failed to store full document {doc.source_id} in vector DB')
        await Files.set_status(file_id, 'error', error=str(e))
        return {
            'source_id': doc.source_id,
            'file_id': file_id,
            'status': 'error',
            'error': str(e),
        }

    return {'source_id': doc.source_id, 'file_id': file_id, 'status': status}


class PersistResult(NamedTuple):
    saved: int
    skipped: int


def _persist_attachments(
    *,
    file_id: str,
    manifest: list[IngestAttachmentManifest],
    part_lookup: dict[str, UploadFile],
) -> PersistResult:
    """Persist a document's attachment bytes to Storage + DB.

    Best-effort: missing parts, Storage failures, and DB insert failures
    are logged + skipped; the document ingest stays successful. Existing
    attachments for file_id are deleted first so re-upload regenerates
    cleanly.

    Returns (saved, skipped) counts as a NamedTuple.
    """
    FileAttachments.delete_attachments_by_file_id(file_id)

    saved, skipped = 0, 0
    for i, entry in enumerate(manifest):
        part = part_lookup.get(entry.part_name)
        if part is None:
            log.warning(
                'attachment manifest references missing part %r (file_id=%s, kind=%s, storey=%s); skipping',
                entry.part_name,
                file_id,
                entry.kind,
                entry.storey,
            )
            skipped += 1
            continue

        # Reset stream: FastAPI leaves the SpooledTemporaryFile at EOF
        # after reading the multipart body, so without this seek the
        # subsequent Storage.upload_file would receive an empty stream.
        part.file.seek(0)
        # Defense in depth: even though part_name comes from a trusted
        # loader-worker today, a path-traversal value (e.g. '../../etc/foo')
        # would escape UPLOAD_DIR via LocalStorageProvider; strip any
        # directory component before composing the Storage filename.
        safe_part_name = os.path.basename(entry.part_name)
        storage_filename = f'{uuid.uuid4()}-{safe_part_name}'
        try:
            _, path = Storage.upload_file(
                part.file,
                storage_filename,
                tags={'file_id': file_id, 'kind': entry.kind},
            )
        except Exception:
            log.exception(
                'storage upload failed for attachment (file_id=%s, part=%s)',
                file_id,
                entry.part_name,
            )
            skipped += 1
            continue

        try:
            FileAttachments.insert_new_attachment(
                FileAttachmentForm(
                    id=str(uuid.uuid4()),
                    file_id=file_id,
                    kind=entry.kind,
                    storey=entry.storey,
                    index=i,
                    content_type=entry.content_type,
                    caption=entry.caption,
                    path=path,
                ),
            )
        except Exception:
            log.exception(
                'db insert failed after successful upload — leaking storage object at %s (file_id=%s, part=%s)',
                path,
                file_id,
                entry.part_name,
            )
            skipped += 1
            continue

        saved += 1
    return PersistResult(saved=saved, skipped=skipped)


def _maybe_persist_attachments(
    *,
    result: dict,
    doc: IngestDocumentBase,
    part_lookup: dict[str, UploadFile],
) -> None:
    """If the document's text save succeeded and it carries attachments,
    persist them and record the counts on the result dict.

    Mutates ``result`` in place; intentional — the dispatch loop already
    owns the dict and we want the side-channel counts to ride on the
    existing per-document response without a wrapper layer.
    """
    # Only persist attachments when the document text was committed.
    if result.get('status') not in ('created', 'updated'):
        return
    if not doc.attachments:
        return
    outcome = _persist_attachments(
        file_id=result['file_id'],
        manifest=doc.attachments,
        part_lookup=part_lookup,
    )
    result['attachments_saved'] = outcome.saved
    result['attachments_skipped'] = outcome.skipped


# --- Endpoints ---


@router.post('/ingest')
async def ingest_documents(
    request: Request,
    data: UploadFile = File(...),
    files: Optional[list[UploadFile]] = File(None),
    original_files: Optional[list[UploadFile]] = File(None),
    attachments: Optional[list[UploadFile]] = File(None),
    principal=Depends(get_integration_principal),
):
    # Parse the JSON payload from the ``data`` *file part* (not a form field).
    # Starlette caps non-file multipart form fields at 1MB
    # (formparsers.max_part_size); large-KB syncs push thousands of chunks well
    # past that, so the JSON rides as a file part instead — file parts spool to
    # a SpooledTemporaryFile with no size check, removing the ceiling entirely.
    # Senders: genai-utils api/gateway/loader_worker/ingest_client.py and
    # document_processing/distributed/pipeline/clients/owui_ingest_client.py.
    try:
        raw = (await data.read()).decode('utf-8')
        form_data = IngestForm(**json.loads(raw))
    except (json.JSONDecodeError, Exception) as e:
        raise HTTPException(400, f"Invalid JSON in 'data' file part: {e}")

    # ``original_files`` carries the source bytes for parsed_text /
    # chunked_text documents, keyed by ``filename == source_id`` (the
    # loader-worker sets this when shipping bytes — see
    # genai-utils/api/gateway/loader_worker/ingest_client.py). Build the
    # lookup once so the dispatch loops below stay O(documents) rather
    # than O(documents * files).
    original_file_lookup = {f.filename: f for f in (original_files or []) if f.filename}
    attachment_lookup = {f.filename: f for f in (attachments or []) if f.filename}

    if isinstance(principal, LoaderPrincipal):
        user = principal.user
        provider = principal.provider_slug
        providers = request.app.state.config.INTEGRATION_PROVIDERS or {}
        # The loader bearer (LOADER_INGEST_API_KEY) is the strong auth signal
        # for machine callers. INTEGRATION_PROVIDERS is for *external* push
        # integrations (third-party systems pushing docs in); built-in cloud
        # sync providers (onedrive, google_drive) don't need to be registered
        # there. Fall back to an empty provider_config so default limits and
        # no custom-metadata requirements apply.
        provider_config = providers.get(provider) or {}
    else:
        user = principal
        provider, provider_config = get_integration_provider(request, user)

    # Validate batch size
    max_per_request = provider_config.get('max_documents_per_request', 50)
    if len(form_data.documents) > max_per_request:
        raise HTTPException(400, f'Too many documents. Maximum {max_per_request} per request.')

    # Validate data_type
    collection = (
        IngestCollection(**form_data.collection) if isinstance(form_data.collection, dict) else form_data.collection
    )
    data_type = collection.data_type
    if data_type not in VALID_DATA_TYPES:
        raise HTTPException(
            400,
            f"Invalid data_type '{data_type}'. Must be one of: {', '.join(sorted(VALID_DATA_TYPES))}",
        )

    # Per-file (non-KB) chat-attachment dispatch. warren echoes collection.target
    # back opaquely; when it is 'file' this push targets a chat attachment's
    # per-file cache collection (file-{id}) with NO KB — skip KB find/create, the
    # KB file-limit check, and the KB membership write entirely. The File row is
    # the one created up-front by the direct upload; owui_upload's empty prefix
    # makes f'{prefix}{source_id}' an identity so we update it in place.
    if isinstance(principal, LoaderPrincipal) and collection.target == 'file':
        if data_type != 'chunked_text':
            raise HTTPException(
                400,
                f"target='file' requires data_type 'chunked_text', got '{data_type}'.",
            )
        bypass = request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL
        results = []
        for raw_doc in form_data.documents:
            try:
                doc = ChunkedTextDocument(**raw_doc)
            except Exception as e:
                raise HTTPException(
                    400,
                    f"Document '{raw_doc.get('source_id', '?')}' invalid for chunked_text: {e}",
                )
            file_id = f'{file_id_prefix_for(provider)}{doc.source_id}'
            result = await _process_chunked_text_document(
                request=request,
                knowledge_id=None,  # no KB link
                provider=provider,
                doc=doc,
                user_id=user.id,
                # The direct upload already stored the source bytes at file.path;
                # warren re-shipping them (original_files) would be a redundant
                # Storage round-trip inside the /ingest critical path — drop it.
                # None is a no-op when warren ships nothing, so this is always safe.
                original_file=None,
                collection_name=f'file-{file_id}',  # per-file cache collection
                add=False,  # native chat parity: create fresh, don't append
                skip_embed=bypass,  # BYPASS: store content only, no vectors
            )
            _maybe_persist_attachments(result=result, doc=doc, part_lookup=attachment_lookup)
            # The callback has landed — clear the pipeline bookkeeping so the
            # restart-safe reconciler stops tracking this file. Status is already
            # 'completed'/'error' from _process_chunked_text_document.
            await Files.update_file_metadata_by_id(file_id, {'pipeline_job_id': None, 'pipeline_submitted_at': None})
            # Emit the honest completion: the vectors just landed (or the embed
            # failed). Phase 1 suppressed _process_handler's submit-time emit to
            # 'processing', so THIS is the signal that ends the frontend's
            # loading state. The target='file' path is only ever reached for
            # direct chat attachments (provider owui_upload), so no cloud-sync
            # guard is needed here. emit_file_status swallows socket errors, so a
            # hiccup never fails ingestion.
            await emit_file_status(
                user_id=user.id,
                file_id=file_id,
                status='completed' if result['status'] in ('created', 'updated') else 'failed',
                error=result.get('error'),
                collection_name=f'file-{file_id}',
            )
            results.append(result)

        created = sum(1 for r in results if r['status'] == 'created')
        updated = sum(1 for r in results if r['status'] == 'updated')
        errors = sum(1 for r in results if r['status'] == 'error')
        return {
            'knowledge_id': None,
            'collection_source_id': collection.source_id,
            'provider': provider,
            'data_type': data_type,
            'target': 'file',
            'total': len(form_data.documents),
            'created': created,
            'updated': updated,
            'errors': errors,
            'documents': results,
        }

    # Find or create KB. For LoaderPrincipal callers (loader-worker pushing
    # the result of a cloud sync), ``collection.source_id`` is the existing
    # open-webui KB UUID — look that up directly so we don't double-create.
    # External push providers continue to use the meta.integration.source_id
    # lookup since they own KB lifecycle.
    knowledge = None
    if isinstance(principal, LoaderPrincipal):
        knowledge = await Knowledges.get_knowledge_by_id(collection.source_id)
    if knowledge is None:
        knowledge = await _find_kb_by_source_id(provider, collection.source_id)
    if not knowledge:
        knowledge = await _create_kb_for_provider(provider, provider_config, collection, user.id)
    elif not isinstance(principal, LoaderPrincipal):
        # Validate data_type consistency with existing KB (push providers only;
        # cloud-sync KBs found via direct ID lookup don't carry meta.integration).
        existing_data_type = (knowledge.meta or {}).get('integration', {}).get('data_type')
        if existing_data_type and existing_data_type != data_type:
            raise HTTPException(
                400,
                f"Collection '{collection.source_id}' was created with data_type '{existing_data_type}'. "
                f"Cannot push with data_type '{data_type}'.",
            )
        # Touch updated_at on the existing KB (KnowledgeForm carries no access_control
        # field; existing access_grants are preserved by passing access_grants=None,
        # which short-circuits the grants-update branch in update_knowledge_by_id).
        await Knowledges.update_knowledge_by_id(
            knowledge.id,
            KnowledgeForm(
                name=knowledge.name,
                description=knowledge.description,
                type=knowledge.type,
            ),
        )

    # Check file limit
    max_files = provider_config.get('max_files_per_kb', KNOWLEDGE_MAX_FILE_COUNT)
    current_files = await Knowledges.get_files_by_id(knowledge.id)
    existing_ids = {f.id for f in current_files} if current_files else set()
    _prefix = file_id_prefix_for(provider)
    new_doc_ids = {f'{_prefix}{doc.get("source_id", "")}' for doc in form_data.documents}
    net_new = len(new_doc_ids - existing_ids)
    # Guard on net_new > 0: a KB already at/over a (lowered) cap must still
    # accept updates to files it already holds — only requests that would *add*
    # files beyond the cap are rejected. Without this, a pure-update push to a
    # KB sitting above max_files (net_new == 0) 400s every sync.
    if net_new > 0 and len(existing_ids) + net_new > max_files:
        raise HTTPException(
            400,
            (
                f'Would exceed {max_files} file limit for KB {knowledge.id!r} '
                f'({knowledge.name!r}). Existing: {len(existing_ids)}, new in this request: '
                f"{net_new}. Raise the provider's max_files_per_kb or split the push into "
                'smaller batches.'
            ),
        )

    # Validate and dispatch based on data_type
    results = []
    created = updated = errors = 0

    if data_type == 'parsed_text':
        for raw_doc in form_data.documents:
            try:
                doc = ParsedTextDocument(**raw_doc)
            except Exception as e:
                raise HTTPException(
                    400,
                    f"Document '{raw_doc.get('source_id', '?')}' invalid for parsed_text: {e}",
                )
            _validate_custom_metadata(doc, provider_config)
            result = await _process_parsed_text_document(
                request=request,
                knowledge_id=knowledge.id,
                provider=provider,
                doc=doc,
                user_id=user.id,
                original_file=original_file_lookup.get(doc.source_id),
            )
            _maybe_persist_attachments(result=result, doc=doc, part_lookup=attachment_lookup)
            results.append(result)

    elif data_type == 'chunked_text':
        for raw_doc in form_data.documents:
            try:
                doc = ChunkedTextDocument(**raw_doc)
            except Exception as e:
                raise HTTPException(
                    400,
                    f"Document '{raw_doc.get('source_id', '?')}' invalid for chunked_text: {e}",
                )
            _validate_custom_metadata(doc, provider_config)
            result = await _process_chunked_text_document(
                request=request,
                knowledge_id=knowledge.id,
                provider=provider,
                doc=doc,
                user_id=user.id,
                original_file=original_file_lookup.get(doc.source_id),
            )
            _maybe_persist_attachments(result=result, doc=doc, part_lookup=attachment_lookup)
            results.append(result)

    elif data_type == 'full_documents':
        if not files:
            raise HTTPException(400, 'full_documents data_type requires uploaded files')

        # Build filename -> UploadFile lookup
        file_lookup = {f.filename: f for f in files}

        for raw_doc in form_data.documents:
            try:
                doc = FullDocument(**raw_doc)
            except Exception as e:
                raise HTTPException(
                    400,
                    f"Document '{raw_doc.get('source_id', '?')}' invalid for full_documents: {e}",
                )
            _validate_custom_metadata(doc, provider_config)

            upload = file_lookup.get(doc.filename)
            if not upload:
                raise HTTPException(
                    400,
                    f"No uploaded file matches document filename '{doc.filename}'. "
                    f'Available files: {list(file_lookup.keys())}',
                )

            result = await _process_full_document(
                request=request,
                knowledge_id=knowledge.id,
                provider=provider,
                doc=doc,
                upload_file=upload,
                user_id=user.id,
            )
            _maybe_persist_attachments(result=result, doc=doc, part_lookup=attachment_lookup)
            results.append(result)

        # Check for unmatched uploaded files
        doc_filenames = {raw_doc.get('filename') for raw_doc in form_data.documents}
        unmatched = set(file_lookup.keys()) - doc_filenames
        if unmatched:
            log.warning(f'Uploaded files without matching documents: {unmatched}')

    else:
        raise HTTPException(400, f'Unsupported data_type: {data_type}')

    for result in results:
        if result['status'] == 'created':
            created += 1
        elif result['status'] == 'updated':
            updated += 1
        elif result['status'] == 'error':
            errors += 1

        # Direct KB uploads routed through warren (provider owui_upload) need an
        # honest file:status once their vectors land here — Phase 1 suppressed
        # _process_handler's submit-time emit to 'processing'. Cloud-sync
        # providers (onedrive/confluence/google_drive/topdesk) are deliberately
        # skipped: they emit their own honest {provider}:file:added, and a
        # redundant file:status here would double-count uploadBatch.added in
        # KnowledgeBase.svelte (which listens to BOTH events).
        if provider == ACTING_PROVIDER:
            await emit_file_status(
                user_id=user.id,
                file_id=result['file_id'],
                status='completed' if result['status'] in ('created', 'updated') else 'failed',
                error=result.get('error'),
                collection_name=knowledge.id,
            )

    return {
        'knowledge_id': knowledge.id,
        'collection_source_id': collection.source_id,
        'provider': provider,
        'data_type': data_type,
        'total': len(form_data.documents),
        'created': created,
        'updated': updated,
        'errors': errors,
        'documents': results,
    }


def _require_loader(principal) -> LoaderPrincipal:
    """The warren cloud-sync routes are machine-only: the loader bearer is the
    trust signal. A human/cookie caller falls through as a plain ``UserModel``
    from :func:`get_integration_principal`; reject it with 403 so these
    staging/submit/poll endpoints are never reachable from a browser session."""
    if not isinstance(principal, LoaderPrincipal):
        raise HTTPException(status_code=403, detail='This endpoint requires the loader service credential')
    return principal


@router.post('/stage')
async def stage_file(
    request: Request,
    body: StageRequest,
    principal=Depends(get_integration_principal),
):
    """Find-or-create the ``File`` row for a cloud-synced file at its canonical
    S3 key and hand back a presigned PUT the loader-worker uses to upload the
    original bytes over plain HTTPS.

    The canonical key is the one wiring invariant: the PUT MUST target the same
    key ``File.path`` records and warren's later presigned GET (issued in
    ``submit_existing_file_to_pipeline``) reads. Both the key derivation and its
    inverse live in the storage provider (``get_object_path`` mirrors
    ``upload_file``), so the path is never string-assembled here."""
    principal = _require_loader(principal)
    if not request.app.state.config.DISTRIBUTED_DOC_PIPELINE_SYNC_ENABLED:
        raise HTTPException(
            status_code=403,
            detail='warren cloud-sync pipeline is disabled (DISTRIBUTED_DOC_PIPELINE_SYNC_ENABLED)',
        )
    provider = principal.provider_slug
    user_id = principal.user.id

    file_id = f'{file_id_prefix_for(provider)}{body.source_id}'
    object_name = f'{file_id}_{body.filename}'
    path = Storage.get_object_path(object_name)

    existing_file = await Files.get_file_by_id(file_id)
    if existing_file:
        # base_worker._create_stub_file_rows may have created a stub (path='')
        # for this file_id; promote it to the canonical path. Never overwrite a
        # populated path with a different one silently unless it actually
        # changed (idempotent re-stage is a no-op on the path).
        if path and path != (existing_file.path or ''):
            await Files.update_file_path_by_id(file_id, path)
        await Files.update_file_metadata_by_id(
            file_id,
            {'content_type': body.content_type, 'collection_name': body.knowledge_id},
        )
    else:
        file_form = FileForm(
            id=file_id,
            filename=body.filename,
            path=path,
            meta={
                'name': body.filename,
                'content_type': body.content_type,
                'collection_name': body.knowledge_id,
                'source': provider,
                'source_id': body.source_id,
            },
        )
        await Files.insert_new_file(user_id, file_form)
        await Knowledges.add_file_to_knowledge_by_id(body.knowledge_id, file_id, user_id)

    ttl = request.app.state.config.PIPELINE_PRESIGN_TTL_SECONDS
    presigned_put_url = await run_in_threadpool(Storage.get_presigned_put_url, path, ttl, body.content_type)

    return {'file_id': file_id, 'presigned_put_url': presigned_put_url}


@router.post('/submit')
async def submit_file(
    request: Request,
    body: SubmitRequest,
    principal=Depends(get_integration_principal),
):
    """Submit an already-staged ``File`` to warren via the shared
    ``submit_existing_file_to_pipeline`` body (presign GET + submit job + link +
    mark 'processing'). The acting user is the loader-resolved principal user."""
    principal = _require_loader(principal)
    if not request.app.state.config.DISTRIBUTED_DOC_PIPELINE_SYNC_ENABLED:
        raise HTTPException(
            status_code=403,
            detail='warren cloud-sync pipeline is disabled (DISTRIBUTED_DOC_PIPELINE_SYNC_ENABLED)',
        )

    file = await Files.get_file_by_id(body.file_id)
    if not file:
        raise HTTPException(status_code=404, detail=f"file '{body.file_id}' not found")

    result = await submit_existing_file_to_pipeline(request, file, body.knowledge_id, principal.user)
    return {'pipeline_job_id': result['pipeline_job_id']}


@router.get('/file-status/{file_id}')
async def get_file_status(
    request: Request,
    file_id: str,
    principal=Depends(get_integration_principal),
):
    """Return the File's current processing status for the loader poll
    (``processing`` → ``completed`` / ``error``). Status is dual-written by
    ``Files.set_status`` into both meta and data; meta is the cheap read."""
    _require_loader(principal)

    file = await Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail=f"file '{file_id}' not found")

    status = (file.meta or {}).get('status') or (file.data or {}).get('status')
    return {'status': status}


@router.delete('/collections/{source_id}')
async def delete_collection(
    request: Request,
    source_id: str,
    principal=Depends(get_integration_principal),
):
    if isinstance(principal, LoaderPrincipal):
        provider = principal.provider_slug
        providers = request.app.state.config.INTEGRATION_PROVIDERS or {}
        if provider not in providers:
            raise HTTPException(
                status_code=403,
                detail=f"Integration provider '{provider}' is not registered",
            )
    else:
        provider, _ = get_integration_provider(request, principal)

    knowledge = await _find_kb_by_source_id(provider, source_id)
    if not knowledge:
        raise HTTPException(404, f"Collection '{source_id}' not found for provider '{provider}'")

    if knowledge.type != provider:
        raise HTTPException(403, 'Cannot delete collections belonging to another provider')

    # Remove all files and vector data
    current_files = await Knowledges.get_files_by_id(knowledge.id)
    file_ids = [f.id for f in current_files] if current_files else []
    for file_id in file_ids:
        try:
            await ASYNC_VECTOR_DB_CLIENT.delete(
                collection_name=knowledge.id,
                filter={'file_id': file_id},
            )
        except Exception:
            pass
        await Files.delete_file_by_id(file_id)

    await Knowledges.soft_delete_by_id(knowledge.id)

    return {'status': 'deleted', 'source_id': source_id, 'provider': provider}


@router.delete('/collections/{source_id}/documents/{document_source_id}')
async def delete_document(
    request: Request,
    source_id: str,
    document_source_id: str,
    user=Depends(get_verified_user),
):
    provider, _ = get_integration_provider(request, user)

    knowledge = await _find_kb_by_source_id(provider, source_id)
    if not knowledge:
        raise HTTPException(404, f"Collection '{source_id}' not found for provider '{provider}'")

    if knowledge.type != provider:
        raise HTTPException(403, "Cannot delete documents from another provider's collection")

    file_id = f'{file_id_prefix_for(provider)}{document_source_id}'
    file = await Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(404, f"Document '{document_source_id}' not found")

    try:
        await ASYNC_VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id,
            filter={'file_id': file_id},
        )
    except Exception:
        pass

    await Knowledges.remove_file_from_knowledge_by_id(knowledge.id, file_id)
    await Files.delete_file_by_id(file_id)

    return {
        'status': 'deleted',
        'source_id': source_id,
        'document_source_id': document_source_id,
        'provider': provider,
    }


@router.get('/openapi.json')
def get_integration_openapi(request: Request, user=Depends(get_verified_user)):
    """Return OpenAPI spec scoped to integration endpoints only."""
    full_spec = request.app.openapi()

    # Filter paths to only integration endpoints (exclude this endpoint itself)
    integration_prefix = '/api/v1/integrations'
    filtered_paths = {
        path: ops
        for path, ops in full_spec.get('paths', {}).items()
        if path.startswith(integration_prefix) and path != f'{integration_prefix}/openapi.json'
    }

    # Build scoped spec
    scoped_spec = {
        'openapi': full_spec.get('openapi', '3.1.0'),
        'info': {
            'title': 'Open WebUI — Integration API',
            'version': full_spec.get('info', {}).get('version', '1.0.0'),
            'description': 'API specification for the Open WebUI push integration endpoints.',
        },
        'paths': filtered_paths,
    }

    # Include only referenced schemas
    all_schemas = full_spec.get('components', {}).get('schemas', {})
    if all_schemas:

        def _collect_refs(obj, refs):
            if isinstance(obj, dict):
                if '$ref' in obj:
                    ref = obj['$ref']
                    if ref.startswith('#/components/schemas/'):
                        refs.add(ref.split('/')[-1])
                for v in obj.values():
                    _collect_refs(v, refs)
            elif isinstance(obj, list):
                for item in obj:
                    _collect_refs(item, refs)

        refs = set()
        _collect_refs(filtered_paths, refs)

        # Collect transitive refs from the schemas themselves
        changed = True
        while changed:
            changed = False
            for name in list(refs):
                if name in all_schemas:
                    before = len(refs)
                    _collect_refs(all_schemas[name], refs)
                    if len(refs) > before:
                        changed = True

        if refs:
            scoped_spec['components'] = {
                'schemas': {name: schema for name, schema in all_schemas.items() if name in refs}
            }

    return scoped_spec
