"""Dark chat handlers over the platform contract and local organisation records."""

import datetime as dt
import logging
import time
from typing import Protocol
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from open_webui.soev import threads
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

    async def record(self, user_id: str, chat_id: str) -> Organization:
        for record in await self.organizations.list(user_id):
            if record.id == chat_id:
                return record
        # A thread shared with the caller need not have a local organisational
        # row. Its platform read is the authority, never the local mapping.
        return Organization(id=chat_id, thread_id=chat_id)

    async def authorized(self, user_id: str, *, q: str = '') -> list[dict]:
        records = await self.organizations.list(user_id)
        active = {record.thread_id for record in records}
        inactive = {thread_id for record in records for thread_id in record.branch_thread_ids} - active
        items, cursor, seen = [], '', set()
        while True:
            page = await self.client.index(q=q, limit=200, cursor=cursor)
            items.extend(
                item for item in threads.join_index(page, records)['items'] if item['thread_id'] not in inactive
            )
            if page.next_cursor is None:
                return items
            if page.next_cursor in seen:
                raise ThreadError(502, 'reconnect')
            seen.add(page.next_cursor)
            cursor = page.next_cursor

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
        items = await self.authorized(user_id, q=q)
        if folder_id is not None:
            items = await self.in_folder(user_id, items, folder_id)
        items = [
            item for item in items if self._included(item, folder_id, archived, pinned, include_pinned, include_folders)
        ]
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
        members = await self.organizations.folder_records(folder_id)
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
        from open_webui.soev.chat_presentation import present_projection
        from open_webui.soev.client import SoevApiError

        if not any(isinstance(m.content, dict) and m.content.get('role') == 'assistant' for m in page.messages):
            return page
        events = []
        try:
            thread = await identity.build_client().get(
                f'/v1/chat/threads/{quote(page.thread_id, safe="")}',
                as_user=acting.requester_ref() or f'owui:user:{user_id}',
            )
            events = thread['events']
        except SoevApiError:
            logging.getLogger(__name__).warning('Thread presentation sources unavailable')
        return await present_projection(page, events)

    async def metadata(self, user_id: str, chat_id: str, changes: dict, *, tag_action: str = 'replace'):
        if not changes or set(changes) - {'title', 'tags'}:
            raise ThreadError(422, 'invalid_request')
        if 'title' in changes and changes['title'] is not None and not isinstance(changes['title'], str):
            raise ThreadError(422, 'invalid_request')
        if 'tags' in changes and (
            not isinstance(changes['tags'], list) or any(not isinstance(tag, str) for tag in changes['tags'])
        ):
            raise ThreadError(422, 'invalid_request')
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
        return result

    async def folder_counts(self, user_id: str, parents: dict[str, str | None]) -> dict[str, int]:
        counts = dict.fromkeys(parents, 0)
        items = await self.authorized(user_id)
        for folder_id in parents:
            joined = {item['thread_id']: item for item in await self.in_folder(user_id, items, folder_id)}
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
        return record

    async def delete(self, user_id: str, chat_id: str) -> None:
        records = {record.id: record for record in await self.organizations.list(user_id)}
        if chat_id not in records:
            raise ThreadError(404, 'unavailable')
        record = records[chat_id]
        for thread_id in sorted(set(record.branch_thread_ids) | {record.thread_id}):
            await self.client.delete(thread_id)
        await self.organizations.remove(user_id, chat_id)

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
