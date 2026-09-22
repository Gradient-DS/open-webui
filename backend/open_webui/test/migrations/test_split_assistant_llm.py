"""Exercise the assistant split against real SQLite DDL and data."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = Path(__file__).resolve().parents[2] / 'migrations/versions/e24b7c9d1f63_split_assistant_llm.py'
spec = importlib.util.spec_from_file_location('split_assistant_llm', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def database(monkeypatch):
    """Supply a pre-split schema containing assistants and ordinary models."""
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE model (id TEXT PRIMARY KEY, base_model_id TEXT)'))
        conn.execute(sa.text('CREATE TABLE chat_message (id TEXT PRIMARY KEY, model_id TEXT)'))
        conn.execute(sa.text("INSERT INTO model VALUES ('a', 'llm'), ('b', 'other'), ('llm', NULL)"))
        conn.execute(sa.text("INSERT INTO chat_message VALUES ('1', 'a'), ('2', 'b'), ('3', 'llm'), ('4', NULL)"))
        config = sa.Table(
            'config', sa.MetaData(), sa.Column('key', sa.Text, primary_key=True), sa.Column('value', sa.JSON)
        )
        config.create(conn)
        conn.execute(
            config.insert(),
            [
                {'key': 'ui.default_models', 'value': 'a,llm, b,other'},
                {'key': 'ui.default_pinned_models', 'value': 'a,llm'},
                {'key': 'ui.model_order_list', 'value': ['b', 'a', 'llm']},
            ],
        )
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
        yield conn, config
    engine.dispose()


def test_upgrade_and_rerun(database):
    """Backfill only assistant rows and leave unrelated config intact on reruns."""
    conn, config = database
    for _ in range(2):
        migration.upgrade()
        assert conn.execute(sa.text('SELECT * FROM chat_message ORDER BY id')).all() == [
            ('1', 'llm', 'a'),
            ('2', 'other', 'b'),
            ('3', 'llm', None),
            ('4', None, None),
        ]
        assert dict(conn.execute(sa.select(config)).all()) == {
            'ui.default_models': 'llm,other',
            'ui.default_pinned_models': 'a,llm',
            'ui.model_order_list': ['b', 'a', 'llm'],
        }
        assert [i['name'] for i in sa.inspect(conn).get_indexes('chat_message')] == ['ix_chat_message_assistant_id']
        assert conn.execute(sa.text("SELECT base_model_id FROM model WHERE id = 'a'")).scalar() == 'llm'


def test_downgrade(database):
    """Restore old model references and remove both the index and column."""
    conn, config = database
    migration.upgrade()
    migration.downgrade()
    migration.downgrade()
    assert conn.execute(sa.text('SELECT * FROM chat_message ORDER BY id')).all() == [
        ('1', 'a'),
        ('2', 'b'),
        ('3', 'llm'),
        ('4', None),
    ]
    assert sa.inspect(conn).get_indexes('chat_message') == []
    assert conn.execute(sa.select(config.c.value).where(config.c.key == 'ui.default_models')).scalar() == 'llm,other'


@pytest.mark.parametrize(
    'value,expected', [(['a', 'llm'], ['llm']), ({'unexpected': 'a'}, {'unexpected': 'a'}), ('llm', 'llm')]
)
def test_config_shapes(database, value, expected):
    """Support historical lists and preserve unknown config shapes."""
    conn, config = database
    conn.execute(config.update().where(config.c.key == 'ui.default_models').values(value=value))
    migration.upgrade()
    assert conn.execute(sa.select(config.c.value).where(config.c.key == 'ui.default_models')).scalar() == expected
