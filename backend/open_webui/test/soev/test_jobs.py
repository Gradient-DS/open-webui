"""Job polling against the shared API fake and durable SQLite File rows."""

import asyncio
import importlib
import json
from contextlib import asynccontextmanager, contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from open_webui.test.soev import test_ingest
from open_webui.test.soev.fake_api import person_ref
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def env(identity_config, fake_api, monkeypatch, tmp_path):
    """Use real Files methods and the HTTP client while recording status events."""
    identity, _ = identity_config
    ingest = importlib.import_module('open_webui.soev.ingest')
    jobs = importlib.import_module('open_webui.soev.jobs')
    files = importlib.import_module('open_webui.models.files')
    storage = importlib.import_module('open_webui.storage.provider')
    monkeypatch.setattr(ingest, 'Storage', storage.LocalStorageProvider())
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
    events = AsyncMock()
    monkeypatch.setattr(jobs, 'emit_file_status', events)
    client = identity.build_client()
    await client.send(
        'POST',
        '/v1/collections',
        {
            'key': 'kb',
            'name': 'Research',
            'visibility': 'restricted',
            'principals': [test_ingest.SERVICE, person_ref('alice')],
            'writers': [person_ref('alice')],
        },
        as_user='owui:user:alice',
        idempotency_key='collection:kb',
    )
    yield SimpleNamespace(
        module=ingest,
        jobs=jobs,
        files=files,
        api=fake_api,
        client=client,
        identity=identity,
        tmp_path=tmp_path,
        events=events,
    )
    await engine.dispose()


@contextmanager
def owner_login(owner='alice'):
    from open_webui.soev import acting

    requester = acting._requester_ref.set(f'owui:user:{owner}')
    resolver = acting._token_resolver.set(AsyncMock(return_value=f'fictional-login:owui:user:{owner}'))
    try:
        yield
    finally:
        acting._token_resolver.reset(resolver)
        acting._requester_ref.reset(requester)


async def poll(env, *, now):
    with owner_login():
        return await env.jobs.poll_once(env.client, now=now)


async def submitted(env, **meta):
    file = await test_ingest.new_file(env, **meta)
    await test_ingest.submit(env, file)
    row = await env.files.Files.get_file_by_id(file.id)
    env.api.requests.clear()
    return row, row.meta['soev_job']


