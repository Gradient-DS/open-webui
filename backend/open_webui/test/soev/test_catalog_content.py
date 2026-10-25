"""Catalog download authorization and original byte streaming through both file routes."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from open_webui.test.soev.test_knowledge_store import env, seed  # noqa: F401

# A scripted SVG must never render inline under the OWUI origin.
CONTENT_TYPES = {
    'text': 'text/plain',
    'svg': 'image/svg+xml',
    'png': 'image/png',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'webp': 'image/webp',
}


@pytest.mark.asyncio
@pytest.mark.parametrize('named', [False, True])
@pytest.mark.parametrize(
    'case',
    [
        'pdf',
        'png',
        'jpeg',
        'gif',
        'webp',
        'attachment',
        'text',
        'svg',
        'missing',
        'unreadable',
        'document_hidden',
        'revoked',
    ],
)
async def test_catalog_content_routes(env, monkeypatch, named, case):  # noqa: F811
    from open_webui.models import knowledge
    from open_webui.routers import files

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    if case == 'unreadable':
        await seed(env, 'private', owner='bob')
    if case != 'missing':
        env.api.add_document(
            'private' if case == 'unreadable' else 'kb',
            'cloud',
            filename='Café.pdf',
            content_type=CONTENT_TYPES.get(case, 'application/pdf'),
            principals=['owui:user:bob' if case in {'unreadable', 'document_hidden'} else 'owui:user:alice'],
        )
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == '/v1/downloads/fictional-code':
            assert request.headers['Authorization'] == requests[-2].headers['Authorization']
            assert request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice'
            assert request.headers['X-Soev-User-Token'] == requests[-2].headers['X-Soev-User-Token']
            assert 'X-Soev-Subject' not in request.headers
            assert 'X-Soev-Subject' not in requests[-2].headers
            assert 'cookie' not in request.headers
            return httpx.Response(200, content=b'original\x00bytes', headers={'Content-Type': 'wrong/type'})
        if request.url.path.endswith('/original'):
            assert request.headers['X-Soev-User-Token']
            if case == 'revoked':
                return httpx.Response(
                    404,
                    json={'code': 'document_not_found', 'detail': 'Unreadable document'},
                    headers={'Content-Type': 'application/problem+json'},
                )
            return httpx.Response(
                303,
                headers={'Location': '/v1/downloads/fictional-code', 'Set-Cookie': 'api=secret'},
            )
        return env.api.handle(request)

    from open_webui.soev.client import close_client

    await close_client()
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
    )
    user = SimpleNamespace(id='alice', role='admin')
    if named:
        result = files.get_file_content_by_id('cloud', user=user, db=None, file_name='requested.pdf')
    else:
        result = files.get_file_content_by_id_inline('cloud', user=user, db=None, attachment=case == 'attachment')
    if case in {'missing', 'unreadable', 'document_hidden', 'revoked'}:
        with pytest.raises(HTTPException) as error:
            await result
        assert error.value.status_code == 404
        assert not any(request.url.path == '/v1/downloads/fictional-code' for request in requests)
    else:
        response = await result
        assert b''.join([chunk async for chunk in response.body_iterator]) == b'original\x00bytes'
        assert response.headers['content-type'].split(';')[0] == CONTENT_TYPES.get(case, 'application/pdf')
        assert response.headers['x-content-type-options'] == 'nosniff'
        disposition = 'inline' if not named and case in {'pdf', 'png', 'jpeg', 'gif', 'webp'} else 'attachment'
        assert response.headers['content-disposition'] == f"{disposition}; filename*=UTF-8''Caf%C3%A9.pdf"
        assert 'location' not in response.headers
        assert requests[-2].url.path == '/v1/collections/kb/documents/cloud/original'


@pytest.mark.asyncio
@pytest.mark.parametrize('rendition', ['# Parsed cloud document', '', None])
async def test_get_file_by_id_serves_a_catalog_only_document(env, monkeypatch, rendition):  # noqa: F811
    """The file view returns authorized cloud metadata and parsed text without creating an upload."""
    from open_webui.models import knowledge
    from open_webui.routers import files

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    env.api.add_document(
        'kb',
        'cloud',
        filename='CV.docx',
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        rendition=rendition,
    )
    env.api.requests.clear()
    result = await files.get_file_by_id('cloud', user=SimpleNamespace(id='alice', role='user'), db=None)
    assert result.id == 'cloud'
    assert result.user_id == 'alice'
    assert result.filename == 'CV.docx'
    assert result.meta['content_type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    assert result.meta['soev_catalog_only'] is True
    assert result.data == {'content': rendition or ''}
    assert await files.Files.get_file_by_id('cloud') is None
    assert any(request.url.path == '/v1/collections/kb/documents/cloud/content' for request in env.api.requests)
    assert not any(request.url.path.endswith('/original') for request in env.api.requests)
    assert all(request.headers.get('X-Soev-User-Token') for request in env.api.requests if request.method == 'GET')


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing', 'unreadable', 'document_hidden'])
async def test_get_file_by_id_hides_inaccessible_catalog_documents(env, monkeypatch, case):  # noqa: F811
    """Even an administrator's file view only resolves catalog members visible to that subject."""
    from open_webui.models import knowledge
    from open_webui.routers import files

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    if case == 'unreadable':
        await seed(env, 'private', owner='bob')
    if case != 'missing':
        env.api.add_document(
            'private' if case == 'unreadable' else 'kb', 'cloud', principals=['owui:user:bob'], rendition='Hidden'
        )
    with pytest.raises(HTTPException) as error:
        await files.get_file_by_id('cloud', user=SimpleNamespace(id='alice', role='admin'), db=None)
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('named', [False, True])
@pytest.mark.parametrize('reference', [False, True])
@pytest.mark.parametrize(
    'range_header,status,content_range,body',
    [
        ('bytes=0-3', 206, 'bytes 0-3/10', b'0123'),
        ('bytes=-3', 206, 'bytes 7-9/10', b'789'),
        ('bytes=20-', 416, 'bytes */10', b''),
    ],
)
async def test_file_previews_relay_ranges_and_status(
    env,  # noqa: F811
    monkeypatch,
    named,
    reference,
    range_header,
    status,
    content_range,
    body,
):
    from fastapi import FastAPI
    from open_webui.models import knowledge
    from open_webui.models.files import FileForm
    from open_webui.routers import files
    from open_webui.soev.client import close_client

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    if reference:
        await files.Files.insert_new_file(
            'alice',
            FileForm(
                id='cloud',
                filename='Fictional.pdf',
                path='',
                meta={'collection_name': 'kb', 'source': {'provider': 'onedrive'}, 'content_type': 'application/pdf'},
            ),
        )
    else:
        env.api.add_document('kb', 'cloud', filename='Fictional.pdf', content_type='application/pdf')
    requests = []

    def handle(request):
        if request.url.path.endswith('/original') or request.url.path.startswith('/v1/downloads/'):
            requests.append(request)
            assert request.headers['Range'] == range_header
            assert request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:alice'
            assert 'X-Soev-Subject' not in request.headers
            if request.url.path.endswith('/original'):
                return httpx.Response(303, headers={'Location': '/v1/downloads/fictional-code'})
            return httpx.Response(
                status,
                content=body,
                headers={
                    'Content-Range': content_range,
                    'Accept-Ranges': 'bytes',
                    'Content-Length': str(len(body)),
                    'Cache-Control': 'private, no-store',
                },
            )
        return env.api.handle(request)

    await close_client()
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: AsyncClient(
            transport=httpx.MockTransport(handle),
            **kwargs,
        ),
    )
    app = FastAPI()
    app.include_router(files.router, prefix='/files')
    app.dependency_overrides[files.get_verified_user] = lambda: SimpleNamespace(id='alice', role='user')
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as browser:
        response = await browser.get(
            '/files/cloud/content' + ('/Fictional.pdf' if named else ''), headers={'Range': range_header}
        )
    assert response.status_code == status
    assert response.content == body
    assert response.headers['Content-Range'] == content_range
    assert response.headers['Content-Length'] == str(len(body))
    assert response.headers['Accept-Ranges'] == 'bytes'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert len(requests) == 2
