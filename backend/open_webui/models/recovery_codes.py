import logging
import time
import uuid
from typing import Optional

import bcrypt
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, String, Text, ForeignKey, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.utils.totp import generate_recovery_codes

log = logging.getLogger(__name__)

####################
# DB MODEL
####################


class RecoveryCode(Base):
    __tablename__ = 'recovery_code'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, ForeignKey('auth.id', ondelete='CASCADE'), nullable=False)
    code_hash = Column(Text, nullable=False)
    used = Column(Boolean, default=False)
    used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class RecoveryCodeModel(BaseModel):
    id: str
    user_id: str
    used: bool = False


####################
# Table
####################


class RecoveryCodesTable:
    async def generate_codes(self, user_id: str, db: Optional[AsyncSession] = None) -> list[str]:
        """Generate new recovery codes. Returns plaintext codes (show once)."""
        plaintext_codes = generate_recovery_codes(count=10)

        async with get_async_db_context(db) as db:
            # Delete any existing codes first
            await db.execute(delete(RecoveryCode).filter_by(user_id=user_id))

            now = int(time.time())
            for code in plaintext_codes:
                code_hash = bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                db.add(
                    RecoveryCode(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        code_hash=code_hash,
                        used=False,
                        created_at=now,
                    )
                )
            await db.commit()

        return plaintext_codes

    async def verify_code(self, user_id: str, code: str, db: Optional[AsyncSession] = None) -> bool:
        """Verify a recovery code and mark it as used. Returns True if valid."""
        async with get_async_db_context(db) as db:
            result = await db.execute(select(RecoveryCode).filter_by(user_id=user_id, used=False))
            codes = result.scalars().all()
            for rc in codes:
                if bcrypt.checkpw(code.encode('utf-8'), rc.code_hash.encode('utf-8')):
                    rc.used = True
                    rc.used_at = int(time.time())
                    await db.commit()
                    return True
        return False

    async def delete_all(self, user_id: str, db: Optional[AsyncSession] = None) -> None:
        async with get_async_db_context(db) as db:
            await db.execute(delete(RecoveryCode).filter_by(user_id=user_id))
            await db.commit()

    async def count_unused(self, user_id: str, db: Optional[AsyncSession] = None) -> int:
        async with get_async_db_context(db) as db:
            from sqlalchemy import func

            result = await db.execute(
                select(func.count()).select_from(RecoveryCode).filter_by(user_id=user_id, used=False)
            )
            return result.scalar() or 0


RecoveryCodes = RecoveryCodesTable()
