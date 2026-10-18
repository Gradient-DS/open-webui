"""The sources panel probes a file with HEAD before offering the document viewer."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def client(monkeypatch):
    from open_webui.routers import files

    async def served(*args, **kwargs):
        from starlette.responses import Response

        return Response(b'%PDF-1.4', media_type='application/pdf')

    monkeypatch.setattr(files, 'get_file_content_by_id_inline', served)
    app = FastAPI()
    app.include_router(files.router, prefix='/api/v1/files')
    for route in app.routes:
        if getattr(route, 'name', None) == 'get_file_content_by_id':
            route.endpoint = served
            route.dependant.call = served
    app.dependency_overrides[files.get_verified_user] = lambda: SimpleNamespace(id='alice', role='user')
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_head_is_answered_like_get_with_an_empty_body(client):
    """HEAD on the content route returns the GET status and headers without a body."""
    async with client:
        head = await client.head('/api/v1/files/f1/content')
        get = await client.get('/api/v1/files/f1/content')
    assert (head.status_code, get.status_code) == (200, 200)
    assert head.headers['content-type'] == get.headers['content-type'] == 'application/pdf'
    assert head.content == b'' and get.content == b'%PDF-1.4'
