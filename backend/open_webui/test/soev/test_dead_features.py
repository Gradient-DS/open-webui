"""Retired product routes stay absent without editing upstream routers."""

import pytest

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from open_webui.soev.product_surface import product_router

RETIRED = {
    'POST /api/v1/data-warnings/accept',
    'POST /api/v1/auths/signin',
    'POST /api/v1/auths/signup',
    'POST /api/v1/auths/ldap',
    'POST /api/v1/auths/update/password',
    'POST /api/v1/auths/password/forgot',
    'POST /api/v1/auths/password/reset',
    'POST /api/v1/auths/2fa/verify',
    'POST /api/v1/auths/2fa/totp/setup',
    'POST /api/v1/auths/2fa/totp/enable',
    'POST /api/v1/auths/2fa/totp/disable',
    'POST /api/v1/auths/2fa/recovery/regenerate',
    'GET /api/v1/auths/password/reset/{token}/validate',
    'GET /api/v1/auths/2fa/status',
    'POST /api/chat/actions/{action_id}',
    'GET /oauth/clients/{client_id}/authorize',
    'GET /oauth/clients/{client_id}/callback',
    'GET /api/v1/tasks/config',
    'POST /api/v1/chats/{id}/compact',
    'POST /api/v1/chats/{id}/messages/{message_id}/resolve',
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
    assert not any(
        route.split(' ', 1)[1].startswith(('/ollama', '/openai', '/api/v1/tools/', '/api/v1/functions/'))
        for route in actual
    )
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


async def _authenticated_config(monkeypatch, saved=None):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import Request
    from importlib import import_module

    main = import_module('open_webui.main')

    user = SimpleNamespace(id='fictional-user', role='user')
    monkeypatch.setattr(main, 'decode_token', lambda _: {'id': user.id})
    monkeypatch.setattr(main, 'is_valid_token', AsyncMock(return_value=True))
    monkeypatch.setattr(main.Users, 'get_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(main.sessions, 'session_user', lambda *_: user)
    values = {'onedrive.enable': True, **(saved or {})}
    monkeypatch.setattr(
        main.Config, 'get_many', AsyncMock(side_effect=lambda *keys: {k: values[k] for k in keys if k in values})
    )
    monkeypatch.setattr(main, 'feature_enabled', AsyncMock(return_value=True))
    monkeypatch.setattr(main.capabilities, 'has', AsyncMock(return_value=True))
    monkeypatch.setattr(main.model_catalog, 'default_models', AsyncMock(return_value='fictional-model'))
    request = Request({'type': 'http', 'headers': [(b'authorization', b'Bearer fictional')], 'app': main.app})
    return await main.get_app_config(request)


def test_personal_connector_settings_are_not_published(monkeypatch):
    import asyncio

    result = asyncio.run(_authenticated_config(monkeypatch))
    assert 'enable_onedrive_personal' not in result['features']
    assert 'client_id_personal' not in result['onedrive']
    assert result['features']['enable_onedrive_integration']
    assert result['features']['enable_image_generation'] is False


def test_local_retention_workers_cannot_start():
    from importlib import import_module

    main = import_module('open_webui.main')

    assert not hasattr(main, 'periodic_archive_cleanup')
    assert not hasattr(main, 'periodic_data_retention_cleanup')


@pytest.mark.parametrize('engine,offered', [('pyodide', False), ('jupyter', True)])
def test_browser_execution_cannot_be_restored_by_saved_enable_flags(monkeypatch, engine, offered):
    import asyncio

    result = asyncio.run(
        _authenticated_config(
            monkeypatch,
            {
                'code_execution.enable': True,
                'code_execution.engine': engine,
                'code_interpreter.enable': True,
                'code_interpreter.engine': engine,
            },
        )
    )
    assert result['features']['enable_code_execution'] is offered
    assert result['features']['enable_code_interpreter'] is offered
    assert result['features']['enable_pyodide_file_persistence'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['pyodide', 'jupyter'])
async def test_code_tool_never_calls_the_browser_and_preserves_server_execution(monkeypatch, engine):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from open_webui.tools import builtin
    from open_webui.utils import code_interpreter

    async def setting(key, default=None):
        return {'code_interpreter.engine': engine, 'code_interpreter.jupyter.url': 'https://jupyter.invalid'}.get(
            key, default
        )

    monkeypatch.setattr(builtin.Config, 'get', setting)
    browser = AsyncMock(side_effect=AssertionError('Retired browser execution was called'))
    server = AsyncMock(return_value={'stdout': 'fictional server result', 'stderr': '', 'result': ''})
    monkeypatch.setattr(code_interpreter, 'execute_code_jupyter', server)
    result = json.loads(
        await builtin.execute_code('print("fictional")', __request__=SimpleNamespace(), __event_call__=browser)
    )
    browser.assert_not_awaited()
    if engine == 'pyodide':
        assert 'error' in result
        server.assert_not_awaited()
    else:
        assert result['stdout'] == 'fictional server result'
        server.assert_awaited_once()


def test_retained_feedback_definitions_use_the_live_keys(monkeypatch):
    import asyncio

    definitions = [{'id': 'clarity', 'labels': {'en': 'Clarity', 'nl': 'Duidelijkheid'}}]
    result = asyncio.run(
        _authenticated_config(
            monkeypatch,
            {'evaluation.feedback.reasons': definitions, 'evaluation.feedback.tags': definitions},
        )
    )
    assert result['features']['feedback_reasons'] == definitions
    assert result['features']['feedback_tags'] == definitions
