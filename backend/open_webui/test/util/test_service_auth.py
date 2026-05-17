"""Unit tests for the loader-worker machine-auth dependency.

Covers ``open_webui.utils.service_auth.get_integration_principal`` — the
``Depends`` shim that lets ``/api/v1/integrations/ingest`` accept either a
session-cookie user *or* the per-tenant ``LOADER_INGEST_API_KEY`` bearer with
``X-Acting-User-Id`` / ``X-Acting-Provider`` headers.

We mount the dependency on a throwaway FastAPI app and exercise it via
``TestClient`` so we don't drag in the DB harness — ``Users.get_user_by_id``
and ``get_current_user`` are monkey-patched at the module's import site.
"""

from __future__ import annotations

import os
from typing import Optional
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from open_webui.utils import service_auth
from open_webui.utils.service_auth import (
    AgentPrincipal,
    LoaderPrincipal,
    get_agent_principal,
    get_integration_principal,
)


@pytest.fixture
def loader_key(monkeypatch):
    """Configure a known LOADER_INGEST_API_KEY for the test."""
    key = 'test-loader-key-' + 'a' * 32
    monkeypatch.setenv('LOADER_INGEST_API_KEY', key)
    return key


@pytest.fixture
def fake_user():
    user = MagicMock()
    user.id = 'user-uuid-1'
    user.email = 'lex@gradient-ds.com'
    user.role = 'user'
    user.name = 'Lex'
    user.info = {'integration_provider': 'onedrive'}
    return user


@pytest.fixture
def app_with_principal(monkeypatch, fake_user):
    """FastAPI app with one endpoint that echoes the resolved principal."""

    def fake_get_user_by_id(user_id: str, db=None):
        if user_id == fake_user.id:
            return fake_user
        return None

    monkeypatch.setattr(service_auth.Users, 'get_user_by_id', fake_get_user_by_id)

    async def fake_get_current_user(request, response, background_tasks, auth_token=None):
        # Cookie-auth fallback: succeed only when there's no Authorization header
        # and a magic 'session' cookie is present. Otherwise emulate "Not authenticated".
        if request.cookies.get('session') == 'cookie-user-1':
            cookie_user = MagicMock()
            cookie_user.id = 'cookie-user-1'
            cookie_user.role = 'user'
            cookie_user.email = 'cookie@example.com'
            cookie_user.name = 'Cookie'
            cookie_user.info = {'integration_provider': 'google_drive'}
            return cookie_user
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    monkeypatch.setattr(service_auth, 'get_current_user', fake_get_current_user)

    app = FastAPI()

    @app.get('/test/principal')
    def echo(principal=Depends(get_integration_principal)):
        if isinstance(principal, LoaderPrincipal):
            return {
                'kind': 'loader',
                'user_id': principal.user.id,
                'provider': principal.provider_slug,
            }
        return {'kind': 'user', 'user_id': principal.id}

    return TestClient(app, raise_server_exceptions=False)


# ---------- bearer / loader path -----------------------------------------------------


