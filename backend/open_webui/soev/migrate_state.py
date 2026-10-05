"""Step markers and the config snapshot that --restore puts back byte for byte.

The state lives outside Alembic, in schema owui_v2_migration on PostgreSQL (prefixed
tables on SQLite), so the database never carries a revision the v1 image does not know.
"""

import time
from functools import cache
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.config import Config

SCHEMA = 'owui_v2_migration'


class MigrationError(Exception):
    """The migration cannot proceed; the message says why."""


@cache
def tables(dialect: str) -> SimpleNamespace:
    postgres = dialect == 'postgresql'
    metadata = sa.MetaData(schema=SCHEMA if postgres else None)
    prefix = '' if postgres else f'{SCHEMA}_'
    text, number = sa.Text, sa.BigInteger
    return SimpleNamespace(
        metadata=metadata,
        # value is the raw JSON text of the config row, NULL when the key had no row.
        config_backup=sa.Table(
            f'{prefix}config_backup',
            metadata,
            sa.Column('migration_id', text, primary_key=True),
            sa.Column('key', text, primary_key=True),
            sa.Column('value', text, nullable=True),
            sa.Column('updated_at', number, nullable=True),
            sa.Column('created_at', number, nullable=False),
        ),
        # path is a JSON list of keys inside the row's field.
        model_id_backup=sa.Table(
            f'{prefix}model_id_backup',
            metadata,
            sa.Column('migration_id', text, primary_key=True),
            sa.Column('site', text, primary_key=True),
            sa.Column('row_id', text, primary_key=True),
            sa.Column('path', text, primary_key=True),
            sa.Column('old_value', text, nullable=False),
            sa.Column('new_value', text, nullable=False),
            sa.Column('created_at', number, nullable=False),
        ),
        marker=sa.Table(
            f'{prefix}migration_marker',
            metadata,
            sa.Column('migration_id', text, primary_key=True),
            sa.Column('step', text, primary_key=True),
            sa.Column('created_at', number, nullable=False),
        ),
    )


async def state(db: AsyncSession) -> SimpleNamespace:
    return tables((await db.connection()).dialect.name)


async def ensure_tables(db: AsyncSession) -> None:
    """Create the schema and tables if missing; idempotent."""
    connection = await db.connection()
    if connection.dialect.name == 'postgresql':
        await connection.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}'))
    await connection.run_sync(tables(connection.dialect.name).metadata.create_all)
    await db.commit()


async def tables_exist(db: AsyncSession) -> bool:
    connection = await db.connection()
    marker = tables(connection.dialect.name).marker
    return await connection.run_sync(lambda sync: sa.inspect(sync).has_table(marker.name, schema=marker.schema))


async def has_marker(db: AsyncSession, migration_id: str, step: str) -> bool:
    marker = (await state(db)).marker
    query = sa.select(marker.c.step).where(marker.c.migration_id == migration_id, marker.c.step == step)
    return (await db.execute(query)).first() is not None


async def add_marker(db: AsyncSession, migration_id: str, step: str) -> None:
    marker = (await state(db)).marker
    await db.execute(sa.insert(marker).values(migration_id=migration_id, step=step, created_at=int(time.time())))


async def drop_marker(db: AsyncSession, migration_id: str, step: str) -> None:
    marker = (await state(db)).marker
    await db.execute(sa.delete(marker).where(marker.c.migration_id == migration_id, marker.c.step == step))


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
    backup = (await state(db)).config_backup
    for key in sorted(keys):
        value, updated_at = rows.get(key, (None, None))
        await db.execute(
            sa.insert(backup).values(
                migration_id=migration_id, key=key, value=value, updated_at=updated_at, created_at=now
            )
        )
    await add_marker(db, migration_id, 'snapshot')
    await db.commit()
    return True


async def snapshot_rows(db: AsyncSession, migration_id: str) -> list:
    backup = (await state(db)).config_backup
    result = await db.execute(sa.select(backup).where(backup.c.migration_id == migration_id).order_by(backup.c.key))
    return list(result.all())


async def restore_config(db: AsyncSession, migration_id: str) -> int:
    """Put every snapshotted row back as stored, deleting keys that had no row."""
    if not await tables_exist(db) or not await has_marker(db, migration_id, 'snapshot'):
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
