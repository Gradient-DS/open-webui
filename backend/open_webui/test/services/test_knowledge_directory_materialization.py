"""Tests for the P2-8 Phase 1 backend: reverse bridge (relative_path →
knowledge_directory materialization), directory rollups, directory-delete
cascade parity, and remove-source subtree cleanup.

Fixtures mirror ``test_knowledge_file_path_columns.py`` (in-memory async
SQLite + monkeypatched ``get_async_db_context`` in every module that opens
sessions).
These tests cover the SQL class that soev-api replaced for the product.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from open_webui.models import files as files_module
from open_webui.models import knowledge as knowledge_module
from open_webui.models.files import File
from open_webui.models.knowledge import (
    Knowledge,
    KnowledgeDirectory,
    KnowledgeFile,
    Knowledges,
    _directory_segments_for_link,
)
from open_webui.models.users import User

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """In-memory SQLite session; patches get_async_db_context for the
    knowledge AND files modules (DeletionService goes through both)."""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Knowledge.__table__.create)
        await conn.run_sync(KnowledgeDirectory.__table__.create)
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
    monkeypatch.setitem(globals(), 'Knowledges', knowledge_module.KnowledgeTable())
    monkeypatch.setattr(files_module, 'get_async_db_context', _get_async_db_context)
    yield Session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FOLDER_SOURCE = {'item_id': 'SRC-1', 'name': 'Alpha Folder', 'type': 'folder'}


def _drive_meta(sources=None, meta_key='google_drive_sync'):
    return {meta_key: {'sources': sources if sources is not None else [dict(_FOLDER_SOURCE)]}}


async def _insert_kb(Session, *, kb_id=None, meta=None, user_id='user-1'):
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
                meta=meta,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        await s.commit()
    return kb_id


async def _insert_file(Session, *, file_id=None, user_id='user-1', meta=None, hash='h1'):
    file_id = file_id or str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id=user_id,
                hash=hash,
                filename=(meta or {}).get('name', 'doc.pdf'),
                path=f'/files/{file_id}.pdf',
                data={},
                meta=meta,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return file_id


async def _dirs(Session, kb_id):
    """All directories of a KB as {id: (parent_id, name)}."""
    async with Session() as s:
        rows = (await s.execute(select(KnowledgeDirectory).filter_by(knowledge_id=kb_id))).scalars().all()
        return {d.id: (d.parent_id, d.name) for d in rows}


async def _link(Session, kb_id, file_id):
    async with Session() as s:
        return (await s.execute(select(KnowledgeFile).filter_by(knowledge_id=kb_id, file_id=file_id))).scalars().first()


async def _dir_path(Session, directory_id):
    """Walk parents to build 'root/sub/leaf' for assertions."""
    segments = []
    async with Session() as s:
        current = directory_id
        while current:
            d = (await s.execute(select(KnowledgeDirectory).filter_by(id=current))).scalars().first()
            if not d:
                break
            segments.insert(0, d.name)
            current = d.parent_id
    return '/'.join(segments)


# ---------------------------------------------------------------------------
# _directory_segments_for_link (pure helper)
# ---------------------------------------------------------------------------


def test_segments_folder_source_gets_wrapper():
    meta = _drive_meta()
    assert _directory_segments_for_link(meta, 'SRC-1', 'docs/sub/report.pdf') == (
        ['Alpha Folder', 'docs', 'sub'],
        'google_drive_sync',
    )


def test_segments_folder_source_root_file_still_wrapped():
    assert _directory_segments_for_link(_drive_meta(), 'SRC-1', 'report.pdf') == (
        ['Alpha Folder'],
        'google_drive_sync',
    )


def test_segments_file_source_gets_no_wrapper():
    meta = _drive_meta(sources=[{'item_id': 'F1', 'name': 'picked.pdf', 'type': 'file'}])
    assert _directory_segments_for_link(meta, 'F1', 'picked.pdf') == ([], None)


def test_segments_unknown_source_skipped():
    assert _directory_segments_for_link(_drive_meta(), 'UNKNOWN', 'a/b.pdf') is None
    assert _directory_segments_for_link(None, 'SRC-1', 'a/b.pdf') is None


def test_segments_name_fallback_to_item_path_basename():
    meta = _drive_meta(sources=[{'item_id': 'S', 'type': 'folder', 'item_path': '/drives/x/Projects/'}])
    assert _directory_segments_for_link(meta, 'S', 'a.pdf') == (['Projects'], 'google_drive_sync')


# ---------------------------------------------------------------------------
# Reverse bridge via add_file_to_knowledge_by_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_materializes_chain_and_stamps_root(db_session):
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    file_id = await _insert_file(
        db_session, meta={'name': 'r.pdf', 'relative_path': 'docs/sub/r.pdf', 'source_item_id': 'SRC-1'}
    )

    link = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')

    assert link.directory_id is not None
    assert await _dir_path(db_session, link.directory_id) == 'Alpha Folder/docs/sub'
    directories = await _dirs(db_session, kb_id)
    assert len(directories) == 3  # Alpha Folder, docs, sub — no duplicates

    # root_directory_id stamped on the source registry entry
    kb = await Knowledges.get_knowledge_by_id(kb_id)
    entry = kb.meta['google_drive_sync']['sources'][0]
    root_id = next(did for did, (parent, name) in directories.items() if parent is None and name == 'Alpha Folder')
    assert entry['root_directory_id'] == root_id


@pytest.mark.asyncio
async def test_second_file_reuses_chain_idempotently(db_session):
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    f1 = await _insert_file(
        db_session, meta={'name': 'a.pdf', 'relative_path': 'docs/a.pdf', 'source_item_id': 'SRC-1'}
    )
    f2 = await _insert_file(
        db_session, meta={'name': 'b.pdf', 'relative_path': 'docs/b.pdf', 'source_item_id': 'SRC-1'}
    )

    l1 = await Knowledges.add_file_to_knowledge_by_id(kb_id, f1, 'user-1')
    l2 = await Knowledges.add_file_to_knowledge_by_id(kb_id, f2, 'user-1')

    assert l1.directory_id == l2.directory_id
    assert len(await _dirs(db_session, kb_id)) == 2  # Alpha Folder + docs, once each

    # Re-linking is also a no-op on the directory rows.
    await Knowledges.add_file_to_knowledge_by_id(kb_id, f1, 'user-1')
    assert len(await _dirs(db_session, kb_id)) == 2


@pytest.mark.asyncio
async def test_file_source_and_unknown_source_do_not_materialize(db_session):
    meta = _drive_meta(sources=[{'item_id': 'F1', 'name': 'picked.pdf', 'type': 'file'}])
    kb_id = await _insert_kb(db_session, meta=meta)
    picked = await _insert_file(
        db_session, meta={'name': 'picked.pdf', 'relative_path': 'picked.pdf', 'source_item_id': 'F1'}
    )
    orphan = await _insert_file(
        db_session, meta={'name': 'o.pdf', 'relative_path': 'x/o.pdf', 'source_item_id': 'GONE'}
    )

    picked_link = await Knowledges.add_file_to_knowledge_by_id(kb_id, picked, 'user-1')
    orphan_link = await Knowledges.add_file_to_knowledge_by_id(kb_id, orphan, 'user-1')

    assert picked_link.directory_id is None  # file source: no wrapper (PR #235)
    assert orphan_link.directory_id is None  # unknown source: skipped
    assert await _dirs(db_session, kb_id) == {}


@pytest.mark.asyncio
async def test_caller_directory_id_wins_over_bridge(db_session):
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    now = int(time.time())
    async with db_session() as s:
        s.add(
            KnowledgeDirectory(
                id='explicit-dir',
                knowledge_id=kb_id,
                parent_id=None,
                name='Explicit',
                user_id='user-1',
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    file_id = await _insert_file(
        db_session, meta={'name': 'a.pdf', 'relative_path': 'docs/a.pdf', 'source_item_id': 'SRC-1'}
    )

    link = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1', directory_id='explicit-dir')

    assert link.directory_id == 'explicit-dir'
    # Bridge did not run: no derived chain was created.
    assert set(await _dirs(db_session, kb_id)) == {'explicit-dir'}


@pytest.mark.asyncio
async def test_moved_file_relink_tracks_new_chain(db_session):
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    file_id = await _insert_file(
        db_session, meta={'name': 'a.pdf', 'relative_path': 'old/a.pdf', 'source_item_id': 'SRC-1'}
    )
    first = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')
    assert await _dir_path(db_session, first.directory_id) == 'Alpha Folder/old'

    async with db_session() as s:
        f = await s.get(File, file_id)
        f.meta = {'name': 'a.pdf', 'relative_path': 'new/a.pdf', 'source_item_id': 'SRC-1'}
        await s.commit()

    second = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')
    assert await _dir_path(db_session, second.directory_id) == 'Alpha Folder/new'


@pytest.mark.asyncio
async def test_loose_local_file_unaffected(db_session):
    """No path identity → bridge never runs; placement-preservation semantics
    of the daemon /stage flow stay exactly as before."""
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    file_id = await _insert_file(db_session, meta={'name': 'loose.txt'})

    link = await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')
    assert link.directory_id is None
    assert await _dirs(db_session, kb_id) == {}


# ---------------------------------------------------------------------------
# Reverse bridge via set_path_fields_by_file_id (/ingest existing-file branch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_path_fields_materializes_per_kb(db_session):
    kb_a = await _insert_kb(db_session, kb_id='kb-a', meta=_drive_meta())
    kb_b = await _insert_kb(
        db_session,
        kb_id='kb-b',
        meta=_drive_meta(sources=[{'item_id': 'SRC-1', 'name': 'Beta Root', 'type': 'folder'}]),
    )
    file_id = await _insert_file(db_session, meta={'name': 'd.pdf'})
    await Knowledges.add_file_to_knowledge_by_id('kb-a', file_id, 'user-1')
    await Knowledges.add_file_to_knowledge_by_id('kb-b', file_id, 'user-1')

    ok = await Knowledges.set_path_fields_by_file_id(
        file_id, {'relative_path': 'fresh/d.pdf', 'source_item_id': 'SRC-1'}
    )
    assert ok is True

    link_a = await _link(db_session, 'kb-a', file_id)
    link_b = await _link(db_session, 'kb-b', file_id)
    assert await _dir_path(db_session, link_a.directory_id) == 'Alpha Folder/fresh'
    assert await _dir_path(db_session, link_b.directory_id) == 'Beta Root/fresh'


# ---------------------------------------------------------------------------
# Race-safety of _find_or_create_directory
# ---------------------------------------------------------------------------


class _RacingSession:
    """Delegates to a real session but hijacks the FIRST commit to simulate a
    lost insert race: the pending insert is discarded, the winner row is
    committed instead, and IntegrityError is raised — exactly what the unique
    constraint produces when a concurrent writer got there first."""

    def __init__(self, session, winner_row):
        self._session = session
        self._winner_row = winner_row
        self._raced = False

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self):
        if not self._raced:
            self._raced = True
            await self._session.rollback()  # discard our pending directory add
            self._session.add(self._winner_row)
            await self._session.commit()
            raise IntegrityError('UNIQUE constraint failed', None, Exception('simulated race'))
        await self._session.commit()


@pytest.mark.asyncio
async def test_lost_insert_race_retries_and_adopts_winner(db_session):
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    now = int(time.time())
    winner = KnowledgeDirectory(
        id='winner-dir',
        knowledge_id=kb_id,
        parent_id=None,
        name='Docs',
        user_id='user-2',
        created_at=now - 5,
        updated_at=now - 5,
    )
    async with db_session() as s:
        racing = _RacingSession(s, winner)
        directory_id = await Knowledges._find_or_create_directory(racing, kb_id, None, 'Docs', 'user-1')

    assert directory_id == 'winner-dir'
    directories = await _dirs(db_session, kb_id)
    assert list(directories) == ['winner-dir']  # no duplicate row survived


class _TwinSession:
    """Delegates to a real session but injects an OLDER same-named root right
    before the first commit — the NULL-parent twin the unique constraint cannot
    catch (NULLs compare distinct). The convergence re-select must then drop
    our row and adopt the older canonical one."""

    def __init__(self, session, twin_row):
        self._session = session
        self._twin_row = twin_row
        self._injected = False

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self):
        if not self._injected:
            self._injected = True
            self._session.add(self._twin_row)
        await self._session.commit()


@pytest.mark.asyncio
async def test_null_parent_twin_converges_on_canonical(db_session):
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    now = int(time.time())
    twin = KnowledgeDirectory(
        id='aaa-older-twin',
        knowledge_id=kb_id,
        parent_id=None,
        name='Docs',
        user_id='user-2',
        created_at=now - 60,
        updated_at=now - 60,
    )
    async with db_session() as s:
        directory_id = await Knowledges._find_or_create_directory(_TwinSession(s, twin), kb_id, None, 'Docs', 'user-1')

    assert directory_id == 'aaa-older-twin'
    directories = await _dirs(db_session, kb_id)
    assert list(directories) == ['aaa-older-twin']  # our twin was deleted


# ---------------------------------------------------------------------------
# Directory rollups (files-response decision 3)
# ---------------------------------------------------------------------------


async def _seed_rollup_kb(Session):
    kb_id = await _insert_kb(Session, meta=_drive_meta())
    files = [
        ('a.pdf', 'docs/a.pdf', 'completed'),
        ('b.pdf', 'docs/b.pdf', 'failed'),
        ('c.pdf', 'docs/sub/c.pdf', 'error'),
        ('d.pdf', 'docs/sub/d.pdf', 'pending'),
        ('e.png', 'images/e.png', None),
        ('root.pdf', 'root.pdf', 'completed'),
    ]
    for name, rel, status in files:
        meta = {'name': name, 'relative_path': rel, 'source_item_id': 'SRC-1'}
        if status is not None:
            meta['status'] = status
        file_id = await _insert_file(Session, meta=meta)
        await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')
    return kb_id


@pytest.mark.asyncio
async def test_directory_rollups_recursive_counts_and_buckets(db_session):
    kb_id = await _seed_rollup_kb(db_session)
    directories = await _dirs(db_session, kb_id)
    root_id = next(d for d, (p, n) in directories.items() if p is None and n == 'Alpha Folder')
    docs_id = next(d for d, (p, n) in directories.items() if n == 'docs')

    rollups = await Knowledges.get_directory_rollups(kb_id, [root_id])
    assert rollups[root_id]['child_count'] == 6
    assert rollups[root_id]['status_counts'] == {'pending': 1, 'completed': 2, 'failed': 2, 'unknown': 1}

    rollups = await Knowledges.get_directory_rollups(kb_id, [docs_id])
    assert rollups[docs_id]['child_count'] == 4
    assert rollups[docs_id]['status_counts'] == {'pending': 1, 'completed': 1, 'failed': 2, 'unknown': 0}


@pytest.mark.asyncio
async def test_files_response_directories_carry_rollups(db_session):
    kb_id = await _seed_rollup_kb(db_session)
    directories = await _dirs(db_session, kb_id)
    root_id = next(d for d, (p, n) in directories.items() if p is None and n == 'Alpha Folder')

    response = await Knowledges.search_files_by_id(kb_id, 'user-1', filter={}, metadata_only=True)

    entry = next(d for d in response.directories if d.id == root_id)
    assert entry.child_count == 6
    assert entry.status_counts == {'pending': 1, 'completed': 2, 'failed': 2, 'unknown': 1}
    # Level scoping: root level lists only the source root directory.
    assert [d.id for d in response.directories] == [root_id]


@pytest.mark.asyncio
async def test_rollups_empty_directory_absent(db_session):
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    now = int(time.time())
    async with db_session() as s:
        s.add(
            KnowledgeDirectory(
                id='empty-dir',
                knowledge_id=kb_id,
                parent_id=None,
                name='Empty',
                user_id='user-1',
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()

    assert await Knowledges.get_directory_rollups(kb_id, ['empty-dir']) == {}

    response = await Knowledges.search_files_by_id(kb_id, 'user-1', filter={}, metadata_only=True)
    entry = next(d for d in response.directories if d.id == 'empty-dir')
    assert entry.child_count == 0
    assert entry.status_counts == {'pending': 0, 'completed': 0, 'failed': 0, 'unknown': 0}


# ---------------------------------------------------------------------------
# DeletionService.delete_directory — full-cascade parity (ledger #4)
# ---------------------------------------------------------------------------


class _FakeVectorClient:
    def __init__(self):
        self.deleted_filters = []  # (collection, filter)
        self.deleted_collections = []

    async def delete(self, collection_name, filter=None, ids=None):
        self.deleted_filters.append((collection_name, filter))

    async def delete_collection(self, collection_name):
        self.deleted_collections.append(collection_name)

    async def has_collection(self, collection_name):
        return True


class _FakeStorage:
    def __init__(self):
        self.deleted_paths = []

    def delete_file(self, path):
        self.deleted_paths.append(path)

    def delete_files(self, paths):
        self.deleted_paths.extend(paths)
        return len(paths)


@pytest_asyncio.fixture
async def deletion_env(db_session, monkeypatch):
    """DeletionService with vector + storage + chat-reference seams faked."""
    from open_webui.services.deletion import service as deletion_service_module
    from open_webui.services.sync import router as sync_router_module

    monkeypatch.setattr(deletion_service_module, 'Knowledges', Knowledges)
    monkeypatch.setattr(sync_router_module, 'Knowledges', Knowledges)

    vector = _FakeVectorClient()
    storage = _FakeStorage()
    monkeypatch.setattr(deletion_service_module, 'ASYNC_VECTOR_DB_CLIENT', vector)
    monkeypatch.setattr(deletion_service_module, 'Storage', storage)

    from open_webui.models.chats import Chats

    async def _no_chat_refs(self, file_ids, db=None):
        return set()

    monkeypatch.setattr(type(Chats), 'get_referenced_file_ids', _no_chat_refs)
    return vector, storage


@pytest.mark.asyncio
async def test_delete_directory_contents_full_cascade(db_session, deletion_env):
    from open_webui.services.deletion import DeletionService

    vector, storage = deletion_env
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    f1 = await _insert_file(
        db_session, meta={'name': 'a.pdf', 'relative_path': 'docs/a.pdf', 'source_item_id': 'SRC-1'}, hash='h-a'
    )
    f2 = await _insert_file(
        db_session, meta={'name': 'c.pdf', 'relative_path': 'docs/sub/c.pdf', 'source_item_id': 'SRC-1'}, hash='h-c'
    )
    await Knowledges.add_file_to_knowledge_by_id(kb_id, f1, 'user-1')
    await Knowledges.add_file_to_knowledge_by_id(kb_id, f2, 'user-1')
    directories = await _dirs(db_session, kb_id)
    docs_id = next(d for d, (p, n) in directories.items() if n == 'docs')

    report = await DeletionService.delete_directory(kb_id, docs_id, move_files_to_parent=False)

    assert not report.has_errors
    # Directory subtree gone (docs + sub), source root remains.
    remaining = await _dirs(db_session, kb_id)
    assert {name for _, name in remaining.values()} == {'Alpha Folder'}
    # KB-collection vectors deleted by file_id AND hash for both files.
    assert (kb_id, {'file_id': f1}) in vector.deleted_filters
    assert (kb_id, {'hash': 'h-a'}) in vector.deleted_filters
    assert (kb_id, {'file_id': f2}) in vector.deleted_filters
    assert (kb_id, {'hash': 'h-c'}) in vector.deleted_filters
    # file-{id} collections + storage objects + File rows gone.
    assert sorted(vector.deleted_collections) == sorted([f'file-{f1}', f'file-{f2}'])
    assert sorted(storage.deleted_paths) == sorted([f'/files/{f1}.pdf', f'/files/{f2}.pdf'])
    async with db_session() as s:
        assert (await s.execute(select(File))).scalars().all() == []
        assert (await s.execute(select(KnowledgeFile))).scalars().all() == []


@pytest.mark.asyncio
async def test_delete_directory_shared_file_survives(db_session, deletion_env):
    from open_webui.services.deletion import DeletionService

    vector, storage = deletion_env
    kb_a = await _insert_kb(db_session, kb_id='kb-a', meta=_drive_meta())
    kb_b = await _insert_kb(db_session, kb_id='kb-b', meta=None)
    shared = await _insert_file(
        db_session, meta={'name': 's.pdf', 'relative_path': 'docs/s.pdf', 'source_item_id': 'SRC-1'}, hash='h-s'
    )
    await Knowledges.add_file_to_knowledge_by_id('kb-a', shared, 'user-1')
    await Knowledges.add_file_to_knowledge_by_id('kb-b', shared, 'user-1')
    directories = await _dirs(db_session, 'kb-a')
    docs_id = next(d for d, (p, n) in directories.items() if n == 'docs')

    report = await DeletionService.delete_directory('kb-a', docs_id, move_files_to_parent=False)

    assert not report.has_errors
    # kb-a vectors removed, but the File row + kb-b link survive.
    assert ('kb-a', {'file_id': shared}) in vector.deleted_filters
    assert vector.deleted_collections == []
    assert storage.deleted_paths == []
    async with db_session() as s:
        assert (await s.get(File, shared)) is not None
    assert (await _link(db_session, 'kb-b', shared)) is not None
    assert (await _link(db_session, 'kb-a', shared)) is None


@pytest.mark.asyncio
async def test_delete_directory_move_files_keeps_files(db_session, deletion_env):
    from open_webui.services.deletion import DeletionService

    vector, storage = deletion_env
    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    f1 = await _insert_file(
        db_session, meta={'name': 'a.pdf', 'relative_path': 'docs/a.pdf', 'source_item_id': 'SRC-1'}
    )
    await Knowledges.add_file_to_knowledge_by_id(kb_id, f1, 'user-1')
    directories = await _dirs(db_session, kb_id)
    docs_id = next(d for d, (p, n) in directories.items() if n == 'docs')
    root_id = next(d for d, (p, n) in directories.items() if n == 'Alpha Folder')

    report = await DeletionService.delete_directory(kb_id, docs_id, move_files_to_parent=True)

    assert not report.has_errors
    assert vector.deleted_filters == [] and storage.deleted_paths == []
    link = await _link(db_session, kb_id, f1)
    assert link.directory_id == root_id  # re-homed to parent
    async with db_session() as s:
        assert (await s.get(File, f1)) is not None


@pytest.mark.asyncio
async def test_delete_directory_wrong_kb_refused(db_session, deletion_env):
    from open_webui.services.deletion import DeletionService

    kb_a = await _insert_kb(db_session, kb_id='kb-a', meta=_drive_meta())
    await _insert_kb(db_session, kb_id='kb-b', meta=None)
    f1 = await _insert_file(
        db_session, meta={'name': 'a.pdf', 'relative_path': 'docs/a.pdf', 'source_item_id': 'SRC-1'}
    )
    await Knowledges.add_file_to_knowledge_by_id('kb-a', f1, 'user-1')
    directories = await _dirs(db_session, 'kb-a')
    docs_id = next(d for d, (p, n) in directories.items() if n == 'docs')

    report = await DeletionService.delete_directory('kb-b', docs_id, move_files_to_parent=False)

    assert report.has_errors
    assert docs_id in await _dirs(db_session, 'kb-a')  # untouched


# ---------------------------------------------------------------------------
# Remove-source subtree cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_source_sweeps_files_and_root_directory(db_session, deletion_env, monkeypatch):
    from open_webui.services.sync.router import remove_files_for_source_generic
    import open_webui.retrieval.vector.async_client as vector_client_module

    vector, storage = deletion_env
    monkeypatch.setattr(vector_client_module, 'ASYNC_VECTOR_DB_CLIENT', vector)

    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    f1 = await _insert_file(
        db_session,
        file_id='googledrive-item1',
        meta={'name': 'a.pdf', 'relative_path': 'docs/a.pdf', 'source_item_id': 'SRC-1'},
    )
    await Knowledges.add_file_to_knowledge_by_id(kb_id, f1, 'user-1')

    kb = await Knowledges.get_knowledge_by_id(kb_id)
    source = kb.meta['google_drive_sync']['sources'][0]
    assert source.get('root_directory_id')  # stamped by the bridge

    removed = await remove_files_for_source_generic(
        knowledge_id=kb_id,
        source_item_id='SRC-1',
        file_id_prefix='googledrive-',
        source=source,
    )

    assert removed == 1
    assert await _dirs(db_session, kb_id) == {}  # root subtree fully gone
    async with db_session() as s:
        assert (await s.get(File, f1)) is None
        assert (await s.execute(select(KnowledgeFile))).scalars().all() == []


@pytest.mark.asyncio
async def test_remove_source_without_stamp_is_noop_on_directories(db_session, deletion_env, monkeypatch):
    from open_webui.services.sync.router import remove_files_for_source_generic
    import open_webui.retrieval.vector.async_client as vector_client_module

    vector, storage = deletion_env
    monkeypatch.setattr(vector_client_module, 'ASYNC_VECTOR_DB_CLIENT', vector)

    kb_id = await _insert_kb(db_session, meta=_drive_meta())
    removed = await remove_files_for_source_generic(
        knowledge_id=kb_id,
        source_item_id='SRC-1',
        file_id_prefix='googledrive-',
        source={'item_id': 'SRC-1', 'name': 'Alpha Folder', 'type': 'folder'},  # no root_directory_id
    )
    assert removed == 0
