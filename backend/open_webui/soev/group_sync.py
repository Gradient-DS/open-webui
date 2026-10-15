"""Serialize group replacements and repair widening failures from current OWUI membership."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
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
    from open_webui.models.groups import GroupTable

P = ParamSpec('P')
T = TypeVar('T')
log = logging.getLogger(__name__)
_LOCK_ID = 0x4F57554947525053
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
_pending: dict[str, tuple[float, int]] = {}


@asynccontextmanager
async def membership_lock(db: AsyncSession | None = None) -> AsyncIterator[None]:
    """Keep local commits, remote pushes and reconcile in one order across workers."""
    global _lock
    from open_webui.internal.db import async_engine

    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1]:
        engine = db.bind if db is not None else async_engine
        if engine.dialect.name != 'postgresql':
            yield
            return
        # The separate transaction retains its lock across GroupTable's commits.
        async with engine.begin() as connection:
            await connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _LOCK_ID})
            yield


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
        await publish(table, [group_id for group_id, (due, _) in _pending.items() if due <= now], db=db)


async def reconcile(table: GroupTable, *, db: AsyncSession | None = None) -> None:
    """Publish all local groups and clear remote OWUI groups missing locally."""
    from open_webui import config
    from open_webui.soev import capabilities, identity

    if not config.SOEV_API_URL:
        return
    async with membership_lock(db):
        groups = await table.get_all_groups(db=db)
        local_ids = {group.id for group in groups}
        await publish(table, local_ids, db=db)
        if not await capabilities.has(capabilities.GROUPS_PUSH):
            if not capabilities.known():
                raise SoevApiError(503, 'capabilities_unknown', 'Group capability is unknown')
            return
        params = {'limit': 200}
        while True:
            page = await identity.build_client().get('/v1/directory/groups', params=params)
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
    group = await table.get_group_by_id(group_id, db=db)
    return await table.get_group_user_ids_by_id(group_id, db=db) if group is not None else []


async def _run() -> None:
    from open_webui import config
    from open_webui.models.groups import Groups

    next_reconcile = 0.0
    try:
        while True:
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
    app.state.soev_group_reconciler = asyncio.create_task(_run())
