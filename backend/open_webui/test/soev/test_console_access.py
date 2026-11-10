"""Console navigation uses only the requesting person's platform eligibility."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends
from open_webui.soev import console
from open_webui.test.soev.test_keycloak_token import token_app as token_app
from open_webui.utils.auth import get_verified_user


@pytest_asyncio.fixture
async def eligibility(token_app, monkeypatch):
    ctx = token_app
    state = SimpleNamespace(payload={'console_url': None}, requests=[])

    def handle(request):
        if request.url.path == '/v1/me':
            return httpx.Response(200, json={'person_id': ctx.user.person_id})
        if request.url.path == '/v1/groups':
            assert 'X-Soev-User-Token' not in request.headers
            return httpx.Response(200, json={'items': [], 'next_cursor': None})
        state.requests.append(request)
        return httpx.Response(200, json=state.payload)

    @ctx.app.get('/console')
    async def read(user=Depends(get_verified_user)):
        return await console.access(user)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: http)
        yield ctx, state


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_console_link_uses_the_callers_login_and_fresh_decision(eligibility, role):
    ctx, state = eligibility
    ctx.user.role = role
    state.payload = {'console_url': 'https://api.example.invalid/console/'}
    response = await ctx.browser.get('/console')
    assert response.status_code == 200, response.text
    assert response.json() == state.payload
    request = state.requests[-1]
    assert request.url.path == '/v1/console-access'
    assert request.headers['X-Soev-User-Token'] == 'fictional-access-token'
    state.payload = {'console_url': None}
    assert (await ctx.browser.get('/console')).json() == state.payload
    assert len(state.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'payload',
    [{}, {'console_url': 3}, {'console_url': 'https://bad.invalid/'}, {'console_url': '//bad.invalid/console/'}],
)
async def test_invalid_console_response_never_grants_navigation(eligibility, payload):
    ctx, state = eligibility
    state.payload = payload
    response = await ctx.browser.get('/console')
    assert response.status_code == 502, response.text
    assert 'console_url' not in response.json()
