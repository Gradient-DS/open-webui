"""A chat routed to an agent sends the user's memories with the chat's prompt: the agent never reads the messages."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.utils import middleware
from open_webui.utils.misc import add_or_update_system_message


async def remember(request, form_data, user, model):
    form_data['messages'] = add_or_update_system_message(
        '<memory_context>Woont in Utrecht</memory_context>', form_data['messages'], append=True
    )
    return form_data


@pytest.fixture(autouse=True)
def memories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(middleware, 'add_memory_context', remember)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(
        middleware, 'process_pipeline_inlet_filter', AsyncMock(side_effect=lambda r, form_data, *a: form_data)
    )
    monkeypatch.setattr(middleware.Config, 'get', AsyncMock(return_value=True))


async def settled(system: list[dict]) -> tuple[dict, dict]:
    """The form data and metadata as the payload pass returns them for the agent."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})), state=SimpleNamespace())
    form_data = {
        'model': 'agent',
        'messages': [*system, {'role': 'user', 'content': 'Waar woon ik?'}],
        'features': {'memory': True},
    }
    metadata = {'chat_id': 'local:chat-1', 'route_to_agent': True}
    user = SimpleNamespace(id='alice', role='admin')
    form_data, metadata, _ = await middleware.process_chat_payload(
        request, form_data, user, metadata, {'id': 'agent', 'info': {}}
    )
    return form_data, metadata


@pytest.mark.asyncio
async def test_memories_follow_the_chats_prompt() -> None:
    _, metadata = await settled([{'role': 'system', 'content': 'Ik ben jurist.'}])
    assert metadata['chat_system_prompt'] == 'Ik ben jurist.\n\n<memory_context>Woont in Utrecht</memory_context>'


@pytest.mark.asyncio
async def test_memories_alone_make_the_chats_prompt() -> None:
    _, metadata = await settled([])
    assert metadata['chat_system_prompt'] == '<memory_context>Woont in Utrecht</memory_context>'
