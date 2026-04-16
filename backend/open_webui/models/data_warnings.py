"""Data sovereignty warning audit log model."""

import time
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Column, String, Text, BigInteger, JSON, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context


class DataWarningLog(Base):
    __tablename__ = 'data_warning_log'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    chat_id = Column(String, nullable=False, index=True)
    model_id = Column(String, nullable=False)
    capabilities = Column(JSON, nullable=False)  # list of capability strings
    warning_message = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class DataWarningLogForm(BaseModel):
    chat_id: str
    model_id: str
    capabilities: list[str]
    warning_message: Optional[str] = None


class DataWarningLogModel(BaseModel):
    id: str
    user_id: str
    chat_id: str
    model_id: str
    capabilities: list[str]
    warning_message: Optional[str]
    created_at: int


class DataWarningLogs:
    @staticmethod
    async def insert_log(
        user_id: str, form: DataWarningLogForm, db: Optional[AsyncSession] = None
    ) -> DataWarningLogModel:
        async with get_async_db_context(db) as db:
            log_entry = DataWarningLog(
                id=str(uuid.uuid4()),
                user_id=user_id,
                chat_id=form.chat_id,
                model_id=form.model_id,
                capabilities=form.capabilities,
                warning_message=form.warning_message,
                created_at=int(time.time()),
            )
            db.add(log_entry)
            await db.commit()
            await db.refresh(log_entry)
            return DataWarningLogModel.model_validate(log_entry)

    @staticmethod
    async def get_logs_by_user(user_id: str, db: Optional[AsyncSession] = None) -> list[DataWarningLogModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(DataWarningLog)
                .filter(DataWarningLog.user_id == user_id)
                .order_by(DataWarningLog.created_at.desc())
            )
            logs = result.scalars().all()
            return [DataWarningLogModel.model_validate(log) for log in logs]

    @staticmethod
    async def get_logs_by_chat(chat_id: str, db: Optional[AsyncSession] = None) -> list[DataWarningLogModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(DataWarningLog)
                .filter(DataWarningLog.chat_id == chat_id)
                .order_by(DataWarningLog.created_at.desc())
            )
            logs = result.scalars().all()
            return [DataWarningLogModel.model_validate(log) for log in logs]
