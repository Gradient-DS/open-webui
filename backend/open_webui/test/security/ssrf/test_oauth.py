"""Cover profile claims and URLs advertised by OAuth resource metadata."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from open_webui.utils import oauth
from .conftest import BLOCKED_URLS, INTERNAL, PUBLIC, assert_public_only


@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.asyncio
async def test_profile_picture_is_blocked(url, http_boundary):
    assert await oauth.OAuthManager._process_picture_url(None, url) == '/user.png'
    assert http_boundary.sent == []


@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
@pytest.mark.asyncio
async def test_profile_picture_connect_guard(attack, http_boundary, offline, monkeypatch):
    monkeypatch.setattr(oauth, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL if attack == 'redirect' else None
    offline.rebind = attack == 'rebind'
    assert await oauth.OAuthManager._process_picture_url(None, PUBLIC) == '/user.png'
    assert_public_only(http_boundary)
    assert http_boundary.sent == ([PUBLIC] if attack == 'redirect' else [])
    if attack == 'rebind':
        assert offline.calls['public.example'] >= 2


@pytest.mark.parametrize('flow', ['static', 'dynamic'])
@pytest.mark.asyncio
async def test_advertised_authorization_server_is_guarded(flow, http_boundary, monkeypatch):
    monkeypatch.setattr(oauth.Config, 'get', AsyncMock(return_value=None))
    metadata_url = 'http://public.example/.well-known/oauth-protected-resource'
    http_boundary.responses[PUBLIC] = (200, {}, b'{}')
    http_boundary.responses[metadata_url] = (
        200,
        {},
        json.dumps(
            {
                'authorization_servers': [INTERNAL],
            }
        ).encode(),
    )
    request = SimpleNamespace(base_url='http://webui.example/')
    try:
        if flow == 'static':
            await oauth.get_oauth_client_info_with_static_credentials(request, 'mcp:test', PUBLIC, 'id', 'secret')
        else:
            await oauth.get_oauth_client_info_with_dynamic_client_registration(request, 'mcp:test', PUBLIC)
    except Exception:
        pass
    assert metadata_url in http_boundary.sent
    assert_public_only(http_boundary)


@pytest.mark.asyncio
async def test_advertised_registration_endpoint_is_guarded(http_boundary, monkeypatch):
    monkeypatch.setattr(oauth.Config, 'get', AsyncMock(return_value=None))
    prm = 'http://public.example/.well-known/oauth-protected-resource'
    discovery = 'http://public.example/.well-known/oauth-authorization-server'
    http_boundary.responses[PUBLIC] = (200, {}, b'{}')
    http_boundary.responses[prm] = (200, {}, b'{"authorization_servers": ["http://public.example/"]}')
    http_boundary.responses[discovery] = (
        200,
        {},
        json.dumps(
            {
                'issuer': PUBLIC,
                'authorization_endpoint': PUBLIC + 'authorize',
                'token_endpoint': PUBLIC + 'token',
                'registration_endpoint': INTERNAL,
                'response_types_supported': ['code'],
            }
        ).encode(),
    )
    try:
        await oauth.get_oauth_client_info_with_dynamic_client_registration(
            SimpleNamespace(base_url='http://webui.example/'),
            'mcp:test',
            PUBLIC,
        )
    except Exception:
        pass
    assert discovery in http_boundary.sent
    assert_public_only(http_boundary)


@pytest.mark.parametrize('kind', ['mcp', 'oidc'])
@pytest.mark.asyncio
async def test_advertised_refresh_endpoint_is_guarded(kind, http_boundary, monkeypatch):
    monkeypatch.setattr(
        oauth,
        'get_oauth_runtime_config',
        AsyncMock(
            return_value=SimpleNamespace(
                OAUTH_REFRESH_TOKEN_INCLUDE_SCOPE=False,
            )
        ),
    )
    manager = SimpleNamespace(
        get_client=(AsyncMock if kind == 'mcp' else Mock)(
            return_value=SimpleNamespace(client_id='test', client_secret='secret')
        ),
        get_client_info=AsyncMock(return_value=None),
        get_server_metadata_url=(AsyncMock if kind == 'mcp' else Mock)(return_value=PUBLIC),
    )
    http_boundary.responses[PUBLIC] = (200, {}, json.dumps({'token_endpoint': INTERNAL}).encode())
    manager_class = oauth.OAuthClientManager if kind == 'mcp' else oauth.OAuthManager
    await manager_class._perform_token_refresh(
        manager,
        SimpleNamespace(provider='mcp:test', token={'refresh_token': 'test'}, id='test'),
    )
    assert http_boundary.sent == [PUBLIC]
    assert_public_only(http_boundary)


@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.asyncio
async def test_authorization_preflight_target_is_blocked(url, http_boundary):
    client = SimpleNamespace(create_authorization_url=AsyncMock(return_value={'url': url}))
    info = oauth.OAuthClientInformationFull(
        client_id='test',
        redirect_uris=['http://webui.example/callback'],
    )
    await oauth.OAuthClientManager._preflight_authorization_url(None, client, info)
    client.create_authorization_url.assert_awaited_once()
    assert http_boundary.sent == []


@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.asyncio
async def test_oauth_resource_metadata_target_is_blocked(url, http_boundary):
    http_boundary.responses[PUBLIC] = (401, {'WWW-Authenticate': f'Bearer resource_metadata="{url}"'}, b'{}')
    await oauth.get_protected_resource_metadata(PUBLIC)
    assert http_boundary.sent == [PUBLIC]
    assert_public_only(http_boundary)
