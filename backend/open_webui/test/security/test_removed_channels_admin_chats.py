"""Retired content routes cannot be reopened by administrator settings."""

import pytest
from fastapi.routing import APIRoute

REMOVED = {
    '/api/v1/chats/all/db',
    '/api/v1/chats/list/user/{user_id}',
    '/api/v1/analytics/messages',
    '/api/v1/analytics/models/{model_id:path}/chats',
    '/api/v1/utils/db/download',
    '/api/v1/utils/db/export',
}


def test_removed_content_routes_are_not_registered():
    from open_webui.main import app
    from open_webui.socket.main import sio

    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert not REMOVED & paths
    assert not any(path.startswith('/api/v1/channels') for path in paths)
    assert not {'join-channels', 'events:channel'} & sio.handlers.get('/', {}).keys()
    assert '/api/v1/users/' in paths
    assert '/api/v1/chats/search' in paths


@pytest.mark.parametrize('setting', ['ENABLE_CHANNELS', 'ENABLE_ADMIN_CHAT_ACCESS'])
def test_removed_settings_cannot_restore_routes(monkeypatch, setting):
    monkeypatch.setenv(setting, 'true')
    test_removed_content_routes_are_not_registered()
