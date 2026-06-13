"""Tests for cloud-sync suspension detection in ``models.knowledge``.

A revoked credential makes a sync worker stamp ``suspended_at`` into the KB's
provider-specific sync meta (e.g. ``topdesk_sync``). Four ``KnowledgeTable``
lookups must then treat the KB as suspended so retrieval/agent-search skip it,
the router returns 403, the workspace list shows a badge, and the cleanup
worker eventually auto-deletes it.

These methods previously hardcoded the provider meta-key tuple and omitted
``topdesk_sync``, so TOPdesk KBs were never suspended. The tuple is now the
module-level ``SYNC_PROVIDER_META_KEYS`` constant; these tests parametrize over
every provider (including topdesk) to lock the full set in.

We use an in-memory SQLite DB and monkeypatch the module's
``get_async_db_context`` (mirrors ``test_invites_model``). The ``access_grant``
table is created too because ``get_suspended_expired_knowledge`` round-trips
through ``_to_knowledge_model`` → access-grant lookup.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from open_webui.models import knowledge as knowledge_module
from open_webui.models.access_grants import AccessGrant
from open_webui.models.knowledge import (
    SUSPENSION_TTL_DAYS,
    SYNC_PROVIDER_META_KEYS,
    Knowledge,
    Knowledges,
)

# (knowledge type, sync meta key) for every cloud-sync provider. TOPdesk is the
# regression case this file exists for.
PROVIDER_CASES = [
    ('onedrive', 'onedrive_sync'),
    ('google_drive', 'google_drive_sync'),
    ('confluence', 'confluence_sync'),
    ('topdesk', 'topdesk_sync'),
]


def test_constant_covers_all_providers():
    """The constant must list every provider's meta key — the bug was a
    missing ``topdesk_sync``."""
    assert set(SYNC_PROVIDER_META_KEYS) == {meta_key for _, meta_key in PROVIDER_CASES}


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """In-memory SQLite session with knowledge + access_grant tables; patches
    the knowledge module's ``get_async_db_context``."""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Knowledge.__table__.create)
        await conn.run_sync(AccessGrant.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        if db is not None:
            yield db
        else:
            async with Session() as s:
                yield s

    monkeypatch.setattr(knowledge_module, 'get_async_db_context', _get_async_db_context)
    yield Session
    await engine.dispose()


async def _insert_kb(Session, *, kb_type: str, meta: dict | None) -> str:
    kb_id = str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        s.add(
            Knowledge(
                id=kb_id,
                user_id='owner-1',
                type=kb_type,
                name=f'{kb_type} KB',
                description='',
                meta=meta,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        await s.commit()
    return kb_id


@pytest.mark.asyncio
@pytest.mark.parametrize('kb_type,meta_key', PROVIDER_CASES)
async def test_is_suspended_true_when_meta_has_suspended_at(db_session, kb_type, meta_key):
    kb_id = await _insert_kb(
        db_session,
        kb_type=kb_type,
        meta={meta_key: {'status': 'failed', 'suspended_at': int(time.time())}},
    )
    assert await Knowledges.is_suspended(kb_id) is True


@pytest.mark.asyncio
@pytest.mark.parametrize('kb_type,meta_key', PROVIDER_CASES)
async def test_is_suspended_false_without_suspended_at(db_session, kb_type, meta_key):
    kb_id = await _insert_kb(
        db_session,
        kb_type=kb_type,
        meta={meta_key: {'status': 'completed', 'last_sync_at': 123}},
    )
    assert await Knowledges.is_suspended(kb_id) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('kb_type,meta_key', PROVIDER_CASES)
async def test_get_suspension_info_returns_details(db_session, kb_type, meta_key):
    suspended_at = int(time.time())
    kb_id = await _insert_kb(
        db_session,
        kb_type=kb_type,
        meta={meta_key: {'suspended_at': suspended_at, 'suspended_reason': 'credential_lost'}},
    )
    info = await Knowledges.get_suspension_info(kb_id)
    assert info is not None
    assert info['suspended_at'] == suspended_at
    assert info['reason'] == 'credential_lost'
    assert info['days_remaining'] == SUSPENSION_TTL_DAYS  # suspended just now


@pytest.mark.asyncio
@pytest.mark.parametrize('kb_type,meta_key', PROVIDER_CASES)
async def test_get_suspension_info_none_when_not_suspended(db_session, kb_type, meta_key):
    kb_id = await _insert_kb(
        db_session,
        kb_type=kb_type,
        meta={meta_key: {'status': 'completed'}},
    )
    assert await Knowledges.get_suspension_info(kb_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('kb_type,meta_key', PROVIDER_CASES)
async def test_get_suspended_expired_includes_old_suspensions(db_session, kb_type, meta_key):
    old = int(time.time()) - (SUSPENSION_TTL_DAYS + 1) * 86400
    recent = int(time.time())

    expired_id = await _insert_kb(
        db_session,
        kb_type=kb_type,
        meta={meta_key: {'suspended_at': old}},
    )
    # A recently-suspended KB of the same provider must NOT be returned.
    await _insert_kb(
        db_session,
        kb_type=kb_type,
        meta={meta_key: {'suspended_at': recent}},
    )

    expired = await Knowledges.get_suspended_expired_knowledge()
    expired_ids = {kb.id for kb in expired}
    assert expired_id in expired_ids
    assert len(expired_ids) == 1
