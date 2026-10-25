"""Channel retirement preserves other resources and restores only empty schema."""

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = Path(__file__).resolve().parents[2] / 'migrations/versions/f73a9c0e2b61_remove_channels.py'
spec = importlib.util.spec_from_file_location('remove_channels', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture(params=['sqlite', 'postgres'])
def connection(request, monkeypatch):
    if request.param == 'postgres':
        if not os.environ.get('BACKEND_TEST_DATABASE_URL'):
            pytest.skip('BACKEND_TEST_DATABASE_URL is not configured')
        url = request.getfixturevalue('postgres_database')
    else:
        url = 'sqlite://'
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        if request.param == 'sqlite':
            conn.execute(sa.text('PRAGMA foreign_keys=ON'))
        conn.execute(sa.text('CREATE TABLE file (id TEXT PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE chat (id TEXT PRIMARY KEY)'))
        conn.execute(sa.text("INSERT INTO chat VALUES ('fictional-chat')"))
        conn.execute(sa.text('CREATE TABLE access_grant (id TEXT PRIMARY KEY, resource_type TEXT)'))
        conn.execute(sa.text("INSERT INTO access_grant VALUES ('old-channel', 'channel'), ('kept-note', 'note')"))
        config = sa.Table(
            'config', sa.MetaData(), sa.Column('key', sa.Text, primary_key=True), sa.Column('value', sa.JSON)
        )
        config.create(conn)
        conn.execute(
            config.insert(),
            [
                {'key': 'channels.enable', 'value': True},
                {'key': 'channels.model_response_mode', 'value': 'thread'},
                {'key': 'user.permissions', 'value': {'features': {'channels': True, 'notes': True}}},
            ],
        )
        groups = sa.Table(
            'group', sa.MetaData(), sa.Column('id', sa.Text, primary_key=True), sa.Column('permissions', sa.JSON)
        )
        groups.create(conn)
        conn.execute(
            groups.insert(), {'id': 'fictional-group', 'permissions': {'features': {'channels': True, 'notes': False}}}
        )
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
        migration.downgrade()
        yield conn
    engine.dispose()


def shape(conn):
    inspector = sa.inspect(conn)
    return {
        name: {
            'columns': [(c['name'], str(c['type']), c['nullable'], c['default']) for c in inspector.get_columns(name)],
            'pk': inspector.get_pk_constraint(name),
            'unique': sorted(inspector.get_unique_constraints(name), key=lambda c: str(c)),
            'fk': sorted(inspector.get_foreign_keys(name), key=lambda c: str(c)),
            'indexes': sorted(inspector.get_indexes(name), key=lambda i: i['name']),
        }
        for name in migration.TABLES
    }


def test_upgrade_and_downgrade(connection):
    before = shape(connection)
    migration.upgrade()
    assert not set(migration.TABLES) & set(sa.inspect(connection).get_table_names())
    assert connection.execute(sa.text('SELECT * FROM chat')).all() == [('fictional-chat',)]
    assert connection.execute(sa.text('SELECT * FROM access_grant')).all() == [('kept-note', 'note')]
    config = sa.table('config', sa.column('key'), sa.column('value', sa.JSON))
    assert dict(connection.execute(sa.select(config)).all()) == {'user.permissions': {'features': {'notes': True}}}
    groups = sa.table('group', sa.column('permissions', sa.JSON))
    assert connection.execute(sa.select(groups.c.permissions)).scalar() == {'features': {'notes': False}}
    migration.upgrade()
    migration.downgrade()
    assert shape(connection) == before
    for name in migration.TABLES:
        assert connection.execute(sa.text(f'SELECT COUNT(*) FROM {name}')).scalar() == 0
    migration.upgrade()


@pytest.mark.parametrize('table', ['channel', 'message'])
def test_existing_content_stops_before_any_mutation(connection, table):
    if table == 'channel':
        connection.execute(sa.text("INSERT INTO channel (id) VALUES ('fictional-channel')"))
    else:
        connection.execute(sa.text("INSERT INTO message (id) VALUES ('fictional-message')"))
    with pytest.raises(RuntimeError, match='stop and report counts'):
        migration.upgrade()
    assert set(migration.TABLES) <= set(sa.inspect(connection).get_table_names())
    assert connection.execute(sa.text('SELECT COUNT(*) FROM access_grant')).scalar() == 2


@pytest.mark.parametrize('dialect', ['sqlite', 'postgres'])
def test_alembic_chain_restores_the_actual_previous_schema(dialect, request, monkeypatch, tmp_path):
    from alembic import command
    from alembic.config import Config
    from open_webui import env

    if dialect == 'postgres':
        if not os.environ.get('BACKEND_TEST_DATABASE_URL'):
            pytest.skip('BACKEND_TEST_DATABASE_URL is not configured')
        url = request.getfixturevalue('postgres_database').render_as_string(hide_password=False)
    else:
        url = f'sqlite:///{tmp_path}/migration.db'
    monkeypatch.setattr(env, 'DATABASE_URL', url)
    config = Config()
    config.set_main_option('script_location', str(PATH.parents[1]))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, migration.down_revision)
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            previous = shape(conn)
        command.upgrade(config, migration.revision)
        with engine.connect() as conn:
            assert not set(migration.TABLES) & set(sa.inspect(conn).get_table_names())
        command.downgrade(config, migration.down_revision)
        with engine.connect() as conn:
            assert shape(conn) == previous
            for name in migration.TABLES:
                assert conn.execute(sa.text(f'SELECT COUNT(*) FROM {name}')).scalar() == 0
        command.upgrade(config, migration.revision)
    finally:
        engine.dispose()
