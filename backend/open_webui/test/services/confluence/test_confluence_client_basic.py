"""ConfluenceClient basic-auth mode — URL construction, auth header, 401 handling.

Confluence Phase 2 added a basic-auth mode (username + API token) that talks
to the customer site directly instead of the Atlassian OAuth gateway. These
tests pin the three behaviours that differ from oauth mode: the base URL, the
Authorization header, and that a 401 is terminal (no refresh retry). They run
without pytest-asyncio via asyncio.run.
"""

from __future__ import annotations

import asyncio
import base64

import httpx

from open_webui.services.confluence.confluence_client import ConfluenceClient


def test_basic_mode_v2_url_targets_site_directly():
    client = ConfluenceClient(
        auth_mode='basic',
        site_url='https://acme.atlassian.net/',
        basic_username='svc@acme.com',
        basic_api_token='tok',
    )
    assert client._v2_url('spaces') == 'https://acme.atlassian.net/wiki/api/v2/spaces'
    assert client._v2_url('/pages/123') == 'https://acme.atlassian.net/wiki/api/v2/pages/123'


def test_oauth_mode_v2_url_targets_gateway():
    client = ConfluenceClient(access_token='bearer-tok', cloud_id='cloud-1')
    assert client._v2_url('spaces') == 'https://api.atlassian.com/ex/confluence/cloud-1/wiki/api/v2/spaces'


def test_basic_mode_sends_basic_auth_header():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['authorization'] = request.headers.get('authorization')
        captured['url'] = str(request.url)
        return httpx.Response(200, json={'results': [], '_links': {}})

    client = ConfluenceClient(
        auth_mode='basic',
        site_url='https://acme.atlassian.net',
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
    assert captured['url'] == 'https://acme.atlassian.net/wiki/api/v2/spaces?limit=1'


def test_basic_mode_401_is_terminal_no_refresh():
    """A 401 with a static credential must not trigger a refresh retry."""
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(401, json={'message': 'Unauthorized'})

    async def _refresh():  # would be invoked if the basic-mode guard were missing
        calls['refreshed'] = True
        return 'new-token'

    client = ConfluenceClient(
        auth_mode='basic',
        site_url='https://acme.atlassian.net',
        basic_username='svc@acme.com',
        basic_api_token='bad',
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
