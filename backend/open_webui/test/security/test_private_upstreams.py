"""Exercise OWUI's proxy boundary with the private services unconfigured."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import agent_proxy, discovery
from open_webui.utils.auth import get_verified_user


@pytest.mark.parametrize(
    'module,prefix,suffix,method,body',
    [
        (agent_proxy, '/api/v1/agent', '/models', 'GET', None),
        (agent_proxy, '/api/v1/agent', '/gradient_agent_meta', 'GET', None),
        (agent_proxy, '/api/v1/agent', '/openapi.json', 'GET', None),
        (agent_proxy, '/api/v1/agent', '/chat/completions', 'POST', {'messages': [], 'model': 'test'}),
        (discovery, '/api/v1/discovery', '/documents', 'GET', None),
    ],
)
def test_proxy_routes_require_the_private_service(monkeypatch, module, prefix, suffix, method, body):
    monkeypatch.setattr(module.Config, 'get', AsyncMock(return_value=True))
    setting = 'AGENT_API_BASE_URL' if module is agent_proxy else 'SEARCH_API_BASE_URL'
    monkeypatch.setattr(module, setting, '')
    app = FastAPI()
    app.include_router(module.router, prefix=prefix)
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='owner', role='user')
    with TestClient(app) as browser:
        answer = browser.request(method, prefix + suffix, json=body)
    assert answer.status_code == 503
    assert setting + ' is not configured' in answer.json()['detail']
