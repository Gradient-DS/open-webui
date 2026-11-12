"""The platform sweep keeps refused operations in its HTTP coverage."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from . import crossuser, seeds
from .authorization_inventory import source_operations
from .test_seeds import LIVE_READS


def test_retired_fixture_reads_have_proof_in_runtime_and_contract():
    runtime = source_operations()
    retired = set(seeds.SURFACE['removed']['routes'])
    for template in LIVE_READS:
        route = f'GET {template}'
        if template not in seeds.SPEC['paths']:
            assert route in retired
            assert route not in runtime


def test_disabled_fixture_ids_stay_fresh_and_do_not_call_creation_routes(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    parameters = [entry for entry in seeds.SURFACE['parameter'] if 'v2_refused_value' in entry]
    assert {'tool', 'skill', 'skill_bundle', 'oauth_exchange_provider', 'reset_token'} <= {
        entry['key'] for entry in parameters
    }
    for parameter in parameters:
        values = {key: f'fictional-{key}' for key in seeds._dependencies(parameter)}
        ctx = seeds.SeedContext(None, None, seeds.SURFACE, values=values.copy())
        assert seeds.seed_enabled(parameter)
        first = seeds._seed_parameter(ctx, parameter)
        other = seeds.SeedContext(None, None, seeds.SURFACE, values=values.copy())
        assert first != seeds._seed_parameter(other, parameter)
        assert ctx.token in first


DISABLED = [
    f'{method.upper()} {path}'
    for path, operations in seeds.SPEC['paths'].items()
    if path.startswith(('/api/v1/tools/', '/api/v1/skills/'))
    for method in operations.keys() & seeds.METHODS
]


@pytest.mark.asyncio
@pytest.mark.parametrize('route', DISABLED)
async def test_disabled_feature_routes_refuse_before_lookup_for_every_actor(monkeypatch, route):
    from open_webui import config, env
    from open_webui.internal.db import get_async_session
    from open_webui.routers import skill_files, skills, tools
    from open_webui.soev import product_config
    from open_webui.utils.auth import get_verified_user

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(product_config, 'get', AsyncMock(return_value=False))
    lookup = AsyncMock(side_effect=AssertionError('Disabled features must not read resources'))
    monkeypatch.setattr(skills.Skills, 'get_skill_by_id', lookup)
    monkeypatch.setattr(tools.Tools, 'get_tool_by_id', lookup)
    app = FastAPI()
    app.include_router(skill_files.router, prefix='/api/v1/skills')
    app.include_router(skills.router, prefix='/api/v1/skills')
    app.include_router(tools.router, prefix='/api/v1/tools')
    app.dependency_overrides[get_async_session] = lambda: None
    declaration = crossuser.expected_refusals()[route]
    assert declaration['all_actors'] and route in seeds.ordinary_targets()
    method, template = route.split(' ', 1)
    path = re.sub(r'\{[^}]+\}', 'fictional-disabled-resource', template)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as client:
        for actor in ('owner', 'member', 'intruder'):
            app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id=actor, role='user')
            response = await client.request(method, path, json={})
            assert crossuser.refused_as_expected(declaration, response.status_code, response.text), response.text
    lookup.assert_not_awaited()


def test_workspace_model_routes_use_an_owned_assistant_and_keep_catalog_completions():
    entries = {entry['key']: entry for entry in seeds.SURFACE['fixture']}
    assistant = entries['assistant']
    assert assistant['via'] == 'POST /api/v1/models/create'
    assert assistant['body']['base_model_id'] == '{model}'
    assert assistant['depends_on'] == ['model']
    assert '{token}' in assistant['body']['id']
    fields = {entry['route']: entry for entry in seeds.SURFACE['field']}
    for route in ('GET /api/v1/models/model', 'GET /api/v1/models/model/sharing', 'POST /api/v1/models/model/toggle'):
        assert fields[route]['params'] == {'id': '{assistant}'}
    assert fields['POST /api/v1/tasks/title/completions']['v2']['json']['model'] == '{model}'
