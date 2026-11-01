"""Conversation read watermarks need precision without changing legacy chat storage."""

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_read_state_upgrade_and_downgrade_preserve_legacy_reads(monkeypatch):
    migration = importlib.import_module('open_webui.migrations.versions.6d9a2c4e8b10_add_thread_read_state')
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE user (id TEXT PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE chat (last_read_at BIGINT)'))
        conn.execute(sa.text('INSERT INTO chat VALUES (1792922400)'))
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        assert {column['name'] for column in sa.inspect(conn).get_columns('thread_read')} == {
            'user_id',
            'conversation_id',
            'last_read_at',
        }
        conn.execute(sa.text('INSERT INTO user VALUES (:user)'), {'user': 'reader'})
        conn.execute(
            sa.text('INSERT INTO thread_read VALUES (:user, :conversation, :read)'),
            {'user': 'reader', 'conversation': 'conversation', 'read': 1792922400.125},
        )
        assert conn.execute(sa.text('SELECT last_read_at FROM thread_read')).scalar() == 1792922400.125
        migration.downgrade()
        assert 'thread_read' not in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.text('SELECT last_read_at FROM chat')).scalar() == 1792922400
    engine.dispose()
