"""Unit tests for the TOPdesk operational router (``/api/v1/topdesk/*``).

The router is mounted on a minimal FastAPI app; the TOPdesk client, provider,
and shared-KB helpers are patched, so these run without a database, network, or
the full application (mirrors ``test_cloud_sync_status`` / ``test_feedback_report``).

Coverage:
  (a) admin-gating — every endpoint rejects a non-admin via the real ``get_admin_user``
  (b) ``POST /auth/test`` — probe success / TopdeskAuthError / TopdeskTransientError
  (c) ``GET /browse/items`` — root vs children, response shape
  (d) shared/* — status / provision / delete delegate to the shared helpers with
      the TOPdesk provider params
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers.topdesk_sync import _run_shared_sync, router
from open_webui.services.topdesk.topdesk_client import (
    TopdeskAuthError,
    TopdeskGraphQLError,
    TopdeskTransientError,
)
from open_webui.utils.auth import get_admin_user, get_current_user


def _make_app():
    app = FastAPI()
    app.include_router(router, prefix='/api/v1/topdesk')
    return app


def _admin_client(app):
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
        id='admin-1', role='admin', email='admin@example.com'
    )
    return TestClient(app)


def _non_admin_client(app):
    # Override the *inner* dependency so the real get_admin_user runs its role
    # check and rejects a non-admin with 401.
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id='user-1', role='user', email='user@example.com'
    )
    return TestClient(app)


# --- admin-gating -----------------------------------------------------------


def test_every_endpoint_rejects_non_admin():
    app = _make_app()
    client = _non_admin_client(app)

    assert client.post('/api/v1/topdesk/auth/test', json={}).status_code == 401
    assert client.get('/api/v1/topdesk/browse/items').status_code == 401
    assert client.get('/api/v1/topdesk/shared/status').status_code == 401
    assert client.post('/api/v1/topdesk/shared/provision', json={'items': []}).status_code == 401
    assert client.post('/api/v1/topdesk/shared/sync').status_code == 401
    assert client.delete('/api/v1/topdesk/shared').status_code == 401


# --- POST /auth/test --------------------------------------------------------


def _patch_test_creds():
    """Patch the stored-config reads so a blank submitted credential resolves.

    The router reads ``TOPDESK_URL``/``TOPDESK_USERNAME``/``TOPDESK_APP_PASSWORD``
    ``.value`` for fallback; give them usable defaults so a body with only the
    application password (or nothing) still has a URL.
    """
    return patch.multiple(
        'open_webui.routers.topdesk_sync',
        TOPDESK_URL=SimpleNamespace(value='https://tenant.topdesk.net'),
        TOPDESK_USERNAME=SimpleNamespace(value='operator'),
        TOPDESK_APP_PASSWORD=SimpleNamespace(value='stored-secret'),
    )


def test_auth_test_probe_success():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.probe = AsyncMock(return_value={'ok': True, 'probe': 'operators/current'})
    fake_client.close = AsyncMock()

    with _patch_test_creds(), patch('open_webui.routers.topdesk_sync.TopdeskClient', return_value=fake_client):
        res = client.post('/api/v1/topdesk/auth/test', json={'app_password': 'typed-secret'})

    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is True
    assert 'successful' in body['detail'].lower()
    fake_client.close.assert_awaited()


def test_auth_test_auth_error_maps_ok_false():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.probe = AsyncMock(side_effect=TopdeskAuthError('401'))
    fake_client.close = AsyncMock()

    with _patch_test_creds(), patch('open_webui.routers.topdesk_sync.TopdeskClient', return_value=fake_client):
        res = client.post('/api/v1/topdesk/auth/test', json={})

    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is False
    assert 'authentication failed' in body['detail'].lower()


def test_auth_test_transient_error_maps_ok_false():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.probe = AsyncMock(side_effect=TopdeskTransientError('5xx', status_code=503))
    fake_client.close = AsyncMock()

    with _patch_test_creds(), patch('open_webui.routers.topdesk_sync.TopdeskClient', return_value=fake_client):
        res = client.post('/api/v1/topdesk/auth/test', json={})

    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is False
    assert 'unavailable' in body['detail'].lower()


def test_auth_test_missing_creds_returns_ok_false():
    app = _make_app()
    client = _admin_client(app)

    # No stored config and no submitted creds → required-fields message.
    with patch.multiple(
        'open_webui.routers.topdesk_sync',
        TOPDESK_URL=SimpleNamespace(value=''),
        TOPDESK_USERNAME=SimpleNamespace(value=''),
        TOPDESK_APP_PASSWORD=SimpleNamespace(value=''),
    ):
        res = client.post('/api/v1/topdesk/auth/test', json={})

    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is False
    assert 'required' in body['detail'].lower()


# --- GET /browse/items ------------------------------------------------------

_ROOT_NODES = [
    {'id': 'i1', 'number': 'KI-1', 'title': 'Root One', 'status': 'PUBLISHED'},
    {'id': 'i2', 'number': 'KI-2', 'title': 'Root Two', 'status': 'PUBLISHED', 'children': []},
]
_CHILD_NODES = [
    {'id': 'c1', 'number': 'KI-9', 'title': 'Child One', 'status': 'DRAFT'},
]


def test_browse_items_root():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.list_root_items = AsyncMock(return_value=_ROOT_NODES)
    fake_client.list_item_children = AsyncMock(return_value=[])
    fake_client.close = AsyncMock()

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.build_client', new_callable=AsyncMock, return_value=fake_client),
    ):
        res = client.get('/api/v1/topdesk/browse/items')

    assert res.status_code == 200
    items = res.json()['items']
    assert [i['id'] for i in items] == ['i1', 'i2']
    assert items[0] == {
        'id': 'i1',
        'name': 'Root One',
        'number': 'KI-1',
        'has_children': True,  # unknown without an extra fetch → expand affordance
        'status': 'PUBLISHED',
    }
    # An explicit empty children array collapses the node.
    assert items[1]['has_children'] is False
    fake_client.list_root_items.assert_awaited_once()
    fake_client.list_item_children.assert_not_awaited()


def test_browse_items_children():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.list_root_items = AsyncMock(return_value=[])
    fake_client.list_item_children = AsyncMock(return_value=_CHILD_NODES)
    fake_client.close = AsyncMock()

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.build_client', new_callable=AsyncMock, return_value=fake_client),
    ):
        res = client.get('/api/v1/topdesk/browse/items', params={'parent_id': 'i1'})

    assert res.status_code == 200
    items = res.json()['items']
    assert len(items) == 1
    assert items[0]['id'] == 'c1'
    assert items[0]['name'] == 'Child One'
    fake_client.list_item_children.assert_awaited_once_with('i1')
    fake_client.list_root_items.assert_not_awaited()


def test_browse_items_unconfigured_returns_400():
    app = _make_app()
    client = _admin_client(app)

    with patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=False):
        res = client.get('/api/v1/topdesk/browse/items')

    assert res.status_code == 400


def test_browse_items_transient_maps_503():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.list_root_items = AsyncMock(side_effect=TopdeskTransientError('5xx', status_code=503))
    fake_client.close = AsyncMock()

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.build_client', new_callable=AsyncMock, return_value=fake_client),
    ):
        res = client.get('/api/v1/topdesk/browse/items')

    assert res.status_code == 503


def test_browse_items_transient_429_maps_502():
    # A 429 rate-limit is the one transient sub-branch that maps to 502 (not 503).
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.list_root_items = AsyncMock(side_effect=TopdeskTransientError('429', status_code=429))
    fake_client.close = AsyncMock()

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.build_client', new_callable=AsyncMock, return_value=fake_client),
    ):
        res = client.get('/api/v1/topdesk/browse/items')

    assert res.status_code == 502
    fake_client.close.assert_awaited()


def test_browse_items_graphql_error_maps_502():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.list_root_items = AsyncMock(side_effect=TopdeskGraphQLError('bad query', [{'message': 'bad query'}]))
    fake_client.close = AsyncMock()

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.build_client', new_callable=AsyncMock, return_value=fake_client),
    ):
        res = client.get('/api/v1/topdesk/browse/items')

    assert res.status_code == 502
    fake_client.close.assert_awaited()


def test_browse_items_connection_error_maps_502():
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.list_root_items = AsyncMock(side_effect=ConnectionError('connection refused'))
    fake_client.close = AsyncMock()

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.build_client', new_callable=AsyncMock, return_value=fake_client),
    ):
        res = client.get('/api/v1/topdesk/browse/items')

    assert res.status_code == 502
    fake_client.close.assert_awaited()


def test_browse_items_unexpected_error_maps_502():
    # An unexpected error (not a mapped TOPdesk exception) must now surface as a
    # clean 502 via the catch-all, never a raw 500 + stacktrace to the admin.
    app = _make_app()
    client = _admin_client(app)

    fake_client = MagicMock()
    fake_client.list_root_items = AsyncMock(side_effect=RuntimeError('unexpected boom'))
    fake_client.close = AsyncMock()

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.build_client', new_callable=AsyncMock, return_value=fake_client),
    ):
        res = client.get('/api/v1/topdesk/browse/items')

    assert res.status_code == 502
    # The client is still closed even on an unexpected failure.
    fake_client.close.assert_awaited()


# --- shared/* delegation ----------------------------------------------------


def test_shared_status_delegates_with_topdesk_params():
    app = _make_app()
    client = _admin_client(app)

    status_mock = AsyncMock(return_value={'provisioned': False, 'knowledge_id': None})

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.shared_kb_status_generic', status_mock),
    ):
        res = client.get('/api/v1/topdesk/shared/status')

    assert res.status_code == 200
    body = res.json()
    assert body['credential_configured'] is True
    assert body['provisioned'] is False
    status_mock.assert_awaited_once_with('topdesk', 'topdesk_sync', items_key='items')


def test_shared_provision_delegates_with_topdesk_params():
    app = _make_app()
    client = _admin_client(app)

    provision_mock = AsyncMock(return_value=SimpleNamespace(id='kb-1', user_id=''))
    status_mock = AsyncMock(return_value={'provisioned': True, 'knowledge_id': 'kb-1'})

    with (
        patch('open_webui.routers.topdesk_sync.service_auth_configured', new_callable=AsyncMock, return_value=True),
        patch('open_webui.routers.topdesk_sync.provision_shared_kb_generic', provision_mock),
        patch('open_webui.routers.topdesk_sync.shared_kb_status_generic', status_mock),
    ):
        res = client.post(
            '/api/v1/topdesk/shared/provision',
            json={
                'items': [
                    {'type': 'folder', 'item_id': 'i1', 'name': 'Root', 'include_descendants': True},
                    {'type': 'file', 'item_id': '  ', 'name': 'blank'},  # blank id is dropped
                ],
                'owner_user_id': '',
            },
        )

    assert res.status_code == 200
    provision_mock.assert_awaited_once()
    kwargs = provision_mock.await_args.kwargs
    assert kwargs['provider_type'] == 'topdesk'
    assert kwargs['meta_key'] == 'topdesk_sync'
    assert kwargs['items_key'] == 'items'
    assert kwargs['owner_id'] == ''
    # The blank-id entry is filtered out; only the valid folder item survives.
    assert kwargs['selected_items'] == [
        {'type': 'folder', 'item_id': 'i1', 'name': 'Root', 'include_descendants': True}
    ]


def test_shared_delete_delegates_and_404_when_absent():
    app = _make_app()
    client = _admin_client(app)

    # Present → returns the deleted id.
    with patch('open_webui.routers.topdesk_sync.delete_shared_kb_generic', AsyncMock(return_value='kb-1')):
        res = client.delete('/api/v1/topdesk/shared')
    assert res.status_code == 200
    assert res.json()['knowledge_id'] == 'kb-1'

    # Absent → 404.
    with patch('open_webui.routers.topdesk_sync.delete_shared_kb_generic', AsyncMock(return_value=None)):
        res = client.delete('/api/v1/topdesk/shared')
    assert res.status_code == 404


def test_shared_sync_404_when_not_provisioned():
    app = _make_app()
    client = _admin_client(app)

    with patch('open_webui.routers.topdesk_sync.find_shared_kb', AsyncMock(return_value=None)):
        res = client.post('/api/v1/topdesk/shared/sync')
    assert res.status_code == 404


def test_shared_sync_schedules_background_task():
    app = _make_app()
    client = _admin_client(app)

    kb = SimpleNamespace(id='kb-1', user_id='owner-1')

    with (
        patch('open_webui.routers.topdesk_sync.find_shared_kb', AsyncMock(return_value=kb)),
        patch('open_webui.routers.topdesk_sync._run_shared_sync', AsyncMock()),
    ):
        res = client.post('/api/v1/topdesk/shared/sync')

    assert res.status_code == 200
    assert res.json()['knowledge_id'] == 'kb-1'


# --- _run_shared_sync (background task) --------------------------------------
#
# Driven via asyncio.run (no pytest-asyncio dependency), matching the TOPdesk
# service-test convention; this file otherwise exercises the router through the
# synchronous FastAPI TestClient.


def test_run_shared_sync_happy_path_awaits_execute_sync():
    # The background task resolves the TOPdesk provider and awaits execute_sync
    # with the knowledge_id / user_id / app it was handed.
    provider = MagicMock()
    provider.execute_sync = AsyncMock()
    app = SimpleNamespace(name='fake-app')

    with patch('open_webui.services.sync.provider.get_sync_provider', return_value=provider) as get_provider:
        asyncio.run(_run_shared_sync(knowledge_id='kb-1', user_id='owner-1', app=app))

    get_provider.assert_called_once_with('topdesk')
    provider.execute_sync.assert_awaited_once_with(knowledge_id='kb-1', user_id='owner-1', app=app)


def test_run_shared_sync_swallows_and_logs_on_error():
    # A failure resolving/executing the provider must be logged and swallowed —
    # the background task must never propagate (matches Confluence's behaviour).
    with (
        patch(
            'open_webui.services.sync.provider.get_sync_provider',
            side_effect=RuntimeError('no such provider'),
        ),
        patch('open_webui.routers.topdesk_sync.log') as log_mock,
    ):
        # Does not raise.
        asyncio.run(_run_shared_sync(knowledge_id='kb-1', user_id='owner-1', app=SimpleNamespace()))

    log_mock.exception.assert_called_once()
