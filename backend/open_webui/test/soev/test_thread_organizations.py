"""Use the organisation repository with every chat content column/table absent."""

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
import sqlalchemy as sa
from open_webui.soev import threads
from open_webui.soev.thread_organizations import ThreadOrganizations
from open_webui.soev.threads import Organization, ThreadError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def organization_store(monkeypatch):
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    engine = create_async_engine('sqlite+aiosqlite://')
    statements = []
    sa.event.listen(
        engine.sync_engine, 'before_cursor_execute', lambda conn, cursor, sql, *args: statements.append(sql)
    )
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("""CREATE TABLE chat (
            id TEXT PRIMARY KEY, user_id TEXT, thread_id TEXT, folder_id TEXT,
            pinned BOOLEAN, archived BOOLEAN, last_read_at BIGINT, created_at BIGINT, updated_at BIGINT
        )""")
        )
        await conn.execute(sa.text('CREATE TABLE chat_thread (thread_id TEXT PRIMARY KEY, chat_id TEXT)'))
        await conn.execute(sa.text("INSERT INTO chat (id, user_id) VALUES ('fictional-legacy', 'fictional-fern')"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session():
        async with sessions() as db:
            yield db

    yield ThreadOrganizations(session), session, statements
    await engine.dispose()


@pytest.mark.asyncio
async def test_organization_survives_restart_and_is_owner_scoped(organization_store):
    repository, session, statements = organization_store
    record = Organization(id='fictional-chat', thread_id='fictional-root', folder_id='fictional-folder')
    await repository.put('fictional-fern', record)
    await repository.put(
        'fictional-fern',
        record.model_copy(
            update={
                'thread_id': 'fictional-edit',
                'branch_thread_ids': ['fictional-root', 'fictional-edit'],
                'pinned': True,
                'last_read_at': 42,
            }
        ),
    )
    reloaded = ThreadOrganizations(session)
    rows = await reloaded.list('fictional-fern')
    assert len(rows) == 1
    assert rows[0].thread_id == 'fictional-edit' and rows[0].pinned
    assert set(rows[0].branch_thread_ids) == {'fictional-root', 'fictional-edit'}
    assert rows[0].last_read_at == 42
    assert await reloaded.list('fictional-other') == []
    with pytest.raises(ThreadError):
        await reloaded.put('fictional-other', record)
    with pytest.raises(ThreadError):
        await reloaded.put('fictional-fern', record.model_copy(update={'id': 'fictional-legacy'}))
    await reloaded.remove('fictional-other', record.id)
    assert len(await reloaded.list('fictional-fern')) == 1
    await reloaded.remove('fictional-fern', record.id)
    assert await reloaded.list('fictional-fern') == []
    async with session() as db:
        assert (await db.execute(sa.text('SELECT COUNT(*) FROM chat_thread'))).scalar() == 0
        assert (await db.execute(sa.text('SELECT id FROM chat'))).scalar() == 'fictional-legacy'
    assert all('chat_message' not in sql and 'summary' not in sql for sql in statements)


@pytest.mark.asyncio
async def test_stale_organization_update_does_not_lose_owned_branches(organization_store):
    repository, session, statements = organization_store
    record = Organization(id='fictional-chat', thread_id='fictional-root')
    await repository.put('fictional-fern', record)
    await repository.put(
        'fictional-fern',
        record.model_copy(
            update={'thread_id': 'fictional-child', 'branch_thread_ids': ['fictional-root', 'fictional-child']}
        ),
    )
    await repository.put('fictional-fern', record.model_copy(update={'pinned': True}))
    rows = await repository.list('fictional-fern')
    assert set(rows[0].branch_thread_ids) == {'fictional-root', 'fictional-child'}
