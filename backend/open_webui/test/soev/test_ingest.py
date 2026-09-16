"""Ingest submission through the shared fake API and real SQLite file rows."""

import base64
import datetime as dt
import hashlib
import importlib
import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

FILE_BYTES = b'%PDF-1.7 stored file content'
SERVICE = 'owui:service:webui'


@pytest_asyncio.fixture
async def env(identity_config, fake_api, monkeypatch, tmp_path):
    """Wire the real client to the shared fake and isolate Files in a temporary SQLite database."""
    identity, _ = identity_config
    module = importlib.import_module('open_webui.soev.ingest')
    files = importlib.import_module('open_webui.models.files')
    storage = importlib.import_module('open_webui.storage.provider')
    monkeypatch.setattr(module, 'Storage', storage.LocalStorageProvider())
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(fake_api.handle), **kwargs),
    )
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/files.db')
    async with engine.begin() as connection:
        await connection.run_sync(files.File.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    monkeypatch.setattr(files, 'get_async_db_context', context)
    client = identity.build_client()
    await identity.ensure_link('owui:user:alice', client)
    await client.send(
        'POST',
        '/v1/collections',
        {
            'key': 'kb',
            'name': 'Research',
            'visibility': 'restricted',
            'principals': [SERVICE, 'owui:user:alice'],
            'writers': ['owui:user:alice'],
        },
        as_user='owui:user:alice',
        idempotency_key='collection:kb',
    )
    fake_api.requests.clear()
    yield SimpleNamespace(module=module, files=files, api=fake_api, client=client, identity=identity, tmp_path=tmp_path)
    await engine.dispose()


async def new_file(env, **meta):
    source_id = str(uuid4())
    path = env.tmp_path / source_id
    path.write_bytes(FILE_BYTES)
    row = await env.files.Files.insert_new_file(
        'alice',
        env.files.FileForm(
            id=source_id,
            filename='original.pdf',
            path=str(path),
            hash='client-checksum',
            data={'status': 'pending', 'content': 'preserved content'},
            meta={'status': 'pending', **meta},
        ),
    )
    assert row is not None
    return row


async def submit(env, file, **kwargs):
    return await env.module.submit(file, collection_key='kb', user_id='alice', client=env.client, **kwargs)


@pytest.mark.asyncio
async def test_submit_declares_the_file_and_puts_its_bytes_then_commits(env, monkeypatch):
    """A file declaration supplies presigned headers and becomes queued only after its upload and commit."""
    file = await new_file(env, name='Report.pdf', content_type='application/pdf')
    loop_thread = threading.get_ident()
    storage_threads, read_threads = [], []
    get_file, read_bytes = env.module.Storage.get_file, Path.read_bytes

    def resolve(path):
        storage_threads.append(threading.get_ident())
        return get_file(path)

    def read(path):
        read_threads.append(threading.get_ident())
        return read_bytes(path)

    monkeypatch.setattr(env.module.Storage, 'get_file', resolve)
    monkeypatch.setattr(Path, 'read_bytes', read)
    job_id = await submit(env, file)
    assert len(storage_threads) == 1 and storage_threads == read_threads
    assert storage_threads[0] != loop_thread
    create, upload, commit = env.api.requests
    digest = hashlib.sha256(FILE_BYTES).hexdigest()
    assert [(r.method, r.url.path) for r in env.api.requests] == [
        ('POST', '/v1/jobs'),
        ('PUT', f'/uploads/{digest}'),
        ('POST', f'/v1/jobs/{job_id}/commit'),
    ]
    assert json.loads(create.content) == {
        'collection_key': 'kb',
        'documents': [
            {
                'source_id': file.id,
                'filename': 'Report.pdf',
                'title': 'Report.pdf',
                'content_type': 'application/pdf',
                'size': len(FILE_BYTES),
                'sha256': digest,
                'created_at': dt.datetime.fromtimestamp(file.created_at, dt.UTC).isoformat().replace('+00:00', 'Z'),
            }
        ],
    }
    assert str(upload.url) == f'https://soev.invalid/uploads/{digest}'
    assert upload.headers['Content-Length'] == str(len(FILE_BYTES))
    assert upload.headers['x-amz-checksum-sha256'] == base64.b64encode(hashlib.sha256(FILE_BYTES).digest()).decode()
    assert upload.content == FILE_BYTES and env.api.uploads[job_id, file.id] == FILE_BYTES
    assert create.headers['Idempotency-Key'] == f'ingest:{file.id}:kb:{digest}:1'
    assert commit.headers['Idempotency-Key'] == create.headers['Idempotency-Key'] + ':commit'
    assert env.api.jobs[job_id]['status'] == 'QUEUED'


@pytest.mark.asyncio
async def test_submit_computes_sha256_from_the_stored_bytes_not_the_row(env):
    """The byte digest replaces a client checksum in the declaration, replay key, and persisted job."""
    file = await new_file(env)
    job_id = await submit(env, file, attempt=3)
    digest = hashlib.sha256(FILE_BYTES).hexdigest()
    assert digest != file.hash
    document = env.api.jobs[job_id]['documents'][0]
    assert document['sha256'] == digest
    assert document['filename'] == document['title'] == file.filename
    assert document['content_type'] == 'application/octet-stream'
    assert env.api.requests[0].headers['Idempotency-Key'] == f'ingest:{file.id}:kb:{digest}:3'
    row = await env.files.Files.get_file_by_id(file.id)
    assert row.meta['soev_job']['sha256'] == digest
    assert row.hash == 'client-checksum'


@pytest.mark.asyncio
@pytest.mark.parametrize('relative_path,path', [('a/b/c.pdf', 'a/b'), ('c.pdf', None), ('', None), (None, None)])
async def test_submit_sends_the_folder_as_the_document_path(env, relative_path, path):
    """Only the directory component of a relative path is declared and retained for the poller."""
    file = await new_file(env, relative_path=relative_path)
    file.created_at = 0 if path else None
    job_id = await submit(env, file)
    document = env.api.jobs[job_id]['documents'][0]
    if path:
        assert document['path'] == path
        assert document['created_at'] == '1970-01-01T00:00:00Z'
    else:
        assert 'path' not in document and 'created_at' not in document
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_job']['path'] == path


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['', 'produced text', 'é' * 131072])
async def test_submit_under_the_inline_budget_sends_text_and_no_upload(env, text):
    """Generated text up to the exact UTF-8 budget queues with no storage read, PUT, or commit."""
    file = await new_file(env)
    file.path = None
    job_id = await submit(env, file, text=text)
    document = env.api.jobs[job_id]['documents'][0]
    assert document['text'] == text
    assert 'size' not in document and 'sha256' not in document
    assert [(r.method, r.url.path) for r in env.api.requests] == [('POST', '/v1/jobs')]
    assert env.api.jobs[job_id]['status'] == 'QUEUED' and not env.api.uploads
    row = await env.files.Files.get_file_by_id(file.id)
    digest = hashlib.sha256(text.encode()).hexdigest()
    assert row.meta['soev_job']['sha256'] == digest
    assert row.meta['soev_job']['committed'] is True
    assert env.api.requests[0].headers['Idempotency-Key'] == f'ingest:{file.id}:kb:{digest}:1'


