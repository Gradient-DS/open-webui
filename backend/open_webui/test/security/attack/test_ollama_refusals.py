"""A discovered backend model never bypasses OWUI model authorization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from . import crossuser, seeds

ROUTES = [
    entry['route'] for entry in seeds.SURFACE['field'] if entry.get('json', {}).get('model') == '{upstream_model}'
]


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ROUTES)
async def test_indexed_ollama_proxies_refuse_unregistered_models_for_all_actors(monkeypatch, route):
    from open_webui.routers import ollama
    from open_webui.utils.auth import get_verified_user

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    monkeypatch.setattr(ollama.Config, 'get', AsyncMock(return_value=True))
    monkeypatch.setattr(ollama.Models, 'get_model_by_id', AsyncMock(return_value=None))
    upstream = AsyncMock(side_effect=AssertionError('An unregistered model must never reach a backend'))
    monkeypatch.setattr(ollama, 'send_request', upstream)
    app = FastAPI()
    app.include_router(ollama.router, prefix='/ollama')
    method, path = route.split(' ', 1)
    path = path.replace('{url_idx}', '0')
    declaration = crossuser.expected_refusals()[route]
    assert declaration['all_actors'] and route in seeds.ordinary_targets()
    entry = next(item for item in seeds.SURFACE['field'] if item['route'] == route)
    context = seeds.SeedContext(None, None, seeds.SURFACE, values={'upstream_model': 'fictional-upstream-model'})
    parameters = seeds.Resolved(fields=seeds.resolve_fields(context, entries=[entry]))
    request = crossuser._benign(route, parameters, seeds.SPEC)
    assert request['json']['model'] == 'fictional-upstream-model'
    required_inputs = {
        'POST /ollama/api/embed/{url_idx}': 'input',
        'POST /ollama/api/embeddings/{url_idx}': 'prompt',
        'POST /ollama/api/generate/{url_idx}': 'prompt',
        'POST /ollama/v1/responses/{url_idx}': 'input',
    }
    if route in required_inputs:
        assert request['json'][required_inputs[route]].startswith('Fictional ')
        assert '{token}' not in request['json'][required_inputs[route]]
    assert declaration['status'] == 403 and declaration['json'] == {'detail': 'Model not found'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as client:
        for actor in ('admin', 'user', 'intruder'):
            app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(
                id=actor, role='admin' if actor == 'admin' else 'user'
            )
            response = await client.request(method, path, **request)
            assert crossuser.refused_as_expected(declaration, response.status_code, response.text), response.text
    upstream.assert_not_awaited()
