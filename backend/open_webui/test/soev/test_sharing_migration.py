import importlib
import os

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def exercise_migration(url):
    migration = importlib.import_module(
        'open_webui.migrations.versions.c4d8e2f671a9_add_assistant_sharing_and_pending_sync'
    )
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE sample_existing (id TEXT PRIMARY KEY, name TEXT)'))
        connection.execute(sa.text("INSERT INTO sample_existing VALUES ('fern', 'Fable project')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            connection.execute(
                sa.text("""INSERT INTO assistant_share_grant VALUES (
                'grant', 'fern', 'helper', 'knowledge', 'local', 'user', 'lark', 1)""")
            )
            connection.execute(sa.text("INSERT INTO soev_pending_sync VALUES ('fern', 'assistant_files', 'helper', 1)"))
            migration.downgrade()
        assert connection.execute(sa.text('SELECT * FROM sample_existing')).one() == ('fern', 'Fable project')
        assert 'assistant_share_grant' not in sa.inspect(connection).get_table_names()
        assert 'soev_pending_sync' not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_sharing_migration_preserves_existing_rows_and_downgrades():
    exercise_migration('sqlite:///:memory:')


@pytest.mark.skipif(not os.environ.get('BACKEND_TEST_DATABASE_URL'), reason='PostgreSQL not configured')
def test_sharing_migration_preserves_existing_postgres_rows_and_downgrades(postgres_database):
    exercise_migration(postgres_database)


@pytest.mark.asyncio
async def test_assistant_ledger_records_once_per_assistant_and_forgets_only_its_source():
    from open_webui.models.assistant_share_grants import AssistantShareGrant, AssistantShareGrants
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(AssistantShareGrant.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        for assistant in ('helper', 'helper', 'guide'):
            await AssistantShareGrants.record('fern', assistant, 'note', 'memo', {('user', 'lark')}, db=db)
        assert len(await AssistantShareGrants.get_by_owner('fern', db=db)) == 2
        await AssistantShareGrants.forget('helper', 'note', 'memo', {('user', 'lark')}, db=db)
        rows = await AssistantShareGrants.get_by_owner('fern', db=db)
        assert [(row.assistant_id, row.principal_id) for row in rows] == [('guide', 'lark')]
    await engine.dispose()
