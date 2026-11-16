"""Migration 901d1275cce6 removes File storage paths embedded in stored JSON, on SQLite and PostgreSQL."""

import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

_PATH = Path(__file__).resolve().parents[2] / 'migrations' / 'versions' / '901d1275cce6_strip_file_storage_paths.py'
_spec = importlib.util.spec_from_file_location('strip_file_storage_paths', _PATH)
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

STORAGE_PATH = 's3://acme-tenant-uploads/f1_offerte.pdf'
RECORD = {'id': 'f1', 'user_id': 'u1', 'filename': 'offerte.pdf', 'path': STORAGE_PATH, 'meta': {'name': 'offerte.pdf'}}
ITEM = {'type': 'file', 'id': 'f1', 'name': 'offerte.pdf', 'file': RECORD}
MESSAGE = {'id': 'm1', 'role': 'user', 'files': [ITEM]}
CHAT = {'title': 't', 'files': [ITEM], 'history': {'messages': {'m1': MESSAGE}}, 'messages': [MESSAGE]}
# A terminal file entry and a skill-style record carry paths that are not storage locations.
UNRELATED = {'files': [{'type': 'filesystem', 'path': '/workspace/a.txt'}], 'file': {'id': 'x', 'path': 'docs/a.md'}}

ROWS = {
    'chat': [('c1', CHAT), ('c2', UNRELATED), ('c3', None)],
    'shared_chat': [('s1', CHAT)],
    'chat_message': [('m1', [ITEM]), ('m2', [RECORD])],
    'folder': [('d1', {'files': [ITEM]})],
    'message': [('x1', {'files': [ITEM]})],
    'model': [('a1', {'knowledge': [ITEM, {'type': 'collection', 'id': 'kb', 'files': [RECORD]}]})],
}
COLUMNS = {'chat': 'chat', 'shared_chat': 'chat', 'chat_message': 'files', 'folder': 'data', 'message': 'data'}


@pytest.fixture(params=['sqlite', 'postgresql'])
def engine(request):
    if request.param == 'sqlite':
        engine = create_engine('sqlite://')
    else:
        engine = create_engine(request.getfixturevalue('postgres_database'))
    yield engine
    engine.dispose()


def _seed(engine, batch_rows=0):
    # JSON columns are native json on PostgreSQL; model.meta is text-encoded JSON everywhere.
    postgres = engine.dialect.name == 'postgresql'
    with engine.begin() as conn:
        for table, rows in ROWS.items():
            column = COLUMNS.get(table, 'meta')
            json_type = 'json' if postgres and table in COLUMNS else 'TEXT'
            value_sql = 'CAST(:v AS json)' if json_type == 'json' else ':v'
            conn.execute(text(f'CREATE TABLE {table} (id TEXT PRIMARY KEY, {column} {json_type})'))
            extra = [(f'{table}-{n:04d}', CHAT) for n in range(batch_rows)] if table == 'chat' else []
            for key, value in rows + extra:
                stored = None if value is None else json.dumps(value)
                conn.execute(
                    text(f'INSERT INTO {table} (id, {column}) VALUES (:id, {value_sql})'), {'id': key, 'v': stored}
                )


def _dump(engine):
    with engine.connect() as conn:
        return {
            table: dict(conn.execute(text(f'SELECT id, CAST({COLUMNS.get(table, "meta")} AS TEXT) FROM {table}')).all())
            for table in ROWS
        }


def test_upgrade_strips_embedded_storage_paths_and_leaves_the_rest(engine):
    _seed(engine)
    with engine.begin() as conn:
        _migration.strip_file_paths(conn)
    stored = _dump(engine)
    for table, rows in stored.items():
        for key, value in rows.items():
            assert value is None or 'acme-tenant-uploads' not in value, (table, key)
    assert json.loads(stored['chat']['c2']) == UNRELATED
    assert stored['chat']['c3'] is None
    assert json.loads(stored['chat']['c1'])['history']['messages']['m1']['files'][0]['file'] == {
        key: value for key, value in RECORD.items() if key != 'path'
    }

    with engine.begin() as conn:
        _migration.strip_file_paths(conn)
    assert _dump(engine) == stored


def test_upgrade_covers_rows_beyond_one_batch(engine):
    _seed(engine, batch_rows=_migration._BATCH_SIZE * 2 + 5)
    with engine.begin() as conn:
        _migration.strip_file_paths(conn)
    assert all('acme-tenant-uploads' not in (value or '') for value in _dump(engine)['chat'].values())


def test_upgrade_skips_absent_tables(engine):
    with engine.begin() as conn:
        _migration.strip_file_paths(conn)
