"""Skill-file join model — mirrors knowledge.py:KnowledgeFile + KnowledgeFilesTable.

Attaching a markdown file to a Skill stores a ``skill_file`` row (keyed by a
virtual ``path`` within the skill bundle) plus populates ``File.data['content']``
once at attach/create time so the agent consumer can read it cheaply from the
DB (no Storage round-trip needed at inference time).

The bundle is a *path tree*: each row pairs a backing ``File`` with a virtual
``path`` (e.g. ``docs/guide.md``).  The same File may appear at multiple paths;
uniqueness is enforced on ``(skill_id, path)`` (case-insensitive collisions are
checked at the router/table layer — see ``path_exists``).

All new code; upstream ``models/skills.py`` is NOT modified.
"""

import logging
import time
import uuid

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.files import File, FileModel, FileModelResponse
from open_webui.models.users import User, UserModel, UserResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, ForeignKey, String, UniqueConstraint, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

####################
# SkillFile DB Schema
####################


class SkillFile(Base):
    __tablename__ = 'skill_file'

    id = Column(String, primary_key=True, unique=True)

    skill_id = Column(String, ForeignKey('skill.id', ondelete='CASCADE'), nullable=False)
    file_id = Column(String, ForeignKey('file.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(String, nullable=False)

    # Virtual path within the skill bundle, e.g. "docs/guide.md". Markdown-only,
    # validated at the router layer. Unique per skill (exact-match; the router
    # additionally rejects case-insensitive collisions).
    path = Column(String, nullable=False)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint('skill_id', 'path', name='uq_skill_file_skill_path'),)


class SkillFileModel(BaseModel):
    id: str
    skill_id: str
    file_id: str
    user_id: str
    path: str

    created_at: int  # epoch timestamp
    updated_at: int  # epoch timestamp

    model_config = ConfigDict(from_attributes=True)


####################
# Response types
####################


class SkillFileUserResponse(FileModelResponse):
    user: UserResponse | None = None
    added_at: int | None = None
    path: str | None = None


class SkillFileListResponse(BaseModel):
    items: list[SkillFileUserResponse]
    total: int


####################
# Table accessor
####################


class SkillFilesTable:
    async def add_file_to_skill_by_id(
        self,
        skill_id: str,
        file_id: str,
        path: str,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> SkillFileModel | None:
        async with get_async_db_context(db) as db:
            skill_file = SkillFileModel(
                **{
                    'id': str(uuid.uuid4()),
                    'skill_id': skill_id,
                    'file_id': file_id,
                    'path': path,
                    'user_id': user_id,
                    'created_at': int(time.time()),
                    'updated_at': int(time.time()),
                }
            )
            try:
                result = SkillFile(**skill_file.model_dump())
                db.add(result)
                await db.commit()
                await db.refresh(result)
                if result:
                    return SkillFileModel.model_validate(result)
                return None
            except Exception:
                await db.rollback()
                return None

    async def has_file(self, skill_id: str, file_id: str, db: AsyncSession | None = None) -> bool:
        """Return True if the file is already attached to the skill (by file_id)."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(SkillFile).filter_by(skill_id=skill_id, file_id=file_id).limit(1))
                return result.scalars().first() is not None
        except Exception:
            return False

    async def get_file_by_path(self, skill_id: str, path: str, db: AsyncSession | None = None) -> SkillFileModel | None:
        """Return the SkillFile row at the exact (skill_id, path), or None."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(SkillFile).filter_by(skill_id=skill_id, path=path).limit(1))
                row = result.scalars().first()
                return SkillFileModel.model_validate(row) if row else None
        except Exception:
            return None

    async def path_exists(self, skill_id: str, path: str, db: AsyncSession | None = None) -> bool:
        """Case-INSENSITIVE existence check for collision detection.

        The DB unique is exact ``(skill_id, path)``; this guards against
        ``Foo.md`` vs ``foo.md`` collisions at the application layer (the brief
        explicitly forbids a functional/lower() index for cross-dialect reasons).
        """
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(SkillFile)
                    .filter(SkillFile.skill_id == skill_id)
                    .filter(func.lower(SkillFile.path) == path.lower())
                    .limit(1)
                )
                return result.scalars().first() is not None
        except Exception:
            return False

    async def remove_file_by_path(
        self, skill_id: str, path: str, db: AsyncSession | None = None
    ) -> SkillFileModel | None:
        """Delete the single row at (skill_id, path). Returns the removed row
        (so the router can delete the backing File), or None if absent."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(SkillFile).filter_by(skill_id=skill_id, path=path).limit(1))
                row = result.scalars().first()
                if row is None:
                    return None
                removed = SkillFileModel.model_validate(row)
                await db.delete(row)
                await db.commit()
                return removed
        except Exception:
            return None

    async def move_path(self, skill_id: str, from_path: str, to_path: str, db: AsyncSession | None = None) -> bool:
        """Rename a single row's path from ``from_path`` to ``to_path``.

        Returns False if no row matches ``from_path``."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(SkillFile).filter_by(skill_id=skill_id, path=from_path).limit(1))
                row = result.scalars().first()
                if row is None:
                    return False
                row.path = to_path
                row.updated_at = int(time.time())
                await db.commit()
                return True
        except Exception:
            return False

    async def move_paths_under_prefix(
        self, skill_id: str, from_prefix: str, to_prefix: str, db: AsyncSession | None = None
    ) -> bool:
        """Folder rename: rewrite the prefix of every row whose path starts with
        ``from_prefix`` to ``to_prefix`` in a single transaction. Returns False
        if no rows match (so the router can 404)."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(SkillFile)
                    .filter(SkillFile.skill_id == skill_id)
                    .filter(SkillFile.path.like(f'{_escape_like(from_prefix)}%', escape='\\'))
                )
                rows = result.scalars().all()
                if not rows:
                    return False
                now = int(time.time())
                for row in rows:
                    row.path = to_prefix + row.path[len(from_prefix) :]
                    row.updated_at = now
                await db.commit()
                return True
        except Exception:
            return False

    async def remove_paths_under_prefix(
        self, skill_id: str, prefix: str, db: AsyncSession | None = None
    ) -> list[SkillFileModel]:
        """Folder delete: remove every row whose path starts with ``prefix``.

        Returns the removed rows (file_ids included) so the router can delete
        the backing Files."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(SkillFile)
                    .filter(SkillFile.skill_id == skill_id)
                    .filter(SkillFile.path.like(f'{_escape_like(prefix)}%', escape='\\'))
                )
                rows = result.scalars().all()
                if not rows:
                    return []
                removed = [SkillFileModel.model_validate(row) for row in rows]
                await db.execute(
                    delete(SkillFile)
                    .where(SkillFile.skill_id == skill_id)
                    .where(SkillFile.path.like(f'{_escape_like(prefix)}%', escape='\\'))
                )
                await db.commit()
                return removed
        except Exception:
            return []

    async def get_files_by_skill_id(self, skill_id: str, db: AsyncSession | None = None) -> list[SkillFileModel]:
        """Return all SkillFile rows attached to the given skill."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(SkillFile).filter_by(skill_id=skill_id))
                return [SkillFileModel.model_validate(row) for row in result.scalars().all()]
        except Exception:
            return []

    async def get_file_counts_by_skill_ids(
        self, skill_ids: list[str], db: AsyncSession | None = None
    ) -> dict[str, int]:
        """Return {skill_id: file_count} for the given skills in one grouped query."""
        if not skill_ids:
            return {}
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(SkillFile.skill_id, func.count(SkillFile.id))
                .filter(SkillFile.skill_id.in_(skill_ids))
                .group_by(SkillFile.skill_id)
            )
            return {row[0]: row[1] for row in result.all()}

    async def search_files_by_id(
        self,
        skill_id: str,
        user_id: str,
        filter: dict,
        skip: int = 0,
        limit: int = 30,
        db: AsyncSession | None = None,
    ) -> SkillFileListResponse:
        try:
            async with get_async_db_context(db) as db:
                stmt = (
                    select(File, User, SkillFile.created_at, SkillFile.path)
                    .join(SkillFile, File.id == SkillFile.file_id)
                    .outerjoin(User, User.id == SkillFile.user_id)
                    .filter(SkillFile.skill_id == skill_id)
                )

                # Sort by the virtual path so the client can build the tree in order.
                primary_sort = SkillFile.path.asc()

                if filter:
                    query_key = filter.get('query')
                    if query_key:
                        stmt = stmt.filter(SkillFile.path.ilike(f'%{query_key}%'))

                stmt = stmt.order_by(primary_sort, File.id.asc())

                count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
                total = count_result.scalar()

                if skip:
                    stmt = stmt.offset(skip)
                if limit:
                    stmt = stmt.limit(limit)

                result = await db.execute(stmt)
                items = result.all()

                files = []
                for file, user, added_at, path in items:
                    file_dump = FileModel.model_validate(file).model_dump()
                    # Drop the File's *storage* path; the virtual skill-bundle
                    # path (from SkillFile) is the one the client cares about.
                    file_dump.pop('path', None)
                    files.append(
                        SkillFileUserResponse(
                            **file_dump,
                            user=(UserResponse(**UserModel.model_validate(user).model_dump()) if user else None),
                            added_at=added_at,
                            path=path,
                        )
                    )

                return SkillFileListResponse(items=files, total=total)
        except Exception as e:
            log.exception(e)
            return SkillFileListResponse(items=[], total=0)


def _escape_like(value: str) -> str:
    r"""Escape SQL ``LIKE`` wildcards in a literal prefix.

    A folder prefix like ``docs/`` is a literal string, but ``%`` / ``_`` in a
    path segment (allowed by validation? no — but defensive) or the ``\`` escape
    char itself must not be interpreted as wildcards. Used with
    ``.like(..., escape='\\')``.
    """
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


SkillFiles = SkillFilesTable()
