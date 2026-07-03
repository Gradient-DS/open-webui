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
    # google_drive: zero-state (no KBs).

    async def fake_get_by_type(provider_type, *a, **k):
        return {
            'confluence': confluence_kbs,
            'onedrive': onedrive_kbs,
            'google_drive': [],
        }[provider_type]

    async def fake_file_counts(ids, *a, **k):
        return {'kb-c1': 482, 'kb-c2': 10, 'kb-o1': 3}

    with patch('open_webui.models.knowledge.Knowledges') as mock_kb:
        mock_kb.get_knowledge_bases_by_type = AsyncMock(side_effect=fake_get_by_type)
        mock_kb.get_file_counts_by_knowledge_ids = AsyncMock(side_effect=fake_file_counts)
        res = client.get('/api/v1/configs/cloud-sync/status')

    assert res.status_code == 200
    body = res.json()

    assert set(body.keys()) == {'confluence', 'google_drive', 'onedrive'}

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
    # google_drive has no KBs → zero-state entry present in the payload.
    assert body['google_drive'] == zero_state


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
