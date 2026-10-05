"""Step markers and the config snapshot that --restore puts back byte for byte."""

import time

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.config import Config
from open_webui.models.soev_migration import ConfigBackup, MigrationMarker


class MigrationError(Exception):
    """The migration cannot proceed; the message says why."""


async def has_marker(db: AsyncSession, migration_id: str, step: str) -> bool:
    return await db.get(MigrationMarker, (migration_id, step)) is not None


def add_marker(db: AsyncSession, migration_id: str, step: str) -> None:
    db.add(MigrationMarker(migration_id=migration_id, step=step, created_at=int(time.time())))


async def drop_marker(db: AsyncSession, migration_id: str, step: str) -> None:
    await db.execute(
        sa.delete(MigrationMarker).where(MigrationMarker.migration_id == migration_id, MigrationMarker.step == step)
    )


async def _raw_config(db: AsyncSession, keys) -> dict[str, tuple[str, int | None]]:
    # Raw text, not the decoded value, so a restore rewrites the exact stored bytes.
    result = await db.execute(
        sa.select(Config.key, sa.cast(Config.value, sa.Text), Config.updated_at).where(Config.key.in_(list(keys)))
    )
    return {key: (value, updated_at) for key, value, updated_at in result.all()}


async def snapshot(db: AsyncSession, migration_id: str, keys) -> bool:
    """Record the keys' rows once per migration id, absent rows as NULL; True when taken now."""
    if await has_marker(db, migration_id, 'snapshot'):
        return False
    rows = await _raw_config(db, keys)
    now = int(time.time())
    for key in sorted(keys):
        value, updated_at = rows.get(key, (None, None))
        db.add(ConfigBackup(migration_id=migration_id, key=key, value=value, updated_at=updated_at, created_at=now))
    add_marker(db, migration_id, 'snapshot')
    await db.commit()
    return True


async def snapshot_rows(db: AsyncSession, migration_id: str) -> list[ConfigBackup]:
    result = await db.execute(
        sa.select(ConfigBackup).where(ConfigBackup.migration_id == migration_id).order_by(ConfigBackup.key)
    )
    return list(result.scalars().all())


async def restore_config(db: AsyncSession, migration_id: str) -> int:
    """Put every snapshotted row back as stored, deleting keys that had no row."""
    if not await has_marker(db, migration_id, 'snapshot'):
        raise MigrationError(f'No config snapshot for migration {migration_id}')
    table = Config.__table__
    postgres = (await db.connection()).dialect.name == 'postgresql'
    rows = await snapshot_rows(db, migration_id)
    for row in rows:
        if row.value is None:
            await db.execute(sa.delete(table).where(table.c.key == row.key))
            continue
        raw = sa.literal(row.value, sa.Text)
        # SQLite would give CAST(... AS JSON) numeric affinity; there the column is plain text.
        value = sa.cast(raw, sa.JSON) if postgres else raw
        result = await db.execute(
            sa.update(table).where(table.c.key == row.key).values(value=value, updated_at=row.updated_at)
        )
        if result.rowcount == 0:
            await db.execute(sa.insert(table).values(key=row.key, value=value, updated_at=row.updated_at))
    await db.commit()
    return len(rows)
