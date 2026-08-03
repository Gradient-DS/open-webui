"""Collection attachments in agent payloads carry a capped file roster.

Every ``type: "collection"`` entry the agent service receives must have
``files`` (at most ``AGENT_KB_FILES_CAP`` ``{"id", "name"}`` records, in KB
insertion order) and ``files_total`` (the KB's true file count) — the agent
side errors loudly when ``files`` is missing.

Two layers are covered:
  * ``enrich_collection_entries`` — the payload-shaping contract, with the
    KB lookup faked.
  * ``Knowledges.get_file_roster_by_id`` — the SQL that backs it, on
    in-memory SQLite (same pattern as test_files_count_by_user_id_model.py).
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from open_webui.models import knowledge as knowledge_module
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge, KnowledgeDirectory, KnowledgeFile, Knowledges
from open_webui.utils import agent as agent_utils
from open_webui.utils.agent import AGENT_KB_FILES_CAP, enrich_collection_entries
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


# --------------------------------------------------------------------------
# enrich_collection_entries — payload shaping
# --------------------------------------------------------------------------


@pytest.fixture
def fake_rosters(monkeypatch):
    """Patch the KB roster lookup with an in-memory ``{kb_id: [(id, name), ...]}``.

    The fake applies the same cap the real query does, so the caller's
    expectations about truncation are exercised end to end.
    """
    rosters: dict[str, list[tuple[str, str]]] = {}

    class _FakeKnowledges:
        @staticmethod
        async def get_file_roster_by_id(knowledge_id: str, limit: int):
            files = rosters.get(knowledge_id, [])
            return files[:limit], len(files)

    monkeypatch.setattr(agent_utils, 'Knowledges', _FakeKnowledges)
    return rosters


@pytest.mark.asyncio
async def test_collection_entry_gains_files_and_total(fake_rosters):
    fake_rosters['kb-1'] = [('f1', 'alpha.pdf'), ('f2', 'beta.docx')]

    entries = await enrich_collection_entries([{'type': 'collection', 'id': 'kb-1', 'name': 'Beleid'}])

    assert entries == [
        {
            'type': 'collection',
            'id': 'kb-1',
            'name': 'Beleid',
            'files': [{'id': 'f1', 'name': 'alpha.pdf'}, {'id': 'f2', 'name': 'beta.docx'}],
            'files_total': 2,
        }
    ]


@pytest.mark.asyncio
async def test_roster_is_capped_but_total_stays_honest(fake_rosters):
    fake_rosters['kb-big'] = [(f'f{i:03d}', f'doc-{i:03d}.pdf') for i in range(120)]

    [entry] = await enrich_collection_entries([{'type': 'collection', 'id': 'kb-big'}])

    assert AGENT_KB_FILES_CAP == 50
    assert len(entry['files']) == 50
    assert entry['files_total'] == 120
    # Insertion order preserved, front-truncated at the cap.
    assert entry['files'][0] == {'id': 'f000', 'name': 'doc-000.pdf'}
    assert entry['files'][-1] == {'id': 'f049', 'name': 'doc-049.pdf'}
    assert [f['id'] for f in entry['files']] == [f'f{i:03d}' for i in range(50)]


@pytest.mark.asyncio
async def test_non_collection_entries_pass_through_untouched(fake_rosters):
    fake_rosters['kb-1'] = [('f1', 'alpha.pdf')]
    file_entry = {'type': 'file', 'id': 'file-9', 'name': 'upload.pdf'}

    entries = await enrich_collection_entries([file_entry, {'type': 'collection', 'id': 'kb-1'}])

    assert entries[0] == {'type': 'file', 'id': 'file-9', 'name': 'upload.pdf'}
    assert 'files_total' not in entries[0]
    assert entries[1]['files_total'] == 1


@pytest.mark.asyncio
async def test_vanished_knowledge_base_gets_empty_roster(fake_rosters):
    [entry] = await enrich_collection_entries([{'type': 'collection', 'id': 'kb-deleted'}])

    assert entry['files'] == []
    assert entry['files_total'] == 0


@pytest.mark.asyncio
async def test_collection_without_id_gets_empty_roster(fake_rosters):
    [entry] = await enrich_collection_entries([{'type': 'collection', 'name': 'orphan'}])

    assert entry['files'] == []
    assert entry['files_total'] == 0


@pytest.mark.asyncio
async def test_empty_and_none_inputs_pass_through(fake_rosters):
    assert await enrich_collection_entries(None) is None
    assert await enrich_collection_entries([]) == []


@pytest.mark.asyncio
async def test_input_entries_are_not_mutated(fake_rosters):
    fake_rosters['kb-1'] = [('f1', 'alpha.pdf')]
    original = {'type': 'collection', 'id': 'kb-1'}

    await enrich_collection_entries([original])

    assert original == {'type': 'collection', 'id': 'kb-1'}


# --------------------------------------------------------------------------
# Knowledges.get_file_roster_by_id — the backing query
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """Async in-memory SQLite with the knowledge/file tables created."""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for table in (Knowledge, KnowledgeDirectory, File, KnowledgeFile):
            await conn.run_sync(table.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        async with Session() as s:
            yield s

    monkeypatch.setattr(knowledge_module, 'get_async_db_context', _get_async_db_context)
    yield Session
    await engine.dispose()


async def _link_files(Session, knowledge_id: str, filenames: list[str]) -> list[str]:
    """Link files to a KB one epoch second apart, oldest first."""
    file_ids = []
    base = int(time.time())
    async with Session() as s:
        for offset, filename in enumerate(filenames):
            file_id = str(uuid.uuid4())
            file_ids.append(file_id)
            s.add(File(id=file_id, user_id='u1', filename=filename, created_at=base, updated_at=base))
            s.add(
                KnowledgeFile(
                    id=str(uuid.uuid4()),
                    knowledge_id=knowledge_id,
                    file_id=file_id,
                    user_id='u1',
                    created_at=base + offset,
                    updated_at=base + offset,
                )
            )
        await s.commit()
    return file_ids


@pytest.mark.asyncio
async def test_roster_query_returns_insertion_order(db_session):
    file_ids = await _link_files(db_session, 'kb-1', ['zeta.pdf', 'alpha.pdf', 'mid.pdf'])

    roster, total = await Knowledges.get_file_roster_by_id('kb-1', AGENT_KB_FILES_CAP)

    assert total == 3
    assert roster == [
        (file_ids[0], 'zeta.pdf'),
        (file_ids[1], 'alpha.pdf'),
        (file_ids[2], 'mid.pdf'),
    ]


@pytest.mark.asyncio
async def test_roster_query_caps_rows_but_counts_all(db_session):
    await _link_files(db_session, 'kb-1', [f'doc-{i:03d}.pdf' for i in range(60)])

    roster, total = await Knowledges.get_file_roster_by_id('kb-1', AGENT_KB_FILES_CAP)

    assert total == 60
    assert len(roster) == 50
    assert [name for _, name in roster] == [f'doc-{i:03d}.pdf' for i in range(50)]


@pytest.mark.asyncio
async def test_roster_query_ignores_other_knowledge_bases(db_session):
    await _link_files(db_session, 'kb-1', ['mine.pdf'])
    await _link_files(db_session, 'kb-2', ['theirs.pdf'])

    roster, total = await Knowledges.get_file_roster_by_id('kb-1', AGENT_KB_FILES_CAP)

    assert total == 1
    assert [name for _, name in roster] == ['mine.pdf']


@pytest.mark.asyncio
async def test_roster_query_on_unknown_knowledge_base(db_session):
    assert await Knowledges.get_file_roster_by_id('kb-gone', AGENT_KB_FILES_CAP) == ([], 0)
