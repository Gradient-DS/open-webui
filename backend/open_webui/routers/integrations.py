from fastapi import APIRouter, Depends, HTTPException, Request
from open_webui.models.config import Config
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.utils.auth import get_verified_user

router = APIRouter()


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


@router.delete('/collections/{source_id}')
async def delete_collection(
    request: Request,
    source_id: str,
    user=Depends(get_verified_user),
):
    provider, _ = await get_integration_provider(request, user)

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

    prefix = {'google_drive': 'googledrive-', 'owui_upload': ''}.get(provider, f'{provider}-')
    file_id = f'{prefix}{document_source_id}'
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
