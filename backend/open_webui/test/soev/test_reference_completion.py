"""Missing reference MIME is filled only with the owner's current login token."""

from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev import acting, jobs, live_documents
from open_webui.test.soev.test_jobs import env  # noqa: F401
from open_webui.test.soev.test_keycloak_token import token_app  # noqa: F401


async def reference(owner='fern'):
    return await live_documents.register_attachment(
        owner,
        {
            'source_id': f'fictional-reference-{owner}',
            'job_id': f'fictional-job-{owner}',
            'collection_key': f'owui-attachments-{owner}',
            'name': 'Fictional report.pdf',
            'web_url': 'https://example.invalid/report',
            'provider': 'onedrive',
            'provider_ref': {'grant_id': 'fictional-grant', 'drive_id': 'fictional-drive', 'item_id': 'fictional-item'},
            'attached_by': 'user',
            'status': 'processing',
            'content_type': None,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('lookup', ['success', 'denied', 'no-mime', 'write-fails'])
async def test_next_owner_request_completes_reference_with_login_proof(token_app, env, monkeypatch, lookup):  # noqa: F811
    ctx = token_app
    file = await reference()
    other = await reference('moss')
    sent = []

    def handle(request):
        sent.append(request)
        if '/jobs/' in request.url.path:
            return httpx.Response(200, json={'status': 'SUCCEEDED'})
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={})
        if lookup == 'denied':
            return httpx.Response(404, json={'code': 'not_found'})
        return httpx.Response(200, json={} if lookup == 'no-mime' else {'content_type': 'application/pdf'})

    metadata_write = env.files.Files.update_file_metadata_by_id
    if lookup == 'write-fails':
        monkeypatch.setattr(env.files.Files, 'update_file_metadata_by_id', AsyncMock(return_value=None))
    async with type(ctx.browser)(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: transport)
        # The autonomous poller cannot fill MIME or mark this reference completed.
        assert await jobs.poll_once(env.client.as_service(), now=file.meta['soev_job']['submitted_at']) == 0
        assert all('/jobs/' in request.url.path for request in sent)
        sent.clear()
        response = await ctx.browser.get('/call/product')
    assert response.status_code == 200
    metadata = [r for r in sent if '/documents/' in r.url.path]
    assert len(metadata) == 1
    assert metadata[0].url.path.endswith('/' + file.id)
    assert metadata[0].headers['X-Soev-User-Token'] == 'fictional-access-token'
    assert all('X-Soev-User-Token' not in r.headers for r in sent if '/jobs/' in r.url.path)
    row = await env.files.Files.get_file_by_id(file.id)
    if lookup == 'success':
        assert row.meta['content_type'] == 'application/pdf'
        assert row.meta['status'] == 'completed'
        assert row.meta['soev_job'] is None
        env.events.assert_awaited_once()
    else:
        assert row.meta['status'] == 'processing'
        assert row.meta['soev_job'] == file.meta['soev_job']
        env.events.assert_not_awaited()
    if lookup != 'success':
        lookup = 'success'
        monkeypatch.setattr(env.files.Files, 'update_file_metadata_by_id', metadata_write)
        async with type(ctx.browser)(transport=httpx.MockTransport(handle)) as transport:
            monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: transport)
            assert (await ctx.browser.get('/call/product')).status_code == 200
        retried = await env.files.Files.get_file_by_id(file.id)
        assert retried.meta['content_type'] == 'application/pdf'
        assert retried.meta['soev_job'] is None
        env.events.assert_awaited_once()
    assert (await env.files.Files.get_file_by_id(other.id)).meta['soev_job'] == other.meta['soev_job']
    assert acting.requester_ref() is None
    assert acting._token_resolver.get() is None


@pytest.mark.asyncio
async def test_request_without_platform_login_keeps_missing_mime_pending(token_app, env, monkeypatch):  # noqa: F811
    ctx = token_app
    file = await reference()
    ctx.browser.cookies.clear()
    sent = []

    def handle(request):
        sent.append(request)
        return httpx.Response(200, json={'status': 'SUCCEEDED'})

    async with type(ctx.browser)(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: transport)
        assert (await ctx.browser.get('/call/product')).status_code == 200
    assert all('/documents/' not in request.url.path for request in sent)
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_job'] == file.meta['soev_job']
    env.events.assert_not_awaited()
