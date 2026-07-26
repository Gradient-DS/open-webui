"""Unit tests for env-authoritative config key prefixes on the per-key
``Config`` model (``models/config.py``).

Covers the ``_ENV_AUTHORITATIVE_KEY_PREFIXES`` carve-out: ``sync_daemon.*``
(pre-existing) and ``rag.file.*`` (upload-guard hardening, decision D6) never
persist admin-UI writes to the DB and always resolve from the in-process
``Config.DEFAULTS`` (env-derived) instead — even when a stale DB row exists
from before the carve-out shipped.

Mirrors the async-SQLite + monkeypatched db-context pattern used in
test_invites_model.py / test_password_reset_model.py, adapted to
``models.config``'s own ``get_async_db`` import name.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from open_webui.models import config as config_module
from open_webui.models.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# NOTE: this suite has no `asyncio_mode = auto`; every async test MUST carry
# `@pytest.mark.asyncio` explicitly, or it will be collected-but-not-run (a
# silent false green).


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """In-memory SQLite engine bound to the Config table, with
    ``models.config.get_async_db`` monkeypatched to yield sessions on it."""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Config.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get_async_db():
        async with Session() as s:
            yield s

    monkeypatch.setattr(config_module, 'get_async_db', _get_async_db)
    yield Session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _isolated_defaults(monkeypatch):
    """Every test gets its own DEFAULTS dict so mutations (upsert's
    non-persistent fallback, seed_defaults, etc.) never leak across tests."""
    monkeypatch.setattr(Config, 'DEFAULTS', {})


# --- persistent_enabled_for: the pure boolean check ---------------------------


@pytest.mark.parametrize(
    'key',
    ['rag.file.max_size', 'rag.file.max_count', 'rag.file.allowed_extensions', 'rag.file.some_future_key'],
)
def test_rag_file_prefix_is_env_authoritative(key):
    assert Config.persistent_enabled_for(key) is False


def test_sync_daemon_carve_out_still_works():
    # Preserved unchanged by the refactor into _ENV_AUTHORITATIVE_KEY_PREFIXES.
    assert Config.persistent_enabled_for('sync_daemon.enabled') is False


def test_sibling_rag_key_outside_file_prefix_still_persists():
    # Prefix scoping must be precise: 'rag.file.' does not swallow all of 'rag.*'.
    assert Config.persistent_enabled_for('rag.template') is True


def test_unrelated_normal_key_still_persists():
    assert Config.persistent_enabled_for('ui.default_locale') is True


# --- Behavioral: Config.get ignores a stale DB row for env-authoritative keys -


@pytest.mark.asyncio
async def test_rag_file_key_resolves_from_env_default_even_with_stale_db_row(db_session):
    # Simulate a DB row left over from before this key became env-authoritative
    # (e.g. an old admin-UI write).
    async with db_session() as s:
        s.add(Config(key='rag.file.max_size', value=999, updated_at=int(time.time())))
        await s.commit()

    # Simulate the env-derived default (what DEFAULT_CONFIG seeds at boot).
    Config.DEFAULTS['rag.file.max_size'] = 26214400

    assert await Config.get('rag.file.max_size') == 26214400


@pytest.mark.asyncio
async def test_normal_key_resolves_from_db_row_when_present(db_session):
    async with db_session() as s:
        s.add(Config(key='rag.template', value='from-db', updated_at=int(time.time())))
        await s.commit()

    Config.DEFAULTS['rag.template'] = 'from-env-default'

    assert await Config.get('rag.template') == 'from-db'


# --- Behavioral: Config.upsert (admin-UI write path) never persists ----------


@pytest.mark.asyncio
async def test_rag_file_admin_write_does_not_persist_to_db(db_session):
    Config.DEFAULTS['rag.file.max_count'] = 10

    await Config.upsert({'rag.file.max_count': 5})

    async with db_session() as s:
        row = await s.get(Config, 'rag.file.max_count')
    assert row is None, 'admin-UI write for an env-authoritative key must not create a DB row'


@pytest.mark.asyncio
async def test_normal_key_admin_write_persists_to_db(db_session):
    await Config.upsert({'rag.template': 'admin-set value'})

    async with db_session() as s:
        row = await s.get(Config, 'rag.template')
    assert row is not None
    assert row.value == 'admin-set value'
    assert await Config.get('rag.template') == 'admin-set value'
