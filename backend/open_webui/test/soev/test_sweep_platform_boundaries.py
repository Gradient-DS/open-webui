"""Prepared boundaries refuse explicitly before touching retired content."""

from importlib import import_module
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui import config, env
from open_webui.routers import analytics, archives, automations, groups, users
from open_webui.soev import threads
from open_webui.soev.client import SoevApiError
from open_webui.soev.errors import problem_message
from open_webui.utils.auth import get_verified_user
from starlette.responses import JSONResponse


@pytest.mark.parametrize('role', ['admin', 'user'])
@pytest.mark.parametrize(
    'module,path,method,expected',
    [
        (archives, '/user/fictional-person', 'POST', {'state': 'unavailable'}),
        (archives, '/fictional-archive', 'GET', {'state': 'unavailable'}),
        (archives, '/fictional-archive/export', 'GET', {'state': 'unavailable'}),
        (archives, '/fictional-archive', 'PATCH', {'state': 'unavailable'}),
        (archives, '/fictional-archive', 'DELETE', {'state': 'unavailable'}),
        (analytics, '/models/fictional-model/overview', 'GET', {'state': 'unavailable'}),
        (automations, '/create', 'POST', {'constraint': 'automations:unavailable'}),
        (automations, '/fictional-automation', 'GET', {'constraint': 'automations:unavailable'}),
        (groups, '/create', 'POST', {'code': 'platform_groups_read_only'}),
        (groups, '/id/fictional-group/update', 'POST', {'code': 'platform_groups_read_only'}),
        (groups, '/id/fictional-group/delete', 'DELETE', {'code': 'platform_groups_read_only'}),
        (users, '/fictional-person/shared-data', 'GET', {'constraint': 'offboarding:console'}),
        (users, '/fictional-person', 'DELETE', {'constraint': 'offboarding:console'}),
    ],
)
def test_prepared_refusal_precedes_body_and_content_access(monkeypatch, role, module, path, method, expected):
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-fern', role=role)

    @app.exception_handler(threads.ThreadError)
    async def thread_error(request, error):
        return JSONResponse({'state': error.state}, error.status)

    @app.exception_handler(SoevApiError)
    async def platform_error(request, error):
        return JSONResponse(
            {'detail': {'code': error.code, 'constraint': error.constraint, 'detail': problem_message(error, 'en')}},
            error.status,
        )

    with TestClient(app) as client:
        body = {'name': 'Fictional renamed group'} if module is groups and path.endswith('/update') else {}
        response = client.request(method, path, json=body)
    assert response.status_code == (409 if module in (archives, analytics) else 403), response.text
    actual = response.json().get('detail', response.json())
    assert expected.items() <= actual.items()


@pytest.mark.asyncio
async def test_legacy_task_id_stop_refuses_before_the_redis_dispatcher(monkeypatch):
    main = import_module('open_webui.main')
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    route = next(route for route in main.app.routes if getattr(route, 'path', None) == '/api/tasks/stop/{task_id}')
    assert main.require_legacy_task_dispatch in [dependency.call for dependency in route.dependant.dependencies]
    with pytest.raises(threads.ThreadError) as caught:
        await main.require_legacy_task_dispatch(SimpleNamespace(id='fictional-fern', role='admin'))
    assert (caught.value.status, caught.value.state) == (409, 'unavailable')
