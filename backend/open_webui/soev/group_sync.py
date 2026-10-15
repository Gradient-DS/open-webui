"""Serialize group replacements and repair widening failures from current OWUI membership."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from time import monotonic
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.soev.client import SoevApiError

if TYPE_CHECKING:
    from open_webui.models.groups import GroupModel, GroupTable

P = ParamSpec('P')
T = TypeVar('T')
log = logging.getLogger(__name__)
ADMIN_ARCHIVE_GROUP = 'admins-archive'
ARCHIVE_OWNER = 'platform:svc:archive-owui'
_LOCK_ID = 0x4F57554947525053
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
_membership_owner: ContextVar[asyncio.Task | None] = ContextVar('soev_membership_owner', default=None)
_pending: dict[str, tuple[float, int]] = {}


@asynccontextmanager
async def membership_lock(db: AsyncSession | None = None) -> AsyncIterator[None]:
    """Keep local commits, remote pushes and reconcile in one order across workers."""
    global _lock
    from open_webui.internal.db import async_engine

    task = asyncio.current_task()
    if _membership_owner.get() is task:
        yield
        return
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1]:
        token = _membership_owner.set(task)
        try:
            engine = db.bind if db is not None else async_engine
            if engine.dialect.name != 'postgresql':
                yield
                return
            async with engine.begin() as connection:
                await connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _LOCK_ID})
                yield
        finally:
            _membership_owner.reset(token)


def serialized(method: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Run a group mutation under the same lock as retries and reconciliation."""
    method_signature = signature(method)

    @wraps(method)
    async def mutate(*args: P.args, **kwargs: P.kwargs) -> T:
        bound = method_signature.bind(*args, **kwargs)
        db = cast(AsyncSession | None, bound.arguments.get('db'))
        async with membership_lock(db):
            return await method(*args, **kwargs)

    return mutate


def pending_groups() -> set[str]:
    """Ids awaiting retry; restart recovery is supplied by the full startup reconcile."""
    return set(_pending)


async def push(group_id: str, user_ids: list[str]) -> None:
    """Replace membership once; an unknown capability is a failed push."""
    from open_webui import config
    from open_webui.soev import capabilities, identity

    if not config.SOEV_API_URL:
        return
    if not await capabilities.has(capabilities.GROUPS_PUSH):
        if capabilities.known():
            return
        raise SoevApiError(503, 'capabilities_unknown', 'Group capability is unknown')
    members = sorted({f'owui:user:{user_id}' for user_id in user_ids})
    await identity.build_client().send(
        'PUT',
        f'/v1/directory/groups/{quote(f"owui:group:{group_id}", safe="")}/members',
        {'members': members},
        idempotency_key=f'group:{group_id}:{uuid4()}',
    )


async def narrow(group_id: str, user_ids: list[str]) -> None:
    """Refuse a local narrowing when its preceding platform replacement fails."""
    try:
        await push(group_id, user_ids)
    except (SoevApiError, httpx.TransportError):
        raise HTTPException(
            status_code=503,
            detail={
                'code': 'group_sync_unavailable',
                'detail': 'Group membership could not be synchronized. Try again.',
            },
        ) from None


async def publish(table: GroupTable, group_ids: Iterable[str], *, db: AsyncSession | None = None) -> None:
    """Push committed memberships, queuing each failure with bounded exponential backoff."""
    for group_id in sorted(set(group_ids)):
        user_ids = await _members(table, group_id, db=db)
        try:
            await push(group_id, user_ids)
        except (SoevApiError, httpx.TransportError):
            attempts = _pending.get(group_id, (0, 0))[1] + 1
            _pending[group_id] = (monotonic() + min(10 * 2 ** min(attempts - 1, 5), 300), attempts)
            log.warning('soev-api group push failed', extra={'group_id': group_id})
        else:
            _pending.pop(group_id, None)


async def retry_pending(table: GroupTable, *, now: float, db: AsyncSession | None = None) -> None:
    """Retry due groups using their current local membership, never a captured payload."""
    async with membership_lock(db):
        from open_webui.soev import identity

        await publish(table, [group_id for group_id, (due, _) in _pending.items() if due <= now], db=db)
        await identity.retry_links(now=now, db=db)


