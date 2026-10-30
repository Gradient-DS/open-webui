"""Forward only the authenticated request's generic OIDC access token."""

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from open_webui.soev import acting
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.soev.task_turns import TaskTurnRequest


async def _send_file(api, ref, operation):
    if operation == 'bytes':
        await api.post_bytes('/v1/images', b'fictional-image', as_user=ref)
    else:

        async def chunks():
            yield b'fictional-upload'

        await api.upload_document('job', 'source', chunks(), size=16, content_type='text/plain', as_user=ref)


async def platform_error(request, error):
    return JSONResponse(status_code=error.status, content={'code': error.code})


@pytest_asyncio.fixture
async def token_app(identity_config, monkeypatch):
    from open_webui.models.oauth_sessions import OAuthSessions
    from open_webui.utils import auth
    from open_webui.utils.oauth import OAuthManager

    identity, _ = identity_config
    user = identity.UserModel(
        person_id='platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace',
        id='fern',
        name='Fern',
        email='fern@example.invalid',
        role='user',
        created_at=0,
        updated_at=0,
        last_active_at=0,
    )
    monkeypatch.setattr(auth.Users, 'get_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(auth, 'SESSION_SECRET', 'fictional-session-secret-for-keycloak-token-tests')
    monkeypatch.setattr(auth.Users, 'update_last_active_by_id', AsyncMock())
    monkeypatch.setattr(auth, 'is_valid_token', AsyncMock(return_value=True))
    monkeypatch.setattr(auth, 'get_current_user_by_api_key', AsyncMock(return_value=user))
    session = SimpleNamespace(
        id='fern-session',
        user_id=user.id,
        provider='oidc',
        expires_at=int(time.time()) + 3600,
        token={'access_token': 'fictional-access-token', 'refresh_token': 'fictional-refresh-token'},
    )

    async def find_session(session_id, user_id):
        return session if (session_id, user_id) == (session.id, session.user_id) else None

    lookup = AsyncMock(side_effect=find_session)
    monkeypatch.setattr(OAuthSessions, 'get_session_by_id_and_user_id', lookup)
    monkeypatch.setattr(OAuthSessions, 'delete_session_by_id', AsyncMock())

    async def update_session(session_id, token):
        assert session_id == session.id
        session.token = token
        return session

    update = AsyncMock(side_effect=update_session)
    monkeypatch.setattr(OAuthSessions, 'update_session_by_id', update)
    manager = OAuthManager.__new__(OAuthManager)
    refresh = AsyncMock(return_value={'access_token': 'fictional-refreshed-token'})
    monkeypatch.setattr(manager, '_perform_token_refresh', refresh)
    app = FastAPI()
    app.state.oauth_manager = manager
    acting.install(app)

    app.add_exception_handler(SoevApiError, platform_error)
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == '/v1/me':
            return httpx.Response(200, json={'person_id': 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'})
        if request.url.path == '/v1/chat':
            return httpx.Response(
                200, headers={'X-Soev-Thread-Id': 'fictional-thread'}, text='event: status\ndata: {"state":"idle"}\n\n'
            )
        if request.url.path == '/v1/completions/task':
            return httpx.Response(
                200,
                json={
                    'contract': 'soev.task/1',
                    'task': 'emoji',
                    'output': {'emoji': '🪶'},
                    'model': 'fictional-model',
                },
            )
        if request.url.path == '/v1/original':
            return httpx.Response(303, headers={'Location': '/v1/downloads/fictional-code'})
        if request.url.path == '/v1/downloads/fictional-code':
            return httpx.Response(200, content=b'fictional-original')
        if request.url.path == '/v1/failure':
            return httpx.Response(503, text='unavailable')
        if request.url.path == '/v1/transport':
            raise httpx.ReadTimeout('fictional-access-token')
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as api_http:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: api_http)
        api = SoevClient('https://soev.invalid', 'fictional-product-key')

        @app.middleware('http')
        async def api_key_header(request, call_next):
            if request.headers.get('x-api-key'):
                request.state.token = HTTPAuthorizationCredentials(scheme='Bearer', credentials='sk-fictional')
            return await call_next(request)

        @app.get('/call/{operation}')
        async def call(operation: str, resolved=Depends(auth.get_verified_user)):
            ref = identity.external_ref(resolved)
            if operation in {'bytes', 'upload'}:
                await _send_file(api, ref, operation)
            elif operation == 'task':
                turn = TaskTurnRequest(task='emoji', input={'text': 'Fictional text'}, idempotency_key='fictional-task')
                return await api.submit_task(turn, as_user=ref)
            elif operation == 'chat':
                assert [event.event async for event in api.chat_stream('/v1/chat', {}, as_user=ref)] == [
                    'connection',
                    'status',
                ]
            elif operation == 'original':
                return StreamingResponse(api.stream('/v1/original', as_user=ref))
            elif operation in {'failure', 'transport'}:
                with pytest.raises(SoevApiError):
                    await api.get(f'/v1/{operation}', as_user=ref)
                raise HTTPException(503)
            elif operation == 'product':
                await api.as_service().get('/v1/capabilities')
            else:
                await api.send('POST', '/v1/collections', {}, as_user=ref, idempotency_key='fictional-operation')
            return {}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid'
        ) as browser:
            browser.headers['Authorization'] = f'Bearer {auth.create_token({"id": user.id})}'
            browser.cookies.set('oauth_session_id', session.id)
            yield SimpleNamespace(
                app=app,
                browser=browser,
                api=api,
                requests=requests,
                session=session,
                lookup=lookup,
                refresh=refresh,
                update=update,
                auth=auth,
                user=user,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['json', 'bytes', 'task', 'chat', 'original', 'upload', 'product'])
async def test_oidc_token_reaches_every_api_transport(token_app, caplog, operation):
    ctx = token_app
    assert acting._token_resolver.get() is None
    with caplog.at_level(logging.DEBUG):
        response = await ctx.browser.get(f'/call/{operation}')
    assert response.status_code == 200
    request = ctx.requests[0]
    if operation == 'product':
        # A product-level call acts as OWUI itself: never with a person's token.
        assert 'X-Soev-User-Token' not in request.headers
        return
    assert request.headers['X-Soev-User-Token'] == 'fictional-access-token'
    assert request.headers['Authorization'] == 'Bearer fictional-product-key'
    if operation != 'product':
        assert 'X-Soev-' + 'Subject' not in request.headers
    if operation == 'bytes':
        assert request.content == b'fictional-image'
    if operation == 'chat':
        assert request.headers['Accept'] == 'text/event-stream'
    if operation == 'task':
        assert request.headers['Content-Type'] == 'application/json'
        assert request.headers.get('Accept') != 'text/event-stream'
    if operation == 'original':
        assert response.content == b'fictional-original'
        assert ctx.requests[1].headers['X-Soev-User-Token'] == 'fictional-access-token'
        assert ctx.requests[1].headers['Authorization'] == 'Bearer fictional-product-key'
        assert 'X-Soev-' + 'Subject' not in ctx.requests[1].headers
    assert 'fictional-access-token' not in caplog.text
    assert 'fictional-refresh-token' not in caplog.text
    ctx.refresh.assert_not_awaited()
    assert acting._token_resolver.get() is None
    await ctx.api.get('/v1/outside-request')
    assert 'X-Soev-User-Token' not in ctx.requests[-1].headers


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['microsoft', 'google', 'password', 'missing', 'other-user'])
async def test_non_oidc_or_unowned_sessions_have_no_token(token_app, provider):
    ctx = token_app
    if provider == 'password':
        ctx.browser.cookies.clear()
    elif provider == 'missing':
        ctx.browser.cookies.set('oauth_session_id', 'missing-session')
    elif provider == 'other-user':
        ctx.session.user_id = 'rowan'
    else:
        ctx.session.provider = provider
    response = await ctx.browser.get('/call/json')
    assert response.status_code == 401 and response.json()['code'] == 'sign_in'
    assert ctx.requests == []
    ctx.refresh.assert_not_awaited()
    if provider == 'password':
        ctx.lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('credential', ['bearer', 'cookie', 'x-api-key'])
