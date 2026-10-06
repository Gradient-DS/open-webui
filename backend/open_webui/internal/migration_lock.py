from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

# Stable across releases and processes; PostgreSQL advisory locks are database-local.
MIGRATION_LOCK_ID = 0x4F5755494D494752


@contextmanager
def migration_lock(engine: Engine) -> Iterator[None]:
    if engine.dialect.name != 'postgresql':
        yield
        return

    # A separate session keeps the lock across migration commits and rollbacks.
    # AUTOCOMMIT avoids holding an idle transaction while another pod migrates.
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        try:
            connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': MIGRATION_LOCK_ID})
            try:
                yield
            finally:
                connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': MIGRATION_LOCK_ID})
        except BaseException:
            # Never return a possibly still-locked session to a connection pool.
            connection.invalidate()
            raise
