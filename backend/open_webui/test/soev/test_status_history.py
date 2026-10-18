"""Persist one status line per tool call, as the frontend shows it live."""

from uuid import uuid4

import pytest
from open_webui.models.chats import ChatForm, Chats


@pytest.mark.asyncio
async def test_a_later_status_of_a_call_replaces_its_earlier_one():
    """Running then done keeps one entry per call; statuses without a call append."""
    chat_id = str(uuid4())
    history = {'messages': {'reply': {'role': 'assistant', 'content': ''}}}
    await Chats.insert_new_chat(chat_id, 'user', ChatForm(chat={'title': 'Chat', 'history': history}))
    statuses = [
        {'action': 'web_search', 'call_id': 'c1', 'done': False},
        {'action': 'fetch', 'call_id': 'c2', 'done': False},
        {'action': 'web_search', 'call_id': 'c1', 'done': True, 'results': '8'},
        {'action': 'summary', 'done': True},
        {'action': 'fetch', 'call_id': 'c2', 'done': True},
    ]
    for status in statuses:
        await Chats.add_message_status_to_chat_by_id_and_message_id(chat_id, 'reply', status)

    chat = await Chats.get_chat_by_id(chat_id)
    assert chat.chat['history']['messages']['reply']['statusHistory'] == [statuses[2], statuses[4], statuses[3]]