@pytest.mark.asyncio
@pytest.mark.parametrize('budget,text', [(262144, 'é' * 131072 + 'a'), (4, 'ééa')])
async def test_submit_over_the_inline_budget_uploads_the_text_bytes(env, monkeypatch, budget, text):
    """The configured UTF-8 limit selects a file declaration over generated text bytes."""
    monkeypatch.setattr(env.module.config, 'SOEV_API_INLINE_DOCUMENT_BYTES', budget)
    file = await new_file(env)
    file.path = None
    job_id = await submit(env, file, text=text)
    document = env.api.jobs[job_id]['documents'][0]
    assert 'text' not in document
    assert document['size'] == len(text.encode())
    assert document['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert env.api.uploads[job_id, file.id] == text.encode()
    assert env.api.jobs[job_id]['status'] == 'QUEUED'


@pytest.mark.asyncio
async def test_submit_runs_under_the_users_assertion(env, monkeypatch):
    """Default clients mint the explicit user's assertion for create and commit and keep the PUT credential free."""
    file = await new_file(env)
    builds = []
    build_client = env.identity.build_client

    def build():
        builds.append(True)
        return build_client()

    monkeypatch.setattr(env.identity, 'build_client', build)
    await env.module.submit(file, collection_key='kb', user_id='alice')
    create, upload, commit = env.api.requests
    for request in (create, commit):
        encoded = request.headers['X-Soev-Subject'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        assert claims['sub'] == 'owui:user:alice'
    assert create.headers['X-Soev-Subject'] != commit.headers['X-Soev-Subject']
    assert 'X-Soev-Subject' not in upload.headers and 'Authorization' not in upload.headers
    await env.module.cancel(file)
    assert len(builds) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'PUT', 'commit'])
