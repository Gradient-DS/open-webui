"""Ingest submission through the shared fake API and real SQLite file rows."""

import asyncio
import datetime as dt
import hashlib
import importlib
import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
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
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda request: fake_api.handle(request)), **kwargs
        ),
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
    """A file declaration streams through the authenticated API before commit."""
    file = await new_file(env, name='Report.pdf', content_type='application/pdf')
    loop_thread = threading.get_ident()
    storage_threads, read_threads = [], []
    get_file, file_info = env.module.Storage.get_file, env.module._file_info

    def resolve(path):
        storage_threads.append(threading.get_ident())
        return get_file(path)

    def read(path):
        read_threads.append(threading.get_ident())
        return file_info(path)

    monkeypatch.setattr(env.module.Storage, 'get_file', resolve)
    monkeypatch.setattr(env.module, '_file_info', read)
    job_id = await submit(env, file)
    assert len(storage_threads) == 1 and storage_threads == read_threads
    assert storage_threads[0] != loop_thread
    create, upload, commit = env.api.requests
    digest = hashlib.sha256(FILE_BYTES).hexdigest()
    assert [(r.method, r.url.path) for r in env.api.requests] == [
        ('POST', '/v1/jobs'),
        ('PUT', f'/v1/jobs/{job_id}/uploads/{file.id}'),
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
    assert str(upload.url) == f'https://soev.invalid/v1/jobs/{job_id}/uploads/{file.id}'
    assert upload.headers['Content-Length'] == str(len(FILE_BYTES))
    assert upload.headers['Content-Type'] == 'application/pdf'
    assert upload.headers['Authorization'] == create.headers['Authorization']
    assert 'x-amz-checksum-sha256' not in upload.headers
    assert upload.content == FILE_BYTES and env.api.uploads[job_id, file.id] == FILE_BYTES
    assert create.headers['Idempotency-Key'] == f'ingest:{file.id}:kb:{digest}:1'
    assert commit.headers['Idempotency-Key'] == create.headers['Idempotency-Key'] + ':commit'
    assert env.api.jobs[job_id]['status'] == 'QUEUED'


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [None, 'fictional generated text'])
@pytest.mark.parametrize(
    'status', ['QUEUED', 'RUNNING', 'SUCCEEDED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'EXPIRED']
)
async def test_submit_replays_an_existing_job_without_uploading_again(env, monkeypatch, status, text):
    monkeypatch.setattr(env.module.config, 'SOEV_API_INLINE_DOCUMENT_BYTES', 4)
    file = await new_file(env)
    job_id = await submit(env, file, text=text)
    env.api.advance(job_id, status)
    await env.files.Files.update_file_metadata_by_id(file.id, {'soev_job': None})
    file = await env.files.Files.get_file_by_id(file.id)
    env.api.requests.clear()

    assert await submit(env, file, text=text) == job_id

    assert [(request.method, request.url.path) for request in env.api.requests] == [
        ('POST', '/v1/jobs'),
        ('POST', f'/v1/jobs/{job_id}/commit'),
    ]
    assert len(env.api.jobs) == 1
    assert env.api.jobs[job_id]['status'] == status
    row = await env.files.Files.get_file_by_id(file.id)
    assert row.meta['soev_job']['job_id'] == job_id
    assert row.meta['soev_job']['committed'] is True
    assert row.meta['status'] == row.data['status'] == 'processing'
    assert [pending.id for pending in await env.files.Files.get_files_with_soev_jobs()] == [file.id]


@pytest.mark.asyncio
@pytest.mark.parametrize('declared', [None, 'application/octet-stream', 'image/png'])
async def test_submit_forwards_derived_content_type(env, declared):
    file = await new_file(env, name='notes.md', content_type=declared)
    job_id = await submit(env, file)
    document = env.api.jobs[job_id]['documents'][0]
    assert document['filename'] == 'notes.md'
    assert document['content_type'] == 'text/markdown'


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
    assert document['content_type'] == 'application/pdf'
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
    file = await new_file(env, name='recording.mp3', content_type='audio/mpeg')
    file.path = None
    job_id = await submit(env, file, text=text)
    document = env.api.jobs[job_id]['documents'][0]
    assert document['text'] == text
    assert document['content_type'] == 'text/plain'
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
    file = await new_file(env, name='recording.mp3', content_type='audio/mpeg')
    file.path = None
    job_id = await submit(env, file, text=text)
    document = env.api.jobs[job_id]['documents'][0]
    assert 'text' not in document
    assert document['size'] == len(text.encode())
    assert document['content_type'] == 'text/plain'
    assert document['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert env.api.uploads[job_id, file.id] == text.encode()
    assert env.api.jobs[job_id]['status'] == 'QUEUED'


@pytest.mark.asyncio
async def test_submit_runs_under_the_users_assertion(env, monkeypatch):
    """Create, upload and commit each carry the acting user with a fresh assertion."""
    file = await new_file(env)
    builds = []
    build_client = env.identity.build_client

    def build():
        builds.append(True)
        return build_client()

    monkeypatch.setattr(env.identity, 'build_client', build)
    await env.module.submit(file, collection_key='kb', user_id='alice')
    create, upload, commit = env.api.requests
    for request in (create, upload, commit):
        assert request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice'
        assert 'X-Soev-' + 'Subject' not in request.headers
        assert request.headers['Authorization'] == create.headers['Authorization']
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
            'personal': True,
            'visibility': 'restricted',
            'principals': [SERVICE, f'owui:user:{user}'],
            'writers': [f'owui:user:{user}'],
            'tags': ['chat-attachments'],
        }
        assert request.headers['Idempotency-Key'] == f'kb:{key}'
        reads = [r for r in env.api.requests if r.url.path == f'/v1/collections/{key}']
        assert len(reads) == 2
        assert all('X-Soev-User-Token' in r.headers for r in [request, *reads])
    assert 'owui-attachments-alice' != env.module.attachments_collection_key('bob')


@pytest.mark.asyncio
@pytest.mark.parametrize('tags', [None, [], ['existing', 'another']])
async def test_existing_attachments_collection_gets_tagged_once(env, tags):
    key = await env.module.ensure_attachments_collection('alice', env.client)
    if tags is None:
        del env.api.collections[key]['tags']
    else:
        env.api.collections[key]['tags'] = tags
    env.api.requests.clear()

    for _ in range(2):
        assert await env.module.ensure_attachments_collection('alice', env.client) == key

    assert [(r.method, r.url.path) for r in env.api.requests] == [
        ('GET', f'/v1/collections/{key}'),
        ('PATCH', f'/v1/collections/{key}'),
        ('GET', f'/v1/collections/{key}'),
    ]
    update = env.api.requests[1]
    expected_tags = [*(tags or []), 'chat-attachments']
    assert json.loads(update.content) == {'tags': expected_tags}
    assert env.api.collections[key]['tags'] == expected_tags
    assert update.headers['Idempotency-Key'] == f'kb:{key}:tag:chat-attachments'
    assert 'X-Soev-User-Token' in update.headers


@pytest.mark.asyncio
async def test_already_tagged_attachments_collection_needs_no_update(env):
    key = await env.module.ensure_attachments_collection('alice', env.client)
    tags = ['existing', 'chat-attachments']
    env.api.collections[key]['tags'] = tags
    env.api.requests.clear()

    assert await env.module.ensure_attachments_collection('alice', env.client) == key

    assert [(r.method, r.url.path) for r in env.api.requests] == [('GET', f'/v1/collections/{key}')]
    assert env.api.collections[key]['tags'] == tags


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


@pytest.mark.asyncio
async def test_pending_files_use_soev_collection_and_data_status_without_a_knowledge_join(env):
    """Pending results accept bare and quoted collection keys and exclude terminal or legacy rows."""
    pending = await new_file(env, soev_collection_key='kb')
    processing = await new_file(env, soev_collection_key='"kb"', status='completed')
    await env.files.Files.update_file_data_by_id(processing.id, {'status': 'processing'})
    for status in ('completed', 'error'):
        terminal = await new_file(env, soev_collection_key='kb')
        await env.files.Files.update_file_data_by_id(terminal.id, {'status': status})
    await new_file(env, soev_collection_key='other')
    await new_file(env, data={'knowledge_id': 'kb'})
    assert {row.id for row in await env.files.Files.get_pending_files_for_knowledge('kb')} == {
        pending.id,
        processing.id,
    }
    assert await env.files.Files.get_pending_files_for_knowledge('absent') == []


async def rendition_file(env, data=None):
    return await env.files.Files.insert_new_file(
        'alice', env.files.FileForm(id=str(uuid4()), filename='report.pdf', path='', data=data or {})
    )


async def data_content(file):
    from open_webui.routers.files import get_file_data_content_by_id

    return await get_file_data_content_by_id(file.id, user=SimpleNamespace(id='alice', role='user'), db=None)


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [{}, {'content': ''}, {'content': None}])
async def test_data_content_serves_the_rendition_for_a_landed_file(env, data):
    """The content route reads markdown on demand without persisting it on the file row."""
    file = await rendition_file(env, data)
    env.api.add_document('kb', file.id, rendition='# Landed\n\nMarkdown content.')
    assert await data_content(file) == {'content': '# Landed\n\nMarkdown content.'}
    assert (await env.files.Files.get_file_by_id(file.id)).model_dump() == file.model_dump()
    assert [(r.method, r.url.path) for r in env.api.requests] == [
        ('GET', '/v1/documents'),
        ('GET', f'/v1/collections/kb/documents/{file.id}/content'),
    ]
    assert dict(env.api.requests[0].url.params) == {'source_id': file.id}


