"""Pull the platform directory into OWUI's read-only group mirror."""

import asyncio
import logging
import time
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from open_webui import config
from open_webui.internal.db import get_async_db_context
from open_webui.models.groups import DatabaseTime, Group, GroupMember, GroupSnapshot, GroupTable
from open_webui.models.users import User
from open_webui.soev.client import SoevApiError, build_service_client
from open_webui.soev.principal_refs import is_canonical_person_ref, is_canonical_uuid

log = logging.getLogger(__name__)
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
_completed_pull: tuple[asyncio.AbstractEventLoop, float] | None = None
_snapshot_flight: tuple[asyncio.AbstractEventLoop, asyncio.Task] | None = None


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


def _interval() -> float:
    return min(config.SOEV_GROUP_RECONCILE_MINUTES * 60, config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS / 3)


async def _database_timeouts(session: AsyncSession, seconds: float) -> None:
    if session.bind.dialect.name == 'postgresql':
        milliseconds = str(max(1, int(seconds * 1000)))
        await session.execute(
            select(
                func.set_config('statement_timeout', milliseconds, True),
                func.set_config('lock_timeout', milliseconds, True),
            )
        )


async def _pull_due(*, db: AsyncSession | None = None) -> bool:
    async with asyncio.timeout(_interval()), get_async_db_context(db) as session:
        await _database_timeouts(session, _interval())
        return bool(
            await session.scalar(
                select(GroupSnapshot.id).where(
                    GroupSnapshot.id == 'directory',
                    GroupSnapshot.lease_until <= DatabaseTime(),
                    GroupSnapshot.next_pull_at <= DatabaseTime(),
                )
            )
        )


async def _renew_lease(session: AsyncSession, token: str) -> None:
    bound = config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS
    lease = bound / 3
    async with asyncio.timeout(lease):
        await _database_timeouts(session, lease)
        renewed = await session.scalar(
            update(GroupSnapshot)
            .where(
                GroupSnapshot.id == 'directory',
                GroupSnapshot.lease_token == token,
                GroupSnapshot.lease_until > DatabaseTime(),
                GroupSnapshot.pull_started_at + bound > DatabaseTime(),
            )
            .values(lease_until=DatabaseTime() + lease)
            .returning(GroupSnapshot.id)
            .execution_options(synchronize_session=False)
        )
        if renewed is None:
            raise RuntimeError('Directory pull exceeded its lease or validity bound')
        await session.commit()


async def _heartbeat(session: AsyncSession, token: str, stop: asyncio.Event) -> None:
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS / 9)
        except TimeoutError:
            async with sessions() as heartbeat_session:
                await _renew_lease(heartbeat_session, token)


