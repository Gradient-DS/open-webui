"""Skill-file join model — mirrors knowledge.py:KnowledgeFile + KnowledgeFilesTable.

Attaching a markdown file to a Skill stores a ``skill_file`` row plus
populates ``File.data['content']`` once at attach time so the agent
consumer can read it cheaply from the DB (no Storage round-trip needed
at inference time).

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

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint('skill_id', 'file_id', name='uq_skill_file_skill_file'),)


class SkillFileModel(BaseModel):
    id: str
    skill_id: str
    file_id: str
    user_id: str

    created_at: int  # epoch timestamp
    updated_at: int  # epoch timestamp

    model_config = ConfigDict(from_attributes=True)


####################
# Response types
####################


class SkillFileUserResponse(FileModelResponse):
    user: UserResponse | None = None
    added_at: int | None = None


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
        user_id: str,
        db: AsyncSession | None = None,
    ) -> SkillFileModel | None:
        async with get_async_db_context(db) as db:
            skill_file = SkillFileModel(
                **{
                    'id': str(uuid.uuid4()),
                    'skill_id': skill_id,
                    'file_id': file_id,
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
                return None

    async def has_file(self, skill_id: str, file_id: str, db: AsyncSession | None = None) -> bool:
        """Return True if the file is already attached to the skill."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(SkillFile).filter_by(skill_id=skill_id, file_id=file_id).limit(1))
                return result.scalars().first() is not None
        except Exception:
            return False

    async def remove_file_from_skill_by_id(self, skill_id: str, file_id: str, db: AsyncSession | None = None) -> bool:
        try:
            async with get_async_db_context(db) as db:
                await db.execute(delete(SkillFile).filter_by(skill_id=skill_id, file_id=file_id))
                await db.commit()
                return True
        except Exception:
            return False

    async def get_file_counts_by_skill_ids(
        self, skill_ids: list[str], db: AsyncSession | None = None
    ) -> dict[str, int]:
        """Return {skill_id: file_count} for the given skills in one grouped query."""
        if not skill_ids:
            return {}
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(SkillFile.skill_id, func.count(SkillFile.file_id))
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
                    select(File, User, SkillFile.created_at)
                    .join(SkillFile, File.id == SkillFile.file_id)
                    .outerjoin(User, User.id == SkillFile.user_id)
                    .filter(SkillFile.skill_id == skill_id)
                )

                primary_sort = File.filename.asc()

                if filter:
                    query_key = filter.get('query')
                    if query_key:
                        stmt = stmt.filter(File.filename.ilike(f'%{query_key}%'))

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
                for file, user, added_at in items:
                    files.append(
                        SkillFileUserResponse(
                            **FileModel.model_validate(file).model_dump(),
                            user=(UserResponse(**UserModel.model_validate(user).model_dump()) if user else None),
                            added_at=added_at,
                        )
                    )

                return SkillFileListResponse(items=files, total=total)
        except Exception as e:
            log.exception(e)
            return SkillFileListResponse(items=[], total=0)


SkillFiles = SkillFilesTable()
