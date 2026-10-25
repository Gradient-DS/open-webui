"""Prepared import relay; platform turn admission remains required."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from open_webui.soev import acting, threads
from open_webui.soev.thread_routes import create_thread_router
from open_webui.soev.threads import ImportContext, ThreadClient, ThreadError
from open_webui.utils.auth import get_verified_user


@pytest.fixture
def context_http(chat_http, monkeypatch):
    token = acting._requester_ref.set('owui:user:fictional-fern')
    resolver = acting._token_resolver.set(AsyncMock(return_value='fictional-login:owui:user:fictional-fern'))
    client = ThreadClient('https://soev.invalid', 'soev_test_cred-runtime_test-secret')
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    monkeypatch.setattr(threads, 'build_client', lambda: client)
    yield chat_http, client
    acting._token_resolver.reset(resolver)
    acting._requester_ref.reset(token)


def imported():
    return {
        'source_thread_id': 'fictional-source',
        'generation': 'fictional-generation',
        'version': 'fictional-version',
        'through': 7,
        'messages': [
            {
                'position': 7,
                'content': {'text': 'Fictional context', 'citations': [{'id': 'fictional-citation'}]},
                'finished': True,
                'version': 'fictional-message-version',
            }
        ],
    }


def admit(fake, result=None):
    fake.thread_contexts['owui:user:fictional-fern', 'fictional-destination', 'fictional-source'] = (
        imported() if result is None else result
    )


@pytest.mark.asyncio
async def test_import_preserves_positions_citations_and_requester_authority(context_http):
    fake, client = context_http
    admit(fake)
    for _ in range(2):
        result = await client.import_context(
            'fictional-destination', ImportContext(source_thread_id='fictional-source')
        )
        assert result.model_dump() == imported()
    assert len(fake.requests) == 2  # Retries always reach platform authorization.
    for request in fake.requests:
        assert request.url.path == '/v1/threads/fictional-destination/context:import'
        assert json.loads(request.content) == {'source_thread_id': 'fictional-source'}
        assert request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:fictional-fern'
        assert request.headers['Authorization'] == 'Bearer soev_test_cred-runtime_test-secret'
        assert 'X-Soev-' + 'Subject' not in request.headers
        assert 'Idempotency-Key' not in request.headers


@pytest.mark.asyncio
async def test_missing_invisible_revoked_and_wrong_destination_release_nothing(context_http):
    fake, client = context_http
    admit(fake)
    for destination, source in [
        ('fictional-destination', 'fictional-invisible'),
        ('fictional-destination', 'fictional-missing'),
        ('fictional-other', 'fictional-source'),
    ]:
        with pytest.raises(ThreadError) as error:
            await client.import_context(destination, ImportContext(source_thread_id=source))
        assert (error.value.status, error.value.state) == (404, 'unavailable')
    fake.thread_contexts.clear()
    with pytest.raises(ThreadError, match='unavailable'):
        await client.import_context('fictional-destination', ImportContext(source_thread_id='fictional-source'))


@pytest.mark.asyncio
async def test_tokenless_import_never_calls_platform(context_http):
    fake, client = context_http
    acting._token_resolver.get().return_value = None
    with pytest.raises(ThreadError, match='sign_in'):
        await client.import_context('fictional-destination', ImportContext(source_thread_id='fictional-source'))
    assert not fake.requests


@pytest.mark.asyncio
async def test_import_retry_reauthorizes_after_account_change_and_platform_failure(context_http):
    fake, client = context_http
    admit(fake)
    form = ImportContext(source_thread_id='fictional-source')
    await client.import_context('fictional-destination', form)
    acting._token_resolver.get().return_value = 'fictional-login:owui:user:fictional-elm'
    with pytest.raises(ThreadError, match='unavailable'):
        await client.import_context('fictional-destination', form)
    acting._token_resolver.get().return_value = 'fictional-login:owui:user:fictional-fern'
    fake.failures.append(503)
    with pytest.raises(ThreadError, match='reconnect'):
        await client.import_context('fictional-destination', form)
    assert len(fake.requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['source', 'head', 'duplicate', 'order', 'shape'])
async def test_invalid_platform_result_releases_nothing(context_http, failure):
    fake, client = context_http
    result = imported()
    if failure == 'source':
        result['source_thread_id'] = 'fictional-other'
    elif failure == 'head':
        result['through'] = 6
    elif failure == 'duplicate':
        result['messages'] *= 2
    elif failure == 'order':
        result['messages'].append({**result['messages'][0], 'position': 6})
    else:
        result['raw_log'] = 'Fictional private data'
    admit(fake, result)
    with pytest.raises(ThreadError, match='reconnect'):
        await client.import_context('fictional-destination', ImportContext(source_thread_id='fictional-source'))


@pytest.mark.asyncio
async def test_browser_import_requires_json_bearer_and_is_private(context_http, monkeypatch):
    fake, _ = context_http
    app = FastAPI()
    app.include_router(create_thread_router(AsyncMock(return_value=[])), prefix='/api/v1/threads')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-fern')
    from open_webui.soev.client import _shared_client

    async with type(_shared_client())(
        transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid'
    ) as browser:
        path = '/api/v1/threads/fictional-destination/context:import'
        form = {'source_thread_id': 'fictional-source'}
        cookie_only = await browser.post(path, json=form)
        assert cookie_only.status_code == 401
        wrong_type = await browser.post(path, content=json.dumps(form), headers={'Authorization': 'Bearer fictional'})
        assert wrong_type.status_code == 415
        assert not fake.requests
        headers = {'Authorization': 'Bearer fictional'}
        refused = await browser.post(path, json=form, headers=headers)
        assert refused.status_code == 404
        assert refused.json() == {'state': 'unavailable'}
        admit(fake)
        accepted = await browser.post(path, json=form, headers=headers)
        assert accepted.json() == imported()
        invalid = await browser.post(path, json={**form, 'subject': 'fictional-other'}, headers=headers)
        assert invalid.status_code == 422
        monkeypatch.setattr(threads, 'enabled', lambda: False)
        disabled = await browser.post(path, json=form, headers=headers)
        assert disabled.status_code == 404
        assert len(fake.requests) == 2
        for response in (cookie_only, wrong_type, refused, accepted, invalid, disabled):
            assert response.headers['Cache-Control'] == 'private, no-store'
            assert response.headers['Vary'] == 'Authorization, Cookie'


@pytest.mark.asyncio
async def test_v2_tools_and_attachment_expansion_never_read_local_chats(monkeypatch):
    from open_webui.tools import builtin
    from open_webui.utils import agent_v2, tools

    monkeypatch.setattr(threads, 'enabled', lambda: True)
    local = AsyncMock(side_effect=AssertionError('Local chat content must not be read'))
    monkeypatch.setattr(builtin.Chats, 'get_chats_by_user_id_and_search_text', local)
    monkeypatch.setattr(builtin.Chats, 'get_chat_by_id_and_user_id', local)
    monkeypatch.setattr(builtin.Chats, 'get_chat_by_id', local)
    monkeypatch.setattr(agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='fictional-fern')))
    request = Request({'type': 'http', 'headers': []})
    user = {'id': 'fictional-fern', 'role': 'admin'}
    search = await builtin.search_chats('Fictional', __request__=request, __user__=user)
    read = await builtin.view_chat('fictional-source', __request__=request, __user__=user)
    assert json.loads(search) == json.loads(read) == {'error': 'Chat not found or access denied'}
    with pytest.raises(ThreadError, match='unavailable'):
        await agent_v2._texts([{'type': 'chat', 'id': 'fictional-source'}], user['id'])
    monkeypatch.setattr(tools.Config, 'get_many', AsyncMock(return_value={}))
    available = await tools.get_builtin_tools(request, {'__user__': user})
    assert 'search_chats' not in available and 'view_chat' not in available
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_chat_tools_keep_their_local_reads(monkeypatch):
    from open_webui.tools import builtin

    monkeypatch.setattr(threads, 'enabled', lambda: False)
    chat = SimpleNamespace(
        id='fictional-source',
        title='Fictional title',
        updated_at=1,
        created_at=1,
        chat={
            'history': {
                'currentId': 'fictional-message',
                'messages': {'fictional-message': {'role': 'user', 'content': 'Fictional text'}},
            }
        },
    )
    search = AsyncMock(return_value=[chat])
    read = AsyncMock(return_value=chat)
    monkeypatch.setattr(builtin.Chats, 'get_chats_by_user_id_and_search_text', search)
    monkeypatch.setattr(builtin.Chats, 'get_chat_by_id_and_user_id', read)
    request, user = Request({'type': 'http', 'headers': []}), {'id': 'fictional-fern'}
    found = json.loads(await builtin.search_chats('Fictional', __request__=request, __user__=user))
    viewed = json.loads(await builtin.view_chat(chat.id, __request__=request, __user__=user))
    assert found[0]['id'] == chat.id
    assert viewed['messages'] == [{'role': 'user', 'content': 'Fictional text'}]
    search.assert_awaited_once()
    read.assert_awaited_once_with(chat.id, user['id'])
