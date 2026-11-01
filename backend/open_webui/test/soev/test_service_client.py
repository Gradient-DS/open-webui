"""Service authority is independent of inherited user request context."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev import acting, capabilities, model_catalog
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.test.soev.fake_api import person_ref


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['get', 'send', 'discovery'])
async def test_service_calls_ignore_request_context(identity_config, monkeypatch, operation):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'contract': 'test', 'features': [], 'data': []})

    resolver = AsyncMock(return_value='fictional-user-token')
    client = SoevClient('https://soev.invalid', 'fictional-product-key')
    requester = acting._requester_ref.set('owui:user:fern')
    token = acting._token_resolver.set(resolver)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: http)
            if operation == 'get':
                await asyncio.create_task(client.as_service().get('/v1/jobs/fictional', as_user='owui:user:fern'))
            elif operation == 'send':
                await client.as_service().send(
                    'POST',
                    '/v1/jobs/fictional/commit',
                    None,
                    as_user='owui:user:fern',
                    idempotency_key='fictional-commit',
                )
            else:
                monkeypatch.setattr(capabilities, '_backend', None)
                monkeypatch.setitem(model_catalog.CATALOG_CACHE, 'expires_at', 0)
                await capabilities.refresh()
                await model_catalog.catalog()
            assert requests
            for request in requests:
                assert request.headers['Authorization'].startswith('Bearer ')
                assert 'X-Soev-' + 'Subject' not in request.headers
                assert 'X-Soev-User-Token' not in request.headers
                assert b'acting_as' not in request.content
            resolver.assert_not_awaited()
    finally:
        acting._token_resolver.reset(token)
        acting._requester_ref.reset(requester)


@pytest.mark.asyncio
@pytest.mark.parametrize('shared_with_service', [False, True])
async def test_service_cannot_borrow_a_persons_collection_access(identity_config, chat_http, shared_with_service):
    identity, _ = identity_config
    client = identity.build_client()
    ref = 'owui:user:fern'
    readers = [person_ref('fern')]
    if shared_with_service:
        readers.append(identity.config.SOEV_API_SERVICE_PRINCIPAL)
    await client.send(
        'POST',
        '/v1/collections',
        {
            'key': 'fictional-private',
            'name': 'Fictional notes',
            'visibility': 'restricted',
            'principals': readers,
            'writers': [person_ref('fern')],
        },
        as_user=ref,
        idempotency_key='fictional-private-create',
    )
    assert await client.get('/v1/collections/fictional-private', as_user=ref)
    requester = acting._requester_ref.set(ref)
    resolver = acting._token_resolver.set(AsyncMock(return_value='fictional-user-token'))
    try:
        service = client.as_service()
        if shared_with_service:
            assert await service.get('/v1/collections/fictional-private', as_user=ref)
        else:
            with pytest.raises(SoevApiError) as private:
                await service.get('/v1/collections/fictional-private', as_user=ref)
            with pytest.raises(SoevApiError) as missing:
                await service.get('/v1/collections/fictional-missing', as_user=ref)
            assert (private.value.status, private.value.code) == (missing.value.status, missing.value.code)
    finally:
        acting._token_resolver.reset(resolver)
        acting._requester_ref.reset(requester)
    assert 'X-Soev-' + 'Subject' not in chat_http.requests[-1].headers
    assert 'X-Soev-User-Token' not in chat_http.requests[-1].headers
