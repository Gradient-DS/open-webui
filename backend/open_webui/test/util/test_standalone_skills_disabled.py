"""[Gradient] Standalone skill entry points stay closed even with old settings and ids."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.models.skills import Skills
from open_webui.utils import features, middleware, tools


def test_old_environment_flags_cannot_restore_skills(monkeypatch):
    from open_webui.main import app

    for key in ('FEATURE_SKILLS', 'FEATURE_SKILL_FILES', 'ENABLE_SKILL_EXECUTION'):
        monkeypatch.setenv(key, 'true')
    assert features.is_feature_enabled('skills') is False
    assert not any(route.path.startswith('/api/v1/skills') for route in app.routes)


@pytest.mark.asyncio
@pytest.mark.parametrize('function_calling', ['native', 'legacy'])
async def test_payload_ignores_explicit_default_and_mentioned_skills(monkeypatch, function_calling):
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', AsyncMock(side_effect=lambda r, form, *a: form))
    monkeypatch.setattr(middleware.Config, 'get', AsyncMock(return_value=None))
    read = AsyncMock(side_effect=AssertionError('Standalone skills must not be collected'))
    monkeypatch.setattr(Skills, 'get_skills', read)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})), state=SimpleNamespace())
    form = {
        'model': 'invented-llm',
        'skill_ids': ['invented-standalone'],
        'messages': [{'role': 'user', 'content': 'Hello <$invented-standalone|Invented skill>'}],
    }
    metadata = {'chat_id': 'local:invented', 'route_to_agent': True, 'params': {'function_calling': function_calling}}
    model = {'id': 'invented-llm', 'info': {'meta': {'skillIds': ['invented-default']}}}
    body, resolved, _ = await middleware.process_chat_payload(
        request, form, SimpleNamespace(id='invented-user', role='user'), metadata, model
    )
    assert 'skill_ids' not in body
    assert not resolved['skill_ids']
    assert not resolved.get('skills')
    assert all(message['role'] != 'system' for message in body['messages'])
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_builtin_skill_tool_is_never_offered_for_stale_ids(monkeypatch):
    monkeypatch.setattr(tools.Config, 'get_many', AsyncMock(return_value={}))
    request = SimpleNamespace(state=SimpleNamespace())
    result = await tools.get_builtin_tools(request, {'__skill_ids__': ['invented-stale']})
    assert 'get_current_timestamp' in result
    assert 'view_skill' not in result
