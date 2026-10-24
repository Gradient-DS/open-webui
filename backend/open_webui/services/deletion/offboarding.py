"""Fail-closed offboarding: remote revocation precedes one local deletion transaction."""

import datetime as dt
from dataclasses import dataclass
from typing import Literal, TypedDict
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_db_context
from open_webui.models.groups import Groups
from open_webui.models.user_archives import UserArchive
from open_webui.models.users import Users
from open_webui.services.deletion import offboarding_store as store
from open_webui.soev import group_sync, identity, ingest
from open_webui.soev.availability import configured
from open_webui.soev.client import SoevApiError
from open_webui.soev.cloud_sync import revoke_cloud
from open_webui.soev.group_sync import ARCHIVE_OWNER
from open_webui.soev.policy_jobs import refuse_offboarding


class OffboardingOptions(BaseModel):
    """Explicit resource disposition and optional chat snapshot retention."""

    mode: Literal['delete', 'transfer', 'archive'] = 'delete'
    transfer_to: str | None = None
    confirm_shared_deletion: list[str] = Field(default_factory=list)
    archive_before_delete: bool = False
    archive_reason: str = 'Admin deletion'
    archive_retention_days: int | None = Field(default=None, gt=0)
    never_delete: bool = False

    @model_validator(mode='after')
    def require_transfer_target(self) -> 'OffboardingOptions':
        if self.mode == 'transfer' and self.transfer_to is None:
            raise ValueError('transfer_to is required for transfer')
        return self


class SharedItem(TypedDict):
    id: str
    kind: str


@dataclass(frozen=True)
class Inventory:
    """Owned collections and the exact shared-resource confirmation inventory."""

    collections: list[dict]
    shared: list[SharedItem]


async def inventory(user_id: str, *, db: AsyncSession) -> Inventory:
    """List owned resources with the platform's principals and writers."""
    refuse_offboarding()
    user = await Users.get_user_by_id(user_id, db=db)
    if user is None:
        raise SoevApiError(404, 'not_found', 'User not found')
    ref = identity.external_ref(user)
    as_user = ref if user.role in {'user', 'admin'} else None
    collections = []
    if configured():
        if as_user is not None:
            # An approved user whose link push is still queued lists as themselves too.
            await identity.ensure_link(as_user, identity.build_client(), db=db)
        collections = [
            row
            async for row in identity.build_client().pages('/v1/collections', as_user=as_user)
            if row['created_by'] == ref
        ]
    shared = [
        {'id': row['key'], 'kind': 'knowledge'}
        for row in collections
        if not ingest.is_attachments_collection(row['key'])
        # [Claude] An assistant's files collection is listed as its assistant.
        and not ingest.is_assistant_collection(row['key'])
        and (set(row['principals']) | set(row['writers'])) - {ref}
    ]
    shared.extend(await store.shared_assistants(user_id, db=db))
    return Inventory(collections=collections, shared=sorted(shared, key=lambda item: (item['kind'], item['id'])))


async def offboard_user(
    user_id: str,
    *,
    actor_id: str,
    options: OffboardingOptions,
    db: AsyncSession | None = None,
    language: str = 'en',
) -> int:
    """Complete remote steps before atomically removing or reassigning local rows."""
    refuse_offboarding()
    async with get_async_db_context(db) as session:
        async with group_sync.membership_lock(session):
            async with identity.link_lock(f'owui:user:{user_id}'):
                return await _offboard(user_id, actor_id=actor_id, options=options, db=session, language=language)


async def _offboard(
    user_id: str, *, actor_id: str, options: OffboardingOptions, db: AsyncSession, language: str
) -> int:
    refuse_offboarding()
    user = await Users.get_user_by_id(user_id, db=db)
    if user is None:
        raise SoevApiError(404, 'not_found', 'User not found')
    await _validate_target(user_id, options, db=db)
    as_user = identity.external_ref(user) if user.role in {'user', 'admin'} else None
    owned = await inventory(user_id, db=db)
    _confirm_deletion(owned, options, language)
    archive = await store.prepare_archive(user, actor_id=actor_id, options=options, owned=owned, db=db)
    if configured() and as_user is not None:
        await revoke_cloud(user_id, client=identity.build_client())
    if not await Groups.remove_user_from_all_groups(user_id, db=db):
        raise SoevApiError(503, 'group_sync_unavailable', 'Group membership update failed')
    await store.narrow_owned_groups(user_id, db=db)
    await apply_collections(owned, options=options, archive=archive, as_user=as_user)
    await delete_external_user_data(user_id, options=options, db=db)
    if configured():
        await identity.unlink(identity.external_ref(user), identity.build_client())
    try:
        count = await finalize_local(user_id, options=options, archive=archive, db=db)
        await db.commit()
        return count
    except BaseException:
        await db.rollback()
        raise


async def _validate_target(user_id: str, options: OffboardingOptions, *, db: AsyncSession) -> None:
    if options.mode != 'transfer':
        return
    target = await Users.get_user_by_id(options.transfer_to, db=db)
    if target is None or target.id == user_id or target.role not in {'user', 'admin'}:
        raise SoevApiError(422, 'invalid_field', 'Invalid transfer target', constraint='offboarding:target')


def _confirm_deletion(owned: Inventory, options: OffboardingOptions, language: str) -> None:
    if options.mode != 'delete':
        return
    if sorted(options.confirm_shared_deletion) == sorted(item['id'] for item in owned.shared):
        return
    from open_webui.soev.errors import shared_deletion_message

    raise HTTPException(
        409,
        detail={
            'code': 'shared_data_confirmation_required',
            'detail': shared_deletion_message(language),
            'items': owned.shared,
        },
    )


async def apply_collections(
    owned: Inventory, *, options: OffboardingOptions, archive: UserArchive | None, as_user: str | None
) -> None:
    """Delete cloud collections in every mode; move only local collection ownership."""
    if not configured():
        return
    client = identity.build_client()
    rows = owned.collections if archive is None else archive.data['offboarding']['collections']
    for row in rows:
        path = f'/v1/collections/{quote(row["key"], safe="")}'
        # Chat attachments are personal like chats: never handed to another person.
        personal = ingest.is_attachments_collection(row['key'])
        if options.mode == 'delete' or row['source_kind'] != 'local' or (options.mode == 'transfer' and personal):
            await client.send('DELETE', path, None, as_user=as_user, idempotency_key=f'offboard-delete:{row["key"]}')
            continue
        body = {'owner': f'owui:user:{options.transfer_to}'}
        if options.mode == 'archive':
            body = {'owner': ARCHIVE_OWNER, 'readers': [f'owui:group:{group_sync.ADMIN_ARCHIVE_GROUP}']}
            if archive.expires_at is not None:
                body['expires_at'] = (
                    dt.datetime.fromtimestamp(archive.expires_at, dt.UTC).isoformat().replace('+00:00', 'Z')
                )
        await client.send(
            'POST',
            path + '/owner',
            body,
            idempotency_key=f'archive:{archive.id}:{row["key"]}' if options.mode == 'archive' else f'owner:{uuid4()}',
        )


async def delete_external_user_data(user_id: str, *, options: OffboardingOptions, db: AsyncSession) -> None:
    """Erase memory vectors and conversation threads before local source rows disappear."""
    await store.delete_external_user_data(user_id, options=options, db=db)


async def finalize_local(
    user_id: str, *, options: OffboardingOptions, archive: UserArchive | None, db: AsyncSession
) -> int:
    """Stage the local deletion and reassignment without committing inside the cascade."""
    return await store.finalize_local(user_id, options=options, archive=archive, db=db)
