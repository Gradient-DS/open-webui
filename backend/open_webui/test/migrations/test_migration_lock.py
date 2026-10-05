import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock

import pytest
from open_webui.internal.migration_lock import MIGRATION_LOCK_ID, migration_lock
from sqlalchemy import create_engine, event, text


def test_sqlite_does_not_open_a_lock_connection(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/test.db')
    connections = []
    event.listen(engine, 'connect', lambda *args: connections.append(True))
    with migration_lock(engine):
        assert connections == []
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE migrated (id INTEGER PRIMARY KEY)'))
    assert len(connections) == 1
    engine.dispose()


@pytest.mark.parametrize('failure_at', [1, 2])
def test_lock_connection_is_discarded_on_acquire_or_release_failure(failure_at):
    engine = MagicMock()
    engine.dialect.name = 'postgresql'
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    connection.execute.side_effect = (
        [RuntimeError('connection lost')] if failure_at == 1 else [None, RuntimeError('connection lost')]
    )
    with pytest.raises(RuntimeError, match='connection lost'), migration_lock(engine):
        pass
    connection.invalidate.assert_called_once()


@pytest.fixture
def postgres_engine():
    url = os.environ.get('MIGRATION_LOCK_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set MIGRATION_LOCK_TEST_DATABASE_URL to an isolated PostgreSQL test database')
    engine = create_engine(url, connect_args={'application_name': 'owui-migration-lock-test'})
    try:
        yield engine
    finally:
        engine.dispose()


def test_postgres_serializes_migrations_across_commits(postgres_engine):
    engine = postgres_engine
    attempted = Event()
    entered = Event()
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE IF EXISTS migration_lock_test'))

    def second_migration():
        attempted.set()
        with migration_lock(engine), engine.begin() as connection:
            entered.set()
            # A concurrent first-install migration must see its predecessor's DDL and data.
            assert connection.execute(text('SELECT revision FROM migration_lock_test')).scalar_one() == 1
            connection.execute(text('UPDATE migration_lock_test SET revision = 2'))

    with ThreadPoolExecutor(max_workers=1) as executor:
        with migration_lock(engine):
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE migration_lock_test (revision INTEGER NOT NULL)'))
                connection.execute(text('INSERT INTO migration_lock_test VALUES (1)'))
            # Committing a migration must not release the session-level lock.
            future = executor.submit(second_migration)
            assert attempted.wait(5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    blocked = connection.execute(
                        text("""
                        SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
                        WHERE l.locktype = 'advisory' AND NOT l.granted
                          AND a.application_name = 'owui-migration-lock-test'
                    """)
                    ).scalar_one()
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked == 1
            assert not entered.is_set()
        future.result(timeout=5)
    with engine.begin() as connection:
        assert connection.execute(text('SELECT revision FROM migration_lock_test')).scalar_one() == 2
        connection.execute(text('DROP TABLE migration_lock_test'))


@pytest.mark.parametrize('fail', [False, True])
def test_postgres_releases_lock_after_success_or_failed_migration(postgres_engine, fail):
    engine = postgres_engine

    def migrate():
        with migration_lock(engine), engine.begin() as connection:
            if fail:
                connection.execute(text('SELECT 1 / 0'))
            else:
                connection.execute(text('SELECT 1'))

    if fail:
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError):
            migrate()
    else:
        migrate()

    # Use an independent physical session: the owner's own session can reenter a lock.
    observer = create_engine(engine.url)
    try:
        with observer.connect() as connection:
            acquired = connection.execute(
                text('SELECT pg_try_advisory_lock(:key)'), {'key': MIGRATION_LOCK_ID}
            ).scalar_one()
            assert acquired
            connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': MIGRATION_LOCK_ID})
    finally:
        observer.dispose()
