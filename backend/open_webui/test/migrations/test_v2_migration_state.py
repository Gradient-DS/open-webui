"""Create and drop the v2 migration state tables against real SQLite DDL."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = Path(__file__).resolve().parents[2] / 'migrations/versions/b02f3fd91aba_add_v2_migration_state.py'
spec = importlib.util.spec_from_file_location('v2_migration_state', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
TABLES = {'config_backup', 'model_id_backup', 'migration_marker'}


def test_upgrade_reruns_and_downgrade(monkeypatch):
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
        for _ in range(2):
            migration.upgrade()
            assert TABLES <= set(sa.inspect(conn).get_table_names())
        columns = {c['name'] for c in sa.inspect(conn).get_columns('config_backup')}
        assert columns == {'migration_id', 'key', 'value', 'updated_at', 'created_at'}
        migration.downgrade()
        assert not TABLES & set(sa.inspect(conn).get_table_names())
    engine.dispose()
