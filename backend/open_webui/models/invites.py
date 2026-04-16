import time
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import BigInteger, Column, String, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context


class Invite(Base):
    __tablename__ = 'invite'

    id = Column(String, primary_key=True)
    email = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    role = Column(String, default='user')
    invited_by = Column(String, nullable=False)
    expires_at = Column(BigInteger, nullable=False)
    accepted_at = Column(BigInteger, nullable=True)
    revoked_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class InviteModel(BaseModel):
    id: str
    email: str
    name: str
    token: str
    role: str
    invited_by: str
    expires_at: int
    accepted_at: Optional[int] = None
    revoked_at: Optional[int] = None
    created_at: int

    model_config = {'from_attributes': True}


class InviteForm(BaseModel):
    name: str
    email: str
    role: Optional[str] = 'user'
    send_email: Optional[bool] = None


class AcceptInviteForm(BaseModel):
    password: str
    name: Optional[str] = None


class InviteTable:
    async def create_invite(
        self,
        email: str,
        name: str,
        role: str,
        invited_by: str,
        expires_at: int,
        db: Optional[AsyncSession] = None,
    ) -> Optional[InviteModel]:
        async with get_async_db_context(db) as db:
            invite = Invite(
                id=str(uuid.uuid4()),
                email=email.lower(),
                name=name,
                token=str(uuid.uuid4()),
                role=role,
                invited_by=invited_by,
                expires_at=expires_at,
                created_at=int(time.time()),
            )
            db.add(invite)
            await db.commit()
            await db.refresh(invite)
            return InviteModel.model_validate(invite)

    async def get_invite_by_token(self, token: str, db: Optional[AsyncSession] = None) -> Optional[InviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Invite).filter_by(token=token))
            invite = result.scalars().first()
            return InviteModel.model_validate(invite) if invite else None

    async def get_invite_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[InviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Invite).filter_by(id=id))
            invite = result.scalars().first()
            return InviteModel.model_validate(invite) if invite else None

    async def get_pending_invites(self, db: Optional[AsyncSession] = None) -> list[InviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Invite)
                .filter(Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))
                .order_by(Invite.created_at.desc())
            )
            invites = result.scalars().all()
            return [InviteModel.model_validate(i) for i in invites]

    async def get_pending_invite_by_email(self, email: str, db: Optional[AsyncSession] = None) -> Optional[InviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Invite)
                .filter(
                    Invite.email == email.lower(),
                    Invite.accepted_at.is_(None),
                    Invite.revoked_at.is_(None),
                )
                .order_by(Invite.created_at.desc())
            )
            invite = result.scalars().first()
            return InviteModel.model_validate(invite) if invite else None

    async def accept_invite(self, token: str, db: Optional[AsyncSession] = None) -> Optional[InviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Invite).filter_by(token=token))
            invite = result.scalars().first()
            if invite:
                invite.accepted_at = int(time.time())
                await db.commit()
                await db.refresh(invite)
                return InviteModel.model_validate(invite)
            return None

    async def revoke_invite(self, id: str, db: Optional[AsyncSession] = None) -> Optional[InviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Invite).filter_by(id=id))
            invite = result.scalars().first()
            if invite:
                invite.revoked_at = int(time.time())
                await db.commit()
                await db.refresh(invite)
                return InviteModel.model_validate(invite)
            return None

    async def refresh_invite(
        self, id: str, new_expires_at: int, db: Optional[AsyncSession] = None
    ) -> Optional[InviteModel]:
        """Refresh an invite with a new token and expiry (for resend)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Invite).filter_by(id=id))
            invite = result.scalars().first()
            if invite:
                invite.token = str(uuid.uuid4())
                invite.expires_at = new_expires_at
                await db.commit()
                await db.refresh(invite)
                return InviteModel.model_validate(invite)
            return None


Invites = InviteTable()
