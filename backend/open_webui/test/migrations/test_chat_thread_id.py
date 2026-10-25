"""The organisation migration preserves legacy rows without adopting bookmarks."""

import importlib.util
import json
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

PATH = Path(__file__).resolve().parents[2] / 'migrations/versions/a8d4e6b92c10_add_chat_thread_id.py'
spec = importlib.util.spec_from_file_location('chat_thread_id', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.parametrize('dialect', ['sqlite', 'postgres'])
def test_upgrade_and_downgrade(dialect, request, monkeypatch, tmp_path):
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
        with engine.begin() as conn:
            conn.execute(
                sa.text('INSERT INTO chat (id, user_id, chat) VALUES (:id, :user, :chat)'),
                {
                    'id': 'fictional-legacy',
                    'user': 'fictional-fern',
                    'chat': json.dumps(
                        {
                            'history': {
                                'messages': {
                                    'fictional-message': {
                                        'meta': {'agent_v2': {'thread_id': 'fictional-bookmark', 'position': 2}}
                                    }
                                }
                            }
                        }
                    ),
                },
            )
            before = conn.execute(sa.text('SELECT id, user_id, chat FROM chat')).all()
        command.upgrade(config, migration.revision)
        with engine.begin() as conn:
            assert conn.execute(sa.text('SELECT id, user_id, chat FROM chat')).all() == before
            assert conn.execute(sa.text('SELECT thread_id FROM chat')).scalar() is None
            assert conn.execute(sa.text('SELECT COUNT(*) FROM chat_thread')).scalar() == 0
            column = next(c for c in sa.inspect(conn).get_columns('chat') if c['name'] == 'thread_id')
            assert column['nullable']
            conn.execute(sa.text("UPDATE chat SET thread_id = 'fictional-explicit-import'"))
        command.downgrade(config, migration.down_revision)
        with engine.connect() as conn:
            assert 'thread_id' not in {c['name'] for c in sa.inspect(conn).get_columns('chat')}
            assert 'chat_thread' not in sa.inspect(conn).get_table_names()
            assert conn.execute(sa.text('SELECT id, user_id, chat FROM chat')).all() == before
        command.upgrade(config, migration.revision)
    finally:
        engine.dispose()
