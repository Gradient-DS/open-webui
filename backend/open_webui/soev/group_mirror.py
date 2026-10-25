"""Pull the platform directory into OWUI's read-only group mirror."""

import asyncio
import logging
import time
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui import config
from open_webui.internal.db import get_async_db_context
from open_webui.models.groups import Group, GroupMember, GroupSnapshot, GroupTable, require_snapshot_verification
from open_webui.models.users import User
from open_webui.soev.client import SoevApiError, build_service_client
from open_webui.soev.principal_refs import is_canonical_person_ref, is_canonical_uuid

log = logging.getLogger(__name__)
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


class PlatformGroup(BaseModel):
    id: str
    ref: str
    name: str
    members: list[str]

    @model_validator(mode='after')
    def check_ref(self) -> 'PlatformGroup':
        if not is_canonical_uuid(self.id) or self.ref != f'platform:group:{self.id}':
            raise ValueError('Invalid group ref')
        if any(not is_canonical_person_ref(member) for member in self.members):
            raise ValueError('Invalid member ref')
        return self


class GroupPage(BaseModel):
    data: list[PlatformGroup]
    next_cursor: str | None


async def reconcile(*, db: AsyncSession | None = None) -> None:
    global _lock
    if not config.SOEV_API_URL:
        return
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1], get_async_db_context(db) as session:
        try:
            # Serialize the complete fetch and replacement across application workers.
            if session.bind.dialect.name == 'postgresql':
                await session.execute(
                    text('SELECT pg_advisory_xact_lock(:key)'),
                    {'key': 0x4F57554947525053},
                )
            fetched_at = time.time()
            groups = await _fetch_groups()
            await _replace_snapshot(session, groups)
            # Fetch latency consumes validity; a slow pull cannot renew old evidence.
            snapshot = await session.get(GroupSnapshot, 'directory')
            if snapshot is None:
                snapshot = GroupSnapshot(id='directory')
                session.add(snapshot)
            snapshot.fetched_at = fetched_at
            snapshot.completed_at = time.time()
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def _fetch_groups() -> dict[str, PlatformGroup]:
    client = build_service_client()
    groups: dict[str, PlatformGroup] = {}
    params = {'limit': 200}
    cursors = set()
    while True:
        try:
            page = GroupPage.model_validate(await client.get('/v1/groups', params=params))
        except ValidationError:
            raise SoevApiError(502, 'upstream_error', 'Invalid group response') from None
        for group in page.data:
            if group.id in groups:
                raise SoevApiError(502, 'upstream_error', 'Duplicate group response')
            groups[group.id] = group
        if page.next_cursor is None:
            break
        if page.next_cursor in cursors:
            raise SoevApiError(502, 'upstream_error', 'Invalid group cursor')
        cursors.add(page.next_cursor)
        params['cursor'] = page.next_cursor
    return groups


async def _replace_snapshot(session: AsyncSession, groups: dict[str, PlatformGroup]) -> None:
    people = (await session.execute(select(User.id, User.person_id).where(User.person_id.isnot(None)))).all()
    users: dict[str, set[str]] = {}
    for user_id, person_id in people:
        if is_canonical_person_ref(person_id):
            users.setdefault(person_id, set()).add(user_id)
    for person_id, user_ids in list(users.items()):
        if len(user_ids) > 1:
            log.warning('ambiguous person binding', extra={'user_ids': sorted(user_ids)})
            del users[person_id]
    current = {row.id: row for row in (await session.scalars(select(Group))).all()}
    now = int(time.time())
    for group in groups.values():
        row = current.get(group.id)
        if row is None:
            row = Group(
                id=group.id,
                user_id='',
                description='',
                created_at=now,
                **GroupTable()._ensure_default_share_config({}),
            )
            session.add(row)
        row.name = group.name
        row.description = ''
        row.updated_at = now
    await session.flush()
    await session.execute(delete(GroupMember))
    for group in groups.values():
        members = {uid for pid in group.members for uid in users.get(pid, set())}
        session.add_all(
            [
                GroupMember(
                    id=str(uuid4()),
                    group_id=group.id,
                    user_id=uid,
                    created_at=now,
                    updated_at=now,
                )
                for uid in sorted(members)
            ]
        )
    await session.execute(delete(Group).where(Group.id.not_in(groups)))


async def _run(stop: asyncio.Event) -> None:
    interval = config.SOEV_GROUP_RECONCILE_MINUTES * 60
    if config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS is not None:
        interval = min(interval, config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS / 2)
    next_reconcile = 0.0
    try:
        while not stop.is_set():
            try:
                if monotonic() >= next_reconcile:
                    await reconcile()
                    next_reconcile = monotonic() + interval
            except Exception:
                # Keep the last complete snapshot and retry without renewing it.
                log.warning('soev-api group reconciliation unavailable')
                next_reconcile = monotonic() + min(10, interval)
            await asyncio.sleep(min(1, max(0, next_reconcile - monotonic())))
    except asyncio.CancelledError:
        return


def start_reconciler(app: FastAPI) -> None:
    require_snapshot_verification()
    app.state.soev_group_reconciler_stop = asyncio.Event()
    app.state.soev_group_reconciler = asyncio.create_task(_run(app.state.soev_group_reconciler_stop))


async def stop_reconciler(app: FastAPI, *, timeout: float = 10.0) -> None:
    app.state.soev_group_reconciler_stop.set()
    task = app.state.soev_group_reconciler
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        log.warning('soev group reconciler did not stop within %ss', timeout)
