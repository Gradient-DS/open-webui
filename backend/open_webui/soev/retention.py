"""Mirror completed platform deletions without reading or deleting remote threads."""

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui import config
from open_webui.internal.db import get_async_db
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.soev.client import build_service_client
from open_webui.soev.identity import credential_id_from_key
from open_webui.soev.policy_jobs import v2_enabled
from open_webui.soev.thread_content import refuse_local_content
from open_webui.soev.threads import enabled as threads_enabled

log = logging.getLogger(__name__)
_lock = asyncio.Lock()


def _key() -> str:
    # Secret rotation preserves the cursor; another credential has a different feed.
    credential_id = credential_id_from_key(config.SOEV_API_KEY)
    credential = f'{config.SOEV_API_URL}:{credential_id}'.encode()
    return 'soev.retention.' + hashlib.sha256(credential).hexdigest()


@asynccontextmanager
async def _state() -> AsyncIterator[tuple[AsyncSession, Config]]:
    async with _lock, get_async_db() as db:
        if db.bind.dialect.name == 'postgresql':
            await db.execute(text('SELECT pg_advisory_xact_lock(734268103)'))
        row = await db.get(Config, _key())
        if row is None:
            row = Config(key=_key(), value={'cursor': None, 'deleted': []})
        yield db, row
        db.add(row)
        await db.commit()


async def remember_deleted(thread_ids: Collection[str]) -> None:
    """Keep successful owner deletions until the pending local cleanup has observed them."""
    async with _state() as (_, row):
        row.value = {**row.value, 'deleted': sorted(set(row.value['deleted']) | set(thread_ids))}


async def known_deleted() -> set[str]:
    async with get_async_db() as db:
        row = await db.get(Config, _key())
        return set(row.value['deleted']) if row else set()


async def poll_deletions() -> None:
    refuse_local_content()
    if not v2_enabled():
        return
    async with _state() as (db, row):
        state = row.value
        params: dict[str, int | str] = {'limit': 100}
        if state['cursor']:
            params['after'] = state['cursor']
        page = await build_service_client().get('/v1/retention/deletions', params=params)
        gone = set(state['deleted']) | {item['thread_id'] for item in page['data']}
        candidates = select(ChatMessage.chat_id).where(ChatMessage.meta['agent_v2']['thread_id'].as_string().in_(gone))
        messages = await db.execute(
            select(ChatMessage.chat_id, ChatMessage.meta).where(ChatMessage.chat_id.in_(candidates))
        )
        bookmarks = defaultdict(set)
        for message in messages:
            bookmark = (message.meta or {}).get('agent_v2') or {}
            if bookmark.get('thread_id'):
                bookmarks[message.chat_id].add(bookmark['thread_id'])
        # Soft delete: the cleanup worker then removes files and tags as for any deleted chat.
        for chat_id, threads in bookmarks.items():
            if threads <= gone:
                await Chats.soft_delete_by_id(chat_id, db=db)
        surviving = set().union(*(threads for threads in bookmarks.values() if not threads <= gone))
        # The final page has no next_cursor: its last id is still the high-water mark.
        cursor = page['next_cursor'] or (page['data'][-1]['thread_id'] if page['data'] else state['cursor'])
        row.value = {'cursor': cursor, 'deleted': sorted(gone & surviving)}


async def deletion_feed_worker() -> None:
    while v2_enabled() and not threads_enabled():
        try:
            await poll_deletions()
        except Exception:
            log.exception('Platform deletion feed poll failed')
        await asyncio.sleep(60)
