"""v2 mode takes its models and default from soev-api; v1 mode is unchanged."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui import env
from open_webui.models.models import ModelModel
from open_webui.soev import model_catalog
from open_webui.test.soev.fake_api import FakeSoevApi
from open_webui.utils import models as models_utils

GLM = {
    'id': 'glm-5-3',
    'label': 'GLM 5.3',
    'description': {'nl': 'Sterk algemeen model', 'en': 'Strong general model'},
    'vendor': 'Zhipu AI',
    'origin': 'CN',
    'open_weights': True,
    'license': 'MIT',
    'hosting': {'hosted_by': 'Nebul', 'region': 'NL', 'retention': 'zero-retention'},
    'capabilities': {'tools': 'supported', 'vision': 'unsupported', 'audio': 'unknown', 'reasoning': 'supported'},
    'structured_output': 'native',
    'context_window': 1048576,
    'max_output_tokens': None,
    'lifecycle': 'active',
    'replaced_by': None,
    'default': True,
}
GEMMA = {
    **GLM,
    'id': 'gemma-4-31b',
    'label': 'gemma-4-31b',
    'description': None,
    'vendor': None,
    'origin': None,
    'open_weights': None,
    'license': None,
    'hosting': None,
    'capabilities': {'tools': 'unknown', 'vision': 'supported', 'audio': 'unknown', 'reasoning': 'unknown'},
    'lifecycle': 'superseded',
    'replaced_by': 'glm-5-3',
    'default': False,
}
CONNECTION_MODEL = {'id': 'zai-org/GLM-5.3', 'name': 'zai-org/GLM-5.3', 'owned_by': 'openai', 'urlIdx': 0}


@pytest.fixture
def v2(chat_http: FakeSoevApi, monkeypatch: pytest.MonkeyPatch) -> FakeSoevApi:
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(env, 'AGENT_API_RUNTIME', 'v2')
    monkeypatch.setitem(model_catalog.CATALOG_CACHE, 'expires_at', 0.0)
    chat_http.models = [GLM, GEMMA]
    return chat_http


@pytest.fixture
def v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(env, 'AGENT_API_RUNTIME', 'v1')


@pytest.fixture
def connections(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fetch = AsyncMock(return_value=[CONNECTION_MODEL])
    monkeypatch.setattr(models_utils, 'fetch_openai_models', fetch)
    monkeypatch.setattr(models_utils, 'get_function_models', AsyncMock(return_value=[]))
    monkeypatch.setattr(
        models_utils.Config, 'get_many', AsyncMock(return_value={'openai.enable': True, 'ollama.enable': False})
    )
    return fetch


def model_row(id: str, base_model_id: str | None = None, **meta) -> ModelModel:
    return ModelModel(
        id=id,
        name=f'Admin {id}',
        user_id='admin',
        base_model_id=base_model_id,
        params={},
        meta=meta,
        is_active=True,
        created_at=1,
        updated_at=1,
    )


def app_request(**state) -> SimpleNamespace:
    defaults = {'MODELS': {}, 'BASE_MODELS': [], 'redis': None}
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**{**defaults, **state})), state=SimpleNamespace())


@pytest.fixture
def all_models(monkeypatch: pytest.MonkeyPatch, connections: AsyncMock):
    """get_all_models with the model table and plugins stubbed out."""
    rows: list[ModelModel] = []
    monkeypatch.setattr(models_utils, 'ENABLE_PLUGINS', False)
    monkeypatch.setattr(models_utils.Models, 'get_all_models', AsyncMock(side_effect=lambda: rows))
    monkeypatch.setattr(models_utils.Functions, 'get_functions_by_ids', AsyncMock(return_value=[]))
    monkeypatch.setattr(models_utils.Functions, 'get_function_valves_by_ids', AsyncMock(return_value={}))
    monkeypatch.setattr(models_utils, 'get_functions_cache', lambda request: {})

    async def run() -> dict[str, dict]:
        return {model['id']: model for model in await models_utils.get_all_models(app_request())}

    return rows, run


@pytest.mark.asyncio
async def test_v2_base_models_are_the_catalog_mapping(v2: FakeSoevApi, connections: AsyncMock) -> None:
    models = await models_utils.get_all_base_models(app_request())
    connections.assert_not_awaited()
    assert [model['id'] for model in models] == ['glm-5-3', 'gemma-4-31b']
    glm, gemma = models
    assert glm['name'] == 'GLM 5.3' and glm['owned_by'] == 'soev'
    meta = glm['info']['meta']
    assert meta['description'] == 'Strong general model'
    assert meta['capabilities'] == {'vision': False}
    assert meta['soev']['description'] == GLM['description']
    assert meta['soev']['hosting'] == GLM['hosting']
    assert meta['soev']['capabilities'] == GLM['capabilities']
    assert {key: meta['soev'][key] for key in ('vendor', 'origin', 'open_weights', 'license', 'lifecycle')} == {
        'vendor': 'Zhipu AI',
        'origin': 'CN',
        'open_weights': True,
        'license': 'MIT',
        'lifecycle': 'active',
    }
    assert gemma['name'] == 'gemma-4-31b'
    assert gemma['info']['meta']['description'] is None
    assert gemma['info']['meta']['capabilities'] == {'vision': True}
    assert gemma['info']['meta']['soev']['replaced_by'] == 'glm-5-3'


@pytest.mark.asyncio
async def test_unknown_vision_counts_as_off(v2: FakeSoevApi) -> None:
    v2.models = [{**GEMMA, 'capabilities': {**GEMMA['capabilities'], 'vision': 'unknown'}}]
    [model] = await model_catalog.base_models()
    assert model['info']['meta']['capabilities'] == {'vision': False}


@pytest.mark.asyncio
async def test_catalog_is_cached_per_process(v2: FakeSoevApi) -> None:
    await model_catalog.catalog()
    await model_catalog.catalog()
    assert [request.url.path for request in v2.requests] == ['/v1/models']


@pytest.mark.asyncio
async def test_unreadable_catalog_is_empty_and_not_cached(v2: FakeSoevApi) -> None:
    v2.failures.append(503)
    assert await model_catalog.catalog() == []
    assert [entry['id'] for entry in await model_catalog.catalog()] == ['glm-5-3', 'gemma-4-31b']


@pytest.mark.asyncio
async def test_v1_base_models_still_come_from_connections(v1, connections: AsyncMock) -> None:
    assert await models_utils.get_all_base_models(app_request()) == [CONNECTION_MODEL]


@pytest.mark.asyncio
async def test_catalog_meta_survives_admin_rows_and_assistants_keep_catalog_bases(v2: FakeSoevApi, all_models) -> None:
    rows, run = all_models
    rows.extend(
        [
            model_row('glm-5-3', description='Admin text', capabilities={'vision': True, 'citations': False}),
            model_row('helper', base_model_id='glm-5-3'),
        ]
    )
    models = await run()
    assert set(models) == {'glm-5-3', 'gemma-4-31b', 'helper'}
    meta = models['glm-5-3']['info']['meta']
    assert meta['description'] == 'Admin text'
    assert meta['capabilities'] == {'vision': False, 'citations': False}
    assert meta['soev']['vendor'] == 'Zhipu AI'
    assert models['helper']['owned_by'] == 'soev'
    assert models['helper']['info']['base_model_id'] == 'glm-5-3'
    # Each run hands out its own copy: no later mutation reaches the cached catalog.
    models['gemma-4-31b']['info']['meta']['soev']['vendor'] = 'mutated'
    assert (await run())['gemma-4-31b']['info']['meta']['soev']['vendor'] is None


@pytest.mark.asyncio
async def test_unconfigured_catalog_models_follow_base_model_access(v2: FakeSoevApi, all_models, monkeypatch) -> None:
    _, run = all_models
    models = list((await run()).values())
    monkeypatch.setattr(models_utils, 'BYPASS_MODEL_ACCESS_CONTROL', False)
    monkeypatch.setattr(models_utils, 'BYPASS_ADMIN_ACCESS_CONTROL', False)
    monkeypatch.setattr(models_utils.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(models_utils.AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value=set()))
    admin = SimpleNamespace(id='root', role='admin')
    user = SimpleNamespace(id='alice', role='user')
    assert [m['id'] for m in await models_utils.get_filtered_models(models, admin)] == ['glm-5-3', 'gemma-4-31b']
    assert await models_utils.get_filtered_models(models, user) == []


@pytest.mark.asyncio
async def test_v2_default_comes_from_the_catalog_and_ignores_ui_default_models(v2: FakeSoevApi) -> None:
    assert await model_catalog.default_models('zai-org/GLM-5.3') == 'glm-5-3'
    v2.models = [{**GLM, 'default': False}, GEMMA]
    model_catalog.CATALOG_CACHE['expires_at'] = 0.0
    assert await model_catalog.default_models('zai-org/GLM-5.3') is None


@pytest.mark.asyncio
async def test_v1_default_is_ui_default_models(v1) -> None:
    assert await model_catalog.default_models('zai-org/GLM-5.3') == 'zai-org/GLM-5.3'
