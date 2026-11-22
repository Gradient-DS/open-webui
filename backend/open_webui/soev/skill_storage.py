"""Remove skill holdings with their links, then purge only confirmed unreferenced objects."""

import asyncio
from contextlib import asynccontextmanager

import sqlalchemy as sa

from open_webui.models import files
from open_webui.models.skill_files import SkillFile
from open_webui.storage.provider import Storage


@asynccontextmanager
async def session_for(db=None):
    if db is not None:
        yield db
    else:
        async with files.get_async_db_context() as session:
            yield session


async def linked(session, file_id: str) -> bool:
    connection = await session.connection()
    tables = await connection.run_sync(lambda bind: set(sa.inspect(bind).get_table_names()))
    for name in ('skill_file', 'chat_file', 'knowledge_file'):
        if name in tables:
            table = sa.table(name, sa.column('file_id'))
            if await session.scalar(sa.select(table.c.file_id).where(table.c.file_id == file_id).limit(1)) is not None:
                return True
    row = await session.get(files.File, file_id)
    if row is not None:
        from open_webui.soev.file_collections import collections_for

        if len(await collections_for(row, session=session)) > 1:
            return True
    return False


async def remove_unused(session, file_ids) -> list[tuple[str, str]]:
    await session.flush()
    removed = []
    for file_id in set(file_ids):
        row = await session.scalar(sa.select(files.File).where(files.File.id == file_id).with_for_update())
        if row is not None and not await linked(session, file_id):
            if row.path:
                removed.append((row.id, row.path))
            await session.delete(row)
    return removed


async def purge_unreferenced(objects, *, db=None) -> None:
    for file_id, path in objects:
        # Commit may have raised after succeeding. Re-read both the link and the File before deleting bytes.
        async with session_for(db) as session:
            if await linked(session, file_id):
                continue
            if (
                await session.scalar(
                    sa.select(files.File.id).where(sa.or_(files.File.id == file_id, files.File.path == path)).limit(1)
                )
                is not None
            ):
                continue
        await asyncio.to_thread(Storage.delete_file, path)


async def upload_linked(file_id: str, *, db=None) -> bool:
    async with session_for(db) as session:
        return await session.scalar(sa.select(SkillFile.id).where(SkillFile.file_id == file_id).limit(1)) is not None


async def delete_skill(skill_id: str, *, db=None) -> bool:
    from open_webui.models.skills import Skill
    from open_webui.models.access_grants import AccessGrant

    removed = []
    async with session_for(db) as session:
        try:
            skill = await session.scalar(sa.select(Skill).where(Skill.id == skill_id).with_for_update())
            if skill is None:
                return False
            file_ids = list(await session.scalars(sa.select(SkillFile.file_id).where(SkillFile.skill_id == skill_id)))
            await session.execute(sa.delete(SkillFile).where(SkillFile.skill_id == skill_id))
            removed = await remove_unused(session, file_ids)
            await session.execute(
                sa.delete(AccessGrant).where(AccessGrant.resource_type == 'skill', AccessGrant.resource_id == skill_id)
            )
            await session.delete(skill)
            commit = asyncio.create_task(session.commit())
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                await commit
                raise
        except BaseException as error:
            await session.rollback()
            # Ambiguous commit: never purge a surviving File/link.
            await purge_unreferenced(removed, db=session)
            if not isinstance(error, asyncio.CancelledError) and await session.get(Skill, skill_id) is None:
                return True
            raise
        await purge_unreferenced(removed, db=session)
    return True
