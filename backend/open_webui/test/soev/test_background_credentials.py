"""Background execution cannot mint a human session in platform mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, Request
from open_webui import config, env
from open_webui.utils import auth, automations, subagents


@pytest.mark.asyncio
@pytest.mark.parametrize('agents', [False, True])
async def test_platform_automations_stay_inert_before_lookup_or_mint(monkeypatch, caplog, agents):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', agents)
    lookup, mint = AsyncMock(), Mock()
    monkeypatch.setattr(automations.Users, 'get_user_by_id', lookup)
    monkeypatch.setattr(automations, 'create_token', mint)
    await automations.scheduler_worker_loop(None)
    await automations.execute_automation(None, None)
    lookup.assert_not_awaited()
    mint.assert_not_called()
    assert {getattr(r, 'code', None) for r in caplog.records} >= {
        'platform_scheduler_credential_refused',
        'platform_automation_credential_refused',
    }


@pytest.mark.parametrize('agents', [False, True])
@pytest.mark.parametrize('internal', [False, True])
def test_platform_subagent_request_never_mints_a_session(monkeypatch, agents, internal):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', agents)
    mint = Mock()
    monkeypatch.setattr(subagents, 'create_token', mint)
    with pytest.raises(HTTPException) as caught:
        subagents._build_request(None, 'fern', internal=internal)
    assert caught.value.status_code == 403
    mint.assert_not_called()


@pytest.mark.asyncio
async def test_platform_without_agent_routing_refuses_subagent_before_io(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    reads, writes = AsyncMock(), Mock()
    monkeypatch.setattr(subagents, 'has_active_tasks', reads)
    monkeypatch.setattr(subagents.Config, 'get_many', reads)
    monkeypatch.setattr(subagents, 'get_async_db', writes)
    await subagents.process_pending_internal_messages(None, 'fable-chat', 'fern', {})
    for background in (False, True):
        with pytest.raises(HTTPException) as caught:
            await subagents.delegate(
                'Fictional task',
                '',
                background,
                request=None,
                user_data={},
                metadata={},
                parent_chat_id='fable-chat',
                parent_message_id=None,
            )
        assert caught.value.status_code == 403
    reads.assert_not_awaited()
    writes.assert_not_called()


@pytest.mark.parametrize('internal', [False, True])
def test_standalone_subagent_credentials_keep_existing_behavior(monkeypatch, internal):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    source = Request({'type': 'http', 'app': SimpleNamespace(), 'headers': []})
    request = subagents._build_request(source, 'fern', internal=internal)
    claims = auth.decode_token(request.state.token.credentials)
    assert claims['id'] == 'fern'
    assert claims['typ'] == 'subagent'
    assert claims['exp'] > claims['iat']
    assert getattr(request.state, 'internal', False) is internal
