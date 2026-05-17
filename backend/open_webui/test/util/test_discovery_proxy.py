"""Unit tests for the discovery proxy router.

Covers the contract documented in the implementation plan:

* feature-flag gate: ``ENABLE_RAG_FILTER_UI`` false → 503
* URL gate: ``SEARCH_API_BASE_URL`` empty → 503
* outbound headers: ``X-API-Key`` injected when configured
* upstream 401 → typed 502 with operator-readable detail
* upstream non-JSON body → typed 502
* auth dependency: missing/invalid auth → 401

The router uses ``aiohttp.ClientSession`` directly, so we monkey-patch
the symbol on the discovery module and feed it a fake session whose
``request()`` returns a ``MagicMock`` shaped like an aiohttp response.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from open_webui.routers import discovery
from open_webui.utils.auth import get_verified_user


def _build_app(*, flag: bool, base_url: str) -> FastAPI:
    """Mount the discovery router with the feature flag set on app.state.

    The router reads ``request.app.state.config.ENABLE_RAG_FILTER_UI``
    at request time, mirroring the production wiring in main.py.
    """
    app = FastAPI()
    app.state.config = SimpleNamespace(ENABLE_RAG_FILTER_UI=flag)
    app.include_router(discovery.router, prefix='/api/v1/discovery')
    return app


def _override_user(app: FastAPI) -> None:
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='user-uuid-1', role='user')


class _FakeResponse:
    """Minimal stand-in for ``aiohttp.ClientResponse``.

    Only implements the attributes the router touches: ``status``,
    ``headers``, ``json()``, ``text()``. ``json()`` may be configured to
    raise ``ContentTypeError`` to exercise the non-JSON branch.
    """

    def __init__(self, status: int, json_body=None, text_body: str = '', json_raises=False):
        self.status = status
        self.headers = {}
        self._json_body = json_body
        self._text_body = text_body
        self._json_raises = json_raises

    async def json(self):
        if self._json_raises:
            raise aiohttp.ContentTypeError(request_info=MagicMock(), history=(), message='not json')
        return self._json_body

    async def text(self):
        return self._text_body


class _FakeSession:
    """Stand-in for ``aiohttp.ClientSession`` used inside the router.

    Captures outbound calls in ``self.requests`` so tests can assert on
    the URL and headers (notably the ``X-API-Key`` injection).
    """

    def __init__(self, response: _FakeResponse | Exception):
        self._response = response
        self.requests: list[dict] = []
        self.closed = False

    async def request(self, method, url, **kwargs):
        self.requests.append({'method': method, 'url': url, **kwargs})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def close(self):
        self.closed = True


def _patch_session(monkeypatch, fake_session: _FakeSession) -> None:
    """Make ``aiohttp.ClientSession(...)`` inside discovery.py return our fake."""
    monkeypatch.setattr(
        discovery.aiohttp,
        'ClientSession',
        lambda *args, **kwargs: fake_session,
    )


# ---------- Gate tests ---------------------------------------------------------


def test_503_when_feature_flag_disabled(monkeypatch):
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', 'http://upstream:3535')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', 'secret')

    app = _build_app(flag=False, base_url='http://upstream:3535')
    _override_user(app)

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code == 503
    assert 'RAG filter UI is disabled' in res.json()['detail']


def test_503_when_base_url_empty(monkeypatch):
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', '')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', 'secret')

    app = _build_app(flag=True, base_url='')
    _override_user(app)

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code == 503
    assert 'SEARCH_API_BASE_URL is not configured' in res.json()['detail']


# ---------- Auth -------------------------------------------------------------


def test_unauthenticated_request_rejected(monkeypatch):
    """No ``get_verified_user`` override → FastAPI returns 403 from the bearer."""
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', 'http://upstream:3535')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', 'secret')

    app = _build_app(flag=True, base_url='http://upstream:3535')
    # Note: deliberately do not override get_verified_user so the real
    # dependency runs; with no session cookie / Authorization header it
    # rejects the request before the proxy code ever runs.

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code in (401, 403)


# ---------- Outbound call -----------------------------------------------------


def test_x_api_key_injected_on_outbound_call(monkeypatch):
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', 'http://upstream:3535')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', 'secret-key-xyz')

    fake = _FakeSession(_FakeResponse(status=200, json_body={'collections': []}))
    _patch_session(monkeypatch, fake)

    app = _build_app(flag=True, base_url='http://upstream:3535')
    _override_user(app)

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code == 200
    assert res.json() == {'collections': []}

    assert len(fake.requests) == 1
    sent = fake.requests[0]
    assert sent['method'] == 'GET'
    assert sent['url'] == 'http://upstream:3535/discovery/documents'
    assert sent['headers'] == {'X-API-Key': 'secret-key-xyz'}
    assert fake.closed is True


def test_no_x_api_key_when_unset(monkeypatch):
    """Unset SEARCH_API_KEY: outbound call goes through without the header."""
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', 'http://upstream:3535')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', '')

    fake = _FakeSession(_FakeResponse(status=200, json_body={'ok': True}))
    _patch_session(monkeypatch, fake)

    app = _build_app(flag=True, base_url='http://upstream:3535')
    _override_user(app)

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code == 200
    sent = fake.requests[0]
    assert 'X-API-Key' not in sent['headers']


# ---------- Upstream error mapping -------------------------------------------


def test_upstream_401_mapped_to_502_with_operator_message(monkeypatch):
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', 'http://upstream:3535')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', 'wrong-key')

    fake = _FakeSession(_FakeResponse(status=401, text_body='unauthorized'))
    _patch_session(monkeypatch, fake)

    app = _build_app(flag=True, base_url='http://upstream:3535')
    _override_user(app)

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code == 502
    detail = res.json()['detail']
    assert 'SEARCH_API_KEY is wrong or unset' in detail


def test_upstream_non_json_mapped_to_502(monkeypatch):
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', 'http://upstream:3535')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', 'secret')

    fake = _FakeSession(_FakeResponse(status=200, text_body='<html>not json</html>', json_raises=True))
    _patch_session(monkeypatch, fake)

    app = _build_app(flag=True, base_url='http://upstream:3535')
    _override_user(app)

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code == 502
    detail = res.json()['detail']
    assert 'non-JSON body' in detail


def test_upstream_connection_error_mapped_to_502(monkeypatch):
    monkeypatch.setattr(discovery, 'SEARCH_API_BASE_URL', 'http://upstream:3535')
    monkeypatch.setattr(discovery, 'SEARCH_API_KEY', 'secret')

    fake = _FakeSession(aiohttp.ClientConnectorError(connection_key=MagicMock(), os_error=OSError('refused')))
    _patch_session(monkeypatch, fake)

    app = _build_app(flag=True, base_url='http://upstream:3535')
    _override_user(app)

    client = TestClient(app)
    res = client.get('/api/v1/discovery/documents')

    assert res.status_code == 502
    assert 'Cannot reach the search-api' in res.json()['detail']
