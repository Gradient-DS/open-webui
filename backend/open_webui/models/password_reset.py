import time
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import BigInteger, Column, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context


class PasswordResetToken(Base):
    __tablename__ = 'password_reset_token'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(BigInteger, nullable=False)
    used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class PasswordResetTokenModel(BaseModel):
    id: str
    user_id: str
    token_hash: str
    expires_at: int
    used_at: Optional[int] = None
    created_at: int

    model_config = {'from_attributes': True}


class PasswordResetTokenTable:
    async def create(
        self,
        user_id: str,
        token_hash: str,
        expires_at: int,
        db: Optional[AsyncSession] = None,
    ) -> PasswordResetTokenModel:
        async with get_async_db_context(db) as db:
            row = PasswordResetToken(
                id=str(uuid.uuid4()),
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                used_at=None,
                created_at=int(time.time()),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return PasswordResetTokenModel.model_validate(row)

    async def get_by_token_hash(
        self, token_hash: str, db: Optional[AsyncSession] = None
    ) -> Optional[PasswordResetTokenModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(PasswordResetToken).filter_by(token_hash=token_hash))
            row = result.scalars().first()
            return PasswordResetTokenModel.model_validate(row) if row else None

    async def mark_used(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(update(PasswordResetToken).filter_by(id=id).values(used_at=int(time.time())))
            await db.commit()
            return result.rowcount == 1

    async def invalidate_unused_for_user(self, user_id: str, db: Optional[AsyncSession] = None) -> None:
        async with get_async_db_context(db) as db:
            await db.execute(
                update(PasswordResetToken)
                .filter(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
                .values(used_at=int(time.time()))
            )
            await db.commit()


PasswordResetTokens = PasswordResetTokenTable()
