"""Delete the soev-api agent threads a chat's messages bookmark when the chat or its messages go.

soev-api keeps a v2 agent's conversation in threads owned by the user; OWUI knows them only by the
`meta.agent_v2` bookmark on each assistant message. A regenerate or an edit forks a thread of its own, and a
clone of a chat shares the original's threads, so a thread goes only once no message of the user bookmarks it. A clone
of another user's chat bookmarks none: soev-api keeps the original's threads from the new owner, so the clone's first
turn starts a thread seeded with the conversation so far, as a chat from before the agent cutover does.

A temporary chat has no messages to hold bookmarks: they are kept beside the socket session pool for as long as
the chat's socket is connected. The platform policy owns temporary thread expiry under v2.
"""

import logging
from collections.abc import Collection, Iterable
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.chat_messages import ChatMessageModel, ChatMessages
from open_webui.soev import acting, identity
from open_webui.soev.availability import configured
from open_webui.soev.client import SoevApiError
from open_webui.soev.policy_jobs import require_thread_owner, v2_enabled
from open_webui.soev.thread_content import refuse_local_content
from open_webui.utils.chat_id import get_temporary_chat_session_id

log = logging.getLogger(__name__)
_PAGE = 500


def without_bookmarks(chat: dict) -> dict:
    """The chat's content with no message bookmarking a thread, for a copy that another user will own."""
    history = chat.get('history') if isinstance(chat.get('history'), dict) else None
    unmarked = dict(chat)
    if history and isinstance(history.get('messages'), dict):
        unmarked['history'] = {**history, 'messages': {k: _unmarked(m) for k, m in history['messages'].items()}}
    if isinstance(chat.get('messages'), list):
        unmarked['messages'] = [_unmarked(message) for message in chat['messages']]
    return unmarked


def _unmarked(message: Any) -> Any:
    if not isinstance(message, dict) or not isinstance(message.get('meta'), dict) or 'agent_v2' not in message['meta']:
        return message
    return {**message, 'meta': {key: value for key, value in message['meta'].items() if key != 'agent_v2'}}


def _bookmarked(messages: Iterable[ChatMessageModel]) -> set[str]:
    return {
        bookmark['thread_id']
        for message in messages
        if isinstance(bookmark := (message.meta or {}).get('agent_v2'), dict) and bookmark.get('thread_id')
    }


async def chat_thread_ids(chat_id: str) -> set[str]:
    """The threads the chat's messages bookmark."""
    return _bookmarked(await ChatMessages.get_messages_by_chat_id(chat_id))


async def user_thread_ids(user_id: str, *, excluding_chat: str | None = None) -> set[str]:
    """The threads the user's messages bookmark, leaving out one chat's."""
    threads: set[str] = set()
    skip = 0
    while page := await ChatMessages.get_messages_by_user_id(user_id, skip=skip, limit=_PAGE):
        threads |= _bookmarked(message for message in page if message.chat_id != excluding_chat)
        skip += len(page)
    return threads


async def delete_threads(user_id: str, thread_ids: Collection[str]) -> list[str]:
    """Delete the threads as their owner; one already gone counts as deleted.

    Returns the threads a transient failure kept, for the caller to retry. A refusal is logged and not retried:
    it would refuse again.
    """
    if v2_enabled() and thread_ids:
        await require_thread_owner(user_id)
        client = identity.build_client()
        for thread_id in sorted(thread_ids):
            try:
                await client.chat_delete(
                    f'/v1/chat/threads/{quote(thread_id, safe="")}', as_user=f'owui:user:{user_id}'
                )
            except SoevApiError as error:
                if error.status != 404:
                    raise
        return []
    return await _delete(f'owui:user:{user_id}', thread_ids)


async def _delete(as_user: str, thread_ids: Collection[str]) -> list[str]:
    if not configured() or not thread_ids:
        return []
    client = identity.build_client()
    kept = []
    for thread_id in sorted(thread_ids):
        try:
            await client.chat_delete(f'/v1/chat/threads/{quote(thread_id, safe="")}', as_user=as_user)
        except SoevApiError as error:
            if error.status == 404:
                continue
            if error.status == 429 or error.status >= 500:
                kept.append(thread_id)
                log.warning('Agent thread not deleted yet', extra={'thread_id': thread_id, 'status': error.status})
            else:
                log.error('Agent thread deletion refused', extra={'thread_id': thread_id, 'status': error.status})
    return kept


async def delete_chat_threads(user_id: str, chat_id: str) -> list[str]:
    """Before a chat is hard-deleted: delete its threads no other chat of the user bookmarks."""
    if v2_enabled():
        await delete_chats_threads(user_id, [chat_id])
        return []
    threads = await chat_thread_ids(chat_id)
    if not threads:
        return []
    return await delete_threads(user_id, threads - await user_thread_ids(user_id, excluding_chat=chat_id))


async def delete_released_threads(user_id: str, before: Collection[str]) -> list[str]:
    """After messages were deleted: delete those of the threads they bookmarked that no message still does."""
    if not before:
        return []
    return await delete_threads(user_id, set(before) - await user_thread_ids(user_id))


