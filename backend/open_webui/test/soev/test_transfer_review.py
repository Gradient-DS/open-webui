"""Original identity, copy isolation and request/reconciliation races."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from open_webui.test.soev.test_ingest import FILE_BYTES, env, new_file, submit
from open_webui.test.soev.test_jobs import owner_login

DIGEST = hashlib.sha256(FILE_BYTES).hexdigest()


def document(file, key='kb', **fields):
    return {'source_id': file.id, 'collection_key': key, 'byte_size': len(FILE_BYTES), 'sha256': DIGEST, **fields}


@pytest.mark.asyncio
async def test_derived_collections_require_actual_database_links(env):
    from open_webui.soev.file_collections import collections_for

    file = await new_file(env, collection_name='file-legacy', soev_collection_key='untrusted')
    async with env.files.get_async_db_context() as db:
        for name, field in [('model', 'meta'), ('folder', 'data')]:
            await db.execute(text(f'CREATE TABLE {name} (id TEXT, user_id TEXT, {field} TEXT)'))
            for owner, key in [('alice', 'ours'), ('bob', 'theirs')]:
                contents = {'knowledge' if name == 'model' else 'files': [{'id': file.id, 'type': 'file'}]}
                await db.execute(
                    text(f'INSERT INTO {name} VALUES (:key, :owner, :data)'),
                    {'key': key, 'owner': owner, 'data': json.dumps(contents)},
                )
        await db.commit()
    assert await collections_for(file) == {'kb', 'owui-attachments-alice', 'owui-assistant-ours', 'owui-folder-ours'}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['unrelated', 'hash', 'size', 'missing_hash'])
async def test_original_lookup_refuses_unrelated_or_mismatched_versions(env, monkeypatch, case):
    from open_webui.soev import originals

    file = await new_file(env, status='completed')
    if case == 'missing_hash':
        file = await env.files.Files.update_file_metadata_by_id(file.id, {'file_hash': None})
    data = document(file, 'other' if case == 'unrelated' else 'kb')
    if case == 'hash':
        data['sha256'] = 'b' * 64
    if case == 'size':
        data['byte_size'] += 1
    pages = []

    async def rows(*args, **kwargs):
        pages.append(True)
        yield data

    monkeypatch.setattr(env.client, 'pages', rows)
    monkeypatch.setattr(env.client, 'stream_response', lambda *a, **k: pytest.fail('Unverified original fetched'))
    assert await originals.lookup(file, env.client, as_user='owui:user:alice') is None
    with pytest.raises(env.module.SoevApiError):
        async with originals.open_original(file, client=env.client, as_user='owui:user:alice'):
            pass
    assert not (await env.files.Files.get_file_by_id(file.id)).meta.get('soev_read_collection')
    if case == 'missing_hash':
        assert not pages
        assert await originals.proxy_original(file, SimpleNamespace(id='alice'), attachment=False) is None


@pytest.mark.asyncio
async def test_reader_404_uses_only_verified_derived_fallback_without_repinning(env, monkeypatch):
    from open_webui.soev import originals

    file = await new_file(env, status='completed', soev_read_collection='kb')
    attachment = env.module.attachments_collection_key('alice')
    key = await env.module.ensure_attachments_collection('alice', env.client)
    env.api.add_document(key, file.id, byte_size=len(FILE_BYTES), sha256=DIGEST)

    @asynccontextmanager
    async def stream(path, **kwargs):
        assert attachment in path
        yield httpx.Response(200, content=FILE_BYTES)

    monkeypatch.setattr(env.client, 'stream_response', stream)
    async with originals.open_original(file, client=env.client, as_user='owui:user:alice') as response:
        assert await response.aread() == FILE_BYTES
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_read_collection'] == 'kb'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'language,expected',
    [
        ('en-US', 'The extracted text of a platform document cannot be edited.'),
        ('nl-NL', 'De geëxtraheerde tekst van een platformdocument kan niet worden bewerkt.'),
    ],
)
async def test_platform_document_text_edit_is_refused_before_any_mutation(env, monkeypatch, language, expected):
    from open_webui.routers import files

    file = await new_file(env, status='completed', soev_collection_key=None)
    env.api.add_document('kb', file.id, byte_size=len(FILE_BYTES), sha256=DIGEST)
    process = AsyncMock(side_effect=AssertionError('Original must not be replaced'))
    monkeypatch.setattr(files, 'process_file', process)
    with owner_login(), pytest.raises(HTTPException) as caught:
        await files.update_file_data_content_by_id(
            SimpleNamespace(headers={'accept-language': language}),
            file.id,
            files.ContentForm(content='replacement'),
            user=SimpleNamespace(id='alice', role='user'),
            db=None,
        )
    assert (caught.value.status_code, caught.value.detail) == (409, expected)
    process.assert_not_called()
    assert (await env.files.Files.get_file_by_id(file.id)).data['content'] == 'preserved content'
    assert not env.api.jobs


@pytest.mark.asyncio
@pytest.mark.parametrize('media', [False, True])
async def test_local_only_or_media_text_edit_keeps_existing_route_behaviour(env, monkeypatch, media):
    from open_webui.routers import files
    from open_webui.soev import originals

    file = await new_file(env, status='completed')
    if media:
        monkeypatch.setattr(originals, 'sniff_content_type', lambda value: 'video/mp4')
    process = AsyncMock()
    monkeypatch.setattr(files, 'process_file', process)
    monkeypatch.setattr(files.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(files.Knowledges, 'get_knowledges_by_file_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(files, 'publish_event', AsyncMock())
    with owner_login():
        await files.update_file_data_content_by_id(
            SimpleNamespace(headers={}),
            file.id,
            files.ContentForm(content='replacement'),
            user=SimpleNamespace(id='alice', role='user'),
            db=None,
        )
    process.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_requests_cannot_interfere_with_a_transfer_older_than_sixty_seconds(env, monkeypatch):
    from open_webui.soev import jobs

    started, resume = asyncio.Event(), asyncio.Event()
    put = env.client.put_transfer

    async def slow(*args, **kwargs):
        started.set()
        await resume.wait()
        await put(*args, **kwargs)

    monkeypatch.setattr(env.client, 'put_transfer', slow)
    file = await new_file(env)
    task = asyncio.create_task(submit(env, file))
    try:
        await asyncio.wait_for(started.wait(), 5)
        row = await env.files.Files.get_file_by_id(file.id)
        with owner_login():
            assert await jobs.poll_once(env.client, now=row.meta['soev_job']['submitted_at'] + 65) == 0
        assert not any(r.url.path.endswith(('/commit', '/cancel')) for r in env.api.requests)
    finally:
        resume.set()
    job_id = await task
    assert env.api.jobs[job_id]['status'] == 'QUEUED'
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_job']['committed']


@pytest.mark.asyncio
async def test_unknown_admission_followed_by_deterministic_refusal_stops_polling_and_can_retry(env, monkeypatch):
    from open_webui.soev import jobs

    file = await new_file(env)
    creates = []
    send = env.client.send

    async def failed(method, path, *args, **kwargs):
        if path == '/v1/jobs':
            creates.append(kwargs['idempotency_key'])
            raise env.module.SoevApiError(504 if len(creates) == 1 else 422, 'upstream_error', 'unavailable')
        return await send(method, path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(env.client, 'send', failed)
        with pytest.raises(env.module.SoevApiError):
            await submit(env, file)
        row = await env.files.Files.get_file_by_id(file.id)
        assert row.meta['status'] == 'failed' and row.meta['soev_job'] is None
        with owner_login():
            assert await jobs.poll_once(env.client, now=10**10) == 0
        assert len(creates) == 2 and creates[0] == creates[1]
    await submit(env, row)
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_attempt'] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('destination', ['knowledge', 'assistant', 'folder'])
@pytest.mark.parametrize('phase', ['store', 'job'])
async def test_failed_copy_preserves_completed_original_and_records_only_destination_failure(
    env, monkeypatch, destination, phase
):
    from open_webui.soev import jobs

    file = await new_file(env, status='completed', soev_read_collection='kb', soev_collection_key='kb')
    key = {'knowledge': 'kb-copy', 'assistant': 'owui-assistant-helper', 'folder': 'owui-folder-project'}[destination]
    async with env.files.get_async_db_context() as db:
        if destination == 'knowledge':
            await db.execute(text('INSERT INTO knowledge_file VALUES (:id, :key)'), {'id': file.id, 'key': key})
        else:
            table, field, entry = (
                ('model', 'meta', 'knowledge') if destination == 'assistant' else ('folder', 'data', 'files')
            )
            await db.execute(text(f'CREATE TABLE {table} (id TEXT, user_id TEXT, {field} TEXT)'))
            await db.execute(
                text(f'INSERT INTO {table} VALUES (:id, :user, :data)'),
                {
                    'id': 'helper' if destination == 'assistant' else 'project',
                    'user': 'alice',
                    'data': json.dumps({entry: [{'id': file.id, 'type': 'file'}]}),
                },
            )
        await db.commit()
    env.api.collections[key] = {**env.api.collections['kb'], 'key': key}
    env.api.add_document('kb', file.id, byte_size=len(FILE_BYTES), sha256=DIGEST)

    @asynccontextmanager
    async def original(*args, **kwargs):
        yield httpx.Response(200, content=FILE_BYTES)

    monkeypatch.setattr(env.client, 'stream_response', original)
    if phase == 'store':
        handle = env.api.handle

        def fail(request):
            return httpx.Response(400) if request.url.host == 'store.invalid' else handle(request)

        monkeypatch.setattr(env.api, 'handle', fail)
        with pytest.raises(env.module.SoevApiError):
            await env.module.submit(file, collection_key=key, user_id='alice', client=env.client)
    else:
        job_id = await env.module.submit(file, collection_key=key, user_id='alice', client=env.client)
        env.api.advance(job_id, 'FAILED')
        with owner_login():
            assert await jobs.poll_once(env.client, now=10**10) == 1
    row = await env.files.Files.get_file_by_id(file.id)
    assert row.meta['status'] == 'completed' and row.data['status'] == file.data['status']
    assert row.meta['soev_read_collection'] == row.meta['soev_collection_key'] == 'kb'
    assert row.meta['soev_copies'][key]['status'] == 'failed'
    assert row.data['content'] == 'preserved content'


@pytest.mark.asyncio
@pytest.mark.parametrize('copy', [False, True])
async def test_terminal_success_during_abort_is_recorded_as_success(env, monkeypatch, copy):
    file = await new_file(env, status='completed' if copy else 'pending', soev_read_collection='kb' if copy else None)
    if copy:
        env.api.add_document('kb', file.id, byte_size=len(FILE_BYTES), sha256=DIGEST)

        @asynccontextmanager
        async def original(*args, **kwargs):
            yield httpx.Response(200, content=FILE_BYTES)

        monkeypatch.setattr(env.client, 'stream_response', original)
        await env.module.submit(file, collection_key='kb', user_id='alice', client=env.client)
    else:
        await submit(env, file)
    row = await env.files.Files.get_file_by_id(file.id)
    job = {**row.meta['soev_job'], 'copy': copy}
    await env.files.Files.update_file_metadata_by_id(file.id, {'soev_job': job})
    env.api.advance(job['job_id'], 'SUCCEEDED')
    assert await env.module.abort(row, client=env.client) is True
    row = await env.files.Files.get_file_by_id(file.id)
    assert row.meta['status'] == 'completed' and row.meta['soev_job'] is None
    if copy:
        assert row.meta['soev_copies']['kb']['status'] == 'completed'
    else:
        assert row.meta['soev_read_collection'] == 'kb'


@pytest.mark.asyncio
async def test_knowledge_counts_exclude_completed_rows(env, monkeypatch):
    from open_webui.internal import db
    from open_webui.soev.knowledge_store import SoevKnowledgeTable

    monkeypatch.setattr(db, 'get_async_db_context', env.files.get_async_db_context)
    await new_file(env, status='completed', soev_collection_key='kb')
    pending = await new_file(env, status='processing', soev_collection_key='kb')
    store = object.__new__(SoevKnowledgeTable)
    rows = await store._unlanded_files_by_collection(['kb'])
    assert [file.id for file in rows['kb']] == [pending.id]


@pytest.mark.asyncio
async def test_knowledge_copy_link_keeps_primary_and_is_locally_derived(env, monkeypatch):
    from open_webui.soev.knowledge_store import SoevKnowledgeTable
    from open_webui.soev.file_collections import collections_for

    file = await new_file(env, status='completed', soev_collection_key='kb', soev_read_collection='kb')
    store = object.__new__(SoevKnowledgeTable)
    monkeypatch.setattr(store, '_collection', AsyncMock(return_value={'key': 'kb-copy'}))
    monkeypatch.setattr(store, '_documents', AsyncMock(return_value=[]))
    monkeypatch.setattr(store._projection, 'knowledge_link_of', lambda *a, **k: 'link')
    store._configured_principal = 'product'
    assert await store.add_file_to_knowledge_by_id('kb-copy', file.id, 'alice') == 'link'
    current = await env.files.Files.get_file_by_id(file.id)
    assert current.meta['soev_collection_key'] == current.meta['soev_read_collection'] == 'kb'
    assert 'kb-copy' in await collections_for(current)


@pytest.mark.asyncio
async def test_retrieval_copy_error_does_not_clear_completed_file_or_hash(env, monkeypatch):
    from open_webui.routers import retrieval

    file = await new_file(env, status='completed', soev_collection_key='kb', soev_read_collection='kb')
    monkeypatch.setattr(retrieval, 'ensure_configured', lambda: None)
    monkeypatch.setattr(retrieval, '_validate_collection_access', AsyncMock())
    monkeypatch.setattr(retrieval, 'publish_event', AsyncMock())
    monkeypatch.setattr(
        env.module, 'submit', AsyncMock(side_effect=env.module.SoevApiError(503, 'upstream_error', 'Down'))
    )
    with owner_login(), pytest.raises(HTTPException):
        await retrieval.process_file(
            SimpleNamespace(headers={}),
            retrieval.ProcessFileForm(file_id=file.id, collection_name='kb-copy'),
            user=SimpleNamespace(id='alice', role='user'),
            db=None,
        )
    current = await env.files.Files.get_file_by_id(file.id)
    assert current.meta['status'] == current.data['status'] == 'completed'
    assert current.meta['file_hash'] == DIGEST and current.meta['soev_read_collection'] == 'kb'


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_other_requester_defers_cancel_without_using_the_creators_token(env, role):
    from open_webui.soev import jobs
    from open_webui.utils.access_control import can_bypass_access_control

    file = await new_file(env)
    job_id = await submit(env, file)
    env.api.requests.clear()
    with owner_login('bob'):
        assert not can_bypass_access_control(SimpleNamespace(id='bob', role=role))
        assert await env.module.cancel(file, client=env.client) is False
    row = await env.files.Files.get_file_by_id(file.id)
    assert not env.api.requests
    assert row.meta['soev_job']['cancel_pending'] is True
    assert row.meta['soev_job']['as_user'] == 'owui:user:alice'
    assert row.meta['soev_collection_key'] == 'kb'
    # Removing the local File cannot erase the creator's pending work.
    async with env.files.get_async_db_context() as db:
        await db.execute(text('DELETE FROM file WHERE id = :id'), {'id': file.id})
        await db.commit()
    with owner_login('bob'):
        assert await jobs.poll_once(env.client, now=10**10) == 0
    assert not env.api.requests
    with owner_login():
        assert await jobs.poll_once(env.client, now=10**10) == 0
    assert env.api.jobs[job_id]['status'] == 'CANCELLED'
    assert env.api.requests[-1].headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice'
    assert await env.files.Files.get_orphaned_file_jobs('alice') == []


@pytest.mark.asyncio
async def test_cancel_404_is_only_requested_and_cleared_as_the_recorded_creator(env):
    file = await new_file(env)
    await submit(env, file)
    async with env.files.get_async_db_context() as db:
        await db.execute(text('UPDATE file SET user_id = :owner WHERE id = :id'), {'owner': 'bob', 'id': file.id})
        await db.commit()
    env.api.requests.clear()
    env.api.failures.append(404)
    with owner_login('bob'):
        await env.module.cancel(file, client=env.client)
    assert not env.api.requests and env.api.failures == [404]
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_job']
    with owner_login():
        await env.module.cancel(file, client=env.client)
    assert env.api.requests[0].headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice'
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_job'] is None
