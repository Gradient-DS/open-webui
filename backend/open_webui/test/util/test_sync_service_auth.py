"""Unit tests for the sync-daemon machine-auth dependencies.

Covers ``open_webui.utils.service_auth.get_sync_principal`` (the strict
machine-only dependency for /api/v1/sync-daemon/*), the ``SYNC_API_KEY``
acceptance added to ``get_integration_principal`` (the daemon reuses the
loader's stage/submit/file-status/ingest edge), and
``routers.knowledge.get_sync_daemon_or_verified_user`` (the flag-gated dual
dependency on the sync-protocol endpoints).

Mirrors ``test_service_auth.py``: the dependencies are mounted on throwaway
FastAPI apps and exercised via ``TestClient``; ``Users.get_user_by_id`` and
``get_current_user`` are monkey-patched at the module's import site.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from open_webui.utils import service_auth
from open_webui.utils.service_auth import (
    LoaderPrincipal,
    SyncPrincipal,
    get_integration_principal,
    get_sync_principal,
)

SYNC_PROVIDER = 'onedrive'


@pytest.fixture
def sync_key(monkeypatch):
    """Configure a known SYNC_API_KEY for the test."""
    key = 'test-sync-key-' + 'c' * 32
    monkeypatch.setenv('SYNC_API_KEY', key)
    return key


@pytest.fixture
def fake_user():
    user = MagicMock()
    user.id = 'user-uuid-1'
    user.email = 'lex@gradient-ds.com'
    user.role = 'user'
    user.name = 'Lex'
    user.info = {}
    return user


@pytest.fixture
def sync_app(monkeypatch, fake_user):
    """FastAPI app with one endpoint that echoes the resolved SyncPrincipal."""

    async def fake_get_user_by_id(user_id: str, db=None):
        if user_id == fake_user.id:
            return fake_user
        return None

    monkeypatch.setattr(service_auth.Users, 'get_user_by_id', fake_get_user_by_id)

    app = FastAPI()

    @app.get('/test/sync-principal')
    def echo(principal: SyncPrincipal = Depends(get_sync_principal)):
        return {'user_id': principal.user.id, 'provider': principal.provider_slug}

    return TestClient(app, raise_server_exceptions=False)


# ---------- get_sync_principal ------------------------------------------------------


def test_valid_key_and_headers_returns_sync_principal(sync_app, sync_key, fake_user):
    resp = sync_app.get(
        '/test/sync-principal',
        headers={
            'Authorization': f'Bearer {sync_key}',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {'user_id': fake_user.id, 'provider': SYNC_PROVIDER}


def test_wrong_key_returns_401(sync_app, sync_key, fake_user):
    resp = sync_app.get(
        '/test/sync-principal',
        headers={
            'Authorization': 'Bearer not-the-sync-key',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 401
    assert 'invalid sync bearer' in resp.json()['detail']


def test_missing_bearer_returns_401(sync_app, sync_key, fake_user):
    resp = sync_app.get(
        '/test/sync-principal',
        headers={'X-Acting-User-Id': fake_user.id, 'X-Acting-Provider': SYNC_PROVIDER},
    )
    assert resp.status_code == 401


def test_missing_acting_headers_returns_400(sync_app, sync_key):
    resp = sync_app.get(
        '/test/sync-principal',
        headers={'Authorization': f'Bearer {sync_key}'},
    )
    assert resp.status_code == 400
    assert 'X-Acting-User-Id' in resp.json()['detail']


def test_missing_only_provider_returns_400(sync_app, sync_key, fake_user):
    resp = sync_app.get(
        '/test/sync-principal',
        headers={
            'Authorization': f'Bearer {sync_key}',
            'X-Acting-User-Id': fake_user.id,
        },
    )
    assert resp.status_code == 400


def test_unknown_acting_user_returns_404(sync_app, sync_key):
    resp = sync_app.get(
        '/test/sync-principal',
        headers={
            'Authorization': f'Bearer {sync_key}',
            'X-Acting-User-Id': 'nonexistent-user',
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 404
    assert 'nonexistent-user' in resp.json()['detail']


def test_unset_env_returns_401(sync_app, monkeypatch, fake_user):
    """When SYNC_API_KEY is unset/empty, no inbound bearer is accepted."""
    monkeypatch.delenv('SYNC_API_KEY', raising=False)
    resp = sync_app.get(
        '/test/sync-principal',
        headers={
            'Authorization': 'Bearer anything',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 401
    assert 'invalid sync bearer' in resp.json()['detail']


def test_sync_key_uses_constant_time_compare(monkeypatch):
    monkeypatch.setenv('SYNC_API_KEY', 'correct-horse-battery-staple')
    assert service_auth._sync_key_matches('correct-horse-battery-staple') is True
    assert service_auth._sync_key_matches('correct-horse-battery-stapleX') is False
    assert service_auth._sync_key_matches('') is False


def test_sync_key_empty_env_never_matches(monkeypatch):
    monkeypatch.delenv('SYNC_API_KEY', raising=False)
    assert service_auth._sync_key_matches('') is False
    assert service_auth._sync_key_matches('anything') is False


# ---------- get_integration_principal accepts SYNC_API_KEY ---------------------------


@pytest.fixture
def integration_app(monkeypatch, fake_user):
    """App exercising get_integration_principal (loader-or-cookie dependency)."""

    async def fake_get_user_by_id(user_id: str, db=None):
        if user_id == fake_user.id:
            return fake_user
        return None

    monkeypatch.setattr(service_auth.Users, 'get_user_by_id', fake_get_user_by_id)

    async def fake_get_current_user(request, response, background_tasks, auth_token=None):
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    monkeypatch.setattr(service_auth, 'get_current_user', fake_get_current_user)

    app = FastAPI()

    @app.get('/test/principal')
    def echo(principal=Depends(get_integration_principal)):
        if isinstance(principal, LoaderPrincipal):
            return {'kind': 'loader', 'user_id': principal.user.id, 'provider': principal.provider_slug}
        return {'kind': 'user', 'user_id': principal.id}

    return TestClient(app, raise_server_exceptions=False)


def test_integration_principal_accepts_sync_key_as_loader(integration_app, monkeypatch, sync_key, fake_user):
    """A SYNC_API_KEY bearer resolves to a LoaderPrincipal exactly like
    LOADER_INGEST_API_KEY — the daemon calls stage/submit/file-status/ingest
    without any handler change."""
    monkeypatch.delenv('LOADER_INGEST_API_KEY', raising=False)
    resp = integration_app.get(
        '/test/principal',
        headers={
            'Authorization': f'Bearer {sync_key}',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {'kind': 'loader', 'user_id': fake_user.id, 'provider': SYNC_PROVIDER}


def test_integration_principal_sync_key_requires_acting_headers(integration_app, monkeypatch, sync_key):
    monkeypatch.delenv('LOADER_INGEST_API_KEY', raising=False)
    resp = integration_app.get(
        '/test/principal',
        headers={'Authorization': f'Bearer {sync_key}'},
    )
    assert resp.status_code == 400
    assert 'X-Acting-User-Id' in resp.json()['detail']


def test_integration_principal_loader_key_still_works_alongside_sync_key(
    integration_app, monkeypatch, sync_key, fake_user
):
    """D-10 co-existence: both keys are valid on the same edge."""
    loader_key = 'test-loader-key-' + 'a' * 32
    monkeypatch.setenv('LOADER_INGEST_API_KEY', loader_key)
    resp = integration_app.get(
        '/test/principal',
        headers={
            'Authorization': f'Bearer {loader_key}',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 200
    assert resp.json()['kind'] == 'loader'


# ---------- knowledge.get_sync_daemon_or_verified_user -------------------------------


@pytest.fixture
def knowledge_dep_app(monkeypatch, fake_user):
    """App exercising the flag-gated dual dependency on the sync-protocol
    endpoints (machine path vs get_verified_user fall-through)."""
    from open_webui.routers import knowledge as knowledge_router

    async def fake_get_user_by_id(user_id: str, db=None):
        if user_id == fake_user.id:
            return fake_user
        return None

    monkeypatch.setattr(service_auth.Users, 'get_user_by_id', fake_get_user_by_id)

    async def fake_get_current_user(request, response, background_tasks, auth_token=None):
        if request.cookies.get('session') == 'cookie-user-1':
            cookie_user = MagicMock()
            cookie_user.id = 'cookie-user-1'
            cookie_user.role = 'user'
            return cookie_user
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    monkeypatch.setattr(knowledge_router, 'get_current_user', fake_get_current_user)

    flag_values = {'sync_daemon.enabled': True}

    async def fake_config_get(key, default=None):
        return flag_values.get(key, default)

    monkeypatch.setattr(knowledge_router.Config, 'get', staticmethod(fake_config_get))

    app = FastAPI()

    @app.get('/test/sync-or-user')
    def echo(user=Depends(knowledge_router.get_sync_daemon_or_verified_user)):
        return {'user_id': user.id}

    return TestClient(app, raise_server_exceptions=False), flag_values


def test_knowledge_dep_machine_path_returns_acting_user(knowledge_dep_app, sync_key, fake_user):
    client, _ = knowledge_dep_app
    resp = client.get(
        '/test/sync-or-user',
        headers={
            'Authorization': f'Bearer {sync_key}',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {'user_id': fake_user.id}


def test_knowledge_dep_machine_path_403_when_flag_off(knowledge_dep_app, sync_key, fake_user):
    client, flag_values = knowledge_dep_app
    flag_values['sync_daemon.enabled'] = False
    resp = client.get(
        '/test/sync-or-user',
        headers={
            'Authorization': f'Bearer {sync_key}',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': SYNC_PROVIDER,
        },
    )
    assert resp.status_code == 403
    assert 'SYNC_DAEMON_ENABLED' in resp.json()['detail']


def test_knowledge_dep_falls_through_to_session_path(knowledge_dep_app, sync_key):
    """A non-matching bearer / plain session keeps today's human path intact."""
    client, _ = knowledge_dep_app
    resp = client.get('/test/sync-or-user', cookies={'session': 'cookie-user-1'})
    assert resp.status_code == 200
    assert resp.json() == {'user_id': 'cookie-user-1'}


def test_knowledge_dep_no_auth_returns_401(knowledge_dep_app):
    client, _ = knowledge_dep_app
    resp = client.get('/test/sync-or-user')
    assert resp.status_code == 401
