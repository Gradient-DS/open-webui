"""Live product configuration owns platform reads and refuses local writes."""

import copy
import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from open_webui import config, env
from open_webui.models import config as config_model
from open_webui.models.config import Config
from open_webui.soev import client, model_catalog, product_config
from open_webui.soev.client import SoevApiError
from open_webui.utils.access_control import get_permissions, has_permission
from open_webui.utils.features import feature_enabled, require_feature
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def document():
    kinds = {'boolean': True, 'string': '', 'integer': 1, 'number': 1.0, 'array': [], 'object': {}, 'null': None}
    return {
        'product': 'owui',
        'revision': 0,
        'values': {key: copy.deepcopy(kinds[kind.split('|')[0]]) for key, kind in product_config.SETTINGS.items()},
        'entitlements': {key: True for key in product_config.FEATURES},
        'unset': [],
    }


@pytest_asyncio.fixture
async def live(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://fictional-api.invalid')
    monkeypatch.setattr(config, 'SOEV_API_KEY', 'soev_test_cred-fictional_service-secret')
    monkeypatch.setattr(config, 'SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', 5)
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(product_config, '_backend', None)
    monkeypatch.setattr(product_config, '_snapshot', None)
    monkeypatch.setattr(product_config, '_expires_at', 0.0)
    now = [100.0]
    monkeypatch.setattr(product_config, 'monotonic', lambda: now[0])
    state = SimpleNamespace(document=document(), calls=[], fail=False, now=now)

    def handle(request):
        state.calls.append(request)
        assert request.url.path == '/v1/products/owui/config'
        assert request.headers['Authorization'] == 'Bearer ' + config.SOEV_API_KEY
        assert not request.headers.get('X-Soev-User-Token')
        return httpx.Response(503 if state.fail else 200, json=state.document)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(client, '_shared_client', lambda: http)
    yield state
    await http.aclose()


@pytest.mark.asyncio
async def test_cached_content_refreshes_without_restart_and_survives_outage(live):
    key = 'models.system_prompt'
    live.document['values'][key] = 'Fictional amber prompt'
    assert await Config.get(key) == 'Fictional amber prompt'
    live.document['values'][key] = 'Fictional cobalt prompt'
    live.now[0] += 59
    assert (await Config.get_many(key))[key] == 'Fictional amber prompt'
    assert len(live.calls) == 1
    live.now[0] += 1
    assert await Config.get(key) == 'Fictional cobalt prompt'
    live.fail = True
    live.now[0] += 60
    assert await Config.get(key) == 'Fictional cobalt prompt'
    calls = len(live.calls)
    live.now[0] += 9
    assert await Config.get(key) == 'Fictional cobalt prompt'
    assert len(live.calls) == calls
    live.fail = False
    live.document['values'][key] = 'Fictional jade prompt'
    live.now[0] += 1
    assert await Config.get(key) == 'Fictional jade prompt'


@pytest.mark.asyncio
async def test_default_model_selection_reads_product_config_live(live):
    live.document['values']['ui.default_models'] = 'fictional/amber'
    assert await model_catalog.default_models('fictional/local-default') == 'fictional/amber'
    live.document['values']['ui.default_models'] = 'fictional/cobalt'
    live.now[0] += 60
    assert await model_catalog.default_models('fictional/local-default') == 'fictional/cobalt'


@pytest.mark.asyncio
async def test_no_snapshot_never_uses_defaults_and_retries(live, monkeypatch):
    monkeypatch.setattr(Config, 'DEFAULTS', {'models.system_prompt': 'Fictional env prompt'})
    live.fail = True
    for _ in range(2):
        with pytest.raises(SoevApiError) as error:
            await Config.get('models.system_prompt', 'Fictional fallback')
        assert error.value.code == 'service_unavailable'
    assert len(live.calls) == 1
    live.fail = False
    live.now[0] += 10
    assert await Config.get('models.system_prompt') == ''


@pytest.mark.asyncio
async def test_invalid_response_retains_snapshot_and_backend_change_discards_it(live, monkeypatch):
    assert await Config.get('models.system_prompt') == ''
    live.document['values']['models.overrides'] = {'fictional/amber': {'is_active': 'false'}}
    live.now[0] += 60
    assert await Config.get('models.overrides') == {}
    monkeypatch.setattr(config, 'SOEV_API_KEY', 'soev_test_cred-fictional_other-secret')
    live.fail = True
    with pytest.raises(SoevApiError) as error:
        await Config.get('models.system_prompt')
    assert error.value.code == 'service_unavailable'


@pytest.mark.asyncio
async def test_product_values_override_env_and_stored_rows(live, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Config.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all(
            [
                Config(key='models.system_prompt', value='Fictional stored prompt'),
                Config(key='rag.file.max_count', value=99),
            ]
        )
        await session.commit()

    @asynccontextmanager
    async def database():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(config_model, 'get_async_db', database)
    monkeypatch.setattr(Config, 'DEFAULTS', {'models.system_prompt': 'Fictional env prompt', 'rag.file.max_count': 3})
    live.document['values']['models.system_prompt'] = 'Fictional console prompt'
    assert await Config.get('models.system_prompt') == 'Fictional console prompt'
    assert await Config.get_many('models.system_prompt', 'rag.file.max_count') == {
        'models.system_prompt': 'Fictional console prompt',
        'rag.file.max_count': 3,
    }
    for updates in (
        {'models.system_prompt': 'Refused'},
        {'rag.file.max_count': 7},
        {'models.system_prompt': 'Refused', 'rag.file.max_count': 7},
    ):
        with pytest.raises(SoevApiError) as error:
            await Config.upsert(updates)
        assert error.value.status == 403
    assert Config.DEFAULTS['rag.file.max_count'] == 3
    async with sessions() as session:
        assert (await session.get(Config, 'models.system_prompt')).value == 'Fictional stored prompt'
        assert (await session.get(Config, 'rag.file.max_count')).value == 99
    await engine.dispose()


@pytest.mark.asyncio
async def test_unentitled_feature_is_off_and_its_route_refused(live):
    live.document['entitlements']['feature.knowledge'] = False
    del live.document['values']['feature.knowledge']
    assert await feature_enabled('knowledge') is False
    with pytest.raises(HTTPException) as error:
        await require_feature('knowledge')()
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_unentitled_knowledge_routes_refuse_every_user(live, role):
    from open_webui.routers import knowledge
    from open_webui.utils.auth import get_verified_user

    live.document['entitlements']['feature.knowledge'] = False
    del live.document['values']['feature.knowledge']
    app = FastAPI()
    app.include_router(knowledge.router, prefix='/knowledge')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-user', role=role)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://fictional-owui.invalid'
    ) as api:
        response = await api.post('/knowledge/create', json={'name': 'Fictional knowledge', 'description': ''})
        assert response.status_code == 403, response.text
        response = await api.get('/knowledge/')
        assert response.status_code == 403, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('module', 'path', 'key'),
    [
        ('prompts', '/', 'feature.prompts'),
        ('models', '/list', 'feature.models'),
        ('skills', '/', 'feature.skills'),
        ('notes', '/', 'notes.enable'),
        ('tools', '/', 'feature.tools'),
        ('terminals', '/', 'feature.terminal_servers'),
    ],
)
async def test_disabled_product_routes_are_refused(live, module, path, key):
    from open_webui.utils.auth import get_verified_user

    if key in live.document['entitlements']:
        live.document['entitlements'][key] = False
        del live.document['values'][key]
    router = importlib.import_module(f'open_webui.routers.{module}').router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-user', role='admin')
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://fictional-owui.invalid'
    ) as api:
        response = await api.get(path)
        assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_permission_endpoints_use_product_leaves(live):
    from open_webui.routers import users
    from open_webui.utils.auth import get_admin_user, get_verified_user

    live.document['values']['permissions.workspace.knowledge'] = False
    app = FastAPI()
    app.include_router(users.router)

    def actor():
        return SimpleNamespace(id='fictional-user', role='admin')

    app.dependency_overrides[get_verified_user] = actor
    app.dependency_overrides[get_admin_user] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://fictional-owui.invalid'
    ) as api:
        for path in ('/permissions', '/default/permissions', '/default/permissions/defaults'):
            response = await api.get(path)
            assert response.status_code == 200, response.text
            assert response.json()['workspace']['knowledge'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('granted', [False, True])
async def test_platform_skill_content_preserves_scoped_token_and_read_grants(live, monkeypatch, granted):
    from fastapi.responses import Response
    from open_webui.routers import skill_files
    from open_webui.utils.auth import create_token, get_optional_verified_user

    live.document['values']['feature.skills'] = True
    live.document['values']['feature.skill_files'] = True
    actor = SimpleNamespace(id='fictional-reader', role='user')
    skill = SimpleNamespace(id='fictional-skill', user_id='fictional-owner')
    monkeypatch.setattr(skill_files.Users, 'get_user_by_id', AsyncMock(return_value=actor))
    monkeypatch.setattr(skill_files, '_get_skill_or_404', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files.AccessGrants, 'has_access', AsyncMock(return_value=granted))
    stream = AsyncMock(return_value=Response('Fictional private file.'))
    monkeypatch.setattr(skill_files, '_stream_skill_file', stream)
    app = FastAPI()
    app.include_router(skill_files.router)
    app.dependency_overrides[get_optional_verified_user] = lambda: None
    token = create_token({'id': actor.id, 'skill_id': skill.id, 'purpose': 'skill_file_read'})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://fictional-owui.invalid'
    ) as api:
        response = await api.get(
            f'/id/{skill.id}/files/content',
            params={'path': 'fictional-private.txt'},
            headers={'Authorization': 'Bearer ' + token},
        )
    assert response.status_code == (200 if granted else 401), response.text
    assert stream.await_count == int(granted)


