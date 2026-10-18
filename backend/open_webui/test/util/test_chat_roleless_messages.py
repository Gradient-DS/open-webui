"""Regression tests for role-less messages in chat history.

Failed LLM turns on ≤0.9.5 could persist a message dict without a ``role``
key into a chat's embedded JSON history. Cloning such a chat copies the JSON
verbatim while ``import_chats`` used to skip role-less messages from the
chat_message row import — so history reconstruction merged the raw role-less
dict back in from the legacy JSON and the first ``message['role']`` index
raised ``KeyError('role')``, shown verbatim to the user. These tests pin the
three layers of the fix:

1. ``Chats.import_chats`` defaults a missing role instead of skipping the
   row (matching ``ChatMessages.upsert_message``).
2. ``load_messages_from_db`` defaults a missing role during reconstruction,
   healing chats poisoned before the fix (including existing broken clones).
3. The ``utils.misc`` message helpers tolerate role-less dicts.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from open_webui.models import chat_messages as chat_messages_module
from open_webui.models import chats as chats_module
from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import Chat, ChatImportForm, Chats


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """In-memory SQLite with the chat + chat_message tables, patched into
    both the ``chats`` and ``chat_messages`` modules."""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Chat.__table__.create)
        await conn.run_sync(ChatMessage.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        if db is not None:
            yield db
        else:
            async with Session() as s:
                yield s

    monkeypatch.setattr(chats_module, 'get_async_db_context', _get_async_db_context)
    monkeypatch.setattr(chat_messages_module, 'get_async_db_context', _get_async_db_context)

    yield Session

    await engine.dispose()


def _poisoned_history() -> dict:
    """History with a normal user message and a role-less failed turn."""
    return {
        'currentId': 'm2',
        'messages': {
            'm1': {
                'id': 'm1',
                'parentId': None,
                'childrenIds': ['m2'],
                'role': 'user',
                'content': 'hello',
                'timestamp': 1700000000,
            },
            'm2': {
                'id': 'm2',
                'parentId': 'm1',
                'childrenIds': [],
                # no 'role' — failed LLM turn on ≤0.9.5
                'content': '',
                'timestamp': 1700000001,
            },
        },
    }


@pytest.mark.asyncio
async def test_import_chats_defaults_missing_role(db_session):
    """Cloning must dual-write role-less messages with a defaulted role
    instead of skipping the row (which left the graph with gaps that get
    re-filled from the raw legacy JSON)."""
    form = ChatImportForm(chat={'title': 'clone', 'history': _poisoned_history()})
    [chat] = await Chats.import_chats('user-1', [form])

    rows = await ChatMessages.get_messages_by_chat_id(chat.id)
    roles_by_message = {row.id.removeprefix(f'{chat.id}-'): row.role for row in rows}

    assert set(roles_by_message) == {'m1', 'm2'}
    assert roles_by_message['m2'] == 'user'


@pytest.mark.asyncio
async def test_load_messages_from_db_defaults_missing_role(monkeypatch, db_session):
    """Already-poisoned clones: the row for the role-less message is absent,
    so reconstruction merges the raw dict from the legacy JSON. The rebuilt
    message list must still give every message a role."""
    from open_webui.utils import middleware

    async def fake_messages_map(chat_id):
        return _poisoned_history()['messages']

    monkeypatch.setattr(middleware.Chats, 'get_messages_map_by_chat_id', fake_messages_map)

    messages = await middleware.load_messages_from_db('chat-1', 'm2')

    assert messages is not None
    assert [m['role'] for m in messages] == ['user', 'user']


def test_misc_helpers_tolerate_roleless_messages():
    """The hard message['role'] indexes in utils.misc must not KeyError on a
    role-less dict."""
    from open_webui.utils.misc import (
        get_last_assistant_message,
        get_last_assistant_message_item,
        get_last_user_message_item,
        get_messages_content,
        get_system_message,
        remove_system_message,
    )

    messages = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'hello'},
        {'content': 'orphaned failed turn'},  # no role
        {'role': 'assistant', 'content': 'hi'},
    ]

    assert get_last_user_message_item(messages)['content'] == 'hello'
    assert get_last_assistant_message_item(messages)['content'] == 'hi'
    assert get_last_assistant_message(messages) == 'hi'
    assert get_system_message(messages)['content'] == 'sys'
    assert len(remove_system_message(messages)) == 3
    assert 'orphaned failed turn' in get_messages_content(messages)
