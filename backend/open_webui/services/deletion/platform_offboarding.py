"""Apply a pulled platform job to local product rows, never to platform chats."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, or_, select, text, update

from open_webui.internal.db import get_async_db
from open_webui.models.access_grants import AccessGrant
from open_webui.models.assistant_share_grants import AssistantShareGrant
from open_webui.models.auths import Auth
from open_webui.models.automations import Automation
from open_webui.models.calendar import Calendar, CalendarEvent, CalendarEventAttendee
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatFile
from open_webui.models.data_warnings import DataWarningLog
from open_webui.models.feedbacks import Feedback
from open_webui.models.files import File
from open_webui.models.folder_share_grants import FolderShareGrant
from open_webui.models.folders import Folder
from open_webui.models.functions import Function
from open_webui.models.groups import GroupMember
from open_webui.models.knowledge import Knowledge, KnowledgeFile
from open_webui.models.memories import Memory
from open_webui.models.models import Model
from open_webui.models.notes import Note, PinnedNote
from open_webui.models.oauth_sessions import OAuthSession
from open_webui.models.password_reset import PasswordResetToken
from open_webui.models.prompt_history import PromptHistory
from open_webui.models.prompts import Prompt
from open_webui.models.recovery_codes import RecoveryCode
from open_webui.models.runtime_state import RuntimeState
from open_webui.models.tags import Tag
from open_webui.models.tools import Tool
from open_webui.models.user_archives import UserArchive
from open_webui.models.users import ApiKey, User
from open_webui.services.deletion.offboarding_store import delete_storage_files
from open_webui.soev.thread_content import refuse_local_content
from open_webui.soev.threads import ThreadError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from open_webui.soev.offboarding import Task

Error = Literal['target_missing', 'source_conflict', 'local_cleanup_failed', 'unsupported_content']
RESOURCES = (Model, Prompt, Tool, Function, Note, File, Memory, Calendar)
ResourceTable = (
    type[Model] | type[Prompt] | type[Tool] | type[Function] | type[Note] | type[File] | type[Memory] | type[Calendar]
)
_lock = asyncio.Lock()


class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job: str
    digest: str
    source: str
    target: str | None
    expires_at: dt.datetime | None
    state: Literal['prepared', 'completed']


class FileBinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    path: str | None
    meta: dict[str, object] | None


def _digest(task: Task) -> str:
    data = task.model_dump(mode='json')
    if task.expires_at is not None:
        data['expires_at'] = task.expires_at.astimezone(dt.UTC).isoformat()
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _key(person: str) -> str:
    return 'soev.offboarding.' + hashlib.sha256(person.encode()).hexdigest()


async def _locked(db: AsyncSession, person: str) -> RuntimeState | None:
    if db.bind.dialect.name == 'postgresql':
        await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key': _key(person)})
    return await db.scalar(select(RuntimeState).where(RuntimeState.key == _key(person)).with_for_update())


async def apply(task: Task) -> Error | None:
    """A committed receipt precedes acknowledgement and fences replays by person."""
    try:
        refuse_local_content()
    except ThreadError:
        return 'unsupported_content'
    async with _lock:
        error = await _prepare(task)
        if error is not None:
            return error
        async with get_async_db() as db:
            receipt = await _locked(db, task.person)
            if receipt is None:
                return 'source_conflict'
            state = Receipt.model_validate(receipt.value)
            if state.digest != _digest(task):
                return 'source_conflict'
            if state.state == 'completed':
                return None
            if _expired(task):
                return 'unsupported_content'
            source, target = state.source, state.target
            error = await _binding_error(db, source, target, task)
            if error is not None:
                return error
            try:
                await _external(db, source, target if task.mode == 'transfer' else None)
                await _local(db, source, target, task)
                await db.execute(
                    update(RuntimeState)
                    .where(RuntimeState.key == _key(task.person))
                    .values(
                        value={**state.model_dump(mode='json'), 'state': 'completed'},
                    )
                )
                await db.commit()
            except Exception:
                await db.rollback()
                return 'local_cleanup_failed'
    return None


async def _prepare(task: Task) -> Error | None:
    async with get_async_db() as db:
        receipt = await _locked(db, task.person)
        job_key = 'soev.offboarding.job.' + hashlib.sha256(task.id.encode()).hexdigest()
        bound = await db.get(RuntimeState, job_key)
        if bound is not None and bound.value['digest'] != _digest(task):
            return 'source_conflict'
        if receipt is not None:
            return None if receipt.value['digest'] == _digest(task) else 'source_conflict'
        if _expired(task):
            return 'unsupported_content'
        target = None
        if task.mode == 'transfer':
            target = await db.scalar(
                select(User.id).where(User.person_id == task.target, User.role.in_(['user', 'admin']))
            )
            if target is None:
                return 'target_missing'
        source = await db.scalar(select(User).where(User.person_id == task.person).with_for_update())
        if source is None:
            return 'source_conflict'
        if await _unsupported_content(db, source.id, task.mode == 'transfer'):
            return 'unsupported_content'
        source.role = 'pending'
        db.add(RuntimeState(key=job_key, value={'digest': _digest(task)}))
        db.add(
            RuntimeState(
                key=_key(task.person),
                value={
                    'job': task.id,
                    'digest': _digest(task),
                    'source': source.id,
                    'target': target,
                    'expires_at': task.expires_at.astimezone(dt.UTC).isoformat()
                    if task.expires_at is not None
                    else None,
                    'state': 'prepared',
                },
            )
        )
        await db.commit()
    return None


async def _binding_error(db: AsyncSession, source: str, target: str | None, task: Task) -> Error | None:
    if not await db.scalar(
        select(User.id)
        .where(User.id == source, User.person_id == task.person, User.role == 'pending')
        .with_for_update()
    ):
        return 'source_conflict'
    if task.mode == 'transfer' and not await db.scalar(
        select(User.id)
        .where(User.id == target, User.person_id == task.target, User.role.in_(['user', 'admin']))
        .with_for_update()
    ):
        return 'target_missing'
    if await _unsupported_content(db, source, task.mode == 'transfer'):
        return 'unsupported_content'
    return None


async def _unsupported_content(db: AsyncSession, source: str, transferring: bool) -> bool:
    refuse_local_content()
    if await db.scalar(select(Knowledge.id).where(Knowledge.user_id == source).limit(1)) is not None:
        return True
    if await db.scalar(select(UserArchive.id).where(UserArchive.user_id == source).limit(1)) is not None:
        return True
    foreign_events = await db.scalar(
        select(CalendarEvent.id)
        .where(
            CalendarEvent.calendar_id.in_(select(Calendar.id).where(Calendar.user_id == source)),
            CalendarEvent.user_id != source,
        )
        .limit(1)
    )
    return (not transferring and foreign_events is not None) or await _content_conflict(db, source, transferring)


def _expired(task: Task) -> bool:
    return task.expires_at is not None and task.expires_at <= dt.datetime.now(dt.UTC)


async def _external(db: AsyncSession, source: str, target: str | None) -> None:
    """Keep database rows until every required external operation has succeeded."""
    from open_webui.services.deletion.platform_offboarding_vectors import memory_vectors, remove_file_vectors

    await memory_vectors(source, target)
    files = [
        FileBinding.model_validate(row) for row in (await db.scalars(select(File).where(File.user_id == source))).all()
    ]
    files = [row for row in files if target is None or (row.meta or {}).get('source') is not None]
    for row in files:
        await remove_file_vectors(row.id)
    paths = sorted({row.path for row in files if row.path})
    if paths:
        other_paths = set(
            (await db.scalars(select(File.path).where(File.user_id != source, File.path.in_(paths)))).all()
        )
        await delete_storage_files([path for path in paths if path not in other_paths])


async def _local(db: AsyncSession, source: str, target: str | None, task: Task) -> None:
    refuse_local_content()
    transferring = task.mode == 'transfer'
    if transferring and target is None:
        message = 'Transfer target required'
        raise ValueError(message)
    await _cloud(db, source)
    await _organisation_grants(db, source, transferring)
    for table in RESOURCES:
        ids = list((await db.scalars(select(table.id).where(table.user_id == source))).all())
        if transferring:
            await db.execute(
                delete(AccessGrant).where(
                    AccessGrant.resource_type == table.__tablename__,
                    AccessGrant.resource_id.in_(ids),
                    AccessGrant.principal_type == 'user',
                    AccessGrant.principal_id == source,
                )
            )
            await db.execute(update(table).where(table.id.in_(ids)).values(user_id=target))
            continue
        await _dependents(db, source, table, ids)
        await db.execute(
            delete(AccessGrant).where(
                AccessGrant.resource_type == table.__tablename__, AccessGrant.resource_id.in_(ids)
            )
        )
        await db.execute(delete(table).where(table.id.in_(ids)))
    if transferring:
        for table in (Folder, Chat, ChatFile, ChatMessage, PromptHistory, CalendarEvent):
            await db.execute(update(table).where(table.user_id == source).values(user_id=target))
        assert target is not None
        await _tags(db, source, target)
        for table in (AssistantShareGrant, FolderShareGrant):
            await db.execute(update(table).where(table.owner_id == source).values(owner_id=target))
    else:
        events = select(CalendarEvent.id).where(CalendarEvent.user_id == source)
        await db.execute(delete(CalendarEventAttendee).where(CalendarEventAttendee.event_id.in_(events)))
        await db.execute(delete(CalendarEvent).where(CalendarEvent.user_id == source))
        await db.execute(delete(ChatFile).where(ChatFile.user_id == source))
        await db.execute(delete(ChatMessage).where(ChatMessage.user_id == source))
        await db.execute(delete(PromptHistory).where(PromptHistory.user_id == source))
        await db.execute(delete(Tag).where(Tag.user_id == source))
        if task.mode == 'archive':
            await _archive_organisation(db, source, task.id)
        else:
            await db.execute(delete(Chat).where(Chat.user_id == source))
            await db.execute(delete(Folder).where(Folder.user_id == source))
        for table in (AssistantShareGrant, FolderShareGrant):
            await db.execute(delete(table).where(table.owner_id == source))
    for table in (
        PinnedNote,
        OAuthSession,
        Feedback,
        ApiKey,
        GroupMember,
        CalendarEventAttendee,
        Automation,
        DataWarningLog,
        PasswordResetToken,
        RecoveryCode,
    ):
        await db.execute(delete(table).where(table.user_id == source))
    await db.execute(delete(Auth).where(Auth.id == source))
    await db.execute(delete(User).where(User.id == source))


async def _cloud(db: AsyncSession, source: str) -> None:
    files = [
        FileBinding.model_validate(row) for row in (await db.scalars(select(File).where(File.user_id == source))).all()
    ]
    cloud_files = [row.id for row in files if (row.meta or {}).get('source') is not None]
    await _dependents(db, source, File, cloud_files)
    await db.execute(
        delete(AccessGrant).where(
            AccessGrant.resource_type == 'file',
            AccessGrant.resource_id.in_(cloud_files),
        )
    )
    await db.execute(delete(File).where(File.id.in_(cloud_files)))


async def _dependents(db: AsyncSession, source: str, table: ResourceTable, ids: list[str]) -> None:
    refuse_local_content()
    if table is Prompt:
        await db.execute(delete(PromptHistory).where(PromptHistory.prompt_id.in_(ids)))
    elif table is Note:
        await db.execute(delete(PinnedNote).where(PinnedNote.note_id.in_(ids)))
    elif table is File:
        owned_files = select(File.id).where(File.user_id == source, File.id.in_(ids))
        await db.execute(delete(ChatFile).where(ChatFile.user_id == source, ChatFile.file_id.in_(owned_files)))
        await db.execute(
            delete(KnowledgeFile).where(KnowledgeFile.user_id == source, KnowledgeFile.file_id.in_(owned_files))
        )
    elif table is Model:
        await db.execute(delete(AssistantShareGrant).where(AssistantShareGrant.assistant_id.in_(ids)))
    elif table is Calendar:
        event_ids = select(CalendarEvent.id).where(CalendarEvent.calendar_id.in_(ids))
        await db.execute(delete(CalendarEventAttendee).where(CalendarEventAttendee.event_id.in_(event_ids)))
        await db.execute(delete(CalendarEvent).where(CalendarEvent.calendar_id.in_(ids)))
    for derived in (FolderShareGrant, AssistantShareGrant):
        await db.execute(
            delete(derived).where(derived.resource_type == table.__tablename__, derived.resource_id.in_(ids))
        )


async def _organisation_grants(db: AsyncSession, source: str, transferring: bool) -> None:
    refuse_local_content()
    for table in (Chat, Folder):
        ids = select(table.id).where(table.user_id == source)
        clause = (AccessGrant.resource_type == table.__tablename__) & AccessGrant.resource_id.in_(ids)
        if transferring:
            clause &= (AccessGrant.principal_type == 'user') & (AccessGrant.principal_id == source)
        await db.execute(delete(AccessGrant).where(clause))


async def _tags(db: AsyncSession, source: str, target: str) -> None:
    for row in (await db.scalars(select(Tag).where(Tag.user_id == source))).all():
        existing = await db.get(Tag, (row.id, target))
        if existing is None:
            db.add(Tag(id=row.id, user_id=target, name=row.name, meta=row.meta))
        elif (existing.name, existing.meta) != (row.name, row.meta):
            message = 'Conflicting transferred tag'
            raise ValueError(message)
    await db.execute(delete(Tag).where(Tag.user_id == source))


async def _archive_organisation(db: AsyncSession, source: str, job: str) -> None:
    refuse_local_content()
    owner = 'platform:svc:archive-offboarding-' + job
    folders: dict[str, str | None] = {
        str(row[0]): str(row[1]) if row[1] is not None else None
        for row in (await db.execute(select(Folder.id, Folder.parent_id).where(Folder.user_id == source))).all()
    }
    needed = set((await db.scalars(select(Chat.folder_id).where(Chat.user_id == source))).all()) - {None}
    while parents := {parent for id in needed if (parent := folders.get(id)) is not None} - needed:
        needed |= parents
    await db.execute(delete(Folder).where(Folder.user_id == source, Folder.id.not_in(needed)))
    await db.execute(update(Folder).where(Folder.user_id == source).values(user_id=owner, items=[], data={}, meta={}))
    await db.execute(
        update(Chat)
        .where(Chat.user_id == source)
        .values(
            user_id=owner,
            share_id=None,
            chat={},
            meta={},
            variables=None,
            tasks=None,
            summary=None,
        )
    )


async def _content_conflict(db: AsyncSession, source: str, transferring: bool) -> bool:
    refuse_local_content()
    owned_chats = select(Chat.id).where(Chat.user_id == source)
    for table in (ChatFile, ChatMessage):
        if (
            await db.scalar(
                select(table.id)
                .where(table.chat_id.in_(owned_chats), or_(table.user_id != source, table.user_id.is_(None)))
                .limit(1)
            )
            is not None
        ):
            return True
    files = (await db.execute(select(File.id, File.meta).where(File.user_id == source))).all()
    deleted_files = [id for id, meta in files if not transferring or (meta or {}).get('source') is not None]
    if (
        await db.scalar(
            select(ChatFile.id)
            .where(ChatFile.file_id.in_(deleted_files), or_(ChatFile.user_id != source, ChatFile.user_id.is_(None)))
            .limit(1)
        )
        is not None
    ):
        return True
    return (
        await db.scalar(
            select(KnowledgeFile.id)
            .outerjoin(Knowledge, Knowledge.id == KnowledgeFile.knowledge_id)
            .where(
                KnowledgeFile.file_id.in_(deleted_files), or_(Knowledge.user_id != source, Knowledge.user_id.is_(None))
            )
            .limit(1)
        )
        is not None
    )
