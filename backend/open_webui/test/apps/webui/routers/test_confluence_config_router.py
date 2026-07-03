"""Unit tests for the Confluence config endpoint (``POST /api/v1/configs/confluence``).

Focused on the *mode-switch guard*: switching Confluence away from the pre-synced
``shared`` mode to ``per_user`` must be blocked with a 400 while a shared KB is
still provisioned (otherwise the pure config write orphans it). The router is
mounted on a minimal FastAPI app; ``get_admin_user`` is overridden and
``find_shared_kb`` is patched, so these run without a database or the full app
(mirrors ``test_topdesk_sync_router``). The router reads/writes settings through
the per-key Config API (``Config.get_many``/``Config.upsert`` on ``confluence.*``
storage keys), so the ``store`` fixture swaps those for an in-memory dict.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import configs
from open_webui.utils.auth import get_admin_user

_DETAIL = 'Delete the shared Confluence knowledge base before switching to on-request (per-user) mode.'

# Per-key seed mirroring the old namespace fixture (dotted storage keys of the
# CONFLUENCE_* fields, per CONFLUENCE_CONFIG_KEYS in routers/configs.py).
_STORE_SEED = {
    'confluence.enable': True,
    'confluence.enable_sync': False,
    'confluence.client_id': 'client',
    'confluence.client_secret': 'secret',
    'confluence.sync_interval_minutes': 60,
    'confluence.max_pages_per_sync': 500,
    'confluence.auth_mode': 'oauth',
    'confluence.site_url': 'https://tenant.atlassian.net',
    'confluence.basic_auth_username': 'user@example.com',
    'confluence.basic_auth_api_token': 'token',
    'confluence.scoped_api_token': 'scoped-token',
    'confluence.cloud_id': '',
    'confluence.kb_mode': 'shared',
}


@pytest.fixture
def store(monkeypatch) -> dict:
    """In-memory per-key Config store: reads and upserts land here, not in a DB."""
    values = dict(_STORE_SEED)

    async def fake_get_many(*keys):
        return {key: values[key] for key in keys if key in values}

    async def fake_upsert(updates):
        values.update(updates)

    monkeypatch.setattr(configs.Config, 'get_many', staticmethod(fake_get_many))
    monkeypatch.setattr(configs.Config, 'upsert', staticmethod(fake_upsert))
    return values


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(configs.router, prefix='/api/v1/configs')
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
        id='admin-1', role='admin', email='admin@example.com'
    )
    return app


def test_switch_to_per_user_blocked_when_shared_kb_exists(store):
    # Leaving shared → per_user while a shared KB is provisioned must 400 and
    # must NOT persist any field (the mode stays 'shared').
    app = _make_app()
    client = TestClient(app)
    fake_kb = SimpleNamespace(id='kb-1', user_id='owner-1', meta={'confluence_sync': {'shared': True}})

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=fake_kb),
    ) as find_mock:
        res = client.post(
            '/api/v1/configs/confluence',
            json={'CONFLUENCE_KB_MODE': 'per_user', 'CONFLUENCE_SITE_URL': 'https://changed.atlassian.net'},
        )

    assert res.status_code == 400
    assert res.json()['detail'] == _DETAIL
    find_mock.assert_awaited_once_with('confluence', 'confluence_sync')
    # Nothing was persisted — the guard raised before any write.
    assert store['confluence.kb_mode'] == 'shared'
    assert store['confluence.site_url'] == 'https://tenant.atlassian.net'


def test_stay_shared_never_blocked_even_with_shared_kb(store):
    # Saving with CONFLUENCE_KB_MODE='shared' must never block, even when a
    # shared KB exists (this is the normal edit-while-shared case).
    app = _make_app()
    client = TestClient(app)
    fake_kb = SimpleNamespace(id='kb-1', user_id='owner-1', meta={'confluence_sync': {'shared': True}})

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=fake_kb),
    ):
        res = client.post(
            '/api/v1/configs/confluence',
            json={'CONFLUENCE_KB_MODE': 'shared', 'CONFLUENCE_SYNC_INTERVAL_MINUTES': 30},
        )

    assert res.status_code == 200
    assert res.json()['CONFLUENCE_KB_MODE'] == 'shared'
    assert store['confluence.sync_interval_minutes'] == 30


def test_switch_to_per_user_allowed_when_no_shared_kb(store):
    # per_user with no shared KB provisioned is allowed and persists.
    app = _make_app()
    client = TestClient(app)

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=None),
    ) as find_mock:
        res = client.post(
            '/api/v1/configs/confluence',
            json={'CONFLUENCE_KB_MODE': 'per_user'},
        )

    assert res.status_code == 200
    assert res.json()['CONFLUENCE_KB_MODE'] == 'per_user'
    assert store['confluence.kb_mode'] == 'per_user'
    find_mock.assert_awaited_once_with('confluence', 'confluence_sync')


def test_basic_auth_coerces_per_user_to_shared(store):
    # Coupling ``basic ⇒ shared``: a basic-auth save with kb_mode='per_user' must
    # be coerced to 'shared' on write. Probed with NO shared KB so the coercion
    # is verified in isolation — switching the auth method *while* a shared KB
    # exists is separately blocked by the auth-switch guard (oauth test below).
    app = _make_app()
    client = TestClient(app)

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=None),
    ):
        res = client.post(
            '/api/v1/configs/confluence',
            json={'CONFLUENCE_AUTH_MODE': 'basic', 'CONFLUENCE_KB_MODE': 'per_user'},
        )

    assert res.status_code == 200
    assert res.json()['CONFLUENCE_AUTH_MODE'] == 'basic'
    assert res.json()['CONFLUENCE_KB_MODE'] == 'shared'
    assert store['confluence.auth_mode'] == 'basic'
    assert store['confluence.kb_mode'] == 'shared'


def test_scoped_auth_coerces_per_user_to_shared(store):
    # Coupling ``scoped ⇒ shared`` (same as basic): a scoped-auth save with
    # kb_mode='per_user' must be coerced to 'shared' and round-trip the scoped
    # token + cloud id. Probed with NO shared KB so the coercion is verified in
    # isolation (the auth-switch guard would otherwise block oauth→scoped).
    app = _make_app()
    client = TestClient(app)

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=None),
    ):
        res = client.post(
            '/api/v1/configs/confluence',
            json={
                'CONFLUENCE_AUTH_MODE': 'scoped',
                'CONFLUENCE_KB_MODE': 'per_user',
                'CONFLUENCE_SCOPED_API_TOKEN': 'scoped-secret',
                'CONFLUENCE_CLOUD_ID': 'cloud-abc',
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body['CONFLUENCE_AUTH_MODE'] == 'scoped'
    assert body['CONFLUENCE_KB_MODE'] == 'shared'
    assert body['CONFLUENCE_SCOPED_API_TOKEN'] == 'scoped-secret'
    assert body['CONFLUENCE_CLOUD_ID'] == 'cloud-abc'
    assert store['confluence.auth_mode'] == 'scoped'
    assert store['confluence.kb_mode'] == 'shared'


def test_scoped_auth_switch_blocked_when_shared_kb_exists(store):
    # The auth-switch guard treats scoped like any other method change: moving
    # oauth → scoped while a shared KB is provisioned must 400 (the KB's pages
    # were gathered under a different identity), mirroring the oauth↔basic guard.
    app = _make_app()
    client = TestClient(app)
    fake_kb = SimpleNamespace(id='kb-1', user_id='owner-1', meta={'confluence_sync': {'shared': True}})

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=fake_kb),
    ) as find_mock:
        res = client.post(
            '/api/v1/configs/confluence',
            json={'CONFLUENCE_AUTH_MODE': 'scoped', 'CONFLUENCE_SCOPED_API_TOKEN': 'scoped-secret'},
        )

    assert res.status_code == 400
    assert 'authentication method' in res.json()['detail']
    find_mock.assert_awaited_once_with('confluence', 'confluence_sync')
    # Nothing persisted — the guard raised before any write.
    assert store['confluence.auth_mode'] == 'oauth'


def test_oauth_per_user_still_blocked_when_shared_kb_exists(store):
    # The orphan guard stays intact for oauth: switching to per_user while a
    # shared KB exists must still 400 (coupling only forces shared for basic).
    app = _make_app()
    client = TestClient(app)
    fake_kb = SimpleNamespace(id='kb-1', user_id='owner-1', meta={'confluence_sync': {'shared': True}})

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=fake_kb),
    ) as find_mock:
        res = client.post(
            '/api/v1/configs/confluence',
            json={'CONFLUENCE_AUTH_MODE': 'oauth', 'CONFLUENCE_KB_MODE': 'per_user'},
        )

    assert res.status_code == 400
    assert res.json()['detail'] == _DETAIL
    find_mock.assert_awaited_once_with('confluence', 'confluence_sync')
    # Nothing persisted — the guard raised before any write.
    assert store['confluence.kb_mode'] == 'shared'


def test_oauth_per_user_allowed_when_no_shared_kb(store):
    # oauth + per_user with no shared KB is unaffected by the coupling: it
    # persists as per_user.
    app = _make_app()
    client = TestClient(app)

    with patch(
        'open_webui.services.sync.shared_kb.find_shared_kb',
        AsyncMock(return_value=None),
    ) as find_mock:
        res = client.post(
            '/api/v1/configs/confluence',
            json={'CONFLUENCE_AUTH_MODE': 'oauth', 'CONFLUENCE_KB_MODE': 'per_user'},
        )

    assert res.status_code == 200
    assert res.json()['CONFLUENCE_AUTH_MODE'] == 'oauth'
    assert res.json()['CONFLUENCE_KB_MODE'] == 'per_user'
    assert store['confluence.auth_mode'] == 'oauth'
    assert store['confluence.kb_mode'] == 'per_user'
    find_mock.assert_awaited_once_with('confluence', 'confluence_sync')


def test_site_url_with_wiki_suffix_is_normalized_on_save(store):
    # An admin-pasted URL that includes the /wiki context path must be stored as
    # scheme://host so the client does not double up into .../wiki/wiki/... → 404.
    app = _make_app()
    client = TestClient(app)
    res = client.post(
        '/api/v1/configs/confluence',
        json={'CONFLUENCE_SITE_URL': 'https://tenant.atlassian.net/wiki'},
    )
    assert res.status_code == 200
    assert store['confluence.site_url'] == 'https://tenant.atlassian.net'
    assert res.json()['CONFLUENCE_SITE_URL'] == 'https://tenant.atlassian.net'


def test_site_url_deep_link_is_normalized_on_save(store):
    # A deep link copied from the browser must also collapse to scheme://host.
    app = _make_app()
    client = TestClient(app)
    res = client.post(
        '/api/v1/configs/confluence',
        json={'CONFLUENCE_SITE_URL': 'https://tenant.atlassian.net/wiki/spaces/ENG/overview'},
    )
    assert res.status_code == 200
    assert store['confluence.site_url'] == 'https://tenant.atlassian.net'
