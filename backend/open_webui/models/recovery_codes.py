import logging
import time
import uuid
from typing import Optional

import bcrypt
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, String, Text, ForeignKey
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, get_db_context
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
    def generate_codes(self, user_id: str, db: Optional[Session] = None) -> list[str]:
        """Generate new recovery codes. Returns plaintext codes (show once)."""
        plaintext_codes = generate_recovery_codes(count=10)

        with get_db_context(db) as db:
            # Delete any existing codes first
            db.query(RecoveryCode).filter_by(user_id=user_id).delete()

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
            db.commit()

        return plaintext_codes

    def verify_code(self, user_id: str, code: str, db: Optional[Session] = None) -> bool:
        """Verify a recovery code and mark it as used. Returns True if valid."""
        with get_db_context(db) as db:
            codes = db.query(RecoveryCode).filter_by(user_id=user_id, used=False).all()
            for rc in codes:
                if bcrypt.checkpw(code.encode('utf-8'), rc.code_hash.encode('utf-8')):
                    rc.used = True
                    rc.used_at = int(time.time())
                    db.commit()
                    return True
        return False

    def delete_all(self, user_id: str, db: Optional[Session] = None) -> None:
        with get_db_context(db) as db:
            db.query(RecoveryCode).filter_by(user_id=user_id).delete()
            db.commit()

    def count_unused(self, user_id: str, db: Optional[Session] = None) -> int:
        with get_db_context(db) as db:
            return db.query(RecoveryCode).filter_by(user_id=user_id, used=False).count()


RecoveryCodes = RecoveryCodesTable()
