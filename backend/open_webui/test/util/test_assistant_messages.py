"""Persist split message identities using the real migrated schema."""

from uuid import uuid4

import pytest
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import ChatForm, Chats


@pytest.mark.asyncio
async def test_message_identity_roundtrip_and_partial_updates():
    """The assistant survives partial updates and reconstructs with its public key."""
    chat_id = str(uuid4())
    await ChatMessages.upsert_message(
        'reply', chat_id, 'user', {'role': 'assistant', 'model': 'llm', 'assistant_id': 'assistant'}
    )
    await ChatMessages.upsert_message('reply', chat_id, 'user', {'content': 'answer'})
    message = (await ChatMessages.get_messages_map_by_chat_id(chat_id))['reply']
    assert message['model'] == 'llm'
    assert message['assistant_id'] == 'assistant'
    await ChatMessages.upsert_messages(chat_id, 'user', {'reply': {'assistant_id': 'other'}})
    assert (await ChatMessages.get_message_by_id(f'{chat_id}-reply')).assistant_id == 'other'
    await ChatMessages.upsert_messages(chat_id, 'user', {'legacy': {'role': 'assistant', 'model': 'llm'}})
    assert 'assistant_id' not in (await ChatMessages.get_messages_map_by_chat_id(chat_id))['legacy']


@pytest.mark.asyncio
async def test_chat_binding_updates_only_meta():
    """Assistant binding preserves chat JSON and all unrelated metadata."""
    chat_id = str(uuid4())
    created = await Chats.insert_new_chat(
        chat_id, 'user', ChatForm(chat={'title': 'Original', 'history': {'messages': {}}})
    )
    await Chats.bind_chat_agent_by_id(chat_id, 'agent')
    assert await Chats.bind_chat_assistant_by_id(chat_id, 'assistant') is True
    updated = await Chats.get_chat_by_id(chat_id)
    assert updated.chat == created.chat
    assert updated.meta['assistant_id'] == 'assistant'
    assert updated.meta['agent_id'] == 'agent'
    assert await Chats.bind_chat_assistant_by_id(str(uuid4()), 'assistant') is False
