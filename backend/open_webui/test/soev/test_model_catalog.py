"""Take models and the default from soev-api in v2 mode; keep Ollama outside v2."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui import env
from open_webui.models.models import ModelModel
from open_webui.soev import model_catalog
from open_webui.test.soev.fake_api import FakeSoevApi
from open_webui.utils import models as models_utils
from open_webui.utils.access_control import has_base_model_access

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
CONNECTION_MODEL = {'id': 'zai-org/GLM-5.3', 'name': 'zai-org/GLM-5.3', 'owned_by': 'ollama'}


@pytest.fixture
def v2(chat_http: FakeSoevApi, monkeypatch: pytest.MonkeyPatch, platform_capabilities) -> FakeSoevApi:
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setitem(model_catalog.CATALOG_CACHE, 'expires_at', 0.0)
    chat_http.models = [GLM, GEMMA]
    return chat_http


@pytest.fixture
def stock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)


@pytest.fixture
def connections(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fetch = AsyncMock(return_value=[CONNECTION_MODEL])
    monkeypatch.setattr(models_utils, 'fetch_ollama_models', fetch)
    monkeypatch.setattr(models_utils, 'get_function_models', AsyncMock(return_value=[]))
    monkeypatch.setattr(models_utils.Config, 'get_many', AsyncMock(return_value={'ollama.enable': True}))
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
    # The catalog's label is not used: every model goes by its provider/model id.
    assert glm['name'] == 'glm-5-3' and glm['owned_by'] == 'soev'
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
async def test_vision_needs_platform_image_admission(v2, platform_capabilities):
    platform_capabilities.discard('images')
    models = await model_catalog.base_models()
    assert all(model['info']['meta']['capabilities']['vision'] is False for model in models)
    assert models[1]['info']['meta']['soev']['capabilities']['vision'] == 'supported'
    platform_capabilities.add('images')
    models = await model_catalog.base_models()
    assert models[1]['info']['meta']['capabilities']['vision'] is True


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
async def test_stock_base_models_can_still_come_from_ollama(stock, connections: AsyncMock) -> None:
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
    # The row's name ('Admin glm-5-3', or a migrated v1 name) never replaces the id.
    assert models['glm-5-3']['name'] == 'glm-5-3'
    assert models['helper']['name'] == 'Admin helper'
    assert meta['description'] == 'Admin text'
    assert meta['capabilities'] == {'vision': False, 'citations': False}
    assert meta['soev']['vendor'] == 'Zhipu AI'
    assert models['helper']['owned_by'] == 'soev'
    assert models['helper']['info']['base_model_id'] == 'glm-5-3'
    # Each run hands out its own copy: no later mutation reaches the cached catalog.
    models['gemma-4-31b']['info']['meta']['soev']['vendor'] = 'mutated'
    assert (await run())['gemma-4-31b']['info']['meta']['soev']['vendor'] is None


@pytest.fixture
def access(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, ModelModel], set[str]]:
    """Access control on, with model rows and the caller's read grants by model id."""
    from open_webui import config

    rows: dict[str, ModelModel] = {}
    granted: set[str] = set()
    monkeypatch.setattr(models_utils, 'BYPASS_MODEL_ACCESS_CONTROL', False)
    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', False)
    monkeypatch.setattr(models_utils.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(models_utils.Models, 'get_model_by_id', AsyncMock(side_effect=lambda id, db=None: rows.get(id)))
    monkeypatch.setattr(
        models_utils.AccessGrants,
        'has_access',
        AsyncMock(side_effect=lambda **kwargs: kwargs['resource_id'] in granted),
    )
    monkeypatch.setattr(
        models_utils.AccessGrants,
        'get_accessible_resource_ids',
        AsyncMock(side_effect=lambda **kwargs: granted & set(kwargs['resource_ids'])),
    )
    return rows, granted


ADMIN = SimpleNamespace(id='root', role='admin')
USER = SimpleNamespace(id='alice', role='user')
PENDING = SimpleNamespace(id='bob', role='pending')


@pytest.mark.asyncio
@pytest.mark.parametrize('user', [ADMIN, USER], ids=['admin', 'user'])
async def test_v2_catalog_models_are_open_to_users_and_admins(v2: FakeSoevApi, all_models, access, user) -> None:
    _, run = all_models
    models = list((await run()).values())
    assert [m['id'] for m in await models_utils.get_filtered_models(models, user)] == ['glm-5-3', 'gemma-4-31b']
    for model in models:
        await models_utils.check_model_access(user, model)


@pytest.mark.asyncio
async def test_v2_catalog_models_stay_closed_to_pending_users(v2: FakeSoevApi, all_models, access) -> None:
    _, run = all_models
    models = list((await run()).values())
    with pytest.raises(Exception, match='Model not found'):
        await models_utils.check_model_access(PENDING, models[0])


@pytest.mark.asyncio
async def test_v2_admin_row_grants_do_not_restrict_a_catalog_model(v2: FakeSoevApi, all_models, access) -> None:
    rows, run = all_models
    rows.append(model_row('glm-5-3'))
    models = await run()
    access[0]['glm-5-3'] = rows[0]
    assert [m['id'] for m in await models_utils.get_filtered_models(list(models.values()), USER)] == [
        'glm-5-3',
        'gemma-4-31b',
    ]
    await models_utils.check_model_access(USER, models['glm-5-3'])
    with pytest.raises(Exception, match='Model not found'):
        await models_utils.check_model_access(PENDING, models['glm-5-3'])


@pytest.mark.asyncio
async def test_v2_custom_model_on_a_catalog_base_follows_its_own_grants(v2: FakeSoevApi, all_models, access) -> None:
    rows, run = all_models
    rows.append(model_row('helper', base_model_id='glm-5-3'))
    models = await run()
    access[0]['helper'] = rows[0]
    with pytest.raises(Exception, match='Model not found'):
        await models_utils.check_model_access(USER, models['helper'])
    access[1].add('helper')
    await models_utils.check_model_access(USER, models['helper'])
    assert await has_base_model_access(USER.id, rows[0], user_role=USER.role)
    access[0]['glm-5-3'] = model_row('glm-5-3')
    assert await has_base_model_access(USER.id, rows[0], user_role=USER.role)
    assert not await has_base_model_access(PENDING.id, rows[0], user_role=PENDING.role)


@pytest.mark.asyncio
async def test_v2_row_less_models_outside_the_catalog_stay_admin_only(v2: FakeSoevApi, access) -> None:
    model = {'id': 'zai-org/GLM-5.3', 'owned_by': 'openai'}
    assert await models_utils.get_filtered_models([model], USER) == []
    assert await models_utils.get_filtered_models([model], ADMIN) == [model]
    for user in (USER, ADMIN):
        with pytest.raises(Exception, match='Model not found'):
            await models_utils.check_model_access(user, model)


@pytest.mark.asyncio
async def test_stock_row_less_models_stay_admin_only(stock, access) -> None:
    model = {'id': 'glm-5-3', 'owned_by': 'openai'}
    assert await models_utils.get_filtered_models([model], USER) == []
    assert await models_utils.get_filtered_models([model], ADMIN) == [model]
    for user in (USER, ADMIN):
        with pytest.raises(Exception, match='Model not found'):
            await models_utils.check_model_access(user, model)
    custom = model_row('helper', base_model_id='glm-5-3')
    assert not await has_base_model_access(USER.id, custom, user_role=USER.role)


@pytest.mark.asyncio
async def test_v2_default_comes_from_the_catalog_and_ignores_ui_default_models(v2: FakeSoevApi) -> None:
    assert await model_catalog.default_models('zai-org/GLM-5.3') == 'glm-5-3'
    v2.models = [{**GLM, 'default': False}, GEMMA]
    model_catalog.CATALOG_CACHE['expires_at'] = 0.0
    assert await model_catalog.default_models('zai-org/GLM-5.3') is None


@pytest.mark.asyncio
async def test_stock_default_is_ui_default_models(stock) -> None:
    assert await model_catalog.default_models('zai-org/GLM-5.3') == 'zai-org/GLM-5.3'


def test_recorded_endpoints_resolve_to_catalog_ids_without_guessing(monkeypatch):
    monkeypatch.setitem(
        model_catalog.CATALOG_CACHE,
        'entries',
        [
            {'id': 'zai-org/glm-5.3', 'endpoint_id': 'nebul--glm-5-3-flash'},
        ],
    )
    assert model_catalog.catalog_id('nebul--glm-5-3-flash') == 'zai-org/glm-5.3'
    assert model_catalog.catalog_id('zai-org/glm-5.3') == 'zai-org/glm-5.3'
    assert model_catalog.catalog_id('unknown-endpoint') == 'unknown-endpoint'
    assert model_catalog.catalog_id('old-endpoint', {'old-endpoint': 'zai-org/glm-5.3'}) == 'zai-org/glm-5.3'
