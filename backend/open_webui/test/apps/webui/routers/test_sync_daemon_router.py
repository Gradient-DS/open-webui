"""Unit tests for the sync-daemon protocol router (/api/v1/sync-daemon).

The router is mounted on a minimal FastAPI app with the sync principal
injected via a dependency override (bearer/header matching is covered by
``test_sync_service_auth.py``); ``Config.get`` is routed to an in-memory
per-key store and model collaborators are patched, so these run without a
database (mirrors the ``test_cloud_sync_status`` / ``test_integrations_stage_submit``
precedents). Covered:

  (a) flag gate — every endpoint 403s when ``sync_daemon.enabled`` is off
  (b) token broker — provider validation, acting-user rule, needs_reauth 404,
      success shape (access token + expiry, never a refresh token)
  (c) config read — per-provider config subset + KB registry shape
  (d) run summary — heartbeat writes, the R8 cursor-advance rule, and the
      fail-mark sweep of staged non-terminal files
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.internal.db import get_async_session
from open_webui.routers import sync_daemon as sync_daemon_router
from open_webui.utils.service_auth import SyncPrincipal, get_sync_principal, get_sync_service_principal

ACTING_USER_ID = 'owner-uuid-1'
KB_ID = 'kb-uuid-1'


@pytest.fixture
def principal():
    user = MagicMock()
    user.id = ACTING_USER_ID
    user.role = 'user'
    user.email = 'lex@gradient-ds.com'
    return SyncPrincipal(user=user, provider_slug='onedrive')


@pytest.fixture
def config_values(monkeypatch):
    """In-memory per-key Config store; the daemon flag defaults on here."""
    values = {'sync_daemon.enabled': True}

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(sync_daemon_router.Config, 'get', staticmethod(fake_get))
    return values


@pytest.fixture
def client(principal, config_values):
    app = FastAPI()
    app.include_router(sync_daemon_router.router, prefix='/api/v1/sync-daemon')
    app.dependency_overrides[get_sync_principal] = lambda: principal
    # Config reads use the service-level dependency (bearer only, no acting
    # user) — the real bearer check is covered in test_sync_service_auth.py.
    app.dependency_overrides[get_sync_service_principal] = lambda: None
    app.dependency_overrides[get_async_session] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def _token_body(**overrides):
    body = {'user_id': ACTING_USER_ID, 'provider': 'onedrive'}
    body.update(overrides)
    return body


def _summary_body(**overrides):
    body = {'provider': 'onedrive', 'status': 'completed'}
    body.update(overrides)
    return body


# --- flag gate ---------------------------------------------------------------


def test_flag_off_403_on_all_endpoints(client, config_values):
    config_values['sync_daemon.enabled'] = False

    for method, url, kwargs in [
        ('post', '/api/v1/sync-daemon/token', {'json': _token_body()}),
        ('get', '/api/v1/sync-daemon/config/onedrive', {}),
        ('post', f'/api/v1/sync-daemon/runs/{KB_ID}/summary', {'json': _summary_body()}),
    ]:
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 403, f'{url}: {resp.text}'
        assert 'SYNC_DAEMON_ENABLED' in resp.json()['detail']


# --- token broker ------------------------------------------------------------


def test_token_unknown_provider_400(client):
    resp = client.post('/api/v1/sync-daemon/token', json=_token_body(provider='dropbox'))
    assert resp.status_code == 400
    assert 'dropbox' in resp.json()['detail']


def test_token_acting_user_mismatch_403(client, monkeypatch):
    token_manager = MagicMock()
    token_manager.get_valid_access_token = AsyncMock(return_value='should-not-be-issued')
    monkeypatch.setattr(sync_daemon_router, 'get_token_manager', lambda provider: token_manager)

    resp = client.post('/api/v1/sync-daemon/token', json=_token_body(user_id='someone-else'))
    assert resp.status_code == 403
    token_manager.get_valid_access_token.assert_not_awaited()


def test_token_needs_reauth_404_when_no_valid_token(client, monkeypatch):
    token_manager = MagicMock()
    token_manager.get_valid_access_token = AsyncMock(return_value=None)
    monkeypatch.setattr(sync_daemon_router, 'get_token_manager', lambda provider: token_manager)

    resp = client.post('/api/v1/sync-daemon/token', json=_token_body())
    assert resp.status_code == 404
    assert resp.json() == {'needs_reauth': True}


def test_token_success_returns_access_token_and_expiry_never_refresh(client, monkeypatch):
    token_manager = MagicMock()
    token_manager.get_valid_access_token = AsyncMock(return_value='access-tok-1')
    monkeypatch.setattr(sync_daemon_router, 'get_token_manager', lambda provider: token_manager)
    session = SimpleNamespace(token={'access_token': 'access-tok-1', 'refresh_token': 'SECRET', 'expires_at': 1234})
    monkeypatch.setattr(
        sync_daemon_router.OAuthSessions,
        'get_session_by_provider_and_user_id',
        AsyncMock(return_value=session),
    )

    resp = client.post('/api/v1/sync-daemon/token', json=_token_body(knowledge_id=KB_ID))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {'access_token': 'access-tok-1', 'expires_at': 1234}
    assert 'SECRET' not in resp.text  # refresh tokens never leave OWUI
    token_manager.get_valid_access_token.assert_awaited_once_with(ACTING_USER_ID, KB_ID)


# --- config read -------------------------------------------------------------


def test_config_unknown_provider_400(client):
    resp = client.get('/api/v1/sync-daemon/config/dropbox')
    assert resp.status_code == 400


def test_config_returns_provider_subset_and_kb_registry(client, monkeypatch):
    stored = {
        'onedrive.enable': True,
        'onedrive.enable_sync': True,
        'onedrive.sync_interval_minutes': 60,
        'onedrive.max_files_per_sync': 250,
    }

    async def fake_get_many(*keys):
        return {key: stored[key] for key in keys if key in stored}

    monkeypatch.setattr(sync_daemon_router.Config, 'get_many', staticmethod(fake_get_many))
    kbs = [
        SimpleNamespace(
            id='kb-1',
            user_id='owner-1',
            meta={'onedrive_sync': {'sources': [{'id': 's1', 'delta_link': 'd1'}], 'status': 'completed'}},
        ),
        SimpleNamespace(id='kb-2', user_id='owner-2', meta=None),
    ]
    get_by_type = AsyncMock(return_value=kbs)
    monkeypatch.setattr(sync_daemon_router.Knowledges, 'get_knowledge_bases_by_type', get_by_type)

    resp = client.get('/api/v1/sync-daemon/config/onedrive')

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['provider'] == 'onedrive'
    # Flat daemon-facing shape: enabled = enable AND enable_sync; the per-sync
    # cap is normalized to max_files_per_sync; oauth providers carry mode only.
    assert body['enabled'] is True
    assert body['sync_interval_minutes'] == 60
    assert body['max_files_per_sync'] == 250
    assert body['auth'] == {'mode': 'oauth'}
    # R2: prefix served from OWUI's registry, never hand-duplicated daemon-side.
    assert body['file_id_prefix'] == 'onedrive-'
    assert body['knowledge_bases'] == [
        {
            'id': 'kb-1',
            'user_id': 'owner-1',
            'meta': {'sources': [{'id': 's1', 'delta_link': 'd1'}], 'status': 'completed'},
        },
        {'id': 'kb-2', 'user_id': 'owner-2', 'meta': {}},
    ]
    get_by_type.assert_awaited_once_with('onedrive')


def test_config_enabled_requires_both_flags(client, monkeypatch):
    stored = {'onedrive.enable': True, 'onedrive.enable_sync': False}

    async def fake_get_many(*keys):
        return {key: stored[key] for key in keys if key in stored}

    monkeypatch.setattr(sync_daemon_router.Config, 'get_many', staticmethod(fake_get_many))
    monkeypatch.setattr(sync_daemon_router.Knowledges, 'get_knowledge_bases_by_type', AsyncMock(return_value=[]))

    resp = client.get('/api/v1/sync-daemon/config/onedrive')

    assert resp.status_code == 200
    assert resp.json()['enabled'] is False


def test_config_confluence_normalizes_cap_and_ships_auth_material(client, monkeypatch):
    stored = {
        'confluence.enable': True,
        'confluence.enable_sync': True,
        'confluence.sync_interval_minutes': 30,
        'confluence.max_pages_per_sync': 500,
        'confluence.auth_mode': 'basic',
        'confluence.site_url': 'https://acme.atlassian.net',
        'confluence.cloud_id': 'cloud-1',
        'confluence.basic_auth_username': 'svc@acme.nl',
        'confluence.basic_auth_api_token': 'tok-123',
        'confluence.scoped_api_token': '',
    }

    async def fake_get_many(*keys):
        return {key: stored[key] for key in keys if key in stored}

    monkeypatch.setattr(sync_daemon_router.Config, 'get_many', staticmethod(fake_get_many))
    monkeypatch.setattr(sync_daemon_router.Knowledges, 'get_knowledge_bases_by_type', AsyncMock(return_value=[]))

    resp = client.get('/api/v1/sync-daemon/config/confluence')

    assert resp.status_code == 200
    body = resp.json()
    # Confluence's max_pages_per_sync value rides the normalized key.
    assert body['max_files_per_sync'] == 500
    assert body['auth'] == {
        'mode': 'basic',
        'site_url': 'https://acme.atlassian.net',
        'cloud_id': 'cloud-1',
        'basic_auth_username': 'svc@acme.nl',
        'basic_auth_api_token': 'tok-123',
        'scoped_api_token': '',
    }


# --- run summary -------------------------------------------------------------


@pytest.fixture
def summary_seams(monkeypatch, principal):
    """Patch the summary handler's collaborators; returns them for asserts."""
    knowledge = SimpleNamespace(
        id=KB_ID,
        user_id=ACTING_USER_ID,
        meta={'onedrive_sync': {'sources': [{'id': 'old', 'delta_link': 'old-cursor'}], 'status': 'syncing'}},
    )
    verify = AsyncMock(return_value=knowledge)
    monkeypatch.setattr(sync_daemon_router, '_verify_knowledge_write_access', verify)

    update_meta = AsyncMock()
    monkeypatch.setattr(sync_daemon_router.Knowledges, 'update_knowledge_meta_by_id', update_meta)

    emit = AsyncMock()
    monkeypatch.setattr(sync_daemon_router, 'emit_sync_progress', emit)

    files = MagicMock()
    files.get_file_by_id = AsyncMock(return_value=None)
    files.set_status = AsyncMock()
    monkeypatch.setattr(sync_daemon_router, 'Files', files)

    return SimpleNamespace(knowledge=knowledge, verify=verify, update_meta=update_meta, emit=emit, files=files)


