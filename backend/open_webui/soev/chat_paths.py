"""Dark chat handlers over the platform contract and local organisation records."""

import asyncio
import datetime as dt
import logging
import time
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from open_webui.soev import threads
from open_webui.soev.request_cache import invalidate, memoized
from open_webui.soev.threads import Organization, ThreadError


class Organizations(Protocol):
    async def list(self, user_id: str) -> list[Organization]: ...
    async def context_thread_id(self, user_id: str, chat_id: str) -> str | None: ...
    async def put(self, user_id: str, record: Organization) -> None: ...
    async def remove(self, user_id: str, chat_id: str) -> None: ...
    async def legacy_share(self, share_id: str) -> tuple[str, str, str | None] | None: ...
    async def folder_records(self, folder_id: str) -> dict[str, str]: ...


class FeedbackForm(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    thread_id: str
    position: int = Field(gt=0)
    rating: int
    comment: str | None = Field(None, max_length=2000)
    comment_authored: bool = False


class ComposerFeatures(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    web_search: bool | None = None
    document_writer: bool | None = None
    image_generation: bool | None = None
    code_interpreter: bool | None = None
    live_documents: Literal['off', 'auto', 'required'] | None = None
    live_mail: Literal['off', 'auto', 'required'] | None = None
    tool_ids: list[str] | None = None
    skill_ids: list[str] | None = None
    filter_ids: list[str] | None = None


# These operations have no suitable platform contract yet.
CONTRACT_GAPS = {
    'export': 'authorized export creation, status and download',
    'import': 'fresh-id owner-only OWUI/full-scope user import',
    'temporary': 'temporary creation with platform TTL policy',
}


def refused() -> None:
    raise ThreadError(409, 'unavailable')


class ChatPaths:
    def __init__(self, client: threads.ThreadClient, organizations: Organizations) -> None:
        self.client, self.organizations = client, organizations

    async def records(self, user_id: str) -> list[Organization]:
        return await memoized(
            ('thread-organizations', self.organizations, user_id), lambda: self.organizations.list(user_id)
        )

    async def record(self, user_id: str, chat_id: str) -> Organization:
        for record in await self.records(user_id):
            if record.id == chat_id:
                return record
        # A thread shared with the caller need not have a local organisational
        # row. Its platform read is the authority, never the local mapping.
        return Organization(id=chat_id, thread_id=chat_id)

    async def authorized(self, user_id: str, *, q: str = '') -> list[dict]:
        return [item async for page in self.authorized_pages(user_id, q=q) for item in page]

    async def authorized_pages(self, user_id: str, *, q: str = ''):
        records, page = await asyncio.gather(self.records(user_id), self.client.index(q=q, limit=200, cursor=''))
        active = {record.thread_id for record in records}
        inactive = {thread_id for record in records for thread_id in record.branch_thread_ids} - active
        cursor, seen = '', set()
        while True:
            yield [item for item in threads.join_index(page, records)['items'] if item['thread_id'] not in inactive]
            if page.next_cursor is None:
                return
            if page.next_cursor in seen:
                raise ThreadError(502, 'reconnect')
            seen.add(page.next_cursor)
            cursor = page.next_cursor
            page = await self.client.index(q=q, limit=200, cursor=cursor)

    async def listing(
        self,
        user_id: str,
        *,
        q: str = '',
        folder_id: str | None = None,
        archived: bool = False,
        pinned: bool | None = None,
        page: int | None = None,
        include_pinned: bool = True,
        include_folders: bool = True,
        sort_by: str = 'updated_at',
        sort_dir: str = 'desc',
    ) -> list[dict]:
        if sort_by not in {'updated_at', 'title', 'unread_updated_at'} or sort_dir not in {'asc', 'desc'}:
            raise ThreadError(422, 'invalid_request')
        if page is not None and page < 1:
            raise ThreadError(422, 'invalid_request')
        if pinned is True and not any(
            record.pinned and record.archived == archived for record in await self.records(user_id)
        ):
            return []
        items = []
        async for batch in self.authorized_pages(user_id, q=q):
            if folder_id is not None:
                batch = await self.in_folder(user_id, batch, folder_id)
            items.extend(
                item
                for item in batch
                if self._included(item, folder_id, archived, pinned, include_pinned, include_folders)
            )
            # The platform index is newest first. Other sorts need the full set.
            if page is not None and sort_by == 'updated_at' and sort_dir == 'desc' and len(items) >= page * 60:
                break
        items.sort(key=lambda item: self._sort_key(item, sort_by), reverse=sort_dir == 'desc')
        selected = items if page is None else items[(page - 1) * 60 : page * 60]
        return self.ui_rows(selected)

    @staticmethod
    def ui_rows(items: list[dict]) -> list[dict]:
        rows = []
        for item in items:
            org = item['organization'] or {}
            readonly = not item['owned'] or item.get('readonly', False)
            row = {
                **item,
                'id': item['thread_id'] if readonly else org.get('id', item['thread_id']),
                'last_read_at': org.get('last_read_at'),
                'readonly': readonly,
                'pinned': org.get('pinned', False),
                'folder_id': org.get('folder_id'),
                'archived': org.get('archived', False),
                'active': False,
            }
            for field in ('updated_at', 'created_at'):
                if isinstance(row.get(field), str):
                    row[field] = int(dt.datetime.fromisoformat(row[field].replace('Z', '+00:00')).timestamp())
            rows.append(row)
        return rows

    async def in_folder(self, user_id: str, items: list[dict], folder_id: str) -> list[dict]:
        members = await memoized(
            ('thread-folder-records', self.organizations, folder_id),
            lambda: self.organizations.folder_records(folder_id),
        )
        result = []
        for item in items:
            org = item['organization']
            if item['thread_id'] not in members and (org or {}).get('folder_id') != folder_id:
                continue
            # Join only after the platform's grant + label filtering. Never
            # borrow another person's pin, archive or last-read state.
            org = org or Organization(id=item['thread_id'], thread_id=item['thread_id']).model_dump()
            result.append(
                {
                    **item,
                    'organization': {**org, 'folder_id': folder_id},
                    'readonly': members.get(item['thread_id'], user_id) != user_id,
                }
            )
        return result

    @staticmethod
    def _included(item, folder_id, archived, pinned, include_pinned, include_folders) -> bool:
        org = item['organization'] or {}
        return (
            org.get('archived', False) == archived
            and (folder_id is None or org.get('folder_id') == folder_id)
            and (pinned is None or org.get('pinned', False) == pinned)
            and (include_pinned or not org.get('pinned', False))
            and (include_folders or not org.get('folder_id'))
        )

    @staticmethod
    def _sort_key(item, sort_by):
        if sort_by == 'title':
            return (item['title'] or '', item['thread_id'])
        if sort_by == 'unread_updated_at':
            org = item['organization'] or {}
            updated = dt.datetime.fromisoformat(item['updated_at'].replace('Z', '+00:00')).timestamp()
            return (updated > (org.get('last_read_at') or 0), item['updated_at'], item['thread_id'])
        return (item['updated_at'], item['thread_id'])

    async def projection(self, user_id: str, chat_id: str, **kwargs):
        record = await self.record(user_id, chat_id)
        return await self.client.projection(record.thread_id, **kwargs)

    async def presentation(self, user_id: str, page: threads.ProjectionPage) -> threads.ProjectionPage:
        from open_webui.soev import acting, identity
        from open_webui.soev.chat_presentation import needs_events, needs_sources, present_projection
        from open_webui.soev.client import SoevApiError

        events = []
        if needs_events(page):
            try:
                client = identity.build_client()
                requester = acting.requester_ref() or f'owui:user:{user_id}'
                after = min(m.position for m in page.messages) - 1
                events = await client.chat_events(
                    page.thread_id,
                    as_user=requester,
                    after=after,
                    through=max(m.position for m in page.messages),
                )
                enriched = await present_projection(page, events)
                if not after or not needs_sources(enriched):
                    return enriched
                # A visible answer can cite a source read on an earlier page.
                # Resolve that dependency only when the visible window needs it.
                previous = await client.chat_events(page.thread_id, as_user=requester, after=0, through=after)
                events = previous + events
            except SoevApiError:
                logging.getLogger(__name__).warning('Thread presentation sources unavailable')
        return await present_projection(page, events)

    async def metadata(self, user_id: str, chat_id: str, changes: dict, *, tag_action: str = 'replace'):
        if not changes or set(changes) - {'title', 'tags', 'features'}:
            raise ThreadError(422, 'invalid_request')
        if 'title' in changes and changes['title'] is not None and not isinstance(changes['title'], str):
            raise ThreadError(422, 'invalid_request')
        if 'tags' in changes and (
            not isinstance(changes['tags'], list) or any(not isinstance(tag, str) for tag in changes['tags'])
        ):
            raise ThreadError(422, 'invalid_request')
        if 'features' in changes:
            ComposerFeatures.model_validate(changes['features'])
        record = await self.record(user_id, chat_id)

        def change(page):
            result = {'title': changes['title']} if 'title' in changes else {}
            if 'tags' in changes:
                if not isinstance(page.preferences, dict):
                    raise ThreadError(404, 'unavailable')
                tags = changes['tags']
                if tag_action == 'add':
                    tags = [*page.tags, *tags]
                elif tag_action == 'remove':
                    tags = [tag for tag in page.tags if tag not in tags]
                result['preferences'] = {**page.preferences, 'tags': tags}
            if 'features' in changes:
                result['preferences'] = {
                    **(result.get('preferences', page.preferences) or {}),
                    'features': changes['features'],
                }
            return result

        return await self.client.update_metadata(record.thread_id, change)

    async def fork(self, user_id: str, chat_id: str, at: int, *, clone: bool = False) -> Organization:
        record = await self.record(user_id, chat_id)
        child = await self.client.fork(record.thread_id, at=at)
        if clone:
            result = Organization(id=child.thread_id, thread_id=child.thread_id, folder_id=record.folder_id)
        else:
            result = record.model_copy(
                update={
                    'thread_id': child.thread_id,
                    'branch_thread_ids': sorted(set(record.branch_thread_ids) | {record.thread_id, child.thread_id}),
                }
            )
        await self.organizations.put(user_id, result)
        invalidate()
        return result

    async def organize(self, user_id: str, chat_id: str, changes: dict) -> Organization:
        if set(changes) - {'folder_id', 'pinned', 'archived', 'last_read_at'}:
            raise ThreadError(422, 'invalid_request')
        record = await self.record(user_id, chat_id)
        # Re-authorize at mutation, including a record that exists locally but
        # has become invisible since the sidebar was loaded.
        await self.client.projection(record.thread_id, window=1)
        try:
            result = Organization.model_validate(
                {
                    **record.model_dump(),
                    'branch_thread_ids': record.branch_thread_ids,
                    **changes,
                }
            )
        except ValidationError:
            raise ThreadError(422, 'invalid_request') from None
        await self.organizations.put(user_id, result)
        invalidate()
        return result

    async def folder_counts(self, user_id: str, parents: dict[str, str | None]) -> dict[str, int]:
        counts = dict.fromkeys(parents, 0)
        if not parents:
            return counts
        items = await self.authorized(user_id)
        memberships = await asyncio.gather(*(self.in_folder(user_id, items, folder_id) for folder_id in parents))
        for members in memberships:
            joined = {item['thread_id']: item for item in members}
            items = [joined.get(item['thread_id'], item) for item in items]
        for item in items:
            org = item['organization'] or {}
            updated = dt.datetime.fromisoformat(item['updated_at'].replace('Z', '+00:00')).timestamp()
            if (
                not item['owned']
                or item.get('readonly', False)
                or org.get('archived')
                or updated <= (org.get('last_read_at') or 0)
            ):
                continue
            folder, seen = org.get('folder_id'), set()
            while folder and folder not in seen:
                seen.add(folder)
                if folder in counts:
                    counts[folder] += 1
                folder = parents.get(folder)
        return counts

    async def create(self, user_id: str, *, agent: str) -> Organization:
        thread_id = await self.client.reserve(agent)
        record = Organization(id=thread_id, thread_id=thread_id)
        await self.organizations.put(user_id, record)
        invalidate()
        return record

    async def delete(self, user_id: str, chat_id: str) -> None:
        records = {record.id: record for record in await self.organizations.list(user_id)}
        if chat_id not in records:
            raise ThreadError(404, 'unavailable')
        record = records[chat_id]
        for thread_id in sorted(set(record.branch_thread_ids) | {record.thread_id}):
            await self.client.delete(thread_id)
        await self.organizations.remove(user_id, chat_id)
        invalidate()

    async def delete_all(self, user_id: str) -> None:
        for record in await self.organizations.list(user_id):
            await self.delete(user_id, record.id)

    async def read_all(self, user_id: str, *, archived: bool | None = None) -> int:
        count = 0
        for item in await self.authorized(user_id):
            org = item['organization'] or {}
            if archived is not None:
                changes = {'archived': archived}
            else:
                changes = {'last_read_at': int(time.time())}
            await self.organize(user_id, org.get('id', item['thread_id']), changes)
            count += 1
        return count
