"""Unit tests for the SkillFilesTable model — hermetic in-memory SQLite.

Covers: add_file_to_skill_by_id (row exists + content populated in File.data),
has_file, remove_file_from_skill_by_id, search_files_by_id ({items, total}).

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
    async def test_add_creates_row(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-1'

        result = await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'user-1')

        assert result is not None
        assert result.skill_id == skill_id
        assert result.file_id == fid
        assert result.user_id == 'user-1'

    @pytest.mark.asyncio
    async def test_add_duplicate_returns_none(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-2'

        first = await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'user-1')
        second = await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'user-1')

        assert first is not None
        assert second is None  # unique constraint violation → returns None


class TestHasFile:
    @pytest.mark.asyncio
    async def test_has_file_true_after_add(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-hf'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'user-1')

        assert await SkillFiles.has_file(skill_id, fid) is True

    @pytest.mark.asyncio
    async def test_has_file_false_before_add(self, db_session):
        assert await SkillFiles.has_file('skill-x', 'file-x') is False


class TestRemoveFile:
    @pytest.mark.asyncio
    async def test_remove_deletes_row(self, db_session):
        fid = await _insert_file(db_session)
        skill_id = 'skill-rm'
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid, 'user-1')

        result = await SkillFiles.remove_file_from_skill_by_id(skill_id, fid)

        assert result is True
        assert await SkillFiles.has_file(skill_id, fid) is False

    @pytest.mark.asyncio
    async def test_remove_nonexistent_returns_true(self, db_session):
        # mirror knowledge.py — delete is idempotent
        result = await SkillFiles.remove_file_from_skill_by_id('skill-z', 'file-z')
        assert result is True


class TestGetFilesBySkillId:
    @pytest.mark.asyncio
    async def test_returns_all_rows_for_skill(self, db_session):
        skill_id = 'skill-gf'
        fid1 = await _insert_file(db_session, filename='x.md')
        fid2 = await _insert_file(db_session, filename='y.md')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'user-1')

        rows = await SkillFiles.get_files_by_skill_id(skill_id)

        assert len(rows) == 2
        returned_file_ids = {r.file_id for r in rows}
        assert fid1 in returned_file_ids
        assert fid2 in returned_file_ids
        for row in rows:
            assert row.skill_id == skill_id

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_unknown_skill(self, db_session):
        rows = await SkillFiles.get_files_by_skill_id('skill-unknown')
        assert rows == []

    @pytest.mark.asyncio
    async def test_does_not_cross_contaminate_skills(self, db_session):
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        await SkillFiles.add_file_to_skill_by_id('skill-A', fid1, 'user-1')
        await SkillFiles.add_file_to_skill_by_id('skill-B', fid2, 'user-1')

        rows_a = await SkillFiles.get_files_by_skill_id('skill-A')
        assert len(rows_a) == 1
        assert rows_a[0].file_id == fid1


class TestSearchFilesById:
    @pytest.mark.asyncio
    async def test_list_returns_items_and_total(self, db_session):
        skill_id = 'skill-ls'
        fid1 = await _insert_file(db_session, filename='a.md')
        fid2 = await _insert_file(db_session, filename='b.md')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid1, 'user-1')
        await SkillFiles.add_file_to_skill_by_id(skill_id, fid2, 'user-1')

        response = await SkillFiles.search_files_by_id(skill_id, 'user-1', {})

        assert response.total == 2
        assert len(response.items) == 2

    @pytest.mark.asyncio
    async def test_list_empty_skill_returns_zero_total(self, db_session):
        response = await SkillFiles.search_files_by_id('skill-empty', 'user-1', {})
        assert response.total == 0
        assert response.items == []
