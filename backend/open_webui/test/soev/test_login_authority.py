"""User routes carry only login proof; legacy authority is confined to background work."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from open_webui.soev import acting, identity
from open_webui.soev.client import BackgroundSoevClient, SoevApiError, SoevClient
from open_webui.soev.knowledge_store import SoevKnowledgeTable
from open_webui.soev.request_cache import request_cache
from open_webui.test.soev.test_keycloak_token import token_app  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['knowledge', 'cloud', 'meeting'])
@pytest.mark.parametrize('logged_in', [True, False])
async def test_main_routes_use_only_login_authority(
    token_app,  # noqa: F811
    fake_api,
    monkeypatch,
    platform_capabilities,
    route,
    logged_in,
):
    from open_webui.routers import cloud_sync, knowledge, meetings
    from open_webui.utils import features

    ctx = token_app
    ctx.user.role = 'admin'
    monkeypatch.setattr(features, 'is_feature_enabled', lambda feature: True)
    monkeypatch.setattr(meetings, 'is_feature_enabled', lambda feature: True)
    monkeypatch.setattr(knowledge.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(knowledge, 'filter_allowed_access_grants', AsyncMock(return_value=[]))
    monkeypatch.setattr(knowledge, 'embed_knowledge_base_metadata', AsyncMock())
    monkeypatch.setattr(knowledge, 'publish_event', AsyncMock())
    monkeypatch.setattr(knowledge, 'Knowledges', SoevKnowledgeTable())
    ctx.app.include_router(knowledge.router, prefix='/knowledge')
    ctx.app.include_router(cloud_sync.router, prefix='/cloud')
    ctx.app.include_router(meetings.router, prefix='/meetings')
    fake_api.user_tokens['fictional-access-token'] = f'owui:user:{ctx.user.id}'
    signer = Mock(side_effect=AssertionError('User routes must not mint assertions'))
    monkeypatch.setattr(identity, 'mint_assertion', signer)
    if not logged_in:
        ctx.browser.cookies.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_api.handle)) as transport:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: transport)
        if route == 'knowledge':
            response = await ctx.browser.post(
                '/knowledge/create', json={'name': 'Fable notes', 'description': 'Fiction'}
            )
        elif route == 'cloud':
            response = await ctx.browser.get('/cloud/connections')
        else:
            response = await ctx.browser.post('/meetings', json={'input': {'text': 'Fictional meeting'}})
    if logged_in:
        assert response.status_code in {200, 201}, response.text
        assert fake_api.requests
        assert all(r.headers['X-Soev-User-Token'] == 'fictional-access-token' for r in fake_api.requests)
        assert all('X-Soev-Subject' not in r.headers for r in fake_api.requests)
    else:
        assert response.status_code == 401, response.text
        assert 'sign_in' in response.text
        assert not fake_api.requests
    signer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('context', ['none', 'requester', 'acting', 'explicit-token'])
async def test_background_subject_path_is_explicit_and_rejects_requests(monkeypatch, context):
    minter = Mock(return_value='fictional-background-assertion')
    client = BackgroundSoevClient('https://soev.invalid', 'fictional-key', subject_minter=minter)
    requester = acting._requester_ref.set('owui:user:fern' if context == 'requester' else None)
    actor = acting._acting_ref.set('owui:user:fern' if context == 'acting' else None)
    try:
        if context == 'none':
            headers = await client._headers('owui:user:fern')
            assert headers['X-Soev-Subject'] == 'fictional-background-assertion'
            assert 'X-Soev-User-Token' not in headers
            minter.assert_called_once_with('owui:user:fern')
        else:
            kwargs = {'user_access_token': 'fictional-token'} if context == 'explicit-token' else {}
            with pytest.raises(SoevApiError) as error:
                await client._headers('owui:user:fern', **kwargs)
            assert (error.value.status, error.value.code) == (401, 'sign_in')
            minter.assert_not_called()
    finally:
        acting._acting_ref.reset(actor)
        acting._requester_ref.reset(requester)


@pytest.mark.asyncio
async def test_cached_service_result_cannot_bypass_missing_or_changed_user_token(monkeypatch):
    sent = []

    def handle(request):
        sent.append(request)
        return httpx.Response(200, json={'authority': request.headers.get('X-Soev-User-Token', 'service')})

    resolver = AsyncMock(return_value='fictional-fern-token')
    requester = acting._requester_ref.set('owui:user:fern')
    token = acting._token_resolver.set(resolver)
    client = SoevClient('https://soev.invalid', 'fictional-key')
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
            monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: transport)
            with request_cache():
                assert await client.as_service().get('/v1/collections') == {'authority': 'service'}
                assert await client.get('/v1/collections') == {'authority': 'fictional-fern-token'}
                resolver.return_value = None
                with pytest.raises(SoevApiError) as error:
                    await client.get('/v1/collections')
                assert (error.value.status, error.value.code) == (401, 'sign_in')
                resolver.return_value = 'fictional-refreshed-token'
                assert await client.get('/v1/collections') == {'authority': 'fictional-refreshed-token'}
                with pytest.raises(SoevApiError):
                    await client.get('/v1/collections', as_user='owui:user:moss')
        assert len(sent) == 3
    finally:
        acting._token_resolver.reset(token)
        acting._requester_ref.reset(requester)