@pytest.mark.asyncio
async def test_public_config_keeps_shape_and_refreshes_features(live, monkeypatch, platform_capabilities):
    main = importlib.import_module('open_webui.main')

    monkeypatch.setattr(main, 'decode_token', lambda _: {'id': 'fictional-user'})
    monkeypatch.setattr(
        main.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='fictional-user', role='user'))
    )
    monkeypatch.setattr(main.model_catalog, 'default_models', AsyncMock(return_value='fictional/amber'))
    request = Request(
        {'type': 'http', 'path': '/api/config', 'headers': [(b'authorization', b'Bearer fictional-token')]}
    )
    first = await main.get_app_config(request)
    assert first['features']['feature_knowledge'] is True
    live.document['values']['feature.knowledge'] = False
    live.now[0] += 60
    second = await main.get_app_config(request)
    assert first.keys() == second.keys()
    assert first['features'].keys() == second['features'].keys()
    assert second['features']['feature_knowledge'] is False
    assert second['default_models'] == 'fictional/amber'


@pytest.mark.asyncio
async def test_permissions_ignore_groups_and_default_rows(live, monkeypatch):
    from open_webui.models.groups import Groups

    groups = AsyncMock(side_effect=AssertionError('Group permission rows must not be read'))
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', groups)
    live.document['values']['permissions.workspace.knowledge'] = False
    defaults = {'workspace': {'knowledge': True}}
    assert not await has_permission('fictional-user', 'workspace.knowledge', defaults)
    assert (await get_permissions('fictional-user', defaults))['workspace']['knowledge'] is False
    assert (await Config.get('user.permissions'))['workspace']['knowledge'] is False
    live.document['values']['permissions.workspace.knowledge'] = True
    live.now[0] += 60
    assert await has_permission('fictional-user', 'workspace.knowledge', defaults)
    groups.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_overrides_replace_rows_and_prompt(live, monkeypatch):
    from open_webui.models.models import Models
    from open_webui.soev import system_prompt

    monkeypatch.setattr(model_catalog, 'catalog', AsyncMock(return_value=[{'id': 'fictional/amber'}]))
    live.document['values']['models.overrides'] = {
        'fictional/amber': {
            'name': 'Fictional Amber',
            'is_active': False,
            'params': {'system': 'Fictional model prompt', 'temperature': 0.4},
            'meta': {'description': 'Fictional description'},
        },
    }
    row = await Models.get_model_by_id('fictional/amber')
    assert row.name == 'Fictional Amber'
    assert row.params.model_dump()['temperature'] == 0.4
    assert row.meta.description == 'Fictional description'
    assert not row.is_active
    assert not await model_catalog.is_offered('fictional/amber', 'admin')
    with pytest.raises(HTTPException) as error:
        await model_catalog.configured_model({'id': row.id}, row)
    assert error.value.status_code == 403
    assert await system_prompt.for_model(row) == 'Fictional model prompt'
    live.document['values']['models.overrides'] = {}
    live.document['values']['models.system_prompt'] = 'Fictional global prompt'
    live.now[0] += 60
    row = await Models.get_model_by_id('fictional/amber')
    assert row.name == 'fictional/amber'
    assert row.is_active
    assert row.params.model_dump() == {}
    assert await system_prompt.for_model(row) == 'Fictional global prompt'


