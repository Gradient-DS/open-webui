"""Unit tests for the loader-worker HTTP client and BaseSyncWorker branches.

Exercises:

* ``PipelineClient.submit_job`` carries the correct ``acting_user_id`` and
  ``provider_slug`` in the request body, addresses the right
  ``/tenants/{tenant}/jobs`` path, and returns the loader-worker's ``job_id``.
* ``PipelineClient.get_status`` / ``cancel_job`` hit the right URLs.
* ``BaseSyncWorker.__init__`` constructs (or skips) the pipeline client based
  on ``use_shared_loader``, and the legacy/shared branches in
  ``_download_and_store`` / ``_process_and_embed`` route correctly.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from open_webui.services.sync.pipeline_client import PipelineClient


# ---------- PipelineClient: submit_job -----------------------------------------------


class _Recorder:
    """Captures requests routed through ``httpx.MockTransport``."""

    def __init__(self, response_body: Dict[str, Any], status_code: int = 202):
        self.response_body = response_body
        self.status_code = status_code
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.response_body)


@pytest.fixture
def submit_recorder() -> _Recorder:
    return _Recorder({'job_id': 'job-abc-123', 'status': 'queued', 'items': 4})


@pytest.fixture
def status_recorder() -> _Recorder:
    return _Recorder(
        {
            'job_id': 'job-abc-123',
            'status': 'completed',
            'items_total': 4,
            'items_completed': 4,
            'items_failed': 0,
            'errors': [],
        },
        status_code=200,
    )


def _client_with_transport(transport: httpx.MockTransport) -> PipelineClient:
    client = PipelineClient(base_url='http://loader-worker.tenant-x.svc:8002', tenant='tenant-x')
    # Patch httpx.AsyncClient construction to use the mock transport.
    client._timeout = httpx.Timeout(5.0)
    return client


@pytest.mark.asyncio
async def test_submit_job_sends_acting_user_and_provider_in_body(submit_recorder, monkeypatch):
    transport = httpx.MockTransport(submit_recorder)
    real_async_client = httpx.AsyncClient

    def make_async_client(*args, **kwargs):
        kwargs['transport'] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr('open_webui.services.sync.pipeline_client.httpx.AsyncClient', make_async_client)

    client = PipelineClient(base_url='http://loader-worker.tenant-x.svc:8002', tenant='tenant-x')

    items = [
        {'file_id': 'onedrive-1', 'filename': 'a.pdf', 'source': 'onedrive'},
        {'file_id': 'onedrive-2', 'filename': 'b.docx', 'source': 'onedrive'},
    ]

    job_id = await client.submit_job(
        knowledge_id='kb-uuid-1',
        acting_user_id='user-uuid-42',
        provider_slug='onedrive',
        callback_base_url='http://open-webui.tenant-x.svc:8080',
        collection={'source_id': 'kb-uuid-1', 'name': 'My KB', 'data_type': 'chunked_text'},
        items=items,
    )

    assert job_id == 'job-abc-123'
    assert len(submit_recorder.requests) == 1
    req = submit_recorder.requests[0]
    assert req.method == 'POST'
    assert str(req.url) == 'http://loader-worker.tenant-x.svc:8002/tenants/tenant-x/jobs'

    body = json.loads(req.content.decode())
    assert body['acting_user_id'] == 'user-uuid-42'
    assert body['provider_slug'] == 'onedrive'
    assert body['knowledge_id'] == 'kb-uuid-1'
    assert body['items'] == items
    assert body['collection']['data_type'] == 'chunked_text'
    # Embedding moved back to open-webui (plan amendment 2026-04-26):
    # the loader-worker no longer needs an embedding_config.
    assert 'embedding_config' not in body


@pytest.mark.asyncio
async def test_submit_job_raises_when_url_or_tenant_unset():
    client = PipelineClient(base_url='', tenant='tenant-x')
    with pytest.raises(RuntimeError, match='LOADER_WORKER_URL'):
        await client.submit_job(
            knowledge_id='k',
            acting_user_id='u',
            provider_slug='onedrive',
            callback_base_url='c',
            collection={},
            items=[],
        )

    client = PipelineClient(base_url='http://x', tenant='')
    with pytest.raises(RuntimeError, match='LOADER_WORKER_URL'):
        await client.submit_job(
            knowledge_id='k',
            acting_user_id='u',
            provider_slug='onedrive',
            callback_base_url='c',
            collection={},
            items=[],
        )


@pytest.mark.asyncio
async def test_submit_job_propagates_provider_slug_through(submit_recorder, monkeypatch):
    """Both 'onedrive' and 'google_drive' must round-trip unchanged."""
    transport = httpx.MockTransport(submit_recorder)
    real_async_client = httpx.AsyncClient

    def make_async_client(*args, **kwargs):
        kwargs['transport'] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr('open_webui.services.sync.pipeline_client.httpx.AsyncClient', make_async_client)

    client = PipelineClient(base_url='http://lw', tenant='t')
    for slug in ('onedrive', 'google_drive'):
        await client.submit_job(
            knowledge_id='k',
            acting_user_id='u',
            provider_slug=slug,
            callback_base_url='http://ow',
            collection={},
            items=[],
        )

    bodies = [json.loads(r.content.decode()) for r in submit_recorder.requests]
    assert [b['provider_slug'] for b in bodies] == ['onedrive', 'google_drive']


@pytest.mark.asyncio
async def test_get_status_uses_correct_url(status_recorder, monkeypatch):
    transport = httpx.MockTransport(status_recorder)
    real_async_client = httpx.AsyncClient

    def make_async_client(*args, **kwargs):
        kwargs['transport'] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr('open_webui.services.sync.pipeline_client.httpx.AsyncClient', make_async_client)

    client = PipelineClient(base_url='http://loader-worker.tenant-x.svc:8002', tenant='tenant-x')
    status = await client.get_status('job-abc-123')
    assert status['status'] == 'completed'
    assert str(status_recorder.requests[0].url) == ('http://loader-worker.tenant-x.svc:8002/jobs/job-abc-123')
    assert status_recorder.requests[0].method == 'GET'


@pytest.mark.asyncio
async def test_cancel_job_uses_correct_url(monkeypatch):
    cancel_recorder = _Recorder({'job_id': 'job-1', 'status': 'cancelling'}, status_code=202)
    transport = httpx.MockTransport(cancel_recorder)
    real_async_client = httpx.AsyncClient

    def make_async_client(*args, **kwargs):
        kwargs['transport'] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr('open_webui.services.sync.pipeline_client.httpx.AsyncClient', make_async_client)

    client = PipelineClient(base_url='http://lw', tenant='tenant-x')
    body = await client.cancel_job('job-1')
    assert body['status'] == 'cancelling'
    assert str(cancel_recorder.requests[0].url) == 'http://lw/jobs/job-1/cancel'
    assert cancel_recorder.requests[0].method == 'POST'


# ---------- BaseSyncWorker branches --------------------------------------------------


def _make_worker(use_shared_loader: bool):
    """Construct an OneDriveSyncWorker with minimal state for branch testing."""
    from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker

    return OneDriveSyncWorker(
        knowledge_id='kb-1',
        sources=[],
        access_token='token-abc',
        user_id='user-uuid-1',
        app=MagicMock(),
        use_shared_loader=use_shared_loader,
    )


def test_init_constructs_pipeline_client_only_when_shared():
    legacy = _make_worker(use_shared_loader=False)
    shared = _make_worker(use_shared_loader=True)

    assert legacy._use_shared_loader is False
    assert legacy._pipeline_client is None

    assert shared._use_shared_loader is True
    assert shared._pipeline_client is not None
    # The shared worker exposes the provider slug on the loader-worker contract.
    assert shared.provider_slug == 'onedrive'


@pytest.mark.asyncio
async def test_download_and_store_branches_to_legacy_when_not_shared(monkeypatch):
    worker = _make_worker(use_shared_loader=False)
    legacy_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, '_download_and_store_legacy', legacy_mock)

    await worker._download_and_store({'item': {'id': 'x'}, 'name': 'x.pdf'})
    legacy_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_and_store_skips_legacy_in_shared_mode(monkeypatch):
    worker = _make_worker(use_shared_loader=True)
    legacy_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, '_download_and_store_legacy', legacy_mock)

    result = await worker._download_and_store({'item': {'id': 'x'}, 'name': 'x.pdf'})
    legacy_mock.assert_not_awaited()
    assert result is None


@pytest.mark.asyncio
async def test_process_and_embed_branches_to_legacy_when_not_shared(monkeypatch):
    from open_webui.services.sync.base_worker import PreparedFile

    worker = _make_worker(use_shared_loader=False)
    legacy_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, '_process_and_embed_legacy', legacy_mock)

    prepared = PreparedFile(file_id='onedrive-1', file_info={}, name='x.pdf', content_hash='h', is_new=True)
    await worker._process_and_embed(prepared)
    legacy_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_and_embed_skips_legacy_in_shared_mode(monkeypatch):
    from open_webui.services.sync.base_worker import PreparedFile

    worker = _make_worker(use_shared_loader=True)
    legacy_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, '_process_and_embed_legacy', legacy_mock)

    prepared = PreparedFile(file_id='onedrive-1', file_info={}, name='x.pdf', content_hash='h', is_new=True)
    result = await worker._process_and_embed(prepared)
    legacy_mock.assert_not_awaited()
    assert result is None


def test_item_from_file_info_default_shape():
    """Default item builder produces the loader-worker contract shape."""
    worker = _make_worker(use_shared_loader=True)
    file_info = {
        'item': {'id': 'item-1', 'size': 12345},
        'drive_id': 'drive-a',
        'source_type': 'folder',
        'source_item_id': 'folder-1',
        'name': 'doc.pdf',
        'relative_path': 'sub/doc.pdf',
    }

    item = worker._item_from_file_info(file_info, access_token='oauth-token')

    assert item['source'] == 'onedrive'
    assert item['credential_type'] == 'user_oauth'
    assert item['source_credential'] == 'oauth-token'
    assert item['file_id'] == 'onedrive-item-1'
    # source_id is the raw provider item id; /ingest re-prefixes it as
    # f'{provider}-{source_id}' to match the stub File row.
    assert item['source_id'] == 'item-1'
    assert item['filename'] == 'doc.pdf'
    # Provider metadata flows through from _get_provider_file_meta.
    assert item['metadata']['source'] == 'onedrive'
    assert item['metadata']['onedrive_item_id'] == 'item-1'
