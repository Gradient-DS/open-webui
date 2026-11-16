"""A v2 turn in a clone of another user's chat: the owner's threads are theirs, the clone starts its own."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.models.chats import Chats
from open_webui.soev import agent_threads
from open_webui.test.soev.test_agent_v2 import Chat, chat, sent_text  # noqa: F401 (fixture)


async def alices_chat(chat: Chat) -> dict:
    """Alice's two turns, as the history a clone copies."""
    await chat.turn('Project Zonnebloem, budget 40k.', 'a1')
    await chat.turn('First step?', 'a2', 'a1')
    messages = {
        'u1': {'id': 'u1', 'parentId': None, 'role': 'user', 'content': 'Project Zonnebloem, budget 40k.'},
        'a1': {'id': 'a1', 'parentId': 'u1', 'role': 'assistant', 'content': 'Noted.', **chat.messages['chat', 'a1']},
        'u2': {'id': 'u2', 'parentId': 'a1', 'role': 'user', 'content': 'First step?'},
        'a2': {'id': 'a2', 'parentId': 'u2', 'role': 'assistant', 'content': 'Plan.', **chat.messages['chat', 'a2']},
    }
    return {'history': {'messages': messages, 'currentId': 'a2'}}


def cloned(chat: Chat, content_: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    for message_id, message in content_['history']['messages'].items():
        chat.messages['clone', message_id] = copy.deepcopy(message)
    monkeypatch.setattr(Chats, 'get_chat_by_id', AsyncMock(return_value=SimpleNamespace(chat=content_)))


@pytest.mark.asyncio
async def test_a_turn_in_a_clone_of_another_users_chat_starts_a_thread_with_the_conversation(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(chat, agent_threads.without_bookmarks(await alices_chat(chat)), monkeypatch)

    chunks = await chat.turn('What is my project called?', 'b1', 'a2', chat_id='clone', user_id='bob')

    assert chat.status is None, chunks
    assert sent_text(chat).startswith('Earlier in this conversation:\n\nuser: Project Zonnebloem, budget 40k.')
    assert sent_text(chat).endswith('\n\n---\n\nWhat is my project called?')
    bob = chat.bookmark('b1', 'clone')['thread_id']
    assert bob != chat.bookmark('a2')['thread_id']
    assert chat.api.chat.threads[bob]['owner'][1] == 'owui:user:bob'


@pytest.mark.asyncio
async def test_a_copy_keeping_the_owners_bookmarks_fails_for_another_user(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(chat, await alices_chat(chat), monkeypatch)

    chunks = await chat.turn('What is my project called?', 'b1', 'a2', chat_id='clone', user_id='bob')

    assert chunks == [{'error': {'code': 'not_found', 'message': 'The agent thread is unavailable.'}}]
