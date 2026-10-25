"""v2 refuses custom-function routes and never loads installed code."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from open_webui import env
from open_webui.events import dispatch_event_functions
from open_webui.routers import functions
from open_webui.utils import filter as filters
from open_webui.utils import plugin
from open_webui.utils.auth import get_admin_user, get_verified_user


@pytest.mark.asyncio
async def test_v2_refuses_every_function_route(identity_config, monkeypatch):
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    app = FastAPI()
    app.include_router(functions.router)
    user = SimpleNamespace(id='fern', role='admin')
    app.dependency_overrides[get_admin_user] = lambda: user
    app.dependency_overrides[get_verified_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owui.invalid') as client:
        for route in functions.router.routes:
            path = route.path.replace('{id}', 'fictional-function')
            for method in route.methods:
                response = await client.request(method, path, json={})
                assert response.status_code == 403, (method, path, response.text)
                assert response.json()['detail'] == 'Custom functions are disabled under v2'


@pytest.mark.asyncio
async def test_v2_skips_saved_filters_events_and_cached_modules(identity_config, monkeypatch):
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(plugin, 'ENABLE_PLUGINS', True)
    load = AsyncMock(side_effect=AssertionError('Installed functions must not be read'))
    monkeypatch.setattr(functions.Functions, 'get_function_by_id', load)
    assert await filters.resolve_filter_pipeline(None, {'info': {'meta': {'filterIds': ['fictional']}}}) == ([], [])
    assert await filters.process_filter_functions(None, None, [object()], 'inlet', {'input': 'fictional'}, {}) == (
        {'input': 'fictional'},
        {},
    )
    await dispatch_event_functions(None, None)
    with pytest.raises(RuntimeError):
        await plugin.load_function_module_by_id('fictional')
    with pytest.raises(RuntimeError):
        await plugin.get_function_module_from_cache(None, 'fictional')
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_function_router_keeps_legacy_behavior_without_v2(identity_config, monkeypatch):
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    functions.require_custom_functions()
