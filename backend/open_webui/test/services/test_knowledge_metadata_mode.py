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
    KnowledgeFileListResponse,
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
    """Insert a file with status/error dual-written to both ``data`` and ``meta``.

    Task 1 ensures all new writes go to both columns; Task 2 backfills existing
    rows.  Tests that seed with this helper mirror real post-backfill data.
    """
    file_id = file_id or str(uuid.uuid4())
    now = int(time.time())
    data: dict = {'status': status, 'content': content}
    if error is not None:
        data['error'] = error
    # Dual-write status/error to meta (mirrors Task 1 write path)
    meta: dict = {'name': filename, 'content_type': 'application/pdf', 'size': 1024, 'status': status}
    if error is not None:
        meta['error'] = error
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id=user_id,
                hash='abc123',
                filename=filename,
                path=f'/files/{filename}',
                data=data,
                meta=meta,
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


async def _insert_file_with_meta_status(
    Session,
    *,
    file_id: str | None = None,
    user_id: str = 'user-1',
    filename: str = 'doc.pdf',
    data_status: str = 'processing',
    meta_status: str = 'completed',
    content: str = 'file content body',
    meta_error: str | None = None,
    data_error: str | None = None,
) -> str:
    """Insert a file where ``meta.status`` and ``data.status`` differ.

    This lets tests prove that the metadata path sources status from ``meta``
    (the small column) and NOT from ``data`` (which holds content and would
    cause a de-TOAST on Postgres).
    """
    file_id = file_id or str(uuid.uuid4())
    now = int(time.time())
    data: dict = {'status': data_status, 'content': content}
    if data_error is not None:
        data['error'] = data_error
    meta: dict = {
        'name': filename,
        'content_type': 'application/pdf',
        'size': 1024,
        'status': meta_status,
    }
    if meta_error is not None:
        meta['error'] = meta_error
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id=user_id,
                hash='abc123',
                filename=filename,
                path=f'/files/{filename}',
                data=data,
                meta=meta,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return file_id


# ---------------------------------------------------------------------------
# TDD Tests — Task 3: status/error sourced from meta (RED before impl)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_mode_sources_status_from_meta_not_data(db_session):
    """Metadata path must return ``meta.status``, NOT ``data.status``.

    The whole point of Task 3: ``data`` holds markdown content and causes a
    Postgres de-TOAST (~1 s for 2500 files); ``meta`` is tiny and avoids it.
    We set ``meta.status='completed'`` and ``data.status='processing'`` so
    that a wrong implementation (reading data) would return 'processing' and
    fail this assertion.
    """
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)
    file_id = await _insert_file_with_meta_status(
        db_session,
        user_id=user_id,
        filename='check.pdf',
        data_status='processing',  # stale / wrong value — must NOT appear
        meta_status='completed',  # correct value — must appear in response
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
    assert item_dict.get('status') == 'completed', (
        f"Expected status='completed' (from meta), got {item_dict.get('status')!r}. "
        'Implementation must read from File.meta, not File.data.'
    )


@pytest.mark.asyncio
async def test_metadata_mode_sources_error_from_meta_not_data(db_session):
    """Metadata path must return ``meta.error``, NOT ``data.error``."""
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)
    file_id = await _insert_file_with_meta_status(
        db_session,
        user_id=user_id,
        filename='broken.pdf',
        data_status='error',
        meta_status='error',
        data_error='old error from data',
        meta_error='real error from meta',  # must be in response
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
    assert item_dict.get('error') == 'real error from meta', (
        f'Expected error from meta, got {item_dict.get("error")!r}. '
        'Implementation must read from File.meta, not File.data.'
    )


@pytest.mark.asyncio
async def test_metadata_mode_status_none_when_meta_key_absent(db_session):
    """When ``meta`` has no ``status`` key, result status should be None (not error).

    This covers a pre-backfill edge case (rows created before Task 1).
    The implementation must NOT fall back to ``data`` — it must return None.
    We insert a file manually with status only in ``data`` (no ``status`` in ``meta``).
    """
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)

    # Insert a file with status ONLY in data, meta has no 'status' key.
    # This simulates a pre-backfill row that Task 2 hasn't touched yet.
    file_id = str(uuid.uuid4())
    now = int(time.time())
    async with db_session() as s:
        s.add(
            File(
                id=file_id,
                user_id=user_id,
                hash='abc123',
                filename='no-meta-status.pdf',
                path='/files/no-meta-status.pdf',
                data={'status': 'completed', 'content': 'some content'},
                # Deliberately no 'status' key in meta
                meta={'name': 'no-meta-status.pdf', 'content_type': 'application/pdf', 'size': 1024},
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
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
    # meta has no status key → status must be None (no fallback to data)
    assert item_dict.get('status') is None, (
        f'Expected status=None (meta key absent, no fallback to data), got {item_dict.get("status")!r}.'
    )


@pytest.mark.asyncio
async def test_metadata_mode_total_correct_no_filter(db_session):
    """``total`` must equal the number of files linked to the KB (no filter)."""
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)
    for i in range(3):
        fid = await _insert_file(
            db_session,
            file_id=str(uuid.uuid4()),
            user_id=user_id,
            filename=f'file-{i}.pdf',
        )
        await _link_file_to_kb(db_session, kb_id=kb_id, file_id=fid, user_id=user_id)

    result = await Knowledges.search_files_by_id(
        kb_id,
        user_id,
        filter={},
        skip=0,
        limit=1,  # only page 1 — total must still be 3
        metadata_only=True,
    )

    assert result.total == 3, f'Expected total=3, got {result.total}'
    assert len(result.items) == 1  # pagination respected


@pytest.mark.asyncio
async def test_metadata_mode_total_correct_with_query_filter(db_session):
    """``total`` must reflect only matched files when a content ``query`` is active."""
    user_id = 'user-1'
    await _insert_user(db_session, user_id=user_id)
    kb_id = await _insert_kb(db_session, user_id=user_id)

    match_id = await _insert_file(
        db_session,
        file_id=str(uuid.uuid4()),
        user_id=user_id,
        filename='match.pdf',
        content='needle in haystack',
    )
    for i in range(2):
        fid = await _insert_file(
            db_session,
            file_id=str(uuid.uuid4()),
            user_id=user_id,
            filename=f'nomatch-{i}.pdf',
            content='irrelevant content',
        )
        await _link_file_to_kb(db_session, kb_id=kb_id, file_id=fid, user_id=user_id)
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=match_id, user_id=user_id)

    result = await Knowledges.search_files_by_id(
        kb_id,
        user_id,
        filter={'query': 'needle'},
        skip=0,
        limit=30,
        metadata_only=True,
    )

    assert result.total == 1, f'Expected total=1 (query matched 1), got {result.total}'
    assert result.items[0].filename == 'match.pdf'


