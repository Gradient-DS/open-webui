"""Tests for the restart-safe distributed doc-pipeline reconciler pass."""

from types import SimpleNamespace

import pytest

import open_webui.services.doc_pipeline_reconciler as reconciler


@pytest.mark.asyncio
async def test_reconcile_marks_failed_and_timed_out_only(monkeypatch):
    config = SimpleNamespace(
        PIPELINE_API_BASE_URL='http://pipe:8080',
        PIPELINE_API_KEY='secret',
        PIPELINE_JOB_MAX_WALL_CLOCK_SECONDS=21600,
    )
    now = 1_000_000
    files = [
        SimpleNamespace(
            id='f-fail', user_id='u-fail', meta={'pipeline_job_id': 'j1', 'pipeline_submitted_at': now - 100}
        ),
        SimpleNamespace(
            id='f-run', user_id='u-run', meta={'pipeline_job_id': 'j2', 'pipeline_submitted_at': now - 100}
        ),
        SimpleNamespace(
            id='f-old', user_id='u-old', meta={'pipeline_job_id': 'j3', 'pipeline_submitted_at': now - 99999}
        ),
        SimpleNamespace(
            id='f-net', user_id='u-net', meta={'pipeline_job_id': 'j4', 'pipeline_submitted_at': now - 100}
        ),
    ]
    status_by_job = {'j1': 'failed', 'j2': 'running', 'j3': 'running', 'j4': 'boom'}
    set_calls = []
    emit_calls = []

    async def fake_get_files():
        return files

    async def fake_get_job_status(*, base_url, api_key, job_id):
        assert base_url == 'http://pipe:8080' and api_key == 'secret'
        if status_by_job[job_id] == 'boom':
            raise RuntimeError('transient network error')
        return status_by_job[job_id]

    async def fake_set_status(file_id, status, error=None, db=None):
        set_calls.append((file_id, status, error))

    async def fake_emit(*, user_id, file_id, status, error=None, collection_name=None):
        emit_calls.append((user_id, file_id, status, error))

    monkeypatch.setattr(reconciler.Files, 'get_processing_files_with_pipeline_job', fake_get_files, raising=False)
    monkeypatch.setattr(reconciler.doc_pipeline, 'get_job_status', fake_get_job_status)
    monkeypatch.setattr(reconciler.Files, 'set_status', fake_set_status, raising=False)
    monkeypatch.setattr(reconciler, 'emit_file_status', fake_emit)

    errored = await reconciler.reconcile_pipeline_jobs(config, now=now)

    # f-fail (failed) + f-old (running past the cap) → error. f-run → wait.
    # f-net (status fetch raised) → skipped, NOT failed (transient; retried next tick).
    assert {c[0] for c in set_calls} == {'f-fail', 'f-old'}
    assert all(c[1] == 'error' for c in set_calls)
    assert errored == 2
    # Every errored file also gets a 'failed' file:status emit to its OWNER, so
    # the spinner resolves. Waited (f-run) and transiently-skipped (f-net) don't.
    assert {(u, f) for (u, f, s, e) in emit_calls} == {('u-fail', 'f-fail'), ('u-old', 'f-old')}
    assert all(s == 'failed' and e for (_, _, s, e) in emit_calls)


