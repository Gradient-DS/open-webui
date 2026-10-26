"""SQL and storage boundaries for atomic local offboarding."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base
from open_webui.models.access_grants import AccessGrant
from open_webui.models.auths import Auth
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatFile
from open_webui.models.feedbacks import Feedback
from open_webui.models.files import File
from open_webui.models.folders import Folder
from open_webui.models.functions import Function
from open_webui.models.knowledge import Knowledge, KnowledgeDirectory, KnowledgeFile
from open_webui.models.memories import Memory
from open_webui.models.models import Model
from open_webui.models.notes import Note, PinnedNote
from open_webui.models.oauth_sessions import OAuthSession
from open_webui.models.prompt_history import PromptHistory
from open_webui.models.prompts import Prompt
from open_webui.models.tags import Tag
from open_webui.models.tools import Tool
from open_webui.models.user_archives import UserArchive
from open_webui.models.users import ApiKey, User, UserModel
from open_webui.services.archival.service import ArchiveService
from open_webui.soev import identity
from open_webui.soev.archive import archive_group_ref
from open_webui.soev.availability import configured
from open_webui.soev.client import SoevApiError
from open_webui.soev.thread_content import refuse_local_content

if TYPE_CHECKING:
    from open_webui.services.deletion.offboarding import Inventory, OffboardingOptions, SharedItem

RESOURCE_TABLES = (Knowledge, Model, Prompt, Tool, Note, File)
DELETE_USER_TABLES = (
    ChatFile,
    ChatMessage,
    Tag,
    Folder,
    PinnedNote,
    OAuthSession,
    Function,
    Feedback,
    ApiKey,
    Memory,
    Chat,
)


async def shared_assistants(user_id: str, *, db: AsyncSession) -> list[SharedItem]:
    """Find owned assistants with read or write grants beyond their owner."""
    ids = (
        (
            await db.execute(
                select(Model.id)
                .join(AccessGrant, AccessGrant.resource_id == Model.id)
                .where(
                    Model.user_id == user_id,
                    AccessGrant.resource_type == 'model',
                    or_(AccessGrant.principal_type != 'user', AccessGrant.principal_id != user_id),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return [{'id': value, 'kind': 'model'} for value in ids]


async def prepare_archive(
    user: UserModel, *, actor_id: str, options: OffboardingOptions, owned: Inventory, db: AsyncSession
) -> UserArchive | None:
    """Persist a stable snapshot and resource manifest before remote changes."""
    archive = await _existing_archive(user.id, options=options, db=db)
    if archive is not None:
        return archive
    if options.mode != 'archive' and not options.archive_before_delete:
        return None
    from open_webui.models.config import Config

    if not await Config.get('admin.enable_user_archival'):
        raise SoevApiError(403, 'policy_forbids', 'Archival is disabled', constraint='offboarding:archive_disabled')
    retention = options.archive_retention_days
    if retention is None and not options.never_delete:
        retention = await Config.get('admin.default_archive_retention_days')
    data = await _archive_data(user.id, options=options, owned=owned, db=db)
    archive = _archive_record(user, actor_id=actor_id, options=options, data=data, retention=retention)
    db.add(archive)
    await db.commit()
    return archive


async def _existing_archive(user_id: str, *, options: OffboardingOptions, db: AsyncSession) -> UserArchive | None:
    previous = (
        await db.execute(select(UserArchive).where(UserArchive.user_id == user_id, UserArchive.restored.is_(False)))
    ).scalars()
    for archive in previous:
        plan = archive.data.get('offboarding')
        if plan is None:
            continue
        if plan['mode'] != options.mode or plan['transfer_to'] != options.transfer_to:
            raise SoevApiError(
                409, 'policy_forbids', 'Resume the existing offboarding', constraint='offboarding:in_progress'
            )
        return archive
    return None


async def _archive_data(user_id: str, *, options: OffboardingOptions, owned: Inventory, db: AsyncSession) -> dict:
    snapshot = await ArchiveService.collect_user_data(user_id, db=db)
    data = snapshot.to_dict()
    data['collections'] = []
    data['resources'] = {}
    if options.mode == 'archive':
        data['collections'] = [row['key'] for row in owned.collections if row['source_kind'] == 'local']
        for table in RESOURCE_TABLES:
            ids = (await db.execute(select(table.id).where(table.user_id == user_id))).scalars()
            data['resources'][table.__tablename__] = list(ids)
    data['offboarding'] = {'mode': options.mode, 'transfer_to': options.transfer_to, 'collections': owned.collections}
    return data


def _archive_record(
    user: UserModel, *, actor_id: str, options: OffboardingOptions, data: dict, retention: int | None
) -> UserArchive:
    now = int(time.time())
    return UserArchive(
        id=str(uuid4()),
        user_id=user.id,
        user_email=user.email,
        user_name=user.name,
        reason=options.archive_reason,
        archived_by=actor_id,
        data=data,
        retention_days=None if options.never_delete else retention,
        expires_at=None if options.never_delete else now + retention * 86400,
        never_delete=options.never_delete,
        restored=False,
        created_at=now,
        updated_at=now,
    )


async def delete_external_user_data(user_id: str, *, options: OffboardingOptions, db: AsyncSession) -> None:
    """Erase memory vectors, disposable files and bookmarked threads before unlinking."""
    await _delete_memory_vectors(user_id)
    await _delete_file_storage(user_id, options=options, db=db)
    await _delete_threads(user_id, db=db)


async def _delete_memory_vectors(user_id: str) -> None:
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

    name = f'user-memory-{user_id}'
    if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=name):
        await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=name)


async def _delete_file_storage(user_id: str, *, options: OffboardingOptions, db: AsyncSession) -> None:
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

    files = list((await db.execute(select(File).where(File.user_id == user_id))).scalars())
    discarded = [row for row in files if options.mode == 'delete' or (row.meta or {}).get('source') is not None]
    for row in discarded:
        collection = f'file-{row.id}'
        if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=collection):
            await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=collection)
    paths = [row.path for row in discarded if row.path]
    await delete_storage_files(paths)


async def delete_storage_files(paths: list[str]) -> None:
    """Refuse local erasure if the storage provider reports an incomplete batch."""
    from open_webui.storage.provider import Storage

    if not paths:
        return
    deleted = await asyncio.to_thread(Storage.delete_files, paths)
    if deleted != len(paths):
        raise SoevApiError(503, 'service_unavailable', 'File storage deletion was incomplete')


async def _delete_threads(user_id: str, *, db: AsyncSession) -> None:
    refuse_local_content()
    if not configured():
        return
    messages = (await db.execute(select(ChatMessage).where(ChatMessage.user_id == user_id))).scalars()
    threads = {
        bookmark['thread_id']
        for row in messages
        if isinstance(bookmark := (row.meta or {}).get('agent_v2'), dict) and bookmark.get('thread_id')
    }
    for thread_id in sorted(threads):
        try:
            await identity.build_client().chat_delete(
                f'/v1/chat/threads/{quote(thread_id, safe="")}', as_user=f'owui:user:{user_id}'
            )
        except SoevApiError as error:
            if error.status != 404:
                raise


async def finalize_local(
    user_id: str, *, options: OffboardingOptions, archive: UserArchive | None, db: AsyncSession
) -> int:
    """Stage all content and account changes in the caller's transaction."""
    refuse_local_content()
    if archive is not None:
        db.add(archive)
    await _dispose_resources(user_id, options=options, db=db)
    for table in DELETE_USER_TABLES:
        await db.execute(delete(table).where(table.user_id == user_id))
    await db.execute(
        delete(AccessGrant).where(AccessGrant.principal_type == 'user', AccessGrant.principal_id == user_id)
    )
    await db.execute(delete(Auth).where(Auth.id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    return 1


async def _dispose_resources(user_id: str, *, options: OffboardingOptions, db: AsyncSession) -> None:
    from open_webui.soev.archive import ARCHIVE_OWNER

    owner = ARCHIVE_OWNER if options.mode == 'archive' else options.transfer_to
    await _delete_cloud_rows(user_id, db=db)
    for table in RESOURCE_TABLES:
        await _dispose_resource_table(table, user_id=user_id, options=options, owner=owner, db=db)
    if options.mode == 'delete':
        await db.execute(delete(PromptHistory).where(PromptHistory.user_id == user_id))
    else:
        await db.execute(update(PromptHistory).where(PromptHistory.user_id == user_id).values(user_id=owner))


async def _delete_cloud_rows(user_id: str, *, db: AsyncSession) -> None:
    cloud_ids = select(Knowledge.id).where(Knowledge.user_id == user_id, Knowledge.type != 'local')
    await db.execute(delete(KnowledgeFile).where(KnowledgeFile.knowledge_id.in_(cloud_ids)))
    await db.execute(delete(KnowledgeDirectory).where(KnowledgeDirectory.knowledge_id.in_(cloud_ids)))
    await db.execute(delete(Knowledge).where(Knowledge.id.in_(cloud_ids)))
    files = (await db.execute(select(File).where(File.user_id == user_id))).scalars()
    ids = [row.id for row in files if (row.meta or {}).get('source') is not None]
    await delete_resource_dependents(File, ids, db=db)
    await db.execute(delete(File).where(File.id.in_(ids)))


async def _dispose_resource_table(
    table: type[Base], *, user_id: str, options: OffboardingOptions, owner: str | None, db: AsyncSession
) -> None:
    ids = list((await db.execute(select(table.id).where(table.user_id == user_id))).scalars())
    if options.mode == 'delete':
        await delete_resource_dependents(table, ids, db=db)
        await db.execute(
            delete(AccessGrant).where(
                AccessGrant.resource_type == table.__tablename__, AccessGrant.resource_id.in_(ids)
            )
        )
        await db.execute(delete(table).where(table.user_id == user_id))
        return
    await db.execute(update(table).where(table.user_id == user_id).values(user_id=owner))
    if options.mode == 'archive' and table is not Knowledge:
        await _archive_grants(table.__tablename__, ids, db=db)


async def delete_resource_dependents(table: type[Base], ids: list[str], *, db: AsyncSession) -> None:
    """Stage junction cleanup before deleting a resource row."""
    refuse_local_content()
    if table is Knowledge:
        await db.execute(delete(KnowledgeFile).where(KnowledgeFile.knowledge_id.in_(ids)))
        await db.execute(delete(KnowledgeDirectory).where(KnowledgeDirectory.knowledge_id.in_(ids)))
    if table is Prompt:
        await db.execute(delete(PromptHistory).where(PromptHistory.prompt_id.in_(ids)))
    if table is File:
        for junction in (ChatFile, KnowledgeFile):
            await db.execute(delete(junction).where(junction.file_id.in_(ids)))


async def _archive_grants(kind: str, ids: list[str], *, db: AsyncSession) -> None:
    await db.execute(delete(AccessGrant).where(AccessGrant.resource_type == kind, AccessGrant.resource_id.in_(ids)))
    for resource_id in ids:
        db.add(
            AccessGrant(
                id=str(uuid4()),
                resource_type=kind,
                resource_id=resource_id,
                principal_type='group',
                principal_id=archive_group_ref().removeprefix('platform:group:'),
                permission='read',
                created_at=int(time.time()),
            )
        )
