"""Run the agent_config drop against SQLite."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = Path(__file__).resolve().parents[2] / 'migrations/versions/ee54ed095346_drop_agent_config_table.py'
spec = importlib.util.spec_from_file_location('drop_agent_config', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def conn(monkeypatch):
    """Supply an agent_config table with grants next to an unrelated grant."""
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE agent_config (id TEXT PRIMARY KEY, name TEXT)'))
        conn.execute(sa.text("INSERT INTO agent_config VALUES ('a', 'Agent')"))
        conn.execute(sa.text('CREATE TABLE access_grant (id TEXT PRIMARY KEY, resource_type TEXT, resource_id TEXT)'))
        conn.execute(sa.text("INSERT INTO access_grant VALUES ('1', 'agent_config', 'a'), ('2', 'model', 'm')"))
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
        yield conn
    engine.dispose()


def test_upgrade_drops_the_table_and_its_grants_and_reruns(conn):
    """Only agent_config grants go; a rerun is a no-op."""
    for _ in range(2):
        migration.upgrade()
        assert 'agent_config' not in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.text('SELECT id FROM access_grant')).all() == [('2',)]


def test_downgrade_recreates_the_empty_table(conn):
    """Downgrade restores the last v1 shape without data."""
    migration.upgrade()
    for _ in range(2):
        migration.downgrade()
    columns = [c['name'] for c in sa.inspect(conn).get_columns('agent_config')]
    assert columns == [
        'id',
        'user_id',
        'name',
        'description',
        'profile_image_url',
        'is_active',
        'is_beta',
        'meta',
        'created_at',
        'updated_at',
        'position',
    ]
    assert conn.execute(sa.text('SELECT COUNT(*) FROM agent_config')).scalar() == 0
