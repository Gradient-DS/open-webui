"""Test for migration d651b063d0d8 — backfill file.meta status/error from file.data.

Uses an in-memory SQLite database to exercise the real ``_backfill()`` function
from the migration module, so a bug in the migration's ``sa.select``/``sa.update``
path will cause these tests to fail.
"""

import importlib.util
import json
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Import _backfill from the migration module (filename starts with a digit,
# so normal import machinery cannot be used — importlib.util is required).
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / 'd651b063d0d8_backfill_file_meta_status_from_data.py'
)

_spec = importlib.util.spec_from_file_location(
    'd651b063d0d8_backfill_file_meta_status_from_data',
    _MIGRATION_PATH,
)
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

_backfill = _migration._backfill


# ---------------------------------------------------------------------------
# Minimal file table schema (mirrors the real schema for the columns we use)
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS file (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    hash        TEXT,
    filename    TEXT,
    path        TEXT,
    data        TEXT,
    meta        TEXT,
    created_at  INTEGER,
    updated_at  INTEGER
)
"""


def _setup_engine():
    """Create an in-memory SQLite engine with the file table."""
    engine = create_engine('sqlite://', echo=False)
    with engine.connect() as conn:
        conn.execute(text(_DDL))
        conn.commit()
    return engine


def _insert_file(conn, file_id, data, meta):
    conn.execute(
        text(
            'INSERT INTO file (id, user_id, filename, data, meta, created_at, updated_at) '
            "VALUES (:id, 'u1', 'f.txt', :data, :meta, :ts, :ts)"
        ),
        {
            'id': file_id,
            'data': json.dumps(data) if data is not None else None,
            'meta': json.dumps(meta) if meta is not None else None,
            'ts': int(time.time()),
        },
    )


def _read_row(conn, file_id):
    row = conn.execute(text('SELECT data, meta FROM file WHERE id = :id'), {'id': file_id}).fetchone()
    return (
        json.loads(row[0]) if row[0] else None,
        json.loads(row[1]) if row[1] else None,
    )


# ---------------------------------------------------------------------------
# Run the real migration _backfill against the in-memory DB
# ---------------------------------------------------------------------------


def _run_upgrade(engine):
    """Call the migration's real ``_backfill`` function against a plain engine.

    SQLite stores JSON columns as TEXT; ``sa.JSON()`` returns the raw string on
    SQLite (no auto-deserialisation).  The migration handles this transparently
    because its Python-side merge logic receives whatever the dialect returns —
    on SQLite the ``isinstance(data, dict)`` guard is False for raw strings, so
    the row is skipped.

    To make the test exercise the full path we pre-insert values as TEXT
    (json.dumps) and rely on the fact that SQLite returns them as strings.
    The migration skips rows where ``data`` is not a dict — that is the
    correct behaviour on SQLite without a JSON type adapter.

    To exercise the update path we use a JSON-aware engine wrapper that
    deserialises TEXT columns via SQLAlchemy's TypeDecorator, matching the
    behaviour the migration sees on PostgreSQL.
    """
    # Use a TypeDecorator-aware column override: re-declare the table with
    # a custom JSON type that round-trips through json.loads on SQLite.
    import json as _json

    class _SQLiteJSON(sa.TypeDecorator):
        impl = sa.Text
        cache_ok = True

        def process_result_value(self, value, dialect):
            if value is None:
                return None
            if isinstance(value, (dict, list)):
                return value
            try:
                return _json.loads(value)
            except (TypeError, ValueError):
                return value

        def process_bind_param(self, value, dialect):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            return _json.dumps(value)

    # Re-create the lightweight table reference the migration uses, but with
    # our SQLite-aware JSON type so the select/update path sees Python dicts.
    _file_sqlite = sa.table(
        'file',
        sa.column('id', sa.String()),
        sa.column('data', _SQLiteJSON()),
        sa.column('meta', _SQLiteJSON()),
    )

    # Monkey-patch the migration module's _file table reference temporarily so
    # _backfill uses our SQLite-aware type for the duration of this call.
    original_file = _migration._file
    _migration._file = _file_sqlite
    try:
        with engine.connect() as conn:
            _backfill(conn)
            conn.commit()
    finally:
        _migration._file = original_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackfillFileMetaStatus:
    """Tests for migration d651b063d0d8."""

    def test_backfill_copies_status_and_preserves_existing_meta_keys(self):
        """Core requirement: meta.status is backfilled AND existing meta keys survive."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(
                conn,
                file_id='f1',
                data={'status': 'completed', 'content': 'x'},
                meta={'relative_path': 'a/b'},
            )
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            data, meta = _read_row(conn, 'f1')

        # meta.status populated from data.status
        assert meta['status'] == 'completed', f"Expected 'completed', got {meta.get('status')!r}"
        # Existing meta key preserved
        assert meta['relative_path'] == 'a/b', f'relative_path clobbered: {meta!r}'
        # data unchanged
        assert data == {'status': 'completed', 'content': 'x'}, f'data modified: {data!r}'

    def test_error_key_backfilled_when_present_in_data(self):
        """meta.error is copied when data contains an error key."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(
                conn,
                file_id='f2',
                data={'status': 'error', 'error': 'parse failed'},
                meta={'relative_path': 'c/d'},
            )
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            data, meta = _read_row(conn, 'f2')

        assert meta['status'] == 'error'
        assert meta['error'] == 'parse failed'
        assert meta['relative_path'] == 'c/d'
        assert data == {'status': 'error', 'error': 'parse failed'}

    def test_rows_without_status_in_data_are_skipped(self):
        """Rows with no data.status (e.g. chat uploads) are left untouched."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(
                conn,
                file_id='f3',
                data={'content': 'hello'},
                meta={'foo': 'bar'},
            )
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            data, meta = _read_row(conn, 'f3')

        assert 'status' not in meta, f'Unexpected status in meta: {meta!r}'
        assert meta == {'foo': 'bar'}
        assert data == {'content': 'hello'}

    def test_null_data_row_is_skipped(self):
        """Rows with NULL data are safely skipped."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(conn, file_id='f4', data=None, meta={'k': 'v'})
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            data, meta = _read_row(conn, 'f4')

        assert data is None
        assert meta == {'k': 'v'}

    def test_null_meta_is_treated_as_empty_dict(self):
        """Rows with NULL meta get a fresh meta dict containing only status."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(
                conn,
                file_id='f5',
                data={'status': 'pending'},
                meta=None,
            )
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            data, meta = _read_row(conn, 'f5')

        assert meta is not None
        assert meta['status'] == 'pending'

    def test_idempotent_re_run(self):
        """Running the migration twice produces the same result."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(
                conn,
                file_id='f6',
                data={'status': 'synced', 'content': 'y'},
                meta={'relative_path': 'e/f'},
            )
            conn.commit()

        _run_upgrade(engine)
        _run_upgrade(engine)  # second run

        with engine.connect() as conn:
            data, meta = _read_row(conn, 'f6')

        assert meta['status'] == 'synced'
        assert meta['relative_path'] == 'e/f'
        assert data == {'status': 'synced', 'content': 'y'}
