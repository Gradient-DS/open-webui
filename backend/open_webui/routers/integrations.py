import hashlib
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from langchain_core.documents import Document
from pydantic import BaseModel

from open_webui.config import KNOWLEDGE_MAX_FILE_COUNT
from open_webui.models.files import FileForm, Files
from open_webui.models.knowledge import KnowledgeForm, Knowledges
from open_webui.retrieval.loaders.main import Loader
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.routers.retrieval import save_docs_to_vector_db
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_verified_user

router = APIRouter()
log = logging.getLogger(__name__)


# --- Pydantic Models ---


VALID_DATA_TYPES = {'parsed_text', 'chunked_text', 'full_documents'}


class IngestCollection(BaseModel):
    source_id: str
    name: str
    description: str = ''
    data_type: str = 'parsed_text'
    language: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}
    # None = public, {} = private (default), {"read": {"group_ids": [], "user_ids": []}, ...} = custom
    access_control: Optional[dict] = {}


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


class ParsedTextDocument(IngestDocumentBase):
    text: str


class ChunkedTextDocument(IngestDocumentBase):
    chunks: list[str]


class FullDocument(IngestDocumentBase):
    pass


class IngestForm(BaseModel):
    collection: IngestCollection
    documents: list[dict]


# --- Helper Functions ---


