"""Picker references cross the user attach boundary without tokens or bytes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from open_webui.test.soev.test_jobs import env  # noqa: F401


@pytest.fixture
def picker(env, monkeypatch):  # noqa: F811
    from open_webui.routers import live_documents as routes

    client = SimpleNamespace(
        send=AsyncMock(
            return_value={
                'source_id': 'source',
                'job_id': 'job',
                'collection_key': 'owui-attachments-alice',
            }
        )
    )
    monkeypatch.setattr(routes.identity, 'build_client', lambda: client)
    monkeypatch.setattr(routes.identity, 'acting_ref', AsyncMock(return_value='owui:user:alice'))
    monkeypatch.setattr(
        routes.ingest, 'ensure_attachments_collection', AsyncMock(return_value='owui-attachments-alice')
    )
    monkeypatch.setattr(routes.Config, 'get', AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/files')
    app.dependency_overrides[routes.get_verified_user] = lambda: SimpleNamespace(id='alice', role='user')
    form = {
        'grant_id': 'grant',
        'drive_id': 'drive',
        'item_id': 'item',
        'etag': 'v1',
        'name': 'Plan.pdf',
        'size': 123,
        'web_url': 'https://tenant.sharepoint.com/plan.pdf',
    }
    return SimpleNamespace(app=app, client=client, routes=routes, form=form)


@pytest.mark.asyncio
async def test_picker_creates_pollable_file_and_replays_same_operation(picker, env):  # noqa: F811
    """The platform alone receives the reference, with user identity and a stable operation id."""
    key = str(uuid4())
    async with AsyncClient(transport=httpx.ASGITransport(picker.app), base_url='http://test') as browser:
        for _ in range(2):
            response = await browser.post(
                '/api/v1/files/onedrive/attach', json=picker.form, headers={'Idempotency-Key': key}
            )
            assert response.status_code == 201
            assert response.json()['attached_by'] == 'user'
            assert response.json()['status'] == 'processing'
    picker.client.send.assert_awaited_with(
        'POST',
        '/v1/attach',
        {
            'collection_key': 'owui-attachments-alice',
            'ref': {field: picker.form[field] for field in ('grant_id', 'drive_id', 'item_id', 'etag')},
        },
        as_user='owui:user:alice',
        idempotency_key=key,
    )
    rows = await env.files.Files.get_files_with_soev_jobs()
    assert len(rows) == 1 and rows[0].id == 'source' and rows[0].path == ''
    assert rows[0].meta['source']['ref']['etag'] == 'v1'
    assert rows[0].meta['soev_job']['kind'] == 'reference'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'field,value',
    [
        ('access_token', 'secret'),
        ('collection_key', 'other'),
        ('etag', ''),
        ('web_url', 'http://insecure.invalid'),
        ('size', -1),
    ],
)
async def test_picker_rejects_tokens_untrusted_destination_and_incomplete_reference(picker, field, value):
    """The request schema admits references and no authority, byte payload or destination override."""
    async with AsyncClient(transport=httpx.ASGITransport(picker.app), base_url='http://test') as browser:
        response = await browser.post(
            '/api/v1/files/onedrive/attach',
            json={**picker.form, field: value},
            headers={'Idempotency-Key': str(uuid4())},
        )
    assert response.status_code == 422
    picker.client.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'code,status', [('connection_required', 403), ('not_readable', 422), ('too_large', 413), ('changed', 409)]
)
async def test_picker_preserves_typed_platform_refusals(picker, env, code, status):  # noqa: F811
    """The platform's grant, encryption, size and version refusals create no File."""
    from open_webui.soev.client import SoevApiError

    picker.client.send.side_effect = SoevApiError(status, code, code)
    async with AsyncClient(transport=httpx.ASGITransport(picker.app), base_url='http://test') as browser:
        response = await browser.post(
            '/api/v1/files/onedrive/attach', json=picker.form, headers={'Idempotency-Key': str(uuid4())}
        )
    assert response.status_code == status and response.json()['detail']['code'] == code
    assert await env.files.Files.get_files_with_soev_jobs() == []


@pytest.mark.asyncio
async def test_picker_honors_product_file_limit(picker, monkeypatch):
    """A consumer file cap is checked before platform admission."""
    monkeypatch.setattr(picker.routes.Config, 'get', AsyncMock(return_value=1))
    monkeypatch.setattr(picker.routes.Files, 'count_files_by_user_id', AsyncMock(return_value=1))
    async with AsyncClient(transport=httpx.ASGITransport(picker.app), base_url='http://test') as browser:
        response = await browser.post(
            '/api/v1/files/onedrive/attach', json=picker.form, headers={'Idempotency-Key': str(uuid4())}
        )
    assert response.status_code == 403
    picker.client.send.assert_not_awaited()
