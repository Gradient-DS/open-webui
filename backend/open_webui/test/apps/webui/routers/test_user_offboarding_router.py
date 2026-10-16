"""Admin offboarding query parameters reach the typed service boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import users


@pytest.fixture
def api(monkeypatch):
    app = FastAPI()
    app.include_router(users.router, prefix='/api/v1/users')
    app.dependency_overrides[users.get_admin_user] = lambda: SimpleNamespace(id='rook', role='admin')
    app.dependency_overrides[users.get_async_session] = lambda: SimpleNamespace()
    monkeypatch.setattr(users.Users, 'get_first_user', AsyncMock(return_value=SimpleNamespace(id='primary')))
    offboard = AsyncMock(return_value=1)
    monkeypatch.setattr(users, 'offboard_user', offboard)
    monkeypatch.setattr(users, 'disconnect_user_sessions', AsyncMock())
    monkeypatch.setattr(users, 'publish_event', AsyncMock())
    with TestClient(app) as browser:
        yield SimpleNamespace(browser=browser, offboard=offboard)


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
    assert result.status_code == 200
    options = api.offboard.await_args.kwargs['options']
    assert options.mode == mode
    assert options.transfer_to == ('lark' if mode == 'transfer' else None)
    assert options.archive_retention_days == 8
    assert options.never_delete
    assert options.confirm_shared_deletion == ['native', 'assistant']


def test_plain_delete_defaults_to_delete(api):
    assert api.browser.delete('/api/v1/users/fern').status_code == 200
    assert api.offboard.await_args.kwargs['options'].mode == 'delete'


@pytest.mark.parametrize('params', [{'mode': 'unknown'}, {'archive_retention_days': 0}])
def test_invalid_queries_never_call_offboarding(api, params):
    assert api.browser.delete('/api/v1/users/fern', params=params).status_code == 422
    api.offboard.assert_not_awaited()
