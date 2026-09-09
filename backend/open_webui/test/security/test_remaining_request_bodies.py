"""HTTP compatibility controls for Phase 4b, using actual handlers and stubbed I/O."""

import copy
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[4]
ACCESS_PATHS = {
    'chats': '/api/v1/chats/shared/{id}/access/update',
    'folders': '/api/v1/folders/{id}/access/update',
    'knowledge': '/api/v1/knowledge/{id}/access/update',
    'notes': '/api/v1/notes/{id}/access/update',
    'prompts': '/api/v1/prompts/id/{prompt_id}/access/update',
    'skills': '/api/v1/skills/id/{id}/access/update',
    'tools': '/api/v1/tools/id/{id}/access/update',
}
GRANTS = [
    {
        'id': 'grant-1',
        'principal_type': 'group',
        'principal_id': 'research-team',
        'permission': 'write',
        'extension': {'audit': ['keep', 7, None]},
    },
    {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'},
]
EXTRA = {'extension': {'opaque': ['keep', 7, None]}}


@pytest.fixture(scope='module')
def application():
    spec = importlib.util.spec_from_file_location(
        'remaining_body_exporter', REPO / 'scripts/security/export_openapi.py'
    )
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    exporter.build()
    from open_webui.main import app

    return app


@pytest.fixture
def mount(application):
    from open_webui.internal.db import get_async_session
    from open_webui.utils.auth import get_admin_user, get_verified_user

    def create(path, role='admin'):
        route = next(route for route in application.routes if route.path == path and 'POST' in route.methods)
        app = FastAPI()
        app.add_api_route(path, route.endpoint, methods=['POST'], response_model=None)
        user = SimpleNamespace(id='control-user', role=role, info={'existing': 'preserve'})
        app.dependency_overrides[get_admin_user] = lambda: user
        app.dependency_overrides[get_verified_user] = lambda: user
        app.dependency_overrides[get_async_session] = lambda: None
        return TestClient(app)

    return create


@pytest.mark.parametrize('module_name', ACCESS_PATHS)
def test_access_grants_keep_nested_extras_and_missing_keys(module_name, mount, monkeypatch):
    module = importlib.import_module(f'open_webui.routers.{module_name}')
    from open_webui.models.chats import ChatModel
    from open_webui.models.knowledge import KnowledgeModel

    resource = KnowledgeModel(
        id='resource-1', user_id='control-user', name='Research', description='Shared notes', created_at=1, updated_at=2
    )
    if module_name == 'chats':
        resource = ChatModel(
            id='resource-1', user_id='control-user', title='Research', chat={}, created_at=1, updated_at=2
        )
    elif module_name == 'notes':
        resource = SimpleNamespace(id='resource-1', user_id='control-user', is_pinned=False)
    table_name, getter = {
        'chats': ('Chats', 'get_chat_by_id'),
        'folders': ('Folders', 'get_folder_by_id'),
        'knowledge': ('Knowledges', 'get_knowledge_by_id'),
        'notes': ('Notes', 'get_note_by_id'),
        'prompts': ('Prompts', 'get_prompt_by_id'),
        'skills': ('Skills', 'get_skill_by_id'),
        'tools': ('Tools', 'get_tool_by_id'),
    }[module_name]
    table = getattr(module, table_name)
    monkeypatch.setattr(table, getter, AsyncMock(return_value=resource))
    monkeypatch.setattr(module.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(module.AccessGrants, 'get_grants_by_resource', AsyncMock(return_value=[]))
    saved = AsyncMock(return_value=[])
    monkeypatch.setattr(module.AccessGrants, 'set_access_grants', saved)
    if hasattr(module, 'publish_event'):
        monkeypatch.setattr(module, 'publish_event', AsyncMock())
    if module_name == 'folders':
        monkeypatch.setattr(module, 'check_folders_permission', AsyncMock())
    if module_name == 'knowledge':
        monkeypatch.setattr(table, 'get_file_metadatas_by_id', AsyncMock(return_value=[]))
    if module_name == 'notes':
        monkeypatch.setattr(table, 'get_pinned_note_ids', AsyncMock(return_value=[]))
    path = ACCESS_PATHS[module_name]
    payload = {'access_grants': copy.deepcopy(GRANTS), **EXTRA}
    with mount(path) as client:
        response = client.post(path.replace('{id}', 'resource-1').replace('{prompt_id}', 'resource-1'), json=payload)
    assert response.status_code == 200, response.text
    saved.assert_awaited_once()
    assert saved.await_args.args[2] == GRANTS
    assert 'id' not in saved.await_args.args[2][1]


def test_model_import_preserves_partial_update_and_nested_extensions(mount, monkeypatch):
    from open_webui.models.models import ModelForm
    from open_webui.routers import models

    existing = ModelForm(id='research-model', name='Original name', meta={}, params={}, access_grants=GRANTS)
    monkeypatch.setattr(models.Models, 'get_models_by_ids', AsyncMock(return_value=[existing]))
    monkeypatch.setattr(models, '_verify_knowledge_file_access', AsyncMock())
    monkeypatch.setattr(models, 'publish_event', AsyncMock())
    filtered = AsyncMock()
    monkeypatch.setattr(models, 'filter_allowed_access_grants', filtered)
    updated = AsyncMock()
    monkeypatch.setattr(models.Models, 'update_model_by_id', updated)
    payload = {
        'models': [
            {
                'id': 'research-model',
                'meta': {'description': 'Research assistant', 'extension': EXTRA},
                'params': {'temperature': '0.70', 'extension': EXTRA},
                **EXTRA,
            }
        ],
        **EXTRA,
    }
    with mount('/api/v1/models/import') as client:
        response = client.post('/api/v1/models/import', json=payload)
    assert response.status_code == 200, response.text
    assert response.json() is True
    updated.assert_awaited_once()
    form = updated.await_args.args[1]
    assert form.name == 'Original name'
    assert form.access_grants == GRANTS
    assert form.meta.description == 'Research assistant'
    assert form.meta.model_extra['extension'] == EXTRA
    assert form.params.model_extra == payload['models'][0]['params']
    filtered.assert_not_awaited()  # Inserting an access_grants default would change existing ACLs.


def test_integration_config_keeps_dynamic_slugs_and_provider_extensions(mount, monkeypatch):
    from open_webui.routers import configs

    providers = {
        'research-docs': {
            'name': 'Research documents',
            'description': 'Team library',
            'service_account_id': 'service-user',
            'max_files_per_kb': '250',
            'custom_metadata_fields': [{'key': 'project', 'label': 'Project', 'required': True, **EXTRA}],
            **EXTRA,
        },
        'another-provider': {'name': 'Secondary'},
    }
    old = {'removed': {'service_account_id': 'old-service-user'}}
    monkeypatch.setattr(configs.Config, 'get', AsyncMock(side_effect=[old, providers]))
    saved = AsyncMock()
    bound, unbound = AsyncMock(), AsyncMock()
    monkeypatch.setattr(configs.Config, 'upsert', saved)
    monkeypatch.setattr(configs, '_bind_service_account', bound)
    monkeypatch.setattr(configs, '_unbind_service_account', unbound)
    with mount('/api/v1/configs/integrations') as client:
        response = client.post('/api/v1/configs/integrations', json={'providers': providers, **EXTRA})
    assert response.status_code == 200, response.text
    assert response.json() == {'providers': providers}
    saved.assert_awaited_once_with({'integrations.providers': providers})
    bound.assert_awaited_once_with('service-user', 'research-docs')
    unbound.assert_awaited_once_with('old-service-user')


def test_user_info_merge_keeps_extra_keys(mount, monkeypatch):
    from open_webui.routers import users

    payload = {'location': '52.367, 4.904 (lat, long)', 'integration_provider': 'research-docs', **EXTRA}
    merged = {'existing': 'preserve', **payload}
    updated = AsyncMock(return_value=SimpleNamespace(info=merged))
    monkeypatch.setattr(users.Users, 'update_user_by_id', updated)
    with mount('/api/v1/users/user/info/update') as client:
        response = client.post('/api/v1/users/user/info/update', json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == merged
    updated.assert_awaited_once_with('control-user', {'info': merged}, db=None)


@pytest.mark.parametrize('role', ['admin', 'user'])
@pytest.mark.parametrize('temperature', ['0.70', 0.7])
def test_user_settings_keep_nested_values_and_permission_filter(role, temperature, mount, monkeypatch):
    from open_webui.routers import users

    payload = {
        'ui': {
            'system': 'Explain carefully.',
            'models': ['research-model'],
            'notifications': {'webhook_url': 'https://hooks.example.test/notify', **EXTRA},
            'toolServers': [{'url': 'https://tools.example.test', 'key': 'test-key', **EXTRA}],
            'temperature': temperature,
            'params': {'temperature': temperature, 'stop': ['END'], **EXTRA},
            **EXTRA,
        },
        **EXTRA,
    }
    expected = copy.deepcopy(payload)
    if role == 'user':
        expected['ui'].pop('toolServers')
    monkeypatch.setattr(users.Config, 'get', AsyncMock(return_value={}))

    # Keyed on the permission rather than call order: v0.11.3 added a
    # features.webhooks check beside the existing tool-servers one, and a
    # positional side_effect silently reassigns which answer belongs to which
    # question when a caller inserts a check.
    async def permits(_user_id, permission, *_args, **_kwargs):
        return permission != 'features.direct_tool_servers'

    monkeypatch.setattr(users, 'has_permission', permits)
    updated = AsyncMock(return_value=SimpleNamespace(id='control-user', settings=expected))
    monkeypatch.setattr(users.Users, 'update_user_settings_by_id', updated)
    monkeypatch.setattr(users, 'publish_event', AsyncMock())
    with mount('/api/v1/users/user/settings/update', role) as client:
        response = client.post('/api/v1/users/user/settings/update', json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == expected
    updated.assert_awaited_once_with('control-user', expected, db=None)


@pytest.fixture(scope='module')
def committed_spec():
    import json

    return json.loads((REPO / 'security/openapi.json').read_text())


@pytest.mark.parametrize('path', ACCESS_PATHS.values())
def test_access_grant_strings_are_derived(path, committed_spec):
    from openapi_surface import writable_string_fields

    assert set(writable_string_fields(committed_spec)[f'POST {path}']) == {
        f'access_grants[].{field}' for field in ('id', 'principal_type', 'principal_id', 'permission')
    }


@pytest.mark.parametrize(
    ('path', 'expected'),
    [
        (
            '/api/v1/models/import',
            {
                'models[].id',
                'models[].name',
                'models[].meta.description',
                'models[].params.system',
                'models[].access_grants[].principal_id',
            },
        ),
        ('/api/v1/users/user/info/update', {'location', 'integration_provider'}),
        (
            '/api/v1/users/user/settings/update',
            {
                'ui.system',
                'ui.models[]',
                'ui.notifications.webhook_url',
                'ui.toolServers[].url',
                'ui.title.prompt',
                'ui.audio.speaker',
            },
        ),
    ],
)
def test_known_nested_strings_are_derived(path, expected, committed_spec):
    from openapi_surface import writable_string_fields

    assert expected <= set(writable_string_fields(committed_spec)[f'POST {path}'])


def test_integration_provider_schema_is_typed_without_invented_slugs(committed_spec):
    from openapi_surface import writable_string_fields

    schemas = committed_spec['components']['schemas']
    providers = schemas['IntegrationsConfigBody']['properties']['providers']
    assert 'properties' not in providers
    assert providers['additionalProperties'] == {'$ref': '#/components/schemas/IntegrationProviderInput'}
    assert {'name', 'description', 'service_account_id', 'custom_metadata_fields'} <= set(
        schemas['IntegrationProviderInput']['properties']
    )
    assert 'POST /api/v1/configs/integrations' not in writable_string_fields(committed_spec)
