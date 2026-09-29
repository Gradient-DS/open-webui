"""Delete the soev-api agent threads a chat's messages bookmark when the chat or its messages go.

soev-api keeps a v2 agent's conversation in threads owned by the user; OWUI knows them only by the
`meta.agent_v2` bookmark on each assistant message. A regenerate or an edit forks a thread of its own, and a
clone of a chat shares the original's threads, so a thread goes only once no message of the user bookmarks it.
"""

import logging
from collections.abc import Collection, Iterable
from urllib.parse import quote

from open_webui.models.chat_messages import ChatMessageModel, ChatMessages
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError

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
    if not thread_ids:
        return []
    client = identity.build_client()
    kept = []
    for thread_id in sorted(thread_ids):
        try:
            await client.chat_delete(f'/v1/chat/threads/{quote(thread_id, safe="")}', as_user=f'owui:user:{user_id}')
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