async def test_api_keys_ignore_an_oidc_session_cookie(token_app, credential):
    ctx = token_app
    del ctx.browser.headers['Authorization']
    if credential == 'bearer':
        ctx.browser.headers['Authorization'] = 'Bearer sk-fictional'
    elif credential == 'cookie':
        ctx.browser.cookies.set('token', 'sk-fictional')
    else:
        ctx.browser.headers['x-api-key'] = 'sk-fictional'
    response = await ctx.browser.get('/call/json')
    assert response.status_code == 401 and response.json()['code'] == 'sign_in'
    assert ctx.requests == []
    ctx.lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('bearer', [True, False])
async def test_credential_precedence_matches_upstream_auth(token_app, bearer):
    ctx = token_app
    if bearer:
        ctx.browser.cookies.set('token', 'sk-fictional')
    else:
        ctx.browser.cookies.set('token', ctx.browser.headers.pop('Authorization').removeprefix('Bearer '))
        ctx.browser.headers['x-api-key'] = 'sk-fictional'
    assert (await ctx.browser.get('/call/json')).status_code == 200
    assert ctx.requests[0].headers['X-Soev-User-Token'] == 'fictional-access-token'


@pytest.mark.asyncio
async def test_expired_session_forwards_the_refreshed_access_token(token_app, caplog):
    ctx = token_app
    ctx.session.expires_at = int(time.time()) - 60
    with caplog.at_level(logging.DEBUG):
        assert (await ctx.browser.get('/call/json')).status_code == 200
    ctx.refresh.assert_awaited_once_with(ctx.session)
    ctx.update.assert_awaited_once_with(ctx.session.id, {'access_token': 'fictional-refreshed-token'})
    assert ctx.requests[0].headers['X-Soev-User-Token'] == 'fictional-refreshed-token'
    for secret in ('fictional-access-token', 'fictional-refreshed-token', 'fictional-refresh-token'):
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('expired', [False, True])
async def test_missing_or_failed_refresh_does_not_forward_old_credentials(token_app, expired):
    ctx = token_app
    if expired:
        ctx.session.expires_at = 0
        ctx.refresh.return_value = None
    else:
        ctx.session.token = {}
    response = await ctx.browser.get('/call/json')
    assert response.status_code == 401 and response.json()['code'] == 'sign_in'
    assert ctx.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['failure', 'transport'])
