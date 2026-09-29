"""Delete the soev-api agent threads a chat's messages bookmark when the chat or its messages go.

soev-api keeps a v2 agent's conversation in threads owned by the user; OWUI knows them only by the
`meta.agent_v2` bookmark on each assistant message. A regenerate or an edit forks a thread of its own, and a
clone of a chat shares the original's threads, so a thread goes only once no message of the user bookmarks it.

A temporary chat has no messages to hold bookmarks: they are kept beside the socket session pool for as long as
the chat's socket is connected, and its threads go when the socket does, as the chat itself does in the browser.
"""

import logging
from collections.abc import Collection, Iterable
from urllib.parse import quote

from open_webui.models.chat_messages import ChatMessageModel, ChatMessages
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError
from open_webui.utils.chat_id import get_temporary_chat_session_id

log = logging.getLogger(__name__)
_PAGE = 500


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
    return await _delete(f'owui:user:{user_id}', thread_ids)


async def _delete(as_user: str, thread_ids: Collection[str]) -> list[str]:
    if not thread_ids:
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
    """Hold a temporary chat's bookmark while its socket is connected; a turn that ends after it is gone deletes
    its thread at once."""
    from open_webui.socket.main import SESSION_POOL

    if get_temporary_chat_session_id(chat_id) not in SESSION_POOL:
        await _delete(as_user, {bookmark['thread_id']})
        return
    store = _temporary()
    entry = store[chat_id] if chat_id in store else {'as_user': as_user, 'bookmarks': {}}
    entry['bookmarks'][message_id] = bookmark
    store[chat_id] = entry


async def release_temporary(session_ids: Collection[str]) -> None:
    """The sockets are gone, so their temporary chats are: delete the chats' threads."""
    store = _temporary()
    for chat_id, entry in list(store.items()):
        if get_temporary_chat_session_id(chat_id) not in session_ids:
            continue
        del store[chat_id]
        threads = {bookmark['thread_id'] for bookmark in entry['bookmarks'].values()}
        if kept := await _delete(entry['as_user'], threads):
            log.warning('Agent threads of a closed temporary chat not deleted', extra={'count': len(kept)})
