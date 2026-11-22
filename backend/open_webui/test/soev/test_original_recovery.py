"""Recover base-code originals without changing local or platform readership."""

import asyncio
import json
import zipfile
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException, UploadFile

from open_webui.test.soev.test_ingest import FILE_BYTES, env, new_file, submit


@pytest.mark.asyncio
@pytest.mark.parametrize('named', [False, True])
async def test_base_completed_row_resolves_as_reader_and_caches_collection(env, monkeypatch, named):
    from open_webui.routers import files

    file = await new_file(env, status='completed', content_type='application/pdf', soev_collection_key=None)
    env.api.add_document('kb', file.id)
    calls = []
    handle = env.api.handle

    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/original'):
            return httpx.Response(303, headers={'Location': '/v1/downloads/once'})
        if request.url.path == '/v1/downloads/once':
            return httpx.Response(200, content=FILE_BYTES)
        return handle(request)

    monkeypatch.setattr(env.api, 'handle', respond)
    user = SimpleNamespace(id='alice', role='user')
    for _ in range(2):
        result = await (
            files.get_file_content_by_id(file.id, 'original.pdf', user=user, db=None)
            if named
            else files.get_file_content_by_id_inline(file.id, user=user, attachment=False, db=None)
        )
        assert b''.join([c async for c in result.body_iterator]) == FILE_BYTES
    assert sum(r.url.path == '/v1/documents' for r in calls) == 1
    assert all(r.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice' for r in calls)
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_read_collection'] == 'kb'
    with pytest.raises(HTTPException) as denied:
        await files.get_file_content_by_id_inline(file.id, user=SimpleNamespace(id='bob', role='user'), db=None)
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_first_publication_is_pinned_and_deleted_collection_uses_lookup(env, monkeypatch):
    from open_webui.soev import jobs, originals

    file = await new_file(env, status='completed', soev_read_collection='first', soev_collection_key='copy')
    await jobs._finish(file, {'collection_key': 'copy'})
    saved = await env.files.Files.get_file_by_id(file.id)
    assert saved.meta['soev_read_collection'] == 'first'
    env.api.add_document('kb', file.id)
    calls = []

    @asynccontextmanager
    async def stream(path, **kwargs):
        calls.append(path)
        if '/first/' in path:
            raise env.module.SoevApiError(404, 'not_found', 'gone')
        yield httpx.Response(200, content=FILE_BYTES)

    monkeypatch.setattr(env.client, 'stream_response', stream)
    async with originals.open_original(saved, client=env.client, as_user='owui:user:alice') as response:
        assert await response.aread() == FILE_BYTES
    assert calls == [originals.path_for('first', file.id), originals.path_for('kb', file.id)]
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_read_collection'] == 'kb'


@pytest.mark.asyncio
@pytest.mark.parametrize('filename', ['document.pdf', 'document.png', 'document'])
async def test_pdf_declared_image_is_relayed_as_document(env, monkeypatch, filename):
    from open_webui.routers import files

    values = {'rag.file.allowed_extensions': ['pdf', 'png'], 'rag.file.max_size': 1}
    monkeypatch.setattr(files.Config, 'get', AsyncMock(side_effect=lambda key: values.get(key)))
    monkeypatch.setattr(files.Storage, 'upload_file', Mock(side_effect=AssertionError('PDF must not be retained')))
    monkeypatch.setattr(files, 'emit_file_status', AsyncMock())
    result = await files.upload_file_handler(
        SimpleNamespace(),
        UploadFile(file=BytesIO(FILE_BYTES), filename=filename, headers={'content-type': 'image/png'}),
        metadata={},
        process=True,
        db=None,
        user=SimpleNamespace(id='alice', email='alice@invalid', name='Alice', role='user'),
    )
    assert result['path'] is None
    assert result['meta']['content_type'] == 'application/pdf'
    assert result['meta']['soev_job']['committed']
    job = env.api.jobs[result['meta']['soev_job']['job_id']]
    assert job['documents'][0]['content_type'] == 'application/pdf'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['store', 'cancelled', 'configuration', 'commit'])
