"""Pull console offboarding work without invoking legacy account deletion."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from contextlib import suppress
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import select

from open_webui.internal.db import get_async_db
from open_webui.models.users import User
from open_webui.soev.client import build_service_client
from open_webui.soev.policy_jobs import v2_enabled
from open_webui.soev.principal_refs import is_canonical_person_ref

log = logging.getLogger(__name__)
ParticipantError = Literal['target_missing', 'source_conflict', 'local_cleanup_failed', 'unsupported_content']


class Task(BaseModel):
    """Only immutable disposition and platform person references cross the feed."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str
    person: str
    mode: Literal['delete', 'transfer', 'archive']
    target: str | None
    expires_at: dt.datetime | None

    @field_validator('person', 'target')
    @classmethod
    def canonical_person(cls, value: str | None) -> str | None:
        if value is not None and not is_canonical_person_ref(value):
            message = 'Offboarding requires a canonical platform person'
            raise ValueError(message)
        return value

    @model_validator(mode='after')
    def disposition(self) -> Task:
        if (
            (self.mode == 'transfer') != (self.target is not None)
            or (self.mode == 'archive') != (self.expires_at is not None)
            or self.person == self.target
        ):
            message = 'Invalid offboarding disposition'
            raise ValueError(message)
        return self


async def apply(task: Task) -> ParticipantError:
    """Refuse until local disposal can preserve the platform's thread semantics."""
    async with get_async_db() as db:
        if task.mode == 'transfer':
            target = await db.scalar(select(User.id).where(User.person_id == task.target))
            if target is None:
                return 'target_missing'
        source = await db.scalar(select(User.id).where(User.person_id == task.person))
        if source is None:
            return 'source_conflict'
    # Legacy offboarding deletes chats in transfer mode and uses the former owner's
    # login for remote writes. Neither operation is authorized by this feed.
    return 'unsupported_content'


async def poll() -> None:
    """Acknowledgements carry fixed codes; no account or content is changed locally."""
    if not v2_enabled():
        return
    client = build_service_client()
    pending = await client.get('/v1/offboarding')
    if not isinstance(pending, list):
        message = 'Invalid offboarding feed'
        raise ValueError(message)
    tasks = tuple(Task.model_validate(row) for row in pending)
    if len({task.id for task in tasks}) != len(tasks):
        message = 'Duplicate offboarding jobs'
        raise ValueError(message)
    for task in tasks:
        error = await apply(task)
        await client.send(
            'POST',
            '/v1/offboarding/' + quote(task.id, safe='') + '/acknowledgement',
            {'error': error},
            idempotency_key='offboarding-ack:' + task.id,
        )


async def worker() -> None:
    while v2_enabled():
        try:
            await poll()
        except Exception as error:  # A failed poll remains retryable; no response body is logged.
            log.warning('Offboarding poll failed', extra={'exc_class': type(error).__name__})
        await asyncio.sleep(10)


def start_worker() -> asyncio.Task[None] | None:
    return asyncio.create_task(worker()) if v2_enabled() else None


async def stop_worker(task: asyncio.Task[None] | None) -> None:
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
