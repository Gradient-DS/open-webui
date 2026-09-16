from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from open_webui.models.config import Config
from open_webui.models.files import FileForm, Files
from open_webui.models.knowledge import Knowledges
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.services.sync.provider import file_id_prefix_for
from open_webui.soev import ingest
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_verified_user
from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal
from pydantic import BaseModel

router = APIRouter()

# Presigned PUT lifetime the cloud-sync loader gets for a staged file.
STAGE_PRESIGN_TTL_SECONDS = 3600


# --- Pydantic Models ---


# Cloud-sync daemon bridge: staging, soev submission, and status polling.


class StageRequest(BaseModel):
    knowledge_id: str
    source_id: str
    filename: str
    content_type: str = 'application/octet-stream'
    # Sync-daemon additive fields — absent for loader-worker callers, so
    # omitting them is byte-identical to the pre-daemon behavior (R12).
    # file_hash stages the provider change token as meta.pending_cloud_hash
    # (R4 staged-promote); directory_id places the KB link in an upstream
    # knowledge_directory row (D-8); relative_path + source_item_id stamp the
    # interim path identity the fork's tree UI renders from (the rollup keys
    # on knowledge_file.source_item_id; relative_path is source-relative,
    # convention '{dir/path}/{filename}').
    file_hash: Optional[str] = None
    directory_id: Optional[str] = None
    relative_path: Optional[str] = None
    source_item_id: Optional[str] = None


class SubmitRequest(BaseModel):
    file_id: str
    knowledge_id: str


# --- Helper Functions ---


async def get_integration_provider(request: Request, user) -> tuple[str, dict]:
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
    providers = await Config.get('integrations.providers')
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


async def _find_kb_by_source_id(provider: str, source_id: str):
    """Find a knowledge base by provider slug + external source_id."""
    kbs = await Knowledges.get_knowledge_bases_by_type(provider)
    for kb in kbs:
        meta = kb.meta or {}
        if meta.get('integration', {}).get('source_id') == source_id:
            return kb
    return None


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
    """Stage a cloud file at its canonical storage path and return a presigned PUT.

    The ingest helper reads the original bytes from this same path.
    """
    principal = _require_loader(principal)
    if not await Config.get('rag.distributed_doc_pipeline_sync_enabled'):
        raise HTTPException(
            status_code=403,
            detail='warren cloud-sync pipeline is disabled (DISTRIBUTED_DOC_PIPELINE_SYNC_ENABLED)',
        )
    provider = principal.provider_slug
    user_id = principal.user.id

    file_id = f'{file_id_prefix_for(provider)}{body.source_id}'
    object_name = f'{file_id}_{body.filename}'
    path = Storage.get_object_path(object_name)

    if body.directory_id:
        directory = await Knowledges.get_directory_by_id(body.directory_id)
        if not directory or directory.knowledge_id != body.knowledge_id:
            raise HTTPException(
                status_code=400,
                detail=f"directory '{body.directory_id}' does not belong to knowledge base '{body.knowledge_id}'",
            )

    existing_file = await Files.get_file_by_id(file_id)
    if existing_file:
        # base_worker._create_stub_file_rows may have created a stub (path='')
        # for this file_id; promote it to the canonical path. Never overwrite a
        # populated path with a different one silently unless it actually
        # changed (idempotent re-stage is a no-op on the path).
        if path and path != (existing_file.path or ''):
            await Files.update_file_path_by_id(file_id, path)
        if body.filename != existing_file.filename:
            # Provider-side rename: refresh the display name so the diff's
            # delete+add pair for a rename converges on one honest row.
            await Files.update_file_name_by_id(file_id, body.filename)
        meta_updates = {'content_type': body.content_type, 'collection_name': body.knowledge_id}
        if body.file_hash:
            # Stage the provider hash until processing completes.
            meta_updates['pending_cloud_hash'] = body.file_hash
        if body.relative_path is not None:
            # D-8 bridge: the sync-daemon stamps the interim path identity it
            # owns (R7) — the fork's tree UI renders from relative_path +
            # source_item_id until the P2-8 knowledge_directory convergence.
            # The loader-worker never sends these fields, so worker-stamped
            # identity is untouched during the D-10 co-existence window.
            meta_updates['relative_path'] = body.relative_path
        if body.source_item_id is not None:
            meta_updates['source_item_id'] = body.source_item_id
        await Files.update_file_metadata_by_id(file_id, meta_updates)
        if body.relative_path is not None or body.source_item_id is not None:
            # Idempotent upsert: links a shared row into this KB when needed
            # (R6) and refreshes the denormalized path columns from the meta
            # just written (self-heal during an active sync).
            await Knowledges.add_file_to_knowledge_by_id(body.knowledge_id, file_id, user_id)
    else:
        meta = {
            'name': body.filename,
            'content_type': body.content_type,
            'collection_name': body.knowledge_id,
            'source': provider,
            'source_id': body.source_id,
        }
        if body.file_hash:
            meta['pending_cloud_hash'] = body.file_hash
        if body.relative_path is not None:
            meta['relative_path'] = body.relative_path
        if body.source_item_id is not None:
            meta['source_item_id'] = body.source_item_id
        file_form = FileForm(
            id=file_id,
            filename=body.filename,
            path=path,
            meta=meta,
        )
        await Files.insert_new_file(user_id, file_form)
        await Knowledges.add_file_to_knowledge_by_id(body.knowledge_id, file_id, user_id)

    if body.directory_id:
        # Directory placement needs a KnowledgeFile join row: a shared File
        # row reached here for a KB it isn't linked to yet (R6 net-new-to-
        # this-KB) gets its link now instead of waiting for /submit.
        if not await Knowledges.has_file(body.knowledge_id, file_id):
            await Knowledges.add_file_to_knowledge_by_id(body.knowledge_id, file_id, user_id)
        await Knowledges.move_file_to_directory(body.knowledge_id, file_id, body.directory_id)

    presigned_put_url = await run_in_threadpool(
        Storage.get_presigned_put_url, path, STAGE_PRESIGN_TTL_SECONDS, body.content_type
    )

    return {'file_id': file_id, 'presigned_put_url': presigned_put_url}


@router.post('/submit')
async def submit_file(
    request: Request,
    body: SubmitRequest,
    principal=Depends(get_integration_principal),
):
    """Submit a staged file as the loader-resolved user and return the daemon job id."""
    principal = _require_loader(principal)
    if not await Config.get('rag.distributed_doc_pipeline_sync_enabled'):
        raise HTTPException(
            status_code=403,
            detail='warren cloud-sync pipeline is disabled (DISTRIBUTED_DOC_PIPELINE_SYNC_ENABLED)',
        )

    file = await Files.get_file_by_id(body.file_id)
    if not file:
        raise HTTPException(status_code=404, detail=f"file '{body.file_id}' not found")

    await Knowledges.add_file_to_knowledge_by_id(body.knowledge_id, file.id, principal.user.id)
    job_id = await ingest.submit(file, collection_key=body.knowledge_id, user_id=principal.user.id)
    return {'pipeline_job_id': job_id}


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
        providers = await Config.get('integrations.providers') or {}
        if provider not in providers:
            raise HTTPException(
                status_code=403,
                detail=f"Integration provider '{provider}' is not registered",
            )
    else:
        provider, _ = await get_integration_provider(request, principal)

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
    provider, _ = await get_integration_provider(request, user)

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