def test_valid_bearer_with_acting_headers_returns_loader_principal(app_with_principal, loader_key, fake_user):
    resp = app_with_principal.get(
        '/test/principal',
        headers={
            'Authorization': f'Bearer {loader_key}',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': 'onedrive',
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {'kind': 'loader', 'user_id': fake_user.id, 'provider': 'onedrive'}


def test_valid_bearer_with_provider_override_uses_header_not_user_info(app_with_principal, loader_key, fake_user):
    """The provider on a LoaderPrincipal comes from the header, not user.info."""
    resp = app_with_principal.get(
        '/test/principal',
        headers={
            'Authorization': f'Bearer {loader_key}',
            'X-Acting-User-Id': fake_user.id,
            'X-Acting-Provider': 'google_drive',  # different from user.info
        },
    )
    assert resp.status_code == 200
    assert resp.json()['provider'] == 'google_drive'


def test_valid_bearer_missing_acting_headers_returns_400(app_with_principal, loader_key):
    resp = app_with_principal.get(
        '/test/principal',
        headers={
            'Authorization': f'Bearer {loader_key}',
            # no X-Acting-User-Id / X-Acting-Provider
        },
    )
    assert resp.status_code == 400
    assert 'X-Acting-User-Id' in resp.json()['detail']


def test_valid_bearer_missing_only_provider_returns_400(app_with_principal, loader_key, fake_user):
    resp = app_with_principal.get(
        '/test/principal',
        headers={
            'Authorization': f'Bearer {loader_key}',
            'X-Acting-User-Id': fake_user.id,
            # no X-Acting-Provider
        },
    )
    assert resp.status_code == 400


def test_valid_bearer_unknown_acting_user_returns_404(app_with_principal, loader_key):
    resp = app_with_principal.get(
        '/test/principal',
        headers={
            'Authorization': f'Bearer {loader_key}',
            'X-Acting-User-Id': 'nonexistent-user',
            'X-Acting-Provider': 'onedrive',
        },
    )
    assert resp.status_code == 404
    assert 'nonexistent-user' in resp.json()['detail']


def test_invalid_bearer_falls_through_to_cookie_path(app_with_principal, loader_key):
    """A bearer that doesn't match LOADER_INGEST_API_KEY shouldn't short-circuit auth."""
    # No cookie present, fallback returns 401 from fake_get_current_user.
    resp = app_with_principal.get(
        '/test/principal',
        headers={
            'Authorization': 'Bearer some-other-token',
            'X-Acting-User-Id': 'should-be-ignored',
            'X-Acting-Provider': 'should-be-ignored',
        },
    )
    assert resp.status_code == 401


def test_no_authorization_header_falls_through_to_cookie_path(app_with_principal, loader_key):
    """Cookie auth is the default fall-through when Authorization is absent."""
    resp = app_with_principal.get(
        '/test/principal',
        cookies={'session': 'cookie-user-1'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {'kind': 'user', 'user_id': 'cookie-user-1'}


def test_acting_headers_ignored_on_cookie_path(app_with_principal, loader_key):
    """Acting-* headers must not influence auth when there's no loader bearer."""
    resp = app_with_principal.get(
        '/test/principal',
        cookies={'session': 'cookie-user-1'},
        headers={
            # No Authorization header → cookie path
            'X-Acting-User-Id': 'attacker-controlled-id',
            'X-Acting-Provider': 'onedrive',
        },
    )
    assert resp.status_code == 200
    # The cookie user wins; acting headers are ignored.
    assert resp.json()['user_id'] == 'cookie-user-1'


# ---------- environment-variable handling --------------------------------------------


def test_loader_key_unset_treats_any_bearer_as_non_loader(monkeypatch, app_with_principal):
    """When LOADER_INGEST_API_KEY is empty, no bearer can match the loader path."""
    monkeypatch.delenv('LOADER_INGEST_API_KEY', raising=False)
    resp = app_with_principal.get(
        '/test/principal',
        headers={'Authorization': 'Bearer anything'},
    )
    # Falls through to cookie auth; no cookie present → 401
    assert resp.status_code == 401


def test_loader_key_uses_constant_time_compare(monkeypatch):
    """``hmac.compare_digest`` is used so a near-miss bearer doesn't match."""
    monkeypatch.setenv('LOADER_INGEST_API_KEY', 'correct-horse-battery-staple')
    assert service_auth._loader_key_matches('correct-horse-battery-staple') is True
    assert service_auth._loader_key_matches('correct-horse-battery-stapleX') is False
    assert service_auth._loader_key_matches('') is False


def test_loader_key_empty_env_never_matches(monkeypatch):
    monkeypatch.delenv('LOADER_INGEST_API_KEY', raising=False)
    assert service_auth._loader_key_matches('') is False
    assert service_auth._loader_key_matches('anything') is False


# ---------- agent key / get_agent_principal ------------------------------------------


@pytest.fixture
def agent_key(monkeypatch):
    """Configure a known AGENT_API_KEY for the test."""
    key = 'test-agent-key-' + 'b' * 32
    monkeypatch.setenv('AGENT_API_KEY', key)
    return key


@pytest.fixture
def agent_user():
    user = MagicMock()
    user.id = 'agent-user-1'
    user.email = 'someone@example.com'
    user.role = 'user'
    user.name = 'Someone'
    user.info = {}
    return user


@pytest.fixture
def agent_app(monkeypatch, agent_user):
    """FastAPI app that exercises ``get_agent_principal`` end-to-end."""

    def fake_get_user_by_id(user_id: str, db=None):
        if user_id == agent_user.id:
            return agent_user
        return None

    monkeypatch.setattr(service_auth.Users, 'get_user_by_id', fake_get_user_by_id)

    app = FastAPI()

    @app.get('/test/agent-principal')
    def echo(principal=Depends(get_agent_principal)):
        return {'agent_id': principal.agent_id, 'user_id': principal.user.id}

    return TestClient(app, raise_server_exceptions=False)


def test_agent_key_uses_constant_time_compare(monkeypatch):
    monkeypatch.setenv('AGENT_API_KEY', 'correct-horse-battery-staple')
    assert service_auth._agent_key_matches('correct-horse-battery-staple') is True
    assert service_auth._agent_key_matches('correct-horse-battery-stapleX') is False
    assert service_auth._agent_key_matches('') is False


def test_agent_key_empty_env_never_matches(monkeypatch):
    monkeypatch.delenv('AGENT_API_KEY', raising=False)
    assert service_auth._agent_key_matches('') is False
    assert service_auth._agent_key_matches('anything') is False


def test_agent_valid_bearer_with_acting_user_returns_200(agent_app, agent_key, agent_user):
    resp = agent_app.get(
        '/test/agent-principal',
        headers={
            'Authorization': f'Bearer {agent_key}',
            'X-Acting-User-Id': agent_user.id,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {'agent_id': 'agent', 'user_id': agent_user.id}


def test_agent_invalid_bearer_returns_401(agent_app, agent_key, agent_user):
    resp = agent_app.get(
        '/test/agent-principal',
        headers={
            'Authorization': 'Bearer not-the-configured-key',
            'X-Acting-User-Id': agent_user.id,
        },
    )
    assert resp.status_code == 401
    assert 'invalid agent bearer' in resp.json()['detail']


def test_agent_missing_bearer_returns_401(agent_app, agent_key, agent_user):
    resp = agent_app.get(
        '/test/agent-principal',
        headers={'X-Acting-User-Id': agent_user.id},
    )
    assert resp.status_code == 401


def test_agent_valid_bearer_missing_acting_user_returns_400(agent_app, agent_key):
    resp = agent_app.get(
        '/test/agent-principal',
        headers={'Authorization': f'Bearer {agent_key}'},
    )
    assert resp.status_code == 400
    assert 'X-Acting-User-Id' in resp.json()['detail']


def test_agent_unknown_acting_user_returns_404(agent_app, agent_key):
    resp = agent_app.get(
        '/test/agent-principal',
        headers={
            'Authorization': f'Bearer {agent_key}',
            'X-Acting-User-Id': 'never-existed',
        },
    )
    assert resp.status_code == 404
    assert 'never-existed' in resp.json()['detail']


def test_agent_key_unset_treats_any_bearer_as_invalid(agent_app, monkeypatch, agent_user):
    """When AGENT_API_KEY is empty, no inbound bearer is accepted."""
    monkeypatch.delenv('AGENT_API_KEY', raising=False)
    resp = agent_app.get(
        '/test/agent-principal',
        headers={
            'Authorization': 'Bearer anything',
            'X-Acting-User-Id': agent_user.id,
        },
    )
    assert resp.status_code == 401
    assert 'invalid agent bearer' in resp.json()['detail']