async def file_view(file):
    from open_webui.routers.files import get_file_by_id

    return await get_file_by_id(file.id, user=SimpleNamespace(id='alice', role='user'), db=None)


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [{}, {'content': ''}, {'content': None}])
async def test_the_file_view_carries_the_rendition_for_a_landed_file(env, data):
    """GET /files/{id} fills data.content from soev-api so the preview modal shows the text."""
    file = await rendition_file(env, data)
    env.api.add_document('kb', file.id, rendition='# Landed\n\nMarkdown content.')
    assert (await file_view(file)).data['content'] == '# Landed\n\nMarkdown content.'
    assert (await env.files.Files.get_file_by_id(file.id)).model_dump() == file.model_dump()


@pytest.mark.asyncio
async def test_the_file_view_keeps_stored_text_without_a_request(env):
    """Legacy rows with extracted text are returned as they are."""
    file = await rendition_file(env, {'content': 'stored text'})
    assert (await file_view(file)).data['content'] == 'stored text'
    assert env.api.requests == []


@pytest.mark.asyncio
async def test_the_file_view_is_unchanged_while_in_flight(env):
    """No rendition yet means the row is returned as stored."""
    file = await rendition_file(env)
    assert (await file_view(file)).model_dump() == file.model_dump()


@pytest.mark.asyncio
async def test_data_content_prefers_stored_text_for_a_legacy_file(env):
    """Legacy text wins over a remote rendition without issuing any API request."""
    file = await rendition_file(env, {'content': 'Legacy text'})
    env.api.add_document('kb', file.id, rendition='# New rendition')
    assert await data_content(file) == {'content': 'Legacy text'}
    assert not env.api.requests


