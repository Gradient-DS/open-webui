"""Tests for the denormalized path columns on ``knowledge_file`` (Phase 1).

Covers the write-path maintenance that keeps ``knowledge_file.relative_path``
and ``knowledge_file.source_item_id`` mirrored from ``file.meta``:

  * ``add_file_to_knowledge_by_id`` populates the columns from the file's meta
    on first link (cloud-sync stub / router add-file / ingest-create all route
    through here).
  * Loose local uploads (no provider path in meta) leave the columns NULL.
  * Re-linking the same file (a re-sync) upserts — refreshing the columns from
    the file's *current* meta, so a moved file's tree position is corrected.
  * ``set_path_fields_by_file_id`` (used by the ``/ingest`` existing-file
    branch) refreshes every link row for a file in one statement.

Fixtures mirror ``test_knowledge_metadata_mode.py`` (in-memory async SQLite +
monkeypatched ``get_async_db_context``).
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from open_webui.models import knowledge as knowledge_module
from open_webui.models.files import File
from open_webui.models.knowledge import (
    Knowledge,
    KnowledgeFile,
    Knowledges,
    _path_fields_from_meta,
)
from open_webui.models.users import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """In-memory SQLite session with knowledge, knowledge_file, file, user
    tables; patches the knowledge module's ``get_async_db_context``."""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Knowledge.__table__.create)
        await conn.run_sync(KnowledgeFile.__table__.create)
        await conn.run_sync(File.__table__.create)
        await conn.run_sync(User.__table__.create)

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_kb(Session, *, kb_id: str | None = None, user_id: str = 'user-1') -> str:
    kb_id = kb_id or str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        s.add(
            Knowledge(
                id=kb_id,
                user_id=user_id,
                type='google_drive',
                name='Test KB',
                description='',
                meta=None,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        await s.commit()
    return kb_id


async def _insert_file(
    Session, *, file_id: str | None = None, user_id: str = 'user-1', meta: dict | None = None
) -> str:
    file_id = file_id or str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id=user_id,
                hash='abc123',
                filename=(meta or {}).get('name', 'doc.pdf'),
                path='/files/doc.pdf',
                data={},
                meta=meta,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return file_id


async def _get_link(Session, *, kb_id: str, file_id: str) -> KnowledgeFile | None:
    async with Session() as s:
        result = await s.execute(select(KnowledgeFile).filter_by(knowledge_id=kb_id, file_id=file_id))
        return result.scalars().first()


# ---------------------------------------------------------------------------
# _path_fields_from_meta (pure helper)
# ---------------------------------------------------------------------------


def test_path_fields_from_meta_extracts_both():
    assert _path_fields_from_meta({'relative_path': 'a/b/c.pdf', 'source_item_id': 'src-1'}) == ('a/b/c.pdf', 'src-1')


def test_path_fields_from_meta_none_and_non_dict():
    assert _path_fields_from_meta(None) == (None, None)
    assert _path_fields_from_meta('not-a-dict') == (None, None)
    assert _path_fields_from_meta({}) == (None, None)


def test_path_fields_from_meta_coerces_non_strings_to_none():
    # A malformed provider payload (e.g. a list) must never poison the columns.
    assert _path_fields_from_meta({'relative_path': ['a', 'b'], 'source_item_id': 123}) == (None, None)


# ---------------------------------------------------------------------------
# add_file_to_knowledge_by_id — write-path population
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_file_populates_path_columns_from_meta(db_session):
    kb_id = await _insert_kb(db_session)
    file_id = await _insert_file(
        db_session,
        meta={'name': 'report.pdf', 'relative_path': 'folder/sub/report.pdf', 'source_item_id': 'drive-folder-1'},
    )

    link = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')

    assert link is not None
    assert link.relative_path == 'folder/sub/report.pdf'
    assert link.source_item_id == 'drive-folder-1'

    # Persisted on the row, not just the returned model.
    row = await _get_link(db_session, kb_id=kb_id, file_id=file_id)
    assert row.relative_path == 'folder/sub/report.pdf'
    assert row.source_item_id == 'drive-folder-1'


@pytest.mark.asyncio
async def test_add_file_loose_upload_leaves_columns_null(db_session):
    """A local upload with no relative_path/source_item_id → NULL columns."""
    kb_id = await _insert_kb(db_session)
    file_id = await _insert_file(db_session, meta={'name': 'loose.txt', 'content_type': 'text/plain', 'size': 12})

    link = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')

    assert link is not None
    assert link.relative_path is None
    assert link.source_item_id is None


@pytest.mark.asyncio
async def test_add_file_null_meta_leaves_columns_null(db_session):
    kb_id = await _insert_kb(db_session)
    file_id = await _insert_file(db_session, meta=None)

    link = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')

    assert link is not None
    assert link.relative_path is None
    assert link.source_item_id is None


@pytest.mark.asyncio
async def test_relink_refreshes_columns_when_file_moves(db_session):
    """Re-linking (re-sync) upserts: columns track the file's *current* meta."""
    kb_id = await _insert_kb(db_session)
    file_id = await _insert_file(
        db_session,
        meta={'name': 'doc.pdf', 'relative_path': 'old/doc.pdf', 'source_item_id': 'src-1'},
    )

    first = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')
    assert first.relative_path == 'old/doc.pdf'

    # The file moves folders — its meta is updated by the sync worker.
    async with db_session() as s:
        f = await s.get(File, file_id)
        f.meta = {'name': 'doc.pdf', 'relative_path': 'new/place/doc.pdf', 'source_item_id': 'src-2'}
        await s.commit()

    second = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')
    assert second.relative_path == 'new/place/doc.pdf'
    assert second.source_item_id == 'src-2'

    # No duplicate link rows were created (upsert, not insert).
    async with db_session() as s:
        rows = (await s.execute(select(KnowledgeFile).filter_by(knowledge_id=kb_id, file_id=file_id))).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# set_path_fields_by_file_id — /ingest existing-file refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_path_fields_updates_all_links_for_file(db_session):
    """A file linked to two KBs gets both link rows refreshed in one call."""
    kb_a = await _insert_kb(db_session, kb_id='kb-a')
    kb_b = await _insert_kb(db_session, kb_id='kb-b')
    file_id = await _insert_file(db_session, meta={'name': 'd.pdf', 'relative_path': 'stale/d.pdf'})

    await Knowledges.add_file_to_knowledge_by_id('kb-a', file_id, 'user-1')
    await Knowledges.add_file_to_knowledge_by_id('kb-b', file_id, 'user-1')

    ok = await Knowledges.set_path_fields_by_file_id(
        file_id, {'relative_path': 'fresh/folder/d.pdf', 'source_item_id': 'src-9'}
    )
    assert ok is True

    for kb_id in ('kb-a', 'kb-b'):
        row = await _get_link(db_session, kb_id=kb_id, file_id=file_id)
        assert row.relative_path == 'fresh/folder/d.pdf'
        assert row.source_item_id == 'src-9'


@pytest.mark.asyncio
async def test_set_path_fields_no_links_is_noop(db_session):
    """No knowledge_file rows for the file → still succeeds (no-op)."""
    file_id = await _insert_file(db_session, meta={'relative_path': 'x/y.pdf'})
    ok = await Knowledges.set_path_fields_by_file_id(file_id, {'relative_path': 'x/y.pdf'})
    assert ok is True