async def test_submit_records_the_job_on_the_row_and_marks_processing(env, monkeypatch, failure):
    """The job is discoverable and uncommitted before upload and stays recoverable if PUT or commit fails."""
    file = await new_file(env, relative_path='a/report.pdf')
    handle = env.api.handle
    snapshots = []

    async def observe(request):
        if request.method == 'PUT' or request.url.path.endswith('/commit'):
            row = await env.files.Files.get_file_by_id(file.id)
            snapshots.append(row)
            assert row.meta['soev_job']['committed'] is False
            assert [row.id for row in await env.files.Files.get_files_with_soev_jobs()] == [file.id]
            if request.method == failure or (failure == 'commit' and request.url.path.endswith('/commit')):
                env.api.failures.append(403)
        return handle(request)

    monkeypatch.setattr(env.api, 'handle', observe)
    before = int(time.time())
    if failure:
        with pytest.raises(env.module.SoevApiError):
            await submit(env, file)
        job_id = next(iter(env.api.jobs))
    else:
        job_id = await submit(env, file)
    row = await env.files.Files.get_file_by_id(file.id)
    job = row.meta['soev_job']
    assert job == {
        'job_id': job_id,
        'collection_key': 'kb',
        'path': 'a',
        'sha256': hashlib.sha256(FILE_BYTES).hexdigest(),
        'attempt': 1,
        'submitted_at': job['submitted_at'],
        'committed': failure is None,
    }
    assert isinstance(job['submitted_at'], int) and before <= job['submitted_at'] <= int(time.time())
    for saved in [*snapshots, row]:
        assert saved.meta['soev_collection_key'] == 'kb'
        assert saved.meta['status'] == saved.data['status'] == 'processing'
        assert saved.data['content'] == 'preserved content'
        assert saved.meta['relative_path'] == 'a/report.pdf'


@pytest.mark.asyncio
@pytest.mark.parametrize('attempt', [1, 2])
async def test_a_second_submit_while_in_flight_is_refused_without_a_request(env, attempt):
    """Both stale and refreshed caller rows refuse another submission, including retry attempts."""
    file = await new_file(env)
    await submit(env, file)
    env.api.requests.clear()
    current = await env.files.Files.get_file_by_id(file.id)
    for row in (file, current):
        with pytest.raises(env.module.IngestBusy):
            await submit(env, row, attempt=attempt)
    assert not env.api.requests


@pytest.mark.asyncio
async def test_a_refused_job_leaves_the_row_untouched(env):
    """A linked reader without writer membership receives the fake's 403 without local state changes."""
    file = await new_file(env)
    env.api.collections['kb']['writers'] = []
    with pytest.raises(env.module.SoevApiError) as error:
        await submit(env, file)
    assert error.value.status == 403 and error.value.code == 'scope_insufficient'
    assert await env.files.Files.get_file_by_id(file.id) == file
    assert 'soev_job' not in file.meta
    assert [(r.method, r.url.path) for r in env.api.requests] == [('POST', '/v1/jobs')]
    assert not env.api.jobs


