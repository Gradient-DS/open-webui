"""Guards PipelineClient's connect/transport-failure reclassification.

When the per-tenant loader-worker is unreachable (connection refused, DNS
failure, timeout), every HTTP method must re-raise as
``PipelineUnreachableError`` (a ``ConnectionError`` subclass) so the sync
worker can attribute the failure to the ingestion pipeline rather than the
sync source.

Crucially, an ``httpx.HTTPStatusError`` (server reached, returns 4xx/5xx)
must NOT be reclassified — the loader-worker is up, just erroring.
"""

from __future__ import annotations

import httpx
import pytest

from open_webui.services.sync.pipeline_client import (
    PipelineClient,
    PipelineUnreachableError,
)


@pytest.fixture
def patch_async_client(monkeypatch):
    """Make every ``httpx.AsyncClient(...)`` honour a per-client transport.

    PipelineClient opens a fresh ``AsyncClient`` per call with only a
    ``timeout`` kwarg, so we wrap the ctor to also pass ``transport`` taken
    from a module-level holder set by each test.
    """
    holder: dict[str, httpx.MockTransport] = {}
    real_ctor = httpx.AsyncClient

    def fake_ctor(*args, **kwargs):
        if 'transport' not in kwargs and holder.get('transport') is not None:
            kwargs['transport'] = holder['transport']
        return real_ctor(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', fake_ctor)
    return holder


def test_pipeline_unreachable_is_connection_error_subclass():
    assert issubclass(PipelineUnreachableError, ConnectionError)


@pytest.mark.asyncio
async def test_submit_job_connect_error_reclassified(patch_async_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('connection refused', request=request)

    patch_async_client['transport'] = httpx.MockTransport(handler)
    client = PipelineClient(base_url='http://loader-worker:8202', tenant='acme')

    with pytest.raises(PipelineUnreachableError) as exc_info:
        await client.submit_job(
            knowledge_id='kb-1',
            acting_user_id='u-1',
            provider_slug='confluence',
            callback_base_url='http://owui',
            collection={},
            items=[{'item': {'id': 'i1'}}],
        )

    # original cause preserved via `from e`
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


@pytest.mark.asyncio
async def test_get_status_transport_error_reclassified(patch_async_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout('timed out', request=request)

    patch_async_client['transport'] = httpx.MockTransport(handler)
    client = PipelineClient(base_url='http://loader-worker:8202', tenant='acme')

    with pytest.raises(PipelineUnreachableError):
        await client.get_status('job-1')


@pytest.mark.asyncio
async def test_cancel_job_connect_error_reclassified(patch_async_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused', request=request)

    patch_async_client['transport'] = httpx.MockTransport(handler)
    client = PipelineClient(base_url='http://loader-worker:8202', tenant='acme')

    with pytest.raises(PipelineUnreachableError):
        await client.cancel_job('job-1')


@pytest.mark.asyncio
async def test_http_status_error_not_reclassified(patch_async_client):
    """A reachable-but-erroring loader-worker (500) must raise HTTPStatusError.

    The server was reached, so this is NOT an unreachability condition and
    must not be tagged as PipelineUnreachableError.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={'detail': 'boom'})

    patch_async_client['transport'] = httpx.MockTransport(handler)
    client = PipelineClient(base_url='http://loader-worker:8202', tenant='acme')

    with pytest.raises(httpx.HTTPStatusError):
        await client.submit_job(
            knowledge_id='kb-1',
            acting_user_id='u-1',
            provider_slug='confluence',
            callback_base_url='http://owui',
            collection={},
            items=[{'item': {'id': 'i1'}}],
        )

    # And specifically NOT a PipelineUnreachableError.
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.get_status('job-1')
    assert not isinstance(exc_info.value, PipelineUnreachableError)
