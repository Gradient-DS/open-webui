"""The v2 migration steps against real SQLite rows and the recorded soev-api contract."""

import asyncio
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

MIGRATION = 'v2-test'
V2_CONFIG = {
    'agent_api.selected_agent': 'soev',
    'document_writer.enable': True,
    'live_documents.enable': True,
    'live_mail.enable': False,
    'notes.enable': True,
    'web.search.enable': True,
    'webui.url': 'https://client.soev.ai',
    'user.permissions.features': {'web_search': True, 'notes': True},
}
V1_CONFIG = {
    'openai.enable': True,
    'openai.api_base_urls': ['https://litellm.invalid/v1'],
    'openai.api_keys': ['sk-litellm'],
    'openai.api_configs': {'0': {'enable': True, 'model_ids': []}},
    'ui.default_models': 'zai-org/GLM-5.3',
    'ui.default_pinned_models': 'zai-org/GLM-5.3,unknown/model',
    'task.model.default': 'zai-org/GLM-5.3',
    'webui.url': 'https://old.soev.ai',
    'user.permissions': {'chat': {'edit': True}, 'features': {'web_search': False, 'memories': True}},
    'web.search.enable': False,
}


class FakeVectors:
    """The async vector client surface the memory reindex uses, kept in memory."""

    def __init__(self):
        self.collections: dict[str, dict] = {}

    async def has_collection(self, collection_name):
        return collection_name in self.collections

    async def delete_collection(self, collection_name):
        self.collections.pop(collection_name, None)

    async def upsert(self, collection_name, items):
        self.collections.setdefault(collection_name, {}).update({item['id']: item for item in items})

    async def get(self, collection_name):
        ids = list(self.collections.get(collection_name, {}))
        return SimpleNamespace(ids=[ids]) if ids else None


class FakeRedis:
    def __init__(self):
        self.values, self.sets = {}, []

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.sets.append(key)

    async def get(self, key):
        return self.values.get(key)


@pytest.fixture
def fake_vector_modules(monkeypatch):
    """The real clients connect to the configured vector store on import."""
    vectors = FakeVectors()
    monkeypatch.setitem(
        sys.modules, 'open_webui.retrieval.vector.async_client', SimpleNamespace(ASYNC_VECTOR_DB_CLIENT=vectors)
    )
    monkeypatch.setitem(sys.modules, 'open_webui.retrieval.vector.factory', SimpleNamespace(VECTOR_DB_CLIENT=None))
    return vectors


@pytest_asyncio.fixture
async def env(identity_config, fake_api, monkeypatch, tmp_path, fake_vector_modules):
    """A file-backed SQLite database behind every session, and soev-api behind the shared fake."""
    identity, _ = identity_config
    database = importlib.import_module('open_webui.internal.db')
    modules = {
        name: importlib.import_module(f'open_webui.models.{name}')
        for name in (
            'config',
            'users',
            'groups',
            'knowledge',
            'access_grants',
            'files',
            'chats',
            'chat_messages',
            'models',
            'memories',
            'automations',
        )
    }
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/owui.db')
    tables = [
        modules['config'].Config,
        modules['users'].User,
        modules['groups'].Group,
        modules['groups'].GroupMember,
        modules['knowledge'].Knowledge,
        modules['knowledge'].KnowledgeFile,
        modules['knowledge'].KnowledgeDirectory,
        modules['access_grants'].AccessGrant,
        modules['files'].File,
        modules['chats'].Chat,
        modules['chat_messages'].ChatMessage,
        modules['models'].Model,
        modules['memories'].Memory,
        modules['automations'].Automation,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: database.Base.metadata.create_all(sync, tables=[table.__table__ for table in tables])
        )
    monkeypatch.setattr(database, 'AsyncSessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(fake_api.handle), **kwargs),
    )
    monkeypatch.setenv('SOEV_V2_MIGRATION_ID', MIGRATION)
    monkeypatch.setenv('SOEV_V2_CONFIG', json.dumps(V2_CONFIG))
    monkeypatch.setenv('SOEV_V2_MODEL_MAP', '{}')
    async with engine.begin() as connection:
        for key, value in V1_CONFIG.items():
            # Hand-written text, so a byte-for-byte restore cannot pass by re-serializing.
            await connection.execute(
                sa.text('INSERT INTO config (key, value, updated_at) VALUES (:key, :value, :at)'),
                {'key': key, 'value': json.dumps(value, indent=1), 'at': 1000},
            )
    vectors = fake_vector_modules
    redis = FakeRedis()
    monkeypatch.setattr(importlib.import_module('open_webui.utils.redis'), 'get_redis_client', lambda **_: redis)
    monkeypatch.setattr(importlib.import_module('open_webui.routers.memories'), 'ASYNC_VECTOR_DB_CLIENT', vectors)
    embedded = []

    async def embed(text, prefix=None, user=None):
        embedded.append(text)
        return [float(len(text)), 1.0]

    monkeypatch.setattr(
        importlib.import_module('open_webui.soev.migrate_memories'),
        'build_embedding_function',
        AsyncMock(return_value=embed),
    )
    module = importlib.import_module('open_webui.soev.migrate')
    yield SimpleNamespace(
        engine=engine,
        api=fake_api,
        identity=identity,
        module=module,
        models=modules,
        tmp_path=tmp_path,
        vectors=vectors,
        embedded=embedded,
        redis=redis,
    )
    await engine.dispose()


