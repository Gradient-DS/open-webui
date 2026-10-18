"""Test for migration e2c7a94b1f38 — materialize knowledge_directory chains
from the knowledge_file path columns (P2-8 Phase 1 backfill).

Uses an in-memory SQLite database to exercise the real ``_materialize()``
function from the migration module. Covers the plan's backfill shapes:
nested paths, multi-source KBs, file-type sources, loose files, and
idempotent re-runs.
"""

import importlib.util
import json
import time
from pathlib import Path

from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Import _materialize from the migration module (filename starts with a digit,
# so normal import machinery cannot be used — importlib.util is required).
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / 'e2c7a94b1f38_materialize_knowledge_directories.py'
)

_spec = importlib.util.spec_from_file_location('e2c7a94b1f38_materialize_knowledge_directories', _MIGRATION_PATH)
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

_materialize = _migration._materialize


# ---------------------------------------------------------------------------
# Minimal schemas (only the columns the migration touches / reads)
# ---------------------------------------------------------------------------

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS knowledge (
        id       TEXT PRIMARY KEY,
        user_id  TEXT,
        meta     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_directory (
        id           TEXT PRIMARY KEY,
        knowledge_id TEXT,
        parent_id    TEXT,
        name         TEXT,
        user_id      TEXT,
        created_at   INTEGER,
        updated_at   INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_file (
        id               TEXT PRIMARY KEY,
        knowledge_id     TEXT,
        file_id          TEXT,
        user_id          TEXT,
        directory_id     TEXT,
        relative_path    TEXT,
        source_item_id   TEXT,
        created_at       INTEGER,
        updated_at       INTEGER
    )
    """,
]


def _setup_engine():
    engine = create_engine('sqlite://', echo=False)
    with engine.connect() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
        conn.commit()
    return engine


def _insert_kb(conn, kb_id, meta, user_id='owner-1'):
    conn.execute(
        text('INSERT INTO knowledge (id, user_id, meta) VALUES (:id, :uid, :meta)'),
        {'id': kb_id, 'uid': user_id, 'meta': json.dumps(meta) if meta is not None else None},
    )


def _insert_link(conn, link_id, kb_id, relative_path=None, source_item_id=None, directory_id=None, user_id='u1'):
    now = int(time.time())
    conn.execute(
        text(
            'INSERT INTO knowledge_file '
            '(id, knowledge_id, file_id, user_id, directory_id, relative_path, source_item_id, created_at, updated_at) '
            'VALUES (:id, :kid, :fid, :uid, :did, :rp, :sid, :ts, :ts)'
        ),
        {
            'id': link_id,
            'kid': kb_id,
            'fid': f'file-of-{link_id}',
            'uid': user_id,
            'did': directory_id,
            'rp': relative_path,
            'sid': source_item_id,
            'ts': now,
        },
    )


def _run(engine):
    with engine.connect() as conn:
        _materialize(conn)
        conn.commit()


def _directories(conn, kb_id):
    rows = conn.execute(
        text('SELECT id, parent_id, name FROM knowledge_directory WHERE knowledge_id = :kid'),
        {'kid': kb_id},
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _link_directory(conn, link_id):
    return conn.execute(text('SELECT directory_id FROM knowledge_file WHERE id = :id'), {'id': link_id}).fetchone()[0]


def _dir_path(conn, directory_id):
    segments = []
    current = directory_id
    while current:
        row = conn.execute(
            text('SELECT parent_id, name FROM knowledge_directory WHERE id = :id'), {'id': current}
        ).fetchone()
        if row is None:
            break
        segments.insert(0, row[1])
        current = row[0]
    return '/'.join(segments)


def _kb_meta(conn, kb_id):
    raw = conn.execute(text('SELECT meta FROM knowledge WHERE id = :id'), {'id': kb_id}).fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


_FOLDER_META = {'google_drive_sync': {'sources': [{'item_id': 'S1', 'name': 'Alpha Folder', 'type': 'folder'}]}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMaterializeKnowledgeDirectories:
    def test_nested_paths_build_one_chain(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', _FOLDER_META)
            _insert_link(conn, 'kf-root', 'kb1', relative_path='root.pdf', source_item_id='S1')
            _insert_link(conn, 'kf-a', 'kb1', relative_path='docs/a.pdf', source_item_id='S1')
            _insert_link(conn, 'kf-c', 'kb1', relative_path='docs/sub/c.pdf', source_item_id='S1')
            conn.commit()

        _run(engine)

        with engine.connect() as conn:
            directories = _directories(conn, 'kb1')
            # Alpha Folder / docs / sub — each exactly once.
            assert sorted(name for _, name in directories.values()) == ['Alpha Folder', 'docs', 'sub']
            assert _dir_path(conn, _link_directory(conn, 'kf-root')) == 'Alpha Folder'
            assert _dir_path(conn, _link_directory(conn, 'kf-a')) == 'Alpha Folder/docs'
            assert _dir_path(conn, _link_directory(conn, 'kf-c')) == 'Alpha Folder/docs/sub'

            # root_directory_id stamped on the registry entry.
            meta = _kb_meta(conn, 'kb1')
            root_id = next(did for did, (parent, name) in directories.items() if parent is None)
            assert meta['google_drive_sync']['sources'][0]['root_directory_id'] == root_id

    def test_multi_source_kb_gets_one_root_per_folder_source(self):
        engine = _setup_engine()
        meta = {
            'onedrive_sync': {
                'sources': [
                    {'item_id': 'S1', 'name': 'Alpha', 'type': 'folder'},
                    {'item_id': 'S2', 'name': 'Beta', 'type': 'folder'},
                ]
            }
        }
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', meta)
            _insert_link(conn, 'kf-1', 'kb1', relative_path='a.pdf', source_item_id='S1')
            _insert_link(conn, 'kf-2', 'kb1', relative_path='x/b.pdf', source_item_id='S2')
            conn.commit()

        _run(engine)

        with engine.connect() as conn:
            assert _dir_path(conn, _link_directory(conn, 'kf-1')) == 'Alpha'
            assert _dir_path(conn, _link_directory(conn, 'kf-2')) == 'Beta/x'
            stamped = _kb_meta(conn, 'kb1')['onedrive_sync']['sources']
            directories = _directories(conn, 'kb1')
            roots = {name: did for did, (parent, name) in directories.items() if parent is None}
            assert stamped[0]['root_directory_id'] == roots['Alpha']
            assert stamped[1]['root_directory_id'] == roots['Beta']

    def test_file_type_source_gets_no_wrapper(self):
        engine = _setup_engine()
        meta = {'google_drive_sync': {'sources': [{'item_id': 'F1', 'name': 'picked.pdf', 'type': 'file'}]}}
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', meta)
            _insert_link(conn, 'kf-1', 'kb1', relative_path='picked.pdf', source_item_id='F1')
            conn.commit()

        _run(engine)

        with engine.connect() as conn:
            assert _link_directory(conn, 'kf-1') is None
            assert _directories(conn, 'kb1') == {}
            assert 'root_directory_id' not in _kb_meta(conn, 'kb1')['google_drive_sync']['sources'][0]

    def test_loose_and_unknown_source_links_untouched(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', _FOLDER_META)
            _insert_link(conn, 'kf-loose', 'kb1')  # no path identity at all
            _insert_link(conn, 'kf-orphan', 'kb1', relative_path='x/y.pdf', source_item_id='UNKNOWN')
            conn.commit()

        _run(engine)

        with engine.connect() as conn:
            assert _link_directory(conn, 'kf-loose') is None
            assert _link_directory(conn, 'kf-orphan') is None
            assert _directories(conn, 'kb1') == {}

    def test_existing_directory_id_untouched(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', _FOLDER_META)
            conn.execute(
                text(
                    'INSERT INTO knowledge_directory (id, knowledge_id, parent_id, name, user_id, created_at, updated_at) '
                    "VALUES ('pre-dir', 'kb1', NULL, 'Manual', 'u1', 1, 1)"
                )
            )
            _insert_link(
                conn, 'kf-placed', 'kb1', relative_path='docs/a.pdf', source_item_id='S1', directory_id='pre-dir'
            )
            conn.commit()

        _run(engine)

        with engine.connect() as conn:
            assert _link_directory(conn, 'kf-placed') == 'pre-dir'
            # No derived chain was created for the already-placed row.
            assert set(_directories(conn, 'kb1')) == {'pre-dir'}

    def test_reuses_existing_directories(self):
        """A pre-existing (write-path-created) chain is reused, not duplicated."""
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', _FOLDER_META)
            conn.execute(
                text(
                    'INSERT INTO knowledge_directory (id, knowledge_id, parent_id, name, user_id, created_at, updated_at) '
                    "VALUES ('root-dir', 'kb1', NULL, 'Alpha Folder', 'u1', 1, 1)"
                )
            )
            _insert_link(conn, 'kf-1', 'kb1', relative_path='a.pdf', source_item_id='S1')
            conn.commit()

        _run(engine)

        with engine.connect() as conn:
            assert _link_directory(conn, 'kf-1') == 'root-dir'
            assert set(_directories(conn, 'kb1')) == {'root-dir'}

    def test_idempotent_re_run(self):
        engine = _setup_engine()
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', _FOLDER_META)
            _insert_link(conn, 'kf-1', 'kb1', relative_path='docs/a.pdf', source_item_id='S1')
            conn.commit()

        _run(engine)
        with engine.connect() as conn:
            first_directories = _directories(conn, 'kb1')
            first_placement = _link_directory(conn, 'kf-1')

        _run(engine)
        with engine.connect() as conn:
            assert _directories(conn, 'kb1') == first_directories
            assert _link_directory(conn, 'kf-1') == first_placement

    def test_batching_covers_more_than_one_batch(self):
        """Keyset batching: > _BATCH_SIZE eligible rows all get placed (an
        OFFSET walk would skip half, since updates shrink the filter set)."""
        engine = _setup_engine()
        total = _migration._BATCH_SIZE + 25
        with engine.connect() as conn:
            _insert_kb(conn, 'kb1', _FOLDER_META)
            for index in range(total):
                _insert_link(conn, f'kf-{index:05d}', 'kb1', relative_path=f'docs/f{index}.pdf', source_item_id='S1')
            conn.commit()

        _run(engine)

        with engine.connect() as conn:
            unplaced = conn.execute(text('SELECT count(*) FROM knowledge_file WHERE directory_id IS NULL')).fetchone()[
                0
            ]
            assert unplaced == 0
            assert sorted(name for _, name in _directories(conn, 'kb1').values()) == ['Alpha Folder', 'docs']
