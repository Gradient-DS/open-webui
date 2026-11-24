"""Creator authority survives file removal and has a reversible schema migration."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def test_job_creator_migration_round_trip(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'migrations/versions/8a71c296e4bf_record_file_job_creators.py'
    spec = importlib.util.spec_from_file_location('file_job_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        monkeypatch.setattr(module, 'op', Operations(MigrationContext.configure(connection)))
        module.upgrade()
        columns = {column['name']: column for column in inspect(connection).get_columns('soev_file_job')}
        assert columns['creator_user_id']['nullable'] is False
        assert inspect(connection).get_foreign_keys('soev_file_job') == []
        assert inspect(connection).get_indexes('soev_file_job')[0]['column_names'] == ['creator_user_id']
        module.downgrade()
        assert 'soev_file_job' not in inspect(connection).get_table_names()
    engine.dispose()
