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

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from open_webui.utils import service_auth
from open_webui.utils.service_auth import (
    get_agent_principal,
)

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

    async def fake_get_user_by_id(user_id: str, db=None):
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
