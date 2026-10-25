from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from open_webui import config, env
from open_webui.routers import data_sources as router
from open_webui.soev import capabilities, data_sources


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
        gate.capabilities.discard(capabilities.DATA_SOURCES)
    elif closed == 'agent_api':
        gate.monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    assert await data_sources.allowed() is (closed == 'none')
    if closed != 'agent_api':
        gate.setting.assert_awaited_with('data_sources.enable')


@pytest.mark.asyncio
async def test_only_data_source_keys_are_chosen_once(gate):
    features = {'data_sources': ['ds:a', 'kb-private', 'ds:', 7, 'ds:a', 'ds:b']}
    assert await data_sources.chosen(features) == ['ds:a', 'ds:b']
    assert await data_sources.chosen({'data_sources': 'ds:a'}) == []
    assert await data_sources.chosen(None) == []


@pytest.mark.asyncio
async def test_nothing_is_chosen_while_the_gate_is_closed(gate):
    gate.setting.return_value = False
    assert await data_sources.chosen({'data_sources': ['ds:a']}) == []


@pytest.mark.asyncio
async def test_the_route_lists_the_callers_data_sources(gate):
    rows = [{'key': 'ds:a', 'name': 'Publications', 'description': 'Research'}, {'key': 'ds:b', 'name': 'B'}]
    listed = AsyncMock(return_value=rows)
    gate.monkeypatch.setattr(router.Knowledges, 'data_sources', listed)
    result = await router.list_data_sources(user=SimpleNamespace(id='alice'))
    assert [item.model_dump() for item in result] == [
        {'id': 'ds:a', 'name': 'Publications', 'description': 'Research'},
        {'id': 'ds:b', 'name': 'B', 'description': ''},
    ]
    listed.assert_awaited_once_with('alice')


@pytest.mark.asyncio
async def test_the_route_is_not_found_while_the_gate_is_closed(gate):
    gate.setting.return_value = False
    listed = AsyncMock()
    gate.monkeypatch.setattr(router.Knowledges, 'data_sources', listed)
    with pytest.raises(HTTPException) as error:
        await router.list_data_sources(user=SimpleNamespace(id='alice'))
    assert error.value.status_code == 404
    listed.assert_not_awaited()