@pytest.mark.asyncio
async def test_data_content_is_empty_while_in_flight(env):
    """A file without a landed document returns None from the helper and an empty route response."""
    file = await rendition_file(env)
    assert await env.module.rendition_of(file, 'alice', client=env.client) is None
    assert await data_content(file) == {'content': ''}
    assert all(r.url.path == '/v1/documents' for r in env.api.requests)


@pytest.mark.asyncio
async def test_the_rendition_is_read_under_the_users_assertion(env):
    """Both HTTP reads carry fresh assertions naming the requesting user."""
    file = await rendition_file(env)
    env.api.add_document('kb', file.id, rendition='# Markdown')
    assert await env.module.rendition_of(file, 'alice', client=env.client) == '# Markdown'
    assert len(env.api.requests) == 2
    assert all(
        request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice' for request in env.api.requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('has_attachment', [False, True])
async def test_rendition_prefers_own_attachments_then_returned_order(env, has_attachment):
    """Own attachments on later pages take precedence; other collections keep their returned order."""
    env.api.page_size = 1
    file = await rendition_file(env)
    for user in ('alice', 'bob'):
        key = env.module.attachments_collection_key(user)
        env.api.collections[key] = {**env.api.collections['kb'], 'key': key}
        if user == 'bob' or has_attachment:
            env.api.add_document(key, file.id, rendition=f'# {user}')
    env.api.add_document('kb', file.id, rendition='# Knowledge')
    env.api.requests.clear()
    expected = '# alice' if has_attachment else '# Knowledge'
    assert await env.module.rendition_of(file, 'alice', client=env.client) == expected
    lookups = [request for request in env.api.requests if request.url.path == '/v1/documents']
    assert [dict(request.url.params) for request in lookups] == [
        {'source_id': file.id},
        *({'source_id': file.id, 'cursor': str(cursor)} for cursor in range(1, 3 if has_attachment else 2)),
    ]
    assert all(
        request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice' for request in env.api.requests
    )


@pytest.mark.asyncio
async def test_rendition_keeps_returned_order_and_quotes_path_segments(env, monkeypatch):
    """Non-attachment keys keep server ordering and reserved characters remain inside path segments."""
    file = await rendition_file(env)
    file.id = 'file/with ?#%'
    lookup = AsyncMock(
        return_value={'data': [{'collection_key': 'z/ ?#%'}, {'collection_key': 'a'}], 'next_cursor': None}
    )
    read = AsyncMock(return_value='# Markdown')
    monkeypatch.setattr(env.client, 'get', lookup)
    monkeypatch.setattr(env.client, 'get_text', read)
    assert await env.module.rendition_of(file, 'alice', client=env.client) == '# Markdown'
    lookup.assert_awaited_once_with('/v1/documents', params={'source_id': file.id}, as_user='owui:user:alice')
    read.assert_awaited_once_with(
        '/v1/collections/z%2F%20%3F%23%25/documents/file%2Fwith%20%3F%23%25/content', as_user='owui:user:alice'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [404, 409])
async def test_rendition_missing_does_not_try_another_collection(env, monkeypatch, status):
    """A missing first rendition yields None even when another collection has markdown."""
    file = await rendition_file(env)
    key = await env.module.ensure_attachments_collection('alice', env.client)
    env.api.add_document(key, file.id)
    env.api.add_document('kb', file.id, rendition='# Other')
    if status == 404:
        original_get = env.client.get

        async def get_then_delete(*args, **kwargs):
            response = await original_get(*args, **kwargs)
            del env.api.documents[key, file.id]
            return response

        monkeypatch.setattr(env.client, 'get', get_then_delete)
    env.api.requests.clear()
    assert await env.module.rendition_of(file, 'alice', client=env.client) is None
    assert [r.url.path for r in env.api.requests] == [
        '/v1/documents',
        f'/v1/collections/{key}/documents/{file.id}/content',
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation,status,code',
    [
        ('get', 404, 'document_not_found'),
        ('get', 409, 'rendition_missing'),
        ('get', 403, 'forbidden'),
        ('get', 500, 'upstream_error'),
        ('get_text', 403, 'forbidden'),
        ('get_text', 409, 'other_conflict'),
        ('get_text', 500, 'upstream_error'),
    ],
)
async def test_rendition_propagates_other_api_errors(env, monkeypatch, operation, status, code):
    """Lookup and content errors propagate unless the content rendition is missing."""
    file = await rendition_file(env)
    env.api.add_document('kb', file.id, rendition='# Markdown')
    error = env.module.SoevApiError(status, code, 'Read failed')
    monkeypatch.setattr(env.client, operation, AsyncMock(side_effect=error))
    with pytest.raises(env.module.SoevApiError) as caught:
        await env.module.rendition_of(file, 'alice', client=env.client)
    assert caught.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [None, '', 'Legacy text'])
