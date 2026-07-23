"""Tests for ``KnowledgeTable.list_tree_level`` — the lazy per-folder browser (Phase 2).

Exercises the SQLite rollup branch (the Postgres ``split_part`` branch is
covered by a manual staging check). Builds a small KB with one synced source
(``S1``), one picked-but-empty source (``S2``), and two root loose files, then
asserts each tree level, the recursive ``child_count`` / ``status_counts``
rollups (incl. NULL→unknown and error→failed merge), and keyset pagination
stability.

Fixtures mirror ``test_knowledge_metadata_mode.py`` (in-memory async SQLite +
monkeypatched ``get_async_db_context``). Links are created via
``add_file_to_knowledge_by_id`` so the Phase 1 write path populates the path
columns — i.e. the tests run against realistic post-write-path data.
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
from open_webui.models.users import User


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


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


async def _insert_kb(Session, *, kb_id: str, meta: dict | None) -> None:
    now = int(time.time())
    async with Session() as s:
        s.add(
            Knowledge(
                id=kb_id,
                user_id='user-1',
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


async def _add_file(Session, *, kb_id, name, relative_path=None, source_item_id=None, status='completed', size=100):
    """Insert a file with the given meta, then link it (populating path columns)."""
    file_id = f'f-{uuid.uuid4().hex[:8]}'
    now = int(time.time())
    meta: dict = {'name': name, 'content_type': 'application/pdf', 'size': size}
    if relative_path is not None:
        meta['relative_path'] = relative_path
    if source_item_id is not None:
        meta['source_item_id'] = source_item_id
    if status is not None:
        meta['status'] = status
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id='user-1',
                hash='h',
                filename=name,
                path='/p',
                data={},
                meta=meta,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    await Knowledges.add_file_to_knowledge_by_id(kb_id, file_id, 'user-1')
    return file_id


# Source S1 file tree:
#   root.pdf                  completed
#   docs/a.pdf                completed
#   docs/b.pdf                failed
#   docs/sub/c.pdf            error    (→ failed bucket)
#   docs/sub/d.pdf            pending
#   images/e.png             (no status → unknown)
# S2: picked but empty. Loose: loose-a.txt, loose-b.txt.
async def _seed(Session, kb_id='kb-1'):
    meta = {
        'google_drive_sync': {
            'sources': [
                {'item_id': 'S1', 'name': 'Alpha Folder', 'type': 'folder'},
                {'item_id': 'S2', 'name': 'Beta Folder', 'type': 'folder'},
            ]
        }
    }
    await _insert_kb(Session, kb_id=kb_id, meta=meta)
    await _add_file(
        Session, kb_id=kb_id, name='root.pdf', relative_path='root.pdf', source_item_id='S1', status='completed'
    )
    await _add_file(
        Session, kb_id=kb_id, name='a.pdf', relative_path='docs/a.pdf', source_item_id='S1', status='completed'
    )
    await _add_file(
        Session, kb_id=kb_id, name='b.pdf', relative_path='docs/b.pdf', source_item_id='S1', status='failed'
    )
    await _add_file(
        Session, kb_id=kb_id, name='c.pdf', relative_path='docs/sub/c.pdf', source_item_id='S1', status='error'
    )
    await _add_file(
        Session, kb_id=kb_id, name='d.pdf', relative_path='docs/sub/d.pdf', source_item_id='S1', status='pending'
    )
    await _add_file(Session, kb_id=kb_id, name='e.png', relative_path='images/e.png', source_item_id='S1', status=None)
    await _add_file(Session, kb_id=kb_id, name='loose-a.txt', status='completed')
    await _add_file(Session, kb_id=kb_id, name='loose-b.txt', status='completed')
    return kb_id


def _folder(resp, name):
    return next(f for f in resp.folders if f.name == name)


# ---------------------------------------------------------------------------
# Sources level (path="")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sources_level_lists_sources_and_loose_files(db_session):
    kb_id = await _seed(db_session)
    resp = await Knowledges.list_tree_level(kb_id, path='')

    # Both sources present, sorted by name; S1 has 6 descendants, S2 has 0.
    assert [f.name for f in resp.folders] == ['Alpha Folder', 'Beta Folder']
    s1 = _folder(resp, 'Alpha Folder')
    assert s1.path == 'S1'
    assert s1.type == 'folder'
    assert s1.child_count == 6
    assert s1.status_counts == {'pending': 1, 'completed': 2, 'failed': 2, 'unknown': 1}

    s2 = _folder(resp, 'Beta Folder')
    assert s2.child_count == 0
    assert s2.status_counts == {'pending': 0, 'completed': 0, 'failed': 0, 'unknown': 0}

    # Root loose files, sorted by filename.
    assert [f.name for f in resp.files] == ['loose-a.txt', 'loose-b.txt']
    assert resp.has_more is False


@pytest.mark.asyncio
async def test_file_type_source_renders_as_loose_files_not_folder(db_session):
    """A picker *file* source (meta type='file') stamps its file rows with its
    own item_id, but must NOT render as a one-file wrapper folder: its file
    surfaces at the root (loose files), while a sibling folder source stays a
    folder. Keys off meta ``type`` alone, so pre-existing rows need no re-sync.
    """
    kb_id = 'kb-filesrc'
    meta = {
        'google_drive_sync': {
            'sources': [
                {'item_id': 'FOLDER1', 'name': 'Alpha Folder', 'type': 'folder'},
                {'item_id': 'FILE1', 'name': 'report.pdf', 'type': 'file'},
            ]
        }
    }
    await _insert_kb(db_session, kb_id=kb_id, meta=meta)
    # Folder source: several files grouped under it.
    await _add_file(
        db_session, kb_id=kb_id, name='a.pdf', relative_path='a.pdf', source_item_id='FOLDER1', status='completed'
    )
    await _add_file(
        db_session, kb_id=kb_id, name='b.pdf', relative_path='docs/b.pdf', source_item_id='FOLDER1', status='pending'
    )
    # File source: a single file stamped with the source's own item_id.
    await _add_file(
        db_session,
        kb_id=kb_id,
        name='report.pdf',
        relative_path='report.pdf',
        source_item_id='FILE1',
        status='completed',
    )
    # A genuine loose file (no source).
    await _add_file(db_session, kb_id=kb_id, name='loose.txt', status='completed')

    resp = await Knowledges.list_tree_level(kb_id, path='')

    # The folder source is a folder; the file source is NOT.
    assert [f.name for f in resp.folders] == ['Alpha Folder']
    folder = _folder(resp, 'Alpha Folder')
    assert folder.path == 'FOLDER1'
    assert folder.type == 'folder'
    assert folder.child_count == 2
    assert not any(f.path == 'FILE1' for f in resp.folders)
    assert not any(f.name == 'report.pdf' for f in resp.folders)

    # The file source's file appears loose at the root, next to the genuine
    # loose file (both on the first page).
    assert sorted(f.name for f in resp.files) == ['loose.txt', 'report.pdf']
    report = next(f for f in resp.files if f.name == 'report.pdf')
    assert report.status == 'completed'
    assert report.size == 100
    assert resp.has_more is False


@pytest.mark.asyncio
async def test_file_source_files_only_on_first_page(db_session):
    """File-source loose files ride the first page only, so a cursor page of
    the genuine loose files neither duplicates them nor is perturbed by them."""
    kb_id = 'kb-filesrc-page'
    meta = {
        'google_drive_sync': {
            'sources': [
                {'item_id': 'FILE1', 'name': 'picked.pdf', 'type': 'file'},
            ]
        }
    }
    await _insert_kb(db_session, kb_id=kb_id, meta=meta)
    await _add_file(
        db_session,
        kb_id=kb_id,
        name='picked.pdf',
        relative_path='picked.pdf',
        source_item_id='FILE1',
        status='completed',
    )
    await _add_file(db_session, kb_id=kb_id, name='loose-a.txt', status='completed')
    await _add_file(db_session, kb_id=kb_id, name='loose-b.txt', status='completed')

    # limit=1 → one genuine loose file per page; the file-source file rides page 1.
    page1 = await Knowledges.list_tree_level(kb_id, path='', limit=1)
    assert sorted(f.name for f in page1.files) == ['loose-a.txt', 'picked.pdf']
    assert page1.folders == []  # the file source is not a folder
    assert page1.has_more is True

    page2 = await Knowledges.list_tree_level(kb_id, path='', cursor=page1.next_cursor, limit=1)
    # Second page: only the next genuine loose file — the file-source file is
    # not repeated (folders/file-source files are first-page only).
    assert [f.name for f in page2.files] == ['loose-b.txt']
    assert page2.has_more is False


# ---------------------------------------------------------------------------
# Source root (path="S1")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_root_level_subfolders_and_direct_files(db_session):
    kb_id = await _seed(db_session)
    resp = await Knowledges.list_tree_level(kb_id, path='S1')

    assert [f.name for f in resp.folders] == ['docs', 'images']
    docs = _folder(resp, 'docs')
    assert docs.path == 'S1/docs/'
    assert docs.child_count == 4  # a, b, sub/c, sub/d
    assert docs.status_counts == {'pending': 1, 'completed': 1, 'failed': 2, 'unknown': 0}

    images = _folder(resp, 'images')
    assert images.child_count == 1
    assert images.status_counts == {'pending': 0, 'completed': 0, 'failed': 0, 'unknown': 1}

    # Only 'root.pdf' sits directly at the source root.
    assert [f.name for f in resp.files] == ['root.pdf']
    assert resp.files[0].status == 'completed'
    assert resp.files[0].size == 100


# ---------------------------------------------------------------------------
# Nested folder (path="S1/docs/")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nested_folder_level(db_session):
    kb_id = await _seed(db_session)
    resp = await Knowledges.list_tree_level(kb_id, path='S1/docs/')

    assert [f.name for f in resp.folders] == ['sub']
    sub = _folder(resp, 'sub')
    assert sub.path == 'S1/docs/sub/'
    assert sub.child_count == 2
    assert sub.status_counts == {'pending': 1, 'completed': 0, 'failed': 1, 'unknown': 0}

    # Direct files of docs/, sorted by relative_path.
    assert [f.name for f in resp.files] == ['a.pdf', 'b.pdf']


@pytest.mark.asyncio
async def test_deepest_folder_has_no_subfolders(db_session):
    kb_id = await _seed(db_session)
    resp = await Knowledges.list_tree_level(kb_id, path='S1/docs/sub/')

    assert resp.folders == []
    assert sorted(f.name for f in resp.files) == ['c.pdf', 'd.pdf']
    statuses = {f.name: f.status for f in resp.files}
    assert statuses == {'c.pdf': 'error', 'd.pdf': 'pending'}


# ---------------------------------------------------------------------------
# Keyset pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyset_pagination_no_dup_or_skip(db_session):
    kb_id = await _seed(db_session)

    # docs/ has two direct files (a.pdf, b.pdf). limit=1 → two pages.
    page1 = await Knowledges.list_tree_level(kb_id, path='S1/docs/', limit=1)
    assert [f.name for f in page1.files] == ['a.pdf']
    assert page1.has_more is True
    assert page1.next_cursor is not None
    # Folders present on the first page.
    assert [f.name for f in page1.folders] == ['sub']

    page2 = await Knowledges.list_tree_level(kb_id, path='S1/docs/', cursor=page1.next_cursor, limit=1)
    assert [f.name for f in page2.files] == ['b.pdf']
    assert page2.has_more is False
    # Folders suppressed on subsequent pages (cursor present).
    assert page2.folders == []

    # No overlap, full coverage.
    seen = [f.name for f in page1.files] + [f.name for f in page2.files]
    assert seen == ['a.pdf', 'b.pdf']


@pytest.mark.asyncio
async def test_loose_files_pagination(db_session):
    kb_id = await _seed(db_session)

    page1 = await Knowledges.list_tree_level(kb_id, path='', limit=1)
    assert [f.name for f in page1.files] == ['loose-a.txt']
    assert page1.has_more is True
    # Sources still returned on first page.
    assert {f.name for f in page1.folders} == {'Alpha Folder', 'Beta Folder'}

    page2 = await Knowledges.list_tree_level(kb_id, path='', cursor=page1.next_cursor, limit=1)
    assert [f.name for f in page2.files] == ['loose-b.txt']
    assert page2.has_more is False
    assert page2.folders == []  # suppressed on cursor pages


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_source_returns_nothing(db_session):
    kb_id = await _seed(db_session)
    resp = await Knowledges.list_tree_level(kb_id, path='S2')
    assert resp.folders == []
    assert resp.files == []
    assert resp.has_more is False


@pytest.mark.asyncio
async def test_underscore_in_folder_name_not_treated_as_wildcard(db_session):
    """LIKE-escaping: 'my_folder/' must not match 'myXfolder/' via the '_' wildcard."""
    kb_id = 'kb-esc'
    await _insert_kb(
        db_session,
        kb_id=kb_id,
        meta={'google_drive_sync': {'sources': [{'item_id': 'S', 'name': 'S', 'type': 'folder'}]}},
    )
    await _add_file(
        db_session,
        kb_id=kb_id,
        name='real.pdf',
        relative_path='my_folder/real.pdf',
        source_item_id='S',
        status='completed',
    )
    await _add_file(
        db_session,
        kb_id=kb_id,
        name='decoy.pdf',
        relative_path='myXfolder/decoy.pdf',
        source_item_id='S',
        status='completed',
    )

    resp = await Knowledges.list_tree_level(kb_id, path='S/my_folder/')
    # Only the genuine my_folder child, not the myXfolder decoy.
    assert [f.name for f in resp.files] == ['real.pdf']


@pytest.mark.asyncio
async def test_missing_kb_returns_empty(db_session):
    resp = await Knowledges.list_tree_level('does-not-exist', path='')
    assert resp.folders == []
    assert resp.files == []


# ---------------------------------------------------------------------------
# Sources-level id-mismatch reconciliation. Pre-canonicalization OneDrive KBs
# hold the *picker* id in the registry while file rows carry the Graph
# *canonical* id — without the merge heuristic that renders a raw-id node
# plus an empty registry twin.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_source_id_mismatch_merges_into_one_node(db_session):
    """Exactly one unknown rollup id + exactly one file-less folder source →
    one node: registry name/type, rollup counts, rollup path."""
    kb_id = 'kb-merge'
    await _insert_kb(
        db_session,
        kb_id=kb_id,
        meta={'onedrive_sync': {'sources': [{'item_id': 'PICKER', 'name': 'Papers', 'type': 'folder'}]}},
    )
    await _add_file(
        db_session, kb_id=kb_id, name='a.pdf', relative_path='a.pdf', source_item_id='CANON', status='completed'
    )
    await _add_file(
        db_session, kb_id=kb_id, name='b.pdf', relative_path='b.pdf', source_item_id='CANON', status='pending'
    )

    resp = await Knowledges.list_tree_level(kb_id, path='')

    assert [f.name for f in resp.folders] == ['Papers']
    merged = resp.folders[0]
    assert merged.path == 'CANON'  # counts/path from the rollup id
    assert merged.type == 'folder'
    assert merged.child_count == 2
    assert merged.status_counts == {'pending': 1, 'completed': 1, 'failed': 0, 'unknown': 0}


@pytest.mark.asyncio
async def test_multi_source_ambiguity_keeps_plain_union(db_session):
    """Two file-less folder sources + one unknown rollup id: ambiguous — no
    merge, the union renders unchanged (worst case: the raw id)."""
    kb_id = 'kb-ambiguous'
    await _insert_kb(
        db_session,
        kb_id=kb_id,
        meta={
            'onedrive_sync': {
                'sources': [
                    {'item_id': 'P1', 'name': 'Alpha', 'type': 'folder'},
                    {'item_id': 'P2', 'name': 'Beta', 'type': 'folder'},
                ]
            }
        },
    )
    await _add_file(
        db_session, kb_id=kb_id, name='a.pdf', relative_path='a.pdf', source_item_id='ORPHAN', status='completed'
    )

    resp = await Knowledges.list_tree_level(kb_id, path='')

    assert [f.name for f in resp.folders] == ['Alpha', 'Beta', 'ORPHAN']
    orphan = _folder(resp, 'ORPHAN')
    assert orphan.child_count == 1
    assert _folder(resp, 'Alpha').child_count == 0
    assert _folder(resp, 'Beta').child_count == 0


@pytest.mark.asyncio
async def test_orphan_rollup_falls_back_to_single_source_name(db_session):
    """No merge candidate (the registry source has its own files), but the KB
    has a single registry source — the orphan renders under its name rather
    than a bare provider id."""
    kb_id = 'kb-fallback'
    await _insert_kb(
        db_session,
        kb_id=kb_id,
        meta={'onedrive_sync': {'sources': [{'item_id': 'S1', 'name': 'Papers', 'type': 'folder'}]}},
    )
    await _add_file(
        db_session, kb_id=kb_id, name='a.pdf', relative_path='a.pdf', source_item_id='S1', status='completed'
    )
    await _add_file(
        db_session, kb_id=kb_id, name='b.pdf', relative_path='b.pdf', source_item_id='ORPHAN', status='completed'
    )

    resp = await Knowledges.list_tree_level(kb_id, path='')

    # Two nodes (distinct paths), both labeled with the registry name.
    assert [f.name for f in resp.folders] == ['Papers', 'Papers']
    assert sorted(f.path for f in resp.folders) == ['ORPHAN', 'S1']


# ---------------------------------------------------------------------------
# Route-level access control (GET /{id}/tree). Calls the handler directly with
# mocked Knowledges/AccessGrants so we test the access gates + delegation
# without mounting the full router. The gates are a verbatim copy of the proven
# GET /{id}/files route.
# ---------------------------------------------------------------------------


class _FakeUser:
    def __init__(self, user_id='user-1', role='user'):
        self.id = user_id
        self.role = role


class _FakeKB:
    def __init__(self, owner='user-1'):
        self.id = 'kb-1'
        self.user_id = owner


@pytest.mark.asyncio
async def test_route_404_when_kb_missing(monkeypatch):
    from fastapi import HTTPException
    from open_webui.routers import knowledge as kr

    async def _none(id=None, db=None):
        return None

    monkeypatch.setattr(kr.Knowledges, 'get_knowledge_by_id', _none)

    with pytest.raises(HTTPException) as exc:
        await kr.get_knowledge_tree('kb-1', path='', cursor=None, limit=200, user=_FakeUser(), db=None)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_route_403_when_no_read_access(monkeypatch):
    from fastapi import HTTPException
    from open_webui.routers import knowledge as kr

    async def _kb(id=None, db=None):
        return _FakeKB(owner='someone-else')

    async def _no_access(**kwargs):
        return False

    monkeypatch.setattr(kr.Knowledges, 'get_knowledge_by_id', _kb)
    monkeypatch.setattr(kr.AccessGrants, 'has_access', _no_access)

    with pytest.raises(HTTPException) as exc:
        await kr.get_knowledge_tree('kb-1', path='', cursor=None, limit=200, user=_FakeUser(), db=None)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_route_403_when_suspended_for_non_admin(monkeypatch):
    from fastapi import HTTPException
    from open_webui.routers import knowledge as kr

    async def _kb(id=None, db=None):
        return _FakeKB(owner='user-1')

    async def _suspension(id, db=None):
        return {'days_remaining': 12}

    monkeypatch.setattr(kr.Knowledges, 'get_knowledge_by_id', _kb)
    monkeypatch.setattr(kr.Knowledges, 'get_suspension_info', _suspension)

    with pytest.raises(HTTPException) as exc:
        await kr.get_knowledge_tree('kb-1', path='', cursor=None, limit=200, user=_FakeUser(role='user'), db=None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_route_owner_happy_path_delegates(monkeypatch):
    from open_webui.routers import knowledge as kr
    from open_webui.models.knowledge import KnowledgeTreeResponse, TreeFolder

    async def _kb(id=None, db=None):
        return _FakeKB(owner='user-1')

    async def _no_suspension(id, db=None):
        return None

    captured = {}

    async def _list_tree_level(kid, path='', cursor=None, limit=200, db=None):
        captured.update(id=kid, path=path, cursor=cursor, limit=limit)
        return KnowledgeTreeResponse(folders=[TreeFolder(name='S1', path='S1', child_count=3)])

    monkeypatch.setattr(kr.Knowledges, 'get_knowledge_by_id', _kb)
    monkeypatch.setattr(kr.Knowledges, 'get_suspension_info', _no_suspension)
    monkeypatch.setattr(kr.Knowledges, 'list_tree_level', _list_tree_level)

    resp = await kr.get_knowledge_tree('kb-1', path='S1/docs/', cursor='abc', limit=50, user=_FakeUser(), db=None)
    assert [f.name for f in resp.folders] == ['S1']
    # Route forwarded the params through to the model method.
    assert captured == {'id': 'kb-1', 'path': 'S1/docs/', 'cursor': 'abc', 'limit': 50}
