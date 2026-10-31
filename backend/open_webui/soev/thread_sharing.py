"""Authenticated live shares; legacy links retain only their owner's mapping."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.security.utils import get_authorization_scheme_param

from open_webui.soev import acting
from open_webui.soev.threads import Share, ThreadError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from open_webui.models.users import UserModel
    from open_webui.soev.chat_paths import ChatPaths
    from open_webui.soev.threads import ProjectionResult, ThreadClient


def require_header_mutation(request: Request) -> None:
    """Refuse cookie-only or non-JSON mutations, so a cross-site form cannot share or clone."""
    if request.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise ThreadError(415, 'invalid_request')
    # get_verified_user validates this credential before dispatch; never
    # allow its cookie fallback to authorize the mutation.
    scheme, credential = get_authorization_scheme_param(request.headers.get('Authorization'))
    if scheme.lower() != 'bearer' or not credential.strip():
        raise ThreadError(401, 'sign_in')


async def require_login() -> None:
    if not await acting.user_token(acting.requester_ref()):
        raise ThreadError(401, 'sign_in')


async def all_shares(client: ThreadClient, thread_id: str) -> list[Share]:
    items, cursor, seen = [], '', set()
    while True:
        page = await client.shares(thread_id, cursor=cursor)
        items.extend(page.items)
        if page.next_cursor is None:
            return items
        if page.next_cursor in seen:
            raise ThreadError(502, 'reconnect')
        seen.add(page.next_cursor)
        cursor = page.next_cursor


async def named_shares(shares: list[Share], user: UserModel, db: AsyncSession | None = None) -> list[dict]:
    from sqlalchemy import select

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.folders import Folders
    from open_webui.models.groups import Groups
    from open_webui.models.users import User
    from open_webui.utils.access_control.folders import has_folder_access

    names = {}
    people = {share.audience.id for share in shares if share.audience.kind == 'person'}
    groups = {share.audience.id for share in shares if share.audience.kind == 'group'}
    folders = {share.audience.id for share in shares if share.audience.kind == 'folder'}
    if not shares:
        return []
    async with get_async_db_context(db) as session:
        if people:
            rows = await session.execute(select(User.person_id, User.name).where(User.person_id.in_(people)))
            names.update({('person', ref): name for ref, name in rows})
        if groups:
            visible = await Groups.get_groups(filter={} if user.role == 'admin' else {'member_id': user.id}, db=session)
            names.update({('group', f'platform:group:{group.id}'): group.name for group in visible})
        for id in folders:
            folder = await Folders.get_folder_by_id(id, db=session)
            if folder and await has_folder_access(user.id, folder, 'read', session):
                names[('folder', id)] = folder.name
    return [
        {
            **share.model_dump(),
            **({'name': names[key]} if (key := (share.audience.kind, share.audience.id)) in names else {}),
        }
        for share in shares
    ]


async def resolve(service: ChatPaths, user_id: str, identifier: str) -> str:
    await require_login()
    legacy = await service.organizations.legacy_share(identifier)
    if legacy is not None:
        owner, _, thread_id = legacy
        if owner != user_id or thread_id is None:
            raise ThreadError(404, 'legacy_share')
        return thread_id
    return identifier


async def projection(
    service: ChatPaths, user_id: str, identifier: str, *, window: int = 20, before: str = ''
) -> ProjectionResult:
    thread_id = await resolve(service, user_id, identifier)
    try:
        result = await service.client.projection(thread_id, window=window, before=before)
        if thread_id != identifier:
            return result  # A retired link's owner opens their own migrated chat.
        return await owner_window(service.client, thread_id, result, window=window)
    except ThreadError as error:
        if error.state == 'unavailable':
            raise ThreadError(404, 'share_refused') from None
        raise


async def owner_window(
    client: ThreadClient, thread_id: str, result: ProjectionResult, *, window: int
) -> ProjectionResult:
    try:
        shares = await all_shares(client, thread_id)
    except ThreadError as error:
        if error.state == 'unavailable':
            return result  # Reader projections already enforce the platform ceiling.
        raise
    if not shares:
        return result
    # The platform bounds readers, while an owner can read beyond the share.
    # Keep the same link window for owners without changing their normal chat.
    at = max(share.at for share in shares)
    seen = set()
    while True:
        messages = [message for message in result.page.messages if message.position <= at]
        if messages or result.page.next_cursor is None:
            from dataclasses import replace

            return replace(result, page=result.page.model_copy(update={'messages': messages}))
        cursor = result.page.next_cursor
        if cursor in seen:
            raise ThreadError(502, 'reconnect')
        seen.add(cursor)
        result = await client.projection(thread_id, window=window, before=cursor)


async def revoke_all(service: ChatPaths, thread_id: str) -> None:
    for share in await all_shares(service.client, thread_id):
        await service.client.unshare(thread_id, share.audience)


async def owned_shared(service: ChatPaths, user_id: str) -> list[dict]:
    records = await service.records(user_id)
    owned = {thread_id for record in records for thread_id in [record.thread_id, *record.branch_thread_ids]}
    if not owned:
        return []
    pending = set(owned)
    slots = asyncio.Semaphore(10)

    async def shared(item):
        async with slots:
            try:
                shares = await all_shares(service.client, item.thread_id)
            except ThreadError as error:
                if error.state == 'unavailable':
                    return None
                raise
            if shares:
                return {**item.model_dump(), 'id': item.thread_id, 'share_id': item.thread_id}
        return None

    items, cursor, seen = [], '', set()
    while True:
        page = await service.client.index(limit=200, cursor=cursor)
        batch = [item for item in page.items if item.thread_id in pending]
        items.extend(item for item in await asyncio.gather(*(shared(item) for item in batch)) if item is not None)
        pending.difference_update(item.thread_id for item in batch)
        if not pending or page.next_cursor is None:
            return items
        if page.next_cursor in seen:
            raise ThreadError(502, 'reconnect')
        seen.add(page.next_cursor)
        cursor = page.next_cursor


async def dispatch(service: ChatPaths, request: Request, user: UserModel, route: str) -> dict | list[dict] | bool:
    from open_webui.soev.chat_routes import integer

    await require_login()
    if (request.method, route) not in {
        ('GET', '/share/{share_id}'),
        ('POST', '/{id}/clone/shared'),
        ('GET', '/shared'),
        ('DELETE', '/share/all'),
        ('GET', '/shared/{id}/access'),
        ('GET', '/{id}/share'),
        ('POST', '/{id}/share'),
        ('DELETE', '/{id}/share'),
    }:
        raise ThreadError(409, 'unavailable')
    if request.method in {'POST', 'DELETE'}:
        require_header_mutation(request)
    if route == '/share/{share_id}':
        result = await projection(
            service,
            user.id,
            request.path_params['share_id'],
            window=integer(request, 'window', 20),
            before=request.query_params.get('before', ''),
        )
        return result.page.model_dump()
    if route == '/{id}/clone/shared':
        from open_webui.routers.chats import require_chat_import_permission

        await require_chat_import_permission(request, user, None)
        result = await projection(service, user.id, request.path_params['id'], window=200)
        positions = [message.position for message in result.page.messages if message.finished]
        if not positions:
            raise ThreadError(409, 'share_refused')
        return (await service.fork(user.id, result.page.thread_id, max(positions), clone=True)).model_dump()
    if route in {'/shared', '/share/all'}:
        return await shared_listing(service, request, user)
    return await manage(service, request, user)


async def shared_listing(service: ChatPaths, request: Request, user: UserModel) -> list[dict] | bool:
    from open_webui.soev.chat_routes import integer

    items = await owned_shared(service, user.id)
    if request.method == 'DELETE':
        for item in items:
            try:
                await revoke_all(service, item['thread_id'])
            except ThreadError as error:
                if error.state != 'unavailable':
                    raise
        return True
    query = request.query_params.get('query', '').casefold()
    items = [item for item in items if query in (item['title'] or '').casefold()]
    order = request.query_params.get('order_by', 'updated_at')
    direction = request.query_params.get('direction', 'desc')
    if order not in {'title', 'updated_at'} or direction not in {'asc', 'desc'}:
        raise ThreadError(422, 'invalid_request')
    items.sort(key=lambda item: (item[order] or '', item['thread_id']), reverse=direction == 'desc')
    page = integer(request, 'page', 1)
    return items[(page - 1) * 60 : page * 60]


async def manage(service: ChatPaths, request: Request, user: UserModel) -> dict | bool:
    from open_webui.soev.chat_routes import body
    from open_webui.soev.threads import ShareAudience

    record = await service.record(user.id, request.path_params['id'])
    if request.method == 'GET':
        return {
            'items': await named_shares(await all_shares(service.client, record.thread_id), user),
            'next_cursor': None,
        }
    if request.method == 'DELETE':
        if await request.body():
            form = await body(request)
            if set(form) != {'audience'}:
                raise ThreadError(422, 'invalid_request')
            await service.client.unshare(record.thread_id, ShareAudience.model_validate(form['audience']))
        else:
            await revoke_all(service, record.thread_id)
        return True
    # Preserve the product's chat.share permission; platform enforces ownership
    # and directory/folder naming independently with the same request token.
    from open_webui.routers.chats import can_share_chats

    if not await can_share_chats(user, None):
        raise ThreadError(404, 'share_refused')
    form = Share.model_validate(await body(request))
    try:
        await service.client.share(record.thread_id, form)
    except ThreadError as error:
        if error.state == 'unavailable':
            raise ThreadError(404, 'share_refused') from None
        raise
    return {'share_id': record.thread_id}
