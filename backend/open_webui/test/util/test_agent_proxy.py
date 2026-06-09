"""Unit tests for the agent proxy ``POST /api/v1/agent/responses`` route.

Covers the contract documented in the implementation plan (§5):

* feature-flag gate: ``ENABLE_AGENT_PROXY`` false → 503
* user-id injection: outbound payload has ``user == verified_user.id``
* extra-fields pass-through (``ConfigDict(extra='allow')``): unknown fields
  on ``ResponsesRequest`` survive ``model_dump`` and reach the upstream
* SSE pass-through: ``text/event-stream`` content type forwarded plus bytes
* connect error: ``ClientConnectorError`` → 502 mentioning ``/v1/responses``

Mirrors the style of ``test_discovery_proxy.py`` — both proxies wrap
``aiohttp.ClientSession`` directly so we monkey-patch the symbol on the
router module and feed a fake session whose ``request()`` returns a
fake response.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Iterable
from unittest.mock import MagicMock

import aiohttp
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import agent_proxy
from open_webui.utils.auth import get_verified_user


# ---------- Fakes ------------------------------------------------------------


class _FakeContent:
    """Minimal async iterator over ``aiohttp.ClientResponse.content``.

    ``stream_wrapper`` does ``async for chunk in response.content`` —
    nothing more — so an ``__aiter__`` returning the configured chunks is
    enough.
    """

    def __init__(self, chunks: Iterable[bytes]):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    """Stand-in for ``aiohttp.ClientResponse``.

    Implements only what ``_proxy_post_sse`` and ``stream_wrapper`` touch:
    ``status``, ``headers``, ``text()``, ``json()``, ``content``,
    ``closed``, and a synchronous ``close()``.
    """

    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict | None = None,
        body_chunks: Iterable[bytes] = (),
        text_body: str = '',
        json_body=None,
    ):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(body_chunks)
        self._text_body = text_body
        self._json_body = json_body
        self.closed = False

    async def text(self):
        return self._text_body

    async def json(self):
        return self._json_body

    def close(self):
        self.closed = True
        return None


class _FakeSession:
    """Stand-in for ``aiohttp.ClientSession`` used inside the router.

    Captures outbound calls in ``self.requests`` so tests can assert on
    the URL, headers, and (crucially) the ``data=`` payload sent to the
    upstream agent service.
    """

    def __init__(self, response_or_exc):
        self._response_or_exc = response_or_exc
        self.requests: list[dict] = []
        self.closed = False

    async def request(self, method, url, **kwargs):
        self.requests.append({'method': method, 'url': url, **kwargs})
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc

    async def close(self):
        self.closed = True


# ---------- Helpers ----------------------------------------------------------


def _build_app(*, enabled: bool = True) -> FastAPI:
    """Mount the agent-proxy router with ``ENABLE_AGENT_PROXY`` on app.state.

    The router reads ``request.app.state.config.ENABLE_AGENT_PROXY`` at
    request time, mirroring the production wiring in main.py.
    """
    app = FastAPI()
    app.state.config = SimpleNamespace(ENABLE_AGENT_PROXY=enabled)
    app.include_router(agent_proxy.router, prefix='/api/v1/agent')
    return app


def _override_user(app: FastAPI, user_id: str = 'user-uuid-xyz') -> None:
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id=user_id, role='user')


def _patch_session(monkeypatch, fake_session: _FakeSession) -> None:
    """Make ``aiohttp.ClientSession(...)`` inside agent_proxy.py return our fake."""
    monkeypatch.setattr(
        agent_proxy.aiohttp,
        'ClientSession',
        lambda *args, **kwargs: fake_session,
    )


def _set_env_constants(monkeypatch, *, base_url: str = 'http://upstream:8080', api_key: str = '') -> None:
    monkeypatch.setattr(agent_proxy, 'AGENT_API_BASE_URL', base_url)
    monkeypatch.setattr(agent_proxy, 'AGENT_API_KEY', api_key)


def _minimal_body(**overrides) -> dict:
    """Smallest valid ``ResponsesRequest`` body — just ``input``."""
    body = {'input': 'hello world'}
    body.update(overrides)
    return body


# ---------- Tests -----------------------------------------------------------


def test_responses_returns_503_when_agent_proxy_disabled(monkeypatch):
    """ENABLE_AGENT_PROXY=false → 503 before any aiohttp call is made."""
    _set_env_constants(monkeypatch, base_url='http://upstream:8080')

    app = _build_app(enabled=False)
    _override_user(app)

    client = TestClient(app)
    res = client.post('/api/v1/agent/responses', json=_minimal_body())

    assert res.status_code == 503
    assert 'Agent Proxy is disabled' in res.json()['detail']


def test_responses_injects_user_field_from_verified_user(monkeypatch):
    """Body without ``user`` → outbound payload has ``user == verified_user.id``."""
    _set_env_constants(monkeypatch, base_url='http://upstream:8080')

    fake = _FakeSession(
        _FakeResponse(
            status=200,
            headers={'Content-Type': 'application/json'},
            json_body={'id': 'resp_1'},
        )
    )
    _patch_session(monkeypatch, fake)

    app = _build_app(enabled=True)
    _override_user(app, user_id='user-uuid-xyz')

    client = TestClient(app)
    res = client.post('/api/v1/agent/responses', json=_minimal_body())

    assert res.status_code == 200
    assert len(fake.requests) == 1
    sent = fake.requests[0]
    assert sent['method'] == 'POST'
    assert sent['url'] == 'http://upstream:8080/v1/responses'

    outbound_payload = json.loads(sent['data'])
    assert outbound_payload['user'] == 'user-uuid-xyz'
    assert outbound_payload['input'] == 'hello world'


def test_responses_forwards_arbitrary_fields_unchanged(monkeypatch):
    """``ConfigDict(extra='allow')``: undeclared fields survive to upstream byte-for-byte."""
    _set_env_constants(monkeypatch, base_url='http://upstream:8080')

    fake = _FakeSession(
        _FakeResponse(
            status=200,
            headers={'Content-Type': 'application/json'},
            json_body={'id': 'resp_1'},
        )
    )
    _patch_session(monkeypatch, fake)

    app = _build_app(enabled=True)
    _override_user(app, user_id='user-uuid-xyz')

    # Mix declared fields (tools, previous_response_id, metadata) with a
    # field *not* declared on ResponsesRequest (parallel_tool_calls) and a
    # nested unknown key (metadata.foo) to confirm extra='allow' both at
    # the model level and inside metadata's dict.
    body = _minimal_body(
        tools=[{'type': 'file_search', 'vector_store_ids': ['vs_123']}],
        previous_response_id='resp_prev',
        metadata={'foo': 'bar', 'trace_id': 't-1'},
        parallel_tool_calls=True,
    )

    client = TestClient(app)
    res = client.post('/api/v1/agent/responses', json=body)

    assert res.status_code == 200
    sent = fake.requests[0]
    outbound_payload = json.loads(sent['data'])

    assert outbound_payload['tools'] == [{'type': 'file_search', 'vector_store_ids': ['vs_123']}]
    assert outbound_payload['previous_response_id'] == 'resp_prev'
    assert outbound_payload['metadata'] == {'foo': 'bar', 'trace_id': 't-1'}
    assert outbound_payload['parallel_tool_calls'] is True


def test_responses_streams_upstream_sse(monkeypatch):
    """Upstream ``text/event-stream`` → client sees the same media type and bytes."""
    _set_env_constants(monkeypatch, base_url='http://upstream:8080')

    sse_chunks = [
        b'event: response.created\ndata: {"id":"resp_1"}\n\n',
        b'event: response.output_text.delta\ndata: {"delta":"hi"}\n\n',
        b'event: response.completed\ndata: {"id":"resp_1"}\n\n',
    ]
    fake = _FakeSession(
        _FakeResponse(
            status=200,
            headers={'Content-Type': 'text/event-stream'},
            body_chunks=sse_chunks,
        )
    )
    _patch_session(monkeypatch, fake)

    app = _build_app(enabled=True)
    _override_user(app)

    client = TestClient(app)
    with client.stream('POST', '/api/v1/agent/responses', json=_minimal_body(stream=True)) as res:
        assert res.status_code == 200
        assert res.headers['content-type'].startswith('text/event-stream')
        received = b''.join(res.iter_bytes())

    for chunk in sse_chunks:
        assert chunk in received


def test_responses_502_on_upstream_connect_error(monkeypatch):
    """``ClientConnectorError`` → 502 whose detail names ``/v1/responses``."""
    _set_env_constants(monkeypatch, base_url='http://upstream:8080')

    fake = _FakeSession(aiohttp.ClientConnectorError(connection_key=MagicMock(), os_error=OSError('refused')))
    _patch_session(monkeypatch, fake)

    app = _build_app(enabled=True)
    _override_user(app)

    client = TestClient(app)
    res = client.post('/api/v1/agent/responses', json=_minimal_body())

    assert res.status_code == 502
    detail = res.json()['detail']
    assert '/v1/responses' in detail
    assert 'Cannot reach the agent service' in detail