@pytest.mark.asyncio
async def test_reconcile_covers_kb_less_chat_files(monkeypatch):
    """Track 1: a KB-less chat attachment (no collection_name / KB membership) is
    reconciled exactly like a KB warren file — selection is by
    status='processing' + pipeline_job_id, so fail/timeout → error, wait → left."""
    config = SimpleNamespace(
        PIPELINE_API_BASE_URL='http://pipe:8080',
        PIPELINE_API_KEY='secret',
        PIPELINE_JOB_MAX_WALL_CLOCK_SECONDS=21600,
    )
    now = 1_000_000
    # No 'collection_name' key ⇒ these are chat attachments, not KB files.
    files = [
        SimpleNamespace(
            id='chat-fail', user_id='owner-1', meta={'pipeline_job_id': 'j1', 'pipeline_submitted_at': now - 100}
        ),
        SimpleNamespace(
            id='chat-hung', user_id='owner-1', meta={'pipeline_job_id': 'j2', 'pipeline_submitted_at': now - 99999}
        ),
        SimpleNamespace(
            id='chat-run', user_id='owner-1', meta={'pipeline_job_id': 'j3', 'pipeline_submitted_at': now - 100}
        ),
    ]
    status_by_job = {'j1': 'failed', 'j2': 'running', 'j3': 'running'}
    set_calls = []
    emit_calls = []

    async def fake_get_files():
        return files

    async def fake_get_job_status(*, base_url, api_key, job_id):
        return status_by_job[job_id]

    async def fake_set_status(file_id, status, error=None, db=None):
        set_calls.append((file_id, status))

    async def fake_emit(*, user_id, file_id, status, error=None, collection_name=None):
        emit_calls.append((user_id, file_id, status))

    monkeypatch.setattr(reconciler.Files, 'get_processing_files_with_pipeline_job', fake_get_files, raising=False)
    monkeypatch.setattr(reconciler.doc_pipeline, 'get_job_status', fake_get_job_status)
    monkeypatch.setattr(reconciler.Files, 'set_status', fake_set_status, raising=False)
    monkeypatch.setattr(reconciler, 'emit_file_status', fake_emit)

    errored = await reconciler.reconcile_pipeline_jobs(config, now=now)

    # fail → error, timeout (past cap) → error, running within cap → wait (untouched).
    assert set_calls == [('chat-fail', 'error'), ('chat-hung', 'error')]
    assert errored == 2
    # Each errored KB-less chat file also emits file:status='failed' to its owner
    # so the spinner resolves (toast + removal); the waited file emits nothing.
    assert emit_calls == [('owner-1', 'chat-fail', 'failed'), ('owner-1', 'chat-hung', 'failed')]


@pytest.mark.asyncio
async def test_reconcile_marks_completed_but_empty_as_error(monkeypatch):
    """A warren job that reports 'completed' while its file is still 'processing'
    means /ingest was never called — warren parsed zero chunks (scanned/no-text
    doc). The reconciler marks it 'error' with the empty-content reason and emits
    'failed' immediately (well within the wall-clock cap) so the spinner clears
    fast instead of hanging until the 6h backstop."""
    config = SimpleNamespace(
        PIPELINE_API_BASE_URL='http://pipe:8080',
        PIPELINE_API_KEY='secret',
        PIPELINE_JOB_MAX_WALL_CLOCK_SECONDS=21600,
    )
    now = 1_000_000
    files = [
        SimpleNamespace(
            id='f-empty', user_id='owner-1', meta={'pipeline_job_id': 'j1', 'pipeline_submitted_at': now - 30}
        ),
    ]
    set_calls = []
    emit_calls = []

    async def fake_get_files():
        return files

    async def fake_get_job_status(*, base_url, api_key, job_id):
        return 'completed'

    async def fake_set_status(file_id, status, error=None, db=None):
        set_calls.append((file_id, status, error))

    async def fake_emit(*, user_id, file_id, status, error=None, collection_name=None):
        emit_calls.append((user_id, file_id, status, error))

    monkeypatch.setattr(reconciler.Files, 'get_processing_files_with_pipeline_job', fake_get_files, raising=False)
    monkeypatch.setattr(reconciler.doc_pipeline, 'get_job_status', fake_get_job_status)
    monkeypatch.setattr(reconciler.Files, 'set_status', fake_set_status, raising=False)
    monkeypatch.setattr(reconciler, 'emit_file_status', fake_emit)

    errored = await reconciler.reconcile_pipeline_jobs(config, now=now)

    assert errored == 1
    assert len(set_calls) == 1
    file_id, file_status, error = set_calls[0]
    assert (file_id, file_status) == ('f-empty', 'error')
    assert 'no searchable content' in error
    # The honest 'failed' emit lands so the frontend spinner resolves.
    assert emit_calls == [('owner-1', 'f-empty', 'failed', error)]
