"""Retirement is explicit and preserves base-code rows and linked resources."""

import importlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.files import File
from open_webui.soev import retire_document_bytes as retirement


def test_no_automatic_document_retirement_revision():
    versions = Path(__file__).resolve().parents[2] / 'migrations/versions'
    assert not (versions / 'f286d9b43a71_retire_document_originals.py').exists()
    from open_webui.soev import migrate

    assert 'retire' not in __import__('inspect').getsource(migrate)


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('database', ['sqlite', 'postgresql'])
@pytest.mark.asyncio
async def test_report_and_action_share_safe_per_file_decisions(tmp_path, request, monkeypatch, apply, database):
    if database == 'postgresql':
        if not os.getenv('BACKEND_TEST_DATABASE_URL'):
            pytest.skip('PostgreSQL is not configured')
        url = request.getfixturevalue('postgres_database').set(drivername='postgresql+psycopg')
    else:
        url = f'sqlite+aiosqlite:///{tmp_path}/files.db'
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(File.__table__.create)
        for table in ('chat_file', 'knowledge_file', 'skill_file'):
            await connection.execute(sa.text(f'CREATE TABLE {table} (file_id TEXT)'))
    monkeypatch.setattr(
        retirement, 'sniff_content_type', lambda prefix: 'image/png' if prefix == b'image' else 'text/plain'
    )
    rows = [
        ('base-held', {'status': 'completed', 'soev_collection_key': None}, b'original'),
        ('unknown-local', {}, b'original'),
        ('unknown-empty', {}, None),
        ('held-empty', {}, None),
        ('linked-chat', {}, None),
        ('linked-kb', {}, None),
        ('inflight', {'status': 'processing', 'soev_job': {'committed': False}}, b'original'),
        ('image-no-mime', {}, b'image'),
        ('image-octet', {'content_type': 'application/octet-stream'}, b'image'),
        ('video', {'content_type': 'video/mp4'}, b'video'),
        ('skill', {}, b'original'),
        ('export', {'local_kind': 'export'}, b'original'),
        ('base-hidden', {'status': 'completed'}, None),
    ]
    for key, _, data in rows:
        if data is not None:
            (tmp_path / key).write_bytes(data)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        for key, meta, data in rows:
            session.add(
                File(
                    id=key,
                    user_id='owner',
                    filename=key,
                    path=str(tmp_path / key) if data else None,
                    meta=meta,
                    data={},
                    created_at=1,
                    updated_at=1,
                )
            )
        for table, key in [('chat_file', 'linked-chat'), ('knowledge_file', 'linked-kb'), ('skill_file', 'skill')]:
            await session.execute(sa.text(f'INSERT INTO {table} VALUES (:key)'), {'key': key})
        await session.commit()

        class Client:
            async def pages(self, path, *, params, as_user):
                if params['source_id'] in {'base-held', 'held-empty', 'inflight'}:
                    yield {'source_id': params['source_id'], 'collection_key': 'published'}

            @asynccontextmanager
            async def stream_response(self, path, *, as_user):
                yield httpx.Response(200)

        counts = await retirement.retire(
            session,
            Client(),
            upload_dir=tmp_path,
            export_dir=tmp_path / 'exports',
            apply=apply,
            owner_id='owner',
            as_user='owui:user:owner',
        )
        assert counts['platform_held'] == 2
        assert counts['unknown_with_bytes'] == 1
        assert counts['unknown_without_bytes'] == 4
        assert counts['in_flight'] == 1 and counts['protected'] == 5
        assert counts['bytes_removed'] == int(apply) and counts['rows_removed'] == int(apply)
        assert bool(await session.get(File, 'unknown-empty')) is not apply
        for key in ('base-held', 'held-empty', 'linked-chat', 'linked-kb', 'base-hidden'):
            assert await session.get(File, key) is not None
        for key in ('unknown-local', 'inflight', 'image-no-mime', 'image-octet', 'video', 'skill', 'export'):
            assert (tmp_path / key).exists()
        assert (tmp_path / 'base-held').exists() is not apply
    await engine.dispose()


@pytest.mark.asyncio
async def test_operator_report_disables_schema_migrations(monkeypatch, tmp_path):
    from open_webui import config, env
    from open_webui.internal import db
    from open_webui.soev import client
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.invalid')
    monkeypatch.setattr(env, 'ENABLE_DB_MIGRATIONS', True)
    monkeypatch.setenv('ENABLE_DB_MIGRATIONS', 'true')

    @asynccontextmanager
    async def context():
        yield object()

    monkeypatch.setattr(db, 'get_async_db_context', context)
    monkeypatch.setattr(client, 'build_service_client', lambda: object())
    monkeypatch.setattr(client, 'close_client', AsyncMock())
    report = AsyncMock(return_value={'platform_held': 1})
    monkeypatch.setattr(retirement, 'retire', report)
    assert await retirement.run(SimpleNamespace(owner_id=None, apply=False)) == {'platform_held': 1}
    assert env.ENABLE_DB_MIGRATIONS is False
    assert os.environ['ENABLE_DB_MIGRATIONS'] == 'false'
    assert report.call_args.kwargs['apply'] is False