def _temporary() -> dict:
    from open_webui.socket.main import TEMPORARY_AGENT_THREADS

    return TEMPORARY_AGENT_THREADS


def temporary_bookmark(chat_id: str, message_id: str) -> dict | None:
    """The bookmark a temporary chat's message holds, if its chat is still open."""
    store = _temporary()
    return store[chat_id]['bookmarks'].get(message_id) if chat_id in store else None


async def remember_temporary(chat_id: str, message_id: str, as_user: str, bookmark: dict) -> None:
    """Hold bookmarks only while the socket is connected; the platform owns expiry."""
    from open_webui.socket.main import SESSION_POOL

    if get_temporary_chat_session_id(chat_id) not in SESSION_POOL:
        if not v2_enabled():
            await _delete(as_user, {bookmark['thread_id']})
        return
    store = _temporary()
    entry = store[chat_id] if chat_id in store else {'bookmarks': {}}
    if not v2_enabled():
        entry['as_user'] = as_user
    entry['bookmarks'][message_id] = bookmark
    store[chat_id] = entry


async def release_temporary(session_ids: Collection[str]) -> None:
    """Forget local bookmarks of disconnected sockets."""
    store = _temporary()
    for chat_id in list(store):
        if get_temporary_chat_session_id(chat_id) not in session_ids:
            continue
        entry = store.pop(chat_id)
        if not v2_enabled():
            kept = await _delete(entry['as_user'], {bookmark['thread_id'] for bookmark in entry['bookmarks'].values()})
            if kept:
                log.warning('Agent threads of a closed temporary chat not deleted', extra={'count': len(kept)})


async def delete_chats_threads(user_id: str, chat_ids: Collection[str], *, db: AsyncSession | None = None) -> None:
    """Delete only threads unreferenced by every surviving bookmark, before local changes."""
    if not v2_enabled() or not chat_ids:
        return
    from open_webui.soev.retention import known_deleted

    rows = await _user_messages(user_id, db=db)
    selected = [row for row in rows if row.chat_id in chat_ids]

    threads = _bookmarked(selected) - await known_deleted()
    # Even a shared bookmark prevents another person's admin from erasing its local chat.
    if threads:
        await require_thread_owner(user_id)
    surviving = _bookmarked(row for row in rows if row.chat_id not in chat_ids)
    removed = threads - surviving
    await delete_threads(user_id, removed)
    if removed:
        from open_webui.soev.retention import remember_deleted

        await remember_deleted(removed)


async def delete_message_threads(
    user_id: str, chat_id: str, message_id: str, *, db: AsyncSession | None = None
) -> None:
    """Use the same history deletion rules as the local mutation, without changing history."""
    if not v2_enabled():
        return
    import copy

    from open_webui.models.chats import Chats
    from open_webui.soev.retention import known_deleted

    chat = await Chats.get_chat_by_id(chat_id, db=db)
    history = copy.deepcopy((chat.chat or {}).get('history') or {})
    deleted_ids = Chats.delete_message_from_history(history, message_id)
    rows = await _user_messages(user_id, db=db)
    selected = [row for row in rows if row.chat_id == chat_id and row.id in deleted_ids]
    gone = await known_deleted()
    chat_threads = _bookmarked(row for row in rows if row.chat_id == chat_id) - gone
    if chat_threads and acting.requester_ref() != f'owui:user:{user_id}':
        await require_thread_owner(user_id)
    threads = _bookmarked(selected) - gone
    if threads:
        await require_thread_owner(user_id)
    surviving = _bookmarked(row for row in rows if not (row.chat_id == chat_id and row.id in deleted_ids))
    removed = threads - surviving
    await delete_threads(user_id, removed)
    if removed:
        from open_webui.soev.retention import remember_deleted

        await remember_deleted(removed)


async def deletion_chat_ids(
    user_id: str, *, folder_ids: Collection[str] | None = None, db: AsyncSession | None = None
) -> list[str]:
    refuse_local_content()
    from sqlalchemy import select

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.chats import Chat, Chats

    async with get_async_db_context(db) as session:
        stmt = select(Chat.id).where(Chat.user_id == user_id)
        if folder_ids is not None:
            stmt = stmt.where(Chat.folder_id.in_(folder_ids))
        ids = list((await session.execute(stmt)).scalars())
    for chat_id in list(ids):
        ids.extend(await Chats.get_internal_chat_ids_by_parent_id(chat_id, user_id))
    return list(set(ids))


async def _user_messages(user_id: str, *, db: AsyncSession | None = None) -> list:
    """The bookmarks of one user's chats: threads belong to one person, so no other user's chat holds them."""
    refuse_local_content()
    from sqlalchemy import select

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.chat_messages import ChatMessage
    from open_webui.models.chats import Chat

    async with get_async_db_context(db) as session:
        stmt = (
            select(ChatMessage.id, ChatMessage.chat_id, ChatMessage.meta)
            .join(Chat, Chat.id == ChatMessage.chat_id)
            .where(Chat.user_id == user_id)
        )
        return list((await session.execute(stmt)).all())
