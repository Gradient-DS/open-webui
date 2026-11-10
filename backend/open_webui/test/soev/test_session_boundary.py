"""HTTP and websocket sessions share purpose and canonical-person boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from open_webui import config, env
from open_webui.soev import group_mirror
from open_webui.utils import auth

PERSON = 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'


@pytest_asyncio.fixture
async def boundary(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    monkeypatch.setattr(auth, 'SESSION_SECRET', 'fictional-session-boundary-secret-key')
    user = SimpleNamespace(id='fern', role='user', email='fern@example.invalid', person_id=PERSON)
    lookup = AsyncMock(return_value=user)
    activity = AsyncMock()
    snapshot = AsyncMock()
    monkeypatch.setattr(auth.Users, 'get_user_by_id', lookup)
    monkeypatch.setattr(auth.Users, 'get_user_by_api_key', lookup)
    monkeypatch.setattr(auth.Users, 'update_last_active_by_id', activity)
    monkeypatch.setattr(group_mirror, 'ensure_snapshot', snapshot)
    monkeypatch.setattr(auth, 'has_permission', AsyncMock(return_value=True))
    monkeypatch.setattr(auth, 'WEBUI_AUTH_TRUSTED_EMAIL_HEADER', None)
    monkeypatch.setattr(auth.Config, 'get_many', AsyncMock(return_value={'auth.enable_api_keys': True}))
    app = FastAPI()
    app.state.redis = None

    @app.get('/session')
    async def session(resolved=Depends(auth.get_current_user)):
        return {'id': resolved.id}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://owui.invalid') as client:
        yield SimpleNamespace(user=user, app=app, client=client, lookup=lookup, activity=activity, snapshot=snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', [False, True])
@pytest.mark.parametrize('purpose', ['2fa_pending', '2fa_setup_pending', 'skill_file_read', 'unknown', '', None])
async def test_purpose_tokens_never_authenticate_sessions(boundary, monkeypatch, platform, purpose):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid' if platform else '')
    token = auth.create_token({'id': 'fern', 'purpose': purpose})
    response = await boundary.client.get('/session', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == (403 if not platform and purpose in ('2fa_pending', '2fa_setup_pending') else 401)
    assert await auth.get_verified_user_by_token(token) is None
    boundary.activity.assert_not_awaited()
    boundary.snapshot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('person', [None, '', 'owui:user:fern', 'platform:user:fern', PERSON.upper()])
@pytest.mark.parametrize('credential', ['jwt', 'api_key'])
async def test_platform_requires_canonical_person_before_side_effects(boundary, person, credential):
    boundary.user.person_id = person
    token = auth.create_token({'id': 'fern'}) if credential == 'jwt' else 'sk-fictional-key'
    response = await boundary.client.get('/session', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    if credential == 'jwt':
        assert await auth.get_verified_user_by_token(token) is None
    boundary.activity.assert_not_awaited()
    boundary.snapshot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', [False, True])
@pytest.mark.parametrize('credential', ['jwt', 'api_key'])
async def test_full_sessions_and_api_keys_keep_working(boundary, monkeypatch, platform, credential):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid' if platform else '')
    if not platform:
        boundary.user.person_id = None
    token = auth.create_token({'id': 'fern'}) if credential == 'jwt' else 'sk-fictional-key'
    response = await boundary.client.get('/session', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200
    assert response.json() == {'id': 'fern'}
    if credential == 'jwt':
        assert await auth.get_verified_user_by_token(token) is boundary.user


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['automation', 'subagent'])
async def test_preexisting_background_credentials_cannot_be_platform_sessions(boundary, kind):
    token = auth.create_token({'id': 'fern', 'typ': kind})
    response = await boundary.client.get('/session', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    assert await auth.get_verified_user_by_token(token) is None
    boundary.activity.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', [False, True])
@pytest.mark.parametrize('revocation', ['token', 'user'])
async def test_full_sessions_still_obey_revocation(boundary, monkeypatch, platform, revocation):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid' if platform else '')
    token = auth.create_token({'id': 'fern'})
    claims = auth.decode_token(token)
    revoked_key = (
        f'{auth.REDIS_KEY_PREFIX}:auth:token:{claims["jti"]}:revoked'
        if revocation == 'token'
        else f'{auth.REDIS_KEY_PREFIX}:auth:user:fern:revoked_at'
    )
    redis = SimpleNamespace(get=AsyncMock(side_effect=lambda key: str(claims['iat']) if key == revoked_key else None))
    boundary.app.state.redis = redis
    response = await boundary.client.get('/session', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    assert await auth.get_verified_user_by_token(token, redis) is None
    boundary.activity.assert_not_awaited()
