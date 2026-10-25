"""The OWUI proxy derives platform subjects from the session, never input or headers."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from open_webui import config
from open_webui.routers import live_documents, meetings
from open_webui.soev import identity
from open_webui.soev.client import SoevClient, close_client
from open_webui.utils.auth import get_verified_user


@pytest.fixture
def platform(monkeypatch, platform_capabilities):
    sent, replies = [], []

    def handle(request):
        sent.append(request)
        assert 'X-Soev-Subject' not in request.headers
        assert request.url.host == 'platform.invalid'
        return replies.pop(0)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )

    async def token(ref):
        return f'fictional-login:{ref}'

    monkeypatch.setattr('open_webui.soev.client.user_token', token)
    monkeypatch.setattr(identity, 'ensure_link', AsyncMock())
    monkeypatch.setattr(
        identity,
        'build_client',
        lambda **kwargs: SoevClient('https://platform.invalid', 'ci-key'),
    )
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.invalid')
    monkeypatch.setattr(meetings, 'is_feature_enabled', lambda feature: True)
    monkeypatch.setattr(live_documents.Config, 'get', AsyncMock(return_value=0))
    monkeypatch.setattr(
        live_documents.ingest,
        'ensure_attachments_collection',
        AsyncMock(side_effect=lambda user_id, client: f'attachments-{user_id}'),
    )

    def session(x_test_user: str = Header()):
        assert x_test_user in {'owner', 'intruder'}
        return SimpleNamespace(id=x_test_user, role='user', email=f'{x_test_user}@example.com')

    app = FastAPI()
    app.dependency_overrides[get_verified_user] = session
    app.include_router(meetings.router, prefix='/api/v1/meetings')
    app.include_router(live_documents.router, prefix='/api/v1/files')
    with TestClient(app) as browser:
        browser.portal.call(close_client)
        yield SimpleNamespace(browser=browser, sent=sent, replies=replies)
        browser.portal.call(close_client)
    assert not replies


@pytest.mark.parametrize('method,suffix', [('GET', ''), ('POST', '/inputs'), ('DELETE', '')])
def test_meeting_resource_operations_bind_the_actual_caller_and_preserve_refusals(platform, method, suffix):
    platform.replies.append(
        httpx.Response(
            404,
            json={'code': 'not_found', 'detail': 'No such thread'},
            headers={'Content-Type': 'application/problem+json'},
        )
    )
    kwargs = {'json': {'input': {'user_id': 'owner', 'text': 'spoof owner'}}} if method == 'POST' else {}
    response = platform.browser.request(
        method,
        f'/api/v1/meetings/thr-owned{suffix}',
        headers={'X-Test-User': 'intruder', 'X-Soev-Subject': 'signed:owui:user:owner', 'X-Acting-User-Id': 'owner'},
        **kwargs,
    )
    assert response.status_code == 404
    assert platform.sent[-1].headers['x-soev-user-token'] == 'fictional-login:owui:user:intruder'
    assert platform.sent[-1].url.path == f'/v1/chat/threads/thr-owned{suffix}'

    if method == 'GET':
        platform.replies.append(httpx.Response(200, json={'status': {'state': 'waiting'}, 'events': []}))
    elif method == 'POST':
        platform.replies.append(httpx.Response(200, headers={'X-Soev-Thread-Id': 'thr-owned'}, content=b''))
    else:
        platform.replies.append(httpx.Response(204))
    response = platform.browser.request(
        method, f'/api/v1/meetings/thr-owned{suffix}', headers={'X-Test-User': 'owner'}, **kwargs
    )
    assert 200 <= response.status_code < 300
    assert platform.sent[-1].headers['x-soev-user-token'] == 'fictional-login:owui:user:owner'


def test_picker_grant_and_idempotency_key_cannot_select_another_users_collection(platform):
    key = str(uuid4())
    body = {
        'grant_id': 'owners-grant',
        'drive_id': 'drive',
        'item_id': 'item',
        'name': 'document',
        'web_url': 'https://example.com/doc',
        'size': 1,
    }
    for actor in ('intruder', 'owner'):
        platform.replies.append(
            httpx.Response(
                403,
                json={'code': 'op_not_granted', 'detail': 'Grant does not permit attachment'},
                headers={'Content-Type': 'application/problem+json'},
            )
        )
        response = platform.browser.post(
            '/api/v1/files/onedrive/attach',
            json=body,
            headers={'X-Test-User': actor, 'Idempotency-Key': key, 'X-Soev-Subject': 'signed:owui:user:owner'},
        )
        assert response.status_code == 403
        request = platform.sent[-1]
        assert request.url.path == '/v1/attach'
        assert request.headers['x-soev-user-token'] == f'fictional-login:owui:user:{actor}'
        assert request.headers['idempotency-key'] == f'picker:{actor}:{key}'
        assert json.loads(request.content)['collection_key'] == f'attachments-{actor}'