async def test_token_is_cleared_after_an_error_and_never_logged(token_app, caplog, failure):
    ctx = token_app
    with caplog.at_level(logging.DEBUG):
        assert (await ctx.browser.get(f'/call/{failure}')).status_code == 503
    assert ctx.requests[0].headers['X-Soev-User-Token'] == 'fictional-access-token'
    assert 'fictional-access-token' not in caplog.text
    assert acting._token_resolver.get() is None
    await ctx.api.get('/v1/outside-request')
    assert 'X-Soev-User-Token' not in ctx.requests[-1].headers


@pytest.mark.asyncio
async def test_request_started_chat_tasks_inherit_the_token(token_app):
    ctx = token_app
    release = asyncio.Event()
    tasks = []

    async def child():
        await release.wait()
        await ctx.api.get('/v1/after-request', as_user=acting.requester_ref())
        return await acting.user_token(acting.requester_ref())

    @ctx.app.get('/spawn')
    async def spawn(user=Depends(ctx.auth.get_verified_user)):
        assert await acting.user_token(acting.requester_ref()) == 'fictional-access-token'
        tasks.append(asyncio.create_task(child()))
        return {}

    try:
        assert (await ctx.browser.get('/spawn')).status_code == 200
        assert acting._token_resolver.get() is None
    finally:
        release.set()
        assert await asyncio.gather(*tasks) == ['fictional-access-token']
    assert ctx.requests[0].headers['X-Soev-User-Token'] == 'fictional-access-token'
    await ctx.api.get('/v1/outside-request')
    assert 'X-Soev-User-Token' not in ctx.requests[1].headers


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_share_tokens(token_app):
    ctx = token_app
    entered = asyncio.Event()
    release = asyncio.Event()

    @ctx.app.get('/held')
    async def held(request: Request, user=Depends(ctx.auth.get_verified_user)):
        if request.cookies.get('oauth_session_id'):
            entered.set()
            await release.wait()
        else:
            release.set()
        await ctx.api.get(
            '/v1/concurrent',
            params={'session': bool(request.cookies.get('oauth_session_id'))},
            as_user=acting.requester_ref(),
        )
        return {}

    pending = asyncio.create_task(ctx.browser.get('/held'))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ctx.app), base_url='https://owui.invalid'
        ) as other:
            assert (await other.get('/held', headers=dict(ctx.browser.headers))).status_code == 401
        assert (await pending).status_code == 200
    finally:
        release.set()
        await pending
    headers = {request.url.params['session']: request.headers for request in ctx.requests}
    assert 'false' not in headers
    assert headers['true']['X-Soev-User-Token'] == 'fictional-access-token'


@pytest.mark.asyncio
async def test_acting_for_another_user_sends_no_user_token(token_app):
    ctx = token_app

    @ctx.app.get('/as-owner')
    async def as_owner(user=Depends(ctx.auth.get_verified_user)):
        assert await acting.user_token(acting.requester_ref()) == 'fictional-access-token'
        assert await acting.user_token('owui:user:fictional-owner') is None
        return {}

    assert (await ctx.browser.get('/as-owner')).status_code == 200


@pytest.mark.asyncio
async def test_token_is_looked_up_only_when_a_call_needs_it(token_app):
    ctx = token_app

    @ctx.app.get('/no-soev-call')
    async def no_call(user=Depends(ctx.auth.get_verified_user)):
        return {}

    assert (await ctx.browser.get('/no-soev-call')).status_code == 200
    ctx.lookup.assert_not_awaited()
