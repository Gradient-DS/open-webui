"""The data migration waits for OWUI's schema before reading per-key config."""

from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from open_webui.soev import migrate_state
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
@pytest.mark.parametrize('initial_schema', ['missing', 'old'])
async def test_waits_for_alembic_head_before_reading_config(tmp_path, monkeypatch, capsys, initial_schema):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/schema.db')
    async with engine.begin() as connection:
        if initial_schema == 'old':
            await connection.execute(sa.text('CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)'))
            await connection.execute(sa.text("INSERT INTO alembic_version VALUES ('old-revision')"))
        await connection.execute(sa.text('CREATE TABLE config (id INTEGER PRIMARY KEY, data TEXT)'))
    monkeypatch.setattr(migrate_state, 'schema_heads', lambda: {'ready-head'})

    async def upgrade(seconds):
        async with engine.begin() as connection:
            await connection.execute(
                sa.text('CREATE TABLE IF NOT EXISTS alembic_version (version_num TEXT PRIMARY KEY)')
            )
            await connection.execute(sa.text('DELETE FROM alembic_version'))
            await connection.execute(sa.text("INSERT INTO alembic_version VALUES ('ready-head')"))
            await connection.execute(sa.text('DROP TABLE config'))
            await connection.execute(
                sa.text('CREATE TABLE config (key TEXT PRIMARY KEY, value JSON, updated_at BIGINT)')
            )

    pause = AsyncMock(side_effect=upgrade)
    monkeypatch.setattr(migrate_state, '_pause', pause)
    async with async_sessionmaker(engine)() as db:
        await migrate_state.wait_for_schema(db)
        assert await migrate_state._raw_config(db, ['test.key']) == {}
    pause.assert_awaited_once()
    output = capsys.readouterr().out
    assert 'Waiting for OWUI schema migrations' in output
    assert 'Traceback' not in output
    await engine.dispose()


@pytest.mark.asyncio
async def test_schema_wait_times_out_with_a_clear_migration_error(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/schema.db')
    monkeypatch.setattr(migrate_state, 'schema_heads', lambda: {'ready-head'})
    async with async_sessionmaker(engine)() as db:
        with pytest.raises(migrate_state.MigrationError, match='OWUI schema'):
            await migrate_state.wait_for_schema(db, timeout_seconds=0)
    await engine.dispose()
