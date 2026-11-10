"""Worker state moves atomically without changing product settings or receipt contents."""

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import JSON, Column, MetaData, Table, Text, create_engine, select


def test_worker_state_migration_preserves_receipts_and_settings():
    migration = importlib.import_module('open_webui.migrations.versions.73c9a4d82e16_separate_runtime_state')
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        metadata = MetaData()
        config = Table('config', metadata, Column('key', Text, primary_key=True), Column('value', JSON, nullable=False))
        metadata.create_all(connection)
        workers = {
            'soev.retention.fictional': {'cursor': 'fictional-thread', 'deleted': ['fictional-deleted']},
            'soev.offboarding.fictional': {'state': 'prepared', 'digest': 'fictional-digest'},
            'soev.offboarding.job.fictional': {'digest': 'fictional-job-digest'},
        }
        settings = {'models.system_prompt': 'Fictional tenant prompt'}
        connection.execute(
            config.insert(), [{'key': key, 'value': value} for key, value in (workers | settings).items()]
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        state = Table('runtime_state', MetaData(), autoload_with=connection)
        assert dict(connection.execute(select(state.c.key, state.c.value)).all()) == workers
        assert dict(connection.execute(select(config.c.key, config.c.value)).all()) == settings
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert dict(connection.execute(select(config.c.key, config.c.value)).all()) == workers | settings
    engine.dispose()
