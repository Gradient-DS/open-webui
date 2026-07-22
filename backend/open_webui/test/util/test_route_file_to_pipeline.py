"""Orchestration test for retrieval.route_file_to_pipeline.

The pure units (build_job_submission / submit_job / should_route_to_pipeline)
are covered in test_doc_pipeline.py; this asserts the OWUI-side wiring: presign
the file's path with the configured TTL, submit a job for the right KB+file,
link the file, and mark it 'processing'. I/O collaborators are mocked (the
boundary is unavoidable to mock here)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import open_webui.routers.retrieval as retrieval


@pytest.mark.asyncio
async def test_route_file_to_pipeline_submits_links_and_marks_processing(monkeypatch):
    # Post-v0.10.2 the handler reads config via get_rag_config_state() (per-key
    # Config store), not request.app.state.config — patch the namespace source.
    config = SimpleNamespace(
        PIPELINE_PRESIGN_TTL_SECONDS=900,
        PIPELINE_API_BASE_URL='http://pipe:8080',
        PIPELINE_API_KEY='secret',
        PIPELINE_INGEST_CALLBACK_URL='http://owui:8080',
        PIPELINE_CHUNK_SIZE=1000,
        PIPELINE_CHUNK_OVERLAP=100,
    )

    async def fake_get_rag_config_state():
        return config

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    file = SimpleNamespace(
        id='file-1',
        filename='report.pdf',
        path='s3://b/k/report.pdf',
        meta={'content_type': 'application/pdf'},
    )
    user = SimpleNamespace(id='user-7')
    captured = {}

    async def fake_get_knowledge_by_id(kb_id):
        return SimpleNamespace(id=kb_id, name='My KB')

    def fake_presign(path, ttl):
        captured['presign'] = (path, ttl)
        return 'https://s3/presigned?sig=abc'

    async def fake_submit_job(*, base_url, api_key, submission):
        captured['submit'] = {'base_url': base_url, 'api_key': api_key, 'submission': submission}
        return 'job-99'

    async def fake_add_file(kb_id, file_id, user_id):
        captured['link'] = (kb_id, file_id, user_id)

    async def fake_update_meta(file_id, meta, db=None):
        captured.setdefault('meta', {}).update(meta)

    async def fake_set_status(file_id, status, db=None):
        captured['status'] = (file_id, status)

    @asynccontextmanager
    async def fake_db():
        yield object()

    monkeypatch.setattr(retrieval.Knowledges, 'get_knowledge_by_id', fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(retrieval.Storage, 'get_presigned_url', fake_presign, raising=False)
    monkeypatch.setattr(retrieval.doc_pipeline, 'submit_job', fake_submit_job)
    monkeypatch.setattr(retrieval.Knowledges, 'add_file_to_knowledge_by_id', fake_add_file, raising=False)
    monkeypatch.setattr(retrieval.Files, 'update_file_metadata_by_id', fake_update_meta, raising=False)
    monkeypatch.setattr(retrieval.Files, 'set_status', fake_set_status, raising=False)
    monkeypatch.setattr(retrieval, 'get_async_db', fake_db)
    monkeypatch.setattr(retrieval, 'get_rag_config_state', fake_get_rag_config_state)

    result = await retrieval.route_file_to_pipeline(request, file, 'kb-9', user)

    # Returns the created job id + KB.
    assert result['pipeline_job_id'] == 'job-99'
    assert result['collection_name'] == 'kb-9'
    # Presigns the file's own stored path with the configured TTL.
    assert captured['presign'] == ('s3://b/k/report.pdf', 900)
    # Submits to the configured pipeline-api with the bearer.
    assert captured['submit']['base_url'] == 'http://pipe:8080'
    assert captured['submit']['api_key'] == 'secret'
    sub = captured['submit']['submission']
    owui = sub['parameters']['owui']
    # target defaults to 'knowledge' (KB path); worker echoes the key through.
    assert owui['collection'] == {'source_id': 'kb-9', 'name': 'My KB', 'target': 'knowledge'}
    assert owui['document']['source_id'] == 'file-1'
    assert owui['ingest_url'] == 'http://owui:8080'
    assert sub['metadata']['items'][0]['format'] == 'pdf'
    assert sub['parameters']['chunk_size'] == 1000
    # Links the file to the KB and marks it processing (job id recorded).
    assert captured['link'] == ('kb-9', 'file-1', 'user-7')
    assert captured['status'] == ('file-1', 'processing')
    assert captured['meta']['pipeline_job_id'] == 'job-99'
    assert captured['meta']['collection_name'] == 'kb-9'
    # A submitted-at timestamp is persisted so the restart-safe reconciler can
    # apply the wall-clock backstop without any in-memory state.
    assert isinstance(captured['meta']['pipeline_submitted_at'], int)
    assert captured['meta']['pipeline_submitted_at'] > 0


@pytest.mark.asyncio
async def test_submit_existing_file_to_pipeline_submits_links_and_marks_processing(monkeypatch):
    """submit_existing_file_to_pipeline is the reusable body extracted out of
    route_file_to_pipeline so B-O4's /integrations/submit endpoint can call the
    same submit logic on an already-stored File outside the upload route."""
    config = SimpleNamespace(
        PIPELINE_PRESIGN_TTL_SECONDS=900,
        PIPELINE_API_BASE_URL='http://pipe:8080',
        PIPELINE_API_KEY='secret',
        PIPELINE_INGEST_CALLBACK_URL='http://owui:8080',
        PIPELINE_CHUNK_SIZE=1000,
        PIPELINE_CHUNK_OVERLAP=100,
    )

    async def fake_get_rag_config_state():
        return config

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    file = SimpleNamespace(
        id='file-1',
        filename='report.pdf',
        path='s3://b/k/report.pdf',
        meta={'content_type': 'application/pdf'},
    )
    user = SimpleNamespace(id='user-7')
    captured = {}

    async def fake_get_knowledge_by_id(kb_id):
        return SimpleNamespace(id=kb_id, name='My KB')

    def fake_presign(path, ttl):
        captured['presign'] = (path, ttl)
        return 'https://s3/presigned?sig=abc'

    async def fake_submit_job(*, base_url, api_key, submission):
        captured['submit'] = {'base_url': base_url, 'api_key': api_key, 'submission': submission}
        return 'job-99'

    async def fake_add_file(kb_id, file_id, user_id):
        captured['link'] = (kb_id, file_id, user_id)

    async def fake_update_meta(file_id, meta, db=None):
        captured.setdefault('meta', {}).update(meta)

    async def fake_set_status(file_id, status, db=None):
        captured['status'] = (file_id, status)

    @asynccontextmanager
    async def fake_db():
        yield object()

    monkeypatch.setattr(retrieval.Knowledges, 'get_knowledge_by_id', fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(retrieval.Storage, 'get_presigned_url', fake_presign, raising=False)
    monkeypatch.setattr(retrieval.doc_pipeline, 'submit_job', fake_submit_job)
    monkeypatch.setattr(retrieval.Knowledges, 'add_file_to_knowledge_by_id', fake_add_file, raising=False)
    monkeypatch.setattr(retrieval.Files, 'update_file_metadata_by_id', fake_update_meta, raising=False)
    monkeypatch.setattr(retrieval.Files, 'set_status', fake_set_status, raising=False)
    monkeypatch.setattr(retrieval, 'get_async_db', fake_db)
    monkeypatch.setattr(retrieval, 'get_rag_config_state', fake_get_rag_config_state)

    result = await retrieval.submit_existing_file_to_pipeline(request, file, 'kb-9', user)

    assert result['pipeline_job_id'] == 'job-99'
    assert result['collection_name'] == 'kb-9'
    # Submitted a job (submit_job called) with the presigned original.
    assert captured['submit']['base_url'] == 'http://pipe:8080'
    assert captured['presign'] == ('s3://b/k/report.pdf', 900)
    # Links the file to the KB and marks it processing (job id recorded).
    assert captured['link'] == ('kb-9', 'file-1', 'user-7')
    assert captured['status'] == ('file-1', 'processing')
