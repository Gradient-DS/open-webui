"""Test for migration a1c2e3f4d5b6 — backfill knowledge_file path columns from file.meta.

Uses an in-memory SQLite database to exercise the real ``_backfill()`` function
from the migration module, so a bug in its ``sa.select``/``sa.update`` join path
will fail these tests.
"""

import importlib.util
import json
import time
from pathlib import Path

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
    / 'a1c2e3f4d5b6_backfill_knowledge_file_path_columns.py'
)

_spec = importlib.util.spec_from_file_location(
    'a1c2e3f4d5b6_backfill_knowledge_file_path_columns',
    _MIGRATION_PATH,
)
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

_backfill = _migration._backfill


# ---------------------------------------------------------------------------
# Minimal schemas (only the columns the migration touches / reads)
# ---------------------------------------------------------------------------

_DDL_FILE = """
CREATE TABLE IF NOT EXISTS file (
    id          TEXT PRIMARY KEY,
    meta        TEXT
)
"""

_DDL_KF = """
CREATE TABLE IF NOT EXISTS knowledge_file (
    id               TEXT PRIMARY KEY,
    knowledge_id     TEXT,
    file_id          TEXT,
    user_id          TEXT,
    relative_path    TEXT,
    source_item_id   TEXT,
    created_at       INTEGER,
    updated_at       INTEGER
)
"""


def _setup_engine():
    engine = create_engine('sqlite://', echo=False)
    with engine.connect() as conn:
        conn.execute(text(_DDL_FILE))
        conn.execute(text(_DDL_KF))
        conn.commit()
    return engine


def _insert_file(conn, file_id, meta):
    conn.execute(
        text('INSERT INTO file (id, meta) VALUES (:id, :meta)'),
        {'id': file_id, 'meta': json.dumps(meta) if meta is not None else None},
    )


def _insert_link(conn, link_id, knowledge_id, file_id):
    now = int(time.time())
    conn.execute(
        text(
            'INSERT INTO knowledge_file (id, knowledge_id, file_id, user_id, created_at, updated_at) '
            "VALUES (:id, :kid, :fid, 'u1', :ts, :ts)"
        ),
        {'id': link_id, 'kid': knowledge_id, 'fid': file_id, 'ts': now},
    )


def _read_link(conn, link_id):
    row = conn.execute(
        text('SELECT relative_path, source_item_id FROM knowledge_file WHERE id = :id'),
        {'id': link_id},
    ).fetchone()
    return (row[0], row[1])


def _run_upgrade(engine):
    """Call the migration's real ``_backfill`` with a SQLite-aware JSON type.

    SQLite stores JSON columns as TEXT and returns the raw string, so the
    migration (which declares ``meta`` as ``sa.JSON()``) would see a string and
    ``_path_fields_from_meta`` would return ``(None, None)``. We swap in a
    TypeDecorator that json.loads on read — matching what the migration sees on
    PostgreSQL — for the duration of the call.
    """

    class _SQLiteJSON(sa.TypeDecorator):
        impl = sa.Text
        cache_ok = True

        def process_result_value(self, value, dialect):
            if value is None:
                return None
            if isinstance(value, (dict, list)):
                return value
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value

        def process_bind_param(self, value, dialect):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            return json.dumps(value)

    _file_sqlite = sa.table(
        'file',
        sa.column('id', sa.String()),
        sa.column('meta', _SQLiteJSON()),
    )

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


class TestBackfillKnowledgeFilePathColumns:
    def test_backfill_copies_both_path_fields(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(conn, 'f1', {'relative_path': 'a/b/c.pdf', 'source_item_id': 'src-1'})
            _insert_link(conn, 'kf1', 'kb1', 'f1')
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            rp, sid = _read_link(conn, 'kf1')
        assert rp == 'a/b/c.pdf'
        assert sid == 'src-1'

    def test_loose_file_link_stays_null(self):
        """A link whose file has no path fields is left at NULL (not written)."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(conn, 'f2', {'name': 'loose.txt', 'content_type': 'text/plain'})
            _insert_link(conn, 'kf2', 'kb1', 'f2')
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            rp, sid = _read_link(conn, 'kf2')
        assert rp is None
        assert sid is None

    def test_null_meta_link_stays_null(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(conn, 'f3', None)
            _insert_link(conn, 'kf3', 'kb1', 'f3')
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            rp, sid = _read_link(conn, 'kf3')
        assert rp is None
        assert sid is None

    def test_partial_meta_only_relative_path(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(conn, 'f4', {'relative_path': 'just/a/path.pdf'})
            _insert_link(conn, 'kf4', 'kb1', 'f4')
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            rp, sid = _read_link(conn, 'kf4')
        assert rp == 'just/a/path.pdf'
        assert sid is None

    def test_multiple_links_same_file(self):
        """The same file linked into two KBs → both links backfilled."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(conn, 'f5', {'relative_path': 'shared/x.pdf', 'source_item_id': 'src-5'})
            _insert_link(conn, 'kf5a', 'kbA', 'f5')
            _insert_link(conn, 'kf5b', 'kbB', 'f5')
            conn.commit()

        _run_upgrade(engine)

        with engine.connect() as conn:
            assert _read_link(conn, 'kf5a') == ('shared/x.pdf', 'src-5')
            assert _read_link(conn, 'kf5b') == ('shared/x.pdf', 'src-5')

    def test_idempotent_re_run(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_file(conn, 'f6', {'relative_path': 'e/f.pdf', 'source_item_id': 'src-6'})
            _insert_link(conn, 'kf6', 'kb1', 'f6')
            conn.commit()

        _run_upgrade(engine)
        _run_upgrade(engine)

        with engine.connect() as conn:
            assert _read_link(conn, 'kf6') == ('e/f.pdf', 'src-6')