async def rows(env, sql, **params):
    async with env.engine.connect() as connection:
        return [tuple(row) for row in (await connection.execute(sa.text(sql), params)).all()]


async def config_rows(env):
    return await rows(env, 'SELECT key, CAST(value AS TEXT), updated_at FROM config ORDER BY key')


def options(env, **overrides):
    return env.module.options_from_env()


@pytest.mark.asyncio
async def test_the_snapshot_is_taken_once_with_absent_keys_recorded(env):
    """A rerun keeps the first snapshot even after the config changed in between."""
    module = env.module
    before = await config_rows(env)
    await module.apply(options(env))
    snapshot = await rows(env, 'SELECT key, value, updated_at FROM owui_v2_migration_config_backup ORDER BY key')
    stored = {key: (value, at) for key, value, at in before}
    from open_webui.soev.migrate_config import SNAPSHOT_KEYS

    assert [key for key, _, _ in snapshot] == sorted(SNAPSHOT_KEYS)
    for key, value, at in snapshot:
        assert (value, at) == stored.get(key, (None, None))
    async with env.engine.begin() as connection:
        await connection.execute(sa.text("UPDATE config SET value = '\"changed\"' WHERE key = 'webui.url'"))
    await module.apply(options(env))
    assert (
        await rows(env, 'SELECT key, value, updated_at FROM owui_v2_migration_config_backup ORDER BY key') == snapshot
    )
    assert await rows(
        env, 'SELECT migration_id, step FROM owui_v2_migration_migration_marker WHERE step = :s', s='snapshot'
    ) == [(MIGRATION, 'snapshot')]


@pytest.mark.asyncio
async def test_restore_puts_config_rows_back_byte_for_byte(env):
    """Changed keys regain their stored text and timestamps; keys that had no row are removed."""
    before = await config_rows(env)
    await env.module.apply(options(env))
    async with env.engine.begin() as connection:
        await connection.execute(sa.text("UPDATE config SET value = '\"x\"', updated_at = 5 WHERE key = 'webui.url'"))
        await connection.execute(
            sa.text("UPDATE config SET value = 'true', updated_at = 7 WHERE key = 'live_mail.enable'")
        )
        await connection.execute(sa.text("INSERT INTO config (key, value, updated_at) VALUES ('other', '1', 7)"))
    assert await env.module.restore(MIGRATION) == 0
    after = await config_rows(env)
    assert after == sorted([*before, ('other', '1', 7)])


@pytest.mark.asyncio
async def test_restore_without_a_snapshot_fails_loudly(env):
    from open_webui.soev.migrate_state import MigrationError

    with pytest.raises(MigrationError, match='No config snapshot'):
        await env.module.restore('never-applied')


V1_HEAD = 'e24b7c9d1f63'


@pytest.mark.asyncio
async def test_restore_is_byte_for_byte_on_postgres_and_state_stays_outside_alembic(env):
    """PostgreSQL json keeps the stored text; the state lives in its own schema and public is untouched."""
    import os

    url = os.environ.get('SOEV_MIGRATE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set SOEV_MIGRATE_TEST_DATABASE_URL to an isolated PostgreSQL test database')
    from open_webui.soev import migrate_config, migrate_state

    config = env.models['config'].Config.__table__
    engine = create_async_engine(url)

    async def reset(connection):
        await connection.execute(sa.text(f'DROP SCHEMA IF EXISTS {migrate_state.SCHEMA} CASCADE'))
        await connection.execute(sa.text('DROP TABLE IF EXISTS config, alembic_version'))

    try:
        async with engine.begin() as connection:
            await reset(connection)
            await connection.run_sync(lambda sync: config.metadata.create_all(sync, tables=[config]))
            await connection.execute(sa.text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
            await connection.execute(sa.text(f"INSERT INTO alembic_version VALUES ('{V1_HEAD}')"))
            for key, value in V1_CONFIG.items():
                await connection.execute(
                    sa.text('INSERT INTO config (key, value, updated_at) VALUES (:key, CAST(:value AS JSON), 1000)'),
                    {'key': key, 'value': json.dumps(value, indent=1)},
                )
        select = 'SELECT key, CAST(value AS TEXT), updated_at FROM config ORDER BY key'
        async with engine.connect() as connection:
            before = (await connection.execute(sa.text(select))).all()
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for _ in range(2):
                await migrate_state.ensure_tables(db)
            assert await migrate_state.snapshot(db, MIGRATION, migrate_config.SNAPSHOT_KEYS)
            await migrate_config.switch(db, migrate_config.parse_v2_config(json.dumps(V2_CONFIG)))
            await migrate_state.restore_config(db, MIGRATION)
        async with engine.connect() as connection:
            after = (await connection.execute(sa.text(select))).all()
            public = await connection.run_sync(lambda sync: sorted(sa.inspect(sync).get_table_names()))
            state = await connection.run_sync(
                lambda sync: sorted(sa.inspect(sync).get_table_names(schema=migrate_state.SCHEMA))
            )
            version = (await connection.execute(sa.text('SELECT version_num FROM alembic_version'))).all()
        assert [tuple(row) for row in after] == [tuple(row) for row in before]
        assert public == ['alembic_version', 'config']
        assert state == ['config_backup', 'migration_marker', 'model_id_backup']
        assert [tuple(row) for row in version] == [(V1_HEAD,)]
    finally:
        async with engine.begin() as connection:
            await reset(connection)
        await engine.dispose()


def test_the_alembic_head_is_still_the_v1_head():
    """The migration adds no revision, so a v1 image can still run its schema upgrade."""
    from pathlib import Path

    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    config = AlembicConfig()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[2] / 'migrations'))
    assert ScriptDirectory.from_config(config).get_heads() == [V1_HEAD]


