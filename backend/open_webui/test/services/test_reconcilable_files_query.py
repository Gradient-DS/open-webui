"""Guards ``Files.get_processing_files_with_pipeline_job`` — the reconciler's
file-selection query.

It must sweep both direct-upload files (status 'processing') AND cloud-sync
warren files, which mirror the loader-worker's per-item stage onto meta.status
and therefore sit at 'ingesting' (or pass through 'downloading'/'parsing')
while awaiting /ingest. Before this fix only 'processing' was swept, so a
cloud-sync file whose warren job failed / produced zero chunks was invisible to
the reconciler and hung the whole sync. Only files carrying a
``pipeline_job_id`` (warren-submitted) are returned.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from open_webui.models import files as files_module
from open_webui.models.files import File, Files


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(File.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        if db is not None:
            yield db
        else:
            async with Session() as s:
                yield s

    monkeypatch.setattr(files_module, 'get_async_db_context', _get_async_db_context)
    yield Session
    await engine.dispose()


async def _add(Session, *, status, job_id):
    file_id = f'f-{uuid.uuid4().hex[:8]}'
    now = int(time.time())
    meta: dict = {'status': status}
    if job_id is not None:
        meta['pipeline_job_id'] = job_id
    async with Session() as s:
        s.add(
            File(
                id=file_id,
                user_id='u',
                hash='h',
                filename=file_id,
                path='/p',
                data={},
                meta=meta,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return file_id


@pytest.mark.asyncio
async def test_sweeps_processing_and_cloud_sync_stages_with_job_id(db_session):
    # Swept: direct-upload 'processing' + cloud-sync warren stages, all with a job id.
    processing = await _add(db_session, status='processing', job_id='j1')
    ingesting = await _add(db_session, status='ingesting', job_id='j2')
    parsing = await _add(db_session, status='parsing', job_id='j3')
    downloading = await _add(db_session, status='downloading', job_id='j4')

    # Excluded: terminal statuses, non-terminal without a job id, and 'pending'.
    await _add(db_session, status='completed', job_id='j5')
    await _add(db_session, status='error', job_id='j6')
    await _add(db_session, status='ingesting', job_id=None)  # not yet submitted to warren
    await _add(db_session, status='pending', job_id='j7')  # stub, pre-submit

    got = {f.id for f in await Files.get_processing_files_with_pipeline_job()}
    assert got == {processing, ingesting, parsing, downloading}
