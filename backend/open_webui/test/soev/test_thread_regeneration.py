"""Regeneration reuses an authorized durable input, including attachments and tool settings."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.soev import threads
from open_webui.utils import agent_v2


@pytest.mark.asyncio
async def test_replays_original_input_with_files_tools_and_approved_model(monkeypatch):
    value = {
        'text': 'Same question',
        'attachments': [{'key': 'attachment', 'name': 'Evidence.pdf'}],
        'images': [{'key': 'image'}],
        'knowledge': [{'key': 'knowledge'}],
        'texts': [{'id': 'note', 'text': 'Note contents'}],
        'tools': {'web_search': 'required', 'fetch': 'auto', 'search_mail': 'required'},
        'documents': 'auto',
    }
    original = copy.deepcopy(value)
    client = SimpleNamespace(
        chat_events=AsyncMock(
            return_value=[
                {
                    'position': 21,
                    'type': 'input',
                    'stream': 'root',
                    'payload': {'payload': value},
                }
            ]
        )
    )
    turn = agent_v2.AgentTurn(client, {'user_id': 'user'}, 'owui:user:user')
    monkeypatch.setattr(
        agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='user', role='admin'))
    )
    for name in ('_web_search_allowed', '_live_documents_allowed', '_live_mail_allowed', '_documents_allowed'):
        monkeypatch.setattr(agent_v2, name, AsyncMock(return_value=True))
    body = await agent_v2._regenerated_input(turn, {'thread_id': 'source', 'position': 21}, 'approved-model')
    assert body == {'input': original, 'model': 'approved-model'}
    client.chat_events.assert_awaited_once_with('source', as_user='owui:user:user', after=20, through=21)
    assert value == original

    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=False))
    body = await agent_v2._regenerated_input(turn, {'thread_id': 'source', 'position': 21}, 'approved-model')
    assert body['input']['tools']['web_search'] == 'off'
    assert value == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'source',
    [
        {'thread_id': 'source', 'position': 0},
        {'thread_id': 'source', 'position': True},
        {'thread_id': 'source', 'position': 21, 'payload': {'text': 'forged'}},
    ],
)
async def test_refuses_invalid_replay_without_reading_source(monkeypatch, source):
    client = SimpleNamespace(chat_events=AsyncMock())
    turn = agent_v2.AgentTurn(client, {'user_id': 'user'}, 'owui:user:user')
    monkeypatch.setattr(
        agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='user', role='admin'))
    )
    with pytest.raises(threads.ThreadError):
        await agent_v2._regenerated_input(turn, source, 'model')
    client.chat_events.assert_not_called()


@pytest.mark.asyncio
async def test_denied_permission_does_not_read_or_replay(monkeypatch):
    client = SimpleNamespace(chat_events=AsyncMock())
    turn = agent_v2.AgentTurn(client, {'user_id': 'user'}, 'owui:user:user')
    monkeypatch.setattr(
        agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='user', role='user'))
    )
    monkeypatch.setattr(agent_v2.Config, 'get', AsyncMock(return_value={}))
    permission = AsyncMock(return_value=False)
    monkeypatch.setattr(agent_v2, 'has_permission', permission)
    with pytest.raises(threads.ThreadError) as error:
        await agent_v2._regenerated_input(turn, {'thread_id': 'source', 'position': 21}, 'model')
    assert error.value.status == 403
    permission.assert_awaited_once_with('user', 'chat.regenerate_response', {})
    client.chat_events.assert_not_called()


@pytest.mark.asyncio
async def test_sent_routes_v2_replays_around_the_current_composer(monkeypatch):
    body = {'input': {'text': 'Same question', 'tools': {'web_search': 'required'}}}
    sentinel = object()
    turn = SimpleNamespace(run=lambda value, agent: (value, agent, sentinel))
    replay = AsyncMock(return_value=body)
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    monkeypatch.setattr(agent_v2, '_regenerated_input', replay)
    source = {'thread_id': 'source', 'position': 2}
    result = await agent_v2._sent(
        turn, 'Ignored edited text', {'user_message': {'regenerate_from': source}}, agent='agent', model='model'
    )
    assert result == (body, 'agent', sentinel)
    replay.assert_awaited_once_with(turn, source, 'model')


@pytest.mark.asyncio
async def test_projection_does_not_enrich_user_model():
    from open_webui.soev import chat_presentation

    page = threads.ProjectionPage(
        thread_id='source',
        generation='generation',
        title=None,
        next_cursor=None,
        messages=[
            threads.MessageView(position=2, content={'role': 'user', 'text': 'Question'}, finished=True, version=None)
        ],
    )
    assert not chat_presentation.needs_events(page)
    result = await chat_presentation.present_projection(
        page,
        [
            {
                'position': 2,
                'type': 'input',
                'stream': 'root',
                'payload': {'model': 'endpoint', 'payload': {'text': 'Question'}},
            },
        ],
    )
    assert result.messages[0].content == {'role': 'user', 'text': 'Question'}
