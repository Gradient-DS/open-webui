"""Request identity follows the real FastAPI authentication dependency chain."""

import asyncio
import importlib
import inspect
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def acting_app(identity_config, monkeypatch):
    """Keep upstream authentication and role checks while faking the API-key user lookup."""
    identity, _ = identity_config
    acting = importlib.import_module('open_webui.soev.acting')
    auth = importlib.import_module('open_webui.utils.auth')
    user = identity.UserModel(
        id='alice',
        name='Alice',
        email='alice@example.invalid',
        role='user',
        created_at=0,
        updated_at=0,
        last_active_at=0,
    )
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(auth, 'get_current_user_by_api_key', lookup)
    app = FastAPI()
    acting.install(app)

    @app.get('/verified')
    async def verified(resolved=Depends(auth.get_verified_user)):
        return {'ref': acting.requester_ref(), 'same_user': resolved is user}

    @app.get('/anonymous')
    async def anonymous():
        return {'ref': acting.requester_ref()}

    return app, acting, auth, user, lookup


def test_a_request_sets_the_acting_ref_only_for_its_handler(acting_app):
    """Verified requests carry their user's ref and leave outside and subsequent anonymous contexts unset."""
    app, acting, _, user, lookup = acting_app
    assert acting.requester_ref() is None
    with TestClient(app) as client:
        response = client.get('/verified', headers={'Authorization': 'Bearer sk-test'})
        assert response.status_code == 200
        assert response.json()['ref'] == 'owui:user:alice'
        assert acting.requester_ref() is None
        assert client.get('/anonymous').json() == {'ref': None}
        user.id = 'bob'
        assert client.get('/verified', headers={'Authorization': 'Bearer sk-test'}).json()['ref'] == 'owui:user:bob'
        assert client.get('/verified').status_code == 401
    assert lookup.await_count == 2
    assert acting.requester_ref() is None


@pytest.mark.asyncio
async def test_request_resets_the_acting_ref_in_the_callers_context(acting_app):
    """Request cleanup resets the caller's context while an existing child task retains its copied context."""
    app, acting, auth, _, _ = acting_app
    released = asyncio.Event()
    tasks = []

    async def child():
        await released.wait()
        return acting.requester_ref()

    @app.get('/spawn')
    async def spawn(user=Depends(auth.get_verified_user)):
        tasks.append(asyncio.create_task(child()))
        return {'ref': acting.requester_ref()}

    assert acting.requester_ref() is None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/spawn', headers={'Authorization': 'Bearer sk-test'})
            assert response.status_code == 200
            assert response.json() == {'ref': 'owui:user:alice'}
            assert acting.requester_ref() is None
            assert (await client.get('/anonymous')).json() == {'ref': None}
    finally:
        released.set()
        refs = await asyncio.gather(*tasks)
    assert refs == ['owui:user:alice']


def test_the_override_returns_the_upstream_user_unchanged(acting_app):
    """The override resolves a distinct callable with the upstream signature and preserves object identity."""
    app, _, auth, _, lookup = acting_app
    override = app.dependency_overrides[auth.get_current_user]
    resolver = inspect.signature(override).parameters['user'].default.dependency
    assert resolver is not auth.get_current_user
    assert inspect.signature(resolver) == inspect.signature(auth.get_current_user)
    with TestClient(app) as client:
        response = client.get('/verified', headers={'Authorization': 'Bearer sk-test'})
    assert response.status_code == 200
    assert response.json()['same_user'] is True
    assert lookup.await_count == 1


@pytest.mark.parametrize(
    ('setting', 'value'),
    [
        ('SOEV_API_KEY', ''),
        ('SOEV_API_KEY', 'malformed-key'),
    ],
)
def test_integration_startup_rejects_invalid_credentials(identity_config, monkeypatch, setting, value):
    from open_webui.soev.acting import install

    identity, _ = identity_config
    monkeypatch.setattr(identity.config, setting, value)
    with pytest.raises(ValueError, match=setting):
        install(FastAPI())


def test_disabled_integration_does_not_require_credentials(identity_config, monkeypatch):
    from open_webui.soev.acting import install

    identity, _ = identity_config
    for setting in ('SOEV_API_URL', 'SOEV_API_KEY'):
        monkeypatch.setattr(identity.config, setting, '')
    install(FastAPI())


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_token_lookup(identity_config, monkeypatch):
    """Parallel soev calls in one request all receive the token, never an unfinished lookup."""
    from types import SimpleNamespace

    acting = importlib.import_module('open_webui.soev.acting')
    sessions = importlib.import_module('open_webui.models.oauth_sessions')
    started = asyncio.Event()

    async def slow_token(user_id, session_id):
        started.set()
        await asyncio.sleep(0.01)
        return {'access_token': 'fictional-access-token'}

    monkeypatch.setattr(
        sessions.OAuthSessions,
        'get_session_by_id_and_user_id',
        AsyncMock(return_value=SimpleNamespace(provider='oidc')),
    )
    manager = SimpleNamespace(get_oauth_token=AsyncMock(side_effect=slow_token))
    request = SimpleNamespace(
        cookies={'oauth_session_id': 'fictional-session'},
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(oauth_manager=manager)),
    )
    resolve = acting.oidc_token_resolver(
        request, SimpleNamespace(id='alice'), SimpleNamespace(credentials='fictional-jwt')
    )
    tokens = await asyncio.gather(resolve(), resolve(), resolve())
    assert started.is_set()
    assert tokens == ['fictional-access-token'] * 3
    manager.get_oauth_token.assert_awaited_once()
