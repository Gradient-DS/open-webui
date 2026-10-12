"""Exercise the sidebar lookup indexes against real SQLite DDL."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = Path(__file__).resolve().parents[2] / 'migrations/versions/b7e3c1d94a25_add_sidebar_lookup_indexes.py'
spec = importlib.util.spec_from_file_location('sidebar_lookup_indexes', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def _indexes(conn, table):
    return {index['name'] for index in sa.inspect(conn).get_indexes(table)}


def test_upgrade_and_downgrade(monkeypatch):
    """Create the owner indexes and drop them again; the Postgres-only expression index is skipped."""
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE folder (id TEXT, user_id TEXT, parent_id TEXT, PRIMARY KEY (id, user_id))'))
        conn.execute(sa.text('CREATE TABLE note (id TEXT PRIMARY KEY, user_id TEXT, updated_at BIGINT)'))
        conn.execute(sa.text('CREATE TABLE file (id TEXT PRIMARY KEY, user_id TEXT, meta JSON)'))
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        assert _indexes(conn, 'folder') == {'folder_user_id_parent_id_idx'}
        assert _indexes(conn, 'note') == {'note_user_id_updated_at_idx'}
        assert _indexes(conn, 'file') == {'file_user_id_idx'}
        migration.downgrade()
        assert not _indexes(conn, 'folder') | _indexes(conn, 'note') | _indexes(conn, 'file')
    engine.dispose()


def test_collection_key_index_matches_the_lookup():
    """The expression index is the expression the knowledge store filters on."""
    from open_webui.models.files import File
    from sqlalchemy.dialects import postgresql

    lookup = (
        File.meta['soev_collection_key']
        .as_string()
        .compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True})
    )
    assert f'({str(lookup).replace("file.", "")})' == migration.COLLECTION_KEY
