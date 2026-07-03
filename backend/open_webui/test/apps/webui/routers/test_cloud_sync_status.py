"""Unit tests for the cross-provider cloud-sync status endpoint.

The router is mounted on a minimal FastAPI app and ``Knowledges`` is patched,
so these run without a database or the full application (mirrors the
``test_feedback_report`` precedent). Two concerns are covered:

  (a) admin-gating — a non-admin caller gets 401 via the real ``get_admin_user``
  (b) aggregation over a couple of synthetic Knowledge rows per provider

The pure aggregation helper is also tested directly.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import configs
from open_webui.routers.configs import router, _aggregate_provider_status
from open_webui.utils.auth import get_admin_user, get_current_user


def _make_app():
    app = FastAPI()
    app.include_router(router, prefix='/api/v1/configs')
    return app


def _admin_client(app):
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
        id='admin-1', role='admin', email='admin@example.com'
    )
    return TestClient(app)


# --- admin-gating -----------------------------------------------------------


def test_status_rejects_non_admin():
    # Override the *inner* dependency so the real get_admin_user runs its
    # role check and rejects a non-admin with 401.
    app = _make_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id='user-1', role='user', email='user@example.com'
    )
    res = TestClient(app).get('/api/v1/configs/cloud-sync/status')
    assert res.status_code == 401


# --- aggregation ------------------------------------------------------------


def _kb(kb_id, meta):
    return SimpleNamespace(id=kb_id, meta=meta)


def test_status_aggregates_per_provider():
    app = _make_app()
    client = _admin_client(app)

    # Two confluence KBs: one idle+shared, one syncing+suspended.
    confluence_kbs = [
        _kb(
            'kb-c1',
            {
                'confluence_sync': {
                    'status': 'completed',
                    'last_sync_at': 1000,
                    'shared': True,
                }
            },
        ),
        _kb(
            'kb-c2',
            {
                'confluence_sync': {
                    'status': 'syncing',
                    'last_sync_at': 2000,
                    'suspended_at': 1765400000,
                }
            },
        ),
    ]
    onedrive_kbs = [_kb('kb-o1', {'onedrive_sync': {'status': 'completed', 'last_sync_at': 500}})]
    # google_drive and topdesk: zero-state (no KBs).

    async def fake_get_by_type(provider_type, *a, **k):
        return {
            'confluence': confluence_kbs,
            'onedrive': onedrive_kbs,
            'google_drive': [],
            'topdesk': [],
        }[provider_type]

    async def fake_file_counts(ids, *a, **k):
        return {'kb-c1': 482, 'kb-c2': 10, 'kb-o1': 3}

    with patch('open_webui.models.knowledge.Knowledges') as mock_kb:
        mock_kb.get_knowledge_bases_by_type = AsyncMock(side_effect=fake_get_by_type)
        mock_kb.get_file_counts_by_knowledge_ids = AsyncMock(side_effect=fake_file_counts)
        res = client.get('/api/v1/configs/cloud-sync/status')

    assert res.status_code == 200
    body = res.json()

    assert set(body.keys()) == {'confluence', 'google_drive', 'onedrive', 'topdesk'}

    conf = body['confluence']
    assert conf['kb_count'] == 2
    assert conf['file_count'] == 492  # 482 + 10
    assert conf['last_sync_at'] == 2000  # max across KBs
    assert conf['syncing'] is True
    assert conf['status'] == 'syncing'
    assert conf['suspended_count'] == 1
    assert conf['shared'] is True

    one = body['onedrive']
    assert one['kb_count'] == 1
    assert one['file_count'] == 3
    assert one['syncing'] is False
    assert one['status'] == 'idle'
    assert one['suspended_count'] == 0
    assert one['shared'] is False

    zero_state = {
        'kb_count': 0,
        'file_count': 0,
        'last_sync_at': None,
        'status': 'idle',
        'syncing': False,
        'suspended_count': 0,
        'shared': False,
    }
    assert body['google_drive'] == zero_state
    # topdesk has no KBs → zero-state entry present in the payload.
    assert body['topdesk'] == zero_state


# --- /configs/topdesk admin-gating + normalization --------------------------


def _patch_topdesk_store(monkeypatch) -> dict:
    """In-memory per-key Config store for the topdesk endpoints.

    The router reads/writes via ``Config.get_many``/``Config.upsert`` on the
    ``topdesk.*`` storage keys (TOPDESK_CONFIG_KEYS in routers/configs.py);
    reads and upserts land in this dict, never in a DB.
    """
    values = dict(_TOPDESK_STORE_SEED)

    async def fake_get_many(*keys):
        return {key: values[key] for key in keys if key in values}

    async def fake_upsert(updates):
        values.update(updates)

    monkeypatch.setattr(configs.Config, 'get_many', staticmethod(fake_get_many))
    monkeypatch.setattr(configs.Config, 'upsert', staticmethod(fake_upsert))
    return values


def _admin_override(app) -> TestClient:
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
        id='admin-1', role='admin', email='admin@example.com'
    )
    return TestClient(app)


# Dotted storage-key seed mirroring the old TOPDESK_* namespace defaults.
_TOPDESK_STORE_SEED = {
    'topdesk.enable': False,
    'topdesk.enable_sync': False,
    'topdesk.url': '',
    'topdesk.username': '',
    'topdesk.app_password': '',
    'topdesk.sync_interval_minutes': 60,
    'topdesk.max_items_per_sync': 500,
    'topdesk.sync_scope': 'ssp',
}

# Friendly field names the GET endpoint returns for the seeded keys.
_TOPDESK_FIELDS = {
    'ENABLE_TOPDESK_INTEGRATION',
    'ENABLE_TOPDESK_SYNC',
    'TOPDESK_URL',
    'TOPDESK_USERNAME',
    'TOPDESK_APP_PASSWORD',
    'TOPDESK_SYNC_INTERVAL_MINUTES',
    'TOPDESK_MAX_ITEMS_PER_SYNC',
    'TOPDESK_SYNC_SCOPE',
}


def test_topdesk_get_rejects_non_admin():
    app = _make_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id='user-1', role='user', email='user@example.com'
    )
    res = TestClient(app).get('/api/v1/configs/topdesk')
    assert res.status_code == 401


def test_topdesk_post_rejects_non_admin():
    app = _make_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id='user-1', role='user', email='user@example.com'
    )
    res = TestClient(app).post('/api/v1/configs/topdesk', json={})
    assert res.status_code == 401


def test_topdesk_get_returns_all_values(monkeypatch):
    app = _make_app()
    _patch_topdesk_store(monkeypatch)
    client = _admin_override(app)
    res = client.get('/api/v1/configs/topdesk')
    assert res.status_code == 200
    assert set(res.json().keys()) == _TOPDESK_FIELDS


def test_topdesk_post_normalizes_url_and_clamps_max(monkeypatch):
    app = _make_app()
    store = _patch_topdesk_store(monkeypatch)
    client = _admin_override(app)

    res = client.post(
        '/api/v1/configs/topdesk',
        json={
            'ENABLE_TOPDESK_INTEGRATION': True,
            'TOPDESK_URL': '  https://tenant.topdesk.net/  ',
            'TOPDESK_USERNAME': '  operator  ',
            'TOPDESK_APP_PASSWORD': '  secret  ',
            'TOPDESK_MAX_ITEMS_PER_SYNC': -5,
        },
    )
    assert res.status_code == 200
    body = res.json()
    # URL stripped + trailing slash removed.
    assert body['TOPDESK_URL'] == 'https://tenant.topdesk.net'
    assert store['topdesk.url'] == 'https://tenant.topdesk.net'
    # Credentials stripped and round-tripped in full (Confluence disclosure profile).
    assert body['TOPDESK_USERNAME'] == 'operator'
    assert body['TOPDESK_APP_PASSWORD'] == 'secret'
    # Negative max clamped to 0 (unlimited).
    assert body['TOPDESK_MAX_ITEMS_PER_SYNC'] == 0
    assert body['ENABLE_TOPDESK_INTEGRATION'] is True


# --- pure helper ------------------------------------------------------------


def test_aggregate_empty_is_zero_state():
    assert _aggregate_provider_status([]) == {
        'kb_count': 0,
        'file_count': 0,
        'last_sync_at': None,
        'status': 'idle',
        'syncing': False,
        'suspended_count': 0,
        'shared': False,
    }


def test_aggregate_picks_max_last_sync_and_any_flags():
    out = _aggregate_provider_status(
        [
            {'_file_count': 5, 'status': 'completed', 'last_sync_at': 100},
            {
                '_file_count': 7,
                'status': 'syncing',
                'last_sync_at': 300,
                'shared': True,
            },
            {
                '_file_count': 0,
                'status': 'failed',
                'last_sync_at': 200,
                'suspended_at': 999,
            },
        ]
    )
    assert out['kb_count'] == 3
    assert out['file_count'] == 12
    assert out['last_sync_at'] == 300
    assert out['syncing'] is True
    assert out['status'] == 'syncing'
    assert out['suspended_count'] == 1
    assert out['shared'] is True
