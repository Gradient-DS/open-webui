"""The v2 migration steps against real SQLite rows and the recorded soev-api contract."""

import importlib
import json
from types import SimpleNamespace

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


@pytest_asyncio.fixture
async def env(identity_config, fake_api, monkeypatch, tmp_path):
    """A file-backed SQLite database behind every session, and soev-api behind the shared fake."""
    identity, _ = identity_config
    database = importlib.import_module('open_webui.internal.db')
    modules = {
        name: importlib.import_module(f'open_webui.models.{name}')
        for name in (
            'config',
            'soev_migration',
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
        modules['soev_migration'].ConfigBackup,
        modules['soev_migration'].ModelIdBackup,
        modules['soev_migration'].MigrationMarker,
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
    async with engine.begin() as connection:
        for key, value in V1_CONFIG.items():
            # Hand-written text, so a byte-for-byte restore cannot pass by re-serializing.
            await connection.execute(
                sa.text('INSERT INTO config (key, value, updated_at) VALUES (:key, :value, :at)'),
                {'key': key, 'value': json.dumps(value, indent=1), 'at': 1000},
            )
    module = importlib.import_module('open_webui.soev.migrate')
    yield SimpleNamespace(
        engine=engine, api=fake_api, identity=identity, module=module, models=modules, tmp_path=tmp_path
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
    snapshot = await rows(env, 'SELECT key, value, updated_at FROM config_backup ORDER BY key')
    stored = {key: (value, at) for key, value, at in before}
    from open_webui.soev.migrate_config import SNAPSHOT_KEYS

    assert [key for key, _, _ in snapshot] == sorted(SNAPSHOT_KEYS)
    for key, value, at in snapshot:
        assert (value, at) == stored.get(key, (None, None))
    async with env.engine.begin() as connection:
        await connection.execute(sa.text("UPDATE config SET value = '\"changed\"' WHERE key = 'webui.url'"))
    await module.apply(options(env))
    assert await rows(env, 'SELECT key, value, updated_at FROM config_backup ORDER BY key') == snapshot
    assert await rows(env, 'SELECT migration_id, step FROM migration_marker WHERE step = :s', s='snapshot') == [
        (MIGRATION, 'snapshot')
    ]


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


@pytest.mark.asyncio
async def test_restore_is_byte_for_byte_on_postgres(env):
    """PostgreSQL json keeps the stored text, so restore must write it back uncast."""
    import os

    url = os.environ.get('SOEV_MIGRATE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set SOEV_MIGRATE_TEST_DATABASE_URL to an isolated PostgreSQL test database')
    from open_webui.soev import migrate_state

    config, state = env.models['config'].Config, env.models['soev_migration']
    engine = create_async_engine(url)
    tables = [config.__table__, state.ConfigBackup.__table__, state.MigrationMarker.__table__]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: sa.MetaData().drop_all(sync, tables=tables))
            await connection.run_sync(lambda sync: tables[0].metadata.create_all(sync, tables=tables))
            for key, value in V1_CONFIG.items():
                await connection.execute(
                    sa.text('INSERT INTO config (key, value, updated_at) VALUES (:key, CAST(:value AS JSON), 1000)'),
                    {'key': key, 'value': json.dumps(value, indent=1)},
                )
        select = 'SELECT key, CAST(value AS TEXT), updated_at FROM config ORDER BY key'
        async with engine.connect() as connection:
            before = (await connection.execute(sa.text(select))).all()
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            from open_webui.soev import migrate_config

            assert await migrate_state.snapshot(db, MIGRATION, migrate_config.SNAPSHOT_KEYS)
            await migrate_config.switch(db, migrate_config.parse_v2_config(json.dumps(V2_CONFIG)))
            await migrate_state.restore_config(db, MIGRATION)
        async with engine.connect() as connection:
            after = (await connection.execute(sa.text(select))).all()
        assert [tuple(row) for row in after] == [tuple(row) for row in before]
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: sa.MetaData().drop_all(sync, tables=tables))
        await engine.dispose()


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
