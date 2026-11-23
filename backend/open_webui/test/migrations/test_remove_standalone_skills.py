"""[Gradient] Exercise irreversible skill deletion on SQLite; File rows are left alone."""

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = Path(__file__).resolve().parents[2] / 'migrations/versions/b7e19d4a6c82_remove_standalone_skills.py'
spec = importlib.util.spec_from_file_location('remove_standalone_skills', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def conn(monkeypatch):
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('PRAGMA foreign_keys=ON'))
        conn.execute(sa.text('CREATE TABLE skill (id TEXT PRIMARY KEY, content TEXT)'))
        conn.execute(sa.text('CREATE TABLE file (id TEXT PRIMARY KEY, path TEXT)'))
        conn.execute(sa.text('CREATE TABLE model (id TEXT PRIMARY KEY, meta TEXT)'))
        conn.execute(sa.text('CREATE TABLE access_grant (id TEXT PRIMARY KEY, resource_type TEXT, resource_id TEXT)'))
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
        migration.downgrade()
        conn.execute(sa.text("INSERT INTO skill VALUES ('standalone', 'Private instructions')"))
        conn.execute(
            sa.text(
                "INSERT INTO file VALUES ('orphan', '/uploads/orphan'), ('empty', NULL), ('other', '/uploads/other')"
            )
        )
        conn.execute(
            sa.text("""
            INSERT INTO skill_file (id, user_id, skill_id, file_id, created_at, updated_at, path) VALUES
            ('one', 'owner', 'standalone', 'orphan', 1, 1, 'one.bin'),
            ('two', 'owner', 'standalone', 'orphan', 1, 1, 'two.bin'),
            ('three', 'owner', 'standalone', 'empty', 1, 1, 'empty.txt')
        """)
        )
        conn.execute(
            sa.text("""
            INSERT INTO access_grant VALUES ('skill-grant', 'skill', 'standalone'),
            ('stale-grant', 'skill', 'already-gone'), ('model-grant', 'model', 'assistant'),
            ('file-grant', 'file', 'orphan'), ('other-file-grant', 'file', 'other')
        """)
        )
        for id, meta in [
            (
                'assistant',
                {'skillIds': ['standalone'], 'description': 'Bewaar dit', 'capabilities': {'workspace': True}},
            ),
            ('base-model', {'skillIds': [], 'hidden': False}),
            ('no-skills', {'description': 'Untouched'}),
            ('null-meta', None),
            ('historical-meta', ['unusual', 'meta']),
        ]:
            conn.execute(sa.text('INSERT INTO model VALUES (:id, :meta)'), {'id': id, 'meta': json.dumps(meta)})
        yield conn
    engine.dispose()


def test_upgrade_deletes_only_standalone_data_and_can_rerun(conn):
    for _ in range(2):
        migration.upgrade()
        assert 'skill_file' not in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.text('SELECT * FROM skill')).all() == []
        assert set(conn.execute(sa.text('SELECT id FROM file')).scalars()) == {'orphan', 'empty', 'other'}
        assert set(conn.execute(sa.text('SELECT id FROM access_grant')).scalars()) == {
            'model-grant',
            'file-grant',
            'other-file-grant',
        }
        meta = {id: json.loads(value) for id, value in conn.execute(sa.text('SELECT * FROM model'))}
        assert meta == {
            'assistant': {'description': 'Bewaar dit', 'capabilities': {'workspace': True}},
            'base-model': {'hidden': False},
            'no-skills': {'description': 'Untouched'},
            'null-meta': None,
            'historical-meta': ['unusual', 'meta'],
        }


def test_downgrade_recreates_only_original_structure(conn):
    before = sa.inspect(conn)
    columns = before.get_columns('skill_file')
    indexes = before.get_indexes('skill_file')
    unique = before.get_unique_constraints('skill_file')
    foreign_keys = before.get_foreign_keys('skill_file')
    migration.upgrade()
    migration.downgrade()
    migration.downgrade()
    after = sa.inspect(conn)
    assert [(c['name'], str(c['type']), c['nullable']) for c in after.get_columns('skill_file')] == [
        (c['name'], str(c['type']), c['nullable']) for c in columns
    ]
    assert after.get_indexes('skill_file') == indexes
    assert after.get_unique_constraints('skill_file') == unique
    assert after.get_foreign_keys('skill_file') == foreign_keys
    assert conn.execute(sa.text('SELECT COUNT(*) FROM skill_file')).scalar() == 0
    assert conn.execute(sa.text('SELECT COUNT(*) FROM skill')).scalar() == 0
    assert set(conn.execute(sa.text('SELECT id FROM file')).scalars()) == {'orphan', 'empty', 'other'}
