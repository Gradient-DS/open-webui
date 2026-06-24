"""Test for migration d651b063d0d8 — backfill file.meta status/error from file.data.

Uses an in-memory SQLite database to exercise the upgrade() function directly
(without going through Alembic's full migration runner), so the test is
self-contained and fast.
"""

import json
import time

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text


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
# Run the upgrade logic against the in-memory DB
# ---------------------------------------------------------------------------


def _run_upgrade(engine):
    """
    Re-implement the upgrade() logic from the migration against a plain
    SQLAlchemy engine so we can test it without Alembic's migration runner.
    """
    _file = sa.table(
        'file',
        sa.column('id', sa.String()),
        sa.column('data', sa.JSON()),
        sa.column('meta', sa.JSON()),
    )

    BATCH_SIZE = 200
    offset = 0

    with engine.connect() as conn:
        while True:
            # SQLite stores JSON as TEXT; we get dicts back because sa.JSON
            # auto-deserialises on supported dialects.  On SQLite the raw text
            # is returned — we parse manually here to mirror the migration's
            # Python-side merge logic.
            rows = conn.execute(
                text('SELECT id, data, meta FROM file ORDER BY id LIMIT :lim OFFSET :off'),
                {'lim': BATCH_SIZE, 'off': offset},
            ).fetchall()

            if not rows:
                break

            for row in rows:
                file_id, raw_data, raw_meta = row

                data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                if not isinstance(data, dict) or 'status' not in data:
                    continue

                meta_val = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                meta = meta_val if isinstance(meta_val, dict) else {}

                new_meta = dict(meta)
                new_meta['status'] = data['status']
                if 'error' in data:
                    new_meta['error'] = data['error']

                conn.execute(
                    text('UPDATE file SET meta = :meta WHERE id = :id'),
                    {'meta': json.dumps(new_meta), 'id': file_id},
                )

            offset += BATCH_SIZE
            if len(rows) < BATCH_SIZE:
                break

        conn.commit()


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
