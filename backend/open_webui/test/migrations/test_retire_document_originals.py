"""Retirement is explicit and preserves base-code rows and linked resources."""

import hashlib
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
            await connection.execute(sa.text(f'CREATE TABLE {table} (file_id TEXT, knowledge_id TEXT)'))
    monkeypatch.setattr(
        retirement,
        'sniff_content_type',
        lambda prefix: 'image/png' if prefix == b'image' else 'video/mp4' if prefix == b'video' else 'text/plain',
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
                    meta={'size': len(b'original'), 'file_hash': hashlib.sha256(b'original').hexdigest(), **meta},
                    data={},
                    created_at=1,
                    updated_at=1,
                )
            )
        for table, key in [('chat_file', 'linked-chat'), ('knowledge_file', 'linked-kb'), ('skill_file', 'skill')]:
            await session.execute(sa.text(f'INSERT INTO {table} VALUES (:key, NULL)'), {'key': key})
        await session.commit()

        class Client:
            async def pages(self, path, *, params, as_user):
                if params['source_id'] in {'base-held', 'held-empty', 'inflight'}:
                    yield {
                        'source_id': params['source_id'],
                        'collection_key': 'owui-attachments-owner',
                        'byte_size': len(b'original'),
                        'sha256': hashlib.sha256(b'original').hexdigest(),
                    }

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
    monkeypatch.setattr(client, 'build_service_client', lambda: SimpleNamespace(is_service=True))
    monkeypatch.setattr(client, 'close_client', AsyncMock())
    report = AsyncMock(return_value={'platform_held': 1})
    monkeypatch.setattr(retirement, 'retire', report)
    assert await retirement.run(SimpleNamespace(owner_id=None, apply=False)) == {'platform_held': 1}
    assert env.ENABLE_DB_MIGRATIONS is False
    assert os.environ['ENABLE_DB_MIGRATIONS'] == 'false'
    assert report.call_args.kwargs['apply'] is False
    assert report.call_args.args[1].is_service is True
    assert report.call_args.kwargs['as_user'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize(
    'case,category',
    [
        ('base', 'platform_held'),
        ('pagination', 'platform_held'),
        ('403', 'platform_error'),
        ('404', 'platform_error'),
        ('down', 'platform_error'),
        ('service_empty', 'unknown_with_bytes'),
        ('hash_differs', 'platform_differs'),
        ('size_differs', 'platform_differs'),
        ('local_differs', 'platform_differs'),
        ('missing_hash', 'platform_differs'),
        ('text_only', 'unknown_without_bytes'),
    ],
)
async def test_retirement_identity_fingerprints_and_platform_failures(tmp_path, monkeypatch, apply, case, category):
    from open_webui.soev.client import SoevClient, close_client

    await close_client()
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/guard.db')
    async with engine.begin() as connection:
        await connection.run_sync(File.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    content = b'%PDF-1.7 base original'
    local = tmp_path / 'base.pdf'
    local.write_bytes(b'changed bytes' if case == 'local_differs' else content)
    digest = hashlib.sha256(content).hexdigest()
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer existing-product-key'
        assert 'X-Soev-User-Token' not in request.headers
        if request.url.path == '/v1/documents':
            if case in ('403', '404', 'down'):
                return httpx.Response(503 if case == 'down' else int(case))
            if case in ('service_empty', 'text_only'):
                return httpx.Response(200, json={'data': [], 'next_cursor': None})
            row = {
                'source_id': 'base',
                'collection_key': 'owui-attachments-owner',
                'byte_size': len(content),
                'sha256': 'b' * 64 if case == 'hash_differs' else digest,
            }
            if case == 'size_differs':
                row['byte_size'] += 1
            if case == 'pagination' and not request.url.params.get('cursor'):
                return httpx.Response(
                    200, json={'data': [{**row, 'collection_key': 'unrelated'}], 'next_cursor': 'second'}
                )
            return httpx.Response(200, json={'data': [row], 'next_cursor': None})
        if request.url.path.endswith('/original'):
            return httpx.Response(303, headers={'Location': '/v1/downloads/verified'})
        assert request.url.path == '/v1/downloads/verified'
        return httpx.Response(200, content=content)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = SoevClient('https://platform.invalid', 'existing-product-key', service=True)
    async with sessions() as session:
        session.add(
            File(
                id='base',
                user_id='owner',
                filename='base.pdf',
                path=None if case == 'text_only' else str(local),
                meta={
                    'status': 'completed',
                    'soev_collection_key': None,
                    'source': None,
                    'content_type': 'application/pdf',
                    'size': len(content),
                    'file_hash': None if case == 'missing_hash' else digest,
                },
                data={'status': 'completed', **({'content': 'only remaining text'} if case == 'text_only' else {})},
                created_at=1,
                updated_at=1,
            )
        )
        await session.commit()
        if not apply:

            async def no_write():
                pytest.fail('Default reporting committed a transaction')

            monkeypatch.setattr(session, 'commit', no_write)
        result = await retirement.retire(
            session, client, upload_dir=tmp_path, export_dir=tmp_path / 'exports', apply=apply
        )
        assert result[category] == 1
        removed = apply and category == 'platform_held'
        assert local.exists() is not removed
        row = await session.get(File, 'base')
        assert row is not None
        if case == 'text_only':
            assert row.data['content'] == 'only remaining text'
        else:
            assert (row.path is None) is removed
        if case == 'pagination':
            assert [dict(r.url.params) for r in calls if r.url.path == '/v1/documents'] == [
                {'source_id': 'base'},
                {'source_id': 'base', 'cursor': 'second'},
            ]
    await close_client()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('after_commit', [False, True])
async def test_retirement_commits_each_row_before_unlink_and_survives_later_failure(
    tmp_path, monkeypatch, after_commit
):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/transactions.db')
    async with engine.begin() as connection:
        await connection.run_sync(File.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    content = b'%PDF original'

    class Client:
        async def pages(self, path, *, params, as_user):
            yield {
                'source_id': params['source_id'],
                'collection_key': 'owui-attachments-owner',
                'byte_size': len(content),
                'sha256': hashlib.sha256(content).hexdigest(),
            }

        @asynccontextmanager
        async def stream_response(self, path, **kwargs):
            yield httpx.Response(200)

    async with sessions() as session:
        for key in ('a', 'b'):
            path = tmp_path / key
            path.write_bytes(content)
            session.add(
                File(
                    id=key,
                    user_id='owner',
                    filename=key,
                    path=str(path),
                    meta={
                        'status': 'completed',
                        'soev_collection_key': None,
                        'size': len(content),
                        'file_hash': hashlib.sha256(content).hexdigest(),
                    },
                    data={},
                    created_at=1,
                    updated_at=1,
                )
            )
        await session.commit()
        commit, calls = session.commit, []

        async def interrupted_commit():
            calls.append(True)
            if len(calls) == 2:
                if after_commit:
                    await commit()
                raise RuntimeError('later commit failed')
            await commit()

        with monkeypatch.context() as patch:
            patch.setattr(session, 'commit', interrupted_commit)
            with pytest.raises(RuntimeError):
                await retirement.retire(
                    session, Client(), upload_dir=tmp_path, export_dir=tmp_path / 'exports', apply=True
                )
        assert (await session.get(File, 'a')).path is None and not (tmp_path / 'a').exists()
        b = await session.get(File, 'b')
        assert (b.path is None) is after_commit
        assert (tmp_path / 'b').exists()
        if after_commit:
            assert b.meta['soev_retire_path'] == str(tmp_path / 'b')
        await session.rollback()
        await retirement.retire(session, Client(), upload_dir=tmp_path, export_dir=tmp_path / 'exports', apply=True)
        assert (await session.get(File, 'b')).path is None and not (tmp_path / 'b').exists()
    await engine.dispose()


def test_text_only_row_is_never_eligible_for_deletion_even_without_history():
    decision = retirement.decide(
        SimpleNamespace(meta={}, data={'content': 'only text'}),
        held=False,
        local_bytes=False,
        protected=False,
        linked=False,
    )
    assert decision.category == 'unknown_without_bytes'
    assert decision.remove_row is False
