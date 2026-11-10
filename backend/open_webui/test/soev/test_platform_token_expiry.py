"""Platform login refusal ends the browser session even while its OWUI JWT is valid."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from open_webui.test.soev.test_keycloak_token import token_app  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['chats/', 'threads', 'meetings/agents', 'auths/console-access'])
@pytest.mark.parametrize(
    'failure', ['refused', 'recover', 'missing', 'refresh_failed', 'no_refresh_token', 'force_failed']
)
async def test_platform_routes_end_an_otherwise_valid_session(
    token_app, monkeypatch, platform_capabilities, route, failure
):
    from open_webui.routers import auths, chats, meetings
    from open_webui.soev import threads
    from open_webui.soev.thread_routes import create_thread_router

    ctx = token_app
    monkeypatch.setattr(auths.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(auths, 'get_permissions', AsyncMock(return_value={}))
    monkeypatch.setattr(meetings, 'feature_enabled', AsyncMock(return_value=True))
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    ctx.app.state.thread_organizations = Mock(list=AsyncMock(return_value=[]), read_states=AsyncMock(return_value={}))
    ctx.app.include_router(auths.router, prefix='/api/v1/auths')
    ctx.app.include_router(chats.router, prefix='/api/v1/chats')
    ctx.app.include_router(create_thread_router(AsyncMock(return_value=[])), prefix='/api/v1/threads')
    ctx.app.include_router(meetings.router, prefix='/api/v1/meetings')
    requests = []

    def refuse(request):
        if request.url.path == '/v1/groups':
            assert 'X-Soev-User-Token' not in request.headers
            return httpx.Response(200, json={'items': [], 'next_cursor': None})
        requests.append(request)
        if failure == 'recover' and request.headers.get('X-Soev-User-Token') == 'fictional-refreshed-token':
            payload = {'console_url': None} if route == 'auths/console-access' else {'items': [], 'next_cursor': None}
            if request.url.path == '/v1/chat/reads':
                payload = {'results': [{'status': 200, 'body': payload}]}
            return httpx.Response(200, json=payload)
        return httpx.Response(401, text='Login token refused')

    if failure == 'missing':
        ctx.browser.cookies.clear()
    elif failure == 'force_failed':
        ctx.refresh.return_value = None
    elif failure in {'refresh_failed', 'no_refresh_token'}:
        ctx.session.expires_at = int(time.time()) - 60
        ctx.refresh.return_value = None
        if failure == 'no_refresh_token':
            ctx.session.token.pop('refresh_token')
            # Exercise the real missing-refresh-token branch without contacting Keycloak.
            from open_webui.utils.oauth import OAuthManager

            monkeypatch.setattr(
                'open_webui.utils.oauth.get_oauth_runtime_config', AsyncMock(return_value=SimpleNamespace())
            )
            ctx.app.state.oauth_manager._perform_token_refresh = OAuthManager._perform_token_refresh.__get__(
                ctx.app.state.oauth_manager
            )

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as upstream:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: upstream)
        monkeypatch.setattr(threads, '_shared_client', lambda: upstream)
        assert (await ctx.browser.get('/api/v1/auths/')).status_code == 200
        response = await ctx.browser.get(f'/api/v1/{route}')
    if failure == 'recover':
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 401, response.text
        payload = response.json()
        assert payload.get('state', payload.get('detail', {}).get('state')) == 'sign_in'
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['vary'] == 'Authorization, Cookie'
    if failure in {'refused', 'recover'}:
        assert len(requests) == 2
        assert requests[0].headers['X-Soev-User-Token'] == 'fictional-access-token'
        assert requests[1].headers['X-Soev-User-Token'] == 'fictional-refreshed-token'
        ctx.refresh.assert_awaited_once()
    elif failure == 'force_failed':
        assert len(requests) == 1
        ctx.refresh.assert_awaited_once()
        ctx.update.assert_not_awaited()
    else:
        assert not requests
        ctx.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_refused_mutations_are_not_replayed(token_app, monkeypatch):
    ctx = token_app
    requests = []

    def refuse(request):
        requests.append(request)
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as upstream:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: upstream)
        response = await ctx.browser.get('/call/json')
    assert response.status_code == 401
    assert response.json() == {'detail': {'state': 'sign_in'}}
    assert len(requests) == 1
    assert requests[0].method == 'POST'
    ctx.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_refused_reads_share_one_refresh(token_app, monkeypatch):
    from fastapi import Depends
    from open_webui.soev import acting

    ctx = token_app
    requests = []

    def respond(request):
        requests.append(request)
        status = 200 if request.headers['X-Soev-User-Token'] == 'fictional-refreshed-token' else 401
        return httpx.Response(status, json={})

    @ctx.app.get('/parallel')
    async def parallel(user=Depends(ctx.auth.get_verified_user)):
        await asyncio.gather(*(ctx.api.get(f'/v1/read-{index}', as_user=acting.requester_ref()) for index in range(10)))
        return {}

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: upstream)
        assert (await ctx.browser.get('/parallel')).status_code == 200
    ctx.refresh.assert_awaited_once()
    ctx.update.assert_awaited_once()
    assert len(requests) == 20


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', [False, True])
@pytest.mark.parametrize('status', [401, 403, 404, 503])
async def test_session_boundary_preserves_other_refusals(token_app, monkeypatch, platform, status):
    from fastapi import Depends, HTTPException
    from open_webui import config

    ctx = token_app
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid' if platform else '')

    @ctx.app.get('/refusal')
    async def refusal(user=Depends(ctx.auth.get_verified_user)):
        raise HTTPException(status, detail={'code': 'original_refusal'})

    response = await ctx.browser.get('/refusal')
    assert response.status_code == status
    assert response.json() == {
        'detail': {'state': 'sign_in'} if platform and status == 401 else {'code': 'original_refusal'}
    }