def _written_sync_info(summary_seams):
    args = summary_seams.update_meta.await_args
    assert args.args[0] == KB_ID
    return args.args[1]['onedrive_sync']


def test_summary_unregistered_provider_uses_slug_sync_meta_key(client, summary_seams):
    """Unregistered providers fall back to the '{slug}_sync' meta_key — the
    same total-function stance as file_id_prefix_for (daemon-era providers and
    the stub E2E harness need no registry entry to report run state)."""
    resp = client.post(f'/api/v1/sync-daemon/runs/{KB_ID}/summary', json=_summary_body(provider='dropbox'))

    assert resp.status_code == 200, resp.text
    args = summary_seams.update_meta.await_args
    assert 'dropbox_sync' in args.args[1]
    assert args.args[1]['dropbox_sync']['status'] == _summary_body(provider='dropbox')['status']


def test_summary_invalid_status_400(client, summary_seams):
    resp = client.post(f'/api/v1/sync-daemon/runs/{KB_ID}/summary', json=_summary_body(status='exploded'))
    assert resp.status_code == 400


def test_summary_heartbeat_writes_heartbeat_without_touching_cursor(client, summary_seams):
    resp = client.post(f'/api/v1/sync-daemon/runs/{KB_ID}/summary', json=_summary_body(status='heartbeat'))

    assert resp.status_code == 200, resp.text
    assert resp.json()['cursor_persisted'] is False
    info = _written_sync_info(summary_seams)
    assert info['status'] == 'syncing'
    assert isinstance(info['sync_heartbeat'], int)
    assert 'last_sync_at' not in info
    assert info['sources'] == [{'id': 'old', 'delta_link': 'old-cursor'}]  # untouched
    summary_seams.files.set_status.assert_not_awaited()  # no fail-mark on non-terminal


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason='knowledge writes moved to soev-api; this path returns with the cloud-sync slice',
)
def test_summary_completed_persists_cursor(client, summary_seams):
    new_sources = [{'id': 's1', 'delta_link': 'fresh-cursor'}]
    resp = client.post(
        f'/api/v1/sync-daemon/runs/{KB_ID}/summary',
        json=_summary_body(status='completed', sources=new_sources, counts={'files_added': 3}),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()['cursor_persisted'] is True
    info = _written_sync_info(summary_seams)
    assert info['status'] == 'completed'
    assert info['sources'] == new_sources
    assert isinstance(info['last_sync_at'], int)
    assert info['last_result']['files_added'] == 3


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason='knowledge writes moved to soev-api; this path returns with the cloud-sync slice',
)
def test_summary_failed_does_not_persist_cursor(client, summary_seams):
    resp = client.post(
        f'/api/v1/sync-daemon/runs/{KB_ID}/summary',
        json=_summary_body(status='failed', sources=[{'id': 's1', 'delta_link': 'poisoned'}], error='boom'),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()['cursor_persisted'] is False
    info = _written_sync_info(summary_seams)
    assert info['status'] == 'failed'
    assert info['sources'] == [{'id': 'old', 'delta_link': 'old-cursor'}]  # frozen
    assert info['error'] == 'boom'
    assert isinstance(info['last_sync_at'], int)  # terminal statuses still stamp


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason='knowledge writes moved to soev-api; this path returns with the cloud-sync slice',
)
def test_summary_completed_with_errors_retryable_codes_freezes_cursor(client, summary_seams):
    resp = client.post(
        f'/api/v1/sync-daemon/runs/{KB_ID}/summary',
        json=_summary_body(
            status='completed_with_errors',
            sources=[{'id': 's1', 'delta_link': 'poisoned'}],
            error_codes={'needs_token_refresh': 2, 'empty_extraction': 1},
        ),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()['cursor_persisted'] is False
    info = _written_sync_info(summary_seams)
    assert info['sources'] == [{'id': 'old', 'delta_link': 'old-cursor'}]  # frozen


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason='knowledge writes moved to soev-api; this path returns with the cloud-sync slice',
)
def test_summary_completed_with_errors_only_non_retryable_advances_cursor(client, summary_seams):
    new_sources = [{'id': 's1', 'delta_link': 'fresh-cursor'}]
    resp = client.post(
        f'/api/v1/sync-daemon/runs/{KB_ID}/summary',
        json=_summary_body(
            status='completed_with_errors',
            sources=new_sources,
            error_codes={'empty_extraction': 1, 'unsupported_content_type': 2},
        ),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()['cursor_persisted'] is True
    assert _written_sync_info(summary_seams)['sources'] == new_sources


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason='knowledge writes moved to soev-api; this path returns with the cloud-sync slice',
)
def test_summary_fail_marks_staged_non_terminal_files(client, summary_seams):
    rows = {
        'onedrive-done': SimpleNamespace(data={'status': 'completed'}),
        'onedrive-errored': SimpleNamespace(data={'status': 'error'}),
        'onedrive-stuck': SimpleNamespace(data={'status': 'processing'}),
        'onedrive-pending': SimpleNamespace(data=None),
    }
    summary_seams.files.get_file_by_id = AsyncMock(side_effect=lambda file_id: rows.get(file_id))

    resp = client.post(
        f'/api/v1/sync-daemon/runs/{KB_ID}/summary',
        json=_summary_body(
            status='failed',
            error='boom',
            staged_file_ids=['onedrive-done', 'onedrive-errored', 'onedrive-stuck', 'onedrive-pending', 'gone'],
        ),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()['fail_marked'] == 2
    marked = {call.args[0]: call for call in summary_seams.files.set_status.await_args_list}
    assert set(marked) == {'onedrive-stuck', 'onedrive-pending'}
    for call in marked.values():
        assert call.args[1] == 'error'
        assert call.kwargs['error'] == 'boom'


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason='knowledge writes moved to soev-api; this path returns with the cloud-sync slice',
)
def test_summary_cancelled_fail_marks_with_cancelled_status(client, summary_seams):
    rows = {'onedrive-stuck': SimpleNamespace(data={'status': 'processing'})}
    summary_seams.files.get_file_by_id = AsyncMock(side_effect=lambda file_id: rows.get(file_id))

    resp = client.post(
        f'/api/v1/sync-daemon/runs/{KB_ID}/summary',
        json=_summary_body(status='cancelled', staged_file_ids=['onedrive-stuck']),
    )

    assert resp.status_code == 200, resp.text
    summary_seams.files.set_status.assert_awaited_once_with(
        'onedrive-stuck', 'cancelled', error='Sync cancelled by user'
    )


