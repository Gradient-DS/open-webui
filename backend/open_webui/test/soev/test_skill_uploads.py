"""Skill upload bytes and link commit together, with current write authority."""

import os
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
@pytest.mark.parametrize('database', ['sqlite', 'postgresql'])
@pytest.mark.parametrize('case', ['owner', 'stranger', 'link_failure', 'write_failure', 'replace'])
async def test_skill_upload_is_atomic(tmp_path, monkeypatch, request, database, case):
    from open_webui.models import files
    from open_webui.models.skills import Skill, Skills
    from open_webui.models.skill_files import SkillFile
    from open_webui.routers import skill_files
    from open_webui.soev import skill_uploads

    if database == 'postgresql':
        if not os.getenv('BACKEND_TEST_DATABASE_URL'):
            pytest.skip('PostgreSQL is not configured')
        url = request.getfixturevalue('postgres_database').set(drivername='postgresql+psycopg')
    else:
        url = f'sqlite+aiosqlite:///{tmp_path}/atomic.db'
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        for table in (Skill.__table__, files.File.__table__, SkillFile.__table__):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with sessions() as session:
                yield session

    monkeypatch.setattr(files, 'get_async_db_context', context)
    monkeypatch.setattr(Skills, '_get_access_grants', AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_files, 'require_skill_files', AsyncMock())
    monkeypatch.setattr(skill_files.AccessGrants, 'has_access', AsyncMock(return_value=False))
    monkeypatch.setattr(skill_files, 'can_bypass_access_control', lambda user: False)
    stored = tmp_path / 'new-file'

    def write(source, *args):
        stored.write_bytes(source.read())
        if case == 'write_failure':
            raise RuntimeError('partial storage failure')
        return stored.read_bytes(), str(stored)

    upload = Mock(side_effect=write)
    delete = Mock(side_effect=lambda path: __import__('pathlib').Path(path).unlink())
    monkeypatch.setattr(skill_uploads.Storage, 'get_object_path', lambda name: str(stored))
    monkeypatch.setattr(skill_uploads.Storage, 'upload_file', upload)
    monkeypatch.setattr(skill_uploads.Storage, 'delete_file', delete)
    async with sessions() as session:
        session.add(
            Skill(
                id='skill', user_id='owner', name='skill', content='instructions', meta={}, created_at=1, updated_at=1
            )
        )
        await session.commit()
        if case == 'link_failure':
            # A bad FK rejects the link at commit, after the File row and bytes exist.
            original_commit = session.commit

            async def fail_commit():
                raise RuntimeError('injected link commit failure')

            monkeypatch.setattr(session, 'commit', fail_commit)
        kwargs = dict(
            file_id='upload',
            metadata={'skill_id': 'skill', 'skill_path': 'docs/readme.md'},
            user=SimpleNamespace(id='stranger' if case == 'stranger' else 'owner', role='user'),
            db=session,
            extension='md',
            allowed_extensions=[],
            max_size=1,
            content_type='text/markdown',
        )
        if case in ('stranger', 'link_failure', 'write_failure'):
            with pytest.raises(HTTPException if case == 'stranger' else RuntimeError):
                await skill_uploads.upload(UploadFile(file=BytesIO(b'content'), filename='readme.md'), **kwargs)
            assert await session.scalar(select(func.count()).select_from(files.File)) == 0
            assert await session.scalar(select(func.count()).select_from(SkillFile)) == 0
            assert not stored.exists()
            if case == 'stranger':
                upload.assert_not_called()
            else:
                delete.assert_called_once()
        else:
            record = await skill_uploads.upload(UploadFile(file=BytesIO(b'content'), filename='readme.md'), **kwargs)
            assert record.data['content'] == 'content'
            link = await session.scalar(select(SkillFile))
            assert link.file_id == record.id == 'upload'
            assert stored.read_bytes() == b'content'
            if case == 'replace':
                kwargs['metadata']['skill_replace'] = True
                kwargs['file_id'] = 'replacement'
                replacement = await skill_uploads.upload(
                    UploadFile(file=BytesIO(b'new'), filename='readme.md'), **kwargs
                )
                await session.refresh(link)
                assert link.file_id == replacement.id == 'replacement'
                assert await session.scalar(select(func.count()).select_from(SkillFile)) == 1
    await engine.dispose()
