"""Pull the platform directory into OWUI's read-only group mirror."""

import asyncio
import logging
import time
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from open_webui import config
from open_webui.internal.db import get_async_db_context
from open_webui.models import groups as group_models
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


def _interval() -> float:
    return min(config.SOEV_GROUP_RECONCILE_MINUTES * 60, config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS / 3)


def _database_now(session: AsyncSession) -> ColumnElement[float]:
    if session.bind.dialect.name == 'postgresql':
        return func.extract('epoch', func.clock_timestamp())
    return (func.julianday('now') - 2440587.5) * 86400


async def observe_generation(*, db: AsyncSession | None = None) -> bool:
    async with get_async_db_context(db) as session:
        now = _database_now(session)
        row = (
            await session.execute(
                select(
                    GroupSnapshot.generation,
                    GroupSnapshot.pull_seconds,
                    (GroupSnapshot.lease_until <= now) & (GroupSnapshot.next_pull_at <= now),
                ).where(GroupSnapshot.id == 'directory')
            )
        ).one_or_none()
        if row is None:
            return False
        group_models._snapshot_verification.observe(row[0], row[1])
        return bool(row[2])


async def reconcile(*, db: AsyncSession | None = None, scheduled: bool = False) -> None:
    global _lock
    if not config.SOEV_API_URL:
        return
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1], get_async_db_context(db) as session:
        token = str(uuid4())
        bound = config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS
        now = _database_now(session)
        conditions = [GroupSnapshot.id == 'directory', GroupSnapshot.lease_until <= now]
        if scheduled:
            conditions.append(GroupSnapshot.next_pull_at <= now)
        try:
            claimed = await session.scalar(
                update(GroupSnapshot)
                .where(*conditions)
                .values(lease_token=token, lease_until=now + bound * 2, next_pull_at=now + _interval())
                .returning(GroupSnapshot.generation)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            if claimed is None:
                return
            started = monotonic()
            fetched_at = time.time()
            groups = await asyncio.wait_for(_fetch_groups(), timeout=bound)
            await _replace_snapshot(session, groups)
            pull_seconds = monotonic() - started
            # The token fences a slow or abandoned pull after another worker takes over.
            generation = await session.scalar(
                update(GroupSnapshot)
                .where(
                    GroupSnapshot.id == 'directory', GroupSnapshot.lease_token == token, GroupSnapshot.lease_until > now
                )
                .values(
                    generation=GroupSnapshot.generation + 1,
                    fetched_at=fetched_at,
                    completed_at=time.time(),
                    pull_seconds=pull_seconds,
                    lease_token=None,
                    lease_until=0,
                )
                .returning(GroupSnapshot.generation)
                .execution_options(synchronize_session=False)
            )
            if generation is None or pull_seconds >= bound:
                raise RuntimeError('Directory pull exceeded its lease or validity bound')
            await session.commit()
            group_models._snapshot_verification.verify(generation, started + bound)
        except BaseException:
            await session.rollback()
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


async def _run(stop: asyncio.Event) -> None:
    if not config.SOEV_API_URL:
        return
    poll_interval = min(0.1, _interval() / 10)
    try:
        while not stop.is_set():
            try:
                if await observe_generation():
                    await reconcile(scheduled=True)
            except Exception:
                # Only a complete pull renews evidence; observing a lease never does.
                log.warning('soev-api group reconciliation unavailable')
            await asyncio.sleep(poll_interval)
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
