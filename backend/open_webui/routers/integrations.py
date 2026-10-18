from fastapi import APIRouter, Depends, Request
from open_webui.utils.auth import get_verified_user

router = APIRouter()


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
