"""Unit tests for the SkillFilesTable model — hermetic in-memory SQLite.

Covers the path-based join model: add_file_to_skill_by_id (row with path),
has_file, get_file_by_path, remove_file_by_path, move_path,
remove_paths_under_prefix, get_files_by_skill_id, search_files_by_id
(returns path), get_file_counts_by_skill_ids.

Mirrors the pattern in test_invites_model.py (async SQLite engine, StaticPool,
monkeypatched get_async_db_context).
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from open_webui.models import files as files_module
from open_webui.models import skill_files as skill_files_module
from open_webui.models.files import File
from open_webui.models.skill_files import SkillFile, SkillFiles
from open_webui.models.skills import Skill
from open_webui.models.users import User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """In-memory SQLite engine with Skill + File + SkillFile tables created.

    We must create the Skill and File parent tables before SkillFile because
    of the FK constraints. Monkeypatches get_async_db_context in both modules
    so all DB operations run against the test engine.
    """
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        # Parent tables first (FK dependencies)
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(Skill.__table__.create)
        await conn.run_sync(File.__table__.create)
        await conn.run_sync(SkillFile.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _ctx(db=None):
        async with Session() as s:
            yield s

    monkeypatch.setattr(skill_files_module, 'get_async_db_context', _ctx)
    monkeypatch.setattr(files_module, 'get_async_db_context', _ctx)
    yield Session
    await engine.dispose()


async def _insert_file(Session, *, file_id: str | None = None, filename: str = 'test.md') -> str:
    """Insert a minimal File row and return its id."""
    fid = file_id or str(uuid.uuid4())
    now = int(time.time())
    async with Session() as s:
        row = File(
            id=fid,
            user_id='user-1',
            filename=filename,
            path=f'uploads/{fid}',
            data=None,
            meta={'content_type': 'text/markdown', 'size': 42},
            created_at=now,
            updated_at=now,
        )
        s.add(row)
        await s.commit()
    return fid


class TestAddFileToSkill:
    @pytest.mark.asyncio
    async def test_add_creates_row_with_path(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-1'

        result = await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'docs/guide.md', 'user-1')

        assert result is not None
        assert result.skill_id == skill_id
        assert result.file_id == fid
        assert result.user_id == 'user-1'
        assert result.path == 'docs/guide.md'

    @pytest.mark.asyncio
    async def test_add_duplicate_path_returns_none(self, db_session):
        """Two rows with the same (skill_id, path) violate the new unique."""
        fid1 = await _insert_file(db_session)
        fid2 = await _insert_file(db_session)
        skill_id = 'skill-2'

        first = await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'guide.md', 'user-1')
        second = await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'guide.md', 'user-1')

        assert first is not None
        assert second is None  # unique (skill_id, path) violation → returns None

    @pytest.mark.asyncio
    async def test_same_file_two_paths_allowed(self, db_session):
        """The new unique is on (skill_id, path), not (skill_id, file_id),
        so the same File may live at two distinct paths."""
        fid = await _insert_file(db_session)
        skill_id = 'skill-2b'

        first = await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'a.md', 'user-1')
        second = await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'b.md', 'user-1')

        assert first is not None
        assert second is not None


class TestHasFile:
    @pytest.mark.asyncio
    async def test_has_file_true_after_add(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-hf'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'x.md', 'user-1')

        assert await SkillFiles.has_file(skill_id, fid) is True

    @pytest.mark.asyncio
    async def test_has_file_false_before_add(self, db_session):
        assert await SkillFiles.has_file('skill-x', 'file-x') is False


class TestGetFileByPath:
    @pytest.mark.asyncio
    async def test_returns_row_for_existing_path(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-gp'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'docs/x.md', 'user-1')

        row = await SkillFiles.get_file_by_path(skill_id, 'docs/x.md')

        assert row is not None
        assert row.file_id == fid
        assert row.path == 'docs/x.md'

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_path(self, db_session):
        assert await SkillFiles.get_file_by_path('skill-gp', 'nope.md') is None


class TestRemoveFileByPath:
    @pytest.mark.asyncio
    async def test_remove_deletes_only_matching_path(self, db_session):
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        skill_id = 'skill-rm'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'a.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'b.md', 'user-1')

        removed = await SkillFiles.remove_file_by_path(skill_id, 'a.md')

        assert removed is not None
        assert removed.file_id == fid1
        assert await SkillFiles.get_file_by_path(skill_id, 'a.md') is None
        assert await SkillFiles.get_file_by_path(skill_id, 'b.md') is not None

    @pytest.mark.asyncio
    async def test_remove_nonexistent_returns_none(self, db_session):
        assert await SkillFiles.remove_file_by_path('skill-z', 'nope.md') is None


class TestMovePath:
    @pytest.mark.asyncio
    async def test_move_updates_path(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-mv'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'old.md', 'user-1')

        ok = await SkillFiles.move_path(skill_id, 'old.md', 'new.md')

        assert ok is True
        assert await SkillFiles.get_file_by_path(skill_id, 'old.md') is None
        moved = await SkillFiles.get_file_by_path(skill_id, 'new.md')
        assert moved is not None
        assert moved.file_id == fid

    @pytest.mark.asyncio
    async def test_move_nonexistent_returns_false(self, db_session):
        assert await SkillFiles.move_path('skill-mv', 'gone.md', 'new.md') is False


class TestMovePathsUnderPrefix:
    @pytest.mark.asyncio
    async def test_rewrites_prefix_for_all_matching_rows(self, db_session):
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        fid3 = await _insert_file(db_session, filename='c.md')
        skill_id = 'skill-fmv'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'docs/a.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'docs/sub/b.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid3, 'other/c.md', 'user-1')

        ok = await SkillFiles.move_paths_under_prefix(skill_id, 'docs/', 'documentation/')

        assert ok is True
        assert await SkillFiles.get_file_by_path(skill_id, 'documentation/a.md') is not None
        assert await SkillFiles.get_file_by_path(skill_id, 'documentation/sub/b.md') is not None
        # untouched sibling
        assert await SkillFiles.get_file_by_path(skill_id, 'other/c.md') is not None
        assert await SkillFiles.get_file_by_path(skill_id, 'docs/a.md') is None

    @pytest.mark.asyncio
    async def test_returns_false_when_no_match(self, db_session):
        assert await SkillFiles.move_paths_under_prefix('skill-fmv', 'nope/', 'x/') is False


class TestRemovePathsUnderPrefix:
    @pytest.mark.asyncio
    async def test_removes_all_rows_under_prefix(self, db_session):
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        fid3 = await _insert_file(db_session, filename='c.md')
        skill_id = 'skill-pre'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'docs/a.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'docs/sub/b.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid3, 'other/c.md', 'user-1')

        removed = await SkillFiles.remove_paths_under_prefix(skill_id, 'docs/')

        removed_file_ids = {r.file_id for r in removed}
        assert removed_file_ids == {fid1, fid2}
        # 'other/c.md' must survive
        assert await SkillFiles.get_file_by_path(skill_id, 'other/c.md') is not None
        assert await SkillFiles.get_file_by_path(skill_id, 'docs/a.md') is None

    @pytest.mark.asyncio
    async def test_prefix_does_not_match_sibling_substring(self, db_session):
        """'docs/' must not match 'docs2/x.md' — prefix is literal."""
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        skill_id = 'skill-pre2'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'docs/a.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'docs2/b.md', 'user-1')

        removed = await SkillFiles.remove_paths_under_prefix(skill_id, 'docs/')

        assert {r.file_id for r in removed} == {fid1}
        assert await SkillFiles.get_file_by_path(skill_id, 'docs2/b.md') is not None


class TestGetFilesBySkillId:
    @pytest.mark.asyncio
    async def test_returns_all_rows_for_skill_with_path(self, db_session):
        skill_id = 'skill-gf'
        fid1 = await _insert_file(db_session, filename='x.md')
        fid2 = await _insert_file(db_session, filename='y.md')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'x.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'sub/y.md', 'user-1')

        rows = await SkillFiles.get_files_by_skill_id(skill_id)

        assert len(rows) == 2
        paths = {r.path for r in rows}
        assert paths == {'x.md', 'sub/y.md'}

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_unknown_skill(self, db_session):
        rows = await SkillFiles.get_files_by_skill_id('skill-unknown')
        assert rows == []


class TestGetFileCountsBySkillIds:
    @pytest.mark.asyncio
    async def test_counts_per_skill(self, db_session):
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        fid3 = await _insert_file(db_session, filename='c.md')
        await SkillFiles.add_file_to_skill_by_id('skill-A', fid1, 'a.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id('skill-A', fid2, 'b.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id('skill-B', fid3, 'c.md', 'user-1')

        counts = await SkillFiles.get_file_counts_by_skill_ids(['skill-A', 'skill-B'])

        assert counts.get('skill-A') == 2
        assert counts.get('skill-B') == 1


class TestSearchFilesById:
    @pytest.mark.asyncio
    async def test_list_returns_items_total_and_path(self, db_session):
        skill_id = 'skill-ls'
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'docs/a.md', 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'b.md', 'user-1')

        response = await SkillFiles.search_files_by_id(skill_id, 'user-1', {})

        assert response.total == 2
        assert len(response.items) == 2
        paths = {item.path for item in response.items}
        assert paths == {'docs/a.md', 'b.md'}

    @pytest.mark.asyncio
    async def test_list_empty_skill_returns_zero_total(self, db_session):
        response = await SkillFiles.search_files_by_id('skill-empty', 'user-1', {})
        assert response.total == 0
        assert response.items == []
