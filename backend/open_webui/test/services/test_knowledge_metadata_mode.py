"""Tests for metadata_only mode of ``KnowledgeTable.search_files_by_id``.

TDD: these tests were written BEFORE the implementation (RED phase).
After implementation they should all go GREEN.

Scenarios:
  1. metadata_only=True returns items WITHOUT ``data``/``content`` but WITH
     correct top-level ``status`` (from data['status']), correct
     ``filename``/``meta``/``user``/``added_at``, and the correct ``total``.
  2. metadata_only=False (default) is unchanged — items still include ``data``
     and its ``content`` key.
  3. The content-search ``query`` filter still matches on content in metadata
     mode (filter in WHERE works without selecting ``data``).

Fixtures follow the same in-memory SQLite + monkeypatch pattern as
``test_knowledge_suspension.py`` and ``test_invites_model.py``.
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
from open_webui.models.files import File
from open_webui.models.knowledge import (
    Knowledge,
    KnowledgeFile,
    Knowledges,
)
from open_webui.models.users import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """In-memory SQLite session with knowledge, knowledge_file, file, user,
    and access_grant tables; patches the knowledge module's
    ``get_async_db_context``."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_user(Session, *, user_id: str = 'user-1') -> str:
    now = int(time.time())
    async with Session() as s:
        s.add(
            User(
                id=user_id,
                name='Test User',
                email=f'{user_id}@example.com',
                role='user',
                profile_image_url='',
                last_active_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return user_id


async def _insert_kb(Session, *, kb_id: str | None = None, user_id: str = 'user-1') -> str:
    kb_id = kb_id or str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        s.add(
            Knowledge(
                id=kb_id,
                user_id=user_id,
                type='local',
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
    Session,
    *,
    file_id: str | None = None,
    user_id: str = 'user-1',
    filename: str = 'doc.pdf',
    status: str = 'completed',
    content: str = 'hello world content',
    error: str | None = None,
) -> str:
    file_id = file_id or str(uuid.uuid4())
    now = int(time.time())
    data: dict = {'status': status, 'content': content}
    if error is not None:
        data['error'] = error
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id=user_id,
                hash='abc123',
                filename=filename,
                path=f'/files/{filename}',
                data=data,
                meta={'name': filename, 'content_type': 'application/pdf', 'size': 1024},
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return file_id


async def _link_file_to_kb(Session, *, kb_id: str, file_id: str, user_id: str = 'user-1') -> None:
    now = int(time.time())
    async with Session() as s:
        s.add(
            KnowledgeFile(
                id=str(uuid.uuid4()),
                knowledge_id=kb_id,
                file_id=file_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_mode_excludes_data_includes_status(db_session):
    """metadata_only=True: items have no ``data`` field but have top-level
    ``status`` and correct ``filename``/``total``."""
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)
    file_id = await _insert_file(
        db_session,
        user_id=user_id,
        filename='report.pdf',
        status='completed',
        content='large markdown content here',
    )
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=file_id, user_id=user_id)

    result = await Knowledges.search_files_by_id(
        kb_id,
        user_id,
        filter={},
        skip=0,
        limit=30,
        metadata_only=True,
    )

    assert result.total == 1
    assert len(result.items) == 1

    item = result.items[0]
    # Serialize to dict to check exact output (what FastAPI would send)
    item_dict = item.model_dump()

    # MUST NOT contain data or content
    assert 'data' not in item_dict or item_dict.get('data') is None
    assert 'content' not in item_dict

    # MUST have top-level status
    assert item_dict.get('status') == 'completed'

    # MUST have correct filename
    assert item_dict.get('filename') == 'report.pdf'

    # MUST have meta (the file's meta dict)
    assert item_dict.get('meta') is not None

    # MUST have user populated
    assert item_dict.get('user') is not None

    # MUST have added_at (from KnowledgeFile.created_at)
    assert item_dict.get('added_at') is not None


@pytest.mark.asyncio
async def test_default_mode_includes_data_content(db_session):
    """metadata_only=False (default): items still include ``data`` and
    ``data.content`` — default path is unchanged."""
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)
    file_id = await _insert_file(
        db_session,
        user_id=user_id,
        filename='report.pdf',
        status='completed',
        content='this is the full content',
    )
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=file_id, user_id=user_id)

    # Default call (no metadata_only argument)
    result = await Knowledges.search_files_by_id(
        kb_id,
        user_id,
        filter={},
        skip=0,
        limit=30,
    )

    assert result.total == 1
    item = result.items[0]
    item_dict = item.model_dump()

    # MUST have data dict with content
    assert item_dict.get('data') is not None
    assert item_dict['data'].get('content') == 'this is the full content'
    assert item_dict['data'].get('status') == 'completed'


@pytest.mark.asyncio
async def test_content_query_filter_works_in_metadata_mode(db_session):
    """The content-search ``query`` filter works in metadata mode (WHERE clause
    on data['content'] does not require selecting data)."""
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)

    # File whose content matches the query
    matching_file_id = await _insert_file(
        db_session,
        user_id=user_id,
        filename='match.pdf',
        status='completed',
        content='unique_search_term appears here',
    )
    # File that does NOT match
    no_match_file_id = await _insert_file(
        db_session,
        file_id=str(uuid.uuid4()),
        user_id=user_id,
        filename='nomatch.pdf',
        status='completed',
        content='completely different text',
    )
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=matching_file_id, user_id=user_id)
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=no_match_file_id, user_id=user_id)

    result = await Knowledges.search_files_by_id(
        kb_id,
        user_id,
        filter={'query': 'unique_search_term'},
        skip=0,
        limit=30,
        metadata_only=True,
    )

    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].filename == 'match.pdf'

    # Confirm data is absent in metadata mode
    item_dict = result.items[0].model_dump()
    assert 'data' not in item_dict or item_dict.get('data') is None


@pytest.mark.asyncio
async def test_metadata_mode_exposes_error_field(db_session):
    """metadata_only=True: top-level ``error`` is populated from data['error']."""
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)
    file_id = await _insert_file(
        db_session,
        user_id=user_id,
        filename='broken.pdf',
        status='error',
        content='',
        error='parse failed: unsupported format',
    )
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=file_id, user_id=user_id)

    result = await Knowledges.search_files_by_id(
        kb_id,
        user_id,
        filter={},
        skip=0,
        limit=30,
        metadata_only=True,
    )

    assert result.total == 1
    item_dict = result.items[0].model_dump()
    assert item_dict.get('status') == 'error'
    assert item_dict.get('error') == 'parse failed: unsupported format'
