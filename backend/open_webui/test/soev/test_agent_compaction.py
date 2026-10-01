"""A chat routed to an agent keeps Open WebUI's own context compaction out of it: the agent owns its history, and
a compaction summary would otherwise land in the chat's prompt the agent receives as the user's instructions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.utils import middleware


class Stop(Exception):
    """Ends the payload pass at the pipeline filters, after compaction and the chat prompt are settled."""


@pytest.fixture
def compaction(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    compact = AsyncMock(side_effect=lambda request, user, messages, *args: (messages, 'SUMMARY', True))
    monkeypatch.setattr(middleware, 'compact_messages_for_request', compact)
    history = [{'role': 'user', 'content': 'earlier'}, {'role': 'user', 'content': 'now'}]
    monkeypatch.setattr(middleware, 'load_messages_from_db', AsyncMock(return_value=history))
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', AsyncMock(side_effect=Stop))
    return compact


async def settled(*, route_to_agent: bool) -> dict:
    """The metadata as the payload pass leaves it at the pipeline filters."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})), state=SimpleNamespace())
    form_data = {'model': 'llm', 'messages': [{'role': 'system', 'content': 'Ik ben jurist.'}]}
    metadata = {'chat_id': 'chat-1', 'user_message_id': 'u1', 'route_to_agent': route_to_agent}
    user = SimpleNamespace(id='alice', role='user')
    with pytest.raises(Stop):
        await middleware.process_chat_payload(request, form_data, user, metadata, {'id': 'llm', 'info': {}})
    return metadata


@pytest.mark.asyncio
async def test_an_agent_chat_is_never_compacted(compaction: AsyncMock) -> None:
    metadata = await settled(route_to_agent=True)
    compaction.assert_not_awaited()
    assert metadata['chat_system_prompt'] == 'Ik ben jurist.'


@pytest.mark.asyncio
async def test_a_chat_without_an_agent_is_still_compacted(compaction: AsyncMock) -> None:
    await settled(route_to_agent=False)
    compaction.assert_awaited_once()
