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
@pytest.mark.parametrize(
    'case',
    [
        'owner',
        'stranger',
        'writer',
        'missing',
        'link_failure',
        'write_failure',
        'replace',
        'replace_commit_then_raise',
        'commit_then_raise',
        'delete',
        'delete_commit_then_raise',
        'delete_failure',
    ],
)
async def test_skill_upload_is_atomic(tmp_path, monkeypatch, request, database, case):
    from open_webui.models import files
    from open_webui.models.skills import Skill, Skills
    from open_webui.models.access_grants import AccessGrant
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
        for table in (Skill.__table__, files.File.__table__, SkillFile.__table__, AccessGrant.__table__):
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
    monkeypatch.setattr(skill_files.AccessGrants, 'has_access', AsyncMock(return_value=case == 'writer'))
    monkeypatch.setattr(skill_files, 'can_bypass_access_control', lambda user: False)
    stored = tmp_path / 'upload_readme.md'

    def write(source, name, *args):
        target = tmp_path / name
        target.write_bytes(source.read())
        if case == 'write_failure':
            raise RuntimeError('partial storage failure')
        return target.read_bytes(), str(target)

    upload = Mock(side_effect=write)
    delete = Mock(side_effect=lambda path: __import__('pathlib').Path(path).unlink())
    monkeypatch.setattr(skill_uploads.Storage, 'get_object_path', lambda name: str(tmp_path / name))
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

            async def fail_commit():
                raise RuntimeError('injected link commit failure')

            monkeypatch.setattr(session, 'commit', fail_commit)
        if case == 'commit_then_raise':
            commit = session.commit

            async def committed_then_failed():
                await commit()
                raise RuntimeError('transport failed after commit')

            monkeypatch.setattr(session, 'commit', committed_then_failed)
        kwargs = dict(
            file_id='upload',
            metadata={'skill_id': 'missing' if case == 'missing' else 'skill', 'skill_path': 'docs/readme.md'},
            user=SimpleNamespace(id=case if case in ('stranger', 'writer') else 'owner', role='user'),
            db=session,
            extension='md',
            allowed_extensions=[],
            max_size=1,
            content_type='text/markdown',
        )
        if case in ('stranger', 'missing', 'link_failure', 'write_failure'):
            with pytest.raises(HTTPException if case in ('stranger', 'missing') else RuntimeError) as caught:
                await skill_uploads.upload(UploadFile(file=BytesIO(b'content'), filename='readme.md'), **kwargs)
            assert await session.scalar(select(func.count()).select_from(files.File)) == 0
            assert await session.scalar(select(func.count()).select_from(SkillFile)) == 0
            assert not stored.exists()
            if case in ('stranger', 'missing'):
                from open_webui.constants import ERROR_MESSAGES

                assert caught.value.status_code == 404 and caught.value.detail == ERROR_MESSAGES.NOT_FOUND
                upload.assert_not_called()
            else:
                delete.assert_called_once()
        else:
            record = await skill_uploads.upload(UploadFile(file=BytesIO(b'content'), filename='readme.md'), **kwargs)
            assert record.data['content'] == 'content'
            link = await session.scalar(select(SkillFile))
            assert link.file_id == record.id == 'upload'
            assert stored.read_bytes() == b'content'
            if case in ('replace', 'replace_commit_then_raise'):
                if case == 'replace_commit_then_raise':
                    commit = session.commit

                    async def committed_replacement_then_failed():
                        await commit()
                        raise RuntimeError('replacement committed then interrupted')

                    monkeypatch.setattr(session, 'commit', committed_replacement_then_failed)
                kwargs['metadata']['skill_replace'] = True
                kwargs['file_id'] = 'replacement'
                replacement = await skill_uploads.upload(
                    UploadFile(file=BytesIO(b'new'), filename='readme.md'), **kwargs
                )
                await session.refresh(link)
                assert link.file_id == replacement.id == 'replacement'
                assert await session.scalar(select(func.count()).select_from(SkillFile)) == 1
                assert await session.get(files.File, 'upload') is None
                assert not stored.exists()
                assert (tmp_path / 'replacement_readme.md').read_bytes() == b'new'
            if case in ('delete', 'delete_commit_then_raise', 'delete_failure'):
                if case != 'delete':
                    commit = session.commit

                    async def interrupted():
                        if case == 'delete_commit_then_raise':
                            await commit()
                        raise RuntimeError('delete commit interrupted')

                    monkeypatch.setattr(session, 'commit', interrupted)
                if case == 'delete_failure':
                    with pytest.raises(RuntimeError):
                        await Skills.delete_skill_by_id('skill', db=session)
                    assert await session.get(files.File, 'upload') is not None
                    assert stored.exists()
                    assert await session.scalar(select(func.count()).select_from(SkillFile)) == 1
                    await engine.dispose()
                    return
                assert await Skills.delete_skill_by_id('skill', db=session)
                assert await session.get(files.File, 'upload') is None
                assert await session.scalar(select(func.count()).select_from(SkillFile)) == 0
                assert not stored.exists()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'target,actor,role,expected',
    [
        ('missing', 'stranger', 'user', 404),
        ('skill', 'stranger', 'user', 404),
        ('skill', 'administrator', 'admin', 404),
        ('skill', 'owner', 'user', 403),
    ],
)
async def test_skill_upload_conceals_targets_before_the_plan_gate(tmp_path, monkeypatch, target, actor, role, expected):
    from open_webui.models.skills import Skill
    from open_webui.routers import skill_files
    from open_webui.soev import skill_uploads
    from open_webui.constants import ERROR_MESSAGES

    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/concealment.db')
    async with engine.begin() as connection:
        await connection.run_sync(Skill.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    gate = AsyncMock(side_effect=HTTPException(403, detail="Feature 'skills' is not available in your plan"))
    monkeypatch.setattr(skill_files, 'require_skill_files', gate)
    monkeypatch.setattr(skill_files.AccessGrants, 'has_access', AsyncMock(return_value=False))
    monkeypatch.setattr(skill_files, 'can_bypass_access_control', lambda user: False)
    storage = Mock(side_effect=AssertionError('A refused upload cannot store bytes'))
    monkeypatch.setattr(skill_uploads.Storage, 'upload_file', storage)
    try:
        async with sessions() as session:
            session.add(
                Skill(
                    id='skill', user_id='owner', name='Fictional skill', content='', meta={}, created_at=1, updated_at=1
                )
            )
            await session.commit()
            with pytest.raises(HTTPException) as caught:
                await skill_uploads.upload(
                    UploadFile(file=BytesIO(b'fictional'), filename='fictional.txt'),
                    file_id='fictional-upload',
                    metadata={'skill_id': target, 'skill_path': 'fictional.txt'},
                    user=SimpleNamespace(id=actor, role=role),
                    db=session,
                    extension='txt',
                    allowed_extensions=[],
                    max_size=1,
                    content_type='text/plain',
                )
            assert caught.value.status_code == expected
            if expected == 404:
                assert caught.value.detail == ERROR_MESSAGES.NOT_FOUND
                gate.assert_not_awaited()
            else:
                gate.assert_awaited_once()
            storage.assert_not_called()
    finally:
        await engine.dispose()