@pytest.mark.asyncio
async def test_upload_recovery_uses_the_owner_login(env):
    file, job = await submitted(env)
    env.api.advance(job['job_id'], 'AWAITING_UPLOAD')
    env.api.uploads.pop((job['job_id'], file.id))
    assert await poll(env, now=job['submitted_at'] + 61) == 1
    assert any(request.url.path.endswith('/commit') for request in env.api.requests)
    assert any(request.url.path.endswith('/cancel') for request in env.api.requests)
    assert any(request.url.path == '/v1/jobs' for request in env.api.requests)
    assert all(r.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice' for r in env.api.requests)


async def assert_terminal(env, file, *, error=None):
    row = await env.files.Files.get_file_by_id(file.id)
    status = 'failed' if error is not None else 'completed'
    assert row.meta['status'] == row.data['status'] == status
    assert row.meta['error'] == row.data['error'] == error
    assert row.meta['soev_job'] is None
    assert row.meta['soev_collection_key'] == ('kb' if error is not None else None)
    assert row.data['content'] == 'preserved content'
    assert await env.files.Files.get_files_with_soev_jobs() == []
    assert [r.id for r in await env.files.Files.get_unlanded_files_for_collection('kb')] == (
        [file.id] if error is not None else []
    )
    env.events.assert_awaited_once_with(
        user_id=file.user_id, file_id=file.id, status=status, error=error, collection_name='kb'
    )


@pytest.mark.asyncio
async def test_a_succeeded_job_completes_the_file_and_emits_completed(env):
    """Successful jobs leave document membership and emit the persisted completed status."""
    file, job = await submitted(env)
    env.api.advance(job['job_id'], 'SUCCEEDED')
    assert await poll(env, now=job['submitted_at'] + 1) == 1
    await assert_terminal(env, file)
    assert [(r.method, r.url.path) for r in env.api.requests] == [
        ('GET', f'/v1/jobs/{job["job_id"]}'),
        ('GET', f'/v1/collections/kb/documents/{file.id}'),
    ]
    assert env.api.requests[0].url.params['include_items'] == 'true'
    assert await poll(env, now=job['submitted_at'] + 2) == 0
    assert len(env.api.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('intended,landed', [('a/b', None), ('a/b', 'a/b'), (None, ''), (None, 'a/b')])
async def test_a_landed_document_is_moved_to_the_intended_folder(env, intended, landed):
    """A move restores the intended path only when the normalized landed path differs."""
    file, job = await submitted(env, relative_path=f'{intended}/report.pdf' if intended else 'report.pdf')
    env.api.advance(job['job_id'], 'SUCCEEDED')
    env.api.documents['kb', file.id]['path'] = landed
    assert await poll(env, now=job['submitted_at']) == 1
    moves = [r for r in env.api.requests if r.url.path.endswith('/move')]
    if (intended or '') != (landed or ''):
        assert len(moves) == 1
        assert json.loads(moves[0].content) == {'to': intended or ''}
        assert moves[0].headers['Idempotency-Key'] == f'ingest:{file.id}:kb:{job["sha256"]}:1:move'
    else:
        assert not moves
    assert (env.api.documents['kb', file.id]['path'] or '') == (intended or '')
    await assert_terminal(env, file)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,code,items',
    [
        ('COMPLETED_WITH_ERRORS', 'unsupported_content_type', True),
        ('COMPLETED_WITH_ERRORS', None, True),
        ('COMPLETED_WITH_ERRORS', 'unsupported_content_type', False),
        ('FAILED', None, True),
        ('CANCELLED', None, True),
        ('EXPIRED', None, True),
    ],
)
async def test_an_item_failure_becomes_the_files_error_with_its_code(env, status, code, items):
    """Only a matching item's code supplies the failure reason, otherwise the terminal status does."""
    file, job = await submitted(env)
    env.api.advance(job['job_id'], status, item_code=code, item_detail='Cannot parse this media type')
    if not items:
        env.api.jobs[job['job_id']]['items'] = []
    env.api.jobs[job['job_id']]['items'].insert(0, {'source_id': 'another-file', 'code': 'wrong', 'detail': 'wrong'})
    assert await poll(env, now=job['submitted_at']) == 1
    await assert_terminal(env, file, error=code if code and items else status)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'item,error',
    [
        ({'code': 'internal_error', 'reason': 'unreadable_file'}, 'internal_error: unreadable_file'),
        ({'code': 'empty_content', 'reason': 'empty_content'}, 'empty_content'),
        ({'code': 'internal_error', 'reason': 'parse of doc_id=x failed: ParseError'}, 'internal_error'),
        ({'code': 'Not A Code', 'reason': 'unreadable_file'}, 'COMPLETED_WITH_ERRORS'),
    ],
)
async def test_a_files_error_carries_only_fixed_codes(env, item, error):
    """Item text the platform should never send is still never shown as a file's error."""
    file, job = await submitted(env)
    env.api.advance(job['job_id'], 'COMPLETED_WITH_ERRORS', item_code='internal_error')
    env.api.jobs[job['job_id']]['items'][0].update(item, detail=item['reason'])
    assert await poll(env, now=job['submitted_at']) == 1
    await assert_terminal(env, file, error=error)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['RUNNING', 'QUEUED'])
