"""[Gradient] Paths in an assistant's skill folder; bytes remain in owner-owned Files."""

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession


class AssistantSkillFile(Base):
    __tablename__ = 'assistant_skill_file'

    id = Column(String, primary_key=True)
    assistant_id = Column(Text, ForeignKey('model.id', ondelete='CASCADE'), nullable=False)
    file_id = Column(String, ForeignKey('file.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(String, nullable=False)
    path = Column(String(255), nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (UniqueConstraint('assistant_id', 'path', name='uq_assistant_skill_file_path'),)


class AssistantSkillFileModel(BaseModel):
    id: str
    assistant_id: str
    file_id: str
    user_id: str
    path: str
    created_at: int
    updated_at: int
    model_config = ConfigDict(from_attributes=True)


class AssistantSkillFilesTable:
    async def file_versions(self, file_ids: list[str], db: AsyncSession | None = None) -> dict[str, list]:
        # Local import: Files also uses this join table for deletion cleanup.
        from open_webui.models.files import File

        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(File.id, File.user_id, File.hash, File.updated_at, File.meta).where(File.id.in_(file_ids))
            )
            return {row.id: [row.user_id, row.hash, row.updated_at, (row.meta or {}).get('size')] for row in result}

    async def get_files(self, assistant_id: str, db: AsyncSession | None = None) -> list[AssistantSkillFileModel]:
        async with get_async_db_context(db) as db:
            rows = await db.execute(
                select(AssistantSkillFile).filter_by(assistant_id=assistant_id).order_by(AssistantSkillFile.path)
            )
            return [AssistantSkillFileModel.model_validate(row) for row in rows.scalars()]


AssistantSkillFiles = AssistantSkillFilesTable()