@pytest.mark.asyncio
async def test_metadata_mode_total_correct_with_view_option(db_session):
    """``total`` must reflect only the user's own files when view_option='created'."""
    owner_id = 'user-owner'
    other_id = 'user-other'
    await _insert_user(db_session, user_id=owner_id)
    await _insert_user(db_session, user_id=other_id)
    kb_id = await _insert_kb(db_session, user_id=owner_id)

    own_file = await _insert_file(db_session, file_id=str(uuid.uuid4()), user_id=owner_id, filename='own.pdf')
    shared_file = await _insert_file(db_session, file_id=str(uuid.uuid4()), user_id=other_id, filename='shared.pdf')
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=own_file, user_id=owner_id)
    await _link_file_to_kb(db_session, kb_id=kb_id, file_id=shared_file, user_id=other_id)

    result = await Knowledges.search_files_by_id(
        kb_id,
        owner_id,
        filter={'view_option': 'created'},
        skip=0,
        limit=30,
        metadata_only=True,
    )

    assert result.total == 1, f"Expected total=1 (owner's own file only), got {result.total}"
    assert result.items[0].filename == 'own.pdf'


def test_knowledge_file_list_response_union_serialization():
    """Pin union-resolution behavior of KnowledgeFileListResponse.

    FastAPI re-validates the response through the response_model, which must
    pick the correct union member (FileUserMetadataResponse, not FileUserResponse)
    when the item carries ``status`` but no ``data``.

    Asserts:
    - The serialized item has NO ``data`` key.
    - ``status`` is preserved ('completed').
    - ``error`` is preserved when present.

    This test exercises the worst-case path: model_validate from a plain dict
    forces Pydantic to discriminate the union from raw data, the same way
    FastAPI's response_model re-validation does.
    """
    import time

    now = int(time.time())

    raw = {
        'items': [
            {
                'id': 'file-abc-123',
                'user_id': 'user-1',
                'hash': 'deadbeef',
                'filename': 'report.pdf',
                'meta': {'name': 'report.pdf', 'content_type': 'application/pdf', 'size': 2048},
                'status': 'completed',
                'error': None,
                'created_at': now,
                'updated_at': now,
                'user': None,
                'added_at': now,
            }
        ],
        'total': 1,
    }

    response = KnowledgeFileListResponse.model_validate(raw)
    assert response.total == 1

    item = response.items[0]
    item_dict = item.model_dump()

    # Must not have a ``data`` key — that would indicate FileUserResponse was
    # selected instead of FileUserMetadataResponse, which would leak content in
    # production responses and break the metadata-only contract.
    assert 'data' not in item_dict, (
        f"Union resolved to FileUserResponse (has 'data'); expected FileUserMetadataResponse. "
        f'item_dict keys: {list(item_dict.keys())}'
    )

    # Status and error must survive the round-trip.
    assert item_dict.get('status') == 'completed'
    assert item_dict.get('error') is None
