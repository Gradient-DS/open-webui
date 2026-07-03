"""Unit tests for the Confluence config endpoint (``POST /api/v1/configs/confluence``).

Focused on the *mode-switch guard*: switching Confluence away from the pre-synced
``shared`` mode to ``per_user`` must be blocked with a 400 while a shared KB is
still provisioned (otherwise the pure config write orphans it). The router is
mounted on a minimal FastAPI app; ``get_admin_user`` is overridden and
``find_shared_kb`` is patched, so these run without a database or the full app
(mirrors ``test_topdesk_sync_router``). ``app.state.config`` is a plain namespace
because ``set_confluence_config`` only reads/writes attributes on it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import configs
from open_webui.utils.auth import get_admin_user

_DETAIL = 'Delete the shared Confluence knowledge base before switching to on-request (per-user) mode.'


def _make_config() -> SimpleNamespace:
    # Mimic the PersistentConfig-backed app.state.config: the router reads and
    # writes these as plain attributes, so a namespace is sufficient.
    return SimpleNamespace(
        ENABLE_CONFLUENCE_INTEGRATION=True,
        ENABLE_CONFLUENCE_SYNC=False,
        CONFLUENCE_OAUTH_CLIENT_ID='client',
        CONFLUENCE_OAUTH_CLIENT_SECRET='secret',
        CONFLUENCE_SYNC_INTERVAL_MINUTES=60,
        CONFLUENCE_MAX_PAGES_PER_SYNC=500,
        CONFLUENCE_AUTH_MODE='oauth',
        CONFLUENCE_SITE_URL='https://tenant.atlassian.net',
        CONFLUENCE_BASIC_AUTH_USERNAME='user@example.com',
        CONFLUENCE_BASIC_AUTH_API_TOKEN='token',
        CONFLUENCE_SCOPED_API_TOKEN='scoped-token',
        CONFLUENCE_CLOUD_ID='',
        CONFLUENCE_KB_MODE='shared',
    )


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(configs.router, prefix='/api/v1/configs')
    app.state.config = _make_config()
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
        id='admin-1', role='admin', email='admin@example.com'
    )
    return app


def test_switch_to_per_user_blocked_when_shared_kb_exists():
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
    assert app.state.config.CONFLUENCE_KB_MODE == 'shared'
    assert app.state.config.CONFLUENCE_SITE_URL == 'https://tenant.atlassian.net'


def test_stay_shared_never_blocked_even_with_shared_kb():
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
    assert app.state.config.CONFLUENCE_SYNC_INTERVAL_MINUTES == 30


def test_switch_to_per_user_allowed_when_no_shared_kb():
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
    assert app.state.config.CONFLUENCE_KB_MODE == 'per_user'
    find_mock.assert_awaited_once_with('confluence', 'confluence_sync')


def test_basic_auth_coerces_per_user_to_shared():
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
    assert app.state.config.CONFLUENCE_AUTH_MODE == 'basic'
    assert app.state.config.CONFLUENCE_KB_MODE == 'shared'


def test_scoped_auth_coerces_per_user_to_shared():
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
    assert app.state.config.CONFLUENCE_AUTH_MODE == 'scoped'
    assert app.state.config.CONFLUENCE_KB_MODE == 'shared'


def test_scoped_auth_switch_blocked_when_shared_kb_exists():
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
    assert app.state.config.CONFLUENCE_AUTH_MODE == 'oauth'


def test_oauth_per_user_still_blocked_when_shared_kb_exists():
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
    assert app.state.config.CONFLUENCE_KB_MODE == 'shared'


def test_oauth_per_user_allowed_when_no_shared_kb():
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
    assert app.state.config.CONFLUENCE_AUTH_MODE == 'oauth'
    assert app.state.config.CONFLUENCE_KB_MODE == 'per_user'
    find_mock.assert_awaited_once_with('confluence', 'confluence_sync')


def test_site_url_with_wiki_suffix_is_normalized_on_save():
    # An admin-pasted URL that includes the /wiki context path must be stored as
    # scheme://host so the client does not double up into .../wiki/wiki/... → 404.
    app = _make_app()
    client = TestClient(app)
    res = client.post(
        '/api/v1/configs/confluence',
        json={'CONFLUENCE_SITE_URL': 'https://tenant.atlassian.net/wiki'},
    )
    assert res.status_code == 200
    assert app.state.config.CONFLUENCE_SITE_URL == 'https://tenant.atlassian.net'
    assert res.json()['CONFLUENCE_SITE_URL'] == 'https://tenant.atlassian.net'


def test_site_url_deep_link_is_normalized_on_save():
    # A deep link copied from the browser must also collapse to scheme://host.
    app = _make_app()
    client = TestClient(app)
    res = client.post(
        '/api/v1/configs/confluence',
        json={'CONFLUENCE_SITE_URL': 'https://tenant.atlassian.net/wiki/spaces/ENG/overview'},
    )
    assert res.status_code == 200
    assert app.state.config.CONFLUENCE_SITE_URL == 'https://tenant.atlassian.net'
