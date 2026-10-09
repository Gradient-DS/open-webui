"""Treat OpenAPI paths, redirect locations and MCP responses as untrusted content."""

import httpx
import pytest
from urllib.parse import urlsplit

from open_webui.utils import tools
from open_webui.utils.mcp import client as mcp
from .conftest import HOSTS, INTERNAL, PUBLIC, assert_public_only


@pytest.mark.parametrize('host', HOSTS)
@pytest.mark.asyncio
async def test_openapi_spec_path_cannot_replace_authority(host, http_boundary):
    path = f'@{host}/'
    spec = {'openapi': {'paths': {path: {'get': {'operationId': 'fetch'}}}}}
    await tools.execute_tool_server(PUBLIC.rstrip('/'), {}, {}, 'fetch', {}, spec)
    assert http_boundary.sent == []


@pytest.mark.parametrize('sink', ['spec', 'execute'])
@pytest.mark.asyncio
async def test_tool_server_redirect_is_guarded(sink, http_boundary, monkeypatch):
    monkeypatch.setattr(tools, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL
    http_boundary.responses[INTERNAL] = (200, {'Content-Type': 'application/json'}, b'{}')
    if sink == 'spec':
        await tools.get_tool_server_data(PUBLIC, {})
    else:
        spec = {'openapi': {'paths': {'/': {'get': {'operationId': 'fetch'}}}}}
        await tools.execute_tool_server(PUBLIC.rstrip('/'), {}, {}, 'fetch', {}, spec)
    assert http_boundary.sent == [PUBLIC]
    assert_public_only(http_boundary)


@pytest.mark.parametrize('host', HOSTS)
@pytest.mark.asyncio
async def test_mcp_redirect_is_guarded(host, monkeypatch, offline):
    sent = []
    target = f'http://{host}/'

    def respond(request):
        sent.append(str(request.url))
        if str(request.url) == PUBLIC:
            return httpx.Response(302, headers={'Location': target})
        return httpx.Response(403)

    monkeypatch.setattr(httpx.AsyncClient, '_init_transport', lambda *args, **kwargs: httpx.MockTransport(respond))
    client = mcp.MCPClient()
    try:
        await client.connect(PUBLIC)
    except Exception:
        pass
    finally:
        await client.disconnect()
    assert sent == [PUBLIC], sent


@pytest.mark.asyncio
async def test_tool_path_parameter_cannot_replace_authority(http_boundary):
    spec = {
        'openapi': {
            'paths': {
                '/{value}': {
                    'get': {
                        'operationId': 'fetch',
                        'parameters': [{'name': 'value', 'in': 'path'}],
                    }
                }
            }
        }
    }
    await tools.execute_tool_server(PUBLIC.rstrip('/'), {}, {}, 'fetch', {'value': '@127.0.0.1/'}, spec)
    assert len(http_boundary.sent) == 1
    assert urlsplit(http_boundary.sent[0]).hostname == 'public.example'
    assert_public_only(http_boundary)
