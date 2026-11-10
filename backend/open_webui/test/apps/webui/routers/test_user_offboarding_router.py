"""Retired offboarding routes refuse every identity without local deletion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui import config, env
from open_webui.routers import users
from open_webui.utils.auth import get_current_user


@pytest.fixture(params=['legacy', 'platform'])
def api(request, monkeypatch):
    from open_webui.main import soev_api_error_handler

    platform = request.param == 'platform'
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', platform)
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid' if platform else '')
    app = FastAPI()
    app.include_router(users.router, prefix='/api/v1/users')
    app.add_exception_handler(users.SoevApiError, soev_api_error_handler)
    actor = SimpleNamespace(id='rook', role='admin')
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[users.get_async_session] = lambda: SimpleNamespace()
    first_user = AsyncMock(return_value=SimpleNamespace(id='primary'))
    monkeypatch.setattr(users.Users, 'get_first_user', first_user)
    offboard = AsyncMock(return_value=1)
    monkeypatch.setattr(users, 'offboard_user', offboard)
    disconnect = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(users, 'disconnect_user_sessions', disconnect)
    monkeypatch.setattr(users, 'publish_event', publish)
    with TestClient(app) as browser:
        yield SimpleNamespace(
            app=app,
            browser=browser,
            actor=actor,
            platform=platform,
            offboard=offboard,
            first_user=first_user,
            disconnect=disconnect,
            publish=publish,
        )


def assert_console_refusal(api, response):
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': 'Not Found'}
    api.first_user.assert_not_awaited()
    api.offboard.assert_not_awaited()
    api.disconnect.assert_not_awaited()
    api.publish.assert_not_awaited()


@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
def test_mode_and_archive_parameters(api, mode):
    params = [
        ('mode', mode),
        ('archive_reason', 'Departure'),
        ('archive_retention_days', '8'),
        ('never_delete', 'true'),
        ('confirm_shared_deletion', 'native'),
        ('confirm_shared_deletion', 'assistant'),
    ]
    if mode == 'transfer':
        params.append(('transfer_to', 'lark'))
    result = api.browser.delete('/api/v1/users/fern', params=params)
    assert_console_refusal(api, result)


def test_plain_delete_defaults_to_delete(api):
    result = api.browser.delete('/api/v1/users/fern')
    assert_console_refusal(api, result)


@pytest.mark.parametrize('params', [{'mode': 'unknown'}, {'archive_retention_days': 0}])
def test_invalid_queries_never_call_offboarding(api, params):
    result = api.browser.delete('/api/v1/users/fern', params=params)
    assert_console_refusal(api, result)


@pytest.mark.parametrize('api', ['platform'], indirect=True)
@pytest.mark.parametrize('actor,role', [('rook', 'admin'), ('fern', 'user'), ('elm', 'user')])
def test_platform_refusal_does_not_depend_on_actor_or_target(api, actor, role, monkeypatch):
    api.actor.id, api.actor.role = actor, role
    read_user = AsyncMock(side_effect=AssertionError('Console refusal must precede target access'))
    monkeypatch.setattr(users.Users, 'get_user_by_id', read_user)
    for target in ('fern', 'missing'):
        assert_console_refusal(api, api.browser.delete(f'/api/v1/users/{target}'))
        assert_console_refusal(api, api.browser.get(f'/api/v1/users/{target}/shared-data'))
    read_user.assert_not_awaited()


@pytest.mark.parametrize('api', ['platform'], indirect=True)
def test_retired_offboarding_is_absent_without_authentication(api):
    del api.app.dependency_overrides[get_current_user]
    response = api.browser.delete('/api/v1/users/fern')
    assert response.status_code == 404
    api.offboard.assert_not_awaited()
