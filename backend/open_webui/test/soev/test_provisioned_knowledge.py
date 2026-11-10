from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from open_webui import config, env
from open_webui.routers import provisioned_knowledge as router
from open_webui.soev import capabilities, provisioned_knowledge


@pytest.fixture
def gate(monkeypatch, platform_capabilities):
    """Every part of the data sources gate open; a test closes one."""
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    setting = AsyncMock(return_value=True)
    monkeypatch.setattr('open_webui.models.config.Config.get', setting)
    return SimpleNamespace(monkeypatch=monkeypatch, capabilities=platform_capabilities, setting=setting)


@pytest.mark.asyncio
@pytest.mark.parametrize('closed', ['none', 'setting', 'capability', 'agent_api'])
async def test_the_gate_needs_setting_capability_and_agent_route(gate, closed):
    if closed == 'setting':
        gate.setting.return_value = False
    elif closed == 'capability':
        gate.capabilities.discard(capabilities.PROVISIONED_KNOWLEDGE)
    elif closed == 'agent_api':
        gate.monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    assert await provisioned_knowledge.allowed() is (closed == 'none')
    if closed != 'agent_api':
        gate.setting.assert_awaited_with('provisioned_knowledge.enable')


@pytest.mark.asyncio
async def test_only_readable_provisioned_keys_are_chosen_once(gate):
    listed = AsyncMock(return_value=[{'key': 'source-a'}, {'key': 'source-b'}])
    gate.monkeypatch.setattr(router.Knowledges, 'provisioned_knowledge', listed)
    features = {'provisioned_knowledge': ['source-a', 'kb-private', 'ds:legacy', '', 7, 'source-a', 'source-b']}
    assert await provisioned_knowledge.chosen(features, user_id='user-a') == ['source-a', 'source-b']
    listed.assert_awaited_once_with('user-a')
    assert await provisioned_knowledge.chosen({'provisioned_knowledge': 'source-a'}) == []
    assert await provisioned_knowledge.chosen(None) == []


@pytest.mark.asyncio
async def test_nothing_is_chosen_while_the_gate_is_closed(gate):
    gate.setting.return_value = False
    assert await provisioned_knowledge.chosen({'provisioned_knowledge': ['source-a']}) == []


@pytest.mark.asyncio
async def test_the_route_lists_the_callers_provisioned_knowledge(gate):
    rows = [{'key': 'source-a', 'name': 'Publications', 'description': 'Research'}, {'key': 'source-b', 'name': 'B'}]
    listed = AsyncMock(return_value=rows)
    gate.monkeypatch.setattr(router.Knowledges, 'provisioned_knowledge', listed)
    result = await router.list_provisioned_knowledge(user=SimpleNamespace(id='user-a'))
    assert [item.model_dump() for item in result] == [
        {'id': 'source-a', 'name': 'Publications', 'description': 'Research'},
        {'id': 'source-b', 'name': 'B', 'description': ''},
    ]
    listed.assert_awaited_once_with('user-a')


@pytest.mark.asyncio
async def test_the_route_is_not_found_while_the_gate_is_closed(gate):
    gate.setting.return_value = False
    listed = AsyncMock()
    gate.monkeypatch.setattr(router.Knowledges, 'provisioned_knowledge', listed)
    with pytest.raises(HTTPException) as error:
        await router.list_provisioned_knowledge(user=SimpleNamespace(id='user-a'))
    assert error.value.status_code == 404
    listed.assert_not_awaited()
