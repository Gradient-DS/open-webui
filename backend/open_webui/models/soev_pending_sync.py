import time

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, CheckConstraint, Column, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession


class SoevPendingSync(Base):
    __tablename__ = 'soev_pending_sync'

    owner_id = Column(Text, primary_key=True)
    kind = Column(Text, primary_key=True)
    resource_id = Column(Text, primary_key=True)
    created_at = Column(BigInteger, nullable=False)
    __table_args__ = (CheckConstraint("kind IN ('folder_files', 'assistant_files')", name='ck_soev_pending_sync_kind'),)


class PendingSyncModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    owner_id: str
    kind: str
    resource_id: str
    created_at: int


class PendingSyncsTable:
    async def get_by_owner(self, owner_id: str, db: AsyncSession | None = None) -> list[PendingSyncModel]:
        async with get_async_db_context(db) as db:
            rows = await db.execute(select(SoevPendingSync).filter_by(owner_id=owner_id))
            return [PendingSyncModel.model_validate(row) for row in rows.scalars()]

    async def mark(self, owner_id: str, kind: str, resource_id: str, db: AsyncSession | None = None) -> None:
        from open_webui.soev.acting import require_owner

        require_owner(owner_id)
        async with get_async_db_context(db) as db:
            # Refresh the timestamp so an older settlement cannot erase newly queued work.
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
            await db.execute(
                insert(SoevPendingSync)
                .values(owner_id=owner_id, kind=kind, resource_id=resource_id, created_at=time.time_ns())
                .on_conflict_do_update(
                    index_elements=['owner_id', 'kind', 'resource_id'], set_={'created_at': time.time_ns()}
                )
            )
            await db.commit()

    async def forget(self, marker: PendingSyncModel, db: AsyncSession | None = None) -> None:
        from open_webui.soev.acting import require_owner

        require_owner(marker.owner_id)
        async with get_async_db_context(db) as db:
            await db.execute(delete(SoevPendingSync).filter_by(**marker.model_dump()))
            await db.commit()


PendingSyncs = PendingSyncsTable()
