"""Tests for ``KnowledgeTable.search_tree`` — the flat search mode (Phase 4).

Covers filename + content matching, the optional ``filetype`` and ``status``
(bucket) filters, keyset pagination stability, and that each hit carries the
``relative_path`` / ``source_item_id`` breadcrumb fields. Same in-memory async
SQLite fixture as the other knowledge model tests.
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
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge, KnowledgeFile, Knowledges


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Knowledge.__table__.create)
        await conn.run_sync(KnowledgeFile.__table__.create)
        await conn.run_sync(File.__table__.create)

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


async def _add(
    Session,
    *,
    kb_id='kb-1',
    name,
    relative_path,
    source_item_id='S1',
    content='',
    status='completed',
    size=100,
):
    file_id = f'f-{uuid.uuid4().hex[:8]}'
    now = int(time.time())
    meta = {'name': name, 'size': size, 'relative_path': relative_path, 'source_item_id': source_item_id}
    if status is not None:
        meta['status'] = status
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id='u',
                hash='h',
                filename=name,
                path='/p',
                data={'content': content},
                meta=meta,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'u')
    return file_id


async def _seed_kb(Session, kb_id='kb-1'):
    now = int(time.time())
    async with Session() as s:
        s.add(
            Knowledge(
                id=kb_id,
                user_id='u',
                type='google_drive',
                name='t',
                description='',
                meta=None,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_filename_match(db_session):
    await _seed_kb(db_session)
    await _add(db_session, name='budget.pdf', relative_path='docs/budget.pdf')
    await _add(db_session, name='notes.txt', relative_path='docs/notes.txt')

    res = await Knowledges.search_tree('kb-1', q='budget')
    assert [h.name for h in res.items] == ['budget.pdf']
    # Breadcrumb fields carried through.
    assert res.items[0].relative_path == 'docs/budget.pdf'
    assert res.items[0].source_item_id == 'S1'


@pytest.mark.asyncio
async def test_content_match(db_session):
    await _seed_kb(db_session)
    await _add(db_session, name='a.pdf', relative_path='a.pdf', content='the quarterly revenue figures')
    await _add(db_session, name='b.pdf', relative_path='b.pdf', content='unrelated text')

    res = await Knowledges.search_tree('kb-1', q='revenue')
    assert [h.name for h in res.items] == ['a.pdf']


@pytest.mark.asyncio
async def test_filetype_filter(db_session):
    await _seed_kb(db_session)
    await _add(db_session, name='model.ifc', relative_path='bim/model.ifc')
    await _add(db_session, name='plan.pdf', relative_path='bim/plan.pdf')

    res = await Knowledges.search_tree('kb-1', q='', filetype='ifc')
    assert [h.name for h in res.items] == ['model.ifc']
    # With a dot prefix too.
    res2 = await Knowledges.search_tree('kb-1', q='', filetype='.pdf')
    assert [h.name for h in res2.items] == ['plan.pdf']


@pytest.mark.asyncio
async def test_status_bucket_filter_merges_failed_and_error(db_session):
    await _seed_kb(db_session)
    await _add(db_session, name='ok.pdf', relative_path='ok.pdf', status='completed')
    await _add(db_session, name='bad1.pdf', relative_path='bad1.pdf', status='failed')
    await _add(db_session, name='bad2.pdf', relative_path='bad2.pdf', status='error')

    res = await Knowledges.search_tree('kb-1', q='', status='failed')
    assert sorted(h.name for h in res.items) == ['bad1.pdf', 'bad2.pdf']


@pytest.mark.asyncio
async def test_status_pending_bucket(db_session):
    await _seed_kb(db_session)
    await _add(db_session, name='p.pdf', relative_path='p.pdf', status='parsing')
    await _add(db_session, name='d.pdf', relative_path='d.pdf', status='downloading')
    await _add(db_session, name='c.pdf', relative_path='c.pdf', status='completed')

    res = await Knowledges.search_tree('kb-1', q='', status='pending')
    assert sorted(h.name for h in res.items) == ['d.pdf', 'p.pdf']


@pytest.mark.asyncio
async def test_keyset_pagination_no_dup_or_skip(db_session):
    await _seed_kb(db_session)
    for n in ['a.pdf', 'b.pdf', 'c.pdf']:
        await _add(db_session, name=n, relative_path=f'docs/{n}', content='match')

    page1 = await Knowledges.search_tree('kb-1', q='match', limit=2)
    assert [h.name for h in page1.items] == ['a.pdf', 'b.pdf']
    assert page1.has_more is True

    page2 = await Knowledges.search_tree('kb-1', q='match', cursor=page1.next_cursor, limit=2)
    assert [h.name for h in page2.items] == ['c.pdf']
    assert page2.has_more is False

    seen = [h.name for h in page1.items] + [h.name for h in page2.items]
    assert seen == ['a.pdf', 'b.pdf', 'c.pdf']


@pytest.mark.asyncio
async def test_like_wildcard_in_query_is_escaped(db_session):
    await _seed_kb(db_session)
    await _add(db_session, name='100%done.pdf', relative_path='100%done.pdf')
    await _add(db_session, name='xanythingx.pdf', relative_path='xanythingx.pdf')

    # '100%' must match literally, not as a wildcard that also hits the decoy.
    res = await Knowledges.search_tree('kb-1', q='100%')
    assert [h.name for h in res.items] == ['100%done.pdf']


@pytest.mark.asyncio
async def test_empty_query_returns_all(db_session):
    await _seed_kb(db_session)
    await _add(db_session, name='a.pdf', relative_path='a.pdf')
    await _add(db_session, name='b.pdf', relative_path='b.pdf')

    res = await Knowledges.search_tree('kb-1', q='')
    assert sorted(h.name for h in res.items) == ['a.pdf', 'b.pdf']