@pytest.mark.asyncio
async def test_apply_leaves_the_alembic_version_and_adds_only_prefixed_tables(env):
    async with env.engine.begin() as connection:
        await connection.execute(sa.text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        await connection.execute(sa.text(f"INSERT INTO alembic_version VALUES ('{V1_HEAD}')"))
    names = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    before = {name for (name,) in await rows(env, names)}
    await env.module.apply(options(env))
    added = {name for (name,) in await rows(env, names)} - before
    assert added == {f'owui_v2_migration_{t}' for t in ('config_backup', 'migration_marker', 'model_id_backup')}
    assert await rows(env, 'SELECT version_num FROM alembic_version') == [(V1_HEAD,)]


async def config_value(env, key):
    found = await rows(env, 'SELECT value FROM config WHERE key = :key', key=key)
    return json.loads(found[0][0]) if found else None


@pytest.mark.asyncio
async def test_the_config_switch_writes_the_fixed_key_set_once(env):
    """Connections and default/task models are retired, env values land, and permissions merge."""
    await env.module.apply(options(env))
    assert await config_value(env, 'openai.enable') is False
    assert await config_value(env, 'openai.api_keys') == []
    assert await config_value(env, 'ollama.enable') is False
    assert await config_value(env, 'ui.default_models') == ''
    assert await config_value(env, 'task.model.default') == ''
    assert await config_value(env, 'webui.url') == 'https://client.soev.ai'
    assert await config_value(env, 'live_documents.enable') is True
    assert await config_value(env, 'agent_api.selected_agent') == 'soev'
    assert await config_value(env, 'user.permissions') == {
        'chat': {'edit': True},
        'features': {'web_search': True, 'memories': True, 'notes': True},
    }
    async with env.engine.begin() as connection:
        await connection.execute(sa.text("UPDATE config SET value = '\"admin\"' WHERE key = 'webui.url'"))
    before = await config_rows(env)
    await env.module.apply(options(env))
    assert await config_rows(env) == before


@pytest.mark.asyncio
async def test_apply_after_restore_switches_again(env):
    """Restore clears the switch marker so a second install reaches the same v2 config."""
    await env.module.apply(options(env))
    switched = await config_rows(env)
    await env.module.restore(MIGRATION)
    await env.module.apply(options(env))
    assert [row[:2] for row in await config_rows(env)] == [row[:2] for row in switched]


async def seed_renamed_model(env):
    """A legacy override row whose catalog id already has a row, as gemma and gemini had on staging."""
    async with env.engine.begin() as connection:
        await connection.execute(
            sa.text(
                'INSERT INTO model (id, user_id, base_model_id, name, params, meta, is_active, created_at, updated_at)'
                " VALUES ('google/gemma-4-31b', 'alice', NULL, 'Gemma', '{}', '{}', 1, 1, 1),"
                " ('gemma-4-31b', 'alice', NULL, 'Gemma', '{}', '{}', 1, 1, 1)"
            )
        )
        await connection.execute(
            sa.text(
                'INSERT INTO access_grant (id, resource_type, resource_id, principal_type, principal_id, permission,'
                " created_at) VALUES ('g3', 'model', 'google/gemma-4-31b', 'group', 'eng', 'read', 1),"
                " ('g4', 'model', 'google/gemma-4-31b', 'user', '*', 'read', 1),"
                " ('g5', 'model', 'gemma-4-31b', 'user', '*', 'read', 1)"
            )
        )


GEMMA = (
    "SELECT id, resource_id, NULL FROM access_grant WHERE id IN ('g3', 'g4', 'g5')"
    " UNION ALL SELECT id, base_model_id, is_active FROM model WHERE id LIKE '%gemma%' ORDER BY 1"
)


@pytest.mark.asyncio
async def test_a_renamed_model_whose_target_exists_is_merged_and_restored(env, monkeypatch, capsys):
    await seed_models(env, monkeypatch)
    await seed_renamed_model(env)
    original = await rows(env, GEMMA)
    await env.module.apply(options(env))
    assert await rows(env, GEMMA) == [
        ('g3', 'gemma-4-31b', None),
        ('g4', 'google/gemma-4-31b', None),
        ('g5', 'gemma-4-31b', None),
        ('gemma-4-31b', None, 1),
        ('google/gemma-4-31b', None, 0),
    ]
    output = capsys.readouterr().out
    expected = '3 model ids: merged model google/gemma-4-31b: gemma-4-31b exists; 1 grants moved to it'
    assert f'{expected}, google/gemma-4-31b deactivated' in output
    async with env.engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM owui_v2_migration_migration_marker WHERE step = 'model_ids'"))
    await env.module.apply(options(env))
    assert 'google/gemma-4-31b already inactive' in capsys.readouterr().out
    await env.module.restore(MIGRATION)
    assert await rows(env, GEMMA) == original


@pytest.mark.parametrize(
    ('raw', 'message'),
    [
        (None, 'not set'),
        ('[]', 'JSON object'),
        (json.dumps({k: v for k, v in V2_CONFIG.items() if k != 'webui.url'}), 'missing webui.url'),
        (json.dumps({**V2_CONFIG, 'rag.top_k': 3}), 'unknown keys rag.top_k'),
        (json.dumps({**V2_CONFIG, 'user.permissions.features': {'notes': 'yes'}}), 'booleans'),
    ],
)
def test_an_invalid_v2_config_is_refused(raw, message):
    from open_webui.soev.migrate_config import parse_v2_config
    from open_webui.soev.migrate_state import MigrationError

    with pytest.raises(MigrationError, match=message):
        parse_v2_config(raw)


MODEL_MAP = {'zai-org/GLM-5.3': 'glm-5-3', 'google/gemma-4-31b': 'gemma-4-31b'}
CHAT = {
    'models': ['zai-org/GLM-5.3', 'helper'],
    'history': {
        'currentId': 'm.2',
        'messages': {
            'm.1': {'role': 'user', 'models': ['zai-org/GLM-5.3']},
            'm.2': {'role': 'assistant', 'model': 'zai-org/GLM-5.3', 'content': 'model: zai-org/GLM-5.3'},
            'm.3': {'role': 'assistant', 'model': 'helper'},
        },
    },
    'messages': [{'model': 'unknown/model'}],
}
TABLE_DUMPS = {
    'chat': 'SELECT id, chat FROM chat ORDER BY id',
    'chat_message': 'SELECT id, model_id FROM chat_message ORDER BY id',
    'model': 'SELECT id, base_model_id FROM model ORDER BY id',
    'access_grant': 'SELECT id, resource_id FROM access_grant ORDER BY id',
    'user': 'SELECT id, settings FROM user ORDER BY id',
    'automation': 'SELECT id, data FROM automation ORDER BY id',
}


async def seed_models(env, monkeypatch):
    monkeypatch.setenv('SOEV_V2_MODEL_MAP', json.dumps(MODEL_MAP))
    statements = [
        (
            'INSERT INTO user (id, email, role, name, settings, created_at, updated_at, last_active_at)'
            " VALUES ('alice', 'alice@example.test', 'user', 'Alice', :settings, 1, 1, 1)",
            {
                'settings': json.dumps(
                    {'ui': {'models': ['zai-org/GLM-5.3'], 'pinnedModels': ['helper', 'unknown/model']}}
                )
            },
        ),
        (
            'INSERT INTO model (id, user_id, base_model_id, name, params, meta, is_active, created_at, updated_at)'
            " VALUES ('zai-org/GLM-5.3', 'alice', NULL, 'GLM', '{}', '{}', 1, 1, 1),"
            " ('helper', 'alice', 'zai-org/GLM-5.3', 'Helper', '{}', '{}', 1, 1, 1),"
            " ('legacy', 'alice', 'unknown/model', 'Legacy', '{}', '{}', 1, 1, 1)",
            {},
        ),
        (
            'INSERT INTO access_grant (id, resource_type, resource_id, principal_type, principal_id, permission,'
            " created_at) VALUES ('g1', 'model', 'zai-org/GLM-5.3', 'user', '*', 'read', 1),"
            " ('g2', 'knowledge', 'zai-org/GLM-5.3', 'user', '*', 'read', 1)",
            {},
        ),
        (
            "INSERT INTO chat (id, user_id, title, chat, created_at, updated_at, meta) VALUES ('c1', 'alice', 'T',"
            " :chat, 1, 1, '{}'), ('c2', 'alice', 'Empty', '{}', 1, 1, '{}')",
            {'chat': json.dumps(CHAT)},
        ),
        (
            'INSERT INTO chat_message (id, chat_id, role, model_id, created_at, updated_at) VALUES'
            " ('m.1', 'c1', 'user', NULL, 1, 1), ('m.2', 'c1', 'assistant', 'zai-org/GLM-5.3', 1, 1),"
            " ('m.3', 'c1', 'assistant', 'unknown/model', 1, 1)",
            {},
        ),
        (
            'INSERT INTO automation (id, user_id, name, data, is_active, created_at, updated_at)'
            " VALUES ('a1', 'alice', 'Daily', :data, 1, 1, 1)",
            {'data': json.dumps({'prompt': 'p', 'model_id': 'zai-org/GLM-5.3', 'rrule': 'FREQ=DAILY'})},
        ),
    ]
    async with env.engine.begin() as connection:
        for statement, params in statements:
            await connection.execute(sa.text(statement), params)


async def dumps(env):
    result = {}
    for name, sql in TABLE_DUMPS.items():
        result[name] = [
            tuple(json.loads(v) if isinstance(v, str) and v[:1] in '{[' else v for v in row)
            for row in await rows(env, sql)
        ]
    return result


@pytest.mark.asyncio
async def test_model_ids_are_rewritten_everywhere_and_unmapped_ids_reported(env, monkeypatch, capsys):
    await seed_models(env, monkeypatch)
    await env.module.apply(options(env))
    data = await dumps(env)
    chat = dict(data['chat'])['c1']
    assert chat['models'] == ['glm-5-3', 'helper']
    assert chat['history']['messages']['m.1']['models'] == ['glm-5-3']
    assert chat['history']['messages']['m.2']['model'] == 'glm-5-3'
    assert chat['history']['messages']['m.2']['content'] == 'model: zai-org/GLM-5.3'
    assert chat['messages'] == [{'model': 'unknown/model'}]
    assert data['chat_message'] == [('m.1', None), ('m.2', 'glm-5-3'), ('m.3', 'unknown/model')]
    assert data['model'] == [('glm-5-3', None), ('helper', 'glm-5-3'), ('legacy', 'unknown/model')]
    assert data['access_grant'] == [('g1', 'glm-5-3'), ('g2', 'zai-org/GLM-5.3')]
    assert data['user'] == [('alice', {'ui': {'models': ['glm-5-3'], 'pinnedModels': ['helper', 'unknown/model']}})]
    assert data['automation'][0][1]['model_id'] == 'glm-5-3'
    assert await config_value(env, 'ui.default_pinned_models') == 'glm-5-3,unknown/model'
    output = capsys.readouterr().out
    assert '3 model ids: unmapped unknown/model' in output
    assert 'unmapped helper' not in output


@pytest.mark.asyncio
async def test_a_model_id_rerun_rewrites_and_records_nothing(env, monkeypatch):
    """Even with the step marker gone, rewritten rows hold catalog ids and are left alone."""
    await seed_models(env, monkeypatch)
    await env.module.apply(options(env))
    before = (
        await dumps(env),
        await rows(env, 'SELECT * FROM owui_v2_migration_model_id_backup ORDER BY site, row_id, path'),
    )
    async with env.engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM owui_v2_migration_migration_marker WHERE step = 'model_ids'"))
    await env.module.apply(options(env))
    assert (
        await dumps(env),
        await rows(env, 'SELECT * FROM owui_v2_migration_model_id_backup ORDER BY site, row_id, path'),
    ) == before


@pytest.mark.asyncio
async def test_restore_gives_back_the_original_model_ids_and_keeps_later_messages(env, monkeypatch):
    await seed_models(env, monkeypatch)
    original = await dumps(env)
    await env.module.apply(options(env))
    async with env.engine.begin() as connection:
        chat = json.loads((await connection.execute(sa.text("SELECT chat FROM chat WHERE id = 'c1'"))).scalar())
        chat['history']['messages']['m.4'] = {'role': 'assistant', 'model': 'glm-5-3'}
        await connection.execute(sa.text("UPDATE chat SET chat = :chat WHERE id = 'c1'"), {'chat': json.dumps(chat)})
    await env.module.restore(MIGRATION)
    restored = await dumps(env)
    expected = json.loads(json.dumps(original))
    dict_chat = dict(restored['chat'])['c1']
    assert dict_chat['history']['messages'].pop('m.4') == {'role': 'assistant', 'model': 'glm-5-3'}
    assert json.loads(json.dumps(restored)) == expected
    assert await rows(env, 'SELECT * FROM owui_v2_migration_model_id_backup') == []


@pytest.mark.parametrize(
    ('raw', 'message'),
    [
        (None, 'not set'),
        ('{"a": ""}', 'catalog ids'),
        ('{"a": "b", "b": "c"}', 'also rewrites: b'),
    ],
)
def test_an_invalid_model_map_is_refused(raw, message):
    from open_webui.soev.migrate_models import parse_model_map
    from open_webui.soev.migrate_state import MigrationError

    with pytest.raises(MigrationError, match=message):
        parse_model_map(raw)


async def seed_knowledge(env, monkeypatch):
    """Alice's local KB with new, missing, failed and blank files, plus a OneDrive KB."""
    users = env.models['users'].Users
    knowledge, files = env.models['knowledge'], env.models['files']
    storage = importlib.import_module('open_webui.storage.provider')
    monkeypatch.setattr(importlib.import_module('open_webui.soev.ingest'), 'Storage', storage.LocalStorageProvider())
    monkeypatch.setattr(knowledge, 'AccessGrants', env.models['access_grants'].AccessGrantsTable())
    for user_id in ('alice', 'bob'):
        assert await users.insert_new_user(user_id, user_id, f'{user_id}@example.test', role='user')
    table = knowledge.KnowledgeTable()
    local = await table.insert_new_knowledge(
        'alice', knowledge.KnowledgeForm(name='Research', description='', type='local', access_grants=[])
    )
    cloud = await table.insert_new_knowledge(
        'alice', knowledge.KnowledgeForm(name='Drive', description='', type='onedrive', access_grants=[])
    )
    names = []
    for index in range(5):
        path = env.tmp_path / f'file-{index}.txt'
        path.write_bytes(f'original {index}'.encode())
        names.append(f'file-{index}')
    names += ['missing', 'failed']
    for name in names:
        meta = {'name': f'{name}.txt', 'content_type': 'text/plain'}
        if name == 'failed':
            meta.update(status='failed', soev_collection_key=local.id, error='unsupported_media_type: no text')
        await files.Files.insert_new_file(
            'alice',
            files.FileForm(id=name, filename=f'{name}.txt', path=str(env.tmp_path / f'{name}.txt'), meta=meta),
        )
        assert await table.add_file_to_knowledge_by_id(local.id, name, 'alice')
    path = env.tmp_path / 'cloud.txt'
    path.write_bytes(b'cloud')
    await files.Files.insert_new_file('alice', files.FileForm(id='cloud', filename='cloud.txt', path=str(path)))
    assert await table.add_file_to_knowledge_by_id(cloud.id, 'cloud', 'alice')
    return local, cloud


def job_posts(env):
    return [r for r in env.api.requests if r.method == 'POST' and r.url.path == '/v1/jobs']


@pytest.mark.asyncio
async def test_reingest_submits_only_missing_local_files_within_the_concurrency(env, monkeypatch, capsys):
    """Missing originals and terminal failures are reported; the cloud KB is left to its schedules."""
    local, _ = await seed_knowledge(env, monkeypatch)
    ingest = importlib.import_module('open_webui.soev.ingest')
    original, active, peak = ingest.submit, set(), []

    async def tracked(file, **kwargs):
        active.add(file.id)
        peak.append(len(active))
        try:
            await asyncio.sleep(0.01)
            return await original(file, **kwargs)
        finally:
            active.discard(file.id)

    monkeypatch.setattr(ingest, 'submit', tracked)
    monkeypatch.setenv('SOEV_V2_INGEST_CONCURRENCY', '2')
    await env.module.apply(options(env))
    submitted = sorted(json.loads(r.content)['documents'][0]['source_id'] for r in job_posts(env))
    assert submitted == [f'file-{index}' for index in range(5)]
    assert max(peak) == 2
    output = capsys.readouterr().out
    assert f'5 re-ingest: {local.id}: failed 2, submitted 5' in output
    assert f'{local.id}/missing: original missing from storage' in output
    assert f'{local.id}/failed: unsupported_media_type: no text' in output

    env.api.requests.clear()
    await env.module.apply(options(env))
    assert job_posts(env) == []
    assert f'{local.id}: failed 2, running 5' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_synced_single_file_becomes_a_schedule_and_is_never_reingested(env, monkeypatch):
    """A cloud KB's synced file lands through its schedule only, never also as a local upload."""
    _, cloud = await seed_knowledge(env, monkeypatch)
    source = {'type': 'file', 'drive_id': 'drive', 'item_id': 'item', 'name': 'Provider.docx'}
    table = env.models['knowledge'].KnowledgeTable()
    await table.update_knowledge_meta_by_id(cloud.id, {'onedrive_sync': {'sources': [source]}})
    path = env.tmp_path / 'onedrive-item.docx'
    path.write_bytes(b'synced')
    files = env.models['files']
    await files.Files.insert_new_file(
        'alice',
        files.FileForm(id='onedrive-item', filename='Provider.docx', path=str(path), meta={'source': 'onedrive'}),
    )
    assert await table.add_file_to_knowledge_by_id(cloud.id, 'onedrive-item', 'alice')
    await env.module.apply(options(env))
    schedules = [
        json.loads(r.content) for r in env.api.requests if r.method == 'POST' and r.url.path == '/v1/schedules'
    ]
    assert {(body['collection_key'], body['scope']['single_file']) for body in schedules} == {(cloud.id, True)}
    submitted = {json.loads(r.content)['documents'][0]['source_id'] for r in job_posts(env)}
    assert submitted.isdisjoint({'cloud', 'onedrive-item'})


@pytest.mark.asyncio
async def test_finished_jobs_count_as_ingested_on_the_next_run(env, monkeypatch, capsys):
    local, _ = await seed_knowledge(env, monkeypatch)
    await env.module.apply(options(env))
    for job_id in list(env.api.jobs):
        env.api.advance(job_id, 'SUCCEEDED')
    jobs = importlib.import_module('open_webui.soev.jobs')
    monkeypatch.setattr(jobs, 'emit_file_status', AsyncMock())
    await jobs.poll_once(env.identity.build_client(), now=10**10)
    capsys.readouterr()
    env.api.requests.clear()
    await env.module.apply(options(env))
    assert job_posts(env) == []
    assert f'{local.id}: failed 2, ingested 5' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_memories_are_reembedded_once_per_row_and_reruns_embed_nothing(env, capsys):
    """Users whose vectors already match their rows are skipped, so a rerun calls no embedding."""
    async with env.engine.begin() as connection:
        await connection.execute(
            sa.text(
                'INSERT INTO memory (id, user_id, type, content, created_at, updated_at) VALUES'
                " ('m1', 'alice', 'context', 'likes tea', 1, 1), ('m2', 'alice', 'context', 'works in Utrecht', 1, 1),"
                " ('m3', 'bob', 'context', 'prefers Dutch', 1, 1)"
            )
        )
    env.vectors.collections['user-memory-bob'] = {'m3': {'id': 'm3'}}
    env.vectors.collections['user-memory-alice'] = {'stale': {'id': 'stale'}}
    await env.module.apply(options(env))
    assert len(env.embedded) == 2 and all('Utrecht' in t or 'tea' in t for t in env.embedded)
    assert set(env.vectors.collections['user-memory-alice']) == {'m1', 'm2'}
    assert '6 memories: 3 rows for 2 users, re-embedded 1 users' in capsys.readouterr().out
    await env.module.apply(options(env))
    assert len(env.embedded) == 2


@pytest.mark.asyncio
async def test_the_embedding_function_is_built_from_the_rag_config_rows(monkeypatch, fake_vector_modules):
    """The Job builds what main.py puts on app.state, from the same persistent keys."""
    memories = importlib.import_module('open_webui.soev.migrate_memories')
    rag = {
        'rag.embedding_engine': 'openai',
        'rag.embedding_model': 'bge-m3',
        'rag.openai.api_base_url': 'https://litellm.invalid/v1',
        'rag.openai.api_key': 'sk-embed',
        'rag.embedding_batch_size': 8,
    }
    monkeypatch.setattr(
        importlib.import_module('open_webui.models.config').Config, 'get_many', AsyncMock(return_value=rag)
    )
    built = {}
    monkeypatch.setattr(
        importlib.import_module('open_webui.retrieval.utils'),
        'get_embedding_function',
        lambda *args, **kwargs: built.update(args=args, kwargs=kwargs) or 'function',
    )
    monkeypatch.setattr(importlib.import_module('open_webui.routers.retrieval'), 'get_ef', lambda engine, model: None)
    assert await memories.build_embedding_function() == 'function'
    assert built['args'] == ('openai', 'bge-m3')
    assert (built['kwargs']['url'], built['kwargs']['key']) == ('https://litellm.invalid/v1', 'sk-embed')
    assert built['kwargs']['embedding_batch_size'] == 8


async def finish_jobs(env, monkeypatch, status='SUCCEEDED'):
    for job_id in list(env.api.jobs):
        if env.api.jobs[job_id]['status'] not in {'SUCCEEDED', 'COMPLETED_WITH_ERRORS'}:
            env.api.advance(job_id, status, item_code='unsupported_media_type', item_detail='no text')
    jobs = importlib.import_module('open_webui.soev.jobs')
    monkeypatch.setattr(jobs, 'emit_file_status', AsyncMock())
    await jobs.poll_once(env.identity.build_client(), now=10**10)


@pytest.mark.asyncio
async def test_reconcile_exits_75_while_ingest_runs_and_0_once_terminal(env, monkeypatch, capsys):
    """Terminal ingest failures are listed and do not block success."""
    local, _ = await seed_knowledge(env, monkeypatch)
    assert await env.module.apply(options(env)) == 75
    await finish_jobs(env, monkeypatch, status='COMPLETED_WITH_ERRORS')
    capsys.readouterr()
    assert await env.module.apply(options(env)) == 0
    output = capsys.readouterr().out
    assert f'{local.id}: failed 7' in output
    assert f'{local.id}/file-0: unsupported_media_type: no text' in output
    assert 'ingest running 0 | ingest failed 7' in output


@pytest.mark.asyncio
async def test_a_missing_memory_vector_exits_1(env, monkeypatch):
    local, _ = await seed_knowledge(env, monkeypatch)
    await env.module.apply(options(env))
    await finish_jobs(env, monkeypatch)
    assert await env.module.apply(options(env)) == 0
    async with env.engine.begin() as connection:
        await connection.execute(
            sa.text(
                'INSERT INTO memory (id, user_id, type, content, created_at, updated_at)'
                " VALUES ('m1', 'alice', 'context', 'x', 1, 1)"
            )
        )
    monkeypatch.setattr(env.vectors, 'upsert', AsyncMock())
    assert await env.module.apply(options(env)) == 1


@pytest.mark.asyncio
async def test_a_missing_collection_exits_1(env, monkeypatch):
    local, _ = await seed_knowledge(env, monkeypatch)
    await env.module.apply(options(env))
    await finish_jobs(env, monkeypatch)
    env.api.failures.clear()
    original = env.api._collections

    def hide(request, body, credential, subject):
        response = original(request, body, credential, subject)
        if request.method != 'GET':
            return response
        data = json.loads(response.content)
        data['data'] = [row for row in data['data'] if row['key'] != local.id]
        return httpx.Response(200, json=data)

    monkeypatch.setattr(env.api, '_collections', hide)
    assert await env.module.apply(options(env)) == 1


@pytest.mark.asyncio
async def test_apply_waits_for_running_ingest_when_asked(env, monkeypatch):
    """With SOEV_V2_WAIT_SECONDS the run polls until the jobs finish instead of exiting 75."""
    await seed_knowledge(env, monkeypatch)
    pauses = []

    async def pause(seconds):
        pauses.append(seconds)
        await finish_jobs(env, monkeypatch)

    monkeypatch.setattr(env.module, '_pause', pause)
    monkeypatch.setenv('SOEV_V2_WAIT_SECONDS', '600')
    assert await env.module.apply(options(env)) == 0
    assert pauses == [env.module.WAIT_POLL_SECONDS]


@pytest.mark.asyncio
async def test_users_are_signed_out_once_per_migration_after_success(env, monkeypatch, capsys):
    """Tokens issued before cutover fail the real revocation check; later runs revoke nothing."""
    await seed_knowledge(env, monkeypatch)
    redis = env.redis
    auth = importlib.import_module('open_webui.utils.auth')
    issued = {'id': 'alice', 'iat': 1}
    assert await env.module.apply(options(env)) == 75
    assert redis.sets == []
    await finish_jobs(env, monkeypatch)
    assert await env.module.apply(options(env)) == 0
    assert sorted(redis.sets) == sorted(f'open-webui:auth:user:{u}:revoked_at' for u in ('alice', 'bob'))
    assert not await auth.is_valid_token(issued, redis)
    assert await auth.is_valid_token({'id': 'alice', 'iat': 10**12}, redis)
    assert '8 sign-out: sessions of 2 users revoked' in capsys.readouterr().out
    assert await env.module.apply(options(env)) == 0
    assert len(redis.sets) == 2
    assert '8 sign-out: already done' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_sign_out_without_redis_fails_loudly(env, monkeypatch):
    monkeypatch.setattr(importlib.import_module('open_webui.utils.redis'), 'get_redis_client', lambda **_: None)
    from open_webui.soev.migrate_state import MigrationError

    with pytest.raises(MigrationError, match='needs Redis'):
        await env.module.apply(options(env))


@pytest.mark.asyncio
async def test_dry_run_prints_every_step_and_writes_nothing(env, monkeypatch, capsys):
    await seed_models(env, monkeypatch)
    async with env.engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM user WHERE id = 'alice'"))
    local, _ = await seed_knowledge(env, monkeypatch)
    before = (await config_rows(env), await dumps(env), await rows(env, 'SELECT * FROM file ORDER BY id'))
    assert await env.module.plan(options(env)) == 0
    after = (await config_rows(env), await dumps(env), await rows(env, 'SELECT * FROM file ORDER BY id'))
    assert after == before
    assert env.api.requests == [] and env.redis.sets == [] and env.embedded == []
    tables = await rows(env, "SELECT name FROM sqlite_master WHERE name LIKE 'owui_v2_migration%'")
    assert tables == []
    output = capsys.readouterr().out
    assert '1 snapshot: would be taken' in output
    assert '2 config switch: openai.enable = false' in output
    assert '3 model ids: map zai-org/GLM-5.3 -> glm-5-3' in output
    assert '3 model ids: chat.chat 3' in output
    assert '3 model ids: unmapped unknown/model' in output
    assert f'5 re-ingest: {local.id}: failed 1, to check 6' in output
    assert '8 sign-out: every user' in output


def test_invalid_input_exits_2_so_the_job_stops_retrying(monkeypatch, capsys):
    module = importlib.import_module('open_webui.soev.migrate')
    monkeypatch.setattr(sys, 'argv', ['migrate', '--apply', '--migration-id', 'v2'])
    monkeypatch.delenv('SOEV_V2_CONFIG', raising=False)
    with pytest.raises(SystemExit) as exited:
        module.main()
    assert exited.value.code == 2
    assert 'SOEV_V2_CONFIG is not set' in capsys.readouterr().err


def test_an_abort_names_the_failing_call_and_problem_without_secrets(monkeypatch, capsys, caplog):
    module = importlib.import_module('open_webui.soev.migrate')
    from open_webui.soev.client import SoevClient

    problem = {'status': 403, 'code': 'policy_forbids', 'detail': 'Schedule scope is disallowed', 'constraint': None}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            403, json=problem, headers={'Content-Type': 'application/problem+json', 'X-Request-ID': 'req-1'}
        )
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient', lambda **kwargs: original(transport=transport, **kwargs)
    )

    async def apply(_options):
        client = SoevClient('https://soev.invalid', 'sk-secret')
        await client.send('POST', '/v1/schedules?x=1', {'scope': {'item_id': 'private'}}, idempotency_key='key-12345')

    monkeypatch.setattr(module, 'options_from_env', lambda **_: None)
    monkeypatch.setattr(module, 'apply', apply)
    monkeypatch.setattr(sys, 'argv', ['migrate', '--apply'])
    with caplog.at_level('WARNING', logger='open_webui.soev.client'), pytest.raises(SystemExit) as exited:
        module.main()
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert 'POST /v1/schedules -> 403 policy_forbids: Schedule scope is disallowed (constraint: None)' in err
    assert 'soev-api response POST /v1/schedules 403 request_id=req-1' in caplog.messages
    assert not any(secret in err + ' '.join(caplog.messages) for secret in ('sk-secret', 'private', 'x=1'))