@pytest.mark.asyncio
async def test_known_catalog_model_never_reads_a_local_row_during_catalog_outage(live, monkeypatch):
    from open_webui.models import models

    monkeypatch.setattr(model_catalog, 'catalog', AsyncMock(return_value=[]))
    monkeypatch.setitem(model_catalog.CATALOG_CACHE, 'entries', [{'id': 'fictional/amber'}])
    local = AsyncMock(side_effect=AssertionError('Stored catalog content must not be read'))
    monkeypatch.setattr(models, 'get_async_db_context', local)
    live.document['values']['models.overrides'] = {
        'fictional/amber': {'params': {'system': 'Fictional console prompt'}}
    }
    row = await models.Models.get_model_by_id('fictional/amber')
    assert row.params.model_dump()['system'] == 'Fictional console prompt'
    local.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_uses_env_features_and_persistent_rows(live, monkeypatch):
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    monkeypatch.setattr(Config, 'PERSISTENT_ENABLED', False)
    monkeypatch.setattr(Config, 'DEFAULTS', {'models.system_prompt': 'Fictional legacy prompt'})
    assert await Config.get('models.system_prompt') == 'Fictional legacy prompt'
    assert await feature_enabled('knowledge', True) is True
    monkeypatch.setattr(config, 'FEATURE_SKILL_FILES', False)
    assert await feature_enabled('skill_files') is False
    await Config.upsert({'models.system_prompt': 'Fictional changed prompt'})
    assert await Config.get('models.system_prompt') == 'Fictional changed prompt'
    assert not live.calls


@pytest.mark.asyncio
async def test_legacy_disabled_skill_routes_remain_absent(live, monkeypatch):
    from open_webui.routers import skill_files

    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    monkeypatch.setattr(config, 'FEATURE_SKILL_FILES', False)
    app = FastAPI()
    app.include_router(skill_files.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://fictional-owui.invalid'
    ) as api:
        for method in ('GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'):
            response = await api.request(method, '/id/fictional-skill/files')
            assert response.status_code == 404, response.text
    assert not live.calls