async def test_failed_attempt_is_terminal_cancelled_and_retry_gets_new_key(env, monkeypatch, failure):
    file = await new_file(env)
    handle = env.api.handle

    def respond(request):
        if request.url.host == 'store.invalid':
            if failure == 'cancelled':
                raise asyncio.CancelledError
            if failure == 'store':
                return httpx.Response(503)
        if failure == 'commit' and request.url.path.endswith('/commit'):
            return httpx.Response(503)
        return handle(request)

    with monkeypatch.context() as patch:
        patch.setattr(env.api, 'handle', respond)
        if failure == 'configuration':
            patch.setattr(env.identity, 'build_client', Mock(side_effect=RuntimeError('configuration')))
        with pytest.raises((env.module.SoevApiError, asyncio.CancelledError, RuntimeError)):
            if failure == 'configuration':
                await env.module.submit(file, collection_key='kb', user_id='alice', source=BytesIO(FILE_BYTES))
            else:
                await submit(env, file)
    saved = await env.files.Files.get_file_by_id(file.id)
    assert saved.meta['status'] == 'failed'
    assert saved.meta.get('soev_job') is None
    assert __import__('pathlib').Path(file.path).read_bytes() == FILE_BYTES
    assert all(j['status'] == 'CANCELLED' for j in env.api.jobs.values())
    creates = [r for r in env.api.requests if r.url.path == '/v1/jobs']
    await submit(env, saved)
    if creates:
        latest = next(r for r in reversed(env.api.requests) if r.url.path == '/v1/jobs')
        assert latest.headers['Idempotency-Key'] != creates[0].headers['Idempotency-Key']
        assert latest.headers['Idempotency-Key'].endswith(':2')


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['admission', 'streaming'])
async def test_original_source_403_is_not_a_store_refresh(env, monkeypatch, stage):
    file = await new_file(env, soev_read_collection='kb')

    class Original(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield FILE_BYTES[:4]
            raise env.module.SoevApiError(403, 'scope_insufficient', 'Original denied')

    @asynccontextmanager
    async def denied(*args, **kwargs):
        if stage == 'admission':
            raise env.module.SoevApiError(403, 'scope_insufficient', 'Original denied')
        yield httpx.Response(200, stream=Original())

    monkeypatch.setattr(env.client, 'stream_response', denied)
    with pytest.raises(env.module.SoevApiError) as caught:
        await env.module.submit(file, collection_key='kb', user_id='alice', client=env.client)
    assert caught.value.code == 'scope_insufficient'
    assert sum(r.url.path == '/v1/jobs' for r in env.api.requests) == 1
    assert (await env.files.Files.get_file_by_id(file.id)).meta['status'] == 'failed'


@pytest.mark.asyncio
async def test_export_contains_platform_original_and_unavailable_manifest(env, monkeypatch, tmp_path):
    from open_webui.soev import export_files
    from open_webui.test.soev.test_jobs import owner_login

    first = await new_file(env, status='completed', soev_read_collection='kb', content_type='application/pdf')
    second = await new_file(env, status='completed', soev_read_collection='denied', content_type='application/pdf')

    @asynccontextmanager
    async def stream(path, **kwargs):
        if '/denied/' in path:
            raise env.module.SoevApiError(403, 'forbidden', 'denied')
        yield httpx.Response(200, content=FILE_BYTES)

    monkeypatch.setattr(env.client, 'stream_response', stream)
    monkeypatch.setattr(env.identity, 'build_client', lambda: env.client)
    from open_webui.services.export.service import ExportService

    monkeypatch.setattr(ExportService, 'get_export_dir', lambda user_id: tmp_path)
    with owner_login():
        target = await ExportService.build_export_zip('alice', {'files': [first.model_dump(), second.model_dump()]})
    with zipfile.ZipFile(target) as archive:
        assert archive.read(f'files/uploads/{first.id}_original.pdf') == FILE_BYTES
        assert json.loads(archive.read('files/unavailable.json')) == [{'id': second.id, 'reason': 'unavailable'}]
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['contents']['files_included'] == manifest['contents']['files_unavailable'] == 1


@pytest.mark.asyncio
async def test_unconfigured_standalone_upload_and_read_keep_local_mode(env, monkeypatch):
    from open_webui.routers import files

    monkeypatch.setattr(env.module.config, 'SOEV_API_URL', '')
    monkeypatch.setattr(files.Config, 'get', AsyncMock(return_value=None))
    path = env.tmp_path / 'standalone.pdf'

    def write(source, *args):
        path.write_bytes(source.read())
        return path.read_bytes(), str(path)

    monkeypatch.setattr(files.Storage, 'upload_file', write)
    monkeypatch.setattr(files.Storage, 'get_file', lambda value: value)
    user = SimpleNamespace(id='alice', role='user', email='alice@invalid', name='Alice')
    result = await files.upload_file_handler(
        SimpleNamespace(),
        UploadFile(file=BytesIO(FILE_BYTES), filename='doc.pdf'),
        metadata={},
        process=False,
        user=user,
        db=None,
    )
    assert result.path == str(path)
    response = await files.get_file_content_by_id_inline(result.id, user=user, attachment=False, db=None)
    assert str(response.path) == str(path)
    assert not env.api.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['image', 'video'])
async def test_processed_media_keeps_its_original_local_display(env, monkeypatch, kind):
    import base64
    from open_webui.routers import files

    data = (
        base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        if kind == 'image'
        else b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 32
    )
    mime, ext = ('image/png', 'png') if kind == 'image' else ('video/mp4', 'mp4')
    values = {
        'rag.content_extraction_engine': 'external',
        'rag.content_extraction.supported_media_mime_types': ['image/png'],
    }
    monkeypatch.setattr(files.Config, 'get', AsyncMock(side_effect=lambda key: values.get(key)))
    monkeypatch.setattr(files, 'emit_file_status', AsyncMock())
    path = env.tmp_path / ('media.' + ext)

    def write(source, *args):
        path.write_bytes(source.read())
        return path.read_bytes(), str(path)

    monkeypatch.setattr(files.Storage, 'upload_file', write)
    monkeypatch.setattr(files.Storage, 'get_file', lambda value: value)
    user = SimpleNamespace(id='alice', role='user', email='alice@invalid', name='Alice')
    result = await files.upload_file_handler(
        SimpleNamespace(),
        UploadFile(file=BytesIO(data), filename='media.' + ext, headers={'content-type': mime}),
        metadata={},
        process=True,
        user=user,
        db=None,
    )
    row = await env.files.Files.get_file_by_id(result['id'])
    assert row.path == str(path)
    if kind == 'video':
        assert row.meta['status'] == 'completed' and not row.meta.get('soev_job')
    else:
        assert row.meta['soev_job']['committed']
    env.api.requests.clear()
    response = await files.get_file_content_by_id_inline(row.id, user=user, attachment=False, db=None)
    assert str(response.path) == str(path)
    assert not env.api.requests


@pytest.mark.asyncio
async def test_later_completion_with_a_stale_row_cannot_replace_first_source(env, monkeypatch):
    from open_webui.soev import jobs

    monkeypatch.setattr(jobs, 'emit_file_status', AsyncMock())
    file = await new_file(env, content_type='application/pdf')
    await jobs._finish(file, {'collection_key': 'first'})
    await jobs._finish(file, {'collection_key': 'copy'})
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_read_collection'] == 'first'