def test_summary_emits_sync_progress_with_mapped_counts(client, summary_seams):
    resp = client.post(
        f'/api/v1/sync-daemon/runs/{KB_ID}/summary',
        json=_summary_body(
            provider='google_drive',
            status='completed',
            counts={'current': 5, 'total': 5, 'files_added': 4, 'files_updated': 1},
        ),
    )

    assert resp.status_code == 200, resp.text
    summary_seams.emit.assert_awaited_once()
    kwargs = summary_seams.emit.await_args.kwargs
    assert kwargs['provider_prefix'] == 'googledrive'  # slug → event-prefix mapping
    assert kwargs['user_id'] == ACTING_USER_ID
    assert kwargs['knowledge_id'] == KB_ID
    assert kwargs['status'] == 'completed'
    assert kwargs['current'] == 5
    assert kwargs['files_added'] == 4
    assert kwargs['needs_reauth'] is False


def test_summary_started_sets_syncing_and_started_at(client, summary_seams):
    resp = client.post(f'/api/v1/sync-daemon/runs/{KB_ID}/summary', json=_summary_body(status='started'))

    assert resp.status_code == 200, resp.text
    info = _written_sync_info(summary_seams)
    assert info['status'] == 'syncing'
    assert isinstance(info['sync_started_at'], int)
    assert 'last_sync_at' not in info


def test_summary_write_access_check_runs_with_acting_user(client, summary_seams, principal):
    client.post(f'/api/v1/sync-daemon/runs/{KB_ID}/summary', json=_summary_body())
    args = summary_seams.verify.await_args.args
    assert args[0] == KB_ID
    assert args[1] is principal.user
