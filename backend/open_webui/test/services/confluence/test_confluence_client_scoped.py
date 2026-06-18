"""ConfluenceClient scoped-token mode + cloudId resolution.

Confluence scoped tokens combine the basic-mode Basic header with the
oauth-mode gateway transport: ``base64(email:token)`` sent against
``https://api.atlassian.com/ex/confluence/{cloudId}/wiki/api/v2/...``. These
tests pin that combination (URL, header, terminal-401-no-refresh) and the
cloudId resolver (manual override vs ``_edge/tenant_info``). They run without
pytest-asyncio via ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from open_webui.services.confluence import basic_auth
from open_webui.services.confluence.confluence_client import ConfluenceClient


def test_scoped_mode_v2_url_targets_gateway():
    client = ConfluenceClient(
        auth_mode='scoped',
        cloud_id='cloud-9',
        basic_username='svc@acme.com',
        basic_api_token='scoped-tok',
    )
    assert client._v2_url('spaces') == 'https://api.atlassian.com/ex/confluence/cloud-9/wiki/api/v2/spaces'
    assert client._v2_url('/pages/42') == 'https://api.atlassian.com/ex/confluence/cloud-9/wiki/api/v2/pages/42'


def test_scoped_mode_sends_basic_auth_header_against_gateway():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['authorization'] = request.headers.get('authorization')
        captured['url'] = str(request.url)
        return httpx.Response(200, json={'results': [], '_links': {}})

    client = ConfluenceClient(
        auth_mode='scoped',
        cloud_id='cloud-9',
        basic_username='svc@acme.com',
        basic_api_token='s3cr3t',
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _run():
        await client.list_spaces(limit=1)
        await client.close()

    asyncio.run(_run())

    expected = 'Basic ' + base64.b64encode(b'svc@acme.com:s3cr3t').decode('ascii')
    assert captured['authorization'] == expected
    assert captured['url'] == 'https://api.atlassian.com/ex/confluence/cloud-9/wiki/api/v2/spaces?limit=1'


def test_scoped_mode_401_is_terminal_no_refresh():
    """A scoped token cannot refresh — a 401 must not trigger a refresh retry."""
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(401, json={'message': 'scope does not match'})

    async def _refresh():  # would be invoked if the scoped-mode guard were missing
        calls['refreshed'] = True
        return 'new-token'

    client = ConfluenceClient(
        auth_mode='scoped',
        cloud_id='cloud-9',
        basic_username='svc@acme.com',
        basic_api_token='under-scoped',
        token_provider=_refresh,
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _run():
        response = await client._request_with_retry('GET', client._v2_url('spaces'))
        await client.close()
        return response

    response = asyncio.run(_run())

    assert response.status_code == 401
    assert calls['count'] == 1
    assert 'refreshed' not in calls


# Captured before any patch so the factory below builds a *real* client rather
# than recursing into the patched name (basic_auth.httpx is the httpx module).
_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(handler):
    """A drop-in for ``httpx.AsyncClient(...)`` backed by a MockTransport."""

    def factory(*_args, **_kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_resolve_cloud_id_returns_manual_override_without_network():
    def handler(request: httpx.Request) -> httpx.Response:  # must never be called
        raise AssertionError('network was hit despite a manual cloudId override')

    with (
        patch.object(basic_auth, 'CONFLUENCE_CLOUD_ID', SimpleNamespace(value='manual-cloud')),
        patch.object(basic_auth, 'CONFLUENCE_SITE_URL', SimpleNamespace(value='https://acme.atlassian.net')),
        patch.object(basic_auth.httpx, 'AsyncClient', _mock_async_client(handler)),
    ):
        result = asyncio.run(basic_auth.resolve_cloud_id())

    assert result == 'manual-cloud'


def test_resolve_cloud_id_parses_tenant_info_when_no_override():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        return httpx.Response(200, json={'cloudId': 'resolved-cloud'})

    with (
        patch.object(basic_auth, 'CONFLUENCE_CLOUD_ID', SimpleNamespace(value='')),
        patch.object(basic_auth, 'CONFLUENCE_SITE_URL', SimpleNamespace(value='https://acme.atlassian.net/')),
        patch.object(basic_auth.httpx, 'AsyncClient', _mock_async_client(handler)),
    ):
        result = asyncio.run(basic_auth.resolve_cloud_id())

    assert result == 'resolved-cloud'
    assert captured['url'] == 'https://acme.atlassian.net/_edge/tenant_info'


def test_resolve_cloud_id_returns_none_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text='not found')

    with (
        patch.object(basic_auth, 'CONFLUENCE_CLOUD_ID', SimpleNamespace(value='')),
        patch.object(basic_auth, 'CONFLUENCE_SITE_URL', SimpleNamespace(value='https://acme.atlassian.net')),
        patch.object(basic_auth.httpx, 'AsyncClient', _mock_async_client(handler)),
    ):
        result = asyncio.run(basic_auth.resolve_cloud_id())

    assert result is None