@pytest.mark.asyncio
async def test_the_attachments_collection_is_created_once_per_user(env):
    """Repeated ensures read the same private per-user collection with explicit readers and writers."""
    for user in ('alice', 'bob'):
        key = env.module.attachments_collection_key(user)
        for _ in range(2):
            assert await env.module.ensure_attachments_collection(user, env.client) == key
        creates = [
            r
            for r in env.api.requests
            if r.method == 'POST' and r.url.path == '/v1/collections' and json.loads(r.content)['key'] == key
        ]
        assert len(creates) == 1
        request = creates[0]
        assert json.loads(request.content) == {
            'key': key,
            'name': 'Chat attachments',
            'visibility': 'restricted',
            'principals': [SERVICE, f'owui:user:{user}'],
            'writers': [f'owui:user:{user}'],
        }
        assert request.headers['Idempotency-Key'] == f'kb:{key}'
        reads = [r for r in env.api.requests if r.url.path == f'/v1/collections/{key}']
        assert len(reads) == 2
        assert all('X-Soev-Subject' in r.headers for r in [request, *reads])
    assert 'owui-attachments-alice' != env.module.attachments_collection_key('bob')


def test_attachments_collections_are_never_knowledge_bases(env):
    """The reserved prefix identifies attachment collections without matching ordinary knowledge keys."""
    assert env.module.ATTACHMENTS_PREFIX == 'owui-attachments-'
    assert env.module.attachments_collection_key('alice') == 'owui-attachments-alice'
    assert env.module.is_attachments_collection('owui-attachments-alice')
    for key in ('kb', '', 'owui-attachments', 'kb-owui-attachments-alice'):
        assert not env.module.is_attachments_collection(key)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['QUEUED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'EXPIRED', 'missing'])
async def test_cancel_clears_the_row_even_when_terminal(env, status):
    """Cancellation clears both metadata keys after success, terminal conflict, or a missing remote job."""
    file = await new_file(env)
    await env.module.cancel(file, client=env.client)
    assert not env.api.requests
    job_id = await submit(env, file)
    if status == 'missing':
        del env.api.jobs[job_id]
    else:
        env.api.advance(job_id, status)
    env.api.requests.clear()
    await env.module.cancel(file, client=env.client)
    row = await env.files.Files.get_file_by_id(file.id)
    assert row.meta['soev_job'] is None and row.meta['soev_collection_key'] is None
    assert row.meta['status'] == row.data['status'] == 'processing'
    assert row.data['content'] == 'preserved content'
    assert [(r.method, r.url.path) for r in env.api.requests] == [('POST', f'/v1/jobs/{job_id}/cancel')]
    assert await env.files.Files.get_files_with_soev_jobs() == []
    assert await env.files.Files.get_unlanded_files_for_collection('kb') == []
    await env.module.cancel(row, client=env.client)
    assert len(env.api.requests) == 1


@pytest.mark.asyncio
async def test_unlanded_files_select_on_the_flat_collection_key(env):
    """SQLite queries include job objects and flat collection matches while excluding cleared or malformed jobs."""
    flat = await new_file(env, soev_collection_key='kb', soev_job={'job_id': 'job'})
    quoted = await new_file(env, soev_collection_key='"kb"', soev_job={})
    await new_file(env, soev_collection_key=None, soev_job=None)
    await new_file(env, soev_collection_key='other', soev_job='null')
    await new_file(env, soev_job=[])
    await new_file(env, soev_job='{}')
    await new_file(env, data={'knowledge_id': 'kb'})
    assert {row.id for row in await env.files.Files.get_unlanded_files_for_collection('kb')} == {flat.id, quoted.id}
    assert {row.id for row in await env.files.Files.get_files_with_soev_jobs()} == {flat.id, quoted.id}
    assert await env.files.Files.get_unlanded_files_for_collection('absent') == []
