"""Retired product routes stay absent without editing upstream routers."""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from open_webui.soev.product_surface import product_router

RETIRED = {
    'POST /api/v1/tasks/image_prompt/completions',
    'POST /api/embeddings',
    'POST /api/v1/embeddings',
    'POST /api/v1/retrieval/process/url',
    'POST /api/v1/retrieval/process/web',
    'POST /api/v1/retrieval/process/youtube',
    'POST /api/v1/retrieval/process/web/search',
}


def test_selective_mount_preserves_the_source_router_and_live_dependencies():
    from fastapi import Depends, HTTPException

    def authorize():
        raise HTTPException(403, 'Fictional owner required')

    router = APIRouter(dependencies=[Depends(authorize)])
    router.add_api_route('/kept', lambda: {'ok': True}, methods=['POST'])
    router.add_api_route('/retired', lambda: {'ok': True}, methods=['POST'])
    app = FastAPI()
    app.include_router(product_router(router, exclude={'/retired'}), prefix='/product')
    client = TestClient(app)
    assert client.post('/product/retired').status_code == 404
    assert client.post('/product/kept').status_code == 403
    assert {r.path for r in router.routes} == {'/kept', '/retired'}


def test_retired_routes_are_absent_from_the_actual_app_and_committed_inventory():
    import json
    from pathlib import Path

    from open_webui.main import app

    actual = {f'{method} {route.path}' for route in app.routes for method in getattr(route, 'methods', [])}
    spec = json.loads((Path(__file__).resolve().parents[4] / 'security/openapi.json').read_text())
    committed = {f'{method.upper()} {path}' for path, item in spec['paths'].items() for method in item}
    assert not any(route.split(' ', 1)[1].startswith(('/ollama', '/openai')) for route in actual)
    assert not actual & RETIRED
    assert not committed & RETIRED
    assert {
        'POST /api/v1/retrieval/process/file',
        'POST /api/v1/retrieval/process/files/batch',
        'POST /api/v1/automations/create',
        'GET /api/v1/calendars/',
        'POST /api/v1/audio/speech',
        'GET /api/v1/skills/',
    } <= actual & committed
