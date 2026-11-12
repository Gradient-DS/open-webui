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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as client:
        for actor in ('owner', 'member', 'intruder'):
            app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id=actor, role='user')
            response = await client.request(
                method,
                path,
                json={
                    'model': 'fictional-upstream-model',
                    'messages': [],
                    'prompt': 'Fictional probe.',
                    'input': 'Fictional probe.',
                    'max_tokens': 1,
                    'stream': False,
                },
            )
            assert crossuser.refused_as_expected(declaration, response.status_code, response.text), response.text
    upstream.assert_not_awaited()
