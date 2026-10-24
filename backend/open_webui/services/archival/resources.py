"""Keep archived resources on the chat snapshot's retention schedule."""

import datetime as dt
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_db_context
from open_webui.models.access_grants import AccessGrant
from open_webui.models.files import File
from open_webui.models.user_archives import UserArchiveModel, UserArchives
from open_webui.services.deletion.offboarding_store import (
    RESOURCE_TABLES,
    delete_resource_dependents,
    delete_storage_files,
)
from open_webui.soev import group_sync, identity
from open_webui.soev.client import SoevApiError, build_service_client
from open_webui.soev.group_sync import ARCHIVE_OWNER


async def set_expiry(archive: UserArchiveModel) -> None:
    """Publish an archive's new retention before committing its local metadata."""
    body = {'owner': ARCHIVE_OWNER, 'readers': [f'owui:group:{group_sync.ADMIN_ARCHIVE_GROUP}']}
    if archive.expires_at is not None and not archive.never_delete:
        body['expires_at'] = dt.datetime.fromtimestamp(archive.expires_at, dt.UTC).isoformat().replace('+00:00', 'Z')
    for key in archive.data.get('collections', []):
        await build_service_client().send(
            'POST',
            f'/v1/collections/{quote(key, safe="")}/owner',
            body,
            idempotency_key=f'archive-retention:{uuid4()}',
        )


async def delete_archive(archive_id: str, *, db: AsyncSession | None = None, delete_collections: bool = True) -> bool:
    """Remove archived content before dropping its snapshot and resource manifest."""
    async with get_async_db_context(db) as session:
        archive = await UserArchives.get_archive_by_id(archive_id, db=session)
        if archive is None:
            return False
        if archive.restored:
            return await UserArchives.delete_archive(archive_id, db=session)
        # Expired platform archives are deleted by soev-sync using archive_expires_at.
        for key in archive.data.get('collections', []) if delete_collections else []:
            try:
                await identity.build_client().send(
                    'DELETE',
                    f'/v1/collections/{quote(key, safe="")}',
                    None,
                    idempotency_key=f'archive-delete:{archive_id}:{key}',
                )
            except SoevApiError as error:
                if error.status != 404:
                    raise
        try:
            await _delete_local(archive, db=session)
            return await UserArchives.delete_archive(archive_id, db=session)
        except BaseException:
            await session.rollback()
            raise


async def _delete_local(archive: UserArchiveModel, *, db: AsyncSession) -> None:
    manifest = archive.data.get('resources', {})
    for table in RESOURCE_TABLES:
        requested = manifest.get(table.__tablename__, [])
        ids = list(
            (
                await db.execute(select(table.id).where(table.id.in_(requested), table.user_id == ARCHIVE_OWNER))
            ).scalars()
        )
        if table is File:
            files = (await db.execute(select(File).where(File.id.in_(ids)))).scalars()
            paths = [row.path for row in files if row.path]
            await delete_storage_files(paths)
        await delete_resource_dependents(table, ids, db=db)
        await db.execute(
            delete(AccessGrant).where(
                AccessGrant.resource_type == table.__tablename__, AccessGrant.resource_id.in_(ids)
            )
        )
        await db.execute(delete(table).where(table.id.in_(ids)))
