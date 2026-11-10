"""Platform entry never offers local first-admin onboarding."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from open_webui import config, env


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', [False, True])
@pytest.mark.parametrize('has_users', [False, True])
@pytest.mark.parametrize('authenticated', [False, True])
@pytest.mark.parametrize('agent_enabled', [False, True])
async def test_config_onboarding(monkeypatch, platform, has_users, authenticated, agent_enabled):
    main = importlib.import_module('open_webui.main')
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid' if platform else '')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', agent_enabled)
    monkeypatch.setattr(main, 'decode_token', lambda _: {'id': 'fern'})
    monkeypatch.setattr(main, 'is_valid_token', AsyncMock(return_value=True))
    monkeypatch.setattr(
        main.Users,
        'get_user_by_id',
        AsyncMock(
            return_value=SimpleNamespace(
                id='fern',
                role='user',
                person_id='platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace',
            )
        ),
    )
    count = AsyncMock(return_value=has_users)
    monkeypatch.setattr(main.Users, 'has_users', count)
    monkeypatch.setattr(main.Config, 'get_many', AsyncMock(return_value={}))
    monkeypatch.setattr(main, 'feature_enabled', AsyncMock(return_value=False))
    monkeypatch.setattr(main.capabilities, 'has', AsyncMock(return_value=False))
    monkeypatch.setattr(main.model_catalog, 'default_models', AsyncMock(return_value=''))
    app = FastAPI()
    app.state.redis = None
    app.get('/api/config')(main.get_app_config)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://chat.example.invalid',
    ) as api:
        response = await api.get(
            '/api/config',
            headers={'Authorization': 'Bearer fictional-session'} if authenticated else {},
        )
    assert response.status_code == 200, response.text
    assert bool(response.json().get('onboarding')) is (not platform and not authenticated and not has_users)
    if platform or authenticated:
        count.assert_not_awaited()
