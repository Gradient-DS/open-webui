"""Unit tests for the sync-daemon HTTP client.

Verifies that:
* ``trigger_sync_run`` POSTs the right URL / bearer header / JSON body and
  treats 202 (accepted) and 409 (already running) as idempotent success,
  raising on 500 or when the daemon URL is unset.
* ``cancel_sync_run`` addresses the right URL and treats 200 (cancelled) and
  404 (nothing active) as idempotent success, raising on 500.

The client is exercised through ``httpx.MockTransport`` and driven with
``asyncio.run`` (no pytest-asyncio), matching the existing sync test style.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List

import httpx
import pytest

from open_webui.services.sync import daemon_client
from open_webui.services.sync.daemon_client import (
    SyncDaemonError,
    cancel_sync_run,
    trigger_sync_run,
)

_BASE_URL = 'http://tenant-x-sync-daemon:8010'
_API_KEY = 'daemon-secret-key'


class _Recorder:
    """Captures requests routed through ``httpx.MockTransport``."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, json={})


@pytest.fixture
def daemon_config(monkeypatch):
    """Point the client at a fake daemon with a known key."""
    monkeypatch.setattr(daemon_client, 'SYNC_DAEMON_URL', _BASE_URL)
    monkeypatch.setattr(daemon_client, 'SYNC_DAEMON_API_KEY', _API_KEY)


def _install_transport(monkeypatch, recorder: _Recorder) -> None:
    transport = httpx.MockTransport(recorder)
    real_async_client = httpx.AsyncClient

    def make_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs['transport'] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(daemon_client.httpx, 'AsyncClient', make_async_client)


def test_trigger_sync_run_posts_url_headers_body(daemon_config, monkeypatch):
    recorder = _Recorder(202)
    _install_transport(monkeypatch, recorder)

    asyncio.run(trigger_sync_run('kb-1', 'onedrive', 'user-42'))

    assert len(recorder.requests) == 1
    req = recorder.requests[0]
    assert req.method == 'POST'
    assert str(req.url) == f'{_BASE_URL}/sync/run'
    assert req.headers['Authorization'] == f'Bearer {_API_KEY}'
    body = json.loads(req.content.decode())
    assert body == {'knowledge_id': 'kb-1', 'provider': 'onedrive', 'user_id': 'user-42'}


def test_trigger_sync_run_accepts_409_as_success(daemon_config, monkeypatch):
    # 409 = a run is already active — idempotent success, must not raise.
    _install_transport(monkeypatch, _Recorder(409))
    asyncio.run(trigger_sync_run('kb-1', 'confluence', 'user-1'))


def test_trigger_sync_run_raises_on_500(daemon_config, monkeypatch):
    _install_transport(monkeypatch, _Recorder(500))
    with pytest.raises(SyncDaemonError):
        asyncio.run(trigger_sync_run('kb-1', 'google_drive', 'user-1'))


def test_trigger_sync_run_raises_when_url_unset(monkeypatch):
    monkeypatch.setattr(daemon_client, 'SYNC_DAEMON_URL', '')
    with pytest.raises(SyncDaemonError):
        asyncio.run(trigger_sync_run('kb-1', 'onedrive', 'user-1'))


def test_cancel_sync_run_posts_url_and_headers(daemon_config, monkeypatch):
    recorder = _Recorder(200)
    _install_transport(monkeypatch, recorder)

    asyncio.run(cancel_sync_run('kb-9'))

    assert len(recorder.requests) == 1
    req = recorder.requests[0]
    assert req.method == 'POST'
    assert str(req.url) == f'{_BASE_URL}/sync/kb-9/cancel'
    assert req.headers['Authorization'] == f'Bearer {_API_KEY}'


def test_cancel_sync_run_accepts_404_as_success(daemon_config, monkeypatch):
    # 404 = nothing active — idempotent success, must not raise.
    _install_transport(monkeypatch, _Recorder(404))
    asyncio.run(cancel_sync_run('kb-9'))


def test_cancel_sync_run_raises_on_500(daemon_config, monkeypatch):
    _install_transport(monkeypatch, _Recorder(500))
    with pytest.raises(SyncDaemonError):
        asyncio.run(cancel_sync_run('kb-9'))