async def test_a_running_job_keeps_processing_until_the_wall_clock(env, monkeypatch, status):
    """The wall clock expires only past the configured cap and never cancels an active job."""
    monkeypatch.setattr(env.jobs.config, 'SOEV_API_JOB_MAX_WALL_CLOCK_SECONDS', 120)
    file, job = await submitted(env)
    env.api.advance(job['job_id'], status)
    for age in (1, 116):
        assert await poll(env, now=job['submitted_at'] + age) == 0
        assert (await env.files.Files.get_file_by_id(file.id)).data == file.data
        env.events.assert_not_awaited()
    assert await poll(env, now=job['submitted_at'] + 121) == 1
    await assert_terminal(env, file, error='did not complete within 120s')
    assert env.api.jobs[job['job_id']]['status'] == status
    assert all(r.method == 'GET' for r in env.api.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('age', [60, 61])
async def test_a_stuck_upload_is_committed_after_a_minute(env, monkeypatch, committed, age):
    """An interrupted commit waits one minute before replaying the original key as the owner."""
    file = await test_ingest.new_file(env)
    send = env.client.send

    async def interrupted(method, path, *args, **kwargs):
        if path.endswith('/commit'):
            raise RuntimeError('commit interrupted')
        return await send(method, path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(env.client, 'send', interrupted)
        with pytest.raises(RuntimeError, match='commit interrupted'):
            await test_ingest.submit(env, file)
    file = await env.files.Files.get_file_by_id(file.id)
    job = {**file.meta['soev_job'], 'committed': committed}
    file = await env.files.Files.update_file_metadata_by_id(file.id, {'soev_job': job})
    env.api.requests.clear()
    assert await poll(env, now=job['submitted_at'] + 55) == 0
    assert (await env.files.Files.get_file_by_id(file.id)).data == file.data
    assert await poll(env, now=job['submitted_at'] + age) == 1
    assert env.api.jobs[job['job_id']]['status'] == 'QUEUED'
    row = await env.files.Files.get_file_by_id(file.id)
    assert row.meta['soev_job']['committed'] is True
    assert row.data['status'] == 'processing'
    commit = env.api.requests[-1]
    assert commit.headers['Idempotency-Key'] == f'ingest:{file.id}:kb:{job["sha256"]}:1:commit'
    assert 'X-Soev-' + 'Subject' not in commit.headers
    env.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_missing_upload_is_resubmitted_at_most_three_times(env):
    """Missing uploads retry with increasing durable attempts and fail before creating a fourth job."""
    file, job = await submitted(env)
    for attempt in (1, 2, 3):
        assert job['attempt'] == attempt
        env.api.advance(job['job_id'], 'AWAITING_UPLOAD')
        env.api.uploads.pop((job['job_id'], file.id))
        env.api.requests.clear()
        assert await poll(env, now=job['submitted_at'] + 61 + attempt * 5) == 1
        row = await env.files.Files.get_file_by_id(file.id)
        if attempt < 3:
            assert env.api.jobs[job['job_id']]['status'] == 'CANCELLED'
            job = row.meta['soev_job']
            assert job['attempt'] == attempt + 1
            assert row.meta['soev_collection_key'] == 'kb'
            assert env.api.jobs[job['job_id']]['status'] == 'QUEUED'
            creates = [r for r in env.api.requests if r.method == 'POST' and r.url.path == '/v1/jobs']
            assert len(creates) == 1
            assert creates[0].headers['Idempotency-Key'] == f'ingest:{file.id}:kb:{job["sha256"]}:{attempt + 1}'
            env.events.assert_not_awaited()
    assert len(env.api.jobs) == 3
    await assert_terminal(env, file, error='upload could not be completed')


@pytest.mark.asyncio
async def test_a_vanished_job_is_an_error(env):
    """A credential-scoped missing job becomes a visible failure with retained collection membership."""
    file, job = await submitted(env)
    del env.api.jobs[job['job_id']]
    assert await poll(env, now=job['submitted_at']) == 1
    await assert_terminal(env, file, error='job disappeared')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['SUCCEEDED', 'FAILED'])
async def test_the_row_is_written_before_the_event(env, monkeypatch, status):
    """Listeners see both the terminal status and cleared job metadata when the event arrives."""
    file, job = await submitted(env)
    env.api.advance(job['job_id'], status)
    calls = []
    original = env.files.Files.set_status

    async def set_status(*args, **kwargs):
        result = await original(*args, **kwargs)
        calls.append('write')
        return result

    async def emit(**kwargs):
        row = await env.files.Files.get_file_by_id(file.id)
        assert row.meta['status'] == row.data['status'] == kwargs['status']
        assert row.meta['soev_job'] is None
        calls.append('emit')

    monkeypatch.setattr(env.files.Files, 'set_status', AsyncMock(side_effect=set_status))
    env.events.side_effect = emit
    assert await poll(env, now=job['submitted_at']) == 1
    assert calls == ['write', 'emit']


@pytest.mark.asyncio
@pytest.mark.parametrize('in_request', [False, True])
async def test_service_only_poll_never_reads_or_completes_owner_jobs(env, in_request):
    file, job = await submitted(env)
    env.api.advance(job['job_id'], 'SUCCEEDED')
    with owner_login() if in_request else nullcontext():
        assert await env.jobs.poll_once(env.client.as_service(), now=job['submitted_at']) == 0
    assert env.api.requests == []
    assert (await env.files.Files.get_file_by_id(file.id)).data == file.data
    env.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_reads_and_moves_use_the_login_token(env):
    file, job = await submitted(env, relative_path='a/b/report.pdf')
    env.api.advance(job['job_id'], 'AWAITING_UPLOAD')
    assert await poll(env, now=job['submitted_at'] + 61) == 1
    env.api.advance(job['job_id'], 'SUCCEEDED')
    env.api.documents['kb', file.id]['path'] = None
    assert await poll(env, now=job['submitted_at'] + 66) == 1
    assert len(env.api.requests) == 5
    assert all('X-Soev-' + 'Subject' not in r.headers for r in env.api.requests)
    assert all(r.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice' for r in env.api.requests)
    await assert_terminal(env, file)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['api', 'unexpected'])
async def test_one_rows_failure_does_not_stop_the_tick(env, monkeypatch, caplog, failure):
    """Any row exception is sanitized in logs and does not prevent the next row completing."""
    first, first_job = await submitted(env)
    second, second_job = await submitted(env)
    for job in (first_job, second_job):
        env.api.advance(job['job_id'], 'SUCCEEDED')
    monkeypatch.setattr(env.files.Files, 'get_files_with_soev_jobs', AsyncMock(return_value=[first, second]))
    if failure == 'api':
        env.api.failures.append(503)
    else:
        get = env.client.get

        async def fail_first(path, **kwargs):
            if path.endswith(first_job['job_id']):
                raise ValueError('secret-assertion secret-key https://private.invalid')
            return await get(path, **kwargs)

        monkeypatch.setattr(env.client, 'get', fail_first)
    assert await poll(env, now=second_job['submitted_at']) == 1
    assert (await env.files.Files.get_file_by_id(first.id)).data == first.data
    assert (await env.files.Files.get_file_by_id(second.id)).meta['status'] == 'completed'
    env.events.assert_awaited_once()
    assert first.id in caplog.text
    assert ('injected_failure' if failure == 'api' else 'ValueError') in caplog.text
    for secret in ('secret-assertion', 'secret-key', 'private.invalid', 'soev_test_cred-runtime_test-secret'):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_owner_reconciliation_is_idle_without_soev_api(env, monkeypatch):
    await submitted(env)
    monkeypatch.setattr(env.jobs.config, 'SOEV_API_URL', '')
    build = Mock(side_effect=AssertionError('disabled reconciliation built a client'))
    monkeypatch.setattr(env.jobs.identity, 'build_client', build)
    with owner_login():
        assert await env.jobs.reconcile_owner_jobs('alice') == 0
    build.assert_not_called()
    assert not env.api.requests
    env.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_is_rate_limited_across_clients_and_concurrent_requests(env):
    file, job = await submitted(env)
    now = job['submitted_at']
    env.api.advance(job['job_id'], 'RUNNING')
    with owner_login():
        assert (
            await asyncio.gather(*[env.jobs.poll_once(env.identity.build_client(), now=now) for _ in range(5)])
            == [0] * 5
        )
        assert len(env.api.requests) == 1
        assert await env.jobs.poll_once(env.identity.build_client(), now=now + 4) == 0
        assert len(env.api.requests) == 1
        env.api.advance(job['job_id'], 'SUCCEEDED')
        assert await env.jobs.poll_once(env.identity.build_client(), now=now + 5) == 1
    await assert_terminal(env, file)


@pytest.mark.asyncio
async def test_transient_read_error_is_logged_without_fallback_and_throttled(env, caplog):
    file, job = await submitted(env)
    env.api.failures.append(404)
    assert await poll(env, now=job['submitted_at']) == 0
    assert await poll(env, now=job['submitted_at'] + 1) == 0
    assert len(env.api.requests) == 1
    assert 'injected_failure' in caplog.text
    assert (await env.files.Files.get_file_by_id(file.id)).data['status'] == 'processing'
    env.events.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', [None, 'bob'])
async def test_other_request_cannot_complete_an_owners_job(env, owner):
    file, job = await submitted(env)
    env.api.advance(job['job_id'], 'SUCCEEDED')
    if owner:
        with owner_login(owner):
            assert await env.jobs.poll_once(env.client, now=job['submitted_at']) == 0
    else:
        assert await env.jobs.poll_once(env.client, now=job['submitted_at']) == 0
    assert env.api.requests == []
    assert await env.files.Files.get_file_by_id(file.id) == file
    env.events.assert_not_awaited()