def get_integration_provider(request: Request, user) -> tuple[str, dict]:
    """Resolve the integration provider from the authenticated service account."""
    provider_slug = (user.info or {}).get('integration_provider')
    if not provider_slug:
        raise HTTPException(
            status_code=403,
            detail='This account is not configured as an integration service account',
        )
    providers = request.app.state.config.INTEGRATION_PROVIDERS
    if not providers:
        raise HTTPException(
            status_code=403,
            detail=f"Integration provider '{provider_slug}' is not registered",
        )
    provider_config = providers.get(provider_slug)
    if not provider_config:
        raise HTTPException(
            status_code=403,
            detail=f"Integration provider '{provider_slug}' is not registered",
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


async def _create_or_update_file_record(
    file_id: str,
    doc: IngestDocumentBase,
    content_text: str,
    file_path: str,
    provider: str,
    knowledge_id: str,
    user_id: str,
) -> str:
    """Create or update a File record. Returns 'created' or 'updated'."""
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

    existing_file = await Files.get_file_by_id(file_id)
    if existing_file:
        await Files.update_file_metadata_by_id(file_id, meta)
        await Files.update_file_data_by_id(file_id, {'content': content_text})
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
        await Knowledges.add_file_to_knowledge_by_id(knowledge_id, file_id, user_id)
        return 'created'


def _delete_old_vectors(knowledge_id: str, file_id: str):
    """Delete existing vectors for a file (idempotent update)."""
    try:
        VECTOR_DB_CLIENT.delete(
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
) -> dict:
    """Process a parsed_text document: create file record, chunk, embed, store."""
    file_id = f'{provider}-{doc.source_id}'

    status = await _create_or_update_file_record(
        file_id=file_id,
        doc=doc,
        content_text=doc.text,
        file_path='',
        provider=provider,
        knowledge_id=knowledge_id,
        user_id=user_id,
    )

    if status == 'updated':
        _delete_old_vectors(knowledge_id, file_id)

    text_hash = hashlib.sha256(doc.text.encode()).hexdigest()
    lc_doc = Document(
        page_content=doc.text,
        metadata=_build_base_metadata(doc, file_id, provider, user_id),
    )

    try:
        save_docs_to_vector_db(
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
        await Files.update_file_data_by_id(file_id, {'status': 'completed'})
    except Exception as e:
        log.exception(f'Failed to store document {doc.source_id} in vector DB')
        await Files.update_file_data_by_id(file_id, {'status': 'error', 'error': str(e)})
        return {
            'source_id': doc.source_id,
            'file_id': file_id,
            'status': 'error',
            'error': str(e),
        }

    return {'source_id': doc.source_id, 'file_id': file_id, 'status': status}


async def _process_chunked_text_document(
    request: Request,
    knowledge_id: str,
    provider: str,
    doc: ChunkedTextDocument,
    user_id: str,
) -> dict:
    """Process a chunked_text document: create file record, embed pre-chunked text, store."""
    file_id = f'{provider}-{doc.source_id}'
    joined_text = '\n\n'.join(doc.chunks)

    status = await _create_or_update_file_record(
        file_id=file_id,
        doc=doc,
        content_text=joined_text,
        file_path='',
        provider=provider,
        knowledge_id=knowledge_id,
        user_id=user_id,
    )

    if status == 'updated':
        _delete_old_vectors(knowledge_id, file_id)

    text_hash = hashlib.sha256(joined_text.encode()).hexdigest()
    base_metadata = _build_base_metadata(doc, file_id, provider, user_id)

    lc_docs = [Document(page_content=chunk, metadata=base_metadata) for chunk in doc.chunks]

    try:
        save_docs_to_vector_db(
            request=request,
            docs=lc_docs,
            collection_name=knowledge_id,
            metadata={
                'file_id': file_id,
                'name': doc.title or doc.filename,
                'hash': text_hash,
            },
            add=True,
            split=False,
        )
        await Files.update_file_data_by_id(file_id, {'status': 'completed'})
    except Exception as e:
        log.exception(f'Failed to store chunked document {doc.source_id} in vector DB')
        await Files.update_file_data_by_id(file_id, {'status': 'error', 'error': str(e)})
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
    file_id = f'{provider}-{doc.source_id}'

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
        _delete_old_vectors(knowledge_id, file_id)

    text_hash = hashlib.sha256(extracted_text.encode()).hexdigest()
    base_metadata = _build_base_metadata(doc, file_id, provider, user_id)

    # Add file-level metadata from extraction
    for d in extracted_docs:
        d.metadata.update(base_metadata)

    try:
        save_docs_to_vector_db(
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
        await Files.update_file_data_by_id(file_id, {'status': 'completed'})
    except Exception as e:
        log.exception(f'Failed to store full document {doc.source_id} in vector DB')
        await Files.update_file_data_by_id(file_id, {'status': 'error', 'error': str(e)})
        return {
            'source_id': doc.source_id,
            'file_id': file_id,
            'status': 'error',
            'error': str(e),
        }

    return {'source_id': doc.source_id, 'file_id': file_id, 'status': status}


# --- Endpoints ---


@router.post('/ingest')
async def ingest_documents(
    request: Request,
    data: str = Form(...),
    files: Optional[list[UploadFile]] = File(None),
    user=Depends(get_verified_user),
):
    # Parse JSON from form field
    try:
        form_data = IngestForm(**json.loads(data))
    except (json.JSONDecodeError, Exception) as e:
        raise HTTPException(400, f"Invalid JSON in 'data' field: {e}")

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

    # Find or create KB
    knowledge = await _find_kb_by_source_id(provider, collection.source_id)
    if not knowledge:
        knowledge = await _create_kb_for_provider(provider, provider_config, collection, user.id)
    else:
        # Validate data_type consistency with existing KB
        existing_data_type = (knowledge.meta or {}).get('integration', {}).get('data_type')
        if existing_data_type and existing_data_type != data_type:
            raise HTTPException(
                400,
                f"Collection '{collection.source_id}' was created with data_type '{existing_data_type}'. "
                f"Cannot push with data_type '{data_type}'.",
            )
        # Update access_control on existing KB (defaults to {} / private if not specified)
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
    new_doc_ids = {f'{provider}-{doc.get("source_id", "")}' for doc in form_data.documents}
    net_new = len(new_doc_ids - existing_ids)
    if len(existing_ids) + net_new > max_files:
        raise HTTPException(400, f'Would exceed {max_files} file limit for this knowledge base.')

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
            )
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
            )
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


@router.delete('/collections/{source_id}')
async def delete_collection(
    request: Request,
    source_id: str,
    user=Depends(get_verified_user),
):
    provider, _ = get_integration_provider(request, user)

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
            VECTOR_DB_CLIENT.delete(
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

    file_id = f'{provider}-{document_source_id}'
    file = await Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(404, f"Document '{document_source_id}' not found")

    try:
        VECTOR_DB_CLIENT.delete(
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
