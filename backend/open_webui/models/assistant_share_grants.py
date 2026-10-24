import time
import uuid

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text, UniqueConstraint, delete, select
from sqlalchemy.ext.asyncio import AsyncSession


class AssistantShareGrant(Base):
    __tablename__ = 'assistant_share_grant'

    id = Column(Text, primary_key=True)
    assistant_id = Column(Text, nullable=False, index=True)
    owner_id = Column(Text, nullable=False, index=True)
    resource_type = Column(Text, nullable=False)  # "knowledge" or "note"
    resource_id = Column(Text, nullable=False)
    principal_type = Column(Text, nullable=False)  # "user" or "group"
    principal_id = Column(Text, nullable=False)  # user id, group id, or "*"
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            'assistant_id',
            'resource_type',
            'resource_id',
            'principal_type',
            'principal_id',
            name='uq_assistant_share_grant',
        ),
    )


class AssistantShareGrantModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assistant_id: str
    owner_id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str


class AssistantShareGrantsTable:
    async def get_by_owner(self, owner_id: str, db: AsyncSession | None = None) -> list[AssistantShareGrantModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(AssistantShareGrant).filter_by(owner_id=owner_id))
            return [AssistantShareGrantModel.model_validate(row) for row in result.scalars().all()]

    async def record(
        self,
        owner_id: str,
        assistant_id: str,
        resource_type: str,
        resource_id: str,
        principals: set[tuple[str, str]],
        db: AsyncSession | None = None,
    ) -> None:
        if not principals:
            return
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AssistantShareGrant.principal_type, AssistantShareGrant.principal_id).filter_by(
                    assistant_id=assistant_id, resource_type=resource_type, resource_id=resource_id
                )
            )
            recorded = {tuple(row) for row in result.all()}
            for principal_type, principal_id in sorted(principals - recorded):
                db.add(
                    AssistantShareGrant(
                        id=str(uuid.uuid4()),
                        owner_id=owner_id,
                        assistant_id=assistant_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        principal_type=principal_type,
                        principal_id=principal_id,
                        created_at=int(time.time()),
                    )
                )
            await db.commit()

    async def forget(
        self,
        assistant_id: str,
        resource_type: str,
        resource_id: str,
        principals: set[tuple[str, str]],
        db: AsyncSession | None = None,
    ) -> None:
        async with get_async_db_context(db) as db:
            for principal_type, principal_id in principals:
                await db.execute(
                    delete(AssistantShareGrant).filter_by(
                        assistant_id=assistant_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        principal_type=principal_type,
                        principal_id=principal_id,
                    )
                )
            await db.commit()


AssistantShareGrants = AssistantShareGrantsTable()
