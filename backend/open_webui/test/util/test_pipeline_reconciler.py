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
        SimpleNamespace(id='f-fail', meta={'pipeline_job_id': 'j1', 'pipeline_submitted_at': now - 100}),
        SimpleNamespace(id='f-run', meta={'pipeline_job_id': 'j2', 'pipeline_submitted_at': now - 100}),
        SimpleNamespace(id='f-old', meta={'pipeline_job_id': 'j3', 'pipeline_submitted_at': now - 99999}),
        SimpleNamespace(id='f-net', meta={'pipeline_job_id': 'j4', 'pipeline_submitted_at': now - 100}),
    ]
    status_by_job = {'j1': 'failed', 'j2': 'running', 'j3': 'running', 'j4': 'boom'}
    set_calls = []

    async def fake_get_files():
        return files

    async def fake_get_job_status(*, base_url, api_key, job_id):
        assert base_url == 'http://pipe:8080' and api_key == 'secret'
        if status_by_job[job_id] == 'boom':
            raise RuntimeError('transient network error')
        return status_by_job[job_id]

    async def fake_set_status(file_id, status, error=None, db=None):
        set_calls.append((file_id, status, error))

    monkeypatch.setattr(reconciler.Files, 'get_processing_files_with_pipeline_job', fake_get_files, raising=False)
    monkeypatch.setattr(reconciler.doc_pipeline, 'get_job_status', fake_get_job_status)
    monkeypatch.setattr(reconciler.Files, 'set_status', fake_set_status, raising=False)

    errored = await reconciler.reconcile_pipeline_jobs(config, now=now)

    # f-fail (failed) + f-old (running past the cap) → error. f-run → wait.
    # f-net (status fetch raised) → skipped, NOT failed (transient; retried next tick).
    assert {c[0] for c in set_calls} == {'f-fail', 'f-old'}
    assert all(c[1] == 'error' for c in set_calls)
    assert errored == 2


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
        SimpleNamespace(id='chat-fail', meta={'pipeline_job_id': 'j1', 'pipeline_submitted_at': now - 100}),
        SimpleNamespace(id='chat-hung', meta={'pipeline_job_id': 'j2', 'pipeline_submitted_at': now - 99999}),
        SimpleNamespace(id='chat-run', meta={'pipeline_job_id': 'j3', 'pipeline_submitted_at': now - 100}),
    ]
    status_by_job = {'j1': 'failed', 'j2': 'running', 'j3': 'running'}
    set_calls = []

    async def fake_get_files():
        return files

    async def fake_get_job_status(*, base_url, api_key, job_id):
        return status_by_job[job_id]

    async def fake_set_status(file_id, status, error=None, db=None):
        set_calls.append((file_id, status))

    monkeypatch.setattr(reconciler.Files, 'get_processing_files_with_pipeline_job', fake_get_files, raising=False)
    monkeypatch.setattr(reconciler.doc_pipeline, 'get_job_status', fake_get_job_status)
    monkeypatch.setattr(reconciler.Files, 'set_status', fake_set_status, raising=False)

    errored = await reconciler.reconcile_pipeline_jobs(config, now=now)

    # fail → error, timeout (past cap) → error, running within cap → wait (untouched).
    assert set_calls == [('chat-fail', 'error'), ('chat-hung', 'error')]
    assert errored == 2