async def reconcile(table: GroupTable, *, db: AsyncSession | None = None) -> None:
    """Publish all local groups and clear remote OWUI groups missing locally."""
    from open_webui import config
    from open_webui.soev import capabilities, identity

    if not config.SOEV_API_URL:
        return
    async with membership_lock(db):
        from open_webui.models.users import Users

        users = (await Users.get_users(db=db))['users']
        refs = {identity.external_ref(user) for user in users}
        for ref in identity._linked_refs - refs:
            await identity.unlink(ref, identity.build_client())
        for user in users:
            ref = identity.external_ref(user)
            if user.role in {'user', 'admin'}:
                identity._linked_refs.discard(ref)
                await identity.publish_link(ref, identity.build_client(), db=db)
            else:
                await identity.unlink(ref, identity.build_client())
        groups = await table.get_all_groups(db=db)
        local_ids = {group.id for group in groups} | {ADMIN_ARCHIVE_GROUP}
        await publish(table, local_ids, db=db)
        if not await capabilities.has(capabilities.GROUPS_PUSH):
            if not capabilities.known():
                raise SoevApiError(503, 'capabilities_unknown', 'Group capability is unknown')
            return
        params = {'limit': 200}
        while True:
            page = await identity.build_client().get('/v1/directory/groups', params=params)
            remote_members = {
                member for row in page['groups'] for member in row['members'] if member.startswith('owui:user:')
            }
            for ref in remote_members - refs:
                await identity.unlink(ref, identity.build_client())
            orphans = {
                row['group'].removeprefix('owui:group:')
                for row in page['groups']
                if row['group'].startswith('owui:group:')
            } - local_ids
            await publish(table, orphans, db=db)
            if page['next_cursor'] is None:
                return
            params['cursor'] = page['next_cursor']


async def _members(table: GroupTable, group_id: str, *, db: AsyncSession | None = None) -> list[str]:
    if group_id == ADMIN_ARCHIVE_GROUP:
        return await admin_ids(db=db)
    group = await table.get_group_by_id(group_id, db=db)
    return await table.get_group_user_ids_by_id(group_id, db=db) if group is not None else []


async def admin_ids(*, db: AsyncSession | None = None) -> list[str]:
    """Derive archive readers directly from the current local admin roles."""
    from open_webui.models.users import Users

    return sorted(user.id for user in (await Users.get_users(db=db))['users'] if user.role == 'admin')


async def narrow_admin(user_id: str, *, db: AsyncSession | None = None) -> None:
    """Remove archive readership before demoting or deleting an admin."""
    ids = await admin_ids(db=db)
    if user_id in ids:
        await narrow(ADMIN_ARCHIVE_GROUP, [value for value in ids if value != user_id])


async def archive_groups_for_users(user_ids: list[str], *, db: AsyncSession) -> dict[str, list[GroupModel]]:
    """Expose the same synthetic admin membership to local resource read checks."""
    from sqlalchemy import select

    from open_webui import config
    from open_webui.models.groups import GroupModel
    from open_webui.models.users import User

    if not config.SOEV_API_URL:
        return {}
    ids = (await db.execute(select(User.id).where(User.id.in_(user_ids), User.role == 'admin'))).scalars()
    group = GroupModel(
        id=ADMIN_ARCHIVE_GROUP,
        user_id=ARCHIVE_OWNER,
        name=ADMIN_ARCHIVE_GROUP,
        description='',
        created_at=0,
        updated_at=0,
    )
    return {user_id: [group] for user_id in ids}


async def _run(stop: asyncio.Event) -> None:
    from open_webui import config
    from open_webui.models.groups import Groups

    next_reconcile = 0.0
    try:
        # The flag, not only cancellation, ends the loop: a cancel swallowed inside a database or
        # HTTP context must not keep the reconciler (and the app's shutdown) alive.
        while not stop.is_set():
            try:
                if monotonic() >= next_reconcile:
                    await reconcile(Groups)
                    next_reconcile = monotonic() + config.SOEV_GROUP_RECONCILE_MINUTES * 60
                await retry_pending(Groups, now=monotonic())
            except (SoevApiError, httpx.TransportError):
                log.warning('soev-api group reconciliation unavailable')
                next_reconcile = monotonic() + 10
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return


def start_reconciler(app: FastAPI) -> None:
    """Reconcile at startup, then periodically, with retries between full scans."""
    app.state.soev_group_reconciler_stop = asyncio.Event()
    app.state.soev_group_reconciler = asyncio.create_task(_run(app.state.soev_group_reconciler_stop))


async def stop_reconciler(app: FastAPI, *, timeout: float = 10.0) -> None:
    """Stop the loop and wait a bounded time; shutdown never hangs on it."""
    app.state.soev_group_reconciler_stop.set()
    task = app.state.soev_group_reconciler
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        log.warning('soev group reconciler did not stop within %ss', timeout)