async def _fetch_with_heartbeat(session: AsyncSession, token: str) -> dict[str, PlatformGroup]:
    stop = asyncio.Event()
    fetch = asyncio.create_task(_fetch_groups())
    heartbeat = asyncio.create_task(_heartbeat(session, token, stop))
    try:
        done, _ = await asyncio.wait({fetch, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            await heartbeat
        return await fetch
    finally:
        stop.set()
        fetch.cancel()
        heartbeat.cancel()
        await asyncio.gather(fetch, heartbeat, return_exceptions=True)


async def _publish_snapshot(
    session: AsyncSession, token: str, started: float, groups: dict[str, PlatformGroup]
) -> None:
    bound = config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS
    # Lock the fencing row before any membership writes. A successor cannot claim
    # the row until the entire replacement commits or rolls back.
    fenced = await session.scalar(
        update(GroupSnapshot)
        .where(
            GroupSnapshot.id == 'directory',
            GroupSnapshot.lease_token == token,
            GroupSnapshot.lease_until > DatabaseTime(),
            DatabaseTime() < started + bound,
        )
        .values(lease_until=DatabaseTime() + bound / 3)
        .returning(GroupSnapshot.id)
        .execution_options(synchronize_session=False)
    )
    if fenced is None:
        raise RuntimeError('Directory pull exceeded its lease or validity bound')
    remaining = await session.scalar(select(started + bound - DatabaseTime()))
    publish_seconds = max(0, min(remaining, bound / 3))
    async with asyncio.timeout(publish_seconds):
        await _database_timeouts(session, publish_seconds)
        await _replace_snapshot(session, groups)
        published = await session.scalar(
            update(GroupSnapshot)
            .where(
                GroupSnapshot.id == 'directory',
                GroupSnapshot.lease_token == token,
                GroupSnapshot.lease_until > DatabaseTime(),
                DatabaseTime() < started + bound,
            )
            .values(
                fetch_started_at=started,
                fetched_at=started,
                completed_at=DatabaseTime(),
                lease_token=None,
                lease_until=0,
            )
            .returning(GroupSnapshot.id)
            .execution_options(synchronize_session=False)
        )
        if published is None:
            raise RuntimeError('Directory pull exceeded its lease or validity bound')
        await session.commit()


async def reconcile(*, db: AsyncSession | None = None, scheduled: bool = False) -> None:
    global _lock, _completed_pull
    if not config.SOEV_API_URL:
        return
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1], get_async_db_context(db) as session:
        local_started = monotonic()
        token = str(uuid4())
        bound = config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS
        conditions = [GroupSnapshot.id == 'directory', GroupSnapshot.lease_until <= DatabaseTime()]
        if scheduled:
            conditions.append(GroupSnapshot.next_pull_at <= DatabaseTime())
        try:
            # This timeout includes acquisition, network work and publication. The
            # database timestamp, rather than this local timer, authorizes readers.
            async with asyncio.timeout(bound):
                async with asyncio.timeout(bound / 3):
                    await _database_timeouts(session, bound / 3)
                    started = await session.scalar(
                        update(GroupSnapshot)
                        .where(*conditions)
                        .values(
                            lease_token=token,
                            lease_until=DatabaseTime() + bound / 3,
                            pull_started_at=DatabaseTime(),
                            next_pull_at=DatabaseTime() + _interval(),
                        )
                        .returning(GroupSnapshot.pull_started_at)
                        .execution_options(synchronize_session=False)
                    )
                    await session.commit()
                if started is None:
                    return
                remaining = await session.scalar(select(started + bound - DatabaseTime()))
                async with asyncio.timeout(max(0, remaining)):
                    groups = await _fetch_with_heartbeat(session, token)
                    await _database_timeouts(session, remaining)
                    await _publish_snapshot(session, token, started, groups)
                    # Never defer the next scheduled start by the pull's latency.
                    _completed_pull = (loop, local_started + _interval())
        except BaseException:
            # Cleanup is bounded too; a failed cleanup leaves only a short lease.
            async with asyncio.timeout(bound / 3):
                await session.rollback()
                await _database_timeouts(session, bound / 3)
                await session.execute(
                    update(GroupSnapshot)
                    .where(GroupSnapshot.id == 'directory', GroupSnapshot.lease_token == token)
                    .values(lease_token=None, lease_until=0)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
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
    desired_members = {
        (group.id, uid) for group in groups.values() for pid in group.members for uid in users.get(pid, set())
    }
    current_members = set((await session.execute(select(GroupMember.group_id, GroupMember.user_id))).all())
    if (
        current.keys() == groups.keys()
        and all(
            current[group.id].name == group.name and current[group.id].description == '' for group in groups.values()
        )
        and current_members == desired_members
    ):
        return
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


async def ensure_snapshot(*, db: AsyncSession | None = None) -> None:
    global _snapshot_flight
    if not config.SOEV_API_URL:
        return
    loop = asyncio.get_running_loop()
    if _completed_pull is not None and _completed_pull[0] is loop and monotonic() < _completed_pull[1]:
        return
    if _snapshot_flight is None or _snapshot_flight[0] is not loop or _snapshot_flight[1].done():
        _snapshot_flight = (loop, asyncio.create_task(_ensure_snapshot(db=db)))
    await asyncio.shield(_snapshot_flight[1])


async def _ensure_snapshot(*, db: AsyncSession | None = None) -> None:
    try:
        if await _pull_due(db=db):
            await reconcile(db=db, scheduled=True)
    except Exception:
        # Expired evidence still grants nothing; an unavailable directory must
        # not prevent a person from using their own resources.
        log.warning('soev-api group reconciliation unavailable')


async def _run(stop: asyncio.Event) -> None:
    if not config.SOEV_API_URL:
        return
    await stop.wait()


def start_reconciler(app: FastAPI) -> None:
    app.state.soev_group_reconciler_stop = asyncio.Event()
    app.state.soev_group_reconciler = asyncio.create_task(_run(app.state.soev_group_reconciler_stop))


async def stop_reconciler(app: FastAPI, *, timeout: float = 10.0) -> None:
    app.state.soev_group_reconciler_stop.set()
    tasks = {app.state.soev_group_reconciler}
    if _snapshot_flight is not None and _snapshot_flight[0] is asyncio.get_running_loop():
        tasks.add(_snapshot_flight[1])
    for task in tasks:
        task.cancel()
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    if pending:
        log.warning('soev group reconciler did not stop within %ss', timeout)
