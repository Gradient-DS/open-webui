"""Data sovereignty warning audit log model."""

import time
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Column, String, Text, BigInteger, JSON

from open_webui.internal.db import Base, get_db


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
    def insert_log(user_id: str, form: DataWarningLogForm) -> DataWarningLogModel:
        with get_db() as db:
            log = DataWarningLog(
                id=str(uuid.uuid4()),
                user_id=user_id,
                chat_id=form.chat_id,
                model_id=form.model_id,
                capabilities=form.capabilities,
                warning_message=form.warning_message,
                created_at=int(time.time()),
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            return DataWarningLogModel.model_validate(log)

    @staticmethod
    def get_logs_by_user(user_id: str) -> list[DataWarningLogModel]:
        with get_db() as db:
            logs = (
                db.query(DataWarningLog)
                .filter(DataWarningLog.user_id == user_id)
                .order_by(DataWarningLog.created_at.desc())
                .all()
            )
            return [DataWarningLogModel.model_validate(log) for log in logs]

    @staticmethod
    def get_logs_by_chat(chat_id: str) -> list[DataWarningLogModel]:
        with get_db() as db:
            logs = (
                db.query(DataWarningLog)
                .filter(DataWarningLog.chat_id == chat_id)
                .order_by(DataWarningLog.created_at.desc())
                .all()
            )
            return [DataWarningLogModel.model_validate(log) for log in logs]