async def test_rendition_without_soev_uses_only_stored_content(env, monkeypatch, content):
    """Disabling soev prevents client construction and preserves legacy content."""
    file = await rendition_file(env, {'content': content})
    monkeypatch.setattr(env.module.config, 'SOEV_API_URL', '')
    build = AsyncMock(side_effect=AssertionError('Unexpected client construction'))
    monkeypatch.setattr(env.identity, 'build_client', build)
    assert await env.module.rendition_of(file, 'alice') == (content or None)
    build.assert_not_called()
    assert not env.api.requests


class _ChunkReader:
    def __init__(self, source, limit, loop_thread):
        self.source = source
        self.limit = limit
        self.loop_thread = loop_thread
        self.reads = 0
        self.closed = False

    def read(self, limit):
        assert 0 < limit <= self.limit
        assert threading.get_ident() != self.loop_thread
        self.reads += 1
        return self.source.read(limit)

    def close(self):
        self.closed = True
        self.source.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.mark.asyncio
async def test_large_upload_reads_bounded_chunks_only_as_the_transport_pulls(env, monkeypatch):
    from open_webui.soev.client import close_client

    file = await new_file(env)
    size = 100 * 1024 * 1024
    path = Path(file.path)
    with path.open('wb') as target:
        target.truncate(size)
    original_open = Path.open
    readers = []
    loop_thread = threading.get_ident()

    def opened(target, *args, **kwargs):
        source = original_open(target, *args, **kwargs)
        if target == path:
            reader = _ChunkReader(source, env.module.UPLOAD_CHUNK_BYTES, loop_thread)
            readers.append(reader)
            return reader
        return source

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.method != 'PUT':
                return env.api.handle(request)
            env.api.requests.append(request)
            assert len(readers) == 1 and readers[0].closed
            digest, received, chunks = hashlib.sha256(), 0, 0
            async for chunk in request.stream:
                assert len(readers) == 2
                assert readers[1].reads == chunks + 1
                assert not readers[1].closed
                assert 0 < len(chunk) <= env.module.UPLOAD_CHUNK_BYTES
                received += len(chunk)
                chunks += 1
                digest.update(chunk)
                await asyncio.sleep(0)
                assert readers[1].reads == chunks
            job = next(iter(env.api.jobs.values()))
            document = job['documents'][0]
            assert received == document['size'] == size
            assert digest.hexdigest() == document['sha256']
            assert chunks > 1
            with pytest.raises(httpx.RequestNotRead):
                _ = request.content
            env.api.uploads[job['job_id'], file.id] = b'counted by streaming transport'
            return httpx.Response(204)

    await close_client()
    original_client = AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(
            transport=Transport(),
            **kwargs,
        ),
    )
    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(Path, 'read_bytes', Mock(side_effect=AssertionError('Whole-file read')))
    job_id = await submit(env, file)
    assert env.api.jobs[job_id]['status'] == 'QUEUED'
    assert len(readers) == 2 and all(reader.closed for reader in readers)
