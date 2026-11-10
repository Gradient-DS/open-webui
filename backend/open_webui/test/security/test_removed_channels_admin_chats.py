"""Retired content routes cannot be reopened by administrator settings."""

import json
import re
import tomllib
from pathlib import Path

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
    assert '/api/v1/users/' not in paths
    assert '/api/v1/chats/search' in paths


@pytest.mark.parametrize('setting', ['ENABLE_CHANNELS', 'ENABLE_ADMIN_CHAT_ACCESS'])
def test_removed_settings_cannot_restore_routes(monkeypatch, setting):
    monkeypatch.setenv(setting, 'true')
    test_removed_content_routes_are_not_registered()


def test_retired_management_operations_are_absent_from_runtime_and_contract():
    from open_webui.main import app

    root = Path(__file__).resolve().parents[4]
    surface = tomllib.loads((root / 'security/attack-surface.toml').read_text())
    retired = set(surface['removed']['routes'])
    contract = json.loads((root / 'security/openapi.json').read_text())
    published = {f'{method.upper()} {path}' for path, item in contract['paths'].items() for method in item}
    runtime = {
        f'{method} {re.sub(":path}", "}", route.path)}'
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert retired and surface['removed']['reason']
    assert not retired & published
    assert not retired & runtime
    assert 'GET /api/v1/auths/console-access' in published & runtime
