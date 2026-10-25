"""The dark UI client against the 7a wire contract, using fictional data only."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev import acting, threads
from open_webui.soev.threads import IndexPage, Organization, ThreadClient, ThreadError, join_index


def message(position: int) -> dict:
    return {
        'position': position,
        'content': {'text': f'Fictional {position}'},
        'finished': True,
        'version': f'v{position}',
    }


def page(*positions: int, before: str | None = None) -> dict:
    return {
        'thread_id': 'fictional-thread',
        'generation': 'generation-one',
        'title': 'Fictional title',
        'preferences': {'fallback': True},
        'messages': [message(p) for p in positions],
        'next_cursor': before,
    }


def index_item(identifier='fictional-thread', parent=None, at=None) -> dict:
    return {
        'thread_id': identifier,
        'title': 'Fictional title',
        'updated_at': '2026-10-25T10:00:00Z',
        'parent_thread': parent,
        'parent_position': at,
    }


@pytest.fixture
def thread_http(monkeypatch):
    requests, responses = [], []

    def handle(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    requester = acting._requester_ref.set('owui:user:fern')
    resolver = acting._token_resolver.set(AsyncMock(return_value='fictional-login-token'))
    yield requests, responses, ThreadClient('https://soev.invalid', 'fictional-product-key')
    acting._token_resolver.reset(resolver)
    acting._requester_ref.reset(requester)


@pytest.mark.asyncio
async def test_windows_versions_and_conditional_reads(thread_http):
    requests, responses, client = thread_http
    responses.extend(
        [
            httpx.Response(200, json=page(3, 4, before='opaque-older'), headers={'ETag': '"fictional-etag"'}),
            httpx.Response(200, json=page(1, 2)),
            httpx.Response(304, headers={'ETag': '"fictional-etag"'}),
            httpx.Response(404, json={'code': 'not_found'}),
        ]
    )
    latest = await client.projection('fictional-thread', window=2)
    assert latest.etag == '"fictional-etag"'
    assert [m.version for m in latest.page.messages] == ['v3', 'v4']
    older = await client.projection('fictional-thread', window=2, before=latest.page.next_cursor)
    assert [m.position for m in older.page.messages] == [1, 2]
    unchanged = await client.projection('fictional-thread', window=2, etag=latest.etag)
    assert unchanged.not_modified and unchanged.page is None
    assert requests[1].url.params['before'] == 'opaque-older'
    assert requests[2].headers['If-None-Match'] == latest.etag
    # A matching validator never hides a later authorization refusal.
    with pytest.raises(ThreadError, match='unavailable'):
        await client.projection('fictional-thread', etag=latest.etag)
    assert len(requests) == 4


@pytest.mark.asyncio
async def test_shared_projection_may_omit_owner_preferences(thread_http):
    _, responses, client = thread_http
    shared = page(1)
    del shared['preferences']
    responses.append(httpx.Response(200, json=shared))
    assert (await client.projection('fictional-thread')).page.preferences is None


@pytest.mark.asyncio
async def test_index_search_forks_and_fork_creation(thread_http):
    requests, responses, client = thread_http
    child = index_item('fictional-child', 'fictional-thread', 4)
    responses.extend(
        [
            httpx.Response(200, json={'items': [index_item()], 'next_cursor': 'opaque-next'}),
            httpx.Response(200, json={'items': [child], 'next_cursor': None}),
            httpx.Response(200, json={'thread_id': 'fictional-child'}),
        ]
    )
    found = await client.index(q='Fictional', cursor='opaque-index', limit=1)
    assert found.next_cursor == 'opaque-next'
    forks = await client.forks('fictional-thread', at=4, cursor='opaque-forks', limit=1)
    assert forks.items[0].parent_position == 4
    assert (await client.fork('fictional-thread', at=4)).thread_id == 'fictional-child'
    assert dict(requests[0].url.params) == {'q': 'Fictional', 'cursor': 'opaque-index', 'limit': '1'}
    assert dict(requests[1].url.params) == {'at': '4', 'cursor': 'opaque-forks', 'limit': '1'}
    assert json.loads(requests[2].content) == {'at': 4}


@pytest.mark.asyncio
async def test_projection_uses_only_requester_login_token(thread_http):
    requests, responses, client = thread_http
    responses.extend([httpx.Response(200, json=page(1)) for _ in range(2)])
    await client.projection('fictional-thread')
    await client.projection('fictional-thread')
    for request in requests:
        assert request.headers['X-Soev-User-Token'] == 'fictional-login-token'
        assert request.headers['Authorization'] == 'Bearer fictional-product-key'
        assert 'X-Soev-' + 'Subject' not in request.headers
        assert 'Idempotency-Key' not in request.headers
    assert acting._token_resolver.get().await_count == 2
    resolver = acting._token_resolver.set(AsyncMock(return_value=None))
    try:
        with pytest.raises(ThreadError, match='sign_in'):
            await client.index()
        assert len(requests) == 2
    finally:
        acting._token_resolver.reset(resolver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,state',
    [
        (401, 'sign_in'),
        (403, 'unavailable'),
        (404, 'unavailable'),
        (422, 'invalid_request'),
        (503, 'reconnect'),
        (302, 'reconnect'),
    ],
)
async def test_refusals_never_return_upstream_details(thread_http, status, state):
    _, responses, client = thread_http
    responses.append(httpx.Response(status, json={'code': 'not_found', 'detail': 'Fictional private title'}))
    with pytest.raises(ThreadError) as caught:
        await client.projection('fictional-thread')
    assert caught.value.state == state
    assert str(caught.value) == state
    assert caught.value.status == (404 if status == 403 else status if status in {401, 404, 422} else 503)


@pytest.mark.asyncio
async def test_transport_invalid_payload_and_unsolicited_304_fail_closed(thread_http):
    _, responses, client = thread_http
    responses.extend(
        [
            httpx.ReadTimeout('fictional timeout'),
            httpx.Response(200, json={'raw_log': 'fictional'}),
            httpx.Response(304),
            httpx.Response(200, json={**page(1), 'thread_id': 'fictional-other'}),
        ]
    )
    for _ in range(4):
        with pytest.raises(ThreadError, match='reconnect'):
            await client.projection('fictional-thread')


def test_join_only_platform_authorized_items_preserves_order():
    platform = IndexPage(items=[index_item('fictional-visible')], next_cursor='opaque-next')
    joined = join_index(
        platform,
        [
            Organization(
                id='fictional-local', thread_id='fictional-visible', pinned=True, folder_id='fictional-folder'
            ),
            Organization(id='fictional-hidden', thread_id='fictional-invisible'),
        ],
    )
    assert len(joined['items']) == 1
    assert joined['items'][0]['organization']['pinned'] is True
    assert joined['items'][0]['organization']['folder_id'] == 'fictional-folder'
    assert joined['next_cursor'] == 'opaque-next'


def test_flag_off_by_default(monkeypatch):
    from open_webui import config, env

    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid')
    assert not threads.enabled()


@pytest.mark.asyncio
async def test_contract_fake_checks_admission_before_etag(thread_http, monkeypatch):
    from open_webui.soev import client as transport

    _, _, client = thread_http
    allowed = True
    calls = []

    def fake_api(request):
        calls.append(request)
        assert 'X-Soev-' + 'Subject' not in request.headers
        if request.headers.get('X-Soev-User-Token') != 'fictional-login-token':
            return httpx.Response(401, json={'code': 'invalid_credential'})
        if not allowed:
            return httpx.Response(404, json={'code': 'not_found'})
        before = request.url.params.get('before', '')
        if before not in {'', 'opaque-older'}:
            return httpx.Response(422, json={'code': 'invalid_field'})
        if request.headers.get('If-None-Match') == '"fictional-etag"':
            return httpx.Response(304)
        body = page(1, 2) if before else page(3, 4, before='opaque-older')
        return httpx.Response(200, json=body, headers={'ETag': '"fictional-etag"'})

    http_type = type(transport._shared_client())
    async with http_type(transport=httpx.MockTransport(fake_api)) as http:
        monkeypatch.setattr(transport, '_shared_client', lambda: http)
        monkeypatch.setattr(threads, '_shared_client', lambda: http)
        newest = await client.projection('fictional-thread', window=2)
        older = await client.projection('fictional-thread', window=2, before=newest.page.next_cursor)
        assert [m.position for m in older.page.messages] == [1, 2]
        assert (await client.projection('fictional-thread', etag=newest.etag)).not_modified
        allowed = False
        with pytest.raises(ThreadError, match='unavailable'):
            await client.projection('fictional-thread', etag=newest.etag)
        assert len(calls) == 4


@pytest.mark.asyncio
async def test_browser_boundary_is_private_and_gated(thread_http, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from open_webui.soev.thread_routes import create_thread_router
    from open_webui.utils.auth import get_verified_user

    requests, responses, client = thread_http
    records = AsyncMock(return_value=[Organization(id='fictional-chat', thread_id='fictional-thread', pinned=True)])
    app = FastAPI()
    app.include_router(create_thread_router(records), prefix='/api/v1/threads')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fern')
    monkeypatch.setattr(threads, 'build_client', lambda: client)
    # thread_http replaces AsyncClient; its original class is still the runtime type.
    from open_webui.soev.client import _shared_client

    http_type = type(_shared_client())
    async with http_type(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as browser:
        monkeypatch.setattr(threads, 'enabled', lambda: False)
        disabled = await browser.get('/api/v1/threads/fictional-thread')
        assert disabled.status_code == 404
        assert not requests
        monkeypatch.setattr(threads, 'enabled', lambda: True)
        responses.append(httpx.Response(200, json=page(1), headers={'ETag': '"fictional-etag"'}))
        projection = await browser.get('/api/v1/threads/fictional-thread')
        assert projection.json()['messages'][0]['version'] == 'v1'
        responses.append(httpx.Response(304))
        conditional = await browser.get(
            '/api/v1/threads/fictional-thread', headers={'If-None-Match': '"fictional-etag"'}
        )
        assert conditional.status_code == 304 and not conditional.content
        responses.append(httpx.Response(200, json={'items': [index_item()], 'next_cursor': None}))
        listed = await browser.get('/api/v1/threads')
        assert listed.json()['items'][0]['organization']['pinned'] is True
        records.assert_awaited_once_with('fern')
        invalid = await browser.get('/api/v1/threads?limit=0')
        assert invalid.json() == {'state': 'invalid_request'}
        for response in (disabled, projection, conditional, listed, invalid):
            assert response.headers['cache-control'] == 'private, no-store'
            assert response.headers['vary'] == 'Authorization, Cookie'


@pytest.mark.asyncio
async def test_authorize_relay_uses_login_token_only_and_is_private(thread_http, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from open_webui.soev.thread_routes import create_thread_router
    from open_webui.utils.auth import get_verified_user

    requests, responses, client = thread_http
    form = {
        'context_epoch': 'fictional-epoch',
        'threads': [{'thread_id': 'fictional-thread', 'generation': 'fictional-generation', 'versions': ['v1']}],
    }
    grant = {
        'principal': 'platform:user:fictional-fern',
        'session': 'fictional-session',
        'context_epoch': form['context_epoch'],
        'authorization_context': 'fictional-door-scope',
        'valid_until': '2026-10-25T11:00:00Z',
        'items': [{**form['threads'][0], 'status': 'ok'}],
    }
    app = FastAPI()
    app.include_router(create_thread_router(AsyncMock(return_value=[])), prefix='/api/v1/threads')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-fern')
    monkeypatch.setattr(threads, 'build_client', lambda: client)
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    from open_webui.soev.client import _shared_client

    http_type = type(_shared_client())
    async with http_type(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as browser:
        responses.append(httpx.Response(200, json=grant))
        with acting.acting_as('owui:user:fictional-other-owner'):
            authorized = await browser.post('/api/v1/threads:authorize', json=form)
        assert authorized.json() == grant
        assert requests[0].url.path == '/v1/threads:authorize'
        assert json.loads(requests[0].content) == form
        assert requests[0].headers['X-Soev-User-Token'] == 'fictional-login-token'
        assert requests[0].headers['Authorization'] == 'Bearer fictional-product-key'
        assert 'X-Soev-Subject' not in requests[0].headers
        resolver = acting._token_resolver.set(AsyncMock(return_value=None))
        try:
            tokenless = await browser.post('/api/v1/threads:authorize', json=form)
        finally:
            acting._token_resolver.reset(resolver)
        assert tokenless.status_code == 401
        assert tokenless.json() == {'state': 'sign_in'}
        assert len(requests) == 1
        invalid = await browser.post('/api/v1/threads:authorize', json={**form, 'subject': 'fictional-other'})
        assert invalid.status_code == 422
        monkeypatch.setattr(threads, 'enabled', lambda: False)
        disabled = await browser.post('/api/v1/threads:authorize', json=form)
        assert disabled.status_code == 404
        for response in (authorized, tokenless, invalid, disabled):
            assert response.headers['cache-control'] == 'private, no-store'
            assert response.headers['vary'] == 'Authorization, Cookie'
