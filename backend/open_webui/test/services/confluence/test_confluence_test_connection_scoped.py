"""Confluence ``POST /auth/test`` — scoped-token probe behaviour.

Mounts the confluence_sync router on a minimal FastAPI app (admin override),
points the ConfluenceClient's httpx at a MockTransport, and pins the scoped
reasons: a healthy gateway returns ``space_count``; a ``401 "scope does not
match"`` maps to ``scope_mismatch``; an unresolvable cloudId maps to
``missing_cloud_id``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import confluence_sync
from open_webui.services.confluence import confluence_client as cc
from open_webui.utils.auth import get_admin_user

# Captured before patching so the factory builds a real client, not a recursion.
_RealAsyncClient = httpx.AsyncClient


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(confluence_sync.router, prefix='/api/v1/confluence')
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
        id='admin-1', role='admin', email='admin@example.com'
    )
    return app


def _mock_async_client(handler):
    def factory(*_args, **_kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_scoped_test_connection_reports_space_count():
    def handler(request: httpx.Request) -> httpx.Response:
        assert 'api.atlassian.com/ex/confluence/cloud-9/wiki/api/v2/spaces' in str(request.url)
        return httpx.Response(200, json={'results': [{'id': '1'}, {'id': '2'}], '_links': {}})

    client = TestClient(_make_app())
    with patch.object(cc.httpx, 'AsyncClient', _mock_async_client(handler)):
        res = client.post(
            '/api/v1/confluence/auth/test',
            json={
                'mode': 'scoped',
                'site_url': 'https://acme.atlassian.net',
                'username': 'svc@acme.com',
                'api_token': 'scoped-tok',
                'cloud_id': 'cloud-9',
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is True
    assert body['reason'] == 'ok'
    assert body['space_count'] == 2


def test_scoped_test_connection_maps_scope_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={'message': 'The token used does not have access: scope does not match'})

    client = TestClient(_make_app())
    with patch.object(cc.httpx, 'AsyncClient', _mock_async_client(handler)):
        res = client.post(
            '/api/v1/confluence/auth/test',
            json={
                'mode': 'scoped',
                'site_url': 'https://acme.atlassian.net',
                'username': 'svc@acme.com',
                'api_token': 'under-scoped',
                'cloud_id': 'cloud-9',
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is False
    assert body['reason'] == 'scope_mismatch'


def test_scoped_test_connection_missing_cloud_id():
    # No cloud_id on the form + resolver returns None → missing_cloud_id, and the
    # client is never built (no network attempted).
    client = TestClient(_make_app())
    with patch.object(confluence_sync, 'resolve_cloud_id', AsyncMock(return_value=None)):
        res = client.post(
            '/api/v1/confluence/auth/test',
            json={
                'mode': 'scoped',
                'site_url': 'https://acme.atlassian.net',
                'username': 'svc@acme.com',
                'api_token': 'scoped-tok',
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is False
    assert body['reason'] == 'missing_cloud_id'
